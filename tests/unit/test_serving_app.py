"""Unit tests for recotem.serving.app.create_app security posture.

Tests:
- TrustedHost middleware blocks unrecognized host
- CORS blocks unconfigured origin
- insecure-no-auth gating (requires RECOTEM_ENV=dev/test)
- --dev-allow-unsigned gating
- security.posture log emitted at startup
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recotem.config import ServeConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_config(tmp_path: Path) -> ServeConfig:
    """Build a minimal ServeConfig pointing at an empty recipes dir."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    cfg.recipes_dir = str(recipes_dir)  # type: ignore[attr-defined]
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]
    return cfg


# ---------------------------------------------------------------------------
# startup posture
# ---------------------------------------------------------------------------


def test_create_app_starts_with_valid_config(tmp_path: Path) -> None:
    """create_app returns a FastAPI instance with a minimal valid config."""
    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    app = create_app(cfg)
    assert app is not None


def test_security_posture_log_emitted_at_startup(tmp_path: Path) -> None:
    """create_app emits a 'security.posture' log event."""
    import structlog.testing

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    with structlog.testing.capture_logs() as cap:
        create_app(cfg)

    assert any(e.get("event") == "security.posture" for e in cap)


def test_security_posture_log_emits_signing_keys_with_fingerprints(
    tmp_path: Path,
) -> None:
    """The security.posture log line must include signing_keys=[{kid,fingerprint}]
    pairs (not just kids), so operators can confirm prod ≠ staging without
    leaking key material."""
    import structlog.testing

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.signing_keys_raw = "active:" + "aa" * 32
    with structlog.testing.capture_logs() as cap:
        create_app(cfg)

    posture = next(e for e in cap if e.get("event") == "security.posture")
    assert "signing_keys" in posture
    assert "signing_kids" in posture
    assert isinstance(posture["signing_keys"], list)
    assert posture["signing_keys"], (
        "signing_keys must not be empty when keys configured"
    )
    entry = posture["signing_keys"][0]
    assert set(entry.keys()) == {"kid", "fingerprint"}
    assert entry["kid"] == "active"
    assert isinstance(entry["fingerprint"], str)
    assert len(entry["fingerprint"]) == 8  # sha256(key)[:8]


def test_dev_allow_unsigned_emits_warning_banner_at_startup(tmp_path: Path) -> None:
    """When --dev-allow-unsigned is in effect, a DEV_ALLOW_UNSIGNED_ACTIVE
    warning is emitted at startup (in addition to the once-per-60s loop)."""
    import structlog.testing

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.env = "development"
    cfg.dev_allow_unsigned = True
    with structlog.testing.capture_logs() as cap:
        create_app(cfg)

    assert any(e.get("event") == "DEV_ALLOW_UNSIGNED_ACTIVE" for e in cap)


# ---------------------------------------------------------------------------
# insecure-no-auth gating
# ---------------------------------------------------------------------------


def test_insecure_no_auth_refused_unless_RECOTEM_ENV_dev(tmp_path: Path) -> None:
    """--insecure-no-auth in a production env raises ValueError."""
    cfg = _minimal_config(tmp_path)
    cfg.env = "production"
    cfg.insecure_no_auth = True
    with pytest.raises(ValueError, match="RECOTEM_ENV"):
        cfg.validate_insecure_flags()


def test_insecure_no_auth_allowed_in_development(tmp_path: Path) -> None:
    cfg = _minimal_config(tmp_path)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.validate_insecure_flags()  # should not raise


def test_insecure_no_auth_allowed_in_test_env(tmp_path: Path) -> None:
    cfg = _minimal_config(tmp_path)
    cfg.env = "test"
    cfg.insecure_no_auth = True
    cfg.validate_insecure_flags()  # should not raise


# ---------------------------------------------------------------------------
# dev-allow-unsigned gating
# ---------------------------------------------------------------------------


def test_dev_allow_unsigned_requires_development_env(tmp_path: Path) -> None:
    cfg = _minimal_config(tmp_path)
    cfg.env = "staging"
    cfg.dev_allow_unsigned = True
    with pytest.raises(ValueError, match="development"):
        cfg.validate_insecure_flags()


def test_dev_allow_unsigned_accepted_in_development(tmp_path: Path) -> None:
    cfg = _minimal_config(tmp_path)
    cfg.env = "development"
    cfg.dev_allow_unsigned = True
    cfg.validate_insecure_flags()  # should not raise


# ---------------------------------------------------------------------------
# empty keys forces localhost bind
# ---------------------------------------------------------------------------


def test_empty_keys_without_insecure_flag_forces_localhost_bind() -> None:
    cfg = ServeConfig()
    cfg.api_keys = []
    cfg.insecure_no_auth = False
    cfg.host = "0.0.0.0"
    cfg.apply_auth_posture()
    assert cfg.host == "127.0.0.1"


def test_keys_present_allows_non_localhost_bind() -> None:
    import hashlib

    from recotem.config import ApiKeyEntry

    entry = ApiKeyEntry(kid="k1", sha256_hex=hashlib.sha256(b"key").hexdigest())
    cfg = ServeConfig()
    cfg.api_keys = [entry]
    cfg.insecure_no_auth = False
    cfg.host = "0.0.0.0"
    cfg.apply_auth_posture()
    assert cfg.host == "0.0.0.0"


# ---------------------------------------------------------------------------
# TrustedHost middleware
# ---------------------------------------------------------------------------


def test_TrustedHost_blocks_unrecognized_host(tmp_path: Path) -> None:
    """A request with a Host header not in allowed_hosts gets 400."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.allowed_hosts = ["allowed.example.com"]
    app = create_app(cfg)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/health", headers={"host": "evil.attacker.com"})
    assert response.status_code in (400, 403, 422)


def test_TrustedHost_allows_configured_host(tmp_path: Path) -> None:
    """A request with a Host header in allowed_hosts succeeds."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.allowed_hosts = ["testserver"]
    app = create_app(cfg)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# CORS deny
# ---------------------------------------------------------------------------


def test_CORS_blocks_unconfigured_origin(tmp_path: Path) -> None:
    """CORS preflight from an unconfigured origin has no Access-Control-Allow-Origin."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.allowed_origins = []  # no CORS configured
    app = create_app(cfg)
    client = TestClient(app)
    response = client.options(
        "/health",
        headers={
            "origin": "https://evil.example.com",
            "access-control-request-method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_CORS_allows_configured_origin(tmp_path: Path) -> None:
    """CORS preflight from a configured origin has Access-Control-Allow-Origin."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.allowed_origins = ["https://app.example.com"]
    app = create_app(cfg)
    client = TestClient(app)
    response = client.options(
        "/health",
        headers={
            "origin": "https://app.example.com",
            "access-control-request-method": "GET",
        },
    )
    assert (
        response.headers.get("access-control-allow-origin") == "https://app.example.com"
    )


# ---------------------------------------------------------------------------
# security.posture log includes unsafe_mode flag
# ---------------------------------------------------------------------------


def test_security_posture_log_includes_unsafe_mode_true_when_insecure_no_auth(
    tmp_path: Path,
) -> None:
    """When --insecure-no-auth is active, the security.posture log must include
    unsafe_mode=True so monitoring alerts can detect insecure deployments.
    """
    import inspect

    from recotem.serving.app import _emit_security_posture

    source = inspect.getsource(_emit_security_posture)
    # The function must reference 'unsafe_mode' as a structured log field.
    assert "unsafe_mode" in source, (
        "_emit_security_posture must include 'unsafe_mode' in the log event"
    )

    # Now verify the actual value by calling with a config that has insecure_no_auth.
    import structlog.testing

    from recotem.artifact.signing import KeyRing
    from recotem.config import ServeConfig

    cfg = ServeConfig()
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.dev_allow_unsigned = False
    cfg.host = "127.0.0.1"
    cfg.allowed_hosts = ["*"]
    cfg.allowed_origins = []

    kr = KeyRing("test:" + "aa" * 32)

    with structlog.testing.capture_logs() as captured:
        _emit_security_posture(cfg, kr)

    posture_events = [e for e in captured if e.get("event") == "security.posture"]
    assert posture_events, "security.posture log event must be emitted"
    assert posture_events[0]["unsafe_mode"] is True


def test_security_posture_log_unsafe_mode_false_when_auth_enabled(
    tmp_path: Path,
) -> None:
    """When API keys are configured and no insecure flags set, unsafe_mode must be False."""
    import structlog.testing

    from recotem.artifact.signing import KeyRing
    from recotem.config import ServeConfig
    from recotem.serving.app import _emit_security_posture

    cfg = ServeConfig()
    cfg.env = "production"
    cfg.insecure_no_auth = False
    cfg.dev_allow_unsigned = False
    cfg.host = "0.0.0.0"
    cfg.allowed_hosts = ["*"]
    cfg.allowed_origins = []

    kr = KeyRing("test:" + "aa" * 32)

    with structlog.testing.capture_logs() as captured:
        _emit_security_posture(cfg, kr)

    posture_events = [e for e in captured if e.get("event") == "security.posture"]
    assert posture_events, "security.posture log event must be emitted"
    assert posture_events[0]["unsafe_mode"] is False
