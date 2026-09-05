"""Unit tests for recotem.log_redaction.

Tests:
- Strips API/signing keys
- Strips AWS/Google creds
- Handles nested dicts/lists
- Must be first-in-chain (processor signature)
"""

from __future__ import annotations

import pytest

from recotem.log_redaction import _should_redact, redact_sensitive_keys

_REDACTED = "[REDACTED]"


def _invoke(event_dict: dict) -> dict:
    """Invoke the redact processor with dummy logger and method_name."""
    return redact_sensitive_keys(None, "info", event_dict)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Direct key redaction
# ---------------------------------------------------------------------------


def test_redact_x_api_key() -> None:
    result = _invoke({"x-api-key": "my_secret_api_key", "event": "request"})
    assert result["x-api-key"] == _REDACTED
    assert result["event"] == "request"


def test_redact_recotem_signing_keys() -> None:
    result = _invoke({"recotem_signing_keys": "kid:aabbcc", "event": "startup"})
    assert result["recotem_signing_keys"] == _REDACTED


def test_redact_recotem_api_keys() -> None:
    result = _invoke({"recotem_api_keys": "kid:sha256:abc", "event": "e"})
    assert result["recotem_api_keys"] == _REDACTED


def test_redact_authorization_header() -> None:
    result = _invoke({"authorization": "Bearer token123", "event": "auth"})
    assert result["authorization"] == _REDACTED


def test_redact_cookie() -> None:
    result = _invoke({"cookie": "session=abc123", "event": "req"})
    assert result["cookie"] == _REDACTED


# ---------------------------------------------------------------------------
# AWS / Google / GCP credentials
# ---------------------------------------------------------------------------


def test_redact_aws_prefix() -> None:
    result = _invoke({"aws_secret_access_key": "AKIASECRET", "event": "e"})
    assert result["aws_secret_access_key"] == _REDACTED


def test_redact_aws_access_key_id() -> None:
    result = _invoke({"aws_access_key_id": "AKIAIOSFODNN7EXAMPLE", "event": "e"})
    assert result["aws_access_key_id"] == _REDACTED


def test_redact_google_credentials() -> None:
    result = _invoke(
        {"google_application_credentials": "/path/to/creds.json", "event": "e"}
    )
    assert result["google_application_credentials"] == _REDACTED


def test_redact_gcp_project() -> None:
    result = _invoke({"gcp_project_id": "my-project", "event": "e"})
    assert result["gcp_project_id"] == _REDACTED


# ---------------------------------------------------------------------------
# Glob patterns
# ---------------------------------------------------------------------------


def test_redact_secret_suffix_pattern() -> None:
    result = _invoke({"db_secret": "password123", "event": "e"})
    assert result["db_secret"] == _REDACTED


def test_redact_password_suffix_pattern() -> None:
    result = _invoke({"admin_password": "hunter2", "event": "e"})
    assert result["admin_password"] == _REDACTED


def test_non_sensitive_key_not_redacted() -> None:
    result = _invoke({"event": "train_done", "recipe": "news", "score": 0.42})
    assert result["event"] == "train_done"
    assert result["recipe"] == "news"
    assert result["score"] == 0.42


# ---------------------------------------------------------------------------
# Nested dicts/lists
# ---------------------------------------------------------------------------


def test_redact_nested_dict() -> None:
    event = {
        "event": "startup",
        "config": {
            "aws_secret_access_key": "secret",
            "host": "localhost",
        },
    }
    result = _invoke(event)
    assert result["config"]["aws_secret_access_key"] == _REDACTED
    assert result["config"]["host"] == "localhost"


def test_redact_nested_list_of_dicts() -> None:
    event = {
        "event": "keys",
        "keys": [
            {"x-api-key": "value1", "name": "safe"},
            {"normal": "field"},
        ],
    }
    result = _invoke(event)
    assert result["keys"][0]["x-api-key"] == _REDACTED
    assert result["keys"][0]["name"] == "safe"
    assert result["keys"][1]["normal"] == "field"


# ---------------------------------------------------------------------------
# should_redact helper
# ---------------------------------------------------------------------------


def test_should_redact_case_insensitive() -> None:
    assert _should_redact("X-API-KEY") is True
    assert _should_redact("x-api-key") is True
    assert _should_redact("AWS_SECRET") is True
    assert _should_redact("aws_secret") is True


def test_should_not_redact_normal_fields() -> None:
    assert _should_redact("recipe") is False
    assert _should_redact("event") is False
    assert _should_redact("score") is False
    assert _should_redact("trained_at") is False


# ---------------------------------------------------------------------------
# Processor is callable (first-in-chain contract)
# ---------------------------------------------------------------------------


def test_processor_is_callable_with_standard_signature() -> None:
    """The processor must accept (logger, method_name, event_dict) positional args."""
    import inspect

    sig = inspect.signature(redact_sensitive_keys)
    params = list(sig.parameters.keys())
    assert len(params) == 3


# ---------------------------------------------------------------------------
# CRITICAL: redact processor is first in structlog processor chain
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# S-A: substring-based key redaction (no underscore boundary required)
# ---------------------------------------------------------------------------


def test_redact_key_with_auth_substring() -> None:
    """Key containing 'auth' substring must be redacted."""
    # "auth" as a substring — e.g. a field named "auth_header" or "reauth"
    result = _invoke({"auth_header": "Bearer token", "event": "e"})
    assert result["auth_header"] == _REDACTED


def test_redact_key_with_apikey_no_underscore() -> None:
    """Key 'apikey' (no underscore before 'key') must be redacted by substring check."""
    result = _invoke({"apikey": "abc123", "event": "e"})
    assert result["apikey"] == _REDACTED


def test_redact_key_with_bearer_substring() -> None:
    """Key containing 'bearer' must be redacted."""
    result = _invoke({"bearer_token": "tok", "event": "e"})
    assert result["bearer_token"] == _REDACTED


def test_redact_key_with_cred_substring() -> None:
    """Key containing 'cred' must be redacted."""
    result = _invoke({"cred_file": "/path/to/creds.json", "event": "e"})
    assert result["cred_file"] == _REDACTED


def test_redact_key_with_private_substring() -> None:
    """Key containing 'private' must be redacted."""
    result = _invoke({"private_key": "rsa-key-data", "event": "e"})
    assert result["private_key"] == _REDACTED


def test_non_sensitive_key_not_redacted_no_false_positive() -> None:
    """Keys like 'created_at', 'score', 'name' must not be redacted."""
    result = _invoke({"created_at": "2026-01-01", "score": 0.9, "recipe_name": "news"})
    assert result["created_at"] == "2026-01-01"
    assert result["score"] == 0.9
    assert result["recipe_name"] == "news"


# ---------------------------------------------------------------------------
# S-J: value-side high-entropy scrubbing
# ---------------------------------------------------------------------------


def test_value_scrub_hex64_in_error_message() -> None:
    """A 64-hex-char substring in a string value must be replaced with [REDACTED-HEX64].

    Scenario: RECOTEM_SIGNING_KEYS value leaks into an error string and ends up
    as the value of a non-sensitive key (e.g. 'error').
    """
    hex64 = "a" * 64
    result = _invoke({"error": f"RECOTEM_SIGNING_KEYS=foo:{hex64} is malformed"})
    assert hex64 not in result["error"], (
        f"64-hex-char substring must be scrubbed from value; got: {result['error']!r}"
    )
    assert "[REDACTED-HEX64]" in result["error"]


def test_value_scrub_base64url43_in_string_value() -> None:
    """A 43-char base64url substring in a string value must be replaced with
    [REDACTED-B64URL43].
    """
    b64url43 = "A" * 43
    result = _invoke({"info": f"api key is {b64url43} from config"})
    assert b64url43 not in result["info"], (
        f"43-char base64url substring must be scrubbed; got: {result['info']!r}"
    )
    assert "[REDACTED-B64URL43]" in result["info"]


def test_value_scrub_multiple_hex64_in_one_string() -> None:
    """Multiple 64-hex-char occurrences in one value are all scrubbed."""
    hex64 = "b" * 64
    raw = f"key1={hex64} key2={hex64}"
    result = _invoke({"msg": raw})
    assert result["msg"].count("[REDACTED-HEX64]") == 2, (
        f"Both hex64 occurrences must be scrubbed; got: {result['msg']!r}"
    )


def test_value_scrub_already_redacted_value_untouched() -> None:
    """A value that is already '[REDACTED]' must not be double-processed."""
    result = _invoke({"safe_key": "[REDACTED]"})
    assert result["safe_key"] == "[REDACTED]"


def test_value_scrub_non_string_value_untouched() -> None:
    """Non-string values (int, float, bool) are returned unchanged by scrubber."""
    result = _invoke({"score": 0.42, "count": 7, "flag": True, "event": "e"})
    assert result["score"] == 0.42
    assert result["count"] == 7
    assert result["flag"] is True


# ---------------------------------------------------------------------------
# Issue-2: key pattern boundary — explicit allowlist guards natural-language words
# ---------------------------------------------------------------------------


def test_redact_api_key_snake_case() -> None:
    """Field named 'api_key' (snake_case, 'key' after underscore) must be redacted."""
    result = _invoke({"api_key": "my_secret", "event": "e"})
    assert result["api_key"] == _REDACTED


def test_redact_apikey_camel_case() -> None:
    """Field named 'apikey' (no separator before 'key') must be redacted."""
    result = _invoke({"apikey": "my_secret", "event": "e"})
    assert result["apikey"] == _REDACTED


def test_redact_signing_key_snake_case() -> None:
    """Field named 'signing_key' must be redacted."""
    result = _invoke({"signing_key": "my_key_material", "event": "e"})
    assert result["signing_key"] == _REDACTED


def test_redact_client_key() -> None:
    """Field named 'client_key' must be redacted."""
    result = _invoke({"client_key": "secret", "event": "e"})
    assert result["client_key"] == _REDACTED


def test_no_redact_monkey_allowlisted() -> None:
    """Field named 'monkey' must NOT be redacted (benign allowlist)."""
    from recotem.log_redaction import _should_redact

    assert _should_redact("monkey") is False


def test_no_redact_turkey_allowlisted() -> None:
    """Field named 'turkey' must NOT be redacted (benign allowlist)."""
    from recotem.log_redaction import _should_redact

    assert _should_redact("turkey") is False


def test_no_redact_signing_kids_not_key() -> None:
    """Field named 'signing_kids' must NOT be redacted (not a key field)."""
    from recotem.log_redaction import _should_redact

    assert _should_redact("signing_kids") is False


# ---------------------------------------------------------------------------
# Issue-3: hex64 value-side scrubbing with lookaround (not word boundary)
# ---------------------------------------------------------------------------


def test_value_scrub_hex64_concatenated_no_separator() -> None:
    """Two 64-hex-char strings concatenated without separator must be redacted.

    With ``\\b``-based regex, a 128-char hex run has no internal word boundary
    and the pattern fails to match any 64-char slice.  The lookaround-based
    pattern matches the ENTIRE 128-char run (length ≥ 64) as one redaction.
    """
    hex128 = "a" * 128
    result = _invoke({"msg": hex128})
    assert hex128 not in result["msg"], (
        f"128 consecutive hex chars must be redacted; got: {result['msg']!r}"
    )
    assert "[REDACTED-HEX64]" in result["msg"]


def test_value_scrub_hex64_url_embedded() -> None:
    """64-hex-char substring embedded in a URL path must be redacted."""
    hex64 = "b" * 64
    url = f"/api/artifacts/{hex64}/metadata"
    result = _invoke({"path": url})
    assert hex64 not in result["path"], (
        f"URL-embedded hex64 must be redacted; got: {result['path']!r}"
    )
    assert "[REDACTED-HEX64]" in result["path"]


def test_value_scrub_hex64_kid_prefix_colon_separated() -> None:
    """Hex64 in 'kid=abc:<hex64>:nextfield' format must be redacted."""
    hex64 = "0" * 64
    s = f"kid=abc:{hex64}:nextfield"
    result = _invoke({"info": s})
    assert hex64 not in result["info"], (
        f"Colon-separated hex64 must be redacted; got: {result['info']!r}"
    )
    assert "[REDACTED-HEX64]" in result["info"]


def test_value_scrub_hex63_not_redacted_as_hex64() -> None:
    """63 hex chars surrounded by non-hex chars must NOT match the hex64 pattern.

    Note: 63 chars that are also valid base64url chars (like lowercase hex 'c')
    would be caught by _B64URL43_RE (43+ base64url pattern).  This test
    isolates the hex64 boundary by using a string that is ONLY hex-digit
    characters (no alpha) so neither pattern matches.  Pure digit strings are
    hex chars but NOT in the base64url alphabet for the purposes of this
    structural test; the key insight is that {64,} means 64 or more.
    """
    # Use a string of 63 hex digits (0-9 only) bounded by non-hex chars.
    # Pure digits are valid base64url chars too, BUT the B64URL43 lookaround
    # anchors on the base64url char class [A-Za-z0-9_-].  A run of 63 digits
    # alone (not adjacent to other base64url chars) would still match B64URL43.
    # We therefore use a context string that embeds the 63-digit run next to
    # non-base64url chars, and assert the hex64 pattern does NOT fire.
    # The broader assertion: the FULL 63-char hex run is not replaced by
    # [REDACTED-HEX64] (it may be replaced by [REDACTED-B64URL43] — that is
    # acceptable because 63 digits is also ≥43 base64url chars).
    hex63 = "0" * 63
    msg = f"count:{hex63}:end"  # non-hex colons delimit the run
    result = _invoke({"msg": msg})
    # The hex64 pattern must NOT match (63 < 64).
    assert "[REDACTED-HEX64]" not in result["msg"], (
        f"63-char hex must NOT trigger [REDACTED-HEX64]; got: {result['msg']!r}"
    )
    # It WILL be caught by the b64url43 pattern (digits are valid base64url).
    # That is intentional — better to over-redact than to under-redact.


# ---------------------------------------------------------------------------
# LR-1: bytes/bytearray values do not leak raw bytes into logs
# ---------------------------------------------------------------------------


def test_bytes_value_signing_key_shaped_is_redacted() -> None:
    """A bytes value whose hex() is 64 hex chars (32-byte signing-key shaped)
    must be fully redacted — not logged as repr(b'...').
    """
    from recotem.log_redaction import _REDACTED

    # 32 raw bytes → 64 hex chars when .hex() is called.
    raw_key = b"\xab\xcd" * 16  # 32 bytes; hex = 'abcd' * 16 = 64 chars
    assert len(raw_key.hex()) == 64, "sanity: must be 64 hex chars"

    result = _invoke({"signing_key_bytes": raw_key, "event": "startup"})
    # Key name contains 'key' → redacted by name.
    assert result["signing_key_bytes"] == _REDACTED


def test_bytes_value_on_non_sensitive_key_signing_key_shaped_is_redacted() -> None:
    """A bytes value whose hex representation is 64+ chars must be replaced with
    _REDACTED even if the key name is not itself sensitive.

    This prevents raw signing-key bytes escaping via a non-obvious key name
    like 'raw_value' or 'body'.
    """
    from recotem.log_redaction import _REDACTED

    raw_key = b"\x01\x02" * 16  # 32 bytes → 64-char hex
    assert len(raw_key.hex()) == 64

    result = _invoke({"raw_value": raw_key, "event": "debug"})
    # Value-side bytes redaction must fire.
    assert result["raw_value"] == _REDACTED, (
        f"64-hex-char bytes value must be redacted; got {result['raw_value']!r}"
    )
    # The raw repr must not appear in the output.
    assert repr(raw_key) not in str(result), (
        "Raw bytes repr must never appear in log output"
    )


def test_bytes_value_not_key_shaped_returns_length_summary() -> None:
    """A short bytes value that is NOT signing-key-shaped must be replaced with
    a safe length summary '<bytes len=N>' rather than logged raw.
    """
    short_bytes = b"hello"  # 5 bytes → 10 hex chars, not key-shaped
    result = _invoke({"body": short_bytes, "event": "debug"})
    assert result["body"] == "<bytes len=5>", (
        f"Short bytes must become '<bytes len=5>'; got {result['body']!r}"
    )
    assert b"hello" not in str(result).encode(), (
        "Raw bytes must never appear in log output"
    )


def test_bytearray_value_signing_key_shaped_is_redacted() -> None:
    """bytearray values with 64+ hex chars must also be redacted."""
    from recotem.log_redaction import _REDACTED

    raw_key = bytearray(b"\xff\xee" * 16)  # 32 bytes → 64-char hex
    assert len(raw_key.hex()) == 64

    result = _invoke({"raw_value": raw_key, "event": "debug"})
    assert result["raw_value"] == _REDACTED, (
        f"64-char-hex bytearray must be redacted; got {result['raw_value']!r}"
    )


def test_bytes_value_nested_in_dict_redacted() -> None:
    """Bytes values nested inside a dict value must also be handled."""
    from recotem.log_redaction import _REDACTED

    raw_key = b"\xde\xad" * 16  # 32 bytes → 64-char hex
    result = _invoke({"event": "e", "context": {"signing_key": raw_key}})
    # Key name 'signing_key' triggers name-based redaction → _REDACTED.
    assert result["context"]["signing_key"] == _REDACTED


def test_redact_processor_is_first_in_chain() -> None:
    """configure_logging must place _redact_sensitive_keys first in the chain.

    The security contract: no sensitive value reaches any renderer because
    the redaction processor runs BEFORE all other processors.  If it is
    placed second (or later), a renderer that fires first could log raw keys.

    This test calls configure_logging("json") then reads the processor list
    to confirm position [0] is the redaction function.  The previous
    structlog config is restored via structlog.reset_defaults() in the
    autouse conftest fixture.
    """
    import structlog

    from recotem.log_redaction import redact_sensitive_keys
    from recotem.logging import configure_logging

    configure_logging("json")
    cfg = structlog.get_config()
    processors = cfg["processors"]

    assert len(processors) > 0, "processor list must not be empty"
    first = processors[0]
    assert first is redact_sensitive_keys, (
        f"Expected processors[0] to be redact_sensitive_keys, "
        f"got {first!r}.  Redaction MUST be first so no sensitive "
        "value can reach a renderer before being stripped."
    )


# ---------------------------------------------------------------------------
# sil m-5: safety net — internal redaction failure must not drop the event
# ---------------------------------------------------------------------------


def test_redact_internal_failure_returns_redaction_failed_event() -> None:
    """If the redaction logic itself raises, the event must not be silently
    dropped.  A safe fallback dict with event='[redaction_failed]' must be
    returned so the log chain can continue."""
    from unittest.mock import patch

    boom = ValueError("unexpected redaction error")
    with patch(
        "recotem.log_redaction._do_redact",
        side_effect=boom,
    ):
        result = _invoke({"event": "something_sensitive", "user": "alice"})

    assert result["event"] == "[redaction_failed]", (
        f"Expected event='[redaction_failed]'; got {result['event']!r}"
    )
    assert "redaction_error_class" in result, (
        "Fallback result must include 'redaction_error_class'"
    )
    assert result["redaction_error_class"] == "ValueError"


def test_redact_internal_failure_preserves_original_event_prefix() -> None:
    """original_event in the fallback must carry the first 64 chars of the
    original event string so operators can identify which log line failed."""
    from unittest.mock import patch

    long_event = "a" * 128
    with patch("recotem.log_redaction._do_redact", side_effect=RuntimeError("boom")):
        result = _invoke({"event": long_event})

    assert result["original_event"] == "a" * 64, (
        "original_event must be truncated to 64 chars"
    )


def test_redact_internal_failure_does_not_raise() -> None:
    """redact_sensitive_keys must never raise — even if _do_redact raises."""
    from unittest.mock import patch

    from recotem.log_redaction import redact_sensitive_keys

    with patch(
        "recotem.log_redaction._do_redact",
        side_effect=Exception("any error"),
    ):
        # Must not raise.
        result = redact_sensitive_keys(None, "info", {"event": "test"})  # type: ignore[arg-type]

    assert isinstance(result, dict), "Result must always be a dict"


# ---------------------------------------------------------------------------
# DSN userinfo redaction
# ---------------------------------------------------------------------------


def test_dsn_userinfo_in_message_is_redacted() -> None:
    from recotem.log_redaction import redact_sensitive_keys

    event = {
        "event": "connecting",
        "dsn": "postgresql://alice:s3cret@db.example.com:5432/orders",
        "message": "Tried postgresql+psycopg://bob:hunter2@10.0.0.1/orders; failed",
    }
    out = redact_sensitive_keys(None, None, event)
    assert "alice" not in out["dsn"]
    assert "s3cret" not in out["dsn"]
    assert "bob" not in out["message"]
    assert "hunter2" not in out["message"]
    assert out["dsn"].startswith("postgresql://")
    assert out["dsn"].endswith("/orders") or "host" in out["dsn"].lower()


def test_dsn_redaction_preserves_non_credentialed_url() -> None:
    from recotem.log_redaction import redact_sensitive_keys

    event = {"event": "ok", "url": "postgresql://db.example.com:5432/orders"}
    out = redact_sensitive_keys(None, None, event)
    assert out["url"] == "postgresql://db.example.com:5432/orders"


# ---------------------------------------------------------------------------
# D1 — mysql+pymysql DSN credentials scrubbed
# ---------------------------------------------------------------------------


def test_mysql_pymysql_dsn_credentials_scrubbed() -> None:
    from urllib.parse import urlparse

    from recotem.log_redaction import _scrub_string_value

    result = _scrub_string_value("mysql+pymysql://root:secret@db.internal:3306/mydb")
    assert "root" not in result
    assert "secret" not in result
    # The host must be preserved at the URL's hostname position (not just as a
    # substring at an arbitrary location).
    parsed = urlparse(result)
    assert parsed.hostname == "db.internal"


# ---------------------------------------------------------------------------
# D2 — sqlite:/// path passes through unchanged
# ---------------------------------------------------------------------------


def test_sqlite_path_passes_through_unchanged() -> None:
    from recotem.log_redaction import _scrub_string_value

    original = "sqlite:///local.db"
    result = _scrub_string_value(original)
    assert result == original


def test_sqlite_uri_mode_with_query_string_passes_through_unchanged() -> None:
    """``sqlite:///file:/tmp/db?mode=ro&uri=true`` (SQLite URI form) is not a
    credentialed DSN; it must pass through the scrubber unchanged.

    Regression guard: ``sqlite`` is intentionally absent from the DSN scheme
    allow-list so query-string keys like ``mode=ro`` cannot trigger spurious
    rewriting that would mangle the URI form used to pin SQLite to read-only.
    """
    from recotem.log_redaction import _scrub_string_value

    original = "sqlite:///file:/tmp/db?mode=ro&uri=true"
    result = _scrub_string_value(original)
    assert result == original, (
        f"SQLite URI-mode DSN was unexpectedly altered: {original!r} -> {result!r}"
    )


# ---------------------------------------------------------------------------
# MINOR-1: DSN scrubber short-circuit on "://" absence
# ---------------------------------------------------------------------------


def test_dsn_scrubber_short_circuit_does_not_break_credentialed_dsn() -> None:
    """Strings containing '://' with DSN credentials must still be scrubbed.

    This verifies the '://' short-circuit guard doesn't accidentally skip
    DSNs that contain user credentials.
    """
    from urllib.parse import urlparse

    from recotem.log_redaction import _scrub_string_value

    dsn = "postgresql://alice:s3cret@db.example.com:5432/orders"
    result = _scrub_string_value(dsn)
    assert "alice" not in result
    assert "s3cret" not in result
    # Check the host is preserved at the URL's hostname position rather than
    # as an arbitrary substring (defends against accidental moves of the host
    # into userinfo / path / query and silences CodeQL py/incomplete-url-
    # substring-sanitization).
    parsed = urlparse(result)
    assert parsed.hostname == "db.example.com"


def test_dsn_scrubber_short_circuit_skips_regex_on_plain_strings() -> None:
    """Strings without '://' must pass through the DSN scrubber unchanged.

    This validates the short-circuit: a bare hex token or log message that
    contains no '://' must not be touched by the DSN regex.
    """
    from recotem.log_redaction import _scrub_string_value

    # A bare hex token — no '://', so DSN regex should not run.
    # (It may still be caught by the HEX64 pattern if long enough, but that
    # is separate from the DSN scrubber.)
    plain = "just a plain log message with no URL"
    result = _scrub_string_value(plain)
    assert result == plain

    # A short hex string (< 64 chars) without a scheme — untouched.
    short_hex = "deadbeef1234"
    assert _scrub_string_value(short_hex) == short_hex


# ---------------------------------------------------------------------------
# DSN scrubber — additional coverage (m1)
# ---------------------------------------------------------------------------


def test_dsn_userinfo_postgresql_basic_scrubbed() -> None:
    """postgresql://user:pass@host DSN must have userinfo replaced with ***."""
    from urllib.parse import urlparse

    from recotem.log_redaction import _scrub_string_value

    result = _scrub_string_value("postgresql://user:pass@db.example.com/mydb")
    assert "user" not in result
    assert "pass" not in result
    parsed = urlparse(result)
    assert parsed.hostname == "db.example.com"


def test_dsn_userinfo_postgresql_psycopg2_with_query_scrubbed() -> None:
    """postgresql+psycopg2 DSN with port and query string: userinfo must be scrubbed."""
    from urllib.parse import urlparse

    from recotem.log_redaction import _scrub_string_value

    dsn = "postgresql+psycopg2://u:p@host:5432/db?sslmode=require"
    result = _scrub_string_value(dsn)
    assert ":p@" not in result
    assert "u:" not in result or result.startswith("postgresql+psycopg2://***@")
    parsed = urlparse(result)
    assert parsed.hostname == "host"


def test_dsn_userinfo_mysql_ipv4_host_scrubbed() -> None:
    """mysql+pymysql DSN with an IPv4 address as host: userinfo must be scrubbed."""
    from urllib.parse import urlparse

    from recotem.log_redaction import _scrub_string_value

    dsn = "mysql+pymysql://root:secret@127.0.0.1/test"
    result = _scrub_string_value(dsn)
    assert "root" not in result
    assert "secret" not in result
    parsed = urlparse(result)
    assert parsed.hostname == "127.0.0.1"


def test_dsn_already_redacted_string_unchanged() -> None:
    """A value starting with '[REDACTED' must not be double-processed."""
    from recotem.log_redaction import _scrub_string_value

    already = "[REDACTED]"
    assert _scrub_string_value(already) == already


def test_plain_https_url_without_credentials_unchanged() -> None:
    """https://example.com/path contains no userinfo and must pass through unchanged."""
    from recotem.log_redaction import _scrub_string_value

    url = "https://example.com/path"
    assert _scrub_string_value(url) == url


# ---------------------------------------------------------------------------
# I-4 — DSN with empty username (":pass@host") is also scrubbed
# ---------------------------------------------------------------------------


def test_dsn_userinfo_empty_username_scrubbed() -> None:
    """``postgresql://:password@host/db`` must have the password redacted.

    RFC 3986 and SQLAlchemy ``make_url`` accept an empty username with a
    non-empty password.  The previous regex required ``+`` (one-or-more) on
    the user character class, so this exact shape slipped through and the
    password leaked verbatim.  The new regex uses ``*`` (zero-or-more).
    """
    from urllib.parse import urlparse

    from recotem.log_redaction import _scrub_string_value

    dsn = "postgresql://:hunter2@db.example.com:5432/orders"
    result = _scrub_string_value(dsn)
    assert "hunter2" not in result, f"password leaked: {result!r}"
    parsed = urlparse(result)
    assert parsed.hostname == "db.example.com"
    # The scrubbed userinfo is the literal ``***``.
    assert "***" in result


def test_dsn_userinfo_only_password_no_user_mysql() -> None:
    """Same shape for mysql+pymysql DSNs."""
    from recotem.log_redaction import _scrub_string_value

    result = _scrub_string_value("mysql+pymysql://:secret@h:3306/db")
    assert "secret" not in result
    assert result.startswith("mysql+pymysql://***@")


# ---------------------------------------------------------------------------
# I-6 — object-store URIs that idiomatically use @ MUST NOT be rewritten
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        # gcsfs syntax: gs://<bucket>@<project>/<key>
        "gs://my-bucket@my-project/data.parquet",
        # s3:// path-style URIs
        "s3://my-bucket@us-east-1/key",
        # Azure blob — abfs / az schemes
        "az://container@account.dfs.core.windows.net/data",
        "abfs://container@account.dfs.core.windows.net/data",
        "abfss://container@account.dfs.core.windows.net/data",
        # arbitrary non-credential-bearing scheme
        "myorg://bucket@project/key",
    ],
)
def test_object_store_uris_with_at_sign_preserved(uri) -> None:
    """The DSN scrubber MUST NOT rewrite object-store URIs that use ``@`` for
    non-credential purposes (e.g. gcsfs ``gs://bucket@project/key``).  Doing
    so would silently delete the bucket name from operator logs without
    protecting any actual secret.
    """
    from recotem.log_redaction import _scrub_string_value

    assert _scrub_string_value(uri) == uri, (
        f"object-store URI {uri!r} must pass through DSN scrubber unchanged"
    )


def test_credential_bearing_schemes_still_scrubbed() -> None:
    """The scheme allowlist still covers all the production DSN shapes."""
    from recotem.log_redaction import _scrub_string_value

    cases = [
        "postgresql://u:p@h/db",
        "postgres://u:p@h/db",
        "postgresql+psycopg://u:p@h/db",
        "postgresql+asyncpg://u:p@h/db",
        "mysql://u:p@h/db",
        "mysql+pymysql://u:p@h/db",
        "mariadb://u:p@h/db",
        "mariadb+pymysql://u:p@h/db",
        "mssql+pyodbc://u:p@h/db",
        "oracle+cx_oracle://u:p@h/db",
        "mongodb://u:p@h/db",
        "mongodb+srv://u:p@h/db",
        "redis://u:p@h:6379",
        "rediss://u:p@h:6379",
        "amqp://u:p@h:5672/",
        "amqps://u:p@h:5672/",
        "http://u:p@example.com/",
        "https://u:p@example.com/",
        "ftp://u:p@h/path",
        "ftps://u:p@h/path",
    ]
    for case in cases:
        result = _scrub_string_value(case)
        assert ":p@" not in result, f"credentials leaked in {case!r}: {result!r}"
        assert "***@" in result, f"scrubber did not fire on {case!r}: {result!r}"


# ---------------------------------------------------------------------------
# M-6 — _redact_value recurses into tuples / sets
# ---------------------------------------------------------------------------


def test_redact_value_recurses_into_tuples() -> None:
    """Strings nested in a tuple value must be scrubbed."""
    from recotem.log_redaction import _redact_value

    out = _redact_value(("plain", "postgresql://u:p@h/db", 42))
    assert isinstance(out, tuple), "tuple identity must be preserved"
    assert out[0] == "plain"
    assert "***" in out[1]
    assert "u:p" not in out[1]
    assert out[2] == 42


def test_redact_value_recurses_into_nested_tuple_in_dict() -> None:
    """Tuples nested inside dict values are still scrubbed."""
    from recotem.log_redaction import _redact_value

    out = _redact_value({"paths": ("postgresql://u:p@h/db", "gs://bucket@project/k")})
    inner = out["paths"]
    assert isinstance(inner, tuple)
    assert "***" in inner[0]  # DSN scrubbed
    assert inner[1] == "gs://bucket@project/k"  # object-store URI preserved


def test_redact_value_recurses_into_sets() -> None:
    """Strings nested in a set value must be scrubbed (defence in depth)."""
    from recotem.log_redaction import _redact_value

    out = _redact_value({"plain", "postgresql://u:p@h/db"})
    assert isinstance(out, set)
    assert "plain" in out
    # The DSN string is replaced with the scrubbed version.
    scrubbed = [s for s in out if s.startswith("postgresql://")]
    assert scrubbed and "***" in scrubbed[0]
    assert all(":p@" not in s for s in out)


# ---------------------------------------------------------------------------
# Feature-aware iALS cold-start: user_features / item_features are PII by
# construction (e.g. age_band, country) and must be redacted by key name.
#
# This is defence in depth, not the primary control -- the primary rule is
# that callers must never pass a feature dict to a logger in the first
# place.  This backstop exists in case one does anyway.
# ---------------------------------------------------------------------------


def test_user_features_values_are_redacted() -> None:
    event = {"event": "x", "user_features": {"band": "35-44", "country": "JP"}}
    out = _invoke(dict(event))
    assert "35-44" not in repr(out)
    assert "JP" not in repr(out)


def test_item_features_values_are_redacted() -> None:
    event = {"event": "x", "item_features": {"new1": {"genre": "action"}}}
    out = _invoke(dict(event))
    assert "action" not in repr(out)


def test_unrelated_keys_still_pass_through() -> None:
    """The new redaction must not swallow ordinary fields."""
    event = {"event": "x", "recipe": "movies", "limit": 10}
    out = _invoke(dict(event))
    assert out["recipe"] == "movies"
    assert out["limit"] == 10


# ---------------------------------------------------------------------------
# End-to-end: the REAL processor chain, asserting on RENDERED output.
#
# Everything above this point calls ``redact_sensitive_keys`` directly, and the
# rest of the suite reaches for ``structlog.testing.capture_logs()`` -- which
# replaces the processor chain wholesale and so never runs redaction at all.
# That blind spot is why two over-redaction defects reached production logs:
# ``security.posture`` losing ``auth_enabled`` / ``signing_key_status``, and
# 43+ character event names rendering as ``[REDACTED-B64URL43]``.
#
# The tests below go through ``recotem.logging.configure_logging`` -- the real
# chain, the real JSON renderer -- and assert on the rendered line.
# ---------------------------------------------------------------------------

# Synthetic test material.  Structurally valid for the value-side patterns but
# obviously fabricated: never a real credential.
_FAKE_SIGNING_KEY_HEX = "0123456789abcdef" * 4  # 64 hex chars
_FAKE_API_KEY_B64URL = "Fake-Api-Key-Material-For-Tests-Only-Not-Real"  # 45 base64url
_FAKE_AWS_SECRET = "FAKEawsSECRETaccessKEYmaterialFORtestsONLY0"  # 43 base64url


class _RenderedLog:
    """Reader over the rendered output of the real structlog chain."""

    def __init__(self, buf) -> None:
        self._buf = buf

    @property
    def raw(self) -> str:
        return self._buf.getvalue()

    def clear(self) -> None:
        self._buf.seek(0)
        self._buf.truncate(0)

    def lines(self) -> list[dict]:
        import json

        return [json.loads(x) for x in self.raw.splitlines() if x.strip()]

    def emit(self, event: str, **kw) -> dict:
        """Log one event through the real chain; return the rendered line."""
        import structlog

        self.clear()
        structlog.get_logger("test.redaction").info(event, **kw)
        assert self.raw, "nothing was rendered"
        return self.lines()[0]

    def event(self, name: str) -> dict:
        """Return the single rendered line whose event name is *name*."""
        matches = [x for x in self.lines() if x.get("event") == name]
        assert matches, f"no {name!r} line rendered; got {self.raw!r}"
        return matches[0]


@pytest.fixture
def rendered():
    """Install the real logging chain and capture its rendered output.

    ``configure_logging`` builds the production processor chain (redaction
    first) and the production JSON renderer; only the handler's output stream
    is swapped for an in-memory buffer.  Unlike ``capture_logs()``, this
    exercises redaction.
    """
    import io
    import logging as stdlib_logging

    import structlog

    from recotem.logging import configure_logging

    root = stdlib_logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_config = structlog.get_config()
    try:
        configure_logging("json")
        buf = io.StringIO()
        for handler in root.handlers:
            handler.setStream(buf)
        yield _RenderedLog(buf)
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        structlog.configure(**saved_config)


# --- (a) security.posture keeps the two fields it exists to convey ----------


def test_rendered_security_posture_keeps_auth_enabled(rendered) -> None:
    """``auth_enabled`` is a computed bool -- an r"auth" false positive."""
    line = rendered.emit("security.posture", auth_enabled=True)
    assert line["auth_enabled"] is True, (
        f"auth_enabled must survive redaction; rendered {rendered.raw!r}"
    )


@pytest.mark.parametrize(
    "status",
    ["configured", "missing", "dev_allow_unsigned", "construction_failed"],
)
def test_rendered_security_posture_keeps_signing_key_status(
    rendered, status: str
) -> None:
    """``signing_key_status`` is a closed-set status -- a r"key" false positive."""
    line = rendered.emit("security.posture", signing_key_status=status)
    assert line["signing_key_status"] == status, (
        f"signing_key_status must survive redaction; rendered {rendered.raw!r}"
    )


def test_rendered_security_posture_from_the_real_call_site(rendered) -> None:
    """Exercise ``serving/app.py``'s real emission, not a hand-built event dict."""
    from recotem.config import ServeConfig
    from recotem.serving.app import _emit_security_posture

    rendered.clear()
    _emit_security_posture(ServeConfig(), None)

    line = rendered.event("security.posture")
    assert isinstance(line["auth_enabled"], bool), (
        f"auth_enabled must render as a bool, got {line['auth_enabled']!r}"
    )
    assert line["signing_key_status"] in {
        "configured",
        "missing",
        "dev_allow_unsigned",
        "construction_failed",
    }, f"signing_key_status must render as a status, got {line!r}"


# --- (b) long snake_case event names survive --------------------------------


@pytest.mark.parametrize(
    "event_name",
    [
        # Every recotem event name at or past the 43-char base64url threshold.
        "sql_statement_timeout_unsupported_on_sqlite",  # 43 (datasource/sql.py)
        "recipe_yaml_parse_failed_on_rescan_new_file",  # 43 (serving/watcher.py)
        "source_registry_unavailable_during_validation",  # 45 (recipe/models.py)
        # A Prometheus metric name -- same shape, same trap, should one be logged.
        "recotem_v1_validation_errors_outside_verb_total",  # 47
    ],
)
def test_rendered_long_snake_case_event_name_survives(
    rendered, event_name: str
) -> None:
    """A 43+ char snake_case identifier is an event name, not key material."""
    assert len(event_name) >= 43, "test data must exceed the base64url threshold"
    line = rendered.emit(event_name)
    assert line["event"] == event_name, (
        f"event name destroyed by value-side scrubbing; rendered {rendered.raw!r}"
    )


def test_rendered_sqlite_timeout_warning_from_the_real_call_site(
    rendered, monkeypatch
) -> None:
    """The warning ``docs/data-sources/sql.md`` promises must be readable.

    ``tests/unit/test_datasource_sql.py`` asserts this event through
    ``capture_logs()``, which bypasses the processor chain -- so it kept
    passing while the rendered line read ``[REDACTED-B64URL43]``.
    """
    from unittest.mock import MagicMock

    from recotem.datasource.sql import SQLConfig, SQLSource

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", "sqlite:///:memory:")
    source = SQLSource(
        SQLConfig(
            type="sql",
            dsn_env="RECOTEM_RECIPE_DB_DSN",
            query="SELECT user_id, item_id FROM events",
        )
    )

    rendered.clear()
    source._apply_statement_timeout(MagicMock())

    line = rendered.event("sql_statement_timeout_unsupported_on_sqlite")
    assert line["requested_seconds"], f"warning lost its context: {line!r}"


# --- (c) real credential material is STILL redacted in rendered output ------


def test_rendered_signing_key_still_redacted(rendered) -> None:
    rendered.emit("startup", recotem_signing_keys=_FAKE_SIGNING_KEY_HEX)
    assert _FAKE_SIGNING_KEY_HEX not in rendered.raw, (
        f"signing key leaked into {rendered.raw!r}"
    )


def test_rendered_api_key_still_redacted(rendered) -> None:
    rendered.emit("request", **{"x-api-key": _FAKE_API_KEY_B64URL})
    assert _FAKE_API_KEY_B64URL not in rendered.raw, (
        f"api key leaked into {rendered.raw!r}"
    )


def test_rendered_aws_secret_still_redacted(rendered) -> None:
    rendered.emit("boot", aws_secret_access_key=_FAKE_AWS_SECRET)
    assert _FAKE_AWS_SECRET not in rendered.raw, (
        f"aws secret leaked into {rendered.raw!r}"
    )


@pytest.mark.parametrize("field", ["note", "detail", "message", "recipe", "path"])
@pytest.mark.parametrize(
    "material", [_FAKE_SIGNING_KEY_HEX, _FAKE_API_KEY_B64URL, _FAKE_AWS_SECRET]
)
def test_rendered_key_material_under_innocuous_key_name_still_redacted(
    rendered, field: str, material: str
) -> None:
    """The value-side passes are what catch a key under a harmless field name.

    This is the case the snake_case exemption must not weaken: the key name
    gives no hint, so only the value pattern stands between the credential and
    the log.
    """
    rendered.emit("evt", **{field: material})
    assert material not in rendered.raw, (
        f"key material leaked under {field!r}: {rendered.raw!r}"
    )


def test_rendered_key_material_in_the_event_field_still_redacted(rendered) -> None:
    """``event`` keeps its value-side scrubbing.

    ``configure_logging`` installs a ``foreign_pre_chain``, so stdlib loggers
    (uvicorn, SQLAlchemy, urllib3) render interpolated *messages* into
    ``event`` -- exactly the text most likely to carry a stray credential.
    Exempting the ``event`` key wholesale would have given this up; narrowing
    the value pattern instead keeps it.
    """
    rendered.emit(f"connecting with key {_FAKE_API_KEY_B64URL}")
    assert _FAKE_API_KEY_B64URL not in rendered.raw, (
        f"api key leaked via event text: {rendered.raw!r}"
    )

    rendered.emit(f"loaded signing key {_FAKE_SIGNING_KEY_HEX}")
    assert _FAKE_SIGNING_KEY_HEX not in rendered.raw, (
        f"signing key leaked via event text: {rendered.raw!r}"
    )

    rendered.emit("connecting to postgresql://u:hunter2@db.internal/x")
    assert "hunter2" not in rendered.raw, (
        f"DSN password leaked via event text: {rendered.raw!r}"
    )


def test_rendered_allowlisted_key_still_gets_value_side_scrubbing(rendered) -> None:
    """Name-allowlisting must not disable the value passes on that field.

    ``signing_key_status`` should only ever hold a closed-set status, but if a
    regression put key material there the value pattern must still fire.
    """
    rendered.emit("security.posture", signing_key_status=_FAKE_SIGNING_KEY_HEX)
    assert _FAKE_SIGNING_KEY_HEX not in rendered.raw, (
        f"allowlisted key bypassed value scrubbing: {rendered.raw!r}"
    )


# --- (d) existing benign-name behaviour is unchanged ------------------------


@pytest.mark.parametrize("name", ["monkey", "turkey", "donkey", "hockey", "jockey"])
def test_rendered_preexisting_benign_names_unchanged(rendered, name: str) -> None:
    line = rendered.emit("zoo", **{name: "value"})
    assert line[name] == "value", (
        f"{name} must not be redacted; rendered {rendered.raw!r}"
    )


@pytest.mark.parametrize(
    "name",
    ["api_key", "signing_key", "apikey", "x-api-key", "auth_header", "db_password"],
)
def test_rendered_sensitive_names_still_redacted_by_name(rendered, name: str) -> None:
    """The allowlist is exact-match: near-miss names must still be redacted."""
    line = rendered.emit("evt", **{name: "some-value"})
    assert line[name] == _REDACTED, (
        f"{name} must be redacted; rendered {rendered.raw!r}"
    )


# ---------------------------------------------------------------------------
# Unit-level coverage of the two narrowed rules.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected_redacted"),
    [
        # Newly allowlisted -- exact match only.
        ("auth_enabled", False),
        ("AUTH_ENABLED", False),  # allowlist is applied to the lowercased name
        ("signing_key_status", False),
        # Near misses must NOT inherit the allowlist.
        ("auth_enabled_key", True),
        ("signing_key_status_token", True),
        ("auth", True),
        ("signing_key", True),
    ],
)
def test_should_redact_allowlist_is_exact_match(
    name: str, expected_redacted: bool
) -> None:
    assert _should_redact(name) is expected_redacted


@pytest.mark.parametrize(
    "value",
    [
        "sql_statement_timeout_unsupported_on_sqlite",
        "source_registry_unavailable_during_validation",
        "recotem_v1_validation_errors_outside_verb_total",
        "a_" + "b" * 60,
    ],
)
def test_scrub_preserves_lowercase_snake_case_identifiers(value: str) -> None:
    from recotem.log_redaction import _scrub_string_value

    assert _scrub_string_value(value) == value


@pytest.mark.parametrize(
    "value",
    [
        _FAKE_API_KEY_B64URL,  # mixed case + hyphens
        _FAKE_AWS_SECRET,  # mixed case
        "A" * 43,  # uppercase run
        "a" * 43,  # lowercase, but no underscore separator
        "abc_def" + "G" * 40,  # mixed case -- not a snake_case identifier
        "_" + "a" * 43,  # leading underscore -- not an identifier shape
        "a" * 43 + "_",  # trailing underscore
        "ab__" + "c" * 42,  # doubled underscore
    ],
)
def test_scrub_still_redacts_non_identifier_base64url_runs(value: str) -> None:
    from recotem.log_redaction import _scrub_string_value

    assert value not in _scrub_string_value(value), f"{value!r} was not redacted"


def test_scrub_redacts_key_material_adjacent_to_an_identifier() -> None:
    """A long identifier in the same string must not shelter real key material."""
    from recotem.log_redaction import _scrub_string_value

    text = (
        f"event=sql_statement_timeout_unsupported_on_sqlite key={_FAKE_API_KEY_B64URL}"
    )
    out = _scrub_string_value(text)
    assert "sql_statement_timeout_unsupported_on_sqlite" in out
    assert _FAKE_API_KEY_B64URL not in out


# ---------------------------------------------------------------------------
# (e) Identifier-shaped runs survive the base64url pass.
#
# The previous exemption required the whole run to fullmatch
# ``[a-z0-9]+(?:_[a-z0-9]+)+``, so a single hyphen or a single capital
# anywhere turned the entire run into ``[REDACTED-B64URL43]``.  Filenames,
# object keys, doc anchors and business IDs routinely have both.
# ---------------------------------------------------------------------------

_STEM = "interactions_export_from_warehouse_20260903_v2_final"  # 52

_IDENTIFIER_SHAPES = [
    pytest.param(_STEM, id="snake_case"),
    pytest.param("rt-" + _STEM, id="kebab_prefix"),
    pytest.param(_STEM + "-v2", id="kebab_suffix"),
    pytest.param("I" + _STEM[1:], id="one_capital"),
    pytest.param(_STEM.replace("_", "-"), id="kebab_case"),
    pytest.param("rt-" + _STEM.replace("_", "-") + "-v2", id="mixed_separators"),
    # The remedy anchor in the feature-axis error message.  Losing this to
    # redaction removes the pointer to the fix from exactly the logs that CI
    # and Kubernetes capture.
    pytest.param("recotem-train-exits-4-with-feature_axis_error", id="docs_anchor"),
    pytest.param("EXPORT_FROM_WAREHOUSE_20260903_V2_FINAL_RUN", id="upper_snake"),
    pytest.param("Interactions-Export-2026" + "-batch" * 6, id="two_capitals"),
]


@pytest.mark.parametrize("run", _IDENTIFIER_SHAPES)
def test_scrub_preserves_identifier_shaped_runs(run: str) -> None:
    from recotem.log_redaction import _scrub_string_value

    assert len(run) >= 43, "test data must exceed the base64url threshold"
    assert _scrub_string_value(run) == run, f"{run!r} was destroyed"


@pytest.mark.parametrize("run", _IDENTIFIER_SHAPES)
def test_rendered_identifier_shaped_run_survives(rendered, run: str) -> None:
    """Through the real chain: an identifier in a value must stay readable."""
    line = rendered.emit("recipe_loaded", path=f"/etc/recotem/{run}.yaml")
    assert line["path"] == f"/etc/recotem/{run}.yaml", (
        f"identifier destroyed by value-side scrubbing; rendered {rendered.raw!r}"
    )


def test_rendered_dotted_identifier_survives(rendered) -> None:
    """A dotted name is split below the threshold by the run pattern itself.

    ``.`` is outside the base64url alphabet, so ``_B64URL43_RE`` never spans
    one.  Pinned so a future widening of that character class cannot silently
    start eating module / dataset names.
    """
    dotted = "warehouse_exports.interactions_daily.20260903_v2_final_run"
    assert len(dotted) >= 43
    line = rendered.emit("table_resolved", table=dotted)
    assert line["table"] == dotted, f"dotted name destroyed; {rendered.raw!r}"


# ---------------------------------------------------------------------------
# (f) ...and real credential material is still redacted.
# ---------------------------------------------------------------------------


def _random_api_keys(n: int) -> list[str]:
    """Deterministic stand-ins for ``recotem keygen --type api`` output.

    ``keygen`` emits ``urlsafe_b64encode(os.urandom(32))`` stripped of padding
    -- 43 characters over the full 64-char base64url alphabet.  A seeded RNG
    over the same alphabet gives the same shape with a reproducible corpus.
    """
    import random
    import string

    alphabet = string.ascii_letters + string.digits + "-_"
    rng = random.Random(20260903)
    return ["".join(rng.choice(alphabet) for _ in range(43)) for _ in range(n)]


def test_scrub_redacts_a_corpus_of_random_api_key_material() -> None:
    """The widened exemption must not admit realistic key material.

    2000 keygen-shaped tokens; the measured probability that one satisfies the
    identifier shape test is ~1.1e-9 per token.
    """
    from recotem.log_redaction import _scrub_string_value

    survivors = [k for k in _random_api_keys(2000) if k in _scrub_string_value(k)]
    assert not survivors, f"{len(survivors)} random keys survived: {survivors[:3]}"


@pytest.mark.parametrize("field", ["note", "detail", "path", "recipe"])
def test_rendered_random_api_key_under_benign_name_still_redacted(
    rendered, field: str
) -> None:
    """The value pass is the only guard when the field name is harmless."""
    for key in _random_api_keys(25):
        rendered.emit("evt", **{field: f"loaded {key} ok"})
        assert key not in rendered.raw, f"key leaked under {field!r}: {rendered.raw!r}"


def test_rendered_random_api_key_in_the_event_text_still_redacted(rendered) -> None:
    """``event`` carries interpolated stdlib messages -- it keeps scrubbing."""
    for key in _random_api_keys(25):
        rendered.emit(f"loaded {key} ok")
        assert key not in rendered.raw, f"key leaked via event: {rendered.raw!r}"


@pytest.mark.parametrize(
    ("value", "why"),
    [
        # Case-consistent per segment, but an uppercase letter in every word:
        # the global case-outlier cap is what keeps this redacted.
        ("Correct-Horse-Battery-Staple-Passphrase-Val", "title_case"),
        ("Fake-Api-Key-Material-For-Tests-Only-Not-Real", "title_case_long"),
        # camelCase segments are not internally case-consistent.
        ("rt-loadedCredentialForProdWarehouseAccount-20260903", "camel_case"),
        # Random base64url that happens to contain separators.
        ("xK3-mQ7p_vB2nR9sT4wY6zA1cD8eF5gH0jL2kM4nP6q", "random_with_separators"),
        # Pre-existing shapes: no separator, or degenerate separator placement.
        ("A" * 43, "no_separator_upper"),
        ("a" * 43, "no_separator_lower"),
        ("_" + "a" * 43, "leading_separator"),
        ("a" * 43 + "_", "trailing_separator"),
        ("ab__" + "c" * 42, "empty_segment"),
        ("abc_def" + "G" * 40, "mixed_case_segment"),
    ],
)
def test_scrub_still_redacts_non_identifier_shapes(value: str, why: str) -> None:
    from recotem.log_redaction import _scrub_string_value

    assert value not in _scrub_string_value(value), f"{why}: {value!r} survived"


_CREDENTIAL_FIELDS = [
    pytest.param({"x-api-key": _FAKE_API_KEY_B64URL}, _FAKE_API_KEY_B64URL, id="api"),
    pytest.param(
        {"authorization": "Bearer " + _FAKE_API_KEY_B64URL},
        _FAKE_API_KEY_B64URL,
        id="authorization",
    ),
    pytest.param(
        {"recotem_signing_keys": "kid:" + _FAKE_SIGNING_KEY_HEX},
        _FAKE_SIGNING_KEY_HEX,
        id="signing",
    ),
    pytest.param(
        {"aws_secret_access_key": _FAKE_AWS_SECRET}, _FAKE_AWS_SECRET, id="aws"
    ),
    pytest.param(
        {"google_application_credentials": _FAKE_API_KEY_B64URL},
        _FAKE_API_KEY_B64URL,
        id="gcp",
    ),
    pytest.param(
        {"azure_storage_account_key": _FAKE_AWS_SECRET}, _FAKE_AWS_SECRET, id="azure"
    ),
    pytest.param(
        {"dsn": "postgresql+psycopg://svc:hunter2@db.internal:5432/prod"},
        "hunter2",
        id="dsn_password",
    ),
]


@pytest.mark.parametrize(("payload", "material"), _CREDENTIAL_FIELDS)
def test_rendered_credentials_still_redacted(
    rendered, payload: dict, material: str
) -> None:
    rendered.emit("boot", **payload)
    assert material not in rendered.raw, f"credential leaked: {rendered.raw!r}"


@pytest.mark.parametrize(("payload", "material"), _CREDENTIAL_FIELDS)
def test_foreign_stdlib_message_credentials_still_redacted(
    rendered, payload: dict, material: str
) -> None:
    """Secrets interpolated by a stdlib logger go through ``foreign_pre_chain``.

    Third-party libraries (uvicorn, SQLAlchemy, urllib3) never touch structlog;
    their records are formatted by ``ProcessorFormatter``, which runs the same
    redaction processor.  Only the value-side passes apply there, because the
    whole message lands in ``event``.
    """
    import logging as stdlib_logging

    rendered.clear()
    stdlib_logging.getLogger("sqlalchemy.engine").warning(
        "connecting with %s", next(iter(payload.values()))
    )
    assert rendered.raw, "nothing was rendered"
    assert material not in rendered.raw, (
        f"credential leaked via a stdlib logger: {rendered.raw!r}"
    )


# ---------------------------------------------------------------------------
# (g) Published digests survive; near-misses do not.
# ---------------------------------------------------------------------------

_FAKE_DIGEST = "3f" * 32  # 64 lowercase hex chars


def test_rendered_recipe_hash_survives(rendered) -> None:
    """``recipe_hash`` is the handle tying a running artifact to its config."""
    line = rendered.emit("train_done", name="news", recipe_hash=_FAKE_DIGEST)
    assert line["recipe_hash"] == _FAKE_DIGEST, (
        f"recipe_hash destroyed by the hex64 pass; rendered {rendered.raw!r}"
    )


def test_rendered_model_version_survives(rendered) -> None:
    """``model_version`` is already public in the body and response header."""
    value = f"sha256:{_FAKE_DIGEST}"
    line = rendered.emit("model_loaded", model_version=value)
    assert line["model_version"] == value, (
        f"model_version destroyed; rendered {rendered.raw!r}"
    )


def test_rendered_nested_recipe_hash_survives(rendered) -> None:
    line = rendered.emit("train_done", stats={"recipe_hash": _FAKE_DIGEST})
    assert line["stats"]["recipe_hash"] == _FAKE_DIGEST, (
        f"nested recipe_hash destroyed; rendered {rendered.raw!r}"
    )


def test_recipe_hash_from_the_real_producer_survives(rendered, tmp_recipe_yaml) -> None:
    """Pin the exemption to the shape ``_compute_recipe_hash`` actually emits.

    A change to the hash encoding (uppercase hex, a prefix, truncation) would
    fail here rather than silently reinstating redaction of the field.
    """
    from recotem.recipe.loader import load_recipe
    from recotem.training.pipeline import _compute_recipe_hash

    digest = _compute_recipe_hash(load_recipe(tmp_recipe_yaml(name="news")))

    line = rendered.emit("train_done", name="news", recipe_hash=digest)
    assert line["recipe_hash"] == digest, (
        f"real recipe_hash destroyed; rendered {rendered.raw!r}"
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        # Same field name, but not a bare digest -> normal scrubbing applies.
        ("recipe_hash", _FAKE_SIGNING_KEY_HEX.upper()),
        ("recipe_hash", f"{_FAKE_DIGEST} kid=active"),
        ("recipe_hash", f"key:{_FAKE_DIGEST}"),
        # A different field name never inherits the exemption.
        ("artifact_hash", _FAKE_DIGEST),
        ("hash", _FAKE_DIGEST),
        ("model_versions", f"sha256:{_FAKE_DIGEST}"),
    ],
)
def test_digest_exemption_is_exact(rendered, key: str, value: str) -> None:
    """Only the two exact field names, and only a bare digest value."""
    rendered.emit("evt", **{key: value})
    assert "[REDACTED-HEX64]" in rendered.raw, (
        f"{key}={value!r} bypassed the hex64 pass: {rendered.raw!r}"
    )


def test_signing_key_under_the_exempt_field_name_still_redacted(rendered) -> None:
    """A signing key is 64 lowercase hex too -- the shape alone cannot tell.

    The exemption is therefore worth only what the field name is worth: it
    must stay pinned to fields that no code path fills from a credential.
    This test documents that a *plural*/near-miss name does not inherit it,
    and that key-name redaction still fires ahead of the exemption.
    """
    rendered.emit("boot", recotem_signing_keys=f"active:{_FAKE_SIGNING_KEY_HEX}")
    assert _FAKE_SIGNING_KEY_HEX not in rendered.raw, (
        f"signing key leaked: {rendered.raw!r}"
    )


# ---------------------------------------------------------------------------
# (h) Python warnings reach the chain (and therefore the redaction processor).
# ---------------------------------------------------------------------------


def test_warning_is_rendered_as_a_structured_record(rendered) -> None:
    """``warnings.warn`` must not bypass structlog.

    Before ``configure_logging`` called ``logging.captureWarnings(True)``, the
    default ``warnings.showwarning`` wrote straight to stderr: the text was
    unstructured (breaking JSON log shipping) and, more importantly, never
    passed through ``redact_sensitive_keys``.
    """
    import json
    import warnings

    rendered.clear()
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn(
            "artifact was trained on another sklearn",
            UserWarning,
            stacklevel=1,
        )

    assert rendered.raw, "the warning never reached the logging handler"
    for raw_line in rendered.raw.splitlines():
        json.loads(raw_line)  # every emitted line must be valid JSON

    line = next(x for x in rendered.lines() if x.get("logger") == "py.warnings")
    assert line["level"] == "warning"
    assert "artifact was trained on another sklearn" in line["event"]


def test_warning_text_is_redacted(rendered) -> None:
    """A credential a library interpolates into a warning must not survive."""
    import warnings

    rendered.clear()
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn(
            f"reusing cached session key {_FAKE_SIGNING_KEY_HEX} "
            f"and token {_FAKE_API_KEY_B64URL}",
            UserWarning,
            stacklevel=1,
        )

    assert rendered.raw, "the warning never reached the logging handler"
    assert _FAKE_SIGNING_KEY_HEX not in rendered.raw, (
        f"signing key leaked through a warning: {rendered.raw!r}"
    )
    assert _FAKE_API_KEY_B64URL not in rendered.raw, (
        f"api key leaked through a warning: {rendered.raw!r}"
    )


def test_warning_dsn_password_is_redacted(rendered) -> None:
    import warnings

    rendered.clear()
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn(
            "falling back to postgresql://svc:hunter2@db.internal/prod",
            UserWarning,
            stacklevel=1,
        )

    assert rendered.raw, "the warning never reached the logging handler"
    assert "hunter2" not in rendered.raw, (
        f"DSN password leaked through a warning: {rendered.raw!r}"
    )


def test_py_warnings_logger_emits_above_the_root_level(rendered) -> None:
    """``py.warnings`` is pinned to WARNING, not left to inherit the root.

    Without the explicit level a later ``root.setLevel(ERROR)`` would drop
    warnings entirely instead of merely quieting info events.
    """
    import logging as stdlib_logging
    import warnings

    stdlib_logging.getLogger().setLevel(stdlib_logging.ERROR)
    rendered.clear()
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn("still visible", UserWarning, stacklevel=1)

    assert "still visible" in rendered.raw, (
        f"warning suppressed by the root level; rendered {rendered.raw!r}"
    )


# --- exception text is scrubbed on both non-structlog sinks -----------------
#
# Two sinks render an exception's text outside the reach of the first
# redaction pass, and a config error quotes the offending value back -- for
# RECOTEM_SIGNING_KEYS that value IS the signing key:
#
#   1. ``structlog.processors.format_exc_info`` materialises the ``exception``
#      field AFTER ``redact_sensitive_keys`` has already run, so the rendered
#      traceback shipped unscrubbed to the log aggregator while the sibling
#      ``error`` field on the SAME event read ``[REDACTED-HEX64]``.
#   2. ``cli._exit`` writes to stderr with ``typer.echo``, never entering the
#      structlog chain at all.
#
# Both are keyed on a plain ``RuntimeError`` below, not on KeyRingConfigError:
# the defect is "any exception whose message embeds a secret", and a guard
# naming one exception class would not notice the next one.

_SECRET_HEX64 = "ab12cd34" * 8


def test_rendered_exception_field_is_scrubbed(rendered) -> None:
    """The ``exception`` traceback must not carry a secret the ``error`` hides.

    Regression: redaction ran only as the FIRST processor, while
    ``format_exc_info`` (last) created the ``exception`` string afterwards.
    """
    import structlog

    rendered.clear()
    try:
        raise RuntimeError(f"malformed entry '{_SECRET_HEX64}': expected kid:hex")
    except RuntimeError:
        structlog.get_logger("test.redaction").error("boom", exc_info=True)

    line = rendered.lines()[0]
    assert "exception" in line, f"no exception field rendered; got {rendered.raw!r}"
    assert _SECRET_HEX64 not in rendered.raw, (
        "raw secret leaked into the rendered log line via the exception "
        f"traceback; rendered {rendered.raw!r}"
    )
    assert "[REDACTED-HEX64]" in line["exception"], (
        "the exception field must be scrubbed in place, not dropped -- the "
        f"traceback is the operator's diagnostic; got {line['exception']!r}"
    )
    # The traceback must survive as a traceback, not be flattened away.
    assert "RuntimeError" in line["exception"]


def test_cli_exit_scrubs_secret_from_stderr(capsys) -> None:
    """``_exit`` writes outside structlog, so it must scrub explicitly."""
    import typer

    from recotem.cli import _exit

    message = f"Training failed: malformed KeyRing entry '{_SECRET_HEX64}'"
    with pytest.raises(typer.Exit):
        _exit(8, message)

    err = capsys.readouterr().err
    assert _SECRET_HEX64 not in err, (
        f"raw secret leaked to stderr via cli._exit; stderr was {err!r}"
    )
    assert "[REDACTED-HEX64]" in err, (
        f"_exit must keep the diagnostic, scrubbed; stderr was {err!r}"
    )


def test_redact_text_is_idempotent() -> None:
    """A second pass over already-scrubbed text must not corrupt it.

    The processor now runs twice in the chain, so non-idempotence would
    double-redact or mangle the placeholder.
    """
    from recotem.log_redaction import redact_text

    once = redact_text(f"key '{_SECRET_HEX64}' is bad")
    assert once == redact_text(once)
    assert "[REDACTED-HEX64]" in once
