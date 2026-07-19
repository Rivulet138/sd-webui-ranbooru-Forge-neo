import json
import socket
import unittest
from unittest.mock import patch

import requests

from scripts.natural_language import (
    BACKEND_OFF,
    BACKEND_OLLAMA,
    BACKEND_OPENAI,
    ENDPOINT_POLICY_ALLOW_PRIVATE,
    ENDPOINT_POLICY_PUBLIC_ONLY,
    ENDPOINT_POLICY_UNRESTRICTED,
    KREA2_SYSTEM_PROMPT,
    MAX_OUTPUT_CHARS,
    MAX_RESPONSE_BYTES,
    PRESET_KREA2,
    CachedTagNaturalLanguageConverter,
    NaturalLanguageConfig,
    NaturalLanguageConversionError,
    NaturalLanguageBatchCancelled,
    _PinnedAddressAdapter,
    convert_cached_tags_safely,
    is_timeout_conversion_error,
    is_retryable_conversion_error,
    iter_cached_tag_conversions,
)


class FakeResponse:
    def __init__(
        self,
        payload,
        status_code=200,
        json_error=None,
        http_error=None,
        body=None,
        headers=None,
    ):
        self.payload = payload
        self.status_code = status_code
        self.json_error = json_error
        self.http_error = http_error
        self.body = (
            json.dumps(payload).encode("utf-8")
            if body is None
            else body
        )
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        if self.http_error:
            raise self.http_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload

    def iter_content(self, chunk_size):
        if self.json_error:
            raise self.json_error
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, payload, post_error=None, response_kwargs=None):
        self.payload = payload
        self.post_error = post_error
        self.response_kwargs = response_kwargs or {}
        self.calls = []
        self.responses = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.post_error:
            raise self.post_error
        response = FakeResponse(self.payload, **self.response_kwargs)
        self.responses.append(response)
        return response

    def close(self):
        self.closed = True


class CachedTagNaturalLanguageConverterTests(unittest.TestCase):
    def test_default_timeout_is_120_seconds(self):
        self.assertEqual(NaturalLanguageConfig().timeout, 120.0)

    def test_owned_session_pins_a_validated_dns_address_and_disables_proxies(self):
        converter = CachedTagNaturalLanguageConverter(
            resolver=lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
            ]
        )
        try:
            base_url, host_header = converter._validated_transport(
                "https://model.example/v1",
                "https://model.example/v1",
                BACKEND_OPENAI,
                ENDPOINT_POLICY_PUBLIC_ONLY,
            )
            adapter = converter.session.get_adapter(base_url)
            self.assertIsInstance(adapter, _PinnedAddressAdapter)
            self.assertEqual(adapter.address, "93.184.216.34")
            self.assertEqual(host_header, "model.example")
            self.assertFalse(converter.session.trust_env)
            with self.assertRaises(NaturalLanguageConversionError):
                adapter.get_connection_with_tls_context(
                    None,
                    True,
                    proxies={"https": "http://proxy.example"},
                )
        finally:
            converter.close()

    def setUp(self):
        def fake_getaddrinfo(host, port, type=0):
            address = "::1" if host == "localhost" else "93.184.216.34"
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 6, "", (address, port))]

        self.dns_patch = patch(
            "scripts.natural_language.socket.getaddrinfo",
            side_effect=fake_getaddrinfo,
        )
        self.dns_patch.start()

    def tearDown(self):
        self.dns_patch.stop()

    def test_disabled_conversion_returns_original_without_http(self):
        session = FakeSession({})
        result = CachedTagNaturalLanguageConverter(session).convert(
            "1girl, blue_hair, outdoors",
            NaturalLanguageConfig(backend=BACKEND_OFF),
        )
        self.assertEqual(result, "1girl, blue_hair, outdoors")
        self.assertEqual(session.calls, [])

    def test_ollama_converts_the_complete_cache_record(self):
        session = FakeSession(
            {"message": {"content": "A blue-haired girl stands outside at sunset."}}
        )
        result = CachedTagNaturalLanguageConverter(session).convert(
            "1girl, blue_hair, outdoors, sunset",
            NaturalLanguageConfig(
                backend=BACKEND_OLLAMA,
                endpoint="http://127.0.0.1:11434",
                model="qwen3:8b",
                preset=PRESET_KREA2,
            ),
        )
        self.assertEqual(result, "A blue-haired girl stands outside at sunset.")
        url, request = session.calls[0]
        self.assertEqual(url, "http://127.0.0.1:11434/api/chat")
        user_prompt = request["json"]["messages"][1]["content"]
        self.assertIn("1girl, blue hair, outdoors, sunset", user_prompt)
        self.assertNotIn("tag_count", request["json"])
        self.assertFalse(request["allow_redirects"])
        self.assertTrue(request["stream"])
        self.assertTrue(session.responses[0].closed)

    def test_krea_preset_requests_concrete_structure_and_rejects_quality_filler(self):
        self.assertIn("medium or rendering form", KREA2_SYSTEM_PROMPT)
        self.assertIn("framing and composition", KREA2_SYSTEM_PROMPT)
        self.assertIn("time, weather, and lighting", KREA2_SYSTEM_PROMPT)
        self.assertIn("masterpiece", KREA2_SYSTEM_PROMPT)
        self.assertIn("Do not invent", KREA2_SYSTEM_PROMPT)

    def test_openai_compatible_uses_default_endpoint(self):
        session = FakeSession(
            {"choices": [{"message": {"content": "A quiet moonlit forest."}}]}
        )
        result = CachedTagNaturalLanguageConverter(session).convert(
            "forest, night, moonlight",
            NaturalLanguageConfig(backend=BACKEND_OPENAI, model="local-model"),
        )
        self.assertEqual(result, "A quiet moonlit forest.")
        self.assertEqual(
            session.calls[0][0],
            "https://api.openai.com/v1/chat/completions",
        )

    def test_ollama_api_base_is_not_duplicated(self):
        session = FakeSession({"message": {"content": "A portrait."}})
        CachedTagNaturalLanguageConverter(session).convert(
            "portrait",
            NaturalLanguageConfig(
                backend=BACKEND_OLLAMA,
                endpoint="http://127.0.0.1:11434/api",
                model="qwen3:8b",
            ),
        )
        self.assertEqual(session.calls[0][0], "http://127.0.0.1:11434/api/chat")

    def test_bearer_keys_are_supported(self):
        session = FakeSession(
            {"choices": [{"message": {"content": "Description: A portrait."}}]}
        )
        result = CachedTagNaturalLanguageConverter(session).convert(
            "portrait",
            NaturalLanguageConfig(
                backend=BACKEND_OPENAI,
                endpoint="https://example.test/v1/chat/completions",
                model="example-model",
                api_key="secret",
            ),
        )
        self.assertEqual(result, "A portrait.")
        self.assertEqual(
            session.calls[0][1]["headers"]["Authorization"],
            "Bearer secret",
        )

    def test_missing_model_is_clear(self):
        with self.assertRaisesRegex(NaturalLanguageConversionError, "模型名称"):
            CachedTagNaturalLanguageConverter(FakeSession({})).convert(
                "1girl",
                NaturalLanguageConfig(backend=BACKEND_OLLAMA),
            )

    def test_unsafe_endpoint_parts_are_rejected(self):
        converter = CachedTagNaturalLanguageConverter(FakeSession({}))
        with self.assertRaisesRegex(NaturalLanguageConversionError, "API Key"):
            converter.convert(
                "portrait",
                NaturalLanguageConfig(
                    backend=BACKEND_OPENAI,
                    endpoint="https://user:password@example.test/v1",
                    model="example-model",
                ),
            )
        with self.assertRaisesRegex(NaturalLanguageConversionError, "query"):
            converter.convert(
                "portrait",
                NaturalLanguageConfig(
                    backend=BACKEND_OPENAI,
                    endpoint="https://example.test/v1?tenant=unsafe",
                    model="example-model",
                ),
            )

    def test_default_policy_blocks_private_openai_and_dangerous_targets(self):
        blocked_endpoints = [
            "http://127.0.0.1:8080/v1",
            "http://10.0.0.8:8080/v1",
            "http://169.254.169.254/latest",
            "http://100.100.100.200/latest",
            "http://[::]:8080/v1",
            "http://[::ffff:169.254.169.254]/latest",
        ]
        for endpoint in blocked_endpoints:
            with self.subTest(endpoint=endpoint):
                session = FakeSession({})
                with self.assertRaisesRegex(
                    NaturalLanguageConversionError, "blocked by endpoint policy"
                ):
                    CachedTagNaturalLanguageConverter(session).convert(
                        "portrait",
                        NaturalLanguageConfig(
                            backend=BACKEND_OPENAI,
                            endpoint=endpoint,
                            model="example-model",
                        ),
                    )
                self.assertEqual(session.calls, [])

    def test_metadata_hostname_is_blocked_before_dns_or_http(self):
        session = FakeSession({})
        with self.assertRaisesRegex(
            NaturalLanguageConversionError, "metadata hostname"
        ):
            CachedTagNaturalLanguageConverter(session).convert(
                "portrait",
                NaturalLanguageConfig(
                    backend=BACKEND_OPENAI,
                    endpoint="http://metadata.google.internal/computeMetadata/v1",
                    model="example-model",
                    endpoint_policy=ENDPOINT_POLICY_ALLOW_PRIVATE,
                ),
            )
        self.assertEqual(session.calls, [])

    def test_parser_ambiguous_endpoint_is_rejected(self):
        session = FakeSession({})
        with self.assertRaisesRegex(
            NaturalLanguageConversionError, "backslashes or control"
        ):
            CachedTagNaturalLanguageConverter(session).convert(
                "portrait",
                NaturalLanguageConfig(
                    backend=BACKEND_OPENAI,
                    endpoint="https://example.test\\@127.0.0.1/v1",
                    model="example-model",
                ),
            )
        self.assertEqual(session.calls, [])

    def test_allow_private_policy_permits_lan_but_never_link_local(self):
        allowed = FakeSession(
            {"choices": [{"message": {"content": "A private endpoint result."}}]}
        )
        result = CachedTagNaturalLanguageConverter(allowed).convert(
            "portrait",
            NaturalLanguageConfig(
                backend=BACKEND_OPENAI,
                endpoint="http://192.168.1.20:8080/v1",
                model="local-model",
                endpoint_policy=ENDPOINT_POLICY_ALLOW_PRIVATE,
            ),
        )
        self.assertEqual(result, "A private endpoint result.")
        self.assertEqual(len(allowed.calls), 1)

        fake_ip = FakeSession(
            {"choices": [{"message": {"content": "A Fake-IP endpoint result."}}]}
        )
        fake_ip_result = CachedTagNaturalLanguageConverter(fake_ip).convert(
            "portrait",
            NaturalLanguageConfig(
                backend=BACKEND_OPENAI,
                endpoint="http://198.18.0.9:8080/v1",
                model="proxy-model",
                endpoint_policy=ENDPOINT_POLICY_ALLOW_PRIVATE,
            ),
        )
        self.assertEqual(fake_ip_result, "A Fake-IP endpoint result.")
        self.assertEqual(len(fake_ip.calls), 1)

        blocked = FakeSession({})
        with self.assertRaisesRegex(
            NaturalLanguageConversionError, "blocked by endpoint policy"
        ):
            CachedTagNaturalLanguageConverter(blocked).convert(
                "portrait",
                NaturalLanguageConfig(
                    backend=BACKEND_OPENAI,
                    endpoint="http://169.254.10.20/v1",
                    model="local-model",
                    endpoint_policy=ENDPOINT_POLICY_ALLOW_PRIVATE,
                ),
            )
        self.assertEqual(blocked.calls, [])

    def test_unrestricted_policy_skips_dns_pinning_and_uses_system_proxy_settings(self):
        def unexpected_resolver(*_args, **_kwargs):
            raise AssertionError("unrestricted mode must not resolve or pin the endpoint")

        converter = CachedTagNaturalLanguageConverter(resolver=unexpected_resolver)
        try:
            base_url, host_header = converter._validated_transport(
                "http://169.254.169.254/latest",
                "http://127.0.0.1",
                BACKEND_OPENAI,
                ENDPOINT_POLICY_UNRESTRICTED,
            )
            self.assertEqual(base_url, "http://169.254.169.254/latest")
            self.assertEqual(host_header, "")
            self.assertTrue(converter.session.trust_env)
        finally:
            converter.close()

    def test_public_only_policy_can_lock_out_local_ollama(self):
        session = FakeSession({})
        with self.assertRaisesRegex(
            NaturalLanguageConversionError, "blocked by endpoint policy"
        ):
            CachedTagNaturalLanguageConverter(session).convert(
                "portrait",
                NaturalLanguageConfig(
                    backend=BACKEND_OLLAMA,
                    model="qwen3:8b",
                    endpoint_policy=ENDPOINT_POLICY_PUBLIC_ONLY,
                ),
            )
        self.assertEqual(session.calls, [])

    def test_dns_must_resolve_and_every_answer_must_pass_policy(self):
        session = FakeSession({})

        def mixed_dns(host, port, type=0):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", port)),
            ]

        with self.assertRaisesRegex(
            NaturalLanguageConversionError, "10.0.0.2"
        ):
            CachedTagNaturalLanguageConverter(
                session, resolver=mixed_dns
            ).convert(
                "portrait",
                NaturalLanguageConfig(
                    backend=BACKEND_OPENAI,
                    endpoint="https://mixed.example/v1",
                    model="example-model",
                ),
            )
        self.assertEqual(session.calls, [])

        def failed_dns(host, port, type=0):
            raise socket.gaierror("not found")

        with self.assertRaisesRegex(
            NaturalLanguageConversionError, "Unable to resolve"
        ):
            CachedTagNaturalLanguageConverter(
                session, resolver=failed_dns
            ).convert(
                "portrait",
                NaturalLanguageConfig(
                    backend=BACKEND_OPENAI,
                    endpoint="https://missing.example/v1",
                    model="example-model",
                ),
            )
        self.assertEqual(session.calls, [])

    def test_timeout_falls_back_to_original(self):
        converter = CachedTagNaturalLanguageConverter(
            FakeSession({}, post_error=requests.Timeout("timed out"))
        )
        result, error = convert_cached_tags_safely(
            "1girl, blue_hair",
            NaturalLanguageConfig(backend=BACKEND_OLLAMA, model="qwen3:8b"),
            converter,
        )
        self.assertEqual(result, "1girl, blue_hair")
        self.assertIn("timed out", error)

    def test_bad_json_and_http_error_fall_back(self):
        bad_json = CachedTagNaturalLanguageConverter(
            FakeSession({}, response_kwargs={"json_error": ValueError("bad json")})
        )
        result, error = convert_cached_tags_safely(
            "portrait",
            NaturalLanguageConfig(backend=BACKEND_OLLAMA, model="qwen3:8b"),
            bad_json,
        )
        self.assertEqual(result, "portrait")
        self.assertIn("bad json", error)

        response = requests.Response()
        response.status_code = 401
        http_error = requests.HTTPError("401 unauthorized", response=response)
        bad_http = CachedTagNaturalLanguageConverter(
            FakeSession({}, response_kwargs={"http_error": http_error})
        )
        result, error = convert_cached_tags_safely(
            "portrait",
            NaturalLanguageConfig(backend=BACKEND_OPENAI, model="example-model"),
            bad_http,
        )
        self.assertEqual(result, "portrait")
        self.assertIn("401 unauthorized", error)

    def test_redirect_is_rejected_without_following(self):
        session = FakeSession({}, response_kwargs={"status_code": 302})
        result, error = convert_cached_tags_safely(
            "portrait",
            NaturalLanguageConfig(
                backend=BACKEND_OPENAI,
                endpoint="https://example.test/v1",
                model="example-model",
                api_key="secret",
            ),
            CachedTagNaturalLanguageConverter(session),
        )
        self.assertEqual(result, "portrait")
        self.assertIn("重定向", error)
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assertTrue(session.responses[0].closed)

    def test_response_body_is_bounded_and_closed(self):
        declared_large = FakeSession(
            {},
            response_kwargs={
                "headers": {"Content-Length": str(MAX_RESPONSE_BYTES + 1)}
            },
        )
        result, error = convert_cached_tags_safely(
            "portrait",
            NaturalLanguageConfig(backend=BACKEND_OLLAMA, model="qwen3:8b"),
            CachedTagNaturalLanguageConverter(declared_large),
        )
        self.assertEqual(result, "portrait")
        self.assertIn("byte limit", error)
        self.assertTrue(declared_large.responses[0].closed)

        streamed_large = FakeSession(
            {},
            response_kwargs={"body": b"x" * (MAX_RESPONSE_BYTES + 1)},
        )
        result, error = convert_cached_tags_safely(
            "portrait",
            NaturalLanguageConfig(backend=BACKEND_OLLAMA, model="qwen3:8b"),
            CachedTagNaturalLanguageConverter(streamed_large),
        )
        self.assertEqual(result, "portrait")
        self.assertIn("byte limit", error)
        self.assertTrue(streamed_large.responses[0].closed)

    def test_owned_session_is_closed_but_injected_session_is_reusable(self):
        owned_session = FakeSession(
            {"message": {"content": "A portrait."}}
        )
        with patch(
            "scripts.natural_language.requests.Session",
            return_value=owned_session,
        ):
            with CachedTagNaturalLanguageConverter() as converter:
                converter.convert(
                    "portrait",
                    NaturalLanguageConfig(
                        backend=BACKEND_OLLAMA,
                        model="qwen3:8b",
                    ),
                )
            self.assertTrue(owned_session.closed)

        injected_session = FakeSession({})
        converter = CachedTagNaturalLanguageConverter(injected_session)
        converter.close()
        self.assertFalse(injected_session.closed)

    def test_empty_and_oversized_outputs_are_handled(self):
        empty = CachedTagNaturalLanguageConverter(
            FakeSession({"message": {"content": "   "}})
        )
        result, error = convert_cached_tags_safely(
            "portrait",
            NaturalLanguageConfig(backend=BACKEND_OLLAMA, model="qwen3:8b"),
            empty,
        )
        self.assertEqual(result, "portrait")
        self.assertIn("空内容", error)

        long_output = CachedTagNaturalLanguageConverter(
            FakeSession({"message": {"content": "x" * (MAX_OUTPUT_CHARS + 50)}})
        )
        result = long_output.convert(
            "portrait",
            NaturalLanguageConfig(backend=BACKEND_OLLAMA, model="qwen3:8b"),
        )
        self.assertEqual(len(result), MAX_OUTPUT_CHARS)

    def test_iterator_stops_after_first_failure(self):
        class FailingConverter:
            def __init__(self):
                self.calls = 0

            def convert(self, tags, config):
                self.calls += 1
                raise NaturalLanguageConversionError("service unavailable")

        converter = FailingConverter()
        rows = list(
            iter_cached_tag_conversions(
                ["first", "second", "third"],
                NaturalLanguageConfig(backend=BACKEND_OLLAMA, model="qwen3:8b"),
                converter,
            )
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "first")
        self.assertIn("service unavailable", rows[0][3])
        self.assertEqual(converter.calls, 1)

    def test_iterator_retries_each_timeout_twice_and_then_continues(self):
        class AlwaysTimeoutConverter:
            def __init__(self):
                self.calls = 0

            def convert(self, tags, config):
                self.calls += 1
                raise NaturalLanguageConversionError(
                    "模型请求超时: upstream read timed out"
                )

        converter = AlwaysTimeoutConverter()
        rows = list(
            iter_cached_tag_conversions(
                ["first", "second"],
                NaturalLanguageConfig(backend=BACKEND_OLLAMA, model="qwen3:8b"),
                converter,
                retry_backoff_seconds=0,
            )
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(converter.calls, 6)
        self.assertTrue(all(is_timeout_conversion_error(row[3]) for row in rows))
        self.assertTrue(all("已自动重试 2 次" in row[3] for row in rows))

    def test_iterator_resets_after_a_transient_timeout_succeeds(self):
        class TransientTimeoutConverter:
            def __init__(self):
                self.calls = 0

            def convert(self, tags, config):
                self.calls += 1
                if self.calls <= 2:
                    raise NaturalLanguageConversionError(
                        "模型请求超时: transient timeout"
                    )
                return f"converted {tags}"

        converter = TransientTimeoutConverter()
        rows = list(
            iter_cached_tag_conversions(
                ["first", "second"],
                NaturalLanguageConfig(backend=BACKEND_OLLAMA, model="qwen3:8b"),
                converter,
                retry_backoff_seconds=0,
            )
        )
        self.assertEqual([row[2] for row in rows], ["converted first", "converted second"])
        self.assertEqual(converter.calls, 4)
        self.assertTrue(all(not row[3] for row in rows))

    def test_iterator_checks_cancellation_between_timeout_attempts(self):
        class TimeoutConverter:
            def __init__(self):
                self.calls = 0

            def convert(self, tags, config):
                self.calls += 1
                raise NaturalLanguageConversionError(
                    "模型请求超时: upstream read timed out"
                )

        converter = TimeoutConverter()
        with self.assertRaises(NaturalLanguageBatchCancelled):
            list(
                iter_cached_tag_conversions(
                    ["first", "second"],
                    NaturalLanguageConfig(
                        backend=BACKEND_OLLAMA,
                        model="qwen3:8b",
                    ),
                    converter,
                    should_cancel=lambda: converter.calls >= 1,
                    retry_backoff_seconds=0,
                )
            )
        self.assertEqual(converter.calls, 1)

    def test_retryable_http_failure_is_classified_for_skip_and_continue(self):
        self.assertTrue(is_retryable_conversion_error("模型服务返回 HTTP 429"))
        self.assertTrue(is_retryable_conversion_error("模型服务返回 HTTP 503"))
        self.assertFalse(is_retryable_conversion_error("模型返回了空内容"))

    def test_retryable_http_failure_retries_then_continues_to_next_record(self):
        class RetryableHttpConverter:
            def __init__(self):
                self.calls = []

            def convert(self, tags, config):
                self.calls.append(tags)
                if tags == "first":
                    raise NaturalLanguageConversionError(
                        "model service returned HTTP 503: unavailable"
                    )
                return f"converted {tags}"

        converter = RetryableHttpConverter()
        rows = list(
            iter_cached_tag_conversions(
                ["first", "second"],
                NaturalLanguageConfig(backend=BACKEND_OLLAMA, model="qwen3:8b"),
                converter,
                retry_backoff_seconds=0,
            )
        )
        self.assertEqual(len(rows), 2)
        self.assertIn("HTTP 503", rows[0][3])
        self.assertEqual(rows[1][2], "converted second")
        self.assertEqual(converter.calls, ["first", "first", "first", "second"])

    def test_iterator_reuses_identical_whole_record_conversion(self):
        class EchoConverter:
            def __init__(self):
                self.calls = 0

            def convert(self, tags, config):
                self.calls += 1
                return f"description {tags}"

        converter = EchoConverter()
        rows = list(
            iter_cached_tag_conversions(
                ["same, whole, record", "same, whole, record", "different"],
                NaturalLanguageConfig(backend=BACKEND_OLLAMA, model="qwen3:8b"),
                converter,
            )
        )
        self.assertEqual(
            [row[2] for row in rows],
            [
                "description same, whole, record",
                "description same, whole, record",
                "description different",
            ],
        )
        self.assertEqual(converter.calls, 2)
        self.assertFalse(rows[0][4])
        self.assertTrue(rows[1][4])


if __name__ == "__main__":
    unittest.main()
