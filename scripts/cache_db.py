"""
SQLite tag cache for Ranbooru.

Focus:
- hard dedupe for exact tag sets
- stable ordered reading for large caches
- lightweight keyword filtering
- optional filtered pools / rules for power users
"""

import os
import csv
import json
import math
import re
import shlex
import shutil
import sqlite3
from datetime import datetime


class TagCacheManager:
    TAG_COLUMNS = {
        "booru": "TEXT",
        "post_id": "TEXT",
        "tags_raw": "TEXT",
        "tags_prompt": "TEXT",
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
        self.current_index = 0
        self._init_db()
        self._load_index()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    query TEXT DEFAULT '',
                    must_include TEXT DEFAULT '',
                    must_exclude TEXT DEFAULT '',
                    sort_order TEXT DEFAULT 'ID',
                    limit_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS filtered_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tag_id INTEGER,
                    tags TEXT NOT NULL
                )
                """
            )
            self._migrate_tags_table(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_post ON tags (booru, post_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_score ON tags (score)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_filtered_pool_tag_id ON filtered_pool (tag_id)")
            conn.commit()
        finally:
            conn.close()

    def _migrate_tags_table(self, conn):
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(tags)")}
        for name, column_type in self.TAG_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE tags ADD COLUMN {name} {column_type}")
        conn.execute("UPDATE tags SET tags_prompt = tags WHERE tags_prompt IS NULL OR tags_prompt = ''")
        conn.execute("UPDATE tags SET tags_raw = tags WHERE tags_raw IS NULL OR tags_raw = ''")
        self._apply_duplicate_key_migration(conn, force=True)

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
        except Exception:
            return default

    @staticmethod
    def parse_positions(value, total=None):
        if not value:
            return []
        positions = []
        seen = set()
        for part in re.split(r"[,，\s]+", str(value).strip()):
            if not part:
                continue
            match = re.fullmatch(r"(\d+)\s*[-~:：]\s*(\d+)", part)
            if match:
                start = int(match.group(1))
                end = int(match.group(2))
                step = 1 if end >= start else -1
                for position in range(start, end + step, step):
                    if position > 0 and (total is None or position <= total) and position not in seen:
                        seen.add(position)
                        positions.append(position)
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

    @staticmethod
    def _normalize_tags(tags):
        return ",".join(TagCacheManager._split_prompt_tokens(tags))

    def _make_duplicate_key(self, record):
        tag_key = self._canonical_tag_key(
            record.get("tags_prompt") or record.get("tags") or record.get("tags_raw") or ""
        )
        if tag_key:
            return f"tags:{tag_key}"
        booru = str(record.get("booru") or "").strip()
        post_id = str(record.get("post_id") or "").strip()
        if booru and post_id:
            return f"post:{booru}:{post_id}"
        return "tags:"

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
            "score": self._safe_int(record.get("score"), 0),
            "rating": record.get("rating") or "",
            "source_url": record.get("source_url") or "",
            "preview_url": record.get("preview_url") or "",
            "search_query": record.get("search_query") or "",
        }
        coerced["duplicate_key"] = self._make_duplicate_key(coerced)
        return coerced

    def _set_metadata(self, key, value, conn=None):
        own_conn = conn is None
        conn = conn or self._connect()
        try:
            conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, str(value)))
            if own_conn:
                conn.commit()
        finally:
            if own_conn:
                conn.close()

    def _get_metadata(self, key, default=None):
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default
        finally:
            conn.close()

    def _rebuild_duplicate_keys(self, conn):
        rows = conn.execute(
            "SELECT id, booru, post_id, tags, tags_prompt, tags_raw, duplicate_key FROM tags ORDER BY id"
        ).fetchall()
        updated = 0
        for row in rows:
            tags_prompt = str(row["tags_prompt"] or row["tags_raw"] or row["tags"] or "").strip()
            tags_raw = str(row["tags_raw"] or row["tags"] or tags_prompt).strip()
            duplicate_key = self._make_duplicate_key(
                {
                    "booru": row["booru"],
                    "post_id": row["post_id"],
                    "tags_prompt": tags_prompt,
                    "tags": tags_raw,
                }
            )
            if row["duplicate_key"] != duplicate_key or row["tags_prompt"] != tags_prompt or row["tags_raw"] != tags_raw:
                conn.execute(
                    "UPDATE tags SET tags_prompt = ?, tags_raw = ?, duplicate_key = ? WHERE id = ?",
                    (tags_prompt, tags_raw, duplicate_key, row["id"]),
                )
                updated += 1
        return updated

    def _ensure_duplicate_index(self, conn):
        conn.execute("DROP INDEX IF EXISTS idx_tags_duplicate")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_duplicate ON tags (duplicate_key)")

    def _apply_duplicate_key_migration(self, conn, force=False):
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?",
            ("duplicate_key_migration_v2",),
        ).fetchone()
        if not force and row and row["value"] == "2":
            return {"updated": 0, "removed": 0}
        conn.execute("DROP INDEX IF EXISTS idx_tags_duplicate")
        updated = self._rebuild_duplicate_keys(conn)
        removed = 0
        self._ensure_duplicate_index(conn)
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            ("duplicate_key_migration_v2", "2"),
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
        shutil.copy2(self.db_path, backup_path)
        return backup_path

    def _last_deleted_path(self):
        return os.path.join(self.cache_dir, "last_deleted.json")

    def _row_to_record(self, row, position=None):
        record = {
            "id": int(row["id"]),
            "booru": row["booru"] or "",
            "post_id": row["post_id"] or "",
            "tags_raw": row["tags_raw"] or row["tags_prompt"] or row["tags"] or "",
            "tags_prompt": row["tags_prompt"] or row["tags"] or "",
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
                   (SELECT COUNT(*) FROM tags AS t_pos WHERE t_pos.id <= tags.id) AS position,
                   booru, post_id, tags, tags_raw, tags_prompt, score, rating,
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
                SELECT id,
                       (SELECT COUNT(*) FROM tags AS t_pos WHERE t_pos.id <= tags.id) AS position,
                       booru, post_id, tags, tags_raw, tags_prompt, score, rating,
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
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return path

    def get_last_deleted_info(self):
        path = self._last_deleted_path()
        if not os.path.exists(path):
            return {"ok": False, "message": "没有可撤销的删除记录", "count": 0}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return {
                "ok": True,
                "path": path,
                "created_at": payload.get("created_at", ""),
                "reason": payload.get("reason", ""),
                "count": len(payload.get("records") or []),
                "backup_path": payload.get("backup_path", ""),
            }
        except Exception as e:
            return {"ok": False, "message": f"读取撤销记录失败: {e}", "count": 0}

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

    def save_cache(self, tags_list):
        return self.save_records([{"tags_prompt": tags} for tags in tags_list])

    def append_cache(self, tags_list):
        result = self.append_records([{"tags_prompt": tags} for tags in tags_list])
        return result["total"]

    def save_records(self, records, dedupe=True, min_score=None, backup_reason="overwrite"):
        conn = self._connect()
        try:
            existing_records = self._all_records(conn)
            backup_path = self.backup_db(backup_reason) if existing_records else ""
            self._save_last_deleted(existing_records, backup_reason, backup_path)
            conn.execute("DELETE FROM tags")
            conn.execute("DELETE FROM filtered_pool")
            self.current_index = 0
            self._save_index(conn)
            conn.commit()
        finally:
            conn.close()
        stats = self.append_records(records, dedupe=dedupe, min_score=min_score)
        stats["backup_path"] = backup_path
        return stats

    def append_records(self, records, dedupe=True, min_score=None):
        stats = {"inserted": 0, "skipped_duplicate": 0, "skipped_score": 0, "skipped_empty": 0, "total": 0}
        conn = self._connect()
        try:
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
                        booru, post_id, tags, tags_raw, tags_prompt, score, rating,
                        source_url, preview_url, search_query, duplicate_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["booru"],
                        record["post_id"],
                        record["tags_prompt"],
                        record["tags_raw"],
                        record["tags_prompt"],
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
            conn.commit()
            stats["total"] = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            return stats
        finally:
            conn.close()

    def get_next_tags(self, loop=True):
        return self.get_next_tags_from_pool(loop=loop)

    def reset_index(self):
        self.current_index = 0
        self._save_index()

    def delete_cache(self):
        try:
            deleted_records = []
            if os.path.exists(self.db_path):
                conn = self._connect()
                try:
                    deleted_records = self._all_records(conn)
                finally:
                    conn.close()
            backup_path = self.backup_db("delete_cache") if os.path.exists(self.db_path) else ""
            undo_path = self._save_last_deleted(deleted_records, "delete_cache", backup_path)
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
            self.current_index = 0
            self._init_db()
            self._save_index()
            return {"deleted": len(deleted_records), "backup_path": backup_path, "undo_path": undo_path}
        except Exception as e:
            print(f"[CacheDB] Delete failed: {e}")
            return {"deleted": 0, "backup_path": "", "undo_path": "", "error": str(e)}

    def compact_duplicates(self):
        backup_path = self.backup_db("compact_duplicates")
        conn = self._connect()
        try:
            conn.execute("DROP INDEX IF EXISTS idx_tags_duplicate")
            updated = self._rebuild_duplicate_keys(conn)
            duplicate_ids = self._duplicate_ids_to_remove(conn)
            deleted_records = self._records_by_ids(conn, duplicate_ids)
            deleted_before_cursor, new_total = self._deletion_cursor_adjustment(conn, duplicate_ids)
            self._save_last_deleted(deleted_records, "compact_duplicates", backup_path)
            removed = self._delete_ids_in_chunks(conn, duplicate_ids) if duplicate_ids else 0
            self._delete_filtered_pool_tag_ids(conn, duplicate_ids)
            self._apply_cursor_after_deletion(conn, deleted_before_cursor, new_total)
            self._ensure_duplicate_index(conn)
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("duplicate_key_migration_v2", "2"),
            )
            conn.commit()
            total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            return {"updated": updated, "removed": removed, "total": total, "backup_path": backup_path}
        except Exception as e:
            conn.rollback()
            print(f"[CacheDB] Compact duplicates failed: {e}")
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

    def compact_similar(self, threshold=0.9, keep_per_group=2, must_include="", must_exclude="", query=""):
        try:
            threshold = float(threshold)
        except Exception:
            threshold = 0.9
        threshold = max(0.5, min(1.0, threshold))
        try:
            keep_per_group = int(keep_per_group)
        except Exception:
            keep_per_group = 2
        keep_per_group = max(1, keep_per_group)

        include, exclude = self.parse_search_syntax(query, must_include, must_exclude)
        where, params = self._where_for_terms(include, exclude)
        backup_path = self.backup_db("compact_similar")
        conn = self._connect()
        try:
            conn.execute("DROP INDEX IF EXISTS idx_tags_duplicate")
            updated = self._rebuild_duplicate_keys(conn)
            exact_delete_ids = self._duplicate_ids_to_remove(conn)
            exact_records = self._records_by_ids(conn, exact_delete_ids)
            exact_deleted_before_cursor, exact_new_total = self._deletion_cursor_adjustment(conn, exact_delete_ids)
            exact_removed = self._delete_ids_in_chunks(conn, exact_delete_ids) if exact_delete_ids else 0
            self._delete_filtered_pool_tag_ids(conn, exact_delete_ids)
            self._apply_cursor_after_deletion(conn, exact_deleted_before_cursor, exact_new_total)

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
            similar_deleted_before_cursor, similar_new_total = self._deletion_cursor_adjustment(conn, delete_ids)
            self._save_last_deleted(exact_records + similar_records, "compact_similar", backup_path)
            similar_removed = self._delete_ids_in_chunks(conn, delete_ids) if delete_ids else 0
            self._delete_filtered_pool_tag_ids(conn, delete_ids)
            self._apply_cursor_after_deletion(conn, similar_deleted_before_cursor, similar_new_total)
            self._ensure_duplicate_index(conn)
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("duplicate_key_migration_v2", "2"),
            )
            conn.commit()
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
            print(f"[CacheDB] Compact similar failed: {e}")
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
            total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            if total <= 0:
                return "缓存为空"
            read_count = max(0, min(self.current_index, total))
            next_position = 1 if self.current_index >= total else self.current_index + 1
            return f"下一条: {next_position} / 总数: {total}（已读 {read_count}）"
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
        except Exception:
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
        id_expr = "(SELECT COUNT(*) FROM tags AS t_pos WHERE t_pos.id <= tags.id)" if visible_positions else "id"
        limit_sql = ""
        if limit and int(limit) > 0:
            limit_sql = " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset or 0)])
        conn = self._connect()
        try:
            cursor = conn.execute(
                f"""
                SELECT {id_expr} AS display_id, booru, post_id, score, rating, COALESCE(tags_prompt, tags) AS tags_prompt
                FROM tags
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
                )
                for row in cursor.fetchall()
            ]
        finally:
            conn.close()

    def _count_records(self, include=None, exclude=None):
        where, params = self._where_for_terms(include, exclude)
        conn = self._connect()
        try:
            return conn.execute(f"SELECT COUNT(*) AS c FROM tags {where}", params).fetchone()["c"]
        finally:
            conn.close()

    def _clamp_current_index(self, conn=None):
        own_conn = conn is None
        conn = conn or self._connect()
        try:
            total = self.get_active_total() if own_conn else conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            self.current_index = max(0, min(int(self.current_index or 0), int(total or 0)))
            self._save_index(conn)
            if own_conn:
                conn.commit()
        finally:
            if own_conn:
                conn.close()

    def _deletion_cursor_adjustment(self, conn, deleted_ids):
        deleted_ids = set(int(tag_id) for tag_id in deleted_ids)
        if not deleted_ids:
            return 0, self.get_active_total()

        if self.is_using_filtered_pool() and self.get_active_rule_id():
            rows, total = self._active_rule_rows()
            deleted_offsets = [offset for offset, row in enumerate(rows) if int(row[0]) in deleted_ids]
            deleted_before_cursor = sum(1 for offset in deleted_offsets if offset < self.current_index)
            return deleted_before_cursor, max(0, total - len(deleted_offsets))

        if self.is_using_filtered_pool():
            placeholders = ",".join("?" for _ in deleted_ids)
            rows = conn.execute(
                f"SELECT id FROM filtered_pool WHERE tag_id IN ({placeholders}) ORDER BY id ASC",
                list(deleted_ids),
            ).fetchall()
            deleted_before_cursor = 0
            for row in rows:
                position = conn.execute(
                    "SELECT COUNT(*) AS c FROM filtered_pool WHERE id < ?",
                    (row["id"],),
                ).fetchone()["c"]
                if position < self.current_index:
                    deleted_before_cursor += 1
            total = conn.execute("SELECT COUNT(*) AS c FROM filtered_pool").fetchone()["c"]
            return deleted_before_cursor, max(0, total - len(rows))

        deleted_before_cursor = 0
        for tag_id in deleted_ids:
            position = conn.execute(
                "SELECT COUNT(*) AS c FROM tags WHERE id < ?",
                (tag_id,),
            ).fetchone()["c"]
            if position < self.current_index:
                deleted_before_cursor += 1
        total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
        return deleted_before_cursor, max(0, total - len(deleted_ids))

    def _delete_filtered_pool_tag_ids(self, conn, tag_ids):
        if not tag_ids:
            return 0
        removed = 0
        ids = list(dict.fromkeys(int(tag_id) for tag_id in tag_ids))
        for start in range(0, len(ids), 800):
            chunk = ids[start:start + 800]
            placeholders = ",".join("?" for _ in chunk)
            cursor = conn.execute(f"DELETE FROM filtered_pool WHERE tag_id IN ({placeholders})", chunk)
            removed += cursor.rowcount
        return removed

    def _apply_cursor_after_deletion(self, conn, deleted_before_cursor, new_total):
        if deleted_before_cursor:
            self.current_index = max(0, self.current_index - deleted_before_cursor)
        self.current_index = max(0, min(self.current_index, int(new_total or 0)))
        self._save_index(conn)

    def get_active_total(self):
        if self.is_using_filtered_pool() and self.get_active_rule_id():
            rule = self.get_rule(self.get_active_rule_id())
            if not rule:
                return 0
            include, exclude = self.parse_search_syntax(rule["query"], rule["must_include"], rule["must_exclude"])
            total = self._count_records(include, exclude)
            limit_count = int(rule.get("limit_count") or 0)
            return min(total, limit_count) if limit_count > 0 else total

        table_name = "filtered_pool" if self.is_using_filtered_pool() else "tags"
        conn = self._connect()
        try:
            return conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()["c"]
        finally:
            conn.close()

    def jump_to_position(self, position):
        try:
            position = int(float(position))
        except Exception:
            return {"ok": False, "message": "请输入有效的缓存序号", "total": self.get_active_total()}

        total = self.get_active_total()
        if total <= 0:
            return {"ok": False, "message": "缓存为空，请先批量爬取", "total": total}
        if position < 1 or position > total:
            return {
                "ok": False,
                "message": f"序号 {position} 超出范围（1-{total}）",
                "total": total,
            }

        self.current_index = position - 1
        self._save_index()
        return {
            "ok": True,
            "position": position,
            "current_index": self.current_index,
            "total": total,
        }

    def _tag_id_at_main_position(self, position, conn=None):
        try:
            position = int(float(position))
        except Exception:
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

    def _tag_id_at_filtered_position(self, position, conn=None):
        try:
            position = int(float(position))
        except Exception:
            return None
        if position < 1:
            return None
        own_conn = conn is None
        conn = conn or self._connect()
        try:
            row = conn.execute(
                "SELECT tag_id FROM filtered_pool ORDER BY id ASC LIMIT 1 OFFSET ?",
                (position - 1,),
            ).fetchone()
            return int(row["tag_id"]) if row and row["tag_id"] is not None else None
        finally:
            if own_conn:
                conn.close()

    def _tag_id_at_active_rule_position(self, position):
        try:
            position = int(float(position))
        except Exception:
            return None
        if position < 1:
            return None
        rows, total = self._active_rule_rows()
        if position > total or position > len(rows):
            return None
        return int(rows[position - 1][0])

    def tag_id_at_position(self, position):
        if self.is_using_filtered_pool() and self.get_active_rule_id():
            return self._tag_id_at_active_rule_position(position)
        if self.is_using_filtered_pool():
            return self._tag_id_at_filtered_position(position)
        return self._tag_id_at_main_position(position)

    def get_by_position(self, position):
        if self.is_using_filtered_pool() and self.get_active_rule_id():
            rows, total = self._active_rule_rows()
            try:
                position = int(float(position))
            except Exception:
                return None
            if position < 1 or position > total or position > len(rows):
                return None
            return rows[position - 1][5]

        table_name = "filtered_pool" if self.is_using_filtered_pool() else "tags"
        tag_expr = "tags" if table_name == "filtered_pool" else "COALESCE(tags_prompt, tags)"
        try:
            position = int(float(position))
        except Exception:
            return None
        if position < 1:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT {tag_expr} AS tags_prompt FROM {table_name} ORDER BY id ASC LIMIT 1 OFFSET ?",
                (position - 1,),
            ).fetchone()
            return row["tags_prompt"] if row else None
        finally:
            conn.close()

    def get_record_by_position(self, position):
        if self.is_using_filtered_pool() and self.get_active_rule_id():
            rows, total = self._active_rule_rows()
            try:
                position = int(float(position))
            except Exception:
                return None
            if position < 1 or position > total or position > len(rows):
                return None
            row = rows[position - 1]
            return {
                "position": position,
                "booru": row[1],
                "post_id": row[2],
                "score": row[3],
                "rating": row[4],
                "tags_prompt": row[5],
            }

        table_name = "filtered_pool" if self.is_using_filtered_pool() else "tags"
        tag_expr = "tags" if table_name == "filtered_pool" else "COALESCE(tags_prompt, tags)"
        select_extra = "'' AS booru, '' AS post_id, 0 AS score, '' AS rating" if table_name == "filtered_pool" else "booru, post_id, score, rating"
        try:
            position = int(float(position))
        except Exception:
            return None
        if position < 1:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                f"""
                SELECT {select_extra}, {tag_expr} AS tags_prompt
                FROM {table_name}
                ORDER BY id ASC LIMIT 1 OFFSET ?
                """,
                (position - 1,),
            ).fetchone()
            if not row:
                return None
            return {
                "position": position,
                "booru": row["booru"] or "",
                "post_id": row["post_id"] or "",
                "score": row["score"] or 0,
                "rating": row["rating"] or "",
                "tags_prompt": row["tags_prompt"] or "",
            }
        finally:
            conn.close()

    def get_next_preview(self):
        total = self.get_active_total()
        if total <= 0:
            return None
        position = 1 if self.current_index >= total else self.current_index + 1
        return self.get_record_by_position(position)

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

    def preview_delete_by_filter(self, must_include="", must_exclude="", query="", sample_limit=8):
        include, exclude = self.parse_search_syntax(query, must_include, must_exclude)
        where, params = self._where_for_terms(include, exclude)
        conn = self._connect()
        try:
            ids = self._ids_matching_where(conn, where, params)
            records = self._records_by_ids(conn, ids)
        finally:
            conn.close()
        return self._preview_result(records, requested=len(ids), sample_limit=sample_limit, title="当前筛选删除预览")

    def preview_delete_all(self, sample_limit=8):
        conn = self._connect()
        try:
            records = self._all_records(conn)
        finally:
            conn.close()
        return self._preview_result(records, requested=len(records), sample_limit=sample_limit, title="删除全部预览")

    def delete_by_positions(self, positions):
        ids = []
        for position in positions:
            tag_id = self.tag_id_at_position(position)
            if tag_id is not None:
                ids.append(tag_id)
        ids = list(dict.fromkeys(ids))
        return self.delete_by_ids(ids) if ids else 0

    def delete_by_position(self, position):
        return self.delete_by_positions([position]) > 0

    def delete_by_position_spec(self, position_spec):
        positions = self.parse_positions(position_spec, self.get_active_total())
        return self.delete_by_positions(positions), len(positions)

    def create_filtered_pool_by_positions(self, positions):
        tag_ids = []
        for position in positions:
            tag_id = self._tag_id_at_main_position(position)
            if tag_id is not None:
                tag_ids.append(tag_id)
        tag_ids = list(dict.fromkeys(tag_ids))
        return self.create_filtered_pool(tag_ids)

    def create_filtered_pool_by_position_spec(self, position_spec):
        positions = self.parse_positions(position_spec, self._count_records())
        return self.create_filtered_pool_by_positions(positions), len(positions)

    def jump_to_id(self, tag_id):
        try:
            tag_id = int(float(tag_id))
        except Exception:
            return {"ok": False, "message": "请输入有效的缓存 ID", "total": self.get_active_total()}

        if self.is_using_filtered_pool() and self.get_active_rule_id():
            rows, total = self._active_rule_rows()
            for offset, row in enumerate(rows):
                if int(row[0]) == tag_id:
                    self.current_index = offset
                    self._save_index()
                    return {
                        "ok": True,
                        "id": tag_id,
                        "position": offset + 1,
                        "current_index": self.current_index,
                        "total": total,
                    }
            return {"ok": False, "message": f"缓存 ID {tag_id} 不在当前规则池中", "total": total}

        conn = self._connect()
        try:
            if self.is_using_filtered_pool():
                row = conn.execute(
                    "SELECT id FROM filtered_pool WHERE tag_id = ? ORDER BY id ASC LIMIT 1",
                    (tag_id,),
                ).fetchone()
                if not row:
                    total = conn.execute("SELECT COUNT(*) AS c FROM filtered_pool").fetchone()["c"]
                    return {"ok": False, "message": f"缓存 ID {tag_id} 不在当前筛选池中", "total": total}
                offset = conn.execute(
                    "SELECT COUNT(*) AS c FROM filtered_pool WHERE id < ?",
                    (row["id"],),
                ).fetchone()["c"]
                total = conn.execute("SELECT COUNT(*) AS c FROM filtered_pool").fetchone()["c"]
            else:
                row = conn.execute("SELECT id FROM tags WHERE id = ?", (tag_id,)).fetchone()
                if not row:
                    total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
                    return {"ok": False, "message": f"未找到缓存 ID {tag_id}", "total": total}
                offset = conn.execute(
                    "SELECT COUNT(*) AS c FROM tags WHERE id < ?",
                    (tag_id,),
                ).fetchone()["c"]
                total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]

            self.current_index = offset
            self._save_index(conn)
            conn.commit()
            return {
                "ok": True,
                "id": tag_id,
                "position": offset + 1,
                "current_index": self.current_index,
                "total": total,
            }
        finally:
            conn.close()

    def search_cache(self, keyword="", limit=200):
        include, exclude = self.parse_search_syntax(keyword)
        return self._query_records(include, exclude, limit=limit, visible_positions=True)

    def get_by_id(self, tag_id):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(tags_prompt, tags) AS tags_prompt FROM tags WHERE id = ?",
                (tag_id,),
            ).fetchone()
            return row["tags_prompt"] if row else None
        finally:
            conn.close()

    def export_records(self, file_format="json", file_path=None):
        file_format = str(file_format or "json").lower().strip()
        if file_format not in ("json", "csv"):
            file_format = "json"
        if not file_path:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            file_path = os.path.join(self.cache_dir, f"tag_cache_export_{timestamp}.{file_format}")
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)

        conn = self._connect()
        try:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, booru, post_id, tags_raw, tags_prompt, score, rating,
                           source_url, preview_url, search_query
                    FROM tags
                    ORDER BY id ASC
                    """
                ).fetchall()
            ]
        finally:
            conn.close()

        if file_format == "csv":
            fieldnames = [
                "id", "booru", "post_id", "tags_raw", "tags_prompt", "score", "rating",
                "source_url", "preview_url", "search_query",
            ]
            with open(file_path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        else:
            with open(file_path, "w", encoding="utf-8") as handle:
                json.dump(rows, handle, ensure_ascii=False, indent=2)
        return {"path": file_path, "count": len(rows), "format": file_format}

    def _load_import_records(self, file_path):
        file_path = str(file_path or "").strip().strip('"')
        if not file_path or not os.path.exists(file_path):
            return {"ok": False, "message": "导入文件不存在", "records": []}

        ext = os.path.splitext(file_path)[1].lower()
        records = []
        try:
            if ext == ".csv":
                with open(file_path, "r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    records = [dict(row) for row in reader]
            else:
                with open(file_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    records = payload.get("records") or payload.get("tags") or []
                elif isinstance(payload, list):
                    records = payload
        except Exception as e:
            return {"ok": False, "message": f"导入失败: {e}", "records": []}

        if not isinstance(records, list) or not records:
            return {"ok": False, "message": "导入文件没有可用记录", "records": []}

        normalized = []
        for record in records:
            if isinstance(record, str):
                normalized.append({"tags_prompt": record})
            elif isinstance(record, dict):
                normalized.append(record)
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

    def delete_by_id(self, tag_id):
        return self.delete_by_ids([tag_id]) > 0

    def delete_by_ids(self, tag_ids):
        ids = list(dict.fromkeys(int(tag_id) for tag_id in tag_ids if str(tag_id).strip().isdigit()))
        if not ids:
            return 0
        conn = self._connect()
        try:
            existing_ids = self._existing_ids(conn, ids)
            if not existing_ids:
                return 0
            deleted_records = self._records_by_ids(conn, existing_ids)
            backup_path = self.backup_db("delete_ids") if existing_ids else ""
            placeholders = ",".join("?" for _ in existing_ids)
            deleted_before_cursor, new_total = self._deletion_cursor_adjustment(conn, existing_ids)
            self._save_last_deleted(deleted_records, "delete_ids", backup_path)
            cursor = conn.execute(f"DELETE FROM tags WHERE id IN ({placeholders})", existing_ids)
            self._delete_filtered_pool_tag_ids(conn, existing_ids)
            self._apply_cursor_after_deletion(conn, deleted_before_cursor, new_total)
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            conn.rollback()
            print(f"[CacheDB] Delete IDs failed: {e}")
            return 0
        finally:
            conn.close()

    def filter_tags_by_keywords(self, must_include="", must_exclude="", query="", sort_order="ID", limit=200):
        include, exclude = self.parse_search_syntax(query, must_include, must_exclude)
        return self._query_records(include, exclude, sort_order=sort_order, limit=limit, visible_positions=True)

    def delete_by_filter(self, must_include="", must_exclude="", query=""):
        include, exclude = self.parse_search_syntax(query, must_include, must_exclude)
        where, params = self._where_for_terms(include, exclude)
        conn = self._connect()
        try:
            deleted_ids = self._ids_matching_where(conn, where, params)
            deleted_records = self._records_by_ids(conn, deleted_ids)
            backup_path = self.backup_db("delete_filter") if deleted_ids else ""
            deleted_before_cursor, new_total = self._deletion_cursor_adjustment(conn, deleted_ids)
            self._save_last_deleted(deleted_records, "delete_filter", backup_path)
            cursor = conn.execute(f"DELETE FROM tags {where}", params)
            self._delete_filtered_pool_tag_ids(conn, deleted_ids)
            self._apply_cursor_after_deletion(conn, deleted_before_cursor, new_total)
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            conn.rollback()
            print(f"[CacheDB] Delete filter failed: {e}")
            return 0
        finally:
            conn.close()

    def delete_by_any_tags(self, tags):
        where, params = self._any_tags_where_params(tags)
        if not where:
            return 0
        conn = self._connect()
        try:
            deleted_ids = self._ids_matching_where(conn, where, params)
            deleted_records = self._records_by_ids(conn, deleted_ids)
            backup_path = self.backup_db("delete_tags") if deleted_ids else ""
            deleted_before_cursor, new_total = self._deletion_cursor_adjustment(conn, deleted_ids)
            self._save_last_deleted(deleted_records, "delete_tags", backup_path)
            cursor = conn.execute(f"DELETE FROM tags {where}", params)
            self._delete_filtered_pool_tag_ids(conn, deleted_ids)
            self._apply_cursor_after_deletion(conn, deleted_before_cursor, new_total)
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            conn.rollback()
            print(f"[CacheDB] Delete by tags failed: {e}")
            return 0
        finally:
            conn.close()

    def create_filtered_pool(self, filtered_ids):
        if not filtered_ids:
            return 0
        conn = self._connect()
        try:
            conn.execute("DELETE FROM filtered_pool")
            placeholders = ",".join("?" for _ in filtered_ids)
            conn.execute(
                f"""
                INSERT INTO filtered_pool (tag_id, tags)
                SELECT id, COALESCE(tags_prompt, tags) FROM tags WHERE id IN ({placeholders})
                """,
                filtered_ids,
            )
            count = conn.execute("SELECT COUNT(*) AS c FROM filtered_pool").fetchone()["c"]
            self.current_index = 0
            self._set_metadata("active_rule_id", "", conn)
            self._save_index(conn)
            conn.commit()
            return count
        except Exception as e:
            print(f"[CacheDB] Create filtered pool failed: {e}")
            return 0
        finally:
            conn.close()

    def create_filtered_pool_by_keywords(self, must_include="", must_exclude="", query="", sort_order="ID", limit=0):
        include, exclude = self.parse_search_syntax(query, must_include, must_exclude)
        where, params = self._where_for_terms(include, exclude)
        order = self.SORT_SQL.get(sort_order or "ID", "id ASC")
        limit_sql = ""
        if limit and int(limit) > 0:
            limit_sql = " LIMIT ?"
            params.append(int(limit))
        conn = self._connect()
        try:
            conn.execute("DELETE FROM filtered_pool")
            conn.execute(
                f"""
                INSERT INTO filtered_pool (tag_id, tags)
                SELECT id, COALESCE(tags_prompt, tags)
                FROM tags
                {where}
                ORDER BY {order}
                {limit_sql}
                """,
                params,
            )
            count = conn.execute("SELECT COUNT(*) AS c FROM filtered_pool").fetchone()["c"]
            self.current_index = 0
            self._set_metadata("active_rule_id", "", conn)
            self._save_index(conn)
            conn.commit()
            return count
        except Exception as e:
            print(f"[CacheDB] Create keyword filtered pool failed: {e}")
            return 0
        finally:
            conn.close()

    def create_rule(self, name="", query="", must_include="", must_exclude="", sort_order="ID", limit_count=0):
        include, exclude = self.parse_search_syntax(query, must_include, must_exclude)
        count = self._count_records(include, exclude)
        if not name:
            label_bits = []
            if query:
                label_bits.append(query)
            if must_include:
                label_bits.append(f"+ {must_include}")
            if must_exclude:
                label_bits.append(f"- {must_exclude}")
            name = " / ".join(label_bits) or "未命名规则"
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO rules (name, query, must_include, must_exclude, sort_order, limit_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, query or "", must_include or "", must_exclude or "", sort_order or "ID", int(limit_count or 0)),
            )
            rule_id = cursor.lastrowid
            conn.commit()
            self.activate_rule(rule_id)
            return rule_id, count
        finally:
            conn.close()

    def list_rules(self):
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, name, query, must_include, must_exclude, sort_order, limit_count
                FROM rules ORDER BY id DESC
                """
            ).fetchall()
            result = []
            for row in rows:
                include, exclude = self.parse_search_syntax(row["query"], row["must_include"], row["must_exclude"])
                result.append(
                    (
                        row["id"],
                        row["name"],
                        row["query"] or "",
                        row["must_include"] or "",
                        row["must_exclude"] or "",
                        row["sort_order"] or "ID",
                        row["limit_count"] or 0,
                        self._count_records(include, exclude),
                    )
                )
            return result
        finally:
            conn.close()

    def get_rule(self, rule_id):
        if not rule_id:
            return None
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM rules WHERE id = ?", (int(rule_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def activate_rule(self, rule_id):
        rule = self.get_rule(rule_id)
        if not rule:
            return "规则不存在"
        self._set_metadata("active_rule_id", int(rule_id))
        self._set_metadata("use_filtered_pool", "1")
        self.reset_index()
        return f"已启用规则池: {rule['name']}"

    def delete_rule(self, rule_id):
        conn = self._connect()
        try:
            cursor = conn.execute("DELETE FROM rules WHERE id = ?", (int(rule_id),))
            conn.commit()
            if str(rule_id) == str(self.get_active_rule_id() or ""):
                self._set_metadata("active_rule_id", "")
                self._set_metadata("use_filtered_pool", "0")
                self.reset_index()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_active_rule_id(self):
        value = self._get_metadata("active_rule_id", "")
        return int(value) if str(value).isdigit() else None

    def get_filtered_pool_status(self):
        active_rule = self.get_rule(self.get_active_rule_id())
        if active_rule and self.is_using_filtered_pool():
            include, exclude = self.parse_search_syntax(
                active_rule["query"], active_rule["must_include"], active_rule["must_exclude"]
            )
            count = self._count_records(include, exclude)
            limit_count = int(active_rule.get("limit_count") or 0)
            if limit_count > 0:
                count = min(count, limit_count)
            return f"规则池: {active_rule['name']} / {count} 条"
        conn = self._connect()
        try:
            count = conn.execute("SELECT COUNT(*) AS c FROM filtered_pool").fetchone()["c"]
            return f"筛选池: {count} 条" if count else "筛选池: 未创建"
        finally:
            conn.close()

    def use_filtered_pool(self, use_pool=True):
        if use_pool:
            active_rule = self.get_rule(self.get_active_rule_id())
            if not active_rule:
                conn = self._connect()
                try:
                    count = conn.execute("SELECT COUNT(*) AS c FROM filtered_pool").fetchone()["c"]
                finally:
                    conn.close()
                if count <= 0:
                    return "规则池/筛选池不存在，请先创建"
            self._set_metadata("use_filtered_pool", "1")
        else:
            self._set_metadata("use_filtered_pool", "0")
        self.reset_index()
        return "已切换到规则池/筛选池" if use_pool else "已切换到主缓存"

    def is_using_filtered_pool(self):
        return self._get_metadata("use_filtered_pool", "0") == "1"

    def _active_rule_rows(self, limit_for_count=False):
        rule = self.get_rule(self.get_active_rule_id())
        if not rule:
            return [], 0
        include, exclude = self.parse_search_syntax(rule["query"], rule["must_include"], rule["must_exclude"])
        total = self._count_records(include, exclude)
        limit_count = int(rule.get("limit_count") or 0)
        if limit_count > 0:
            total = min(total, limit_count)
        rows = self._query_records(
            include,
            exclude,
            sort_order=rule.get("sort_order") or "ID",
            limit=limit_count if limit_count > 0 else 0,
            random_seed=rule.get("id"),
        )
        return rows, total

    def _active_rule_next_row(self):
        rule = self.get_rule(self.get_active_rule_id())
        if not rule:
            return None, 0
        include, exclude = self.parse_search_syntax(rule["query"], rule["must_include"], rule["must_exclude"])
        total = self._count_records(include, exclude)
        limit_count = int(rule.get("limit_count") or 0)
        if limit_count > 0:
            total = min(total, limit_count)
        if total == 0:
            return None, 0
        row = self._query_records(
            include,
            exclude,
            sort_order=rule.get("sort_order") or "ID",
            limit=1,
            offset=self.current_index,
            random_seed=rule.get("id"),
        )
        return (row[0] if row else None), total

    def get_next_tags_from_pool(self, loop=True):
        if self.is_using_filtered_pool() and self.get_active_rule_id():
            row, total = self._active_rule_next_row()
            if total == 0:
                return None, 0, 0
            if self.current_index >= total:
                if loop:
                    self.current_index = 0
                else:
                    return None, self.current_index, total
                row, total = self._active_rule_next_row()
            if row is None:
                return None, self.current_index, total
            tags = row[5]
            self.current_index += 1
            self._save_index()
            return tags, self.current_index, total

        table_name = "filtered_pool" if self.is_using_filtered_pool() else "tags"
        tag_expr = "tags" if table_name == "filtered_pool" else "COALESCE(tags_prompt, tags)"
        conn = self._connect()
        try:
            total = conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()["c"]
            if total == 0:
                return None, 0, 0
            if self.current_index >= total:
                if loop:
                    self.current_index = 0
                else:
                    return None, self.current_index, total
            row = conn.execute(
                f"SELECT {tag_expr} AS tags_prompt FROM {table_name} ORDER BY id ASC LIMIT 1 OFFSET ?",
                (self.current_index,),
            ).fetchone()
            if row:
                tags = row["tags_prompt"]
                self.current_index += 1
                self._save_index()
                return tags, self.current_index, total
            return None, self.current_index, total
        finally:
            conn.close()
