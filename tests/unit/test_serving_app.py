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
# --insecure-no-auth must override configured API keys
# ---------------------------------------------------------------------------


def test_insecure_no_auth_overrides_configured_api_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``insecure_no_auth=True`` and ``api_keys`` is non-empty,
    ``create_app`` must pass ``api_keys=[]`` to ``make_router`` so the
    X-API-Key header is not enforced. Otherwise the flag is documented
    but silently ineffective whenever ``RECOTEM_API_KEYS`` is still set
    in the environment."""
    import hashlib

    from recotem.config import ApiKeyEntry
    from recotem.serving import app as app_module

    cfg = _minimal_config(tmp_path)
    cfg.insecure_no_auth = True
    cfg.api_keys = [
        ApiKeyEntry(kid="leftover", sha256_hex=hashlib.sha256(b"x").hexdigest())
    ]

    captured: dict = {}
    real_make_router = app_module.make_router

    def _spy(*args, **kwargs):
        captured["api_keys"] = kwargs.get("api_keys")
        return real_make_router(*args, **kwargs)

    monkeypatch.setattr(app_module, "make_router", _spy)
    app_module.create_app(cfg)

    assert captured["api_keys"] == [], (
        "insecure_no_auth must clear router api_keys; otherwise the flag "
        "does not actually disable authentication when RECOTEM_API_KEYS is set."
    )


def test_insecure_no_auth_security_posture_marks_auth_disabled(
    tmp_path: Path,
) -> None:
    """The ``security.posture`` log line must report ``auth_enabled=False``
    when ``insecure_no_auth`` is active, even if ``api_keys`` is non-empty."""
    import hashlib

    import structlog.testing

    from recotem.artifact.signing import KeyRing
    from recotem.config import ApiKeyEntry, ServeConfig
    from recotem.serving.app import _emit_security_posture

    cfg = ServeConfig()
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.api_keys = [
        ApiKeyEntry(kid="leftover", sha256_hex=hashlib.sha256(b"x").hexdigest())
    ]
    cfg.host = "127.0.0.1"
    cfg.allowed_hosts = ["*"]
    cfg.allowed_origins = []

    kr = KeyRing("test:" + "aa" * 32)
    with structlog.testing.capture_logs() as captured:
        _emit_security_posture(cfg, kr)

    posture = next(e for e in captured if e.get("event") == "security.posture")
    assert posture["auth_enabled"] is False
    assert posture["unsafe_mode"] is True


def test_normal_mode_passes_configured_api_keys_to_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``insecure_no_auth``, the configured api_keys must reach the router."""
    import hashlib

    from recotem.config import ApiKeyEntry
    from recotem.serving import app as app_module

    entry = ApiKeyEntry(kid="k1", sha256_hex=hashlib.sha256(b"x").hexdigest())
    cfg = _minimal_config(tmp_path)
    cfg.insecure_no_auth = False
    cfg.api_keys = [entry]

    captured: dict = {}
    real_make_router = app_module.make_router

    def _spy(*args, **kwargs):
        captured["api_keys"] = kwargs.get("api_keys")
        return real_make_router(*args, **kwargs)

    monkeypatch.setattr(app_module, "make_router", _spy)
    app_module.create_app(cfg)

    assert captured["api_keys"] == [entry]


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


# ---------------------------------------------------------------------------
# Failed-load handling — /health degraded + stub registry entry
# ---------------------------------------------------------------------------


def _write_recipe_yaml(recipes_dir: Path, name: str, output_path: Path) -> Path:
    """Write a minimal recipe YAML pointing at *output_path* as the artifact."""
    yaml_text = f"""
name: {name}
source:
  type: csv
  path: {recipes_dir / "data.csv"}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms:
    - TopPop
output:
  path: {output_path}
"""
    yaml_path = recipes_dir / f"{name}.yaml"
    yaml_path.write_text(yaml_text)
    return yaml_path


def test_failed_initial_load_inserts_stub_with_loaded_false(tmp_path: Path) -> None:
    """A recipe whose artifact is missing at startup must still appear in
    /health as loaded=false with an error string, not silently dropped."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    recipes_dir = Path(cfg.recipes_dir)  # type: ignore[arg-type]
    missing_artifact = tmp_path / "does-not-exist.recotem"
    _write_recipe_yaml(recipes_dir, "missing_recipe", missing_artifact)

    app = create_app(cfg)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "degraded", (
        f"a missing artifact at startup must surface as degraded; got {body}"
    )
    assert "missing_recipe" in body["recipes"]
    entry = body["recipes"]["missing_recipe"]
    assert entry["loaded"] is False
    assert "error" in entry and entry["error"], (
        "stub entry must carry the failure reason"
    )


def test_failed_load_recipe_returns_503_on_predict(tmp_path: Path) -> None:
    """/predict against a recipe whose artifact failed to load returns 503,
    not 200 or 500."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    recipes_dir = Path(cfg.recipes_dir)  # type: ignore[arg-type]
    missing_artifact = tmp_path / "does-not-exist.recotem"
    _write_recipe_yaml(recipes_dir, "broken", missing_artifact)

    app = create_app(cfg)
    client = TestClient(app)
    response = client.post("/predict/broken", json={"user_id": "u1", "cutoff": 5})
    assert response.status_code == 503


def test_initial_load_metadata_field_missing_with_on_field_missing_error_marks_failed(
    tmp_path: Path,
) -> None:
    """A recipe whose ``item_metadata`` declares a missing field with
    ``on_field_missing: error`` must register at startup as
    ``loaded=false`` with a metadata-related error visible via ``/health``."""
    import pickle  # noqa: S403  (test fixture: payload is built locally)

    import pandas as pd
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app
    from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact

    cfg = _minimal_config(tmp_path)
    recipes_dir = Path(cfg.recipes_dir)  # type: ignore[arg-type]

    artifact_path = tmp_path / "model.recotem"
    payload = pickle.dumps({"recommender": "stub"}, protocol=4)  # noqa: S301
    artifact_path.write_bytes(
        build_raw_artifact(
            kid="active",
            key_hex=ACTIVE_KEY_HEX,
            header_dict={
                "recipe_name": "with_bad_metadata",
                "best_class": "TopPop",
                "trained_at": "2026-01-01T00:00:00Z",
            },
            payload_bytes=payload,
        )
    )

    metadata_csv = tmp_path / "items.csv"
    pd.DataFrame({"item_id": ["i1"], "title": ["A"]}).to_csv(metadata_csv, index=False)

    yaml_path = recipes_dir / "with_bad_metadata.yaml"
    yaml_path.write_text(
        f"""\
name: with_bad_metadata
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
item_metadata:
  type: csv
  path: {metadata_csv}
  fields: [missing_column]
  on_field_missing: error
output:
  path: {artifact_path}
"""
    )

    app = create_app(cfg)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()

    assert "with_bad_metadata" in body["recipes"]
    entry = body["recipes"]["with_bad_metadata"]
    assert entry["loaded"] is False, (
        f"expected metadata error to mark recipe not-loaded; got {entry}"
    )
    assert "metadata" in (entry.get("error") or "").lower()


def test_failed_load_recipe_excluded_from_models_listing(tmp_path: Path) -> None:
    """/models lists only successfully loaded recipes; stubs are hidden
    (operators see them via /health instead)."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    recipes_dir = Path(cfg.recipes_dir)  # type: ignore[arg-type]
    missing_artifact = tmp_path / "does-not-exist.recotem"
    _write_recipe_yaml(recipes_dir, "broken", missing_artifact)

    app = create_app(cfg)
    client = TestClient(app)
    response = client.get("/models")
    assert response.status_code == 200
    names = [m.get("name") for m in response.json()]
    assert "broken" not in names
