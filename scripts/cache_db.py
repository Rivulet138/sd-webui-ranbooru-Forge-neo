"""
SQLite tag cache for Ranbooru.

Focus:
- hard dedupe for exact tag sets
- stable ordered reading for large caches
- lightweight keyword filtering
"""

import os
import csv
import hashlib
import json
import math
import re
import shlex
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from functools import wraps

PROMPT_BATCH_SCHEMA = "prompt_batch.v1"
PROMPT_BATCH_MAX_PROMPT_LENGTH = 12_000
PROMPT_BATCH_META_PREFIX = "prompt_batch.v1:"

try:
    from .ranbooru_logging import get_logger, log_event
    from .version import NATURAL_PROMPT_CONVERTER_VERSION
except ImportError:
    _scripts_dir = os.path.dirname(os.path.abspath(__file__))
    _added_scripts_dir = _scripts_dir not in sys.path
    if _added_scripts_dir:
        sys.path.insert(0, _scripts_dir)
    try:
        from ranbooru_logging import get_logger, log_event
        from version import NATURAL_PROMPT_CONVERTER_VERSION
    finally:
        if _added_scripts_dir:
            sys.path.remove(_scripts_dir)


logger = get_logger("cache_db")


def _large_json_declares_prompt_batch(file_path):
    pushed = []
    number_re = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\Z")

    with open(file_path, "r", encoding="utf-8") as handle:
        def take():
            return pushed.pop() if pushed else handle.read(1)

        def take_nonspace():
            char = take()
            while char and char in " \t\r\n":
                char = take()
            return char

        def read_string(capture=False, limit=1024):
            raw = ['"'] if capture else None
            while True:
                char = take()
                if not char:
                    raise ValueError("JSON string is incomplete")
                if ord(char) < 0x20:
                    raise ValueError("JSON string contains a control character")
                if char == '"':
                    if capture:
                        raw.append(char)
                        return json.loads("".join(raw))
                    return None
                if char == "\\":
                    escape = take()
                    if escape not in '"\\/bfnrtu':
                        raise ValueError("JSON string contains an invalid escape")
                    escape_text = escape
                    if escape == "u":
                        digits = "".join(take() for _ in range(4))
                        if len(digits) != 4 or any(digit not in "0123456789abcdefABCDEF" for digit in digits):
                            raise ValueError("JSON string contains an invalid unicode escape")
                        escape_text += digits
                    if capture:
                        raw.extend(("\\", escape_text))
                elif capture:
                    raw.append(char)
                if capture and sum(len(part) for part in raw) > limit:
                    raise ValueError("JSON string is too long")

        def parse_scalar(first):
            chars = [first]
            char = take()
            while char and char not in " \t\r\n,]}":
                chars.append(char)
                if len(chars) > 128:
                    raise ValueError("JSON scalar is too long")
                char = take()
            if char:
                pushed.append(char)
            token = "".join(chars)
            if token not in ("true", "false", "null") and number_re.fullmatch(token) is None:
                raise ValueError("JSON scalar is invalid")

        def parse_value(depth):
            if depth > 256:
                raise ValueError("JSON nesting is too deep")
            char = take_nonspace()
            if char == '"':
                read_string()
                return
            if char == "{":
                parse_object(depth + 1)
                return
            if char == "[":
                parse_array(depth + 1)
                return
            if not char:
                raise ValueError("JSON value is incomplete")
            parse_scalar(char)

        schema_version = None

        def parse_object(depth, top_level=False):
            nonlocal schema_version
            char = take_nonspace()
            if char == "}":
                return
            while True:
                if char != '"':
                    raise ValueError("JSON object key is invalid")
                key = read_string(capture=True)
                if take_nonspace() != ":":
                    raise ValueError("JSON object is missing a colon")
                if top_level and key == "schema_version":
                    if schema_version is not None or take_nonspace() != '"':
                        raise ValueError("JSON schema_version is invalid or duplicated")
                    schema_version = read_string(capture=True, limit=128)
                else:
                    parse_value(depth + 1)
                separator = take_nonspace()
                if separator == "}":
                    return
                if separator != ",":
                    raise ValueError("JSON object separator is invalid")
                char = take_nonspace()

        def parse_array(depth):
            char = take_nonspace()
            if char == "]":
                return
            pushed.append(char)
            while True:
                parse_value(depth + 1)
                separator = take_nonspace()
                if separator == "]":
                    return
                if separator != ",":
                    raise ValueError("JSON array separator is invalid")

        if take_nonspace() != "{":
            return False
        parse_object(0, top_level=True)
        if take_nonspace():
            raise ValueError("JSON contains trailing content")
        return schema_version == PROMPT_BATCH_SCHEMA


def _serialized_destructive_operation(method):
    """Keep the DB commit and shared undo-journal publication in one process order."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._destructive_lock:
            return method(self, *args, **kwargs)

    return wrapped


class TagCacheManager:
    _destructive_locks = {}
    _destructive_locks_guard = threading.Lock()
    _session_cursor_paths = set()
    MAX_POSITION_SELECTION = 10000
    MAX_PROMPT_RAG_CANDIDATES = 5000
    MAX_PROMPT_RAG_EXAMPLES = 5
    MAX_IMPORT_BYTES = 64 * 1024 * 1024
    MAX_IMPORT_RECORDS = 100000
    BACKUP_MAX_COUNT = 20
    BACKUP_MAX_AGE_DAYS = 30
    BACKUP_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
    NATURAL_CONVERTER_VERSION = NATURAL_PROMPT_CONVERTER_VERSION

    TAG_COLUMNS = {
        "booru": "TEXT",
        "post_id": "TEXT",
        "tags_raw": "TEXT",
        "tags_prompt": "TEXT",
        "natural_prompt": "TEXT",
        "natural_preset": "TEXT",
        "natural_model": "TEXT",
        "natural_updated_at": "TEXT",
        "natural_source_hash": "TEXT",
        "natural_converter_version": "TEXT",
        "score": "INTEGER DEFAULT 0",
        "rating": "TEXT",
        "source_url": "TEXT",
        "preview_url": "TEXT",
        "search_query": "TEXT",
        "duplicate_key": "TEXT",
        "created_at": "TEXT",
    }

    SORT_SQL = {
        "ID": "id ASC",
        "Newest": "id DESC",
        "Oldest": "id ASC",
        "High Score": "score DESC, id ASC",
        "Low Score": "score ASC, id ASC",
        "Random": "RANDOM()",
        "随机": "RANDOM()",
        "高分": "score DESC, id ASC",
        "低分": "score ASC, id ASC",
        "最新": "id DESC",
        "最旧": "id ASC",
    }

    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.db_path = os.path.join(cache_dir, "tag_cache.db")
        lock_key = os.path.normcase(os.path.realpath(self.db_path))
        with self._destructive_locks_guard:
            self._destructive_lock = self._destructive_locks.setdefault(
                lock_key,
                threading.RLock(),
            )
        self.current_index = 0
        with self._destructive_lock:
            self._init_db()
            with self._destructive_locks_guard:
                first_open_this_session = lock_key not in self._session_cursor_paths
                if first_open_this_session:
                    self._session_cursor_paths.add(lock_key)
            try:
                if first_open_this_session:
                    self.reset_index()
                else:
                    self._load_index()
            except Exception:
                if first_open_this_session:
                    with self._destructive_locks_guard:
                        self._session_cursor_paths.discard(lock_key)
                raise

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _init_db(self):
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tags TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            self._migrate_tags_table(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_post ON tags (booru, post_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_score ON tags (score)")
            conn.commit()
            self._retire_filtered_pool_v1(conn)
        finally:
            conn.close()

    @staticmethod
    def _retire_filtered_pool_v1(conn):
        migration_key = "retire_filtered_pool_v1"
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM metadata WHERE key = ?",
                (migration_key,),
            ).fetchone()
            if row and row["value"] in ("not-present", "inert-retained"):
                conn.commit()
                return

            legacy_tables = {
                item["name"]
                for item in conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name IN ('rules', 'filtered_pool')
                    """
                ).fetchall()
            }
            state = "inert-retained" if legacy_tables else "not-present"
            if legacy_tables:
                conn.execute(
                    "DELETE FROM metadata WHERE key IN (?, ?)",
                    ("use_filtered_pool", "active_rule_id"),
                )
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (migration_key, state),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _migrate_tags_table(self, conn):
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(tags)")}
        for name, column_type in self.TAG_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE tags ADD COLUMN {name} {column_type}")
        conn.execute(
            """
            UPDATE tags
            SET tags_prompt = tags,
                natural_prompt = '',
                natural_preset = '',
                natural_model = '',
                natural_updated_at = '',
                natural_source_hash = '',
                natural_converter_version = ''
            WHERE tags_prompt IS NULL OR tags_prompt = ''
            """
        )
        conn.execute("UPDATE tags SET tags_raw = tags WHERE tags_raw IS NULL OR tags_raw = ''")
        self._apply_duplicate_key_migration(conn)
        self._apply_natural_metadata_migration(conn)

    @staticmethod
    def _natural_source_hash(tags_prompt):
        source = str(tags_prompt or "").strip()
        return hashlib.sha256(source.encode("utf-8")).hexdigest() if source else ""

    def _apply_natural_metadata_migration(self, conn):
        migration_key = "natural_metadata_migration_v1"
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (migration_key,),
        ).fetchone()
        if row and row["value"] == "1":
            return
        rows = conn.execute(
            """
            SELECT id, COALESCE(tags_prompt, tags) AS tags_prompt
            FROM tags
            WHERE COALESCE(natural_prompt, '') != ''
              AND COALESCE(natural_source_hash, '') = ''
            """
        ).fetchall()
        for item in rows:
            conn.execute(
                """
                UPDATE tags
                SET natural_source_hash = ?,
                    natural_converter_version = CASE
                        WHEN COALESCE(natural_converter_version, '') = '' THEN 'legacy'
                        ELSE natural_converter_version
                    END
                WHERE id = ?
                """,
                (self._natural_source_hash(item["tags_prompt"]), item["id"]),
            )
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (migration_key, "1"),
        )

    def _load_index(self):
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM metadata WHERE key = ?", ("current_index",)).fetchone()
            self.current_index = int(row["value"]) if row else 0
        except Exception:
            self.current_index = 0
        finally:
            conn.close()

    def _save_index(self, conn=None):
        own_conn = conn is None
        conn = conn or self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("current_index", str(self.current_index)),
            )
            if own_conn:
                conn.commit()
        finally:
            if own_conn:
                conn.close()

    @staticmethod
    def _normalize_tag_token(tag):
        normalized = re.sub(r"[\s_]+", "_", str(tag or "").strip().lower())
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        if re.fullmatch(r"[+\-_]+", normalized or ""):
            return ""
        girl_match = re.fullmatch(r"(\d+)girls?", normalized)
        if girl_match:
            count = girl_match.group(1)
            return "1girl" if count == "1" else f"{count}girls"
        boy_match = re.fullmatch(r"(\d+)boys?", normalized)
        if boy_match:
            count = boy_match.group(1)
            return "1boy" if count == "1" else f"{count}boys"
        return normalized

    @staticmethod
    def _split_keywords(keywords):
        if not keywords:
            return []
        normalized = str(keywords).replace("，", ",").replace("\n", ",").replace("\r", ",")
        tokens = []
        seen = set()
        for keyword in normalized.split(","):
            token = TagCacheManager._normalize_tag_token(keyword)
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
        return tokens

    @staticmethod
    def _split_prompt_tokens(tags):
        if not tags:
            return []
        if isinstance(tags, (list, tuple, set)):
            raw_tokens = tags
        else:
            normalized = str(tags).replace("，", ",").replace("\n", ",").replace("\r", ",")
            raw_tokens = normalized.split(",") if "," in normalized else normalized.split()
        tokens = []
        seen = set()
        for token in raw_tokens:
            normalized = TagCacheManager._normalize_tag_token(token)
            if normalized and normalized not in seen:
                seen.add(normalized)
                tokens.append(normalized)
        return tokens

    @staticmethod
    def _canonical_tag_key(tags):
        return "|".join(sorted(TagCacheManager._split_prompt_tokens(tags)))

    @staticmethod
    def _escape_like(value):
        return str(value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _safe_int(value, default=0):
        try:
            if value in (None, ""):
                return default
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def parse_positions(value, total=None, max_selection=None):
        if not value:
            return []
        max_selection = TagCacheManager._safe_int(
            max_selection,
            TagCacheManager.MAX_POSITION_SELECTION,
        )
        max_selection = max(1, min(max_selection, TagCacheManager.MAX_POSITION_SELECTION))
        total = TagCacheManager._safe_int(total, 0) if total is not None else None
        if total is not None and total <= 0:
            return []
        positions = []
        seen = set()
        for part in re.split(r"[,，\s]+", str(value).strip()):
            if len(positions) >= max_selection:
                break
            if not part:
                continue
            match = re.fullmatch(r"(\d+)\s*[-~:：]\s*(\d+)", part)
            if match:
                start = int(match.group(1))
                end = int(match.group(2))
                step = 1 if end >= start else -1
                if total is not None:
                    if step > 0:
                        start = max(1, start)
                        end = min(total, end)
                        if start > end:
                            continue
                    else:
                        start = min(total, start)
                        end = max(1, end)
                        if start < end:
                            continue
                for position in range(start, end + step, step):
                    if position > 0 and position not in seen:
                        seen.add(position)
                        positions.append(position)
                        if len(positions) >= max_selection:
                            break
                continue
            if part.isdigit():
                position = int(part)
                if position > 0 and (total is None or position <= total) and position not in seen:
                    seen.add(position)
                    positions.append(position)
        return positions

    def _sort_sql(self, sort_order="ID", random_seed=None):
        if (sort_order or "") in ("Random", "随机"):
            if random_seed is not None:
                seed = self._safe_int(random_seed, 0)
                return f"((id * 1103515245 + {seed}) % 2147483647) ASC, id ASC"
            return self.SORT_SQL.get(sort_order or "Random", "RANDOM()")
        return self.SORT_SQL.get(sort_order or "ID", "id ASC")

    def _make_duplicate_key(self, record):
        metadata = self._prompt_batch_metadata(record)
        sha256 = str(metadata.get("sha256") or "")
        if sha256:
            return f"prompt_batch:sha256:{sha256}"
        producer = str(metadata.get("producer") or "unknown")
        record_id = str(metadata.get("record_id") or "")
        if record_id:
            return f"prompt_batch:{producer}:record:{record_id}"
        filename = str(metadata.get("filename") or "")
        if filename:
            return f"prompt_batch:{producer}:file:{filename}"
        tag_key = self._canonical_tag_key(
            record.get("tags_raw") or record.get("tags_prompt") or record.get("tags") or ""
        )
        if tag_key:
            return f"tags:{tag_key}"
        booru = str(record.get("booru") or "").strip()
        post_id = str(record.get("post_id") or "").strip()
        if booru and post_id:
            return f"post:{booru}:{post_id}"
        return "tags:"

    @staticmethod
    def prompt_batch_search_query(record):
        image = record.get("image") if isinstance(record.get("image"), dict) else {}
        metadata = {
            "producer": str(record.get("prompt_batch_producer") or "")[:160],
            "record_id": str(record.get("record_id") or "")[:256],
            "filename": os.path.basename(str(image.get("filename") or ""))[:255],
            "sha256": str(image.get("sha256") or "")[:128],
            "status": str(record.get("status") or "")[:80],
            "error": str(record.get("error") or "")[:2000],
            "appended": record.get("appended") is True,
        }
        if not any(metadata.values()):
            return ""
        return PROMPT_BATCH_META_PREFIX + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _prompt_batch_metadata(record):
        value = str(record.get("search_query") or "")
        if not value.startswith(PROMPT_BATCH_META_PREFIX):
            return {}
        try:
            metadata = json.loads(value[len(PROMPT_BATCH_META_PREFIX):])
        except (TypeError, ValueError):
            return {}
        return metadata if isinstance(metadata, dict) else {}

    def _coerce_record(self, record):
        if isinstance(record, str):
            record = {"tags_prompt": record}
        tags_prompt = str(record.get("tags_prompt") or record.get("tags") or "").strip()
        tags_raw = str(record.get("tags_raw") or tags_prompt).strip()
        coerced = {
            "booru": record.get("booru") or "",
            "post_id": str(record.get("post_id") or ""),
            "tags_raw": tags_raw,
            "tags_prompt": tags_prompt,
            "tags": tags_prompt,
            "natural_prompt": str(record.get("natural_prompt") or "").strip(),
            "natural_preset": str(record.get("natural_preset") or "").strip(),
            "natural_model": str(record.get("natural_model") or "").strip(),
            "natural_updated_at": str(record.get("natural_updated_at") or "").strip(),
            "natural_source_hash": str(record.get("natural_source_hash") or "").strip(),
            "natural_converter_version": str(record.get("natural_converter_version") or "").strip(),
            "score": self._safe_int(record.get("score"), 0),
            "rating": record.get("rating") or "",
            "source_url": record.get("source_url") or "",
            "preview_url": record.get("preview_url") or "",
            "search_query": record.get("search_query") or "",
        }
        if coerced["natural_prompt"]:
            if not coerced["natural_source_hash"]:
                coerced["natural_source_hash"] = self._natural_source_hash(tags_prompt)
            if not coerced["natural_converter_version"]:
                coerced["natural_converter_version"] = "legacy"
        else:
            coerced["natural_source_hash"] = ""
            coerced["natural_converter_version"] = ""
        coerced["duplicate_key"] = self._make_duplicate_key(coerced)
        return coerced

    def _rebuild_duplicate_keys(self, conn):
        rows = conn.execute(
            "SELECT id, booru, post_id, tags, tags_prompt, tags_raw, search_query, duplicate_key FROM tags ORDER BY id"
        ).fetchall()
        updated = 0
        for row in rows:
            tags_prompt = str(row["tags_prompt"] or row["tags_raw"] or row["tags"] or "").strip()
            tags_raw = str(row["tags_raw"] or row["tags"] or tags_prompt).strip()
            duplicate_key = self._make_duplicate_key(
                {
                    "booru": row["booru"],
                    "post_id": row["post_id"],
                    "tags_raw": tags_raw,
                    "tags_prompt": tags_prompt,
                    "tags": tags_raw,
                    "search_query": row["search_query"],
                }
            )
            if row["duplicate_key"] != duplicate_key or row["tags_prompt"] != tags_prompt or row["tags_raw"] != tags_raw:
                conn.execute(
                    """
                    UPDATE tags
                    SET tags_prompt = ?,
                        tags_raw = ?,
                        duplicate_key = ?,
                        natural_prompt = CASE
                            WHEN COALESCE(tags_prompt, tags) = ? THEN natural_prompt
                            ELSE ''
                        END,
                        natural_preset = CASE
                            WHEN COALESCE(tags_prompt, tags) = ? THEN natural_preset
                            ELSE ''
                        END,
                        natural_model = CASE
                            WHEN COALESCE(tags_prompt, tags) = ? THEN natural_model
                            ELSE ''
                        END,
                        natural_updated_at = CASE
                            WHEN COALESCE(tags_prompt, tags) = ? THEN natural_updated_at
                            ELSE ''
                        END,
                        natural_source_hash = CASE
                            WHEN COALESCE(tags_prompt, tags) = ? THEN natural_source_hash
                            ELSE ''
                        END,
                        natural_converter_version = CASE
                            WHEN COALESCE(tags_prompt, tags) = ? THEN natural_converter_version
                            ELSE ''
                        END
                    WHERE id = ?
                    """,
                    (
                        tags_prompt,
                        tags_raw,
                        duplicate_key,
                        tags_prompt,
                        tags_prompt,
                        tags_prompt,
                        tags_prompt,
                        tags_prompt,
                        tags_prompt,
                        row["id"],
                    ),
                )
                updated += 1
        return updated

    def _ensure_duplicate_index(self, conn):
        conn.execute("DROP INDEX IF EXISTS idx_tags_duplicate")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_duplicate ON tags (duplicate_key)")

    def _apply_duplicate_key_migration(self, conn, force=False):
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?",
            ("duplicate_key_migration_v4",),
        ).fetchone()
        if not force and row and row["value"] == "4":
            return {"updated": 0, "removed": 0}
        conn.execute("DROP INDEX IF EXISTS idx_tags_duplicate")
        updated = self._rebuild_duplicate_keys(conn)
        removed = 0
        self._ensure_duplicate_index(conn)
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            ("duplicate_key_migration_v4", "4"),
        )
        return {"updated": updated, "removed": removed}

    def _backup_reason(self, reason):
        reason = re.sub(r"[^0-9A-Za-z_-]+", "_", str(reason or "manual")).strip("_")
        return (reason or "manual")[:48]

    def backup_db(self, reason="manual"):
        if not os.path.exists(self.db_path):
            return ""
        backup_dir = os.path.join(self.cache_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        filename = f"tag_cache_backup_{timestamp}_{self._backup_reason(reason)}.db"
        backup_path = os.path.join(backup_dir, filename)
        source = self._connect()
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        self.prune_backups()
        return backup_path

    def prune_backups(self):
        backup_dir = os.path.join(self.cache_dir, "backups")
        if not os.path.isdir(backup_dir):
            return []
        prefix = "tag_cache_backup_"
        backups = []
        for name in os.listdir(backup_dir):
            path = os.path.join(backup_dir, name)
            if name.startswith(prefix) and name.endswith(".db") and os.path.isfile(path):
                try:
                    backups.append((os.path.getmtime(path), os.path.getsize(path), path))
                except OSError:
                    continue
        backups.sort(reverse=True)
        cutoff = datetime.now().timestamp() - timedelta(
            days=max(0, int(self.BACKUP_MAX_AGE_DAYS))
        ).total_seconds()
        removed = []
        retained_bytes = 0
        for index, (modified, size, path) in enumerate(backups):
            if index == 0:
                retained_bytes += size
                continue
            expired = self.BACKUP_MAX_AGE_DAYS >= 0 and modified < cutoff
            excess = index >= max(1, int(self.BACKUP_MAX_COUNT))
            over_budget = retained_bytes + size > max(
                1,
                int(self.BACKUP_MAX_TOTAL_BYTES),
            )
            if not (expired or excess or over_budget):
                retained_bytes += size
                continue
            try:
                os.remove(path)
                removed.append(path)
            except OSError:
                pass
        return removed

    def _last_deleted_path(self):
        return os.path.join(self.cache_dir, "last_deleted.json")

    def _row_to_record(self, row, position=None):
        tags_prompt = row["tags_prompt"] or row["tags"] or ""
        natural_prompt = row["natural_prompt"] or ""
        natural_source_hash = row["natural_source_hash"] or ""
        natural_preset = row["natural_preset"] or ""
        natural_model = row["natural_model"] or ""
        natural_updated_at = row["natural_updated_at"] or ""
        natural_converter_version = row["natural_converter_version"] or ""
        if natural_prompt and (
            natural_source_hash != self._natural_source_hash(tags_prompt)
            or natural_converter_version != self.NATURAL_CONVERTER_VERSION
        ):
            natural_prompt = ""
            natural_preset = ""
            natural_model = ""
            natural_updated_at = ""
            natural_source_hash = ""
            natural_converter_version = ""
        record = {
            "id": int(row["id"]),
            "booru": row["booru"] or "",
            "post_id": row["post_id"] or "",
            "tags_raw": row["tags_raw"] or row["tags_prompt"] or row["tags"] or "",
            "tags_prompt": tags_prompt,
            "natural_prompt": natural_prompt,
            "natural_preset": natural_preset,
            "natural_model": natural_model,
            "natural_updated_at": natural_updated_at,
            "natural_source_hash": natural_source_hash,
            "natural_converter_version": natural_converter_version,
            "score": row["score"] or 0,
            "rating": row["rating"] or "",
            "source_url": row["source_url"] or "",
            "preview_url": row["preview_url"] or "",
            "search_query": row["search_query"] or "",
            "duplicate_key": row["duplicate_key"] or "",
        }
        if position is not None:
            record["position"] = int(position)
        elif "position" in row.keys():
            record["position"] = int(row["position"])
        return record

    def _all_records(self, conn):
        rows = conn.execute(
            """
            SELECT id,
                   ROW_NUMBER() OVER (ORDER BY id ASC) AS position,
                   booru, post_id, tags, tags_raw, tags_prompt,
                   natural_prompt, natural_preset, natural_model, natural_updated_at,
                   natural_source_hash, natural_converter_version,
                   score, rating,
                   source_url, preview_url, search_query, duplicate_key
            FROM tags
            ORDER BY id ASC
            """
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _records_by_ids(self, conn, ids, position_map=None):
        ids = list(dict.fromkeys(int(tag_id) for tag_id in ids if tag_id is not None))
        if not ids:
            return []
        rows_by_id = {}
        for start in range(0, len(ids), 800):
            chunk = ids[start:start + 800]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT id, booru, post_id, tags, tags_raw, tags_prompt,
                       natural_prompt, natural_preset, natural_model, natural_updated_at,
                       natural_source_hash, natural_converter_version,
                       score, rating,
                       source_url, preview_url, search_query, duplicate_key
                FROM tags
                WHERE id IN ({placeholders})
                """,
                chunk,
            ).fetchall()
            for row in rows:
                rows_by_id[int(row["id"])] = row
        records = []
        for tag_id in ids:
            row = rows_by_id.get(tag_id)
            if not row:
                continue
            position = position_map.get(tag_id) if position_map else None
            records.append(self._row_to_record(row, position=position))
        return records

    def _existing_ids(self, conn, ids):
        ids = list(dict.fromkeys(int(tag_id) for tag_id in ids if tag_id is not None))
        if not ids:
            return []
        existing = []
        for start in range(0, len(ids), 800):
            chunk = ids[start:start + 800]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(f"SELECT id FROM tags WHERE id IN ({placeholders}) ORDER BY id ASC", chunk).fetchall()
            existing.extend(int(row["id"]) for row in rows)
        requested_order = {tag_id: offset for offset, tag_id in enumerate(ids)}
        return sorted(set(existing), key=lambda tag_id: requested_order.get(tag_id, tag_id))

    def _ids_matching_where(self, conn, where="", params=None):
        rows = conn.execute(f"SELECT id FROM tags {where} ORDER BY id ASC", params or []).fetchall()
        return [int(row["id"]) for row in rows]

    def _duplicate_ids_to_remove(self, conn):
        rows = conn.execute(
            "SELECT id, duplicate_key, score FROM tags ORDER BY duplicate_key, score DESC, id ASC"
        ).fetchall()
        seen = set()
        duplicate_ids = []
        for row in rows:
            duplicate_key = str(row["duplicate_key"] or "")
            if duplicate_key in seen:
                duplicate_ids.append(int(row["id"]))
            else:
                seen.add(duplicate_key)
        return duplicate_ids

    def _save_last_deleted(self, records, reason="", backup_path=""):
        records = list(records or [])
        if not records:
            return ""
        payload = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "reason": str(reason or "delete"),
            "backup_path": backup_path or "",
            "count": len(records),
            "records": records,
        }
        path = self._last_deleted_path()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        return path

    def _save_last_deleted_after_commit(self, records, reason="", backup_path=""):
        """Write the convenience undo journal without masking a committed DB change."""
        try:
            return self._save_last_deleted(records, reason, backup_path)
        except Exception as error:
            log_event(logger, "cache_mutation_failure", "Failed to update undo journal after commit: %s", error)
            return ""

    @_serialized_destructive_operation
    def restore_last_deleted(self, dedupe=True):
        path = self._last_deleted_path()
        if not os.path.exists(path):
            return {"ok": False, "message": "没有可撤销的删除记录", "inserted": 0, "total": self.get_active_total()}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            records = payload.get("records") or []
        except Exception as e:
            return {"ok": False, "message": f"读取撤销记录失败: {e}", "inserted": 0, "total": self.get_active_total()}
        if not records:
            return {"ok": False, "message": "撤销记录为空", "inserted": 0, "total": self.get_active_total()}
        stats = self.append_records(records, dedupe=dedupe)
        return {
            "ok": True,
            **stats,
            "restored_from": path,
            "deleted_count": len(records),
            "reason": payload.get("reason", ""),
            "created_at": payload.get("created_at", ""),
        }

    @_serialized_destructive_operation
    def save_records(self, records, dedupe=True, min_score=None, backup_reason="overwrite"):
        conn = self._connect()
        existing_records = []
        backup_path = ""
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_records = self._all_records(conn)
            backup_path = self.backup_db(backup_reason) if existing_records else ""
            conn.execute("DELETE FROM tags")
            self.current_index = 0
            self._save_index(conn)
            stats = self._insert_records(conn, records, dedupe=dedupe, min_score=min_score)
            conn.commit()
            stats["total"] = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            stats["backup_path"] = backup_path
            self._save_last_deleted_after_commit(existing_records, backup_reason, backup_path)
            return stats
        except Exception:
            conn.rollback()
            self._load_index()
            raise
        finally:
            conn.close()

    def _insert_records(self, conn, records, dedupe=True, min_score=None):
        stats = {"inserted": 0, "skipped_duplicate": 0, "skipped_score": 0, "skipped_empty": 0, "total": 0}
        for item in records:
            record = self._coerce_record(item)
            if not record["tags_prompt"]:
                stats["skipped_empty"] += 1
                continue
            if min_score is not None and record["score"] < int(min_score):
                stats["skipped_score"] += 1
                continue
            if dedupe and conn.execute(
                "SELECT 1 FROM tags WHERE duplicate_key = ? LIMIT 1",
                (record["duplicate_key"],),
            ).fetchone():
                stats["skipped_duplicate"] += 1
                continue
            cursor = conn.execute(
                """
                INSERT INTO tags (
                    booru, post_id, tags, tags_raw, tags_prompt,
                    natural_prompt, natural_preset, natural_model, natural_updated_at,
                    natural_source_hash, natural_converter_version,
                    score, rating, source_url, preview_url, search_query, duplicate_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["booru"],
                    record["post_id"],
                    record["tags_prompt"],
                    record["tags_raw"],
                    record["tags_prompt"],
                    record["natural_prompt"],
                    record["natural_preset"],
                    record["natural_model"],
                    record["natural_updated_at"],
                    record["natural_source_hash"],
                    record["natural_converter_version"],
                    record["score"],
                    record["rating"],
                    record["source_url"],
                    record["preview_url"],
                    record["search_query"],
                    record["duplicate_key"],
                ),
            )
            if cursor.rowcount > 0:
                stats["inserted"] += 1
        return stats

    def append_records(self, records, dedupe=True, min_score=None):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            stats = self._insert_records(conn, records, dedupe=dedupe, min_score=min_score)
            conn.commit()
            stats["total"] = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            return stats
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reset_index(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self.current_index = 0
            self._save_index(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            self._load_index()
            raise
        finally:
            conn.close()

    @_serialized_destructive_operation
    def delete_cache(self):
        conn = self._connect()
        deleted_records = []
        backup_path = ""
        try:
            conn.execute("BEGIN IMMEDIATE")
            deleted_records = self._all_records(conn)
            backup_path = self.backup_db("delete_cache")
            conn.execute("DELETE FROM tags")
            self.current_index = 0
            self._save_index(conn)
            conn.commit()
            undo_path = self._save_last_deleted_after_commit(
                deleted_records,
                "delete_cache",
                backup_path,
            )
            return {"deleted": len(deleted_records), "backup_path": backup_path, "undo_path": undo_path}
        except Exception as e:
            conn.rollback()
            self._load_index()
            log_event(logger, "cache_mutation_failure", "Delete failed: %s", e)
            return {"deleted": 0, "backup_path": "", "undo_path": "", "error": str(e)}
        finally:
            conn.close()

    @_serialized_destructive_operation
    def compact_duplicates(self):
        backup_path = ""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            backup_path = self.backup_db("compact_duplicates")
            conn.execute("DROP INDEX IF EXISTS idx_tags_duplicate")
            updated = self._rebuild_duplicate_keys(conn)
            duplicate_ids = self._duplicate_ids_to_remove(conn)
            deleted_records = self._records_by_ids(conn, duplicate_ids)
            current_index, deleted_before_cursor, new_total = self._deletion_cursor_adjustment(
                conn,
                duplicate_ids,
            )
            removed = self._delete_ids_in_chunks(conn, duplicate_ids) if duplicate_ids else 0
            self._apply_cursor_after_deletion(
                conn,
                current_index,
                deleted_before_cursor,
                new_total,
            )
            self._ensure_duplicate_index(conn)
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("duplicate_key_migration_v4", "4"),
            )
            conn.commit()
            self._save_last_deleted_after_commit(deleted_records, "compact_duplicates", backup_path)
            total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            return {"updated": updated, "removed": removed, "total": total, "backup_path": backup_path}
        except Exception as e:
            conn.rollback()
            log_event(logger, "cache_mutation_failure", "Duplicate compaction failed: %s", e)
            return {"updated": 0, "removed": 0, "total": self.get_active_total(), "backup_path": backup_path, "error": str(e)}
        finally:
            conn.close()

    @staticmethod
    def _tag_jaccard(left, right):
        if not left or not right:
            return 0.0
        intersection = len(left & right)
        if intersection <= 0:
            return 0.0
        return intersection / float(len(left | right))

    @staticmethod
    def _can_reach_similarity(left_len, right_len, threshold):
        if left_len <= 0 or right_len <= 0:
            return False
        return (min(left_len, right_len) / float(max(left_len, right_len))) >= threshold

    def _delete_ids_in_chunks(self, conn, ids):
        removed = 0
        for start in range(0, len(ids), 800):
            chunk = ids[start:start + 800]
            placeholders = ",".join("?" for _ in chunk)
            cursor = conn.execute(f"DELETE FROM tags WHERE id IN ({placeholders})", chunk)
            removed += cursor.rowcount
        return removed

    @_serialized_destructive_operation
    def compact_similar(self, threshold=0.9, keep_per_group=2, must_include="", must_exclude="", query=""):
        try:
            threshold = float(threshold)
        except (TypeError, ValueError, OverflowError):
            threshold = 0.9
        threshold = max(0.5, min(1.0, threshold))
        try:
            keep_per_group = int(keep_per_group)
        except (TypeError, ValueError, OverflowError):
            keep_per_group = 2
        keep_per_group = max(1, keep_per_group)

        include, exclude = self.parse_search_syntax(query, must_include, must_exclude)
        where, params = self._where_for_terms(include, exclude)
        backup_path = ""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            backup_path = self.backup_db("compact_similar")
            conn.execute("DROP INDEX IF EXISTS idx_tags_duplicate")
            updated = self._rebuild_duplicate_keys(conn)
            exact_delete_ids = self._duplicate_ids_to_remove(conn)
            exact_records = self._records_by_ids(conn, exact_delete_ids)
            (
                exact_current_index,
                exact_deleted_before_cursor,
                exact_new_total,
            ) = self._deletion_cursor_adjustment(conn, exact_delete_ids)
            exact_removed = self._delete_ids_in_chunks(conn, exact_delete_ids) if exact_delete_ids else 0
            self._apply_cursor_after_deletion(
                conn,
                exact_current_index,
                exact_deleted_before_cursor,
                exact_new_total,
            )

            rows = conn.execute(
                f"""
                SELECT id, score, COALESCE(tags_prompt, tags_raw, tags) AS tags_prompt
                FROM tags
                {where}
                ORDER BY score DESC, id ASC
                """,
                params,
            ).fetchall()

            token_frequency = {}
            items = []
            for row in rows:
                tokens = set(self._split_prompt_tokens(row["tags_prompt"]))
                if not tokens:
                    continue
                for token in tokens:
                    token_frequency[token] = token_frequency.get(token, 0) + 1
                items.append({"id": int(row["id"]), "tokens": tokens})

            def prefix_tokens(tokens):
                sorted_tokens = sorted(tokens, key=lambda item: (token_frequency.get(item, 0), item))
                required_overlap = math.ceil(threshold * len(tokens))
                prefix_len = max(1, len(tokens) - required_overlap + 1)
                return sorted_tokens[:prefix_len]

            groups = []
            prefix_to_groups = {}
            delete_ids = []
            compared = 0

            for item in items:
                tokens = item["tokens"]
                token_count = len(tokens)
                candidate_group_ids = set()
                item_prefix_tokens = prefix_tokens(tokens)
                for token in item_prefix_tokens:
                    candidate_group_ids.update(prefix_to_groups.get(token, ()))

                matched_group = None
                matched_group_id = None
                for group_id in candidate_group_ids:
                    group = groups[group_id]
                    if not self._can_reach_similarity(token_count, group["min_len"], threshold) and not self._can_reach_similarity(token_count, group["max_len"], threshold):
                        if token_count < group["min_len"] and (token_count / float(group["min_len"])) < threshold:
                            continue
                        if token_count > group["max_len"] and (group["max_len"] / float(token_count)) < threshold:
                            continue
                    for member_tokens in group["members"]:
                        if not self._can_reach_similarity(token_count, len(member_tokens), threshold):
                            continue
                        compared += 1
                        if self._tag_jaccard(tokens, member_tokens) >= threshold:
                            matched_group = group
                            matched_group_id = group_id
                            break
                    if matched_group is not None:
                        break

                if matched_group is None:
                    group_id = len(groups)
                    groups.append({
                        "members": [tokens],
                        "kept": 1,
                        "min_len": token_count,
                        "max_len": token_count,
                    })
                    for token in item_prefix_tokens:
                        prefix_to_groups.setdefault(token, set()).add(group_id)
                    continue

                matched_group["members"].append(tokens)
                matched_group["min_len"] = min(matched_group["min_len"], token_count)
                matched_group["max_len"] = max(matched_group["max_len"], token_count)
                for token in item_prefix_tokens:
                    prefix_to_groups.setdefault(token, set()).add(matched_group_id)
                if matched_group["kept"] < keep_per_group:
                    matched_group["kept"] += 1
                else:
                    delete_ids.append(item["id"])

            similar_records = self._records_by_ids(conn, delete_ids)
            (
                similar_current_index,
                similar_deleted_before_cursor,
                similar_new_total,
            ) = self._deletion_cursor_adjustment(conn, delete_ids)
            similar_removed = self._delete_ids_in_chunks(conn, delete_ids) if delete_ids else 0
            self._apply_cursor_after_deletion(
                conn,
                similar_current_index,
                similar_deleted_before_cursor,
                similar_new_total,
            )
            self._ensure_duplicate_index(conn)
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("duplicate_key_migration_v4", "4"),
            )
            conn.commit()
            self._save_last_deleted_after_commit(
                exact_records + similar_records,
                "compact_similar",
                backup_path,
            )
            total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            return {
                "updated": updated,
                "exact_removed": exact_removed,
                "similar_removed": similar_removed,
                "checked": len(rows),
                "compared": compared,
                "groups": len(groups),
                "threshold": threshold,
                "keep_per_group": keep_per_group,
                "total": total,
                "backup_path": backup_path,
            }
        except Exception as e:
            conn.rollback()
            log_event(logger, "cache_mutation_failure", "Similarity compaction failed: %s", e)
            return {
                "updated": 0,
                "exact_removed": 0,
                "similar_removed": 0,
                "checked": 0,
                "compared": 0,
                "groups": 0,
                "threshold": threshold,
                "keep_per_group": keep_per_group,
                "total": 0,
                "backup_path": backup_path,
                "error": str(e),
            }
        finally:
            conn.close()

    def get_status(self):
        conn = self._connect()
        try:
            main_total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            row = conn.execute(
                "SELECT value FROM metadata WHERE key = ?",
                ("current_index",),
            ).fetchone()
            current_index = self._safe_int(row["value"], 0) if row else 0
            self.current_index = current_index
            active_total = main_total
            if main_total <= 0:
                return "缓存为空"
            read_count = max(0, min(current_index, active_total))
            next_position = 1 if current_index >= active_total else current_index + 1
            return (
                f"主缓存下一条: {next_position} / 活动总数: {active_total}"
                f"（已读 {read_count}）| 主缓存总数: {main_total}"
            )
        except Exception:
            return "缓存状态未知"
        finally:
            conn.close()

    @staticmethod
    def parse_search_syntax(query="", must_include="", must_exclude=""):
        include = TagCacheManager._split_keywords(must_include)
        exclude = TagCacheManager._split_keywords(must_exclude)
        normalized = str(query or "").replace("，", " ").replace(",", " ").replace("\n", " ")
        try:
            tokens = shlex.split(normalized)
        except ValueError:
            tokens = normalized.split()
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            lowered = token.lower()
            if lowered.startswith(("include:", "包含:")):
                include.extend(TagCacheManager._split_keywords(token.split(":", 1)[1]))
            elif lowered.startswith(("exclude:", "排除:")):
                exclude.extend(TagCacheManager._split_keywords(token.split(":", 1)[1]))
            elif token.startswith("+") and len(token) > 1:
                include.append(TagCacheManager._normalize_tag_token(token[1:]))
            elif token.startswith("-") and len(token) > 1:
                exclude.append(TagCacheManager._normalize_tag_token(token[1:]))
            else:
                include.append(TagCacheManager._normalize_tag_token(token))
        include = list(dict.fromkeys([item for item in include if item]))
        exclude = list(dict.fromkeys([item for item in exclude if item]))
        return include, exclude

    def _where_for_terms(self, include=None, exclude=None):
        include = include or []
        exclude = exclude or []
        clauses = []
        params = []
        tag_expr = (
            "(',' || LOWER(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(tags_prompt, tags), "
            "'，', ','), CHAR(10), ','), CHAR(13), ','), ' ', '_')) || ',')"
        )
        raw_expr = (
            "(',' || LOWER(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(tags_raw, tags), "
            "'，', ','), CHAR(10), ','), CHAR(13), ','), ' ', ',')) || ',')"
        )
        for keyword in include:
            pattern = f"%,{self._escape_like(keyword)},%"
            clauses.append(f"({tag_expr} LIKE ? ESCAPE '\\' OR {raw_expr} LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])
        for keyword in exclude:
            pattern = f"%,{self._escape_like(keyword)},%"
            clauses.append(f"({tag_expr} NOT LIKE ? ESCAPE '\\' AND {raw_expr} NOT LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return where, params

    def _query_records(
        self,
        include=None,
        exclude=None,
        sort_order="ID",
        limit=200,
        offset=0,
        random_seed=None,
        visible_positions=False,
    ):
        where, params = self._where_for_terms(include, exclude)
        order = self._sort_sql(sort_order, random_seed)
        if visible_positions:
            from_sql = """
                FROM (
                    SELECT tags.*, ROW_NUMBER() OVER (ORDER BY id ASC) AS visible_position
                    FROM tags
                ) AS tags
            """
            id_expr = "visible_position"
        else:
            from_sql = "FROM tags"
            id_expr = "id"
        limit_sql = ""
        if limit and int(limit) > 0:
            limit_sql = " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset or 0)])
        conn = self._connect()
        try:
            cursor = conn.execute(
                f"""
                SELECT {id_expr} AS display_id, id AS tag_id, booru, post_id, score, rating,
                       COALESCE(tags_prompt, tags) AS tags_prompt
                {from_sql}
                {where}
                ORDER BY {order}
                {limit_sql}
                """,
                params,
            )
            return [
                (
                    row["display_id"],
                    row["booru"] or "",
                    row["post_id"] or "",
                    row["score"] or 0,
                    row["rating"] or "",
                    row["tags_prompt"] or "",
                    int(row["tag_id"]),
                )
                for row in cursor.fetchall()
            ]
        finally:
            conn.close()

    def _deletion_cursor_adjustment(self, conn, deleted_ids):
        deleted_ids = set(int(tag_id) for tag_id in deleted_ids)
        current_row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?",
            ("current_index",),
        ).fetchone()
        current_index = max(
            0,
            self._safe_int(current_row["value"] if current_row else 0, 0),
        )
        if not deleted_ids:
            total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            return current_index, 0, total

        deleted_before_cursor = 0
        for tag_id in deleted_ids:
            position = conn.execute(
                "SELECT COUNT(*) AS c FROM tags WHERE id < ?",
                (tag_id,),
            ).fetchone()["c"]
            if position < current_index:
                deleted_before_cursor += 1
        total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
        existing_deleted = conn.execute(
            f"SELECT COUNT(*) AS c FROM tags WHERE id IN ({','.join('?' for _ in deleted_ids)})",
            list(deleted_ids),
        ).fetchone()["c"]
        return (
            current_index,
            deleted_before_cursor,
            max(0, total - existing_deleted),
        )

    def _apply_cursor_after_deletion(
        self,
        conn,
        current_index,
        deleted_before_cursor,
        new_total,
    ):
        next_index = max(0, int(current_index or 0) - int(deleted_before_cursor or 0))
        next_index = max(0, min(next_index, int(new_total or 0)))
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            ("current_index", str(next_index)),
        )
        self.current_index = next_index

    def get_active_total(self):
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
        finally:
            conn.close()

    def jump_to_position(self, position):
        try:
            position = int(float(position))
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "message": "请输入有效的缓存序号", "total": self.get_active_total()}

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            if total <= 0:
                conn.rollback()
                return {"ok": False, "message": "缓存为空，请先批量爬取", "total": total}
            if position < 1 or position > total:
                conn.rollback()
                return {
                    "ok": False,
                    "message": f"序号 {position} 超出范围（1-{total}）",
                    "total": total,
                }

            self.current_index = position - 1
            self._save_index(conn)
            conn.commit()
            return {
                "ok": True,
                "position": position,
                "current_index": self.current_index,
                "total": total,
            }
        except Exception:
            conn.rollback()
            self._load_index()
            raise
        finally:
            conn.close()

    def _tag_id_at_main_position(self, position, conn=None):
        try:
            position = int(float(position))
        except (TypeError, ValueError, OverflowError):
            return None
        if position < 1:
            return None
        own_conn = conn is None
        conn = conn or self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM tags ORDER BY id ASC LIMIT 1 OFFSET ?",
                (position - 1,),
            ).fetchone()
            return int(row["id"]) if row else None
        finally:
            if own_conn:
                conn.close()

    def tag_id_at_position(self, position):
        return self._tag_id_at_main_position(position)

    def get_by_position(self, position, prefer_natural=False):
        record = self.get_record_by_position(position)
        if not record:
            return None
        natural_prompt = str(record.get("natural_prompt") or "").strip()
        if prefer_natural and natural_prompt:
            return natural_prompt
        return str(record.get("tags_prompt") or "")

    def get_record_by_position(self, position):
        try:
            position = int(float(position))
        except (TypeError, ValueError, OverflowError):
            return None
        if position < 1:
            return None
        tag_id = self.tag_id_at_position(position)
        record = self.get_record_by_id(tag_id) if tag_id is not None else None
        if record:
            record["position"] = position
        return record

    def get_next_preview(self):
        total = self.get_active_total()
        if total <= 0:
            return None
        position = 1 if self.current_index >= total else self.current_index + 1
        return self.get_record_by_position(position)

    def _active_position_id_pairs(self, positions):
        positions = list(dict.fromkeys(int(position) for position in positions if int(position) > 0))
        if not positions:
            return []

        conn = self._connect()
        try:
            rows = conn.execute("SELECT id FROM tags ORDER BY id ASC").fetchall()
            ids = [int(row["id"]) for row in rows]
        finally:
            conn.close()
        return [
            (position, ids[position - 1])
            for position in positions
            if position <= len(ids)
        ]

    def get_records_by_position_spec(self, position_spec, only_missing=False):
        positions = self.parse_positions(position_spec, self.get_active_total())
        pairs = self._active_position_id_pairs(positions)
        ids = [tag_id for _, tag_id in pairs]
        position_map = {tag_id: position for position, tag_id in pairs}
        conn = self._connect()
        try:
            records = self._records_by_ids(conn, ids, position_map=position_map)
        finally:
            conn.close()
        if only_missing:
            records = [
                record
                for record in records
                if not str(record.get("natural_prompt") or "").strip()
            ]
        return records

    def preview_natural_conversion(self, position_spec, only_missing=False, sample_limit=8):
        positions = self.parse_positions(position_spec, self.get_active_total())
        all_records = self.get_records_by_position_spec(
            position_spec,
            only_missing=False,
        )
        records = (
            [
                record
                for record in all_records
                if not str(record.get("natural_prompt") or "").strip()
            ]
            if only_missing
            else all_records
        )
        result = self._preview_result(
            records,
            requested=len(positions),
            sample_limit=sample_limit,
            title="自然语言预转换预览",
        )
        result["matched"] = len(all_records)
        result["selected"] = len(records)
        result["converted"] = sum(
            1
            for record in all_records
            if str(record.get("natural_prompt") or "").strip()
        )
        result["missing"] = sum(
            1
            for record in all_records
            if not str(record.get("natural_prompt") or "").strip()
        )
        return result

    def update_natural_prompts(
        self,
        updates,
        preset="",
        model="",
        only_missing=False,
        converter_version=None,
    ):
        updates = list(updates or [])
        normalized = []
        rejected = 0
        for update in updates:
            try:
                tag_id = int(update.get("id"))
            except (AttributeError, TypeError, ValueError):
                rejected += 1
                continue
            natural_prompt = str(update.get("natural_prompt") or "").strip()
            source_tags_prompt = str(update.get("source_tags_prompt") or "").strip()
            if tag_id <= 0 or not natural_prompt or not source_tags_prompt:
                rejected += 1
                continue
            normalized.append(
                (
                    tag_id,
                    natural_prompt,
                    source_tags_prompt,
                    self._natural_source_hash(source_tags_prompt),
                )
            )
        if not normalized:
            return {
                "requested": len(updates),
                "updated": 0,
                "stale_or_missing": 0,
                "rejected": rejected,
            }

        timestamp = datetime.now().isoformat(timespec="seconds")
        converter_version = str(
            converter_version
            if converter_version is not None
            else self.NATURAL_CONVERTER_VERSION
        ).strip()
        updated = 0
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for tag_id, natural_prompt, source_tags_prompt, source_hash in normalized:
                missing_sql = (
                    """
                    AND (
                        COALESCE(natural_prompt, '') = ''
                        OR COALESCE(natural_source_hash, '') != ?
                        OR COALESCE(natural_converter_version, '') != ?
                    )
                    """
                    if only_missing
                    else ""
                )
                cursor = conn.execute(
                    f"""
                    UPDATE tags
                    SET natural_prompt = ?,
                        natural_preset = ?,
                        natural_model = ?,
                        natural_updated_at = ?,
                        natural_source_hash = ?,
                        natural_converter_version = ?
                    WHERE id = ?
                      AND COALESCE(tags_prompt, tags) = ?
                      {missing_sql}
                    """,
                    (
                        natural_prompt,
                        str(preset or ""),
                        str(model or ""),
                        timestamp,
                        source_hash,
                        converter_version,
                        tag_id,
                        source_tags_prompt,
                        *((source_hash, converter_version) if only_missing else ()),
                    ),
                )
                updated += max(0, int(cursor.rowcount or 0))
            conn.commit()
            return {
                "requested": len(normalized) + rejected,
                "updated": updated,
                "stale_or_missing": max(0, len(normalized) - updated),
                "rejected": rejected,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def clear_natural_prompts_by_ids(self, tag_ids):
        ids = []
        for tag_id in tag_ids or []:
            try:
                tag_id = int(tag_id)
            except (TypeError, ValueError):
                continue
            if tag_id > 0 and tag_id not in ids:
                ids.append(tag_id)
        if not ids:
            return 0

        updated = 0
        conn = self._connect()
        try:
            for start in range(0, len(ids), 800):
                chunk = ids[start:start + 800]
                placeholders = ",".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"""
                    UPDATE tags
                    SET natural_prompt = '',
                        natural_preset = '',
                        natural_model = '',
                        natural_updated_at = '',
                        natural_source_hash = '',
                        natural_converter_version = ''
                    WHERE id IN ({placeholders})
                      AND COALESCE(natural_prompt, '') != ''
                    """,
                    chunk,
                )
                updated += max(0, int(cursor.rowcount or 0))
            conn.commit()
            return updated
        finally:
            conn.close()

    def get_natural_prompt_status(self):
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT COALESCE(tags_prompt, tags) AS tags_prompt,
                       natural_prompt,
                       natural_source_hash,
                       natural_converter_version
                FROM tags
                """
            ).fetchall()
            total = len(rows)
            converted = sum(
                1
                for row in rows
                if str(row["natural_prompt"] or "").strip()
                and str(row["natural_source_hash"] or "")
                == self._natural_source_hash(row["tags_prompt"])
                and str(row["natural_converter_version"] or "")
                == self.NATURAL_CONVERTER_VERSION
            )
            return {
                "total": total,
                "converted": converted,
                "missing": max(0, total - converted),
            }
        finally:
            conn.close()

    def get_prompt_rag_candidates(
        self,
        preset="",
        min_score_percentile=0.75,
        per_booru_limit=512,
        max_candidates=None,
    ):
        """Load a bounded snapshot of valid high-score prompt conversion pairs."""
        try:
            min_score_percentile = float(min_score_percentile)
        except (TypeError, ValueError, OverflowError):
            min_score_percentile = 0.75
        min_score_percentile = max(0.0, min(1.0, min_score_percentile))
        preset = str(preset or "").strip()
        max_candidates = self._safe_int(
            max_candidates,
            self.MAX_PROMPT_RAG_CANDIDATES,
        )
        max_candidates = max(1, min(max_candidates, self.MAX_PROMPT_RAG_CANDIDATES))
        per_booru_limit = max(1, min(self._safe_int(per_booru_limit, 512), 2000))
        candidates = []
        booru_counts = {}
        offset = 0
        batch_size = max_candidates
        conn = self._connect()
        try:
            while len(candidates) < max_candidates:
                rows = conn.execute(
                    """
                    WITH ranked AS (
                        SELECT id, COALESCE(booru, '') AS booru,
                               COALESCE(tags_prompt, tags) AS tags_prompt,
                               natural_prompt, natural_preset, natural_source_hash,
                               natural_converter_version, COALESCE(score, 0) AS score,
                               PERCENT_RANK() OVER (
                                   PARTITION BY COALESCE(booru, '')
                                   ORDER BY COALESCE(score, 0)
                               ) AS score_percentile
                        FROM tags
                    )
                    SELECT id, booru, tags_prompt, natural_prompt, natural_preset,
                           natural_source_hash, natural_converter_version,
                           score, score_percentile
                    FROM ranked
                    WHERE score_percentile >= ?
                      AND COALESCE(natural_prompt, '') != ''
                      AND natural_converter_version = ?
                      AND (? = '' OR COALESCE(natural_preset, '') = ?)
                    ORDER BY score_percentile DESC, booru ASC, score DESC, id ASC
                    LIMIT ? OFFSET ?
                    """,
                    (
                        min_score_percentile,
                        self.NATURAL_CONVERTER_VERSION,
                        preset,
                        preset,
                        batch_size,
                        offset,
                    ),
                ).fetchall()
                if not rows:
                    break
                offset += len(rows)
                for row in rows:
                    tags_prompt = str(row["tags_prompt"] or "").strip()
                    natural_prompt = str(row["natural_prompt"] or "").strip()
                    booru = str(row["booru"] or "")
                    if not tags_prompt or not natural_prompt:
                        continue
                    if (
                        str(row["natural_source_hash"] or "")
                        != self._natural_source_hash(tags_prompt)
                    ):
                        continue
                    if booru_counts.get(booru, 0) >= per_booru_limit:
                        continue
                    candidates.append(
                        {
                            "id": int(row["id"]),
                            "booru": booru,
                            "tags_prompt": tags_prompt,
                            "natural_prompt": natural_prompt,
                            "score": self._safe_int(row["score"], 0),
                            "score_percentile": float(
                                row["score_percentile"] or 0.0
                            ),
                            "tokens": frozenset(
                                self._split_prompt_tokens(tags_prompt)
                            ),
                        }
                    )
                    booru_counts[booru] = booru_counts.get(booru, 0) + 1
                    if len(candidates) >= max_candidates:
                        break
        finally:
            conn.close()
        return candidates

    @classmethod
    def select_prompt_rag_examples(
        cls,
        query_tags,
        candidates,
        limit=3,
        min_similarity=0.08,
    ):
        """Rank relevant conversion pairs by tag similarity with a score prior."""
        limit = max(0, min(cls._safe_int(limit, 3), cls.MAX_PROMPT_RAG_EXAMPLES))
        query_tokens = frozenset(cls._split_prompt_tokens(query_tags))
        if not query_tokens or limit <= 0:
            return []

        try:
            min_similarity = float(min_similarity)
        except (TypeError, ValueError, OverflowError):
            min_similarity = 0.08
        min_similarity = max(0.0, min(1.0, min_similarity))
        usable = [candidate for candidate in candidates or [] if candidate.get("tokens")]
        query_key = cls._canonical_tag_key(query_tags)
        ranked = []
        for candidate in usable:
            candidate_tokens = frozenset(candidate["tokens"])
            if cls._canonical_tag_key(candidate.get("tags_prompt")) == query_key:
                continue
            overlap = len(query_tokens & candidate_tokens)
            if overlap <= 0:
                continue
            if min(len(query_tokens), len(candidate_tokens)) >= 3 and overlap < 2:
                continue
            union_size = len(query_tokens | candidate_tokens)
            jaccard = overlap / union_size if union_size else 0.0
            coverage = overlap / len(query_tokens)
            similarity = (0.65 * jaccard) + (0.35 * coverage)
            if similarity < min_similarity:
                continue
            score_quality = max(
                0.0,
                min(1.0, float(candidate.get("score_percentile") or 0.0)),
            )
            rank_score = (0.85 * similarity) + (0.15 * score_quality)
            ranked.append(
                (
                    -rank_score,
                    -overlap,
                    -score_quality,
                    str(candidate.get("booru") or ""),
                    cls._safe_int(candidate.get("id"), 0),
                    candidate,
                    similarity,
                )
            )

        ranked.sort(key=lambda item: item[:5])
        examples = []
        for _, overlap_sort, _, _, _, candidate, similarity in ranked[:limit]:
            examples.append(
                {
                    "id": candidate.get("id"),
                    "tags_prompt": str(candidate.get("tags_prompt") or ""),
                    "natural_prompt": str(candidate.get("natural_prompt") or ""),
                    "score": cls._safe_int(candidate.get("score"), 0),
                    "score_percentile": round(
                        float(candidate.get("score_percentile") or 0.0),
                        6,
                    ),
                    "overlap": -overlap_sort,
                    "similarity": round(similarity, 6),
                }
            )
        return examples

    def _preview_result(self, records, requested=0, sample_limit=8, title="预览"):
        records = list(records or [])
        sample = records[: max(1, int(sample_limit or 8))]
        return {
            "ok": True,
            "title": title,
            "requested": int(requested or len(records)),
            "count": len(records),
            "sample": sample,
            "total": self.get_active_total(),
        }

    def preview_delete_by_positions(self, position_spec, sample_limit=8):
        positions = self.parse_positions(position_spec, self.get_active_total())
        position_map = {}
        ids = []
        for position in positions:
            tag_id = self.tag_id_at_position(position)
            if tag_id is not None and tag_id not in position_map:
                ids.append(tag_id)
                position_map[tag_id] = position
        conn = self._connect()
        try:
            records = self._records_by_ids(conn, ids, position_map=position_map)
        finally:
            conn.close()
        return self._preview_result(records, requested=len(positions), sample_limit=sample_limit, title="按序号删除预览")

    def _any_tags_where_params(self, tags):
        terms = self._split_keywords(tags)
        if not terms:
            return "", []
        tag_expr = (
            "(',' || LOWER(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(tags_prompt, tags), "
            "'，', ','), CHAR(10), ','), CHAR(13), ','), ' ', '_')) || ',')"
        )
        raw_expr = (
            "(',' || LOWER(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(tags_raw, tags), "
            "'，', ','), CHAR(10), ','), CHAR(13), ','), ' ', ',')) || ',')"
        )
        clauses = []
        params = []
        for term in terms:
            pattern = f"%,{self._escape_like(term)},%"
            clauses.append(f"({tag_expr} LIKE ? ESCAPE '\\' OR {raw_expr} LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])
        return f"WHERE {' OR '.join(clauses)}", params

    def preview_delete_by_any_tags(self, tags, sample_limit=8):
        where, params = self._any_tags_where_params(tags)
        if not where:
            return {"ok": False, "message": "请输入要预览删除的 tag", "count": 0, "sample": []}
        conn = self._connect()
        try:
            ids = self._ids_matching_where(conn, where, params)
            records = self._records_by_ids(conn, ids)
        finally:
            conn.close()
        return self._preview_result(records, requested=len(ids), sample_limit=sample_limit, title="按 Tag 删除预览")

    def preview_delete_all(self, sample_limit=8):
        conn = self._connect()
        try:
            records = self._all_records(conn)
        finally:
            conn.close()
        return self._preview_result(records, requested=len(records), sample_limit=sample_limit, title="删除全部预览")

    def search_cache(self, keyword="", limit=200):
        include, exclude = self.parse_search_syntax(keyword)
        return self._query_records(include, exclude, limit=limit, visible_positions=True)

    def get_by_id(self, tag_id, prefer_natural=False):
        record = self.get_record_by_id(tag_id)
        if not record:
            return None
        if prefer_natural and str(record.get("natural_prompt") or "").strip():
            return record["natural_prompt"]
        return record["tags_prompt"]

    def get_record_by_id(self, tag_id):
        records = self.get_records_by_ids([tag_id])
        return records[0] if records else None

    def find_record_by_prompt(self, prompt):
        value = str(prompt or "").strip()
        if not value:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT id
                FROM tags
                WHERE tags_prompt = ? OR tags = ? OR natural_prompt = ?
                ORDER BY CASE WHEN natural_prompt = ? THEN 0 ELSE 1 END, id ASC
                LIMIT 1
                """,
                (value, value, value, value),
            ).fetchone()
        finally:
            conn.close()
        return self.get_record_by_id(int(row["id"])) if row else None

    def get_records_by_ids(self, tag_ids):
        conn = self._connect()
        try:
            return self._records_by_ids(conn, tag_ids)
        finally:
            conn.close()

    def _to_prompt_batch_record(self, row, position):
        metadata = self._prompt_batch_metadata(row)
        positive = str(row.get("tags_prompt") or "")
        natural = str(row.get("natural_prompt") or "")
        if len(positive) > PROMPT_BATCH_MAX_PROMPT_LENGTH or len(natural) > PROMPT_BATCH_MAX_PROMPT_LENGTH:
            raise ValueError(f"记录 {row.get('id', position)} 的 Prompt 超过 {PROMPT_BATCH_MAX_PROMPT_LENGTH} 字符")
        prompt = {"positive": positive, "natural": natural}
        if natural:
            prompt["processed"] = natural
        result = {
            "record_id": str(metadata.get("record_id") or row.get("id", "")),
            "index": position,
            "image": {
                "filename": str(metadata.get("filename") or ""),
                "sha256": str(metadata.get("sha256") or ""),
                "source_url": row.get("source_url", ""),
                "preview_url": row.get("preview_url", ""),
            },
            "prompt": prompt,
            "booru": {
                "site": row.get("booru", ""),
                "post_id": row.get("post_id", ""),
                "score": row.get("score", 0),
                "rating": row.get("rating", ""),
            },
        }
        if metadata.get("status"):
            result["status"] = str(metadata["status"])
        if metadata.get("error"):
            result["error"] = str(metadata["error"])
        if metadata.get("appended") is True:
            result["appended"] = True
        return result

    def export_records(self, file_format="json", file_path=None):
        file_format = str(file_format or "json").lower().strip()
        if file_format not in ("json", "csv"):
            file_format = "json"
        if not file_path:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            file_path = os.path.join(self.cache_dir, f"tag_cache_export_{timestamp}.{file_format}")
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)

        fieldnames = [
            "id", "booru", "post_id", "tags_raw", "tags_prompt",
            "natural_prompt", "natural_preset", "natural_model", "natural_updated_at",
            "natural_source_hash", "natural_converter_version",
            "score", "rating", "source_url", "preview_url", "search_query",
        ]
        conn = self._connect()
        try:
            all_rows = [
                {field: record.get(field, "") for field in fieldnames}
                for record in self._all_records(conn)
            ]
        finally:
            conn.close()

        if file_format == "csv":
            rows = all_rows
            with open(file_path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        else:
            records = [
                self._to_prompt_batch_record(row, position)
                for position, row in enumerate(all_rows, 1)
            ]
            payload = {
                "schema_version": PROMPT_BATCH_SCHEMA,
                "producer": {"name": "ranbooru"},
                "records": records,
            }
            content = json.dumps(payload, ensure_ascii=False, indent=2)
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write(content)
            rows = records
        return {
            "path": file_path,
            "count": len(rows),
            "total_count": len(all_rows),
            "truncated": len(rows) < len(all_rows),
            "format": file_format,
        }

    def _load_import_records(self, file_path):
        file_path = str(file_path or "").strip().strip('"')
        if not file_path:
            return {"ok": False, "message": "导入文件不存在", "records": []}
        try:
            file_path = os.path.realpath(os.path.abspath(file_path))
            file_stat = os.stat(file_path)
        except (OSError, ValueError) as e:
            return {"ok": False, "message": f"导入文件不存在: {e}", "records": []}
        if not os.path.isfile(file_path):
            return {"ok": False, "message": "导入路径不是普通文件", "records": []}
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in (".csv", ".json"):
            return {"ok": False, "message": "仅支持 JSON 或 CSV 导入", "records": []}
        is_large_file = file_stat.st_size > int(self.MAX_IMPORT_BYTES)
        if ext == ".csv" and is_large_file:
            return {
                "ok": False,
                "message": f"导入文件过大（上限 {self.MAX_IMPORT_BYTES} 字节）",
                "records": [],
            }
        if ext == ".json" and is_large_file:
            try:
                is_large_prompt_batch = _large_json_declares_prompt_batch(file_path)
            except (OSError, UnicodeError, ValueError):
                is_large_prompt_batch = False
            if not is_large_prompt_batch:
                return {
                    "ok": False,
                    "message": f"导入文件过大（上限 {self.MAX_IMPORT_BYTES} 字节）；超大 JSON 必须声明 prompt_batch.v1 schema_version",
                    "records": [],
                }
        records = []
        is_prompt_batch = False
        producer_name = ""
        try:
            if ext == ".csv":
                with open(file_path, "r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        records.append(dict(row))
                        if len(records) > int(self.MAX_IMPORT_RECORDS):
                            return {
                                "ok": False,
                                "message": f"导入记录过多（上限 {self.MAX_IMPORT_RECORDS} 条）",
                                "records": [],
                            }
            else:
                with open(file_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    if payload.get("schema_version"):
                        if payload.get("schema_version") != PROMPT_BATCH_SCHEMA:
                            return {"ok": False, "message": "不支持的 prompt_batch schema", "records": []}
                        is_prompt_batch = True
                        producer = payload.get("producer") if isinstance(payload.get("producer"), dict) else {}
                        producer_name = str(producer.get("name") or "unknown")[:160]
                    records = payload.get("records") or payload.get("tags") or []
                elif isinstance(payload, list):
                    records = payload
        except Exception as e:
            return {"ok": False, "message": f"导入失败: {e}", "records": []}

        if not isinstance(records, list) or not records:
            return {"ok": False, "message": "导入文件没有可用记录", "records": []}
        if not is_prompt_batch and len(records) > int(self.MAX_IMPORT_RECORDS):
            return {
                "ok": False,
                "message": f"导入记录过多（上限 {self.MAX_IMPORT_RECORDS} 条）",
                "records": [],
            }

        normalized = []
        seen_record_ids = set()
        for record_index, record in enumerate(records, 1):
            if isinstance(record, str):
                if is_prompt_batch:
                    return {"ok": False, "message": "prompt_batch.v1 的记录必须是对象", "records": []}
                normalized.append({"tags_prompt": record})
            elif isinstance(record, dict):
                prompt = record.get("prompt") or {}
                image = record.get("image") or {}
                booru = record.get("booru") or {}
                if is_prompt_batch and (not isinstance(prompt, dict) or not isinstance(image, dict)):
                    return {"ok": False, "message": "prompt_batch.v1 记录缺少 image 或 prompt", "records": []}
                if not isinstance(prompt, dict) or not isinstance(image, dict) or not isinstance(booru, (dict, str)):
                    continue
                positive = record.get("tags_prompt") or prompt.get("positive") or ""
                natural = record.get("natural_prompt") or prompt.get("natural") or prompt.get("processed") or ""
                if is_prompt_batch:
                    if not str(positive).strip():
                        return {"ok": False, "message": "prompt_batch.v1 记录缺少正向 Prompt", "records": []}
                    if len(str(positive)) > PROMPT_BATCH_MAX_PROMPT_LENGTH or len(str(natural)) > PROMPT_BATCH_MAX_PROMPT_LENGTH:
                        return {"ok": False, "message": "prompt_batch.v1 的 Prompt 超过 12000 字符", "records": []}
                    record_id = str(record.get("record_id") or "").strip()
                    sha256 = str(image.get("sha256") or "")
                    if sha256 and (len(sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in sha256)):
                        return {"ok": False, "message": f"prompt_batch.v1 第 {record_index} 条 sha256 必须是 64 位十六进制", "records": []}
                    if not record_id:
                        identity = f"{producer_name}\x1f{sha256}\x1f{image.get('filename', '')}\x1f{positive}"
                        record_id = f"generated-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
                    if record_id in seen_record_ids:
                        return {"ok": False, "message": f"prompt_batch.v1 第 {record_index} 条 record_id 重复: {record_id}", "records": []}
                    seen_record_ids.add(record_id)
                else:
                    record_id = record.get("record_id") or ""
                normalized_record = {
                    **record,
                    "record_id": record_id,
                    "prompt_batch_producer": producer_name,
                    "tags_prompt": positive,
                    "natural_prompt": natural,
                    "natural_source_hash": record.get("natural_source_hash") or (self._natural_source_hash(positive) if natural else ""),
                    "natural_converter_version": record.get("natural_converter_version") or (self.NATURAL_CONVERTER_VERSION if natural else ""),
                    "source_url": record.get("source_url") or image.get("source_url") or "",
                    "preview_url": record.get("preview_url") or image.get("preview_url") or "",
                    "booru": record.get("booru") if isinstance(record.get("booru"), str) else booru.get("site", ""),
                    "post_id": record.get("post_id") or booru.get("post_id", ""),
                    "score": record.get("score", booru.get("score", 0)),
                    "rating": record.get("rating", booru.get("rating", "")),
                }
                normalized_record["search_query"] = record.get("search_query") or self.prompt_batch_search_query(normalized_record)
                normalized.append(normalized_record)
        if not normalized:
            return {"ok": False, "message": "导入文件没有可用 tag", "records": []}
        return {"ok": True, "records": normalized, "path": file_path}

    def preview_import_records(self, file_path, append=True, dedupe=True):
        loaded = self._load_import_records(file_path)
        if not loaded.get("ok"):
            return {
                "ok": False,
                "message": loaded.get("message", "导入预检失败"),
                "source_count": 0,
                "inserted": 0,
                "skipped_duplicate": 0,
                "skipped_empty": 0,
                "current_total": self.get_active_total(),
                "estimated_total": self.get_active_total(),
                "sample": [],
            }

        conn = self._connect()
        try:
            current_total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            existing_keys = {
                row["duplicate_key"]
                for row in conn.execute("SELECT duplicate_key FROM tags WHERE duplicate_key IS NOT NULL AND duplicate_key != ''").fetchall()
            } if append else set()
        finally:
            conn.close()

        stats = {
            "ok": True,
            "path": loaded.get("path", ""),
            "source_count": len(loaded["records"]),
            "inserted": 0,
            "skipped_duplicate": 0,
            "skipped_empty": 0,
            "current_total": current_total,
            "estimated_total": current_total,
            "append": bool(append),
            "dedupe": bool(dedupe),
            "sample": [],
        }
        seen_keys = set()
        for item in loaded["records"]:
            record = self._coerce_record(item)
            if not record["tags_prompt"]:
                stats["skipped_empty"] += 1
                continue
            duplicate_key = record["duplicate_key"]
            if dedupe and (duplicate_key in existing_keys or duplicate_key in seen_keys):
                stats["skipped_duplicate"] += 1
                continue
            seen_keys.add(duplicate_key)
            stats["inserted"] += 1
            if len(stats["sample"]) < 8:
                stats["sample"].append(record)
        stats["estimated_total"] = (current_total if append else 0) + stats["inserted"]
        return stats

    def import_records(self, file_path, append=True, dedupe=True):
        loaded = self._load_import_records(file_path)
        if not loaded.get("ok"):
            return {"ok": False, "message": loaded.get("message", "导入失败"), "inserted": 0, "total": self.get_active_total()}

        stats = (
            self.append_records(loaded["records"], dedupe=dedupe)
            if append
            else self.save_records(loaded["records"], dedupe=dedupe, backup_reason="import_overwrite")
        )
        return {"ok": True, **stats}

    @_serialized_destructive_operation
    def delete_by_ids(self, tag_ids):
        ids = list(dict.fromkeys(int(tag_id) for tag_id in tag_ids if str(tag_id).strip().isdigit()))
        if not ids:
            return 0
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_ids = self._existing_ids(conn, ids)
            if not existing_ids:
                return 0
            deleted_records = self._records_by_ids(conn, existing_ids)
            backup_path = self.backup_db("delete_ids") if existing_ids else ""
            placeholders = ",".join("?" for _ in existing_ids)
            current_index, deleted_before_cursor, new_total = self._deletion_cursor_adjustment(
                conn,
                existing_ids,
            )
            cursor = conn.execute(f"DELETE FROM tags WHERE id IN ({placeholders})", existing_ids)
            self._apply_cursor_after_deletion(
                conn,
                current_index,
                deleted_before_cursor,
                new_total,
            )
            conn.commit()
            self._save_last_deleted_after_commit(deleted_records, "delete_ids", backup_path)
            return cursor.rowcount
        except Exception as e:
            conn.rollback()
            log_event(logger, "cache_mutation_failure", "ID deletion failed: %s", e)
            return 0
        finally:
            conn.close()

    @_serialized_destructive_operation
    def delete_by_any_tags(self, tags):
        where, params = self._any_tags_where_params(tags)
        if not where:
            return 0
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            deleted_ids = self._ids_matching_where(conn, where, params)
            deleted_records = self._records_by_ids(conn, deleted_ids)
            backup_path = self.backup_db("delete_tags") if deleted_ids else ""
            current_index, deleted_before_cursor, new_total = self._deletion_cursor_adjustment(
                conn,
                deleted_ids,
            )
            cursor = conn.execute(f"DELETE FROM tags {where}", params)
            self._apply_cursor_after_deletion(
                conn,
                current_index,
                deleted_before_cursor,
                new_total,
            )
            conn.commit()
            self._save_last_deleted_after_commit(deleted_records, "delete_tags", backup_path)
            return cursor.rowcount
        except Exception as e:
            conn.rollback()
            log_event(logger, "cache_mutation_failure", "Tag deletion failed: %s", e)
            return 0
        finally:
            conn.close()

    def get_next_tags_batch_from_pool(
        self,
        count,
        loop=True,
        prefer_natural=False,
        include_prompt_metadata=False,
    ):
        count = max(0, min(self._safe_int(count, 0), self.MAX_POSITION_SELECTION))
        if count <= 0:
            return [], self.current_index, self.get_active_total()

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM metadata WHERE key = ?",
                ("current_index",),
            ).fetchone()
            current_index = max(0, self._safe_int(row["value"] if row else 0, 0))
            total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            select_sql = """
                SELECT COALESCE(tags_prompt, tags) AS tags_prompt,
                       COALESCE(natural_prompt, '') AS natural_prompt,
                       COALESCE(natural_source_hash, '') AS natural_source_hash,
                       COALESCE(natural_converter_version, '') AS natural_converter_version
                FROM tags
                ORDER BY id ASC
                LIMIT ? OFFSET ?
            """

            total = int(total or 0)
            if total <= 0:
                current_index = 0
                rows = []
            else:
                current_index = min(current_index, total)
                rows = []
                remaining = count
                while remaining > 0:
                    if current_index >= total:
                        if not loop:
                            break
                        current_index = 0
                    take = min(remaining, total - current_index)
                    segment = conn.execute(select_sql, (take, current_index)).fetchall()
                    if not segment:
                        break
                    rows.extend(segment)
                    current_index += len(segment)
                    remaining -= len(segment)
                    if len(segment) < take:
                        break

            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("current_index", str(current_index)),
            )
            conn.commit()
            self.current_index = current_index
            prompts = []
            for row in rows:
                tags_prompt = str(row["tags_prompt"] or "")
                natural_prompt = str(row["natural_prompt"] or "").strip()
                valid_natural = (
                    natural_prompt
                    and str(row["natural_source_hash"] or "")
                    == self._natural_source_hash(tags_prompt)
                    and str(row["natural_converter_version"] or "")
                    == self.NATURAL_CONVERTER_VERSION
                )
                is_natural = bool(prefer_natural and valid_natural)
                selected_prompt = natural_prompt if is_natural else tags_prompt
                if include_prompt_metadata:
                    prompts.append({"prompt": selected_prompt, "is_natural": is_natural})
                else:
                    prompts.append(selected_prompt)
            return prompts, current_index, total
        except Exception:
            conn.rollback()
            self._load_index()
            raise
        finally:
            conn.close()

    def get_next_tags_from_pool(self, loop=True, prefer_natural=False):
        tags, current_index, total = self.get_next_tags_batch_from_pool(
            1,
            loop=loop,
            prefer_natural=prefer_natural,
        )
        return (tags[0] if tags else None), current_index, total
