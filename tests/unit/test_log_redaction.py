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
