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


def test_security_posture_log_emitted_at_startup(tmp_path: Path, caplog) -> None:
    """create_app emits a 'security.posture' log event."""
    import logging

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    with caplog.at_level(logging.INFO):
        create_app(cfg)

    # Check that security.posture appears somewhere in the captured logs
    log_text = " ".join(record.getMessage() for record in caplog.records)
    assert "security.posture" in log_text or any(
        "security" in str(r.msg) or "posture" in str(r.msg) for r in caplog.records
    )


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
