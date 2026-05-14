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
#
# The pattern intentionally has NO leading word-boundary (\b) anchor so that
# it matches "key" as a substring in camelCase/snake_case/kebab-case identifiers
# like "apikey", "api_key", "signing_key", "client_key", "x-api-key".
# Adding \b would fix false positives on natural-language words (monkey, turkey)
# but would miss all of the above critical cases because underscore and lowercase
# letters adjacent to "key" do not form a word boundary.
#
# Instead, an explicit allowlist (_KEY_BENIGN_EXACT_NAMES) guards against
# the natural-language false-positive words that could plausibly appear as log
# field names (none have appeared in this codebase; the list exists for defence
# in depth).
_KEY_BENIGN_EXACT_NAMES: frozenset[str] = frozenset(
    {
        "monkey",
        "turkey",
        "donkey",
        "hockey",
        "jockey",
        # Add other benign names here if a false-positive is found in practice.
    }
)

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

# 64+ consecutive hex chars (sha256 digest / 32-byte signing key as hex).
# Use hex-char-class lookaround instead of \b: \b is a word-boundary that fires
# between \w and \W, but hex chars are all \w, so \b would NOT block a run of
# 128 hex chars from matching the first 64 (or any 64-char slice within it).
# The lookaround approach (?<![0-9a-fA-F])…(?![0-9a-fA-F]) detects hex-digit
# adjacency directly and matches the ENTIRE run of hex digits (length ≥ 64),
# redacting any concatenated or URL-embedded key material as a unit.
_HEX64_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64,}(?![0-9a-fA-F])")
_REDACTED_HEX64 = "[REDACTED-HEX64]"

# 43+ consecutive base64url chars (api key / bearer token material).
# The character class [A-Za-z0-9_-] is base64url alphabet; length 43 = ceil(256/6).
# Same lookaround rationale as above — base64url chars include letters/digits/
# underscore/hyphen, so \b would not reliably delimit a run of base64url chars
# that is embedded inside a longer base64url string.  The lookaround on the
# base64url char class itself ensures the ENTIRE adjacent run is captured.
_B64URL43_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43,}(?![A-Za-z0-9_-])")
_REDACTED_B64URL43 = "[REDACTED-B64URL43]"

# DSN / connection-URL userinfo scrubbing.  Matches any scheme://user[:pass]@host
# pattern and replaces the userinfo with ***.  Applied before hex/base64 passes
# so that passwords containing high-entropy hex/base64 chars are still removed.
_DSN_USERINFO_RE = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+\-.]*\+?[A-Za-z0-9]*)://"
    r"(?:[^/@\s:]+(?::[^/@\s]*)?@)"
    r"(?P<host>[^/?#\s]+)"
)

_REDACTED = "[REDACTED]"


def _should_redact(key: str) -> bool:
    """Return True if the key name matches any redaction rule.

    Rules (checked in order):
    1. Exact match against ``_EXACT_KEYS`` (lowercased).
    2. Exact match against ``_KEY_BENIGN_EXACT_NAMES`` → NOT redacted (allowlist).
    3. Lowercased name starts with any prefix in ``_REDACT_PREFIXES``.
    4. Lowercased name matches any pattern in ``_REDACT_PATTERNS``.
    """
    k = key.lower()
    if k in _EXACT_KEYS:
        return True
    if k in _KEY_BENIGN_EXACT_NAMES:
        return False
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
    s = _DSN_USERINFO_RE.sub(r"\g<scheme>://***@\g<host>", s)
    s = _HEX64_RE.sub(_REDACTED_HEX64, s)
    s = _B64URL43_RE.sub(_REDACTED_B64URL43, s)
    return s


def _redact_bytes_value(value: bytes | bytearray) -> Any:
    """Redact or summarise a bytes/bytearray log value.

    Strategy (in order):
    1. Take the lowercase hex representation of the bytes.
    2. If that hex string matches the high-entropy hex64 pattern (64+ hex
       chars), the raw bytes are signing-key-shaped and must be redacted.
    3. Otherwise return a length-only summary ``<bytes len=N>`` so arbitrary
       binary blobs are never logged verbatim.

    We never try to decode arbitrary bytes as UTF-8 — invalid sequences would
    raise ``UnicodeDecodeError`` and might mask the original log event.
    """
    n = len(value)
    hex_repr = value.hex()  # pure hex, always valid, no decoding needed
    if _HEX64_RE.fullmatch(hex_repr):
        return _REDACTED
    # Not signing-key-shaped, but still never log raw bytes verbatim.
    return f"<bytes len={n}>"


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
    if isinstance(value, (bytes, bytearray)):
        return _redact_bytes_value(value)
    return value


def _do_redact(
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Core redaction logic extracted for safety-net wrapping."""
    result: structlog.types.EventDict = {}
    for key, value in event_dict.items():
        if _should_redact(str(key)):
            result[key] = _REDACTED
        else:
            result[key] = _redact_value(value)
    return result


def redact_sensitive_keys(
    logger: structlog.types.WrappedLogger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """structlog processor: redact sensitive keys from *event_dict*.

    Walks the top-level event dict and any nested dicts/lists, replacing the
    *values* of sensitive keys with ``"[REDACTED]"``.

    This processor is designed to be the **first** in the chain.

    If the redaction logic itself raises an unexpected exception (e.g. due to
    a pathological event dict), the event is not silently dropped.  Instead a
    safe fallback dict is returned so the log chain can continue and operators
    can diagnose the failure.
    """
    try:
        return _do_redact(event_dict)
    except Exception as exc:  # noqa: BLE001
        return {
            "event": "[redaction_failed]",
            "original_event": str(event_dict.get("event", ""))[:64],
            "redaction_error_class": type(exc).__name__,
        }
