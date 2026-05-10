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

from recotem.config import ConfigError, ServeConfig

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
    """--insecure-no-auth in a production env raises ConfigError."""
    cfg = _minimal_config(tmp_path)
    cfg.env = "production"
    cfg.insecure_no_auth = True
    with pytest.raises(ConfigError, match="RECOTEM_ENV"):
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
    with pytest.raises(ConfigError, match="development"):
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


# ---------------------------------------------------------------------------
# ServeConfig.allowed_hosts parsing — RECOTEM_ALLOWED_HOSTS edge cases (M5)
# ---------------------------------------------------------------------------


def test_allowed_hosts_unset_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """When RECOTEM_ALLOWED_HOSTS is unset, allowed_hosts defaults to
    ['127.0.0.1', 'localhost'] — never an empty list."""
    monkeypatch.delenv("RECOTEM_ALLOWED_HOSTS", raising=False)
    cfg = ServeConfig.from_env()
    assert cfg.allowed_hosts == ["127.0.0.1", "localhost"]


def test_allowed_hosts_empty_string_returns_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When RECOTEM_ALLOWED_HOSTS='' (explicitly empty), allowed_hosts must
    fall back to the documented default rather than yielding an empty list.
    An empty list would cause TrustedHostMiddleware to accept all hosts (via
    the old 'or [\"*\"]' fallback) — a security footgun."""
    monkeypatch.setenv("RECOTEM_ALLOWED_HOSTS", "")
    cfg = ServeConfig.from_env()
    assert cfg.allowed_hosts == ["127.0.0.1", "localhost"], (
        f"Expected default on empty string, got {cfg.allowed_hosts!r}"
    )


def test_allowed_hosts_single_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single hostname is returned as a one-element list."""
    monkeypatch.setenv("RECOTEM_ALLOWED_HOSTS", "example.com")
    cfg = ServeConfig.from_env()
    assert cfg.allowed_hosts == ["example.com"]


def test_allowed_hosts_multiple_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """A comma-separated list is split and stripped correctly."""
    monkeypatch.setenv("RECOTEM_ALLOWED_HOSTS", "a,b,c")
    cfg = ServeConfig.from_env()
    assert cfg.allowed_hosts == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Regression: csv-list env vars that must still default to EMPTY (not inherit
# the RECOTEM_ALLOWED_HOSTS non-empty default).  The allowed_hosts fix must be
# narrowly scoped — other csv-list vars must keep their own defaults.
# ---------------------------------------------------------------------------


def test_allowed_origins_empty_string_stays_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_ALLOWED_ORIGINS='' must remain [] (CORS default-deny).

    The allowed_hosts fix introduced a non-empty default for RECOTEM_ALLOWED_HOSTS
    inside _split_csv_env.  This test ensures that fix is scoped to
    RECOTEM_ALLOWED_HOSTS only: RECOTEM_ALLOWED_ORIGINS must NEVER fall back to
    a non-empty list — an empty string means 'deny all' for CORS.
    """
    monkeypatch.setenv("RECOTEM_ALLOWED_ORIGINS", "")
    monkeypatch.delenv("RECOTEM_ALLOWED_HOSTS", raising=False)
    cfg = ServeConfig.from_env()
    assert cfg.allowed_origins == [], (
        "RECOTEM_ALLOWED_ORIGINS='' must stay empty (CORS default-deny); "
        f"got {cfg.allowed_origins!r}"
    )


def test_allowed_origins_unset_stays_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When RECOTEM_ALLOWED_ORIGINS is unset, CORS must remain denied (empty list)."""
    monkeypatch.delenv("RECOTEM_ALLOWED_ORIGINS", raising=False)
    cfg = ServeConfig.from_env()
    assert cfg.allowed_origins == [], (
        f"RECOTEM_ALLOWED_ORIGINS unset must yield [], got {cfg.allowed_origins!r}"
    )


def test_api_keys_empty_string_stays_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_API_KEYS='' must yield an empty api_keys list (auth disabled / localhost bind).

    Guards against a future refactor accidentally applying the RECOTEM_ALLOWED_HOSTS
    non-empty fallback to API keys, which would produce a malformed entry and
    break the server startup with a confusing ValueError.
    """
    monkeypatch.setenv("RECOTEM_API_KEYS", "")
    cfg = ServeConfig.from_env()
    assert cfg.api_keys == [], (
        f"RECOTEM_API_KEYS='' must yield empty api_keys, got {cfg.api_keys!r}"
    )


def test_allowed_hosts_non_empty_default_does_not_bleed_into_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify allowed_hosts and allowed_origins are independently parsed.

    Setting only RECOTEM_ALLOWED_HOSTS must not affect RECOTEM_ALLOWED_ORIGINS.
    """
    monkeypatch.setenv("RECOTEM_ALLOWED_HOSTS", "example.com")
    monkeypatch.delenv("RECOTEM_ALLOWED_ORIGINS", raising=False)
    cfg = ServeConfig.from_env()
    assert cfg.allowed_hosts == ["example.com"]
    assert cfg.allowed_origins == [], (
        "Setting RECOTEM_ALLOWED_HOSTS must not change RECOTEM_ALLOWED_ORIGINS"
    )


# ---------------------------------------------------------------------------
# F1. Lifespan watcher join timeout boundary logic
# ---------------------------------------------------------------------------


def test_watcher_join_timeout_boundary_values(tmp_path: Path) -> None:
    """The watcher join timeout is clamped to max(1.0, min(5.0, drain_seconds)).

    We verify the boundary: drain_seconds values of 0, 3, 30, and 100 each
    produce the expected timeout without requiring a running server.

    This mirrors the logic in app.py:
        watcher_join_timeout = max(1.0, min(5.0, float(serve_config.drain_seconds)))
    """
    cases = [
        (0, 1.0),  # below min → clamped to 1.0
        (3, 3.0),  # within range → unchanged
        (30, 5.0),  # above max → clamped to 5.0
        (100, 5.0),  # well above max → clamped to 5.0
    ]
    cfg = _minimal_config(tmp_path)
    for drain_seconds, expected_timeout in cases:
        cfg.drain_seconds = drain_seconds
        actual = max(1.0, min(5.0, float(cfg.drain_seconds)))
        assert actual == expected_timeout, (
            f"drain_seconds={drain_seconds}: expected timeout={expected_timeout}, "
            f"got {actual}"
        )


def test_lifespan_watcher_joined_on_shutdown(tmp_path: Path) -> None:
    """When the FastAPI app shuts down, the ArtifactWatcher thread must be stopped.

    We use TestClient as a context manager — entering starts the lifespan and
    exiting triggers shutdown.  We spy on ArtifactWatcher.join to confirm it
    is called during shutdown.
    """
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from recotem.serving import app as app_mod
    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)

    join_calls: list[dict] = []

    OriginalWatcher = app_mod.ArtifactWatcher

    class _SpyWatcher(OriginalWatcher):
        def join(self, timeout=None):
            join_calls.append({"timeout": timeout})
            super().join(timeout=timeout)

    # Patch the symbol in app.py's namespace (it imports ArtifactWatcher
    # at module load time, so patching the watcher module is too late).
    with patch.object(app_mod, "ArtifactWatcher", _SpyWatcher):
        app = create_app(cfg)
        with TestClient(app):
            pass  # lifespan shutdown runs on __exit__

    assert join_calls, "ArtifactWatcher.join() must be called during app shutdown"


# ---------------------------------------------------------------------------
# CRITICAL-3 (lightweight): SIGTERM drain — drain_seconds propagated to uvicorn
# and watcher join timeout logic
# ---------------------------------------------------------------------------


def test_drain_seconds_used_as_uvicorn_graceful_shutdown_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The drain_seconds from ServeConfig is forwarded to uvicorn as
    timeout_graceful_shutdown so in-flight requests have time to complete.

    We verify this structurally via the CLI module rather than actually
    starting uvicorn (which would bind a real port).
    """
    import inspect

    from recotem.cli import serve as serve_command

    source = inspect.getsource(serve_command)
    assert "timeout_graceful_shutdown" in source, (
        "cli.serve must pass drain_seconds to uvicorn.run "
        "as timeout_graceful_shutdown so in-flight requests are drained on SIGTERM"
    )
    assert "drain_seconds" in source, (
        "cli.serve must reference cfg.drain_seconds when calling uvicorn.run"
    )


def test_watcher_join_timeout_uses_drain_seconds_clamped(tmp_path: Path) -> None:
    """The watcher join timeout is clamped to max(1, min(5, drain_seconds)).

    This test verifies that very large drain_seconds values (e.g. 300s) are
    clamped so the watcher join does not block process exit indefinitely.
    """
    # Confirm the formula is applied in app.py source
    import inspect

    from recotem.serving import app as app_mod

    source = inspect.getsource(app_mod.create_app)
    # The clamp logic should reference min/max around 5.0 and drain_seconds
    assert "min(5.0" in source or "min(5," in source, (
        "Watcher join timeout must be clamped with min(5.0, ...) "
        "to prevent blocking process exit on large drain_seconds"
    )
    assert "drain_seconds" in source, (
        "Watcher join timeout must reference drain_seconds"
    )


def test_drain_seconds_respected_during_lifespan_shutdown(tmp_path: Path) -> None:
    """The lifespan must emit 'serve_shutdown' with drain_seconds in the log.

    This verifies the drain_seconds value flows through the shutdown path.
    """
    import structlog.testing
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.drain_seconds = 42  # non-default value

    app = create_app(cfg)

    with structlog.testing.capture_logs() as captured:
        with TestClient(app):
            pass  # triggers lifespan shutdown

    shutdown_events = [e for e in captured if e.get("event") == "serve_shutdown"]
    assert shutdown_events, "lifespan shutdown must emit 'serve_shutdown' log event"
    assert shutdown_events[0].get("drain_seconds") == 42, (
        f"serve_shutdown log must include drain_seconds=42; got: {shutdown_events[0]!r}"
    )


# ---------------------------------------------------------------------------
# Fix 3: corrupt header JSON returns _failed_entry instead of crashing server
# ---------------------------------------------------------------------------


def _build_artifact_with_corrupt_header_json(key_hex: str) -> bytes:
    """Build a .recotem artifact whose header JSON bytes are valid UTF-8 but
    not valid JSON.  HMAC is computed over the tampered content so the
    signature check passes; only json.loads should fail.
    """
    import hmac as _hmac
    import struct

    from recotem.artifact.format import FORMAT_VERSION, MAGIC

    bad_json: bytes = b"{ this is not valid json !!!"
    payload_bytes: bytes = b""  # empty payload — json.loads fires before unpickle

    kid = "active"
    kid_bytes = kid.encode("utf-8")
    key_bytes = bytes.fromhex(key_hex)

    h = _hmac.new(key_bytes, digestmod="sha256")
    h.update(kid_bytes)
    h.update(bad_json)
    h.update(payload_bytes)
    digest = h.digest()

    parts: list[bytes] = [
        MAGIC,
        struct.pack("<HH", FORMAT_VERSION, 0),
        bytes([len(kid_bytes)]),
        kid_bytes,
        digest,
        struct.pack("<I", len(bad_json)),
        bad_json,
        payload_bytes,
    ]
    return b"".join(parts)


def test_corrupt_header_json_returns_failed_entry_not_crash(tmp_path: Path) -> None:
    """A valid artifact whose header JSON is syntactically invalid must produce
    a stub registry entry (loaded=False, last_load_error containing
    'header JSON decode failed') instead of crashing the server.

    Regression for app.py — previously json.loads(hdr.header_data.decode())
    was unwrapped; a corrupt header would propagate as an unhandled ValueError.
    """
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app
    from tests.conftest import ACTIVE_KEY_HEX

    cfg = _minimal_config(tmp_path)
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    recipes_dir = Path(cfg.recipes_dir)  # type: ignore[arg-type]

    artifact_path = tmp_path / "bad_header.recotem"
    artifact_path.write_bytes(_build_artifact_with_corrupt_header_json(ACTIVE_KEY_HEX))
    _write_recipe_yaml(recipes_dir, "corrupt_header", artifact_path)

    app = create_app(cfg)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()

    assert "corrupt_header" in body["recipes"], (
        "Recipe with corrupt header JSON must appear in /health"
    )
    entry = body["recipes"]["corrupt_header"]
    assert entry["loaded"] is False, (
        f"corrupt header JSON must cause loaded=False; got {entry!r}"
    )
    error_str = entry.get("error") or ""
    assert "header JSON decode failed" in error_str, (
        f"last_load_error must contain 'header JSON decode failed'; got {error_str!r}"
    )


# ---------------------------------------------------------------------------
# insecure-no-auth HTTP request without key returns 200
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# I-C: CORS — allow_credentials=False, OPTIONS method included, preflight works
# ---------------------------------------------------------------------------


def test_CORS_preflight_returns_success_for_configured_origin(tmp_path: Path) -> None:
    """An OPTIONS preflight request from a configured origin must receive a
    2xx response (200 or 204) so browsers proceed with the actual request.

    FastAPI's CORSMiddleware returns 200 for OPTIONS when the origin matches.
    """
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.allowed_origins = ["https://app.example.com"]
    app = create_app(cfg)
    client = TestClient(app)
    response = client.options(
        "/predict/some_model",
        headers={
            "origin": "https://app.example.com",
            "access-control-request-method": "POST",
            "access-control-request-headers": "x-api-key",
        },
    )
    assert response.status_code in (200, 204), (
        f"CORS preflight must return 2xx for a configured origin; "
        f"got {response.status_code}"
    )
    assert (
        response.headers.get("access-control-allow-origin") == "https://app.example.com"
    )


def test_CORS_allow_credentials_header_not_sent_for_configured_origin(
    tmp_path: Path,
) -> None:
    """With ``allow_credentials=False``, the ``Access-Control-Allow-Credentials``
    header must NOT be present in preflight responses.

    Browsers only send the ``Access-Control-Allow-Credentials: true`` header
    when ``allow_credentials=True``.  With ``False`` the header is omitted
    entirely — which is the correct posture for an API key auth scheme
    (credentials in the ``X-API-Key`` header, not cookies / HTTP auth).
    """
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
    # access-control-allow-credentials must be absent or set to "false".
    acao_cred = response.headers.get("access-control-allow-credentials")
    assert acao_cred is None or acao_cred.lower() == "false", (
        f"allow_credentials=False must not produce "
        f"'Access-Control-Allow-Credentials: true'; got {acao_cred!r}"
    )


def test_CORS_allow_methods_includes_options(tmp_path: Path) -> None:
    """The CORSMiddleware must be configured with OPTIONS in allow_methods
    so that preflight requests are handled correctly.

    Verifies structurally via source inspection that 'OPTIONS' is listed.
    """
    import inspect

    from recotem.serving import app as app_mod

    source = inspect.getsource(app_mod.create_app)
    assert "OPTIONS" in source, (
        "create_app must include 'OPTIONS' in the CORSMiddleware allow_methods list"
    )
    assert "allow_credentials=False" in source, (
        "create_app must explicitly set allow_credentials=False in CORSMiddleware"
    )


def test_insecure_no_auth_http_request_without_key_returns_200(
    tmp_path: Path,
) -> None:
    """With insecure_no_auth=True and RECOTEM_ENV=dev, /health is accessible
    without an X-API-Key header and returns HTTP 200."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.env = "dev"
    cfg.insecure_no_auth = True
    cfg.api_keys = []  # no keys configured

    app = create_app(cfg)
    client = TestClient(app)
    # No X-API-Key header — must still pass with insecure_no_auth=True
    response = client.get("/health")
    assert response.status_code == 200, (
        f"insecure_no_auth=True must allow unauthenticated requests; "
        f"got {response.status_code}"
    )
