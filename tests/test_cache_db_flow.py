import json
import tempfile
import unittest
from pathlib import Path

from scripts.cache_db import TagCacheManager


def _record(post_id, prompt, score=0):
    return {
        "booru": "safebooru",
        "post_id": str(post_id),
        "tags_raw": prompt.replace(",", " "),
        "tags_prompt": prompt,
        "score": score,
        "rating": "safe",
        "source_url": f"https://example.test/posts/{post_id}",
        "preview_url": f"https://example.test/previews/{post_id}.jpg",
    }


class CacheDbFlowTests(unittest.TestCase):
    def test_append_dedupe_cursor_delete_restore_and_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            stats = manager.append_records([
                _record(1, "one,two", 1),
                _record(2, "three,four", 2),
                _record(3, "one,two", 3),
            ])

            self.assertEqual(stats["inserted"], 2)
            self.assertEqual(stats["skipped_duplicate"], 1)
            self.assertEqual(manager.get_active_total(), 2)

            first, index, total = manager.get_next_tags_batch_from_pool(2, loop=False)
            self.assertEqual(first, ["one,two", "three,four"])
            self.assertEqual((index, total), (2, 2))
            self.assertEqual(manager.get_next_tags_batch_from_pool(1, loop=False)[0], [])

            looped, index, total = manager.get_next_tags_batch_from_pool(1, loop=True)
            self.assertEqual(looped, ["one,two"])
            self.assertEqual((index, total), (1, 2))

            deleted = manager.delete_by_any_tags("three")
            self.assertEqual(deleted, 1)
            self.assertEqual(manager.get_active_total(), 1)

            restored = manager.restore_last_deleted()
            self.assertTrue(restored["ok"])
            self.assertEqual(restored["inserted"], 1)
            self.assertEqual(manager.get_active_total(), 2)
            self.assertFalse(Path(directory, "last_deleted.json").exists())
            repeated = manager.restore_last_deleted()
            self.assertFalse(repeated["ok"])

            manager.update_natural_prompts([{
                "id": 1,
                "source_tags_prompt": "one,two",
                "natural_prompt": "one and two",
            }])
            manager.reset_index()
            converted, _, _ = manager.get_next_tags_batch_from_pool(
                1,
                loop=False,
                prefer_natural=True,
            )
            self.assertEqual(converted, ["one and two"])

    def test_export_import_and_overwrite_create_backup(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = TagCacheManager(source_dir)
            source.append_records([_record(1, "one,two"), _record(2, "three,four")])
            export_result = source.export_records("json")
            export_path = Path(export_result["path"])
            self.assertTrue(export_path.is_file())

            target = TagCacheManager(target_dir)
            imported = target.import_records(str(export_path), append=True, dedupe=True)
            self.assertTrue(imported["ok"])
            self.assertEqual(imported["inserted"], 2)
            self.assertEqual(target.get_active_total(), 2)

            target.append_records([_record(3, "five,six")])
            overwritten = target.import_records(str(export_path), append=False, dedupe=True)
            self.assertTrue(overwritten["ok"])
            self.assertEqual(target.get_active_total(), 2)
            self.assertTrue(overwritten["backup_path"])
            self.assertTrue(Path(overwritten["backup_path"]).is_file())

    def test_prompt_batch_import_normalizes_record_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            payload = {
                "schema_version": "prompt_batch.v1",
                "producer": {"name": "test"},
                "records": [{
                    "record_id": "record-1",
                    "prompt": {"positive": "one,two", "negative": ""},
                    "image": {"filename": "one.png"},
                    "booru": {"site": "safebooru", "post_id": "1"},
                }],
            }
            path = Path(directory) / "batch.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            result = manager.import_records(str(path), append=True, dedupe=True)

            self.assertTrue(result["ok"])
            self.assertEqual(result["inserted"], 1)
            self.assertEqual(manager.get_by_position(1), "one,two")


if __name__ == "__main__":
    unittest.main()
