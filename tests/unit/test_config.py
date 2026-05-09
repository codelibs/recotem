"""Unit tests for recotem.config helper functions.

Tests:
- _split_csv_env: whitespace-only / comma-only falls back to default
- _split_csv_env: normal value parses correctly
- get_http_allow_private: default False when env unset
- get_http_allow_private: truthy and falsy values
"""

from __future__ import annotations

import pytest

from recotem.config import ServeConfig, get_http_allow_private

# ---------------------------------------------------------------------------
# A1. _split_csv_env whitespace-only falls back to default
# ---------------------------------------------------------------------------


def test_split_csv_env_whitespace_only_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_ALLOWED_HOSTS set to only spaces and commas must fall back to
    the documented default ['127.0.0.1', 'localhost'].

    Passing [] to TrustedHostMiddleware is a security footgun — it accepts all
    hosts.  The fix is that _split_csv_env returns *default* when the parsed
    list is empty after stripping.
    """
    monkeypatch.setenv("RECOTEM_ALLOWED_HOSTS", " , , ")
    cfg = ServeConfig.from_env()
    assert cfg.allowed_hosts == ["127.0.0.1", "localhost"], (
        f"whitespace-only RECOTEM_ALLOWED_HOSTS must fall back to default, "
        f"got {cfg.allowed_hosts!r}"
    )


# ---------------------------------------------------------------------------
# A2. _split_csv_env normal value parses (strip + skip empty)
# ---------------------------------------------------------------------------


def test_split_csv_env_normal_value_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comma-separated value with surrounding spaces is stripped and split."""
    monkeypatch.setenv("RECOTEM_ALLOWED_HOSTS", "example.com, foo.bar ,baz")
    cfg = ServeConfig.from_env()
    assert cfg.allowed_hosts == ["example.com", "foo.bar", "baz"]


# ---------------------------------------------------------------------------
# A3. get_http_allow_private default False when env unset
# ---------------------------------------------------------------------------


def test_get_http_allow_private_default_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """When RECOTEM_HTTP_ALLOW_PRIVATE is unset, get_http_allow_private returns False.

    The conftest autouse fixture sets RECOTEM_HTTP_ALLOW_PRIVATE=1 for all
    tests, so we must explicitly delete it here before checking the default.
    """
    monkeypatch.delenv("RECOTEM_HTTP_ALLOW_PRIVATE", raising=False)
    assert get_http_allow_private() is False


# ---------------------------------------------------------------------------
# A4. get_http_allow_private truthy and falsy values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_get_http_allow_private_truthy_values(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Truthy strings enable private-host fetches."""
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", value)
    assert get_http_allow_private() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_get_http_allow_private_falsy_values(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falsy strings (including empty) keep private-host fetches disabled."""
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", value)
    assert get_http_allow_private() is False
