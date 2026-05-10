"""structlog processor that strips sensitive keys from every event dict.

This processor MUST be placed first in the structlog processor chain so that
redaction runs before any other processor can serialize the event.

Redacted key patterns (case-insensitive, matched against the **key name**):
  - x-api-key, authorization, cookie
  - recotem_signing_key, recotem_signing_keys, recotem_api_keys
  - any key whose name contains: secret, password, passwd, token, key,
    auth, bearer, cred, private
  - any key whose lowercased name starts with: aws_, gcp_, google_, azure_

Value-side scrubbing (applied to string values of non-redacted keys):
  - 64-hex-char substrings (sha256 / signing key hex): replaced with
    ``[REDACTED-HEX64]``
  - 43-char base64url substrings (api key material): replaced with
    ``[REDACTED-B64URL43]``
"""

from __future__ import annotations

import re
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# Key-name redaction
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

# Prefix-based check: lowercased key names starting with these are redacted.
_REDACT_PREFIXES: tuple[str, ...] = (
    "aws_",
    "gcp_",
    "google_",
    "azure_",
)

# Substring-based check: if any of these substrings appears in the lowercased
# key name, the key is redacted.  Aligned with envvars.py _BLACKLIST_SUBSTRINGS
# (case-folded to lower).
#
# Note on "key": the pattern uses a negative lookahead to avoid redacting the
# plural "keys" (a common benign field name in structured logs for lists of
# items) while still catching "apikey", "_key", "key_id", etc.
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"secret"),
    re.compile(r"password"),
    re.compile(r"passwd"),
    re.compile(r"token"),
    # "key" but not "keys" (plural) — avoids false-positive on list fields
    # named "keys" (e.g. {"keys": [{"x-api-key": "..."}]}).
    re.compile(r"key(?!s\b)"),
    re.compile(r"auth"),
    re.compile(r"bearer"),
    re.compile(r"cred"),
    re.compile(r"private"),
)

# ---------------------------------------------------------------------------
# Value-side high-entropy pattern scrubbing
# ---------------------------------------------------------------------------

# 64 consecutive hex chars (sha256 digest / 32-byte signing key as hex).
_HEX64_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
_REDACTED_HEX64 = "[REDACTED-HEX64]"

# 43 consecutive base64url chars (api key / bearer token material).
# The character class [A-Za-z0-9_-] is base64url alphabet; length 43 = ceil(256/6).
_B64URL43_RE = re.compile(r"\b[A-Za-z0-9_-]{43}\b")
_REDACTED_B64URL43 = "[REDACTED-B64URL43]"

_REDACTED = "[REDACTED]"


def _should_redact(key: str) -> bool:
    """Return True if the key name matches any redaction rule.

    Rules (checked in order):
    1. Exact match against ``_EXACT_KEYS`` (lowercased).
    2. Lowercased name starts with any prefix in ``_REDACT_PREFIXES``.
    3. Lowercased name matches any pattern in ``_REDACT_PATTERNS``.
    """
    k = key.lower()
    if k in _EXACT_KEYS:
        return True
    if any(k.startswith(p) for p in _REDACT_PREFIXES):
        return True
    return any(p.search(k) for p in _REDACT_PATTERNS)


def _scrub_string_value(s: str) -> str:
    """Replace high-entropy substrings in a string value.

    Applied to string values of keys that were *not* fully redacted by name.
    Patterns:
    - 64 hex chars → ``[REDACTED-HEX64]``
    - 43 base64url chars → ``[REDACTED-B64URL43]``

    Already-redacted placeholder values are returned unchanged.
    """
    if s == _REDACTED or s.startswith("[REDACTED"):
        return s
    s = _HEX64_RE.sub(_REDACTED_HEX64, s)
    s = _B64URL43_RE.sub(_REDACTED_B64URL43, s)
    return s


def _redact_value(value: Any) -> Any:
    """Recursively walk dicts/lists and redact matched keys; scrub string values."""
    if isinstance(value, dict):
        return {
            k: _REDACTED if _should_redact(str(k)) else _redact_value(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _scrub_string_value(value)
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
