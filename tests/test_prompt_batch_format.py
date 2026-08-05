import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts.cache_db import TagCacheManager


class PromptBatchFormatTests(unittest.TestCase):
    def test_versioned_export_and_import_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            manager.append_records([{"tags_prompt": "1girl, blue_eyes", "booru": "x"}])
            exported = manager.export_records("json")
            payload = json.loads(Path(exported["path"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "prompt_batch.v1")
            self.assertEqual(payload["records"][0]["prompt"]["positive"], "1girl, blue_eyes")

            imported = Path(directory) / "batch.json"
            imported.write_text(json.dumps({"schema_version": "prompt_batch.v1", "records": [{"prompt": {"positive": "2girls"}}]}), encoding="utf-8")
            result = manager.import_records(str(imported))
            self.assertTrue(result["ok"])

            legacy = Path(directory) / "legacy.json"
            legacy.write_text(json.dumps(["3girls"]), encoding="utf-8")
            self.assertTrue(manager.import_records(str(legacy))["ok"])

    def test_same_prompt_from_two_images_stays_as_two_records(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            source = Path(directory) / "images.json"
            source.write_text(json.dumps({
                "schema_version": "prompt_batch.v1",
                "producer": {"name": "collector"},
                "records": [
                    {"record_id": "one", "image": {"filename": "one.png", "sha256": "a" * 64}, "prompt": {"positive": "same prompt"}},
                    {"record_id": "two", "image": {"filename": "two.png", "sha256": "b" * 64}, "prompt": {"positive": "same prompt"}},
                ],
            }), encoding="utf-8")

            imported = manager.import_records(str(source), dedupe=True)
            self.assertEqual(imported["inserted"], 2)
            self.assertEqual(manager.import_records(str(source), dedupe=True)["inserted"], 0)

            exported = manager.export_records("json")
            payload = json.loads(Path(exported["path"]).read_text(encoding="utf-8"))
            self.assertEqual([record["record_id"] for record in payload["records"]], ["one", "two"])
            self.assertEqual([record["image"]["filename"] for record in payload["records"]], ["one.png", "two.png"])
            self.assertEqual([record["image"]["sha256"] for record in payload["records"]], ["a" * 64, "b" * 64])

    def test_llm_processed_prompt_round_trips_as_natural_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            source = Path(directory) / "processed.json"
            source.write_text(json.dumps({
                "schema_version": "prompt_batch.v1",
                "records": [{
                    "record_id": "one",
                    "image": {"filename": "one.png", "sha256": "a" * 64},
                    "prompt": {"positive": "raw tags", "processed": "polished prompt"},
                }],
            }), encoding="utf-8")
            self.assertEqual(manager.import_records(str(source))["inserted"], 1)
            exported = manager.export_records("json")
            record = json.loads(Path(exported["path"]).read_text(encoding="utf-8"))["records"][0]
            self.assertEqual(record["prompt"]["positive"], "raw tags")
            self.assertEqual(record["prompt"]["processed"], "polished prompt")

    def test_prompt_batch_identity_is_validated_and_producer_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            duplicate = Path(directory) / "duplicate.json"
            duplicate.write_text(json.dumps({
                "schema_version": "prompt_batch.v1",
                "producer": {"name": "collector-a"},
                "records": [
                    {"record_id": "same", "image": {"filename": "one.png"}, "prompt": {"positive": "one"}},
                    {"record_id": "same", "image": {"filename": "two.png"}, "prompt": {"positive": "two"}},
                ],
            }), encoding="utf-8")
            self.assertIn("record_id 重复", manager._load_import_records(str(duplicate))["message"])

            invalid_sha = Path(directory) / "invalid-sha.json"
            invalid_sha.write_text(json.dumps({
                "schema_version": "prompt_batch.v1",
                "records": [{"image": {"filename": "one.png", "sha256": "bad"}, "prompt": {"positive": "one"}}],
            }), encoding="utf-8")
            self.assertIn("sha256", manager._load_import_records(str(invalid_sha))["message"])

            first = {"record_id": "same", "prompt_batch_producer": "collector-a", "image": {"filename": "one.png"}}
            second = {"record_id": "same", "prompt_batch_producer": "collector-b", "image": {"filename": "one.png"}}
            first["search_query"] = manager.prompt_batch_search_query(first)
            second["search_query"] = manager.prompt_batch_search_query(second)
            self.assertNotEqual(manager._make_duplicate_key(first), manager._make_duplicate_key(second))

    def test_processing_status_round_trips_through_ranbooru(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            source = Path(directory) / "status.json"
            source.write_text(json.dumps({
                "schema_version": "prompt_batch.v1",
                "producer": {"name": "collector"},
                "records": [{
                    "record_id": "one",
                    "image": {"filename": "one.png", "sha256": "a" * 64},
                    "prompt": {"positive": "raw", "processed": "done"},
                    "status": "completed",
                    "error": "kept evidence",
                    "appended": True,
                }],
            }), encoding="utf-8")
            self.assertEqual(manager.import_records(str(source))["inserted"], 1)
            record = json.loads(Path(manager.export_records("json")["path"]).read_text(encoding="utf-8"))["records"][0]
            self.assertEqual(record["status"], "completed")
            self.assertEqual(record["error"], "kept evidence")
            self.assertTrue(record["appended"])

    def test_json_export_and_import_have_no_batch_record_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            manager.append_records(
                [{"tags_prompt": f"prompt {index} " + "x" * 1024} for index in range(5001)],
                dedupe=False,
            )
            result = manager.export_records("json")
            self.assertEqual(result["count"], 5001)
            self.assertEqual(result["total_count"], 5001)
            self.assertFalse(result["truncated"])
            self.assertGreater(Path(result["path"]).stat().st_size, 4 * 1024 * 1024)

            imported = TagCacheManager(Path(directory) / "imported")
            imported_result = imported.import_records(result["path"], dedupe=False)
            self.assertTrue(imported_result["ok"])
            self.assertEqual(imported_result["inserted"], 5001)

    def test_oversized_legacy_json_is_rejected_before_full_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            manager.MAX_IMPORT_BYTES = 16
            legacy = Path(directory) / "legacy-large.json"
            legacy.write_text(json.dumps({"records": ["x" * 64]}), encoding="utf-8")

            with mock.patch("scripts.cache_db.json.load") as load:
                result = manager._load_import_records(str(legacy))

            self.assertFalse(result["ok"])
            self.assertIn("导入文件过大", result["message"])
            load.assert_not_called()

    def test_oversized_prompt_batch_uses_unbounded_schema_path(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            manager.MAX_IMPORT_BYTES = 16
            source = Path(directory) / "prompt-batch-large.json"
            source.write_text(json.dumps({
                "producer": {"name": "collector"},
                "schema_version": "prompt_batch.v1",
                "records": [{"image": {"filename": "one.png"}, "prompt": {"positive": "prompt"}}],
            }), encoding="utf-8")

            result = manager._load_import_records(str(source))

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["records"]), 1)

    def test_oversized_json_with_duplicate_schema_is_rejected_before_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            manager.MAX_IMPORT_BYTES = 16
            source = Path(directory) / "duplicate-schema.json"
            source.write_text(
                '{"schema_version":"prompt_batch.v1","schema_version":"legacy","records":[]}',
                encoding="utf-8",
            )

            with mock.patch("scripts.cache_db.json.load") as load:
                result = manager._load_import_records(str(source))

            self.assertFalse(result["ok"])
            self.assertIn("导入文件过大", result["message"])
            load.assert_not_called()

    def test_oversized_malformed_json_is_rejected_without_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            manager.MAX_IMPORT_BYTES = 16
            source = Path(directory) / "malformed-large.json"
            source.write_text('{"producer":"' + "x" * 64, encoding="utf-8")

            with mock.patch("scripts.cache_db.json.load") as load:
                result = manager._load_import_records(str(source))

            self.assertFalse(result["ok"])
            self.assertIn("导入文件过大", result["message"])
            load.assert_not_called()

    def test_oversized_json_with_invalid_tokens_is_rejected_before_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            manager.MAX_IMPORT_BYTES = 16
            invalid_payloads = (
                '{"schema_version":"prompt_batch.v1","producer":wat,"records":[]}',
                '{"schema_version":"prompt_batch.v1","producer":"\\q","records":[]}',
            )
            for index, payload in enumerate(invalid_payloads):
                with self.subTest(index=index):
                    source = Path(directory) / f"invalid-token-{index}.json"
                    source.write_text(payload, encoding="utf-8")
                    with mock.patch("scripts.cache_db.json.load") as load:
                        result = manager._load_import_records(str(source))
                    self.assertFalse(result["ok"])
                    self.assertIn("导入文件过大", result["message"])
                    load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
