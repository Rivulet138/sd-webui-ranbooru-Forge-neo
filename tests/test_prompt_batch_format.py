import json
import tempfile
import unittest
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

    def test_json_export_reports_the_compatible_batch_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TagCacheManager(directory)
            manager.append_records([{"tags_prompt": f"prompt {index}"} for index in range(201)], dedupe=False)
            result = manager.export_records("json")
            self.assertEqual(result["count"], 200)
            self.assertEqual(result["total_count"], 201)
            self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
