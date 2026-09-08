import ast
import random
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SOURCE = (ROOT / "scripts" / "ranbooru.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _Booru:
    def __init__(self, booru, url):
        self.booru = booru
        self.base_url = url
        self.booru_url = url
        self.headers = {"user-agent": "test"}

    def _filter_tags_by_category(self, post, categories):
        if not categories:
            return post.get("tag_string", "")
        return " ".join(
            post.get(f"tag_string_{category}", "")
            for category in categories
        ).strip()


def _load_provider(name):
    selected = [
        node for node in TREE.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    namespace = {
        "Booru": _Booru,
        "POST_AMOUNT": 100,
        "USER_AGENT": "test",
        "random": random,
        "logger": mock.Mock(),
        "redact_sensitive": str,
        "_normalize_max_pages": lambda value: max(1, int(value)),
        "_response_posts_or_raise": _response_posts_or_raise,
        "_response_post_or_raise": _response_post_or_raise,
    }
    exec(  # noqa: S102 - isolates Forge-dependent provider classes
        compile(ast.Module(body=selected, type_ignores=[]), "ranbooru.py", "exec"),
        namespace,
    )
    return namespace[name]


def _response_posts_or_raise(response, source, keys=("posts", "post")):
    payload = response.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if payload.get("success") is False or payload.get("error"):
            raise RuntimeError(f"{source}: {payload.get('error') or 'API error'}")
        for key in keys:
            if isinstance(payload.get(key), list):
                return payload[key]
    raise RuntimeError(f"{source} invalid response")


def _response_post_or_raise(response, source):
    payload = response.json()
    if isinstance(payload, dict):
        if payload.get("success") is False or payload.get("error"):
            raise RuntimeError(f"{source}: {payload.get('error') or 'API error'}")
        post = payload.get("post", payload)
        if isinstance(post, dict):
            return post
    raise RuntimeError(f"{source} invalid response")


class ProviderRequestContractTests(unittest.TestCase):
    def test_all_provider_page_endpoints_normalize_posts(self):
        cases = [
            ("Gelbooru", {"id": 1, "tags": "one", "directory": "d", "image": "a.jpg"}, "pid=0", "one"),
            ("e621", {"id": 2, "tags": {"general": ["one"], "artist": ["artist"]}}, "page=1", "one artist"),
            ("XBooru", {"id": 3, "tags": "one", "directory": "d", "image": "b.jpg"}, "pid=0", "one"),
            ("Rule34", {"id": 4, "tags": "one"}, "pid=0", "one"),
            ("Safebooru", {"id": 5, "tag_string": "one", "large_file_url": "https://img.test/a.jpg"}, "page=1", "one"),
            ("Konachan", {"id": 6, "tags": "one"}, "page=0", "one"),
            ("Yandere", {"id": 7, "tags": "one"}, "page=0", "one"),
            ("AIBooru", {"id": 8, "tag_string": "one"}, "page=0", "one"),
            ("Danbooru", {"id": 9, "tag_string": "one"}, "page=1", "one"),
        ]

        for provider, post, page_marker, expected_tags in cases:
            with self.subTest(provider=provider):
                cls = _load_provider(provider)
                client = cls()
                payload = {"posts": [post]} if provider in {"e621", "Yandere"} else [post]
                client.fetch_with_retry = mock.Mock(return_value=_Response(payload))

                result = client.get_data_page("&tags=one%20two", page=0)

                self.assertEqual(len(result["post"]), 1)
                self.assertEqual(result["post"][0].get("tags"), expected_tags)
                requested_url = client.fetch_with_retry.call_args.args[0]
                self.assertIn(page_marker, requested_url)
                self.assertIn("tags=one%20two", requested_url)

    def test_single_post_endpoints_return_post_and_propagate_errors(self):
        cases = [
            ("e621", "https://e621.net/posts/12.json", {"post": {"id": 12, "tags": {"general": ["one"]}}}),
            ("Safebooru", "https://safebooru.donmai.us/posts/12.json", {"id": 12, "tag_string": "one"}),
            ("Danbooru", "https://danbooru.donmai.us/posts/12.json", {"id": 12, "tag_string": "one"}),
        ]

        for provider, expected_url, payload in cases:
            with self.subTest(provider=provider):
                cls = _load_provider(provider)
                client = cls()
                client.fetch_with_retry = mock.Mock(return_value=_Response(payload))

                result = client.get_post("", id="12")

                self.assertEqual(result["post"][0]["id"], 12)
                self.assertEqual(client.booru_url, expected_url)

                client.fetch_with_retry.return_value = _Response({
                    "success": False,
                    "error": "TagLimitError",
                    "message": "too many tags",
                })
                with self.assertRaisesRegex(RuntimeError, "TagLimitError"):
                    client.get_post("", id="12")

    def test_online_generation_starts_from_first_page_for_sparse_queries(self):
        cases = [
            ("Gelbooru", {"id": 1, "tags": "one"}, "pid=0"),
            ("e621", {"id": 2, "tags": {"general": ["one"]}}, "page=1"),
            ("XBooru", {"id": 3, "tags": "one"}, "pid=0"),
            ("Rule34", {"id": 4, "tags": "one"}, "pid=0"),
            ("Safebooru", {"id": 5, "tag_string": "one"}, "page=1"),
            ("Konachan", {"id": 6, "tags": "one"}, "page=1"),
            ("Yandere", {"id": 7, "tags": "one"}, "page=1"),
            ("AIBooru", {"id": 8, "tag_string": "one"}, "page=1"),
            ("Danbooru", {"id": 9, "tag_string": "one"}, "page=1"),
        ]

        for provider, post, page_marker in cases:
            with self.subTest(provider=provider):
                cls = _load_provider(provider)
                client = cls()
                payload = {"posts": [post]} if provider in {"e621", "Yandere"} else [post]
                client.fetch_with_retry = mock.Mock(return_value=_Response(payload))

                result = client.get_data("&tags=rare_tag", max_pages=100)

                self.assertEqual(len(result["post"]), 1)
                self.assertIn(page_marker, client.fetch_with_retry.call_args.args[0])
                client.fetch_with_retry.assert_called_once()


if __name__ == "__main__":
    unittest.main()
