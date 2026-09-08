import ast
from pathlib import Path
import random
import re
import unittest
from unittest import mock
from types import SimpleNamespace
from urllib.parse import parse_qs, quote_plus, urlsplit

from scripts.booru_pipeline import (
    BooruRequestConfig,
    CredentialInput,
    PromptTransformConfig,
    generate_online_prompt,
)


ROOT = Path(__file__).parents[1]


def _load_functions(*names):
    tree = ast.parse((ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8"))
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
    ]
    namespace = {"random": random}
    exec(  # noqa: S102 - isolates Forge-dependent definitions for unit testing
        compile(ast.Module(body=selected, type_ignores=[]), "ranbooru.py", "exec"),
        namespace,
    )
    return namespace


class _Response:
    def __init__(self, posts):
        self._posts = posts

    def json(self):
        return self._posts


class ProviderFallbackContractTests(unittest.TestCase):
    def test_safebooru_query_does_not_consume_a_search_tag_for_animated_filter(self):
        namespace = _load_functions(
            "_build_booru_search_suffix",
            "_split_query_tags",
            "_get_rating_tag",
        )
        namespace.update({
            "re": re,
            "quote_plus": quote_plus,
            "RATINGS": {"safebooru": {"All": "All"}},
            "SAFEBOORU_MAX_QUERY_TAGS": 2,
        })

        suffix = namespace["_build_booru_search_suffix"]("safebooru", "1girl,solo", "All")
        query = parse_qs(urlsplit(f"https://example.test/posts?limit=100&page=1{suffix}").query)

        self.assertEqual(query["tags"], ["1girl solo"])

    def test_safebooru_rejects_more_than_two_search_tags_before_request(self):
        namespace = _load_functions(
            "_build_booru_search_suffix",
            "_split_query_tags",
            "_get_rating_tag",
        )
        namespace.update({
            "re": re,
            "quote_plus": quote_plus,
            "RATINGS": {"safebooru": {"All": "All"}},
            "SAFEBOORU_MAX_QUERY_TAGS": 2,
        })

        with self.assertRaisesRegex(ValueError, "最多允许 2 个搜索 tag"):
            namespace["_build_booru_search_suffix"]("safebooru", "1girl,solo,blue_eyes", "All")

    def test_api_error_payload_keeps_provider_message(self):
        namespace = _load_functions("_response_posts_or_raise")

        with self.assertRaisesRegex(RuntimeError, "PostQuery::TagLimitError"):
            namespace["_response_posts_or_raise"](
                _Response({
                    "success": False,
                    "error": "PostQuery::TagLimitError",
                    "message": "You cannot search for more than 2 tags at a time.",
                }),
                "Safebooru",
            )

    def test_single_post_error_payload_is_not_silently_treated_as_empty(self):
        namespace = _load_functions("_response_post_or_raise")

        with self.assertRaisesRegex(RuntimeError, "PostQuery::TagLimitError"):
            namespace["_response_post_or_raise"](
                _Response({
                    "success": False,
                    "error": "PostQuery::TagLimitError",
                    "message": "too many tags",
                }),
                "Safebooru",
            )

    def test_e621_category_filter_accepts_tuple_from_ui(self):
        namespace = _load_functions()
        source = (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        e621_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "e621"
        )
        namespace.update({
            "Booru": type("Booru", (), {}),
        })
        exec(  # noqa: S102 - isolates the provider from Forge imports
            compile(ast.Module(body=[e621_class], type_ignores=[]), "ranbooru.py", "exec"),
            namespace,
        )
        client = object.__new__(namespace["e621"])
        post = {"tags": {"general": ["one"], "artist": ["artist_name"]}}

        self.assertEqual(client._filter_tags_by_category(post, ("artist",)), "artist_name")
        self.assertEqual(post["_ranbooru_match_tags"], "one artist_name")

    def test_shared_pipeline_adapter_forwards_rule34_fallback_opt_in(self):
        namespace = _load_functions(
            "_BooruPipelineClient",
            "_pipeline_request_tags",
            "_adapt_pipeline_client",
            "_build_booru_search_suffix",
            "_split_query_tags",
            "_get_rating_tag",
        )
        namespace.update({
            "re": re,
            "quote_plus": quote_plus,
            "RATINGS": {"rule34": {"All": "All"}},
            "SAFEBOORU_MAX_QUERY_TAGS": 2,
        })
        namespace["_get_rating_tag"] = lambda *_args: ""
        request = SimpleNamespace(service="rule34", tags="one", mature_rating="g")

        enabled = namespace["_adapt_pipeline_client"](mock.Mock(), request, True)
        disabled = namespace["_adapt_pipeline_client"](mock.Mock(), request, False)

        self.assertTrue(enabled.allow_animated_fallback)
        self.assertFalse(disabled.allow_animated_fallback)
        source = (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")
        self.assertIn("lambda pipeline_request, _credential: _adapt_pipeline_client(", source)

    def test_selecting_rule34_resets_shared_fallback_setting_to_off(self):
        namespace = _load_functions("show_fringe_benefits")
        namespace["gr"] = mock.Mock()

        namespace["show_fringe_benefits"]("rule34")

        namespace["gr"].update.assert_called_once_with(visible=True, value=False)

    def test_xbooru_valid_first_result_does_not_request_again(self):
        namespace = _load_functions("_normalize_max_pages", "_response_posts_or_raise")
        namespace.update({
            "POST_AMOUNT": 100,
            "logger": mock.Mock(),
            "redact_sensitive": str,
        })

        class FakeBooru:
            def __init__(self, booru, url):
                self.base_url = url

        namespace["Booru"] = FakeBooru
        exec(  # noqa: S102 - isolates the provider from Forge imports
            compile(ast.Module(body=[
                next(node for node in ast.parse(
                    (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")
                ).body if isinstance(node, ast.ClassDef) and node.name == "XBooru")
            ], type_ignores=[]), "ranbooru.py", "exec"),
            namespace,
        )
        client = namespace["XBooru"]()
        client.fetch_with_retry = mock.Mock(return_value=_Response([
            {"id": 1, "tags": "one", "directory": "d", "image": "i.jpg"},
        ]))

        result = client.get_data("&tags=test", max_pages=10)

        self.assertEqual(len(result["post"]), 1)
        client.fetch_with_retry.assert_called_once()

    def test_safebooru_online_fetch_uses_first_page_for_sparse_queries(self):
        namespace = _load_functions("_normalize_max_pages", "_response_posts_or_raise")
        namespace.update({
            "POST_AMOUNT": 100,
            "logger": mock.Mock(),
            "redact_sensitive": str,
        })

        class FakeBooru:
            def __init__(self, booru, url):
                self.base_url = url
                self.headers = {"user-agent": "test"}

            def _filter_tags_by_category(self, post, categories):
                return post.get("tag_string", "")

        namespace["Booru"] = FakeBooru
        source = (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        safebooru = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Safebooru")
        exec(  # noqa: S102 - isolates the Forge-dependent provider for unit testing
            compile(ast.Module(body=[safebooru], type_ignores=[]), "ranbooru.py", "exec"),
            namespace,
        )
        client = namespace["Safebooru"]()
        client.fetch_with_retry = mock.Mock(return_value=_Response([
            {"id": 1, "tag_string": "rare_tag"},
        ]))

        result = client.get_data("&tags=rare_tag", max_pages=100)

        self.assertEqual(len(result["post"]), 1)
        requested_url = client.fetch_with_retry.call_args.args[0]
        self.assertIn("page=1", requested_url)
        client.fetch_with_retry.assert_called_once()

    def test_rule34_animated_fallback_requires_opt_in_and_reports_evidence(self):
        namespace = _load_functions(
            "_fetch_booru_posts_with_fallback",
            "_build_booru_search_suffix",
            "_split_query_tags",
            "_get_rating_tag",
        )
        namespace.update({
            "_fetch_booru_data": lambda *args, **kwargs: {"post": []},
            "_normalize_posts": lambda posts: list(posts),
            "_get_rating_tag": lambda *args: "",
            "re": re,
            "quote_plus": quote_plus,
            "SAFEBOORU_MAX_QUERY_TAGS": 2,
        })
        function = namespace["_fetch_booru_posts_with_fallback"]
        api = mock.Mock()
        api.get_data.return_value = {"post": [{"tags": "animated"}]}
        evidence = {}

        posts = function(api, "rule34", "&tags=-animated+one", 2, fallback_evidence=evidence)

        self.assertEqual(posts, [])
        api.get_data.assert_not_called()
        self.assertEqual(evidence["status"], "empty_without_fallback")

        posts = function(
            api,
            "rule34",
            "&tags=-animated+one",
            2,
            plain_tags="one",
            allow_animated_fallback=True,
            fallback_evidence=evidence,
        )

        self.assertEqual(posts, [{"tags": "animated"}])
        api.get_data.assert_called_once_with("&tags=one", 2)
        self.assertEqual(evidence["status"], "fallback_succeeded")
        self.assertEqual(evidence["initial_post_count"], 0)
        self.assertEqual(evidence["fallback_post_count"], 1)

        api.get_data.side_effect = RuntimeError("fallback request failed")
        with self.assertRaisesRegex(RuntimeError, "fallback request failed"):
            function(
                api,
                "rule34",
                "&tags=-animated+one",
                2,
                plain_tags="one",
                allow_animated_fallback=True,
                fallback_evidence=evidence,
            )
        self.assertEqual(evidence["status"], "fallback_failed")

    def test_pipeline_result_exposes_fetch_evidence(self):
        class Client:
            fetch_evidence = {
                "status": "fallback_succeeded",
                "initial_post_count": 0,
                "fallback_post_count": 1,
            }

            def fetch(self):
                return [{"id": 1, "tags": "one two"}]

            def close(self):
                return None

        result = generate_online_prompt(
            BooruRequestConfig(service="rule34"),
            PromptTransformConfig(),
            CredentialInput(service="rule34"),
            lambda request, credential: Client(),
        )

        self.assertEqual(result.fetch_status, "fallback_succeeded")
        self.assertEqual(result.fetch_evidence["initial_post_count"], 0)


if __name__ == "__main__":
    unittest.main()
