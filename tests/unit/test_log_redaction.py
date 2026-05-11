"""Unit tests for recotem.log_redaction.

Tests:
- Strips API/signing keys
- Strips AWS/Google creds
- Handles nested dicts/lists
- Must be first-in-chain (processor signature)
"""

from __future__ import annotations

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
