"""structlog processor that strips sensitive keys from every event dict.

This processor MUST be placed first in the structlog processor chain so that
redaction runs before any other processor can serialize the event.

Redacted key patterns (case-insensitive, matched against the **key name**):
  - x-api-key, authorization, cookie
  - recotem_signing_key, recotem_signing_keys, recotem_api_keys
  - user_features, item_features (feature-aware iALS cold-start request
    attributes -- PII by construction, e.g. age_band, country. Defense in
    depth only: callers must never pass a feature dict to a logger.)
  - any key whose name contains: secret, password, passwd, token, key,
    auth, bearer, cred, private
  - any key whose lowercased name starts with: aws_, gcp_, google_, azure_

Value-side scrubbing (applied to string values of non-redacted keys):
  - 64-hex-char substrings (sha256 / signing key hex): replaced with
    ``[REDACTED-HEX64]``
  - 43-char base64url substrings (api key material): replaced with
    ``[REDACTED-B64URL43]``, *except* runs that are shaped like a
    human-authored identifier (see ``_is_identifier_shaped``) — those are
    recotem's own event / metric names, doc anchors, filenames and object
    keys, not credentials.
  - URL-shaped strings containing ``scheme://user[:pass]@host``: the userinfo
    is replaced with ``***`` (any SQL DSN or HTTP basic-auth URL embedded in a
    string value is scrubbed before the hex/base64 passes run)

Value-side scrubbing is skipped for the handful of fields in
``_NON_SECRET_DIGEST_KEYS`` whose value is a *published* digest (see below).
"""

from __future__ import annotations

import re
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# Key-name redaction
# ---------------------------------------------------------------------------

# Exact key names (lowercased) to always redact.
#
# user_features / item_features: feature-aware iALS cold-start requests carry
# raw request-supplied attributes (e.g. age_band, country) that are PII by
# construction. This is defense in depth, NOT the primary control -- the
# primary rule is that caller code must never pass a feature dict to a
# logger in the first place (log column names and counts instead). This key
# match is a mechanical backstop for if one does anyway.
_EXACT_KEYS: frozenset[str] = frozenset(
    {
        "x-api-key",
        "authorization",
        "cookie",
        "recotem_signing_key",
        "recotem_signing_keys",
        "recotem_api_keys",
        "user_features",
        "item_features",
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
# Instead, an explicit allowlist (_BENIGN_EXACT_NAMES) guards against the
# false-positive field names that the substring rules would otherwise eat.
#
# Entries are matched **exactly** (after lowercasing), never as substrings, so
# allowlisting "auth_enabled" does not also allowlist "auth_enabled_key".
# Value-side scrubbing still runs on allowlisted keys (see ``_do_redact``), so
# an allowlisted field that somehow carried key-shaped material would still be
# caught by the hex64 / base64url / DSN passes.  Only add names whose value is
# non-secret *by construction* -- a computed bool, or a status from a closed
# set -- never a name whose value is caller-supplied.
_BENIGN_EXACT_NAMES: frozenset[str] = frozenset(
    {
        # Natural-language words that contain "key" (none have appeared in this
        # codebase; these exist for defence in depth).
        "monkey",
        "turkey",
        "donkey",
        "hockey",
        "jockey",
        # security.posture fields (serving/app.py).  These are the two fields
        # the event exists to convey, and both are non-secret by construction:
        #   auth_enabled       -- computed bool
        #   signing_key_status -- one of configured / missing /
        #                         dev_allow_unsigned / construction_failed
        # Without these entries, r"auth" eats the first and r"key(?!s\b)" eats
        # the second, leaving a SIEM unable to answer "is auth on?" or "did the
        # key ring build?".  Note the sibling "signing_keys" field (kid +
        # fingerprint metadata) already survives via the plural lookahead.
        "auth_enabled",
        "signing_key_status",
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

# Exemption for the base64url pass: a human-readable identifier is not
# credential material.  Recotem's naming convention routinely produces such
# identifiers at or past the 43-char threshold -- structlog event names
# ("sql_statement_timeout_unsupported_on_sqlite", 43;
# "source_registry_unavailable_during_validation", 45;
# "recipe_yaml_parse_failed_on_rescan_new_file", 43), Prometheus metric names
# ("recotem_v1_validation_errors_outside_verb_total", 47), the doc anchors
# embedded in remedy links ("recotem-train-exits-4-with-feature_axis_error",
# 45), and operator-supplied names such as file stems, S3/GCS object keys and
# business IDs -- and redacting them destroys the only information the log
# line carries.
#
# Why exempt by *value shape* rather than by key name: the ``event`` field is
# not reliably an event-name literal.  ``recotem.logging.configure_logging``
# installs a ``foreign_pre_chain`` that routes stdlib loggers (uvicorn,
# SQLAlchemy, urllib3, ``py.warnings``) through this processor, and those emit
# interpolated messages into ``event`` (observed: "Uvicorn running on
# http://...").  That is precisely the text most likely to carry an
# accidentally interpolated DSN or key, so ``event`` must keep its value-side
# scrubbing.  Narrowing the pattern fixes the false positive wherever the
# identifier appears, without giving up any coverage.
#
# The shape test has three parts, all of which must hold:
#
#   1. The run splits on ``-`` / ``_`` into **two or more non-empty segments**.
#      A separator-free run is never exempt, so a 43-char single-case blob is
#      still redacted.  (``.`` needs no handling: it is outside the base64url
#      alphabet, so a dotted identifier is already split into sub-threshold
#      runs by ``_B64URL43_RE`` itself.)
#   2. Every segment is **internally case-consistent**: all-lowercase/digits,
#      all-uppercase/digits, or a single leading capital followed by
#      lowercase/digits.  Random base64url flips case at every character.
#   3. Across the whole run, ``min(uppercase_count, lowercase_count) <= 2`` --
#      the run is essentially single-case, allowing at most two outliers (a
#      capitalised leading word, an "-EU-" style tag).  This is what keeps a
#      Title-Case-Hyphenated *secret* redacted: it is case-consistent per
#      segment but has an uppercase letter in every word.
#
# Safety of the exemption: API keys are generated by ``recotem keygen`` from
# ``os.urandom(32)`` rendered as 43 base64url characters over the full 64-char
# alphabet.  An exact enumeration over that distribution puts the probability
# that a random key satisfies all three conditions at **1.1e-9** (about 1 in
# 940 million), against 2.6e-10 for the previous lowercase-snake_case-only
# rule -- the same order of magnitude, and still far below the probability
# that any real deployment logs enough raw keys to hit it.  Raising the length
# threshold was rejected as an alternative: it only moves the boundary, and a
# 43-char key is exactly the shape ``keygen`` emits.
#
# Known residual gap: a >=43-char run whose segments are camelCase (mixed case
# *inside* a segment) still fails condition 2 and is redacted.  Filenames,
# object keys and doc anchors -- the shapes actually observed being destroyed
# -- are not camelCase, and admitting camelCase would raise the miss
# probability by two orders of magnitude (to ~1.2e-7).
_IDENT_SEGMENT_RE = re.compile(r"[a-z0-9]+|[A-Z0-9]+|[A-Z][a-z0-9]*")
_IDENT_SEPARATOR_RE = re.compile(r"[-_]")

# Maximum number of "case outliers" (see condition 3 above) an exempt run may
# carry.  Two admits a capitalised first word plus one more; three would start
# admitting Title-Case-Hyphenated secrets.
_MAX_CASE_OUTLIERS = 2


def _is_identifier_shaped(run: str) -> bool:
    """Return True if *run* looks like a human-authored identifier, not a key.

    See the block comment above for the three conditions and the measured
    false-negative probability.
    """
    segments = _IDENT_SEPARATOR_RE.split(run)
    if len(segments) < 2:
        return False
    if not all(_IDENT_SEGMENT_RE.fullmatch(seg) for seg in segments):
        return False
    n_upper = sum(1 for c in run if "A" <= c <= "Z")
    n_lower = sum(1 for c in run if "a" <= c <= "z")
    return min(n_upper, n_lower) <= _MAX_CASE_OUTLIERS


def _b64url_replacement(match: re.Match[str]) -> str:
    """Redact a base64url-shaped run unless it is an identifier."""
    run = match.group(0)
    if _is_identifier_shaped(run):
        return run
    return _REDACTED_B64URL43


# ---------------------------------------------------------------------------
# Published-digest fields exempt from value-side scrubbing
# ---------------------------------------------------------------------------

# A few fields carry a SHA-256 digest that recotem publishes in the clear
# everywhere else, so redacting it in the log removes the only handle an
# operator has for correlating a running model with the config and bytes that
# produced it:
#
#   recipe_hash    SHA-256 of the canonical recipe YAML.  Computed from config
#                  only, before any data fetch; printed in full by
#                  ``recotem inspect`` and carried in the artifact header.
#   model_version  ``sha256:<digest>`` of the artifact.  Returned in every
#                  recommend response body and in the
#                  ``X-Recotem-Model-Version`` response header.
#
# Neither is derived from a credential, and ``_HEX64_RE`` -- which exists to
# catch a raw 32-byte signing key rendered as hex -- cannot tell them apart
# from one.  The exemption is therefore made as narrow as possible: it applies
# only when the field name is an exact match AND the value is *nothing but*
# the digest, in the exact lowercase form ``hexdigest()`` produces (optionally
# with the ``sha256:`` prefix ``model_version`` uses).  A field of the same
# name holding anything else -- a signing key, a digest with trailing text, an
# uppercase hex blob -- falls through to the normal passes.
_NON_SECRET_DIGEST_KEYS: frozenset[str] = frozenset(
    {
        "recipe_hash",
        "model_version",
    }
)

_DIGEST_VALUE_RE = re.compile(r"(?:sha256:)?[0-9a-f]{64}")


def _is_published_digest(key: str, value: Any) -> bool:
    """Return True if *key* / *value* is one of the published digest fields."""
    return (
        key.lower() in _NON_SECRET_DIGEST_KEYS
        and isinstance(value, str)
        and _DIGEST_VALUE_RE.fullmatch(value) is not None
    )


# DSN / connection-URL userinfo scrubbing.  Matches *credential-bearing*
# scheme://user[:pass]@host patterns and replaces the userinfo with ***.
#
# Schemes are explicitly enumerated rather than accepting "any scheme" because
# several object-store URI shapes idiomatically use ``@`` for non-credential
# purposes (e.g. ``gs://<bucket>@<project>/<key>`` for gcsfs, ``s3://...`` with
# vendor-specific extensions).  Rewriting those would silently delete useful
# information from operator logs without protecting any actual secret.
#
# The user part is ``*`` (not ``+``) so URLs of the form ``scheme://:pass@host``
# — a valid RFC 3986 / SQLAlchemy form with an empty username — are also
# scrubbed, instead of leaving the password visible.
#
# Applied before hex/base64 passes so that passwords containing high-entropy
# hex/base64 chars are still removed.
_DSN_USERINFO_RE = re.compile(
    r"(?P<scheme>"
    # HTTP family
    r"https?|ftps?"
    # SQL drivers (with optional SQLAlchemy +driver suffix)
    r"|postgresql(?:\+[A-Za-z0-9_]+)?|postgres(?:\+[A-Za-z0-9_]+)?"
    r"|mysql(?:\+[A-Za-z0-9_]+)?|mariadb(?:\+[A-Za-z0-9_]+)?"
    r"|mssql(?:\+[A-Za-z0-9_]+)?|oracle(?:\+[A-Za-z0-9_]+)?"
    # Other credential-bearing protocols
    r"|mongodb(?:\+srv)?|redis|rediss|amqp|amqps"
    r")"
    r"://"
    r"(?:[^/@\s:]*(?::[^/@\s]*)?@)"
    r"(?P<host>[^/?#\s]+)"
)

_REDACTED = "[REDACTED]"


def _should_redact(key: str) -> bool:
    """Return True if the key name matches any redaction rule.

    Rules (checked in order):
    1. Exact match against ``_EXACT_KEYS`` (lowercased).
    2. Exact match against ``_BENIGN_EXACT_NAMES`` → NOT redacted (allowlist).
    3. Lowercased name starts with any prefix in ``_REDACT_PREFIXES``.
    4. Lowercased name matches any pattern in ``_REDACT_PATTERNS``.
    """
    k = key.lower()
    if k in _EXACT_KEYS:
        return True
    if k in _BENIGN_EXACT_NAMES:
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
    if "://" in s:
        s = _DSN_USERINFO_RE.sub(r"\g<scheme>://***@\g<host>", s)
    s = _HEX64_RE.sub(_REDACTED_HEX64, s)
    s = _B64URL43_RE.sub(_b64url_replacement, s)
    return s


def redact_text(s: str) -> str:
    """Scrub high-entropy substrings out of a free-text string.

    The value-side half of the processor, exposed for the two sinks that write
    text **outside** the structlog chain and therefore never see
    ``redact_sensitive_keys``:

    - ``cli.py``'s ``_exit``, which prints an exception message to stderr with
      ``typer.echo``; and
    - the ``exception`` field that ``structlog.processors.format_exc_info``
      materialises *after* redaction has already run.

    An exception message is attacker-independent but not secret-independent: a
    config error quotes the malformed value back, and for
    ``RECOTEM_SIGNING_KEYS`` that value *is* the signing key.  Any code path
    that renders an exception to a human or to a log sink must send it through
    here first.

    Idempotent: already-redacted placeholders are returned unchanged.
    """
    return _scrub_string_value(s)


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


def _redact_field(key: Any, value: Any) -> Any:
    """Apply the per-field decision: full redaction, exemption, or scrubbing.

    Shared by the top-level walk and the nested-dict branch of
    ``_redact_value`` so a field behaves identically wherever it appears.
    """
    name = str(key)
    if _should_redact(name):
        return _REDACTED
    if _is_published_digest(name, value):
        return value
    return _redact_value(value)


def _redact_value(value: Any) -> Any:
    """Recursively walk dicts/lists/tuples and redact matched keys; scrub strings.

    Tuples are recursed into and returned as tuples — without this branch a
    log call like ``logger.info("evt", coords=("user", "pass"))`` would leave
    the tuple's contents untouched even if a contained string matched a
    high-entropy pattern.  ``set`` / ``frozenset`` are also covered for
    defence in depth.
    """
    if isinstance(value, dict):
        return {k: _redact_field(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, set | frozenset):
        # ``set`` cannot hold unhashable elements; ``_redact_value`` only ever
        # returns hashables when given hashables (str/bytes/numbers).  Return
        # the same container type so downstream rendering is unchanged.
        scrubbed = {_redact_value(item) for item in value}
        return frozenset(scrubbed) if isinstance(value, frozenset) else scrubbed
    if isinstance(value, str):
        return _scrub_string_value(value)
    if isinstance(value, bytes | bytearray):
        return _redact_bytes_value(value)
    return value


def _do_redact(
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Core redaction logic extracted for safety-net wrapping."""
    result: structlog.types.EventDict = {}
    for key, value in event_dict.items():
        result[key] = _redact_field(key, value)
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
