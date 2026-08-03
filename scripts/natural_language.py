"""Convert cached booru tags into natural-language image descriptions."""

from dataclasses import dataclass
import ipaddress
import json
import os
import re
import socket
import sys
import time
from urllib.parse import urlparse

import requests

try:
    from .ranbooru_logging import get_logger, redact_sensitive
    from .version import NATURAL_PROMPT_CONVERTER_VERSION
except ImportError:
    _scripts_dir = os.path.dirname(os.path.abspath(__file__))
    _added_scripts_dir = _scripts_dir not in sys.path
    if _added_scripts_dir:
        sys.path.insert(0, _scripts_dir)
    try:
        from ranbooru_logging import get_logger, redact_sensitive
        from version import NATURAL_PROMPT_CONVERTER_VERSION
    finally:
        if _added_scripts_dir:
            sys.path.remove(_scripts_dir)


logger = get_logger("natural_language")


BACKEND_OFF = "不转换"
BACKEND_OLLAMA = "Ollama（本地）"
BACKEND_OPENAI = "OpenAI 兼容 LLM"
BACKEND_CHOICES = [BACKEND_OFF, BACKEND_OLLAMA, BACKEND_OPENAI]

DEFAULT_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_OPENAI_ENDPOINT = "https://api.openai.com/v1"
MAX_OUTPUT_CHARS = 4000
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_FEW_SHOT_EXAMPLES = 5
MAX_FEW_SHOT_CHARS = 12000
DEFAULT_FEW_SHOT_CHARS = 3000
ENDPOINT_POLICY_DEFAULT = "default"
ENDPOINT_POLICY_PUBLIC_ONLY = "public_only"
ENDPOINT_POLICY_ALLOW_PRIVATE = "allow_private"
ENDPOINT_POLICY_UNRESTRICTED = "unrestricted"
ENDPOINT_POLICY_CHOICES = [
    ENDPOINT_POLICY_UNRESTRICTED,
    ENDPOINT_POLICY_ALLOW_PRIVATE,
    ENDPOINT_POLICY_DEFAULT,
    ENDPOINT_POLICY_PUBLIC_ONLY,
]
PRESET_KREA2 = "Krea 2｜紧凑自然语言（推荐）"
PRESET_CHOICES = [PRESET_KREA2]
CONVERTER_VERSION = NATURAL_PROMPT_CONVERTER_VERSION
KREA2_SYSTEM_PROMPT = (
    "Rewrite the complete booru tag set as one tight English prompt for Krea 2. "
    "Follow this visual order when the source tags provide the information: medium or "
    "rendering form; subject count and identity; appearance and clothing; action or pose; "
    "setting and important objects; framing and composition; time, weather, and lighting; "
    "palette, texture, and one explicit style anchor. Use concrete visual language in one "
    "coherent sentence or short paragraph. Preserve every meaningful visual fact and "
    "relationship, including names, counts, colors, and camera terms. Convert underscores "
    "to natural spacing. Do not invent missing details, add commentary, censor or soften "
    "the source, or return a tag list. Omit generic AI-quality filler such as masterpiece, "
    "best quality, beautiful, stunning, 8k, score labels, and source labels. If several "
    "style or artist tags conflict, keep only the first explicit anchor. Return only the "
    "finished prompt with no heading, Markdown, negative prompt, or explanation."
)
DEFAULT_SYSTEM_PROMPT = KREA2_SYSTEM_PROMPT


class NaturalLanguageConversionError(RuntimeError):
    """Raised when a configured tag conversion cannot produce usable text."""


class NaturalLanguageBatchCancelled(RuntimeError):
    """Raised when a batch conversion is cancelled between HTTP attempts."""


@dataclass(frozen=True)
class NaturalLanguageConfig:
    backend: str = BACKEND_OFF
    endpoint: str = ""
    model: str = ""
    api_key: str = ""
    timeout: float = 120.0
    preset: str = PRESET_KREA2
    system_prompt: str = ""
    endpoint_policy: str = ENDPOINT_POLICY_DEFAULT
    few_shot_max_chars: int = DEFAULT_FEW_SHOT_CHARS


def split_cached_tags(tags):
    """Split a cached comma-separated prompt while preserving tag order."""
    return [part.strip() for part in str(tags or "").split(",") if part.strip()]


def _normalize_timeout(value):
    try:
        return min(300.0, max(1.0, float(value or 120.0)))
    except (TypeError, ValueError):
        return 120.0


_BLOCKED_METADATA_HOSTS = frozenset(
    {
        "instance-data",
        "instance-data.ec2.internal",
        "metadata",
        "metadata.aws.internal",
        "metadata.google.internal",
        "metadata.azure.internal",
        "metadata.packet.net",
    }
)
_BLOCKED_METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("168.63.129.16"),
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.170.2"),
        ipaddress.ip_address("192.0.0.192"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)


def _normalize_endpoint_policy(value):
    policy = str(value or ENDPOINT_POLICY_DEFAULT).strip().lower()
    if policy not in ENDPOINT_POLICY_CHOICES:
        raise NaturalLanguageConversionError(
            f"Unsupported endpoint policy: {value!r}"
        )
    return policy


def _address_is_allowed(address, backend, policy):
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return _address_is_allowed(address.ipv4_mapped, backend, policy)
        if address.sixtofour is not None or address.teredo is not None:
            return False
    if address in _BLOCKED_METADATA_IPS:
        return False
    if (
        address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    if address.is_loopback:
        return policy == ENDPOINT_POLICY_ALLOW_PRIVATE or (
            policy == ENDPOINT_POLICY_DEFAULT and backend == BACKEND_OLLAMA
        )
    if address.is_private:
        return policy == ENDPOINT_POLICY_ALLOW_PRIVATE
    return address.is_global


def _resolve_endpoint_addresses(hostname, port, resolver):
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            records = resolver(hostname, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise NaturalLanguageConversionError(
                f"Unable to resolve model endpoint host {hostname!r}: {error}"
            ) from error

        addresses = set()
        for record in records:
            try:
                addresses.add(ipaddress.ip_address(record[4][0].split("%", 1)[0]))
            except (IndexError, TypeError, ValueError) as error:
                raise NaturalLanguageConversionError(
                    f"DNS returned an invalid address for model endpoint {hostname!r}"
                ) from error
        if not addresses:
            raise NaturalLanguageConversionError(
                f"Model endpoint host {hostname!r} resolved to no addresses"
            )
        return addresses
    return {literal}


def _validate_endpoint_details(
    endpoint,
    default,
    backend,
    policy=ENDPOINT_POLICY_DEFAULT,
    resolver=socket.getaddrinfo,
):
    normalized = str(endpoint or default).strip().rstrip("/")
    if "\\" in normalized or any(ord(character) < 32 for character in normalized):
        raise NaturalLanguageConversionError(
            "Model endpoint cannot contain backslashes or control characters"
        )
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise NaturalLanguageConversionError(
            "服务地址必须是完整的 http:// 或 https:// URL"
        )
    if parsed.username or parsed.password:
        raise NaturalLanguageConversionError(
            "请不要把用户名或密码写在服务地址中；鉴权请使用 API Key"
        )
    if parsed.query or parsed.fragment:
        raise NaturalLanguageConversionError("服务地址不能包含 query 参数或 URL fragment")
    hostname = parsed.hostname
    if not hostname or "%" in hostname:
        raise NaturalLanguageConversionError("Model endpoint has an invalid hostname")
    hostname = hostname.rstrip(".").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (UnicodeError, ValueError) as error:
        raise NaturalLanguageConversionError(
            f"Model endpoint has an invalid hostname or port: {error}"
        ) from error
    if not 1 <= port <= 65535:
        raise NaturalLanguageConversionError("Model endpoint port must be 1-65535")
    policy = _normalize_endpoint_policy(policy)
    if policy != ENDPOINT_POLICY_UNRESTRICTED and hostname in _BLOCKED_METADATA_HOSTS:
        raise NaturalLanguageConversionError(
            "Model endpoint targets a blocked cloud metadata hostname"
        )
    canonical_host = f"[{hostname}]" if ":" in hostname else hostname
    canonical_netloc = (
        f"{canonical_host}:{parsed.port}"
        if parsed.port is not None
        else canonical_host
    )
    normalized = parsed._replace(netloc=canonical_netloc).geturl().rstrip("/")

    if policy == ENDPOINT_POLICY_UNRESTRICTED:
        return normalized, hostname, port, ()

    addresses = _resolve_endpoint_addresses(hostname, port, resolver)
    blocked = [
        str(address)
        for address in addresses
        if not _address_is_allowed(address, backend, policy)
    ]
    if blocked:
        raise NaturalLanguageConversionError(
            "Model endpoint resolves to a network address blocked by endpoint policy: "
            + ", ".join(sorted(blocked))
        )
    return normalized, hostname, port, tuple(
        sorted(addresses, key=lambda address: (address.version, int(address)))
    )


class _PinnedAddressAdapter(requests.adapters.HTTPAdapter):
    """Connect to one validated DNS result while preserving Host/SNI validation."""

    def __init__(self, hostname, address, port):
        self.hostname = str(hostname)
        self.address = str(address)
        self.port = int(port)
        super().__init__()

    def get_connection_with_tls_context(
        self,
        request,
        verify,
        proxies=None,
        cert=None,
    ):
        if proxies:
            raise NaturalLanguageConversionError(
                "Proxy routing is disabled for model endpoints so address validation cannot be bypassed"
            )
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(
            request,
            verify,
            cert,
        )
        host_params["host"] = self.address
        host_params["port"] = self.port
        if host_params.get("scheme") == "https":
            pool_kwargs["assert_hostname"] = self.hostname
            pool_kwargs["server_hostname"] = self.hostname
        return self.poolmanager.connection_from_host(
            **host_params,
            pool_kwargs=pool_kwargs,
        )


def _read_json_response(response):
    content_length = str(
        getattr(response, "headers", {}).get("Content-Length", "")
    ).strip()
    if content_length:
        try:
            too_large = int(content_length) > MAX_RESPONSE_BYTES
        except ValueError:
            too_large = False
        if too_large:
            raise NaturalLanguageConversionError(
                f"Model response exceeds the {MAX_RESPONSE_BYTES}-byte limit"
            )

    body = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise NaturalLanguageConversionError(
                f"Model response exceeds the {MAX_RESPONSE_BYTES}-byte limit"
            )
    return json.loads(body.decode("utf-8"))


def _response_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text_parts = []
        for item in value:
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
                text_parts.append(str(item.get("text") or ""))
        return " ".join(part for part in text_parts if part)
    return ""


def _clean_model_output(value):
    text = _response_text(value).strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:text|markdown)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"^\s*(?:description|prompt)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip().strip("\"'")
    if not text:
        raise NaturalLanguageConversionError("模型返回了空内容")
    return text[:MAX_OUTPUT_CHARS].rstrip()


def _reject_redirect_response(response):
    status_code = int(getattr(response, "status_code", 200) or 200)
    if 300 <= status_code < 400:
        raise NaturalLanguageConversionError(
            "模型服务返回了重定向；为避免把 Tag 或 API Key 发往其他地址，已拒绝跟随"
        )


def convert_cached_tags_safely(tags, config, converter=None, examples=None):
    """Convert one cached prompt and fall back to its original tags on failure."""
    original = str(tags or "").strip()
    if not original or config.backend == BACKEND_OFF:
        return original, ""
    owns_converter = converter is None
    converter = converter or CachedTagNaturalLanguageConverter()
    try:
        if examples:
            return converter.convert(original, config, examples=examples), ""
        return converter.convert(original, config), ""
    except NaturalLanguageConversionError as error:
        return original, str(error)
    except Exception as error:
        # Injected converter implementations are an external compatibility boundary.
        logger.error("Unexpected conversion failure: %s", redact_sensitive(error))
        return original, f"未预期的转换错误: {error}"
    finally:
        if owns_converter:
            converter.close()


def is_timeout_conversion_error(error):
    message = str(error or "").strip().lower()
    return (
        message.startswith("模型请求超时:")
        or "read timed out" in message
        or "timed out" in message
        or "timeout" in message
    )


def is_retryable_conversion_error(error):
    """Return whether retrying a failed request is likely to be useful."""
    if is_timeout_conversion_error(error):
        return True
    message = str(error or "").strip().lower()
    if re.search(r"\bhttp\s+(408|425|429|500|502|503|504)\b", message):
        return True
    return any(
        marker in message
        for marker in (
            "connection reset",
            "connection aborted",
            "connection refused",
            "remote end closed connection",
            "temporarily unavailable",
        )
    )


def iter_cached_tag_conversions(
    tags_list,
    config,
    converter=None,
    timeout_retries=2,
    should_cancel=None,
    retry_backoff_seconds=0.5,
    sleep_fn=time.sleep,
    example_provider=None,
):
    """Yield whole-record conversions, retrying transient failures per record."""
    originals = [str(tags or "").strip() for tags in tags_list]
    owns_converter = converter is None
    converter = converter or CachedTagNaturalLanguageConverter()
    converted_by_original = {}
    timeout_retries = max(0, int(timeout_retries or 0))
    retry_backoff_seconds = max(0.0, float(retry_backoff_seconds or 0.0))

    def raise_if_cancelled():
        if should_cancel is not None and should_cancel():
            raise NaturalLanguageBatchCancelled("batch conversion cancelled")

    try:
        for index, original in enumerate(originals):
            raise_if_cancelled()
            if not original:
                yield index, original, "", "", False
                continue
            if original in converted_by_original:
                yield index, original, converted_by_original[original], "", True
                continue

            examples = ()
            if example_provider is not None:
                try:
                    examples = tuple(example_provider(original) or ())
                except Exception as error:
                    logger.warning(
                        "Prompt RAG retrieval failed; continuing without examples: %s",
                        redact_sensitive(error),
                    )

            for attempt in range(timeout_retries + 1):
                raise_if_cancelled()
                prepared, error = convert_cached_tags_safely(
                    original,
                    config,
                    converter,
                    examples,
                )
                if not error or not is_retryable_conversion_error(error):
                    break
                if attempt >= timeout_retries:
                    break
                raise_if_cancelled()
                if retry_backoff_seconds:
                    sleep_fn(retry_backoff_seconds * (2**attempt))
                raise_if_cancelled()
            if error:
                if is_retryable_conversion_error(error):
                    yield (
                        index,
                        original,
                        "",
                        f"{error}（已自动重试 {timeout_retries} 次）",
                        False,
                    )
                    continue
                yield index, original, "", error, False
                return
            converted_by_original[original] = prepared
            yield index, original, prepared, "", False
    finally:
        if owns_converter:
            converter.close()


class CachedTagNaturalLanguageConverter:
    """HTTP client for Ollama and OpenAI-compatible chat completion servers."""

    def __init__(self, session=None, resolver=None):
        self._owns_session = session is None
        self.session = session or requests.Session()
        if self._owns_session:
            self.session.trust_env = False
        self.resolver = resolver or socket.getaddrinfo

    def close(self):
        if self._owns_session and self.session is not None:
            self.session.close()
            self.session = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _validated_transport(
        self,
        endpoint,
        default,
        backend,
        endpoint_policy,
    ):
        base_url, hostname, port, addresses = _validate_endpoint_details(
            endpoint,
            default,
            backend,
            endpoint_policy,
            self.resolver,
        )
        policy = _normalize_endpoint_policy(endpoint_policy)
        if self._owns_session:
            self.session.trust_env = policy == ENDPOINT_POLICY_UNRESTRICTED
        if not addresses:
            return base_url, ""
        if not self._owns_session or not hasattr(self.session, "mount"):
            return base_url, ""

        parsed = urlparse(base_url)
        adapter = _PinnedAddressAdapter(hostname, addresses[0], port)
        default_port = 443 if parsed.scheme == "https" else 80
        host_header = f"[{hostname}]" if ":" in hostname else hostname
        if port != default_port:
            host_header = f"{host_header}:{port}"
        self.session.mount(f"{parsed.scheme}://{parsed.netloc}/", adapter)
        return base_url, host_header

    def convert(self, tags, config, examples=None):
        if config.backend == BACKEND_OFF:
            return str(tags or "").strip()

        tokens = split_cached_tags(tags)
        if not tokens:
            return ""

        return self._request_description(tokens, config, examples=examples)

    @staticmethod
    def _few_shot_messages(examples, max_chars=DEFAULT_FEW_SHOT_CHARS):
        try:
            max_chars = int(float(max_chars))
        except (TypeError, ValueError, OverflowError):
            max_chars = DEFAULT_FEW_SHOT_CHARS
        max_chars = max(0, min(MAX_FEW_SHOT_CHARS, max_chars))
        messages = []
        used_chars = 0
        for example in list(examples or ())[:MAX_FEW_SHOT_EXAMPLES]:
            if not isinstance(example, dict):
                continue
            example_tags = split_cached_tags(
                example.get("tags_prompt") or example.get("tags")
            )
            natural_prompt = re.sub(
                r"\s+",
                " ",
                str(example.get("natural_prompt") or "").strip(),
            )[:MAX_OUTPUT_CHARS].rstrip()
            if not example_tags or not natural_prompt:
                continue
            user_content = "Tags:\n" + ", ".join(
                tag.replace("_", " ") for tag in example_tags
            )
            example_chars = len(user_content) + len(natural_prompt)
            if used_chars + example_chars > max_chars:
                continue
            messages.extend(
                [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": natural_prompt},
                ]
            )
            used_chars += example_chars
        return messages

    def _request_description(self, tags, config, examples=None):
        model = str(config.model or "").strip()
        if not model:
            raise NaturalLanguageConversionError("请填写要使用的模型名称")

        few_shot_messages = self._few_shot_messages(
            examples,
            max_chars=config.few_shot_max_chars,
        )
        system_prompt = str(
            config.system_prompt
            or (
                KREA2_SYSTEM_PROMPT
                if config.preset == PRESET_KREA2
                else DEFAULT_SYSTEM_PROMPT
            )
        ).strip()
        if few_shot_messages:
            system_prompt += (
                " Use the following retrieved conversions only as formatting examples. "
                "Do not copy any visual fact that is absent from the final Tags message."
            )
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
        ]
        messages.extend(few_shot_messages)
        messages.append(
            {
                "role": "user",
                "content": "Tags:\n" + ", ".join(tag.replace("_", " ") for tag in tags),
            }
        )
        timeout = _normalize_timeout(config.timeout)

        try:
            if config.backend == BACKEND_OLLAMA:
                return self._request_ollama(
                    messages,
                    config.endpoint,
                    model,
                    config.api_key,
                    timeout,
                    config.endpoint_policy,
                )
            if config.backend == BACKEND_OPENAI:
                return self._request_openai_compatible(
                    messages,
                    config.endpoint,
                    model,
                    config.api_key,
                    timeout,
                    config.endpoint_policy,
                )
        except requests.Timeout as error:
            raise NaturalLanguageConversionError(f"模型请求超时: {error}") from error
        except requests.HTTPError as error:
            status_code = getattr(getattr(error, "response", None), "status_code", "unknown")
            raise NaturalLanguageConversionError(
                f"妯″瀷鏈嶅姟杩斿洖 HTTP {status_code}: {error}"
            ) from error
        except requests.RequestException as error:
            raise NaturalLanguageConversionError(f"模型请求失败: {error}") from error
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise NaturalLanguageConversionError(f"无法解析模型响应: {error}") from error

        raise NaturalLanguageConversionError(f"不支持的转换后端: {config.backend}")

    def _request_ollama(
        self,
        messages,
        endpoint,
        model,
        api_key,
        timeout,
        endpoint_policy,
    ):
        base_url, host_header = self._validated_transport(
            endpoint,
            DEFAULT_OLLAMA_ENDPOINT,
            BACKEND_OLLAMA,
            endpoint_policy,
        )
        if base_url.endswith("/api/chat"):
            url = base_url
        elif base_url.endswith("/api"):
            url = f"{base_url}/chat"
        else:
            url = f"{base_url}/api/chat"
        headers = {}
        if host_header:
            headers["Host"] = host_header
        if str(api_key or "").strip():
            headers["Authorization"] = f"Bearer {str(api_key).strip()}"
        response = self.session.post(
            url,
            headers=headers,
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 512},
            },
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        try:
            _reject_redirect_response(response)
            response.raise_for_status()
            payload = _read_json_response(response)
        finally:
            response.close()
        return _clean_model_output(payload["message"]["content"])

    def _request_openai_compatible(
        self,
        messages,
        endpoint,
        model,
        api_key,
        timeout,
        endpoint_policy,
    ):
        base_url, host_header = self._validated_transport(
            endpoint,
            DEFAULT_OPENAI_ENDPOINT,
            BACKEND_OPENAI,
            endpoint_policy,
        )
        url = (
            base_url
            if base_url.endswith("/chat/completions")
            else f"{base_url}/chat/completions"
        )
        headers = {"Content-Type": "application/json"}
        if host_header:
            headers["Host"] = host_header
        if str(api_key or "").strip():
            headers["Authorization"] = f"Bearer {str(api_key).strip()}"
        response = self.session.post(
            url,
            headers=headers,
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 512,
            },
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        try:
            _reject_redirect_response(response)
            response.raise_for_status()
            payload = _read_json_response(response)
        finally:
            response.close()
        return _clean_model_output(payload["choices"][0]["message"]["content"])
