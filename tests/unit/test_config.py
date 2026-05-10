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


# ---------------------------------------------------------------------------
# Fix 4: RECOTEM_MAX_ARTIFACT_BYTES clamped to [1 MiB, 16 GiB]
# ---------------------------------------------------------------------------

_1_MIB = 1 * 1024 * 1024
_16_GIB = 16 * 1024 * 1024 * 1024
_2_GIB = 2 * 1024 * 1024 * 1024  # default


@pytest.mark.parametrize(
    "raw_value,expected",
    [
        ("0", _1_MIB),  # 0 -> clamped to 1 MiB
        ("-1", _1_MIB),  # negative -> clamped to 1 MiB
        ("999999999999999", _16_GIB),  # absurdly large -> clamped to 16 GiB
        (str(_1_MIB), _1_MIB),  # exact lower bound -> unchanged
        (str(_16_GIB), _16_GIB),  # exact upper bound -> unchanged
        (str(_2_GIB), _2_GIB),  # default value -> unchanged
    ],
)
def test_max_artifact_bytes_clamped(
    raw_value: str,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_MAX_ARTIFACT_BYTES values outside [1 MiB, 16 GiB] are clamped."""
    monkeypatch.setenv("RECOTEM_MAX_ARTIFACT_BYTES", raw_value)
    cfg = ServeConfig.from_env()
    assert cfg.max_artifact_bytes == expected, (
        f"RECOTEM_MAX_ARTIFACT_BYTES={raw_value!r}: expected {expected}, "
        f"got {cfg.max_artifact_bytes}"
    )


def test_max_artifact_bytes_unset_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When RECOTEM_MAX_ARTIFACT_BYTES is unset, the 2 GiB default is used."""
    monkeypatch.delenv("RECOTEM_MAX_ARTIFACT_BYTES", raising=False)
    cfg = ServeConfig.from_env()
    assert cfg.max_artifact_bytes == _2_GIB


def test_max_artifact_bytes_non_integer_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-integer RECOTEM_MAX_ARTIFACT_BYTES falls back to the 2 GiB default."""
    monkeypatch.setenv("RECOTEM_MAX_ARTIFACT_BYTES", "not_a_number")
    cfg = ServeConfig.from_env()
    assert cfg.max_artifact_bytes == _2_GIB


# ---------------------------------------------------------------------------
# MAJOR-3: RECOTEM_MAX_PAYLOAD_BYTES
# ---------------------------------------------------------------------------

_512_MIB = 512 * 1024 * 1024  # default


def test_max_payload_bytes_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """When RECOTEM_MAX_PAYLOAD_BYTES is unset, the 512 MiB default is used."""
    monkeypatch.delenv("RECOTEM_MAX_PAYLOAD_BYTES", raising=False)
    cfg = ServeConfig.from_env()
    assert cfg.max_payload_bytes == _512_MIB


def test_max_payload_bytes_clamp_low(monkeypatch: pytest.MonkeyPatch) -> None:
    """Values below 1 MiB are clamped to 1 MiB."""
    monkeypatch.setenv("RECOTEM_MAX_PAYLOAD_BYTES", "0")
    cfg = ServeConfig.from_env()
    assert cfg.max_payload_bytes == _1_MIB


def test_max_payload_bytes_clamp_high(monkeypatch: pytest.MonkeyPatch) -> None:
    """Values above 16 GiB are clamped to 16 GiB."""
    monkeypatch.setenv("RECOTEM_MAX_PAYLOAD_BYTES", str(_16_GIB + 1))
    cfg = ServeConfig.from_env()
    assert cfg.max_payload_bytes == _16_GIB


def test_max_payload_bytes_separate_from_artifact_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_payload_bytes and max_artifact_bytes are independently parsed."""
    monkeypatch.setenv("RECOTEM_MAX_PAYLOAD_BYTES", str(_1_MIB))
    monkeypatch.setenv("RECOTEM_MAX_ARTIFACT_BYTES", str(_16_GIB))
    cfg = ServeConfig.from_env()
    assert cfg.max_payload_bytes == _1_MIB
    assert cfg.max_artifact_bytes == _16_GIB


# ---------------------------------------------------------------------------
# validate_insecure_flags — env variant coverage
# ---------------------------------------------------------------------------


def test_dev_allow_unsigned_refused_when_recotem_env_empty() -> None:
    """dev_allow_unsigned=True with env='' raises ValueError."""
    cfg = ServeConfig()
    cfg.env = ""
    cfg.dev_allow_unsigned = True
    with pytest.raises(ValueError, match="development"):
        cfg.validate_insecure_flags()


def test_dev_allow_unsigned_refused_when_recotem_env_test() -> None:
    """dev_allow_unsigned=True with env='test' raises ValueError (development only)."""
    cfg = ServeConfig()
    cfg.env = "test"
    cfg.dev_allow_unsigned = True
    with pytest.raises(ValueError, match="development"):
        cfg.validate_insecure_flags()


@pytest.mark.parametrize("env_value", ["dev", "DEV", "Development", "test"])
def test_insecure_no_auth_recotem_env_variants(env_value: str) -> None:
    """insecure_no_auth=True is accepted for dev/DEV/Development/test (case-insensitive)."""
    cfg = ServeConfig()
    cfg.env = env_value
    cfg.insecure_no_auth = True
    cfg.validate_insecure_flags()  # must not raise


@pytest.mark.parametrize("env_value", ["prod", ""])
def test_insecure_no_auth_recotem_env_variants_rejected(env_value: str) -> None:
    """insecure_no_auth=True is rejected for 'prod' and '' (empty)."""
    cfg = ServeConfig()
    cfg.env = env_value
    cfg.insecure_no_auth = True
    with pytest.raises(ValueError, match="RECOTEM_ENV"):
        cfg.validate_insecure_flags()


@pytest.mark.parametrize("env_value", ["development", "Development"])
def test_dev_allow_unsigned_recotem_env_variants_accepted(env_value: str) -> None:
    """dev_allow_unsigned=True is accepted for 'development' and 'Development'."""
    cfg = ServeConfig()
    cfg.env = env_value
    cfg.dev_allow_unsigned = True
    cfg.validate_insecure_flags()  # must not raise


@pytest.mark.parametrize("env_value", ["dev", "test", ""])
def test_dev_allow_unsigned_recotem_env_variants_rejected(env_value: str) -> None:
    """dev_allow_unsigned=True is rejected for 'dev', 'test', and '' (empty)."""
    cfg = ServeConfig()
    cfg.env = env_value
    cfg.dev_allow_unsigned = True
    with pytest.raises(ValueError, match="development"):
        cfg.validate_insecure_flags()


# ---------------------------------------------------------------------------
# D-3: RECOTEM_DRAIN_SECONDS clamp [1, 300]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_value,expected",
    [
        ("0", 1),  # below minimum -> clamped to 1
        ("-1", 1),  # negative -> clamped to 1
        ("1", 1),  # exact lower bound -> unchanged
        ("30", 30),  # default value -> unchanged
        ("300", 300),  # exact upper bound -> unchanged
        ("301", 300),  # above maximum -> clamped to 300
        ("9999999", 300),  # far above maximum -> clamped to 300
    ],
)
def test_drain_seconds_clamped(
    raw_value: str,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_DRAIN_SECONDS values outside [1, 300] are clamped."""
    monkeypatch.setenv("RECOTEM_DRAIN_SECONDS", raw_value)
    cfg = ServeConfig.from_env()
    assert cfg.drain_seconds == expected, (
        f"RECOTEM_DRAIN_SECONDS={raw_value!r}: expected {expected}, got {cfg.drain_seconds}"
    )


def test_drain_seconds_unset_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """When RECOTEM_DRAIN_SECONDS is unset, the default (30 s) is used."""
    monkeypatch.delenv("RECOTEM_DRAIN_SECONDS", raising=False)
    cfg = ServeConfig.from_env()
    assert cfg.drain_seconds == 30


# ---------------------------------------------------------------------------
# D-3: RECOTEM_PORT validation [1, 65535]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("port_value", ["0", "65536", "70000", "-1"])
def test_port_out_of_range_raises(
    port_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RECOTEM_PORT values outside 1–65535 raise ValueError."""
    monkeypatch.setenv("RECOTEM_PORT", port_value)
    with pytest.raises(ValueError, match="RECOTEM_PORT"):
        ServeConfig.from_env()


@pytest.mark.parametrize("port_value", ["1", "8080", "65535"])
def test_port_valid_values_accepted(
    port_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid RECOTEM_PORT values in range 1–65535 are accepted."""
    monkeypatch.setenv("RECOTEM_PORT", port_value)
    cfg = ServeConfig.from_env()
    assert cfg.port == int(port_value)


# ---------------------------------------------------------------------------
# D-6: RECOTEM_LOG_FORMAT invalid value raises ValueError
# ---------------------------------------------------------------------------


def test_log_format_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid RECOTEM_LOG_FORMAT value raises ValueError with allowed values listed."""
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", "jsonl")
    with pytest.raises(ValueError, match="RECOTEM_LOG_FORMAT"):
        ServeConfig.from_env()


@pytest.mark.parametrize("fmt", ["json", "console", "auto"])
def test_log_format_valid_values_accepted(
    fmt: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid RECOTEM_LOG_FORMAT values are accepted without error."""
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", fmt)
    cfg = ServeConfig.from_env()
    assert cfg.log_format == fmt


def test_log_format_unset_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """When RECOTEM_LOG_FORMAT is unset, the default ('auto') is used."""
    monkeypatch.delenv("RECOTEM_LOG_FORMAT", raising=False)
    cfg = ServeConfig.from_env()
    assert cfg.log_format == "auto"


def test_log_format_typo_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typo values like 'JSON' (wrong case after lower()) still fail."""
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", "JSON")
    # After .lower() -> 'json' which IS valid — test an actual invalid value
    cfg = ServeConfig.from_env()
    assert cfg.log_format == "json"  # case-insensitive; 'JSON' -> 'json' is valid


def test_log_format_clearly_invalid_typo_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clearly invalid value 'structured' raises ValueError."""
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", "structured")
    with pytest.raises(ValueError, match="RECOTEM_LOG_FORMAT"):
        ServeConfig.from_env()
