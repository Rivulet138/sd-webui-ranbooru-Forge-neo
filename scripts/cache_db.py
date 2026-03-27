"""
SQLite-based Tag Cache Manager
Drop-in replacement for JSON-based TagCacheManager with better reliability.
"""

import sqlite3
import os
import json


class TagCacheManager:
    """SQLite-based tag cache with same interface as JSON version."""

    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.db_path = os.path.join(cache_dir, 'tag_cache.db')
        self.current_index = 0
        self._init_db()
        self._load_index()

    def _init_db(self):
        """Initialize database schema."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tags TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            conn.commit()
        finally:
            conn.close()

    def _load_index(self):
        """Load current index from metadata."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute('SELECT value FROM metadata WHERE key = ?', ('current_index',))
            row = cursor.fetchone()
            self.current_index = int(row[0]) if row else 0
        except Exception:
            self.current_index = 0
        finally:
            conn.close()

    def _save_index(self):
        """Save current index to metadata."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)',
                        ('current_index', str(self.current_index)))
            conn.commit()
        finally:
            conn.close()

    def save_cache(self, tags_list):
        """Replace entire cache with new tags."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('DELETE FROM tags')
            conn.executemany('INSERT INTO tags (tags) VALUES (?)',
                           [(tags,) for tags in tags_list])
            conn.commit()
        finally:
            conn.close()

    def append_cache(self, tags_list):
        """Append tags to existing cache."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executemany('INSERT INTO tags (tags) VALUES (?)',
                           [(tags,) for tags in tags_list])
            conn.commit()
            cursor = conn.execute('SELECT COUNT(*) FROM tags')
            return cursor.fetchone()[0]
        finally:
            conn.close()

    def get_next_tags(self, loop=True):
        """Get next tags from cache, returns (tags, index, total)."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute('SELECT COUNT(*) FROM tags')
            total = cursor.fetchone()[0]
            
            if total == 0:
                return None, 0, 0
            
            if self.current_index >= total:
                if loop:
                    self.current_index = 0
                else:
                    return None, self.current_index, total
            
            cursor = conn.execute('SELECT tags FROM tags LIMIT 1 OFFSET ?', (self.current_index,))
            row = cursor.fetchone()
            
            if row:
                tags = row[0]
                self.current_index += 1
                self._save_index()
                return tags, self.current_index, total
            
            return None, self.current_index, total
        finally:
            conn.close()

    def reset_index(self):
        """Reset index to 0."""
        self.current_index = 0
        self._save_index()

    def delete_cache(self):
        """Delete cache database."""
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
            self.current_index = 0
            # Recreate schema immediately so following operations won't fail
            self._init_db()
            self._save_index()
        except Exception as e:
            print(f"[CacheDB] Delete failed: {e}")

    def get_status(self):
        """Get cache status string."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute('SELECT COUNT(*) FROM tags')
            total = cursor.fetchone()[0]
            return f"索引: {self.current_index} / 总数: {total}"
        except Exception:
            return "缓存状态未知"
        finally:
            conn.close()

    def search_cache(self, keyword=''):
        """Search cache by keyword."""
        conn = sqlite3.connect(self.db_path)
        try:
            if keyword:
                cursor = conn.execute(
                    'SELECT id, tags FROM tags WHERE tags LIKE ? ORDER BY id',
                    (f'%{keyword}%',)
                )
            else:
                cursor = conn.execute('SELECT id, tags FROM tags ORDER BY id LIMIT 50')
            return cursor.fetchall()
        finally:
            conn.close()

    def get_by_id(self, tag_id):
        """Get specific tag by ID."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute('SELECT tags FROM tags WHERE id = ?', (tag_id,))
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def delete_by_id(self, tag_id):
        """Delete specific tag by ID."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('DELETE FROM tags WHERE id = ?', (tag_id,))
            conn.commit()
            return True
        except Exception as e:
            print(f"[CacheDB] Delete ID {tag_id} failed: {e}")
            return False
        finally:
            conn.close()

    def filter_tags_by_keywords(self, must_include='', must_exclude=''):
        """Filter tags that must include certain keywords and exclude others.
        
        Args:
            must_include: Comma-separated keywords that must be present (e.g., '1girl,blue_eyes')
            must_exclude: Comma-separated keywords that must not be present (e.g., '2girls,multiple_girls')
        
        Returns:
            List of (id, tags) tuples matching the filter
        """
        conn = sqlite3.connect(self.db_path)
        try:
            query = 'SELECT id, tags FROM tags WHERE 1=1'
            params = []
            
            if must_include:
                include_keywords = [k.strip() for k in must_include.split(',') if k.strip()]
                for keyword in include_keywords:
                    query += ' AND tags LIKE ?'
                    params.append(f'%{keyword}%')
            
            if must_exclude:
                exclude_keywords = [k.strip() for k in must_exclude.split(',') if k.strip()]
                for keyword in exclude_keywords:
                    query += ' AND tags NOT LIKE ?'
                    params.append(f'%{keyword}%')
            
            query += ' ORDER BY id'
            cursor = conn.execute(query, params)
            return cursor.fetchall()
        finally:
            conn.close()

    def create_filtered_pool(self, filtered_ids):
        """Create a temporary filtered pool from selected IDs.
        
        Args:
            filtered_ids: List of tag IDs to include in the pool
        
        Returns:
            Number of tags in the filtered pool
        """
        if not filtered_ids:
            return 0
        
        conn = sqlite3.connect(self.db_path)
        try:
            # Create filtered pool table
            conn.execute('DROP TABLE IF EXISTS filtered_pool')
            conn.execute('''
                CREATE TABLE filtered_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tags TEXT NOT NULL
                )
            ''')
            
            # Copy selected tags to filtered pool
            placeholders = ','.join('?' * len(filtered_ids))
            conn.execute(f'''
                INSERT INTO filtered_pool (tags)
                SELECT tags FROM tags WHERE id IN ({placeholders})
            ''', filtered_ids)
            
            conn.commit()
            
            # Get count
            cursor = conn.execute('SELECT COUNT(*) FROM filtered_pool')
            return cursor.fetchone()[0]
        except Exception as e:
            print(f"[CacheDB] Create filtered pool failed: {e}")
            return 0
        finally:
            conn.close()

    def get_filtered_pool_status(self):
        """Get filtered pool status."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='filtered_pool'")
            if not cursor.fetchone():
                return "筛选池: 未创建"
            
            cursor = conn.execute('SELECT COUNT(*) FROM filtered_pool')
            count = cursor.fetchone()[0]
            return f"筛选池: {count} 条"
        except Exception as e:
            print(f"[CacheDB] Get filtered pool status failed: {e}")
            return "筛选池: 错误"
        finally:
            conn.close()

    def use_filtered_pool(self, use_pool=True):
        """Switch between using filtered pool or main cache.
        
        Args:
            use_pool: True to use filtered pool, False to use main cache
        
        Returns:
            Status message
        """
        conn = sqlite3.connect(self.db_path)
        try:
            if use_pool:
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='filtered_pool'")
                if not cursor.fetchone():
                    return "筛选池不存在，请先创建"
                conn.execute('INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)', ('use_filtered_pool', '1'))
            else:
                conn.execute('INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)', ('use_filtered_pool', '0'))
            conn.commit()
            # Keep behavior consistent with docs: reset index after source switch
            self.current_index = 0
            self._save_index()
            return "已切换到筛选池" if use_pool else "已切换到主缓存"
        except Exception as e:
            print(f"[CacheDB] Switch pool failed: {e}")
            return f"切换失败: {e}"
        finally:
            conn.close()

    def is_using_filtered_pool(self):
        """Check if currently using filtered pool."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute('SELECT value FROM metadata WHERE key = ?', ('use_filtered_pool',))
            row = cursor.fetchone()
            return row and row[0] == '1'
        except Exception:
            return False
        finally:
            conn.close()

    def get_next_tags_from_pool(self, loop=True):
        """Get next tags from filtered pool or main cache based on setting."""
        use_pool = self.is_using_filtered_pool()
        table_name = 'filtered_pool' if use_pool else 'tags'
        
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(f'SELECT COUNT(*) FROM {table_name}')
            total = cursor.fetchone()[0]
            
            if total == 0:
                return None, 0, 0
            
            if self.current_index >= total:
                if loop:
                    self.current_index = 0
                else:
                    return None, self.current_index, total
            
            cursor = conn.execute(f'SELECT tags FROM {table_name} LIMIT 1 OFFSET ?', (self.current_index,))
            row = cursor.fetchone()
            
            if row:
                tags = row[0]
                self.current_index += 1
                self._save_index()
                return tags, self.current_index, total
            
            return None, self.current_index, total
        finally:
            conn.close()
