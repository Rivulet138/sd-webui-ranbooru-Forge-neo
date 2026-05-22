"""
SQLite tag cache for Ranbooru.

The prompt-facing API still returns only cleaned tag strings. Metadata is stored
for filtering, previewing, dedupe, and future maintenance.
"""

import os
import re
import shlex
import sqlite3


class TagCacheManager:
    """SQLite-backed tag cache with metadata, rules, and legacy API shims."""

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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_duplicate ON tags (duplicate_key)")
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
        rows = conn.execute("SELECT id, booru, post_id, tags_prompt, duplicate_key FROM tags").fetchall()
        for row in rows:
            if row["duplicate_key"]:
                continue
            duplicate_key = self._make_duplicate_key(
                {
                    "booru": row["booru"],
                    "post_id": row["post_id"],
                    "tags_prompt": row["tags_prompt"],
                    "tags": row["tags_prompt"],
                }
            )
            conn.execute("UPDATE tags SET duplicate_key = ? WHERE id = ?", (duplicate_key, row["id"]))

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
    def _split_keywords(keywords):
        if not keywords:
            return []
        normalized = str(keywords).replace("，", ",").replace("\n", ",").replace("\r", ",")
        return [keyword.strip() for keyword in normalized.split(",") if keyword.strip()]

    @staticmethod
    def _safe_int(value, default=0):
        try:
            if value in (None, ""):
                return default
            return int(float(value))
        except Exception:
            return default

    @staticmethod
    def _normalize_tags(tags):
        return re.sub(r"\s+", " ", str(tags or "").strip().lower())

    def _make_duplicate_key(self, record):
        booru = str(record.get("booru") or "").strip()
        post_id = str(record.get("post_id") or "").strip()
        if booru and post_id:
            return f"post:{booru}:{post_id}"
        return f"tags:{self._normalize_tags(record.get('tags_prompt') or record.get('tags') or '')}"

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
        coerced["duplicate_key"] = record.get("duplicate_key") or self._make_duplicate_key(coerced)
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

    def save_cache(self, tags_list):
        return self.save_records([{"tags_prompt": tags} for tags in tags_list])

    def append_cache(self, tags_list):
        result = self.append_records([{"tags_prompt": tags} for tags in tags_list])
        return result["total"]

    def save_records(self, records, dedupe=True, min_score=None):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM tags")
            conn.execute("DELETE FROM filtered_pool")
            self.current_index = 0
            self._save_index(conn)
            conn.commit()
        finally:
            conn.close()
        return self.append_records(records, dedupe=dedupe, min_score=min_score)

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
                if dedupe:
                    exists = conn.execute(
                        "SELECT 1 FROM tags WHERE duplicate_key = ? LIMIT 1",
                        (record["duplicate_key"],),
                    ).fetchone()
                    if exists:
                        stats["skipped_duplicate"] += 1
                        continue
                conn.execute(
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
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
            self.current_index = 0
            self._init_db()
            self._save_index()
        except Exception as e:
            print(f"[CacheDB] Delete failed: {e}")

    def get_status(self):
        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"]
            return f"索引: {self.current_index} / 总数: {total}"
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
                include.append(token[1:])
            elif token.startswith("-") and len(token) > 1:
                exclude.append(token[1:])
            else:
                include.append(token)
        include = list(dict.fromkeys(include))
        exclude = list(dict.fromkeys(exclude))
        return include, exclude

    def _where_for_terms(self, include=None, exclude=None):
        include = include or []
        exclude = exclude or []
        clauses = []
        params = []
        for keyword in include:
            clauses.append("(COALESCE(tags_prompt, tags) LIKE ? OR COALESCE(tags_raw, tags) LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        for keyword in exclude:
            clauses.append("(COALESCE(tags_prompt, tags) NOT LIKE ? AND COALESCE(tags_raw, tags) NOT LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return where, params

    def _query_records(self, include=None, exclude=None, sort_order="ID", limit=200, offset=0):
        where, params = self._where_for_terms(include, exclude)
        order = self.SORT_SQL.get(sort_order or "ID", "id ASC")
        limit_sql = ""
        if limit and int(limit) > 0:
            limit_sql = " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset or 0)])
        conn = self._connect()
        try:
            cursor = conn.execute(
                f"""
                SELECT id, booru, post_id, score, rating, COALESCE(tags_prompt, tags) AS tags_prompt
                FROM tags
                {where}
                ORDER BY {order}
                {limit_sql}
                """,
                params,
            )
            return [
                (
                    row["id"],
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

    def search_cache(self, keyword="", limit=200):
        include, exclude = self.parse_search_syntax(keyword)
        return self._query_records(include, exclude, limit=limit)

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

    def delete_by_id(self, tag_id):
        return self.delete_by_ids([tag_id]) > 0

    def delete_by_ids(self, tag_ids):
        ids = [int(tag_id) for tag_id in tag_ids if str(tag_id).strip().isdigit()]
        if not ids:
            return 0
        conn = self._connect()
        try:
            placeholders = ",".join("?" for _ in ids)
            cursor = conn.execute(f"DELETE FROM tags WHERE id IN ({placeholders})", ids)
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            print(f"[CacheDB] Delete IDs failed: {e}")
            return 0
        finally:
            conn.close()

    def filter_tags_by_keywords(self, must_include="", must_exclude="", query="", sort_order="ID", limit=200):
        include, exclude = self.parse_search_syntax(query, must_include, must_exclude)
        return self._query_records(include, exclude, sort_order=sort_order, limit=limit)

    def delete_by_filter(self, must_include="", must_exclude="", query=""):
        include, exclude = self.parse_search_syntax(query, must_include, must_exclude)
        rows = self._query_records(include, exclude, limit=0)
        return self.delete_by_ids([row[0] for row in rows])

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
            self._save_index(conn)
            conn.commit()
            return count
        except Exception as e:
            print(f"[CacheDB] Create filtered pool failed: {e}")
            return 0
        finally:
            conn.close()

    def create_filtered_pool_by_keywords(self, must_include="", must_exclude="", query="", sort_order="ID", limit=0):
        rows = self.filter_tags_by_keywords(must_include, must_exclude, query, sort_order=sort_order, limit=limit)
        return self.create_filtered_pool([row[0] for row in rows])

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
        )
        return rows, total

    def get_next_tags_from_pool(self, loop=True):
        if self.is_using_filtered_pool() and self.get_active_rule_id():
            rows, total = self._active_rule_rows()
            if total == 0:
                return None, 0, 0
            if self.current_index >= total:
                if loop:
                    self.current_index = 0
                else:
                    return None, self.current_index, total
            tags = rows[self.current_index][5]
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
                f"SELECT {tag_expr} AS tags_prompt FROM {table_name} LIMIT 1 OFFSET ?",
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
