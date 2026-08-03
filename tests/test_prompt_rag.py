import ast
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.cache_db import TagCacheManager
from scripts.natural_language import (
    BACKEND_OPENAI,
    BACKEND_OLLAMA,
    ENDPOINT_POLICY_UNRESTRICTED,
    CachedTagNaturalLanguageConverter,
    NaturalLanguageConfig,
    iter_cached_tag_conversions,
)


class _FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(
            {"choices": [{"message": {"content": "A converted prompt."}}]}
        )


class PromptRagCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = TagCacheManager(self.temp_dir.name)
        self.manager.append_records(
            [
                {
                    "booru": "site-a",
                    "tags_prompt": "1girl, blue_hair, outdoors",
                    "post_id": "1",
                    "score": 10,
                },
                {
                    "booru": "site-a",
                    "tags_prompt": "1girl, blue_hair, portrait",
                    "post_id": "2",
                    "score": 100,
                },
                {
                    "booru": "site-b",
                    "tags_prompt": "1girl, red_hair, sunset",
                    "post_id": "3",
                    "score": 1000,
                },
                {
                    "booru": "site-b",
                    "tags_prompt": "mecha, city, night",
                    "post_id": "4",
                    "score": 5000,
                },
            ]
        )
        records = self.manager.get_records_by_position_spec("1-4")
        for record in records:
            self.manager.update_natural_prompts(
                [
                    {
                        "id": record["id"],
                        "source_tags_prompt": record["tags_prompt"],
                        "natural_prompt": f"Converted post {record['post_id']}.",
                    }
                ],
                preset="Krea 2",
                model="test-model",
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_retrieval_requires_overlap_and_uses_score_as_tie_breaking_prior(self):
        candidates = self.manager.get_prompt_rag_candidates(min_score_percentile=0)
        examples = self.manager.select_prompt_rag_examples(
            "1girl, blue_hair, indoors",
            candidates,
            limit=3,
        )

        self.assertEqual([item["id"] for item in examples[:2]], [2, 1])
        self.assertNotIn(4, [item["id"] for item in examples])
        self.assertTrue(all(item["overlap"] > 0 for item in examples))

    def test_candidate_snapshot_excludes_low_score_and_stale_conversions(self):
        conn = self.manager._connect()
        conn.execute(
            "UPDATE tags SET tags_prompt = ?, tags = ? WHERE id = ?",
            ("changed, source", "changed, source", 2),
        )
        conn.execute(
            "UPDATE tags SET natural_converter_version = ? WHERE id = ?",
            ("old-converter", 3),
        )
        conn.commit()
        conn.close()

        candidates = self.manager.get_prompt_rag_candidates(min_score_percentile=0)

        self.assertEqual([item["id"] for item in candidates], [4, 1])

    def test_exact_source_is_not_returned_as_its_own_example(self):
        candidates = self.manager.get_prompt_rag_candidates(min_score_percentile=0)
        examples = self.manager.select_prompt_rag_examples(
            "1girl, blue_hair, portrait",
            candidates,
            limit=5,
        )

        self.assertNotIn(2, [item["id"] for item in examples])

    def test_default_candidate_floor_is_per_booru_top_quartile(self):
        candidates = self.manager.get_prompt_rag_candidates(preset="Krea 2")

        self.assertEqual([item["id"] for item in candidates], [2, 4])
        self.assertTrue(all(item["score_percentile"] == 1.0 for item in candidates))

    def test_weak_single_tag_overlap_and_empty_queries_return_no_examples(self):
        candidates = self.manager.get_prompt_rag_candidates(min_score_percentile=0)

        self.assertEqual(
            self.manager.select_prompt_rag_examples(
                "1girl, green_hair, forest",
                candidates,
            ),
            [],
        )
        self.assertEqual(
            self.manager.select_prompt_rag_examples("", candidates),
            [],
        )

    def test_candidate_preset_must_match_when_requested(self):
        self.assertEqual(
            self.manager.get_prompt_rag_candidates(
                preset="Different preset",
                min_score_percentile=0,
            ),
            [],
        )

    def test_candidate_loading_pages_past_stale_rows_to_fill_limit(self):
        conn = self.manager._connect()
        conn.execute("UPDATE tags SET booru = ?", ("one-site",))
        conn.execute(
            "UPDATE tags SET tags_prompt = ?, tags = ? WHERE id = ?",
            ("stale, highest", "stale, highest", 4),
        )
        conn.execute(
            "UPDATE tags SET tags_prompt = ?, tags = ? WHERE id = ?",
            ("stale, second", "stale, second", 3),
        )
        conn.commit()
        conn.close()

        candidates = self.manager.get_prompt_rag_candidates(
            min_score_percentile=0,
            max_candidates=2,
        )

        self.assertEqual([item["id"] for item in candidates], [2, 1])


class CacheCursorSessionTests(unittest.TestCase):
    def test_new_process_session_resets_saved_cache_cursor(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            manager = TagCacheManager(cache_dir)
            manager.append_records(
                [
                    {"tags_prompt": "first"},
                    {"tags_prompt": "second"},
                ]
            )
            prompt, index, _ = manager.get_next_tags_from_pool(loop=False)
            self.assertEqual((prompt, index), ("first", 1))

            same_session = TagCacheManager(cache_dir)
            self.assertEqual(same_session.current_index, 1)

            lock_key = os.path.normcase(os.path.realpath(manager.db_path))
            with TagCacheManager._destructive_locks_guard:
                TagCacheManager._session_cursor_paths.discard(lock_key)

            new_session = TagCacheManager(cache_dir)
            self.assertEqual(new_session.current_index, 0)
            prompt, index, _ = new_session.get_next_tags_from_pool(loop=False)
            self.assertEqual((prompt, index), ("first", 1))


class PromptFewShotMessageTests(unittest.TestCase):
    def test_retrieved_pairs_are_inserted_before_the_current_request(self):
        session = _FakeSession()
        converter = CachedTagNaturalLanguageConverter(session)
        result = converter.convert(
            "1girl, blue_hair, indoors",
            NaturalLanguageConfig(
                backend=BACKEND_OPENAI,
                endpoint="https://example.test/v1",
                endpoint_policy=ENDPOINT_POLICY_UNRESTRICTED,
                model="test-model",
            ),
            examples=[
                {
                    "tags_prompt": "1girl, blue_hair, portrait",
                    "natural_prompt": "A blue-haired girl in a close portrait.",
                },
                {
                    "tags_prompt": "1girl, indoors, window",
                    "natural_prompt": "A girl stands indoors beside a window.",
                },
            ],
        )

        self.assertEqual(result, "A converted prompt.")
        messages = session.calls[0][1]["json"]["messages"]
        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user", "assistant", "user", "assistant", "user"],
        )
        self.assertIn("formatting examples", messages[0]["content"])
        self.assertIn("blue hair, portrait", messages[1]["content"])
        self.assertEqual(
            messages[-1]["content"],
            "Tags:\n1girl, blue hair, indoors",
        )

    def test_context_budget_skips_oversized_pairs_and_keeps_smaller_ones(self):
        messages = CachedTagNaturalLanguageConverter._few_shot_messages(
            [
                {
                    "tags_prompt": "1girl, blue_hair, portrait",
                    "natural_prompt": "x" * 200,
                },
                {
                    "tags_prompt": "forest",
                    "natural_prompt": "A forest.",
                },
            ],
            max_chars=50,
        )

        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["content"], "A forest.")

    def test_retrieval_failure_falls_back_to_zero_shot(self):
        class _Converter:
            def convert(self, tags, config):
                return f"Converted {tags}."

        def failing_provider(_tags):
            raise RuntimeError("local retrieval failed")

        with mock.patch("scripts.natural_language.logger.warning") as warning:
            rows = list(
                iter_cached_tag_conversions(
                    ["forest, night"],
                    NaturalLanguageConfig(
                        backend=BACKEND_OLLAMA,
                        model="test-model",
                    ),
                    converter=_Converter(),
                    example_provider=failing_provider,
                )
            )

        self.assertEqual(rows[0][2], "Converted forest, night.")
        self.assertEqual(rows[0][3], "")
        warning.assert_called_once()


class PromptRagUiContractTests(unittest.TestCase):
    def test_gradio_callback_inputs_match_extended_method_signatures(self):
        source = ast.parse(
            (Path(__file__).parents[1] / "scripts" / "ranbooru.py").read_text(
                encoding="utf-8"
            )
        )
        script_class = next(
            node
            for node in source.body
            if isinstance(node, ast.ClassDef) and node.name == "Script"
        )
        methods = {
            node.name: node
            for node in script_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        credentials_class = next(
            node
            for node in source.body
            if isinstance(node, ast.ClassDef) and node.name == "CredentialsManager"
        )
        save_settings = next(
            node
            for node in credentials_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "save_natural_language_settings"
        )
        default_offset = len(save_settings.args.args) - len(save_settings.args.defaults)
        defaults = {
            argument.arg: default
            for argument, default in zip(
                save_settings.args.args[default_offset:],
                save_settings.args.defaults,
            )
        }
        self.assertIs(defaults["rag_enabled"].value, False)
        ui_method = methods["ui"]
        click_inputs = {}
        click_outputs = {}
        for node in ast.walk(ui_method):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "click"
                and isinstance(node.func.value, ast.Name)
            ):
                continue
            keyword_values = {keyword.arg: keyword.value for keyword in node.keywords}
            inputs = keyword_values.get("inputs")
            outputs = keyword_values.get("outputs")
            if isinstance(inputs, ast.List):
                click_inputs[node.func.value.id] = len(inputs.elts)
            if isinstance(outputs, ast.List):
                click_outputs[node.func.value.id] = len(outputs.elts)

        self.assertEqual(
            click_inputs["cache_natural_language_save_settings_btn"],
            len(methods["_save_natural_language_settings"].args.args),
        )
        self.assertEqual(
            click_inputs["cache_natural_language_convert_btn"],
            len(methods["_cache_batch_convert_natural"].args.args),
        )
        clear_return = next(
            node
            for node in ast.walk(methods["_clear_natural_language_settings"])
            if isinstance(node, ast.Return)
        )
        self.assertEqual(
            click_outputs["cache_natural_language_clear_settings_btn"],
            len(clear_return.value.elts),
        )


if __name__ == "__main__":
    unittest.main()
