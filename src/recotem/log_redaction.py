"""structlog processor that strips sensitive keys from every event dict.

This processor MUST be placed first in the structlog processor chain so that
redaction runs before any other processor can serialize the event.

Redacted key patterns (case-insensitive, matched against the **key name**
not the value):
  - x-api-key, authorization, cookie
  - recotem_signing_key, recotem_signing_keys, recotem_api_keys
  - *_secret*, *_password*, *_token*, *_key*
  - aws_*, gcp_*, google_*, azure_*
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# Redaction patterns
# ---------------------------------------------------------------------------

# Exact key names (lowercased) to always redact.
_EXACT_KEYS: frozenset[str] = frozenset(
    {
        "x-api-key",
        "authorization",
        "cookie",
        "recotem_signing_key",
        "recotem_signing_keys",
        "recotem_api_keys",
    }
)

# Glob-style patterns matched against the lowercased key.
# Aligned with src/recotem/recipe/envvars.py blacklist (case-folded to lower).
_GLOB_PATTERNS: tuple[str, ...] = (
    "*_secret*",
    "*_password*",
    "*_token*",
    "*_key*",
    "aws_*",
    "gcp_*",
    "google_*",
    "azure_*",
)

_COMPILED_GLOBS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(fnmatch.translate(p), re.IGNORECASE) for p in _GLOB_PATTERNS
)

_REDACTED = "[REDACTED]"


def _should_redact(key: str) -> bool:
    """Return True if the key name matches any redaction rule."""
    k = key.lower()
    if k in _EXACT_KEYS:
        return True
    return any(p.match(k) for p in _COMPILED_GLOBS)


def _redact_value(value: Any) -> Any:
    """Recursively walk dicts/lists and redact matched keys."""
    if isinstance(value, dict):
        return {
            k: _REDACTED if _should_redact(str(k)) else _redact_value(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def redact_sensitive_keys(
    logger: structlog.types.WrappedLogger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """structlog processor: redact sensitive keys from *event_dict*.

    Walks the top-level event dict and any nested dicts/lists, replacing the
    *values* of sensitive keys with ``"[REDACTED]"``.

    This processor is designed to be the **first** in the chain.
    """
    result: structlog.types.EventDict = {}
    for key, value in event_dict.items():
        if _should_redact(str(key)):
            result[key] = _REDACTED
        else:
            result[key] = _redact_value(value)
    return result
