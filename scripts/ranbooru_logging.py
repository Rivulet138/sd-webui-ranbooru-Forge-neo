"""Logging helpers with centralized credential redaction for Ranbooru."""

import logging
import re
from collections.abc import Mapping


REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "token",
    "user_id",
    "userid",
    "x-api-key",
}
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|user[_-]?id|password|token)"
    r"(\s*[=:]\s*)(?!\[REDACTED\])([^&\s,;]+)"
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)(\bauthorization\s*[:=]\s*)(bearer\s+)?(?!\[REDACTED\])([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(
    r"(?i)(\bbearer\s+)(?!\[REDACTED\])([^\s,;]+)"
)


EVENT_LEVELS = {
    "network_retry": logging.WARNING,
    "network_timeout": logging.WARNING,
    "cache_mutation_failure": logging.ERROR,
    "image_failure": logging.ERROR,
    "credential_read_failure": logging.ERROR,
}


def get_logger(component=None):
    name = "ranbooru"
    if component:
        name = f"{name}.{str(component).strip('.')}"
    return logging.getLogger(name)


def _is_sensitive_key(key):
    normalized = str(key).strip().lower().replace("-", "_")
    return (
        normalized in {item.replace("-", "_") for item in _SENSITIVE_KEYS}
        or normalized.endswith(("_api_key", "_password", "_token", "_user_id"))
    )


def _redact_text(value):
    value = _AUTHORIZATION_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2) or ''}{REDACTED}",
        str(value),
    )
    value = _BEARER_PATTERN.sub(rf"\1{REDACTED}", value)
    return _KEY_VALUE_PATTERN.sub(rf"\1\2{REDACTED}", value)


def redact_sensitive(value):
    if isinstance(value, Mapping):
        return {
            key: REDACTED
            if _is_sensitive_key(key)
            else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, set):
        return {redact_sensitive(item) for item in value}
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, BaseException):
        return _redact_text(value)
    return value


def log_event(logger, event, message, *args, **kwargs):
    try:
        level = EVENT_LEVELS[event]
    except KeyError as error:
        raise ValueError(f"Unknown logging event: {event}") from error
    logger.log(level, message, *args, **kwargs)
