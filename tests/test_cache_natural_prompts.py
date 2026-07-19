import concurrent.futures
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from scripts.cache_db import TagCacheManager


class NaturalPromptCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = TagCacheManager(self.temp_dir.name)
        self.manager.append_records(
            [
                {"tags_prompt": "1girl, blue_hair", "post_id": "1", "score": 10},
                {"tags_prompt": "forest, night", "post_id": "2", "score": 20},
                {"tags_prompt": "city, sunset", "post_id": "3", "score": 30},
                {"tags_prompt": "1boy, rain", "post_id": "4", "score": 40},
                {"tags_prompt": "ocean, moonlight", "post_id": "5", "score": 50},
            ]
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _save(self, record, text):
        return self.manager.update_natural_prompts(
            [
                {
                    "id": record["id"],
                    "source_tags_prompt": record["tags_prompt"],
                    "natural_prompt": text,
                }
            ],
            preset="Krea 2",
            model="test-model",
        )

    def test_old_database_is_migrated_without_changing_original_tags(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            db_path = os.path.join(cache_dir, "tag_cache.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT, tags TEXT NOT NULL)"
            )
            conn.execute("INSERT INTO tags (tags) VALUES ('1girl, blue_hair')")
            conn.commit()
            conn.close()

            manager = TagCacheManager(cache_dir)
            conn = manager._connect()
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(tags)").fetchall()
            }
            row = conn.execute(
                "SELECT tags, tags_prompt, natural_prompt FROM tags"
            ).fetchone()
            conn.close()

            self.assertIn("natural_prompt", columns)
            self.assertIn("natural_preset", columns)
            self.assertEqual(row["tags"], "1girl, blue_hair")
            self.assertEqual(row["tags_prompt"], "1girl, blue_hair")
            self.assertIn(row["natural_prompt"], (None, ""))

    def test_range_uses_visible_positions_even_with_id_holes(self):
        conn = self.manager._connect()
        conn.execute("DELETE FROM tags WHERE id = 2")
        conn.commit()
        conn.close()

        records = self.manager.get_records_by_position_spec("2-3")
        self.assertEqual([record["post_id"] for record in records], ["3", "4"])
        self.assertEqual([record["position"] for record in records], [2, 3])

    def test_saved_natural_prompt_is_preferred_with_raw_fallback(self):
        first, second = self.manager.get_records_by_position_spec("1-2")
        result = self._save(first, "A blue-haired girl.")
        self.assertEqual(result["updated"], 1)
        self.assertEqual(
            self.manager.get_by_position(1, prefer_natural=True),
            "A blue-haired girl.",
        )
        self.assertEqual(
            self.manager.get_by_position(2, prefer_natural=True),
            second["tags_prompt"],
        )
        self.assertEqual(
            self.manager.get_by_position(1, prefer_natural=False),
            first["tags_prompt"],
        )

    def test_only_missing_skips_records_with_saved_results(self):
        first = self.manager.get_records_by_position_spec("1")[0]
        self._save(first, "A blue-haired girl.")

        records = self.manager.get_records_by_position_spec(
            "1-3",
            only_missing=True,
        )
        self.assertEqual([record["position"] for record in records], [2, 3])

        preview = self.manager.preview_natural_conversion(
            "1-3",
            only_missing=True,
        )
        self.assertEqual(preview["matched"], 3)
        self.assertEqual(preview["selected"], 2)
        self.assertEqual(preview["converted"], 1)
        self.assertEqual(preview["missing"], 2)

    def test_filtered_pool_reads_fresh_natural_result_from_main_table(self):
        records = self.manager.get_records_by_position_spec("1-3")
        self.manager.create_filtered_pool([records[0]["id"], records[2]["id"]])
        self.manager.use_filtered_pool(True)
        active = self.manager.get_records_by_position_spec("1-2")
        self._save(active[0], "Converted filtered prompt.")

        self.assertEqual(
            self.manager.get_by_position(1, prefer_natural=True),
            "Converted filtered prompt.",
        )
        self.manager.reset_index()
        tags, _, _ = self.manager.get_next_tags_from_pool(
            loop=False,
            prefer_natural=True,
        )
        self.assertEqual(tags, "Converted filtered prompt.")

    def test_filtered_pool_raw_fallback_reads_fresh_main_prompt(self):
        first = self.manager.get_records_by_position_spec("1")[0]
        self.manager.create_filtered_pool([first["id"]])
        self.manager.use_filtered_pool(True)
        conn = self.manager._connect()
        conn.execute(
            "UPDATE tags SET tags = ?, tags_prompt = ? WHERE id = ?",
            ("fresh, main, tags", "fresh, main, tags", first["id"]),
        )
        conn.commit()
        conn.close()
        self.assertEqual(
            self.manager.get_by_position(1, prefer_natural=True),
            "fresh, main, tags",
        )

    def test_rule_pool_positions_and_natural_reads_follow_rule_order(self):
        rule_id, _ = self.manager.create_rule(
            name="newest two",
            sort_order="Newest",
            limit_count=2,
        )
        self.manager.activate_rule(rule_id)
        records = self.manager.get_records_by_position_spec("1-2")
        self.assertEqual([record["post_id"] for record in records], ["5", "4"])
        self._save(records[0], "Moonlit ocean.")
        self.assertEqual(
            self.manager.get_by_position(1, prefer_natural=True),
            "Moonlit ocean.",
        )

    def test_optimistic_update_rejects_changed_source_record(self):
        record = self.manager.get_records_by_position_spec("1")[0]
        conn = self.manager._connect()
        conn.execute(
            "UPDATE tags SET tags_prompt = ?, tags = ? WHERE id = ?",
            ("changed, source", "changed, source", record["id"]),
        )
        conn.commit()
        conn.close()

        result = self._save(record, "Stale conversion.")
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["stale_or_missing"], 1)
        self.assertEqual(self.manager.get_natural_prompt_by_id(record["id"]), "")

    def test_update_requires_the_original_complete_prompt(self):
        record = self.manager.get_records_by_position_spec("1")[0]
        result = self.manager.update_natural_prompts(
            [{"id": record["id"], "natural_prompt": "Unsafe result."}],
            preset="Krea 2",
            model="test-model",
        )
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(self.manager.get_natural_prompt_by_id(record["id"]), "")

    def test_only_missing_update_does_not_overwrite_a_concurrent_result(self):
        record = self.manager.get_records_by_position_spec("1")[0]
        self._save(record, "First completed result.")
        result = self.manager.update_natural_prompts(
            [
                {
                    "id": record["id"],
                    "source_tags_prompt": record["tags_prompt"],
                    "natural_prompt": "Later concurrent result.",
                }
            ],
            preset="Krea 2",
            model="other-model",
            only_missing=True,
        )
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["stale_or_missing"], 1)
        self.assertEqual(
            self.manager.get_natural_prompt_by_id(record["id"]),
            "First completed result.",
        )

    def test_only_missing_replaces_an_outdated_converter_version(self):
        record = self.manager.get_records_by_position_spec("1")[0]
        source_hash = self.manager._natural_source_hash(record["tags_prompt"])
        conn = self.manager._connect()
        conn.execute(
            """
            UPDATE tags
            SET natural_prompt = ?,
                natural_source_hash = ?,
                natural_converter_version = ?
            WHERE id = ?
            """,
            ("Old generated text.", source_hash, "old-converter", record["id"]),
        )
        conn.commit()
        conn.close()

        selected = self.manager.get_records_by_position_spec("1", only_missing=True)
        self.assertEqual(len(selected), 1)
        result = self.manager.update_natural_prompts(
            [
                {
                    "id": record["id"],
                    "source_tags_prompt": record["tags_prompt"],
                    "natural_prompt": "New generated text.",
                }
            ],
            only_missing=True,
            converter_version=self.manager.NATURAL_CONVERTER_VERSION,
        )

        self.assertEqual(result["updated"], 1)
        self.assertEqual(
            self.manager.get_by_position(1, prefer_natural=True),
            "New generated text.",
        )

    def test_repairing_a_missing_source_prompt_invalidates_derived_text(self):
        record = self.manager.get_records_by_position_spec("1")[0]
        self._save(record, "A blue-haired girl.")
        conn = self.manager._connect()
        conn.execute(
            "UPDATE tags SET tags = ?, tags_prompt = '' WHERE id = ?",
            ("changed, source", record["id"]),
        )
        conn.commit()
        conn.close()

        reopened = TagCacheManager(self.temp_dir.name)
        repaired = reopened.get_records_by_position_spec("1")[0]
        self.assertEqual(repaired["tags_prompt"], "changed, source")
        self.assertEqual(repaired["natural_prompt"], "")

    def test_clear_only_removes_derived_natural_prompt(self):
        first = self.manager.get_records_by_position_spec("1")[0]
        self._save(first, "A blue-haired girl.")
        cleared = self.manager.clear_natural_prompts_by_positions("1")
        self.assertEqual(cleared, 1)
        self.assertEqual(self.manager.get_natural_prompt_by_id(first["id"]), "")
        self.assertEqual(
            self.manager.get_by_position(1),
            first["tags_prompt"],
        )

    def test_clear_by_fixed_ids_is_independent_of_active_pool_changes(self):
        first, second = self.manager.get_records_by_position_spec("1-2")
        self._save(first, "First.")
        self._save(second, "Second.")
        self.manager.create_filtered_pool([second["id"]])
        self.manager.use_filtered_pool(True)
        cleared = self.manager.clear_natural_prompts_by_ids([first["id"]])
        self.assertEqual(cleared, 1)
        self.assertEqual(self.manager.get_natural_prompt_by_id(first["id"]), "")
        self.assertEqual(
            self.manager.get_natural_prompt_by_id(second["id"]),
            "Second.",
        )

    def test_export_import_preserves_natural_prompt_metadata(self):
        first = self.manager.get_records_by_position_spec("1")[0]
        self._save(first, "A blue-haired girl.")
        export_path = os.path.join(self.temp_dir.name, "export.json")
        self.manager.export_records("json", export_path)

        with tempfile.TemporaryDirectory() as imported_dir:
            imported = TagCacheManager(imported_dir)
            result = imported.import_records(
                export_path,
                append=False,
                dedupe=True,
            )
            self.assertTrue(result["ok"])
            record = imported.get_records_by_position_spec("1")[0]
            self.assertEqual(record["natural_prompt"], "A blue-haired girl.")
            self.assertEqual(record["natural_preset"], "Krea 2")
            self.assertEqual(record["natural_model"], "test-model")
            self.assertEqual(
                record["natural_source_hash"],
                self.manager._natural_source_hash(first["tags_prompt"]),
            )
            self.assertEqual(
                record["natural_converter_version"],
                TagCacheManager.NATURAL_CONVERTER_VERSION,
            )

    def test_status_counts_converted_and_missing(self):
        first, second = self.manager.get_records_by_position_spec("1-2")
        self._save(first, "First.")
        self._save(second, "Second.")
        self.assertEqual(
            self.manager.get_natural_prompt_status(),
            {"total": 5, "converted": 2, "missing": 3},
        )

    def test_backup_is_a_readable_sqlite_snapshot(self):
        backup_path = self.manager.backup_db("natural-test")
        conn = sqlite3.connect(backup_path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 5)

    def test_hundreds_of_whole_records_can_be_selected_and_updated(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            manager = TagCacheManager(cache_dir)
            manager.append_records(
                [
                    {"tags_prompt": f"1girl, pose_{index}, sunset"}
                    for index in range(250)
                ]
            )
            records = manager.get_records_by_position_spec("1-250")
            self.assertEqual(len(records), 250)
            result = manager.update_natural_prompts(
                [
                    {
                        "id": record["id"],
                        "source_tags_prompt": record["tags_prompt"],
                        "natural_prompt": (
                            f"Natural description for the complete record {index}."
                        ),
                    }
                    for index, record in enumerate(records)
                ],
                preset="Krea 2",
                model="bulk-model",
            )
            self.assertEqual(result["updated"], 250)
            self.assertEqual(
                manager.get_natural_prompt_status(),
                {"total": 250, "converted": 250, "missing": 0},
            )

    def test_parse_positions_clamps_ranges_before_iteration_and_caps_selection(self):
        self.assertEqual(
            TagCacheManager.parse_positions("0-999999999999", total=5),
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            TagCacheManager.parse_positions("999999999999-1", total=5),
            [5, 4, 3, 2, 1],
        )
        self.assertEqual(
            TagCacheManager.parse_positions("100-200", total=5),
            [],
        )
        uncapped_total = TagCacheManager.parse_positions("1-999999999999")
        self.assertEqual(len(uncapped_total), TagCacheManager.MAX_POSITION_SELECTION)
        self.assertEqual(uncapped_total[-1], TagCacheManager.MAX_POSITION_SELECTION)

    def test_failed_overwrite_rolls_back_delete_and_partial_inserts(self):
        original = self.manager.get_records_by_position_spec("1-5")
        with self.assertRaises(AttributeError):
            self.manager.save_records(
                [
                    {"tags_prompt": "temporary, replacement"},
                    42,
                ]
            )
        restored = self.manager.get_records_by_position_spec("1-5")
        self.assertEqual(
            [record["id"] for record in restored],
            [record["id"] for record in original],
        )
        self.assertEqual(
            [record["tags_prompt"] for record in restored],
            [record["tags_prompt"] for record in original],
        )

    def test_concurrent_dedupe_is_serialized_but_dedupe_false_is_preserved(self):
        other = TagCacheManager(self.temp_dir.name)

        def append(manager, dedupe):
            return manager.append_records(
                [{"tags_prompt": "shared, concurrent, record"}],
                dedupe=dedupe,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda manager: append(manager, True), [self.manager, other]))
        self.assertEqual(sum(result["inserted"] for result in results), 1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda manager: append(manager, False), [self.manager, other]))
        self.assertEqual(sum(result["inserted"] for result in results), 2)
        conn = self.manager._connect()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM tags WHERE duplicate_key = ?",
                (self.manager._make_duplicate_key({"tags_prompt": "shared, concurrent, record"}),),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 3)

    def test_atomic_batch_read_advances_one_shared_cursor(self):
        other = TagCacheManager(self.temp_dir.name)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(manager.get_next_tags_batch_from_pool, count, False, False)
                for manager, count in ((self.manager, 3), (other, 2))
            ]
            batches = [future.result() for future in futures]

        prompts = [prompt for batch, _, _ in batches for prompt in batch]
        self.assertCountEqual(
            prompts,
            [record["tags_prompt"] for record in self.manager.get_records_by_position_spec("1-5")],
        )
        self.assertEqual(self.manager._get_metadata("current_index"), "5")
        prompt, current_index, total = self.manager.get_next_tags_from_pool(loop=False)
        self.assertIsNone(prompt)
        self.assertEqual((current_index, total), (5, 5))

    def test_delete_uses_database_cursor_instead_of_stale_manager_state(self):
        stale_manager = TagCacheManager(self.temp_dir.name)
        prompts, current_index, total = self.manager.get_next_tags_batch_from_pool(
            3,
            loop=False,
        )
        self.assertEqual(len(prompts), 3)
        self.assertEqual((current_index, total), (3, 5))
        self.assertEqual(stale_manager.current_index, 0)

        fifth_id = stale_manager.get_records_by_position_spec("5")[0]["id"]
        self.assertEqual(stale_manager.delete_by_ids([fifth_id]), 1)

        next_prompts, next_index, next_total = self.manager.get_next_tags_batch_from_pool(
            1,
            loop=False,
        )
        self.assertEqual(next_prompts, ["1boy, rain"])
        self.assertEqual((next_index, next_total), (4, 4))

    def test_batch_read_freezes_filtered_pool_order_and_wrapper_continues(self):
        records = self.manager.get_records_by_position_spec("1-5")
        self.manager.create_filtered_pool_by_ids(
            [records[4]["id"], records[0]["id"], records[2]["id"]]
        )
        self.manager.use_filtered_pool(True)
        batch, current_index, total = self.manager.get_next_tags_batch_from_pool(
            2,
            loop=False,
        )
        self.assertEqual(
            batch,
            [records[4]["tags_prompt"], records[0]["tags_prompt"]],
        )
        self.assertEqual((current_index, total), (2, 3))
        prompt, current_index, total = self.manager.get_next_tags_from_pool(loop=False)
        self.assertEqual(prompt, records[2]["tags_prompt"])
        self.assertEqual((current_index, total), (3, 3))

    def test_batch_read_uses_one_rule_snapshot_with_limit_and_loop(self):
        records = self.manager.get_records_by_position_spec("1-5")
        rule_id, _ = self.manager.create_rule(
            name="newest two",
            sort_order="Newest",
            limit_count=2,
        )
        self.manager.activate_rule(rule_id)
        batch, current_index, total = self.manager.get_next_tags_batch_from_pool(
            3,
            loop=True,
        )
        self.assertEqual(
            batch,
            [
                records[4]["tags_prompt"],
                records[3]["tags_prompt"],
                records[4]["tags_prompt"],
            ],
        )
        self.assertEqual((current_index, total), (1, 2))

    def test_search_rows_expose_stable_tag_id_for_id_based_pool_creation(self):
        records = self.manager.get_records_by_position_spec("1-5")
        self.manager.delete_by_id(records[1]["id"])
        results = self.manager.search_cache("city")
        self.assertEqual(results[0][0], 2)
        self.assertEqual(results[0][6], records[2]["id"])
        ids = self.manager.tag_ids_from_search_results(results)
        self.assertEqual(ids, [records[2]["id"]])
        self.assertEqual(self.manager.create_filtered_pool_by_ids(ids), 1)

    def test_import_canonicalizes_path_and_enforces_size_and_record_limits(self):
        import_path = os.path.join(self.temp_dir.name, "small.json")
        with open(import_path, "w", encoding="utf-8") as handle:
            json.dump([{"tags_prompt": "one"}, {"tags_prompt": "two"}], handle)
        loaded = self.manager._load_import_records(
            os.path.join(self.temp_dir.name, ".", "small.json")
        )
        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["path"], os.path.realpath(import_path))

        self.manager.MAX_IMPORT_RECORDS = 1
        too_many = self.manager._load_import_records(import_path)
        self.assertFalse(too_many["ok"])
        self.assertIn("上限", too_many["message"])

        self.manager.MAX_IMPORT_RECORDS = 100
        self.manager.MAX_IMPORT_BYTES = 8
        too_large = self.manager._load_import_records(import_path)
        self.assertFalse(too_large["ok"])
        self.assertIn("过大", too_large["message"])

    def test_duplicate_migration_marker_prevents_startup_full_table_rebuild(self):
        first = self.manager.get_records_by_position_spec("1")[0]
        conn = self.manager._connect()
        conn.execute(
            "UPDATE tags SET duplicate_key = 'intentionally-stale' WHERE id = ?",
            (first["id"],),
        )
        conn.commit()
        conn.close()
        TagCacheManager(self.temp_dir.name)
        conn = self.manager._connect()
        try:
            duplicate_key = conn.execute(
                "SELECT duplicate_key FROM tags WHERE id = ?",
                (first["id"],),
            ).fetchone()["duplicate_key"]
        finally:
            conn.close()
        self.assertEqual(duplicate_key, "intentionally-stale")

    def test_backup_retention_prunes_oldest_snapshots(self):
        self.manager.BACKUP_MAX_COUNT = 2
        self.manager.BACKUP_MAX_AGE_DAYS = 365
        paths = [self.manager.backup_db(f"retention-{index}") for index in range(3)]
        remaining = [
            path
            for path in paths
            if os.path.exists(path)
        ]
        self.assertEqual(len(remaining), 2)
        self.assertFalse(os.path.exists(paths[0]))

    def test_natural_prompt_hash_invalidates_stale_derived_text(self):
        record = self.manager.get_records_by_position_spec("1")[0]
        self._save(record, "A blue-haired girl.")
        conn = self.manager._connect()
        conn.execute(
            "UPDATE tags SET tags_prompt = ?, tags = ? WHERE id = ?",
            ("changed, after, conversion", "changed, after, conversion", record["id"]),
        )
        conn.commit()
        conn.close()
        stale = self.manager.get_record_by_id(record["id"])
        self.assertEqual(stale["natural_prompt"], "")
        self.assertEqual(self.manager.get_natural_prompt_by_id(record["id"]), "")
        self.assertEqual(
            self.manager.get_natural_prompt_status(),
            {"total": 5, "converted": 0, "missing": 5},
        )

    def test_legacy_natural_prompt_metadata_is_backfilled_but_treated_as_stale(self):
        record = self.manager.get_records_by_position_spec("1")[0]
        self._save(record, "Legacy conversion.")
        conn = self.manager._connect()
        conn.execute(
            """
            UPDATE tags
            SET natural_source_hash = '', natural_converter_version = ''
            WHERE id = ?
            """,
            (record["id"],),
        )
        conn.execute(
            "DELETE FROM metadata WHERE key = 'natural_metadata_migration_v1'"
        )
        conn.commit()
        conn.close()

        reopened = TagCacheManager(self.temp_dir.name)
        migrated = reopened.get_record_by_id(record["id"])
        self.assertEqual(migrated["natural_prompt"], "")
        check = reopened._connect()
        raw = check.execute(
            """
            SELECT natural_prompt, natural_source_hash, natural_converter_version
            FROM tags WHERE id = ?
            """,
            (record["id"],),
        ).fetchone()
        check.close()
        self.assertEqual(raw["natural_prompt"], "Legacy conversion.")
        self.assertEqual(raw["natural_converter_version"], "legacy")
        self.assertEqual(
            raw["natural_source_hash"],
            reopened._natural_source_hash(record["tags_prompt"]),
        )

    def test_duplicate_identity_uses_original_tags_not_derived_prompt(self):
        result = self.manager.append_records(
            [
                {
                    "tags_raw": "1girl, blue_hair",
                    "tags_prompt": "A blue-haired girl standing in soft light.",
                }
            ]
        )
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["skipped_duplicate"], 1)

    def test_duplicate_rebuild_preserves_distinct_original_tag_sets(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            manager = TagCacheManager(cache_dir)
            manager.append_records(
                [
                    {
                        "tags_raw": "alpha, red",
                        "tags_prompt": "same, cleaned, prompt",
                    },
                    {
                        "tags_raw": "beta, blue",
                        "tags_prompt": "same, cleaned, prompt",
                    },
                ],
                dedupe=False,
            )

            result = manager.compact_duplicates()
            records = manager.get_records_by_position_spec("1-2")

            self.assertEqual(result["removed"], 0)
            self.assertEqual(len(records), 2)
            self.assertNotEqual(
                records[0]["duplicate_key"],
                records[1]["duplicate_key"],
            )

    def test_failed_overwrite_does_not_replace_previous_undo_journal(self):
        deleted = self.manager.get_records_by_position_spec("2")[0]
        self.manager.delete_by_id(deleted["id"])
        before = self.manager.get_last_deleted_info()
        with open(before["path"], "r", encoding="utf-8") as handle:
            before_payload = json.load(handle)

        with self.assertRaises(AttributeError):
            self.manager.save_records(
                [
                    {"tags_prompt": "temporary, replacement"},
                    42,
                ]
            )

        after = self.manager.get_last_deleted_info()
        with open(after["path"], "r", encoding="utf-8") as handle:
            after_payload = json.load(handle)
        self.assertEqual(after_payload["records"], before_payload["records"])
        self.assertEqual(after_payload["records"][0]["id"], deleted["id"])

    def test_concurrent_deletes_publish_undo_journal_in_commit_order(self):
        first, second = self.manager.get_records_by_position_spec("1-2")
        second_manager = TagCacheManager(self.temp_dir.name)
        first_journal_started = threading.Event()
        allow_first_journal = threading.Event()
        original_save = self.manager._save_last_deleted_after_commit
        call_count = 0
        call_count_lock = threading.Lock()

        def delayed_save(*args, **kwargs):
            nonlocal call_count
            with call_count_lock:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_journal_started.set()
                self.assertTrue(allow_first_journal.wait(timeout=5))
            return original_save(*args, **kwargs)

        with mock.patch.object(
            self.manager,
            "_save_last_deleted_after_commit",
            side_effect=delayed_save,
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(self.manager.delete_by_id, first["id"])
                self.assertTrue(first_journal_started.wait(timeout=5))
                second_future = executor.submit(second_manager.delete_by_id, second["id"])
                time.sleep(0.1)
                self.assertFalse(second_future.done())
                allow_first_journal.set()
                self.assertTrue(first_future.result(timeout=5))
                self.assertTrue(second_future.result(timeout=5))

        journal_info = self.manager.get_last_deleted_info()
        with open(journal_info["path"], "r", encoding="utf-8") as handle:
            journal = json.load(handle)
        self.assertEqual(journal["records"][0]["id"], second["id"])

    def test_batch_read_can_report_natural_prompt_kind(self):
        first = self.manager.get_records_by_position_spec("1")[0]
        self._save(first, "A girl by a lake, under warm sunset light.")
        self.manager.reset_index()

        entries, current_index, total = self.manager.get_next_tags_batch_from_pool(
            2,
            loop=False,
            prefer_natural=True,
            include_prompt_metadata=True,
        )

        self.assertEqual((current_index, total), (2, 5))
        self.assertEqual(
            entries[0],
            {
                "prompt": "A girl by a lake, under warm sunset light.",
                "is_natural": True,
            },
        )
        self.assertEqual(entries[1]["prompt"], "forest, night")
        self.assertFalse(entries[1]["is_natural"])

    def test_create_rule_rolls_back_rule_and_activation_together(self):
        with mock.patch.object(
            self.manager,
            "_save_index",
            side_effect=RuntimeError("injected failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                self.manager.create_rule(name="atomic", query="1girl")

        conn = self.manager._connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM rules").fetchone()["c"], 0)
            active = conn.execute(
                "SELECT value FROM metadata WHERE key = 'active_rule_id'"
            ).fetchone()
            self.assertTrue(active is None or active["value"] in (None, ""))
        finally:
            conn.close()

    def test_delete_rule_rolls_back_rule_and_deactivation_together(self):
        rule_id, _ = self.manager.create_rule(name="atomic-delete", query="1girl")
        with mock.patch.object(
            self.manager,
            "_save_index",
            side_effect=RuntimeError("injected failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                self.manager.delete_rule(rule_id)

        self.assertIsNotNone(self.manager.get_rule(rule_id))
        self.assertEqual(self.manager.get_active_rule_id(), rule_id)
        self.assertTrue(self.manager.is_using_filtered_pool())

    def test_jump_updates_the_database_cursor_for_the_active_rule_snapshot(self):
        records = self.manager.get_records_by_position_spec("1-5")
        rule_id, _ = self.manager.create_rule(
            name="score-order",
            sort_order="High Score",
            limit_count=3,
        )
        self.assertEqual(self.manager.get_active_rule_id(), rule_id)

        result = self.manager.jump_to_id(records[3]["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["position"], 2)
        conn = self.manager._connect()
        try:
            stored = conn.execute(
                "SELECT value FROM metadata WHERE key = 'current_index'"
            ).fetchone()["value"]
        finally:
            conn.close()
        self.assertEqual(stored, "1")


if __name__ == "__main__":
    unittest.main()
