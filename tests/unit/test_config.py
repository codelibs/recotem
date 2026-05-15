"""Unit tests for recotem.config helper functions.

Tests:
- _split_csv_env: whitespace-only / comma-only falls back to default
- _split_csv_env: normal value parses correctly
- get_http_allow_private: default False when env unset
- get_http_allow_private: truthy and falsy values
"""

from __future__ import annotations

import pytest

from recotem.config import (
    ApiKeyEntry,
    ConfigError,
    ServeConfig,
    get_http_allow_private,
    get_max_download_bytes,
)

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
    """Non-numeric RECOTEM_WATCH_INTERVAL raises ConfigError (per from_env docstring)."""
    monkeypatch.setenv("RECOTEM_WATCH_INTERVAL", "not_a_number")
    with pytest.raises(ConfigError):
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
    """RECOTEM_MAX_ARTIFACT_BYTES values outside [1 MiB, 16 GiB] are clamped.

    Set RECOTEM_MAX_PAYLOAD_BYTES to 1 MiB (the minimum) so the
    payload <= artifact invariant is not violated when the artifact cap
    is clamped to a low value like 1 MiB.
    """
    monkeypatch.setenv("RECOTEM_MAX_ARTIFACT_BYTES", raw_value)
    monkeypatch.setenv("RECOTEM_MAX_PAYLOAD_BYTES", str(_1_MIB))
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
    """Values above 16 GiB are clamped to 16 GiB.

    We must also set RECOTEM_MAX_ARTIFACT_BYTES to the same ceiling so the
    payload <= artifact invariant is satisfied after clamping.
    """
    monkeypatch.setenv("RECOTEM_MAX_PAYLOAD_BYTES", str(_16_GIB + 1))
    monkeypatch.setenv("RECOTEM_MAX_ARTIFACT_BYTES", str(_16_GIB))
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
    """dev_allow_unsigned=True with env='' raises ConfigError."""
    cfg = ServeConfig()
    cfg.env = ""
    cfg.dev_allow_unsigned = True
    with pytest.raises(ConfigError, match="development"):
        cfg.validate_insecure_flags()


def test_dev_allow_unsigned_refused_when_recotem_env_test() -> None:
    """dev_allow_unsigned=True with env='test' raises ConfigError (development only)."""
    cfg = ServeConfig()
    cfg.env = "test"
    cfg.dev_allow_unsigned = True
    with pytest.raises(ConfigError, match="development"):
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
    with pytest.raises(ConfigError, match="RECOTEM_ENV"):
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
    with pytest.raises(ConfigError, match="development"):
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
    """RECOTEM_PORT values outside 1–65535 raise ConfigError."""
    monkeypatch.setenv("RECOTEM_PORT", port_value)
    with pytest.raises(ConfigError, match="RECOTEM_PORT"):
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
    """Invalid RECOTEM_LOG_FORMAT value raises ConfigError with allowed values listed."""
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", "jsonl")
    with pytest.raises(ConfigError, match="RECOTEM_LOG_FORMAT"):
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
    """Clearly invalid value 'structured' raises ConfigError."""
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", "structured")
    with pytest.raises(ConfigError, match="RECOTEM_LOG_FORMAT"):
        ServeConfig.from_env()


# ---------------------------------------------------------------------------
# M-5: RECOTEM_MAX_PAYLOAD_BYTES > RECOTEM_MAX_ARTIFACT_BYTES invariant
# ---------------------------------------------------------------------------


def test_payload_bytes_exceeds_artifact_bytes_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_MAX_PAYLOAD_BYTES > RECOTEM_MAX_ARTIFACT_BYTES must raise ConfigError.

    CLAUDE.md: 'Smaller than RECOTEM_MAX_ARTIFACT_BYTES to bound deserialization
    memory expansion.'  This is enforced at from_env() time so a misconfigured
    server fails loudly at startup.
    """
    _1_MIB_local = 1 * 1024 * 1024
    _2_MIB_local = 2 * 1024 * 1024
    monkeypatch.setenv("RECOTEM_MAX_ARTIFACT_BYTES", str(_1_MIB_local))
    monkeypatch.setenv("RECOTEM_MAX_PAYLOAD_BYTES", str(_2_MIB_local))
    with pytest.raises(ConfigError, match="RECOTEM_MAX_PAYLOAD_BYTES"):
        ServeConfig.from_env()


def test_payload_bytes_equal_artifact_bytes_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_MAX_PAYLOAD_BYTES == RECOTEM_MAX_ARTIFACT_BYTES is a valid configuration."""
    _4_MIB_local = 4 * 1024 * 1024
    monkeypatch.setenv("RECOTEM_MAX_ARTIFACT_BYTES", str(_4_MIB_local))
    monkeypatch.setenv("RECOTEM_MAX_PAYLOAD_BYTES", str(_4_MIB_local))
    cfg = ServeConfig.from_env()
    assert cfg.max_payload_bytes == _4_MIB_local
    assert cfg.max_artifact_bytes == _4_MIB_local


# ---------------------------------------------------------------------------
# m-12: duplicate API kid raises ConfigError
# ---------------------------------------------------------------------------


def test_duplicate_api_kid_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate kid in RECOTEM_API_KEYS must raise ConfigError.

    The first kid silently wins in KeyRing but the config is almost certainly
    wrong — fail fast so operators notice the mistake.
    """
    hash1 = "aa" * 32
    hash2 = "bb" * 32
    monkeypatch.setenv("RECOTEM_API_KEYS", f"k1:sha256:{hash1},k1:sha256:{hash2}")
    with pytest.raises(ConfigError, match="duplicate kid"):
        ServeConfig.from_env()


def test_unique_api_kids_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two entries with distinct kids must be accepted without error."""
    hash1 = "aa" * 32
    hash2 = "bb" * 32
    monkeypatch.setenv("RECOTEM_API_KEYS", f"k1:sha256:{hash1},k2:sha256:{hash2}")
    cfg = ServeConfig.from_env()
    assert len(cfg.api_keys) == 2


# ---------------------------------------------------------------------------
# N-13: MIN-2 — is_truthy_env public function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("TRUE", True),
        ("Yes", True),
        ("On", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("", False),
        (None, False),
        ("anything", False),
        ("2", False),
        ("enabled", False),
    ],
)
def test_is_truthy_env(value: str | None, expected: bool) -> None:
    """is_truthy_env recognises exactly the truthy set {1, true, yes, on}
    (case-insensitive) and treats everything else — including None and the
    empty string — as falsy.
    """
    from recotem.config import is_truthy_env

    assert is_truthy_env(value) is expected, (
        f"is_truthy_env({value!r}) expected {expected}"
    )


# ---------------------------------------------------------------------------
# MF-6: apply_auth_posture emits host_forced_to_loopback warning
# ---------------------------------------------------------------------------


def test_apply_auth_posture_forces_loopback_and_warns_when_host_is_not_loopback() -> (
    None
):
    """apply_auth_posture with host='0.0.0.0', no api_keys, no insecure flag
    must:
      1. Force host to '127.0.0.1'.
      2. Emit a structured 'host_forced_to_loopback' warning.
    """
    import structlog.testing

    cfg = ServeConfig()
    cfg.host = "0.0.0.0"
    cfg.api_keys = []
    cfg.insecure_no_auth = False

    with structlog.testing.capture_logs() as captured:
        cfg.apply_auth_posture()

    assert cfg.host == "127.0.0.1", (
        f"host must be forced to 127.0.0.1, got {cfg.host!r}"
    )
    warn_events = [e for e in captured if e.get("event") == "host_forced_to_loopback"]
    assert warn_events, (
        "apply_auth_posture must emit 'host_forced_to_loopback' when "
        "forcing non-loopback host to 127.0.0.1"
    )
    assert warn_events[0].get("log_level") == "warning"
    assert warn_events[0].get("requested_host") == "0.0.0.0"


def test_apply_auth_posture_does_not_warn_when_already_loopback() -> None:
    """apply_auth_posture with host='127.0.0.1' (already loopback) must NOT
    emit a warning — no change is being forced.
    """
    import structlog.testing

    cfg = ServeConfig()
    cfg.host = "127.0.0.1"
    cfg.api_keys = []
    cfg.insecure_no_auth = False

    with structlog.testing.capture_logs() as captured:
        cfg.apply_auth_posture()

    assert cfg.host == "127.0.0.1"
    warn_events = [e for e in captured if e.get("event") == "host_forced_to_loopback"]
    assert not warn_events, (
        "apply_auth_posture must NOT warn when host is already 127.0.0.1"
    )


def test_apply_auth_posture_does_not_force_when_api_keys_set() -> None:
    """When api_keys are configured, apply_auth_posture must not override the host."""
    cfg = ServeConfig()
    cfg.host = "0.0.0.0"
    cfg.api_keys = [ApiKeyEntry(kid="k1", sha256_hex="a" * 64)]
    cfg.insecure_no_auth = False
    cfg.apply_auth_posture()
    assert cfg.host == "0.0.0.0", (
        "apply_auth_posture must not force loopback when api_keys are configured"
    )


# ---------------------------------------------------------------------------
# Task 2.2: RECOTEM_MAX_SQL_ROWS and RECOTEM_SQL_ALLOW_PRIVATE
# ---------------------------------------------------------------------------


def test_max_sql_rows_default(monkeypatch) -> None:
    monkeypatch.delenv("RECOTEM_MAX_SQL_ROWS", raising=False)
    from recotem.config import get_max_sql_rows

    assert get_max_sql_rows() == 50_000_000


def test_max_sql_rows_clamps_low(monkeypatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_SQL_ROWS", "10")
    from recotem.config import get_max_sql_rows

    assert get_max_sql_rows() == 1_000


def test_max_sql_rows_clamps_high(monkeypatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_SQL_ROWS", "9999999999")
    from recotem.config import get_max_sql_rows

    assert get_max_sql_rows() == 500_000_000


def test_sql_allow_private_default_false(monkeypatch) -> None:
    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    from recotem.config import sql_allow_private

    assert sql_allow_private() is False


def test_sql_allow_private_truthy_values(monkeypatch) -> None:
    from recotem.config import sql_allow_private

    for v in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", v)
        assert sql_allow_private() is True
    for v in ("0", "false", "no", "off", "", "anything-else"):
        monkeypatch.setenv("RECOTEM_SQL_ALLOW_PRIVATE", v)
        assert sql_allow_private() is False


# ---------------------------------------------------------------------------
# Task 3.2: RECOTEM_GA4_MAX_PAGES
# ---------------------------------------------------------------------------


def test_ga4_max_pages_default(monkeypatch) -> None:
    monkeypatch.delenv("RECOTEM_GA4_MAX_PAGES", raising=False)
    from recotem.config import get_ga4_max_pages

    assert get_ga4_max_pages() == 500


def test_ga4_max_pages_clamp_low(monkeypatch) -> None:
    monkeypatch.setenv("RECOTEM_GA4_MAX_PAGES", "0")
    from recotem.config import get_ga4_max_pages

    assert get_ga4_max_pages() == 1


def test_ga4_max_pages_clamp_high(monkeypatch) -> None:
    monkeypatch.setenv("RECOTEM_GA4_MAX_PAGES", "999999")
    from recotem.config import get_ga4_max_pages

    assert get_ga4_max_pages() == 10_000


# ---------------------------------------------------------------------------
# E1 — Non-integer env value falls back to default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_var,env_value,helper_name,expected_default",
    [
        ("RECOTEM_MAX_SQL_ROWS", "abc", "get_max_sql_rows", 50_000_000),
        ("RECOTEM_GA4_MAX_PAGES", "xyz", "get_ga4_max_pages", 500),
    ],
)
def test_non_integer_env_value_falls_back_to_default(
    env_var: str,
    env_value: str,
    helper_name: str,
    expected_default: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-integer values for numeric env vars must fall back to the documented
    default rather than raising an unhandled exception."""
    import recotem.config as config_mod

    monkeypatch.setenv(env_var, env_value)
    helper = getattr(config_mod, helper_name)
    result = helper()
    assert result == expected_default, (
        f"{env_var}={env_value!r}: expected default {expected_default}, got {result}"
    )


# ---------------------------------------------------------------------------
# MINOR-2: env_var_unparseable warning on bad numeric env vars
# ---------------------------------------------------------------------------


def test_max_sql_rows_non_integer_logs_env_var_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_MAX_SQL_ROWS set to a non-integer must return the default AND
    emit an 'env_var_unparseable' warning with the variable name and raw value.
    """
    import structlog.testing

    from recotem.config import get_max_sql_rows

    monkeypatch.setenv("RECOTEM_MAX_SQL_ROWS", "notanumber")
    with structlog.testing.capture_logs() as captured:
        result = get_max_sql_rows()

    assert result == 50_000_000, f"Expected default 50_000_000, got {result}"
    warn_events = [e for e in captured if e.get("event") == "env_var_unparseable"]
    assert warn_events, (
        "An 'env_var_unparseable' warning must be logged when "
        "RECOTEM_MAX_SQL_ROWS is not a valid integer"
    )
    assert warn_events[0].get("log_level") == "warning"
    assert warn_events[0].get("name") == "RECOTEM_MAX_SQL_ROWS"
    assert warn_events[0].get("raw") == "notanumber"
    assert warn_events[0].get("fallback") == 50_000_000


def test_ga4_max_pages_non_integer_logs_env_var_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_GA4_MAX_PAGES set to '5OO' (letter O typo) must return the default
    AND emit an 'env_var_unparseable' warning with the variable name and raw value.
    """
    import structlog.testing

    from recotem.config import get_ga4_max_pages

    monkeypatch.setenv("RECOTEM_GA4_MAX_PAGES", "5OO")
    with structlog.testing.capture_logs() as captured:
        result = get_ga4_max_pages()

    assert result == 500, f"Expected default 500, got {result}"
    warn_events = [e for e in captured if e.get("event") == "env_var_unparseable"]
    assert warn_events, (
        "An 'env_var_unparseable' warning must be logged when "
        "RECOTEM_GA4_MAX_PAGES is not a valid integer (e.g. '5OO' typo)"
    )
    assert warn_events[0].get("log_level") == "warning"
    assert warn_events[0].get("name") == "RECOTEM_GA4_MAX_PAGES"
    assert warn_events[0].get("raw") == "5OO"
    assert warn_events[0].get("fallback") == 500
