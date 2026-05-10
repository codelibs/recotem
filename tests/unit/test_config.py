"""Unit tests for recotem.config helper functions.

Tests:
- _split_csv_env: whitespace-only / comma-only falls back to default
- _split_csv_env: normal value parses correctly
- get_http_allow_private: default False when env unset
- get_http_allow_private: truthy and falsy values
"""

from __future__ import annotations

import pytest

from recotem.config import ServeConfig, get_http_allow_private, get_max_download_bytes

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


# ---------------------------------------------------------------------------
# MAJOR-7: RECOTEM_WATCH_INTERVAL clamping [1, 30]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_value,expected",
    [
        ("0", 1.0),  # below minimum -> clamped to 1
        ("0.001", 1.0),  # very small -> clamped to 1
        ("-5", 1.0),  # negative -> clamped to 1
        ("1", 1.0),  # exact minimum -> unchanged
        ("5", 5.0),  # default value -> unchanged
        ("15", 15.0),  # mid-range -> unchanged
        ("30", 30.0),  # exact maximum -> unchanged
        ("31", 30.0),  # above maximum -> clamped to 30
        ("9999", 30.0),  # far above maximum -> clamped to 30
        ("100", 30.0),  # above maximum -> clamped to 30
    ],
)
def test_watch_interval_clamped_within_1_30(
    raw_value: str,
    expected: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_WATCH_INTERVAL values outside [1, 30] are clamped."""
    monkeypatch.setenv("RECOTEM_WATCH_INTERVAL", raw_value)
    cfg = ServeConfig.from_env()
    assert cfg.watch_interval == expected, (
        f"RECOTEM_WATCH_INTERVAL={raw_value!r}: "
        f"expected {expected}, got {cfg.watch_interval}"
    )


def test_watch_interval_non_numeric_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-numeric RECOTEM_WATCH_INTERVAL raises ValueError (per from_env docstring)."""
    monkeypatch.setenv("RECOTEM_WATCH_INTERVAL", "not_a_number")
    with pytest.raises(ValueError):
        ServeConfig.from_env()


def test_watch_interval_unset_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """When RECOTEM_WATCH_INTERVAL is unset, the default (5s) is used."""
    monkeypatch.delenv("RECOTEM_WATCH_INTERVAL", raising=False)
    cfg = ServeConfig.from_env()
    assert cfg.watch_interval == 5.0


# ---------------------------------------------------------------------------
# Download byte-cap clamping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_value,expected_min,expected_max",
    [
        ("1", 1 * 1024 * 1024, 16 * 1024 * 1024 * 1024),  # "1" clamps to 1 MiB
        ("0", 1 * 1024 * 1024, 16 * 1024 * 1024 * 1024),  # "0" clamps to 1 MiB
    ],
)
def test_get_max_download_bytes_clamps_small_values(
    raw_value: str,
    expected_min: int,
    expected_max: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Very small RECOTEM_MAX_DOWNLOAD_BYTES values are clamped to 1 MiB."""
    monkeypatch.setenv("RECOTEM_MAX_DOWNLOAD_BYTES", raw_value)
    result = get_max_download_bytes()
    assert result >= expected_min, (
        f"RECOTEM_MAX_DOWNLOAD_BYTES={raw_value!r}: expected >= {expected_min}, got {result}"
    )
    assert result <= expected_max
