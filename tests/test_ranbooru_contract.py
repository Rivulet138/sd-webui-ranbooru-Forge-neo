import ast
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest


RANBOORU_PATH = Path(__file__).parents[1] / "scripts" / "ranbooru.py"


class RanbooruStaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = RANBOORU_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.script_class = next(
            node
            for node in cls.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Script"
        )
        cls.methods = {
            node.name: node
            for node in cls.script_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_manual_callbacks_only_accept_preconverted_preference(self):
        expected = {
            "_cache_fill_position_to_tag_prompt": 4,
            "_cache_get_next_and_set": 5,
            "_cache_jump_take_and_set": 6,
        }
        actual = {
            name: len(self.methods[name].args.args)
            for name in expected
        }
        self.assertEqual(actual, expected)
        for name in expected:
            arg_names = [arg.arg for arg in self.methods[name].args.args]
            self.assertIn("prefer_natural", arg_names)
            self.assertFalse(any("backend" in arg for arg in arg_names))
            self.assertFalse(any("tag_count" in arg for arg in arg_names))

    def test_batch_callback_converts_selected_records(self):
        method = self.methods["_cache_batch_convert_natural_unlocked"]
        self.assertEqual(
            [arg.arg for arg in method.args.args],
            [
                "position_spec",
                "only_missing",
                "preset",
                "backend",
                "endpoint_policy",
                "endpoint",
                "model",
                "api_key",
                "timeout",
            ],
        )
        called = {
            node.func.id
            for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("iter_cached_tag_conversions", called)
        attributes = {
            node.func.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("get_records_by_position_spec", attributes)
        self.assertIn("update_natural_prompts", attributes)
        self.assertIn("backup_db", attributes)

        wrapper = self.methods["_cache_batch_convert_natural"]
        wrapper_source = ast.unparse(wrapper)
        self.assertIn("_natural_batch_lock.acquire", wrapper_source)
        self.assertIn("_cache_batch_convert_natural_unlocked", wrapper_source)
        self.assertIn("_natural_batch_lock.release", wrapper_source)

        method_source = ast.unparse(method)
        self.assertIn("update_batch_size = 10", method_source)
        self.assertIn("len(pending_updates) >= update_batch_size", method_source)
        self.assertIn("max_consecutive_timeouts = 6", method_source)
        self.assertIn("consecutive_timeouts >= max_consecutive_timeouts", method_source)
        self.assertIn("timeout_skipped += 1", method_source)

    def test_ui_return_only_exposes_generation_read_settings(self):
        ui_method = self.methods["ui"]
        return_lists = [
            node.value
            for node in ast.walk(ui_method)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.List)
        ]
        self.assertEqual(len(return_lists), 1)
        return_names = [item.id for item in return_lists[0].elts]
        self.assertEqual(
            return_names[-2:],
            ["cache_prompt_write_mode", "use_preconverted_cache_prompt"],
        )
        self.assertNotIn("cache_natural_language_api_key", return_names)
        self.assertNotIn("cache_natural_language_model", return_names)

        before_process = self.methods["before_process"]
        assignments = {}
        for node in before_process.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    assignments[target.id] = ast.unparse(node.value)
        self.assertEqual(
            assignments["cache_prompt_write_mode"],
            "args[0] if args else '追加到后面'",
        )
        self.assertEqual(
            assignments["use_preconverted_cache_prompt"],
            "bool(args[1]) if len(args) > 1 else True",
        )

    def test_batch_button_contains_range_preset_and_model_inputs(self):
        ui_method = self.methods["ui"]
        click = next(
            node
            for node in ast.walk(ui_method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "click"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "cache_natural_language_convert_btn"
        )
        inputs = next(
            keyword.value
            for keyword in click.keywords
            if keyword.arg == "inputs"
        )
        self.assertIsInstance(inputs, ast.List)
        self.assertEqual(
            [item.id for item in inputs.elts],
            [
                "cache_natural_language_positions",
                "cache_natural_language_only_missing",
                "cache_natural_language_preset",
                "cache_natural_language_backend",
                "cache_natural_language_endpoint_policy",
                "cache_natural_language_endpoint",
                "cache_natural_language_model",
                "cache_natural_language_api_key",
                "cache_natural_language_timeout",
            ],
        )

    def test_private_and_fake_ip_endpoints_are_available_without_admin_gate(self):
        ui_source = ast.unparse(self.methods["ui"])
        self.assertIn(
            "saved_natural_settings.get('endpoint_policy', NATURAL_LANGUAGE_ENDPOINT_UNRESTRICTED)",
            ui_source,
        )
        self.assertIn("value=saved_natural_policy", ui_source)
        self.assertNotIn("RANBOORU_ALLOW_PRIVATE_MODEL_ENDPOINTS", self.source)
        batch_source = ast.unparse(
            self.methods["_cache_batch_convert_natural_unlocked"]
        )
        self.assertNotIn("_allow_private_model_endpoints", batch_source)

    def test_generation_never_calls_a_language_model(self):
        before_process = self.methods["before_process"]
        called_functions = {
            node.func.id
            for node in ast.walk(before_process)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("iter_cached_tag_conversions", called_functions)
        self.assertNotIn("CachedTagNaturalLanguageConverter", called_functions)
        self.assertNotIn("convert_cached_tags_safely", called_functions)
        self.assertIn("prefer_natural=use_preconverted_cache_prompt", ast.unparse(before_process))

    def test_obsolete_per_tag_and_raw_state_controls_are_gone(self):
        self.assertNotIn("cache_natural_language_tag_count", self.source)
        self.assertNotIn("cache_next_raw_state", self.source)
        self.assertNotIn("_cache_get_next_with_state", self.source)

    def test_hires_detection_only_uses_explicit_host_phase(self):
        function = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_is_hires_second_pass"
        )
        source = ast.unparse(function)
        self.assertIn("is_hr_pass", source)
        self.assertNotIn("denoising_strength", source)
        self.assertNotIn("init_images", source)

        module = ast.Module(body=[function], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {}
        exec(compile(module, str(RANBOORU_PATH), "exec"), namespace)
        normal_img2img = type(
            "NormalImg2Img",
            (),
            {
                "is_hr_pass": False,
                "init_images": [object()],
                "denoising_strength": 0.75,
            },
        )()
        hires_pass = type("HiresPass", (), {"is_hr_pass": True})()
        self.assertFalse(namespace["_is_hires_second_pass"](normal_img2img))
        self.assertTrue(namespace["_is_hires_second_pass"](hires_pass))

    def test_saved_booru_credentials_are_never_hydrated_into_frontend_values(self):
        ui_source = ast.unparse(self.methods["ui"])
        loader_source = ast.unparse(self.methods["load_gelbooru_credentials"])
        self.assertNotIn("saved_creds.get", ui_source)
        self.assertNotIn("credentials.get('api_key'", loader_source)
        self.assertIn("value=''", loader_source)

    def test_llm_settings_persist_api_key_without_hydrating_it_to_browser(self):
        credentials_class = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "CredentialsManager"
        )
        namespace = {
            "json": json,
            "os": os,
            "threading": threading,
            "PRESET_KREA2": "Krea 2",
            "NATURAL_LANGUAGE_OFF": "off",
            "NATURAL_LANGUAGE_ENDPOINT_UNRESTRICTED": "unrestricted",
        }
        exec(
            compile(
                ast.Module(body=[credentials_class], type_ignores=[]),
                str(RANBOORU_PATH),
                "exec",
            ),
            namespace,
        )
        with tempfile.TemporaryDirectory() as extension_root:
            manager = namespace["CredentialsManager"](extension_root)
            manager.save_booru_credentials("gelbooru", "booru-key", "42")
            saved = manager.save_natural_language_settings(
                "Krea 2",
                "OpenAI",
                "unrestricted",
                "https://llm.example/v1",
                "model-id",
                "secret-key",
                120,
            )
            self.assertEqual(saved["api_key"], "secret-key")

            reopened = namespace["CredentialsManager"](extension_root)
            self.assertEqual(
                reopened.get_natural_language_settings()["model"], "model-id"
            )
            self.assertEqual(
                reopened.get_natural_language_settings()["api_key"], "secret-key"
            )
            preserved = reopened.save_natural_language_settings(
                "Krea 2",
                "OpenAI",
                "unrestricted",
                "https://llm.example/v1",
                "new-model-id",
                "",
                120,
            )
            self.assertEqual(preserved["api_key"], "secret-key")
            self.assertEqual(
                reopened.resolve_natural_language_api_key(
                    "",
                    "OpenAI",
                    "https://llm.example/v1",
                ),
                "secret-key",
            )
            self.assertEqual(
                reopened.resolve_natural_language_api_key(
                    "",
                    "OpenAI",
                    "https://different.example/v1",
                ),
                "",
            )
            reopened.clear_natural_language_settings()
            self.assertEqual(reopened.get_natural_language_settings(), {})
            self.assertEqual(
                reopened.get_booru_credentials("gelbooru")["api_key"],
                "booru-key",
            )

        ui_source = ast.unparse(self.methods["ui"])
        self.assertIn("get_natural_language_settings", ui_source)
        self.assertNotIn("saved_natural_settings.get('api_key', '')", ui_source)
        self.assertIn("value=''", ui_source)
        self.assertIn("cache_natural_language_save_settings_btn.click", ui_source)
        self.assertIn("cache_natural_language_clear_settings_btn.click", ui_source)

    def test_http_cache_is_session_local(self):
        self.assertNotIn("requests_cache.install_cache", self.source)
        self.assertNotIn("requests_cache.uninstall_cache", self.source)
        booru_class = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Booru"
        )
        booru_source = ast.unparse(booru_class)
        self.assertIn("requests_cache.CachedSession", booru_source)
        self.assertIn("self.session.get", booru_source)

    def test_local_cache_runs_common_prompt_pipeline_without_empty_list_division(self):
        before_source = ast.unparse(self.methods["before_process"])
        self.assertIn("_apply_local_cache_prompt_pipeline", before_source)
        self.assertIn("get_next_tags_batch_from_pool", before_source)
        self.assertNotIn("get_next_tags_from_pool", before_source)
        self.assertNotIn("total_images // len(p.prompt)", before_source)
        pipeline = ast.unparse(self.methods["_apply_local_cache_prompt_pipeline"])
        for option in (
            "same_prompt",
            "shuffle_tags",
            "negative_mode",
            "chaos_mode",
            "limit_tags",
            "max_tags",
            "use_same_seed",
        ):
            self.assertIn(option, pipeline)

    def test_postprocess_does_not_append_cross_job_images_when_disabled(self):
        source = ast.unparse(self.methods["postprocess"])
        self.assertIn("enabled and use_last_img", source)
        self.assertIn("_active_job_id == _processing_job_id(p)", source)

    def test_natural_batch_has_cancel_control(self):
        self.assertIn("cache_natural_language_cancel_btn", self.source)
        self.assertIn("_natural_batch_cancel.is_set()", self.source)
        ui_source = ast.unparse(self.methods["ui"])
        self.assertIn("natural_conversion_event", ui_source)
        self.assertIn("cancels=[natural_conversion_event]", ui_source)
        self.assertIn("queue=False", ui_source)

    def test_preconverted_natural_prompt_is_opaque_to_tag_transforms(self):
        before_source = ast.unparse(self.methods["before_process"])
        pipeline_source = ast.unparse(
            self.methods["_apply_local_cache_prompt_pipeline"]
        )
        self.assertIn("include_prompt_metadata=True", before_source)
        self.assertIn("natural_prompt_flags", pipeline_source)
        self.assertIn("if is_natural", pipeline_source)
        self.assertIn("cleaned = cached_prompt", pipeline_source)

    def test_online_generation_closes_selected_booru_client(self):
        method = self.methods["before_process"]
        finally_calls = [
            ast.unparse(statement)
            for node in ast.walk(method)
            if isinstance(node, ast.Try)
            for statement in node.finalbody
        ]
        self.assertIn("api_url.close()", finally_calls)
        self.assertIn("image_api_url.close()", finally_calls)

    def test_search_actions_use_stable_internal_ids(self):
        ui_source = ast.unparse(self.methods["ui"])
        self.assertIn("headers=['主缓存序号', 'Booru', 'Post ID', 'Score', 'Rating', 'Tags', '内部 ID']", ui_source)
        self.assertIn("fn=self._cache_fill_by_id", ui_source)
        self.assertIn("fn=self._cache_delete_by_id", ui_source)
        self.assertIn("fn=self._cache_preview_delete_by_id", ui_source)

    def test_import_uses_upload_component_not_arbitrary_path_textbox(self):
        ui_source = ast.unparse(self.methods["ui"])
        self.assertIn(
            "cache_import_path = gr.File",
            ui_source,
        )
        self.assertIn("file_types=['.json', '.csv']", ui_source)

    def test_partial_batch_fetch_cannot_overwrite_cache(self):
        callback = ast.unparse(self.methods["_cache_batch_fetch"])
        self.assertIn("not fetch_stats.get('complete', True) and (not append_mode)", callback)
        self.assertIn("覆盖保存已取消", callback)

    def test_page_json_failures_are_not_reported_as_empty_pages(self):
        helper = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_response_posts_or_raise"
        )
        namespace = {}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), "<helper>", "exec"), namespace)

        class BrokenResponse:
            @staticmethod
            def json():
                raise ValueError("broken")

        with self.assertRaises(RuntimeError):
            namespace["_response_posts_or_raise"](BrokenResponse(), "test page")

        for class_name in (
            "Gelbooru",
            "e621",
            "XBooru",
            "Rule34",
            "Safebooru",
            "Konachan",
            "Yandere",
            "AIBooru",
            "Danbooru",
        ):
            class_node = next(
                node
                for node in self.tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            )
            method = next(
                node
                for node in class_node.body
                if isinstance(node, ast.FunctionDef) and node.name == "get_data_page"
            )
            self.assertIn("_response_posts_or_raise", ast.unparse(method))

            one_shot = next(
                node
                for node in class_node.body
                if isinstance(node, ast.FunctionDef) and node.name == "get_data"
            )
            self.assertIn("_response_posts_or_raise", ast.unparse(one_shot))

    def test_booru_result_counts_are_request_local(self):
        self.assertNotIn("global COUNT", self.source)
        module_assignments = {
            target.id
            for node in self.tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        self.assertNotIn("COUNT", module_assignments)

    def test_local_cache_job_dedupe_retains_the_current_job_at_capacity(self):
        source = ast.unparse(self.methods["before_process"])
        self.assertNotIn("self._local_cache_applied_jobs.clear()", source)
        prune = source.index("self._local_cache_applied_jobs.pop(oldest_job_id, None)")
        add = source.index("self._local_cache_applied_jobs[job_id] = None")
        self.assertLess(prune, add)

    def test_prompt_preview_creates_and_closes_only_the_selected_client(self):
        method = self.methods["generate_prompts_only"]
        source = ast.unparse(method)
        self.assertIn("_create_booru_api(", source)
        self.assertNotIn("booru_apis = {", source)
        finally_calls = [
            ast.unparse(statement)
            for node in ast.walk(method)
            if isinstance(node, ast.Try)
            for statement in node.finalbody
        ]
        self.assertIn("api_url.close()", finally_calls)

    def test_natural_batch_closes_converter_in_finally(self):
        method = self.methods["_cache_batch_convert_natural_unlocked"]
        finally_calls = [
            ast.unparse(statement)
            for node in ast.walk(method)
            if isinstance(node, ast.Try)
            for statement in node.finalbody
        ]
        self.assertIn("converter.close()", finally_calls)


if __name__ == "__main__":
    unittest.main()
