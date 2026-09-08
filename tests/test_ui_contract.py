import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class RanbooruUiContractTests(unittest.TestCase):
    def test_prompt_studio_bridge_uses_canonical_module_singleton(self):
        source = (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")
        self.assertIn('importlib.import_module("prompt_studio_ui")', source)
        self.assertNotIn('spec_from_file_location("ranbooru_prompt_studio_bridge"', source)

    def test_ranbooru_has_no_collector_receiver(self):
        source = (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")
        self.assertNotIn("_cache_import_payload", source)
        self.assertNotIn("ranbooru_prompt_batch_payload", source)
        self.assertNotIn("导入 Collector 批次", source)
        self.assertNotIn("此旧面板已停用", source)
        self.assertIn('gr.Tab("自然语言转换", elem_id="ranbooru_tab_natural", visible=True)', source)
        self.assertNotIn("Prompt RAG / Few-Shot", source)
        self.assertNotIn("服务地址兼容策略", source)
        self.assertNotIn("转换方式", source)

    def test_natural_language_cache_callbacks_are_live(self):
        source = ast.parse(
            (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")
        )
        script_class = next(
            node for node in source.body
            if isinstance(node, ast.ClassDef) and node.name == "Script"
        )
        methods = {
            node.name: node
            for node in script_class.body
            if isinstance(node, ast.FunctionDef)
        }
        for method_name in (
            "_save_natural_language_settings",
            "_cache_preview_natural_conversion",
            "_cache_batch_convert_natural",
            "_cache_clear_natural_conversion",
        ):
            with self.subTest(method=method_name):
                first = methods[method_name].body[0]
                if isinstance(first, ast.Return):
                    self.assertNotIn(
                        "自然语言转换与 RAG 已移除",
                        ast.unparse(first.value) if first.value else "",
                    )

    def test_cache_workspace_exposes_task_tabs_and_stable_ids(self):
        source = (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")

        self.assertIn('gr.Accordion(label=f"高级选项 · Ranbooru · {view_label}", open=False', source)
        self.assertIn('view_suffix = "_img2img" if is_img2img else ""', source)
        self.assertIn('elem_id=f"ranbooru_tag_prompt{view_suffix}"', source)
        self.assertNotIn('elem_id=f"ranbooru_prompt_batch_payload{view_suffix}"', source)
        self.assertNotIn("Ranbooru 在线生成设置", source)
        for label in ("缓存采集", "浏览与联动", "自然语言转换", "维护与导入导出"):
            self.assertIn(f'gr.Tab("{label}"', source)

        for elem_id in (
            "ranbooru_cache_workspace",
            "ranbooru_cache_tabs",
            "ranbooru_cache_status",
            "ranbooru_cache_current_prompt",
            "ranbooru_llm_handoff_status",
            "ranbooru_cache_delete_preview",
        ):
            self.assertIn(f'elem_id="{elem_id}"', source)


    def test_cache_workspace_is_collapsible_and_closed_by_default(self):
        source = ast.parse(
            (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")
        )
        workspace = next(
            node
            for node in ast.walk(source)
            if isinstance(node, ast.With)
            and node.items
            and isinstance(node.items[0].context_expr, ast.Call)
            and isinstance(node.items[0].context_expr.func, ast.Attribute)
            and node.items[0].context_expr.func.attr == "Accordion"
            and node.items[0].context_expr.args
            and isinstance(node.items[0].context_expr.args[0], ast.Constant)
            and node.items[0].context_expr.args[0].value == "本地缓存工作区"
        )
        keywords = {
            keyword.arg: keyword.value
            for keyword in workspace.items[0].context_expr.keywords
        }

        self.assertIs(keywords["open"].value, False)
        self.assertEqual(keywords["elem_id"].value, "ranbooru_cache_workspace")
        nested_tabs = {
            call.args[0].value
            for call in ast.walk(workspace)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "Tab"
            and call.args
            and isinstance(call.args[0], ast.Constant)
        }
        self.assertTrue(
            {"缓存采集", "浏览与联动", "自然语言转换", "维护与导入导出"}
            <= nested_tabs
        )

    def test_each_cache_tab_owns_its_workflow_controls(self):
        source = ast.parse(
            (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")
        )
        expected = {
            "缓存采集": {"cache_fetch_btn"},
            "浏览与联动": {"cache_next_btn", "cache_send_prompt_studio_btn"},
            "自然语言转换": {"cache_natural_language_convert_btn"},
            "维护与导入导出": {"cache_delete_btn", "cache_import_btn"},
        }
        found = {}
        for node in ast.walk(source):
            if not (
                isinstance(node, ast.With)
                and node.items
                and isinstance(node.items[0].context_expr, ast.Call)
            ):
                continue
            call = node.items[0].context_expr
            if not (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "Tab"
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and call.args[0].value in expected
            ):
                continue
            assigned = {
                target.id
                for child in ast.walk(node)
                if isinstance(child, ast.Assign)
                for target in child.targets
                if isinstance(target, ast.Name)
            }
            found[call.args[0].value] = assigned

        self.assertEqual(set(found), set(expected))
        for label, controls in expected.items():
            self.assertTrue(controls <= found[label], f"{label} 缺少 {controls - found[label]}")

    def test_extension_styles_cover_dense_rows_tables_and_mobile(self):
        css = (ROOT / "style.css").read_text(encoding="utf-8")

        self.assertIn("#ranbooru_cache_workspace", css)
        self.assertIn(".ranbooru-form-row", css)
        self.assertIn(".ranbooru-table", css)
        self.assertIn("@media (max-width: 900px)", css)

    def test_prompt_studio_actions_have_matching_output_contracts(self):
        source = (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")
        self.assertIn("cache_process_prompt_studio_btn.click(", source)
        self.assertIn("outputs=[cache_prompt_studio_result, cache_prompt_studio_status],", source)
        self.assertIn("cache_process_prompt_studio_write_btn.click(", source)
        self.assertIn("outputs=[cache_prompt_studio_result, cache_prompt_studio_status, tag_prompt_input],", source)
        self.assertIn("focusHandoff", source)


if __name__ == "__main__":
    unittest.main()
