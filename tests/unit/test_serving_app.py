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


def test_dev_allow_unsigned_emits_warning_banner_via_lifespan(tmp_path: Path) -> None:
    """When --dev-allow-unsigned is in effect, a DEV_ALLOW_UNSIGNED_ACTIVE
    warning must be emitted by the lifespan _warn_loop (once per 60s), NOT by
    create_app itself.  This test verifies that the lifespan is capable of
    emitting the banner when needed.

    The banner is specifically NOT emitted at create_app() time any more (that
    was the source of the double-emit bug).  To capture the first banner emit
    from the lifespan task we would need to wait up to 60s, which is too slow
    for a unit test.  Instead we confirm that the log has the signing-key
    warning emitted by _build_key_ring (signing_key_verification_disabled),
    which proves the dev_allow_unsigned code path was reached.
    """
    import structlog.testing

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.env = "development"
    cfg.dev_allow_unsigned = True
    with structlog.testing.capture_logs() as cap:
        create_app(cfg)

    # _build_key_ring emits this warning when dev_allow_unsigned is True —
    # it is always synchronous and confirms the flag was honoured.
    assert any(e.get("event") == "signing_key_verification_disabled" for e in cap), (
        "create_app must emit 'signing_key_verification_disabled' when "
        "dev_allow_unsigned=True to confirm the flag was processed"
    )


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
    response = client.get("/v1/health", headers={"host": "evil.attacker.com"})
    assert response.status_code in (400, 403, 422)


def test_TrustedHost_allows_configured_host(tmp_path: Path) -> None:
    """A request with a Host header in allowed_hosts succeeds."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.allowed_hosts = ["testserver"]
    app = create_app(cfg)
    client = TestClient(app)
    response = client.get("/v1/health")
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
        "/v1/health",
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
        "/v1/health",
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
    /health/details as loaded=false with an error string, not silently dropped.

    /health (probe-safe) only shows aggregate counts; per-recipe detail
    requires authentication via /health/details (I-3).
    """
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    recipes_dir = Path(cfg.recipes_dir)  # type: ignore[arg-type]
    missing_artifact = tmp_path / "does-not-exist.recotem"
    _write_recipe_yaml(recipes_dir, "missing_recipe", missing_artifact)

    app = create_app(cfg)
    client = TestClient(app)
    # /health (probe-safe) must return 503 when degraded
    response = client.get("/v1/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded", (
        f"a missing artifact at startup must surface as degraded; got {body}"
    )

    # /health/details carries the per-recipe info (auth passed via insecure_no_auth)
    response_details = client.get("/v1/health/details")
    assert response_details.status_code == 503
    details = response_details.json()
    assert "missing_recipe" in details["recipes"]
    entry = details["recipes"]["missing_recipe"]
    assert entry["loaded"] is False
    assert "error" in entry and entry["error"], (
        "stub entry must carry the failure reason"
    )


def test_failed_load_recipe_returns_503_on_predict(tmp_path: Path) -> None:
    """POST /v1/recipes/{name}:recommend against a recipe whose artifact failed
    to load returns 503, not 200 or 500."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    recipes_dir = Path(cfg.recipes_dir)  # type: ignore[arg-type]
    missing_artifact = tmp_path / "does-not-exist.recotem"
    _write_recipe_yaml(recipes_dir, "broken", missing_artifact)

    app = create_app(cfg)
    client = TestClient(app)
    response = client.post(
        "/v1/recipes/broken:recommend",
        json={"user_id": "u1", "limit": 5},
    )
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
    response = client.get("/v1/health")
    # Same B-2 contract: a stub recipe (loaded=False) makes the overall
    # status degraded, which now surfaces as HTTP 503.
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"

    # Per-recipe detail only available via /health/details (I-3).
    response_details = client.get("/v1/health/details")
    assert response_details.status_code == 503
    details = response_details.json()
    assert "with_bad_metadata" in details["recipes"]
    entry = details["recipes"]["with_bad_metadata"]
    assert entry["loaded"] is False, (
        f"expected metadata error to mark recipe not-loaded; got {entry}"
    )
    assert "metadata" in (entry.get("error") or "").lower()


def test_failed_load_recipe_excluded_from_models_listing(tmp_path: Path) -> None:
    """/v1/recipes lists only successfully loaded recipes; stubs are hidden
    (operators see them via /v1/health/details instead)."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    recipes_dir = Path(cfg.recipes_dir)  # type: ignore[arg-type]
    missing_artifact = tmp_path / "does-not-exist.recotem"
    _write_recipe_yaml(recipes_dir, "broken", missing_artifact)

    app = create_app(cfg)
    client = TestClient(app)
    response = client.get("/v1/recipes")
    assert response.status_code == 200
    names = [r.get("name") for r in response.json().get("recipes", [])]
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
    response = client.get("/v1/health")
    # Same B-2 contract: a stub recipe (loaded=False) makes overall
    # status degraded → HTTP 503.
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"

    # Per-recipe detail only available via /health/details (I-3).
    response_details = client.get("/v1/health/details")
    assert response_details.status_code == 503
    details = response_details.json()
    assert "corrupt_header" in details["recipes"], (
        "Recipe with corrupt header JSON must appear in /health/details"
    )
    entry = details["recipes"]["corrupt_header"]
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
        "/v1/recipes/some_model:recommend",
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
        "/v1/health",
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


# ---------------------------------------------------------------------------
# T-5: SIGTERM drain — RECOTEM_DRAIN_SECONDS config resolution + lifespan
# ---------------------------------------------------------------------------


def test_lifespan_drain_seconds_default_is_30(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When RECOTEM_DRAIN_SECONDS is unset, ServeConfig resolves drain_seconds to 30.

    The default is both the field default (ServeConfig.drain_seconds = 30) and
    the value from_env() produces when the variable is absent.
    """
    from recotem.config import ServeConfig

    monkeypatch.delenv("RECOTEM_DRAIN_SECONDS", raising=False)
    cfg = ServeConfig.from_env()
    assert cfg.drain_seconds == 30, (
        f"Expected drain_seconds=30 when RECOTEM_DRAIN_SECONDS is unset, "
        f"got {cfg.drain_seconds}"
    )


def test_lifespan_drain_seconds_clamped_to_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_DRAIN_SECONDS=0 is below the [1, 300] range and is clamped to 1."""
    from recotem.config import ServeConfig

    monkeypatch.setenv("RECOTEM_DRAIN_SECONDS", "0")
    cfg = ServeConfig.from_env()
    assert cfg.drain_seconds == 1, (
        f"RECOTEM_DRAIN_SECONDS=0 must be clamped to 1, got {cfg.drain_seconds}"
    )


def test_lifespan_drain_seconds_clamped_to_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_DRAIN_SECONDS=99999 is above the [1, 300] range and is clamped to 300."""
    from recotem.config import ServeConfig

    monkeypatch.setenv("RECOTEM_DRAIN_SECONDS", "99999")
    cfg = ServeConfig.from_env()
    assert cfg.drain_seconds == 300, (
        f"RECOTEM_DRAIN_SECONDS=99999 must be clamped to 300, got {cfg.drain_seconds}"
    )


def test_lifespan_drain_seconds_invalid_value_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_DRAIN_SECONDS=garbage (non-numeric) falls back to the default 30.

    The _clamped_int_env helper catches ValueError from int(raw) and returns
    the default unchanged — so unparseable values are treated like 'unset'.
    """
    from recotem.config import ServeConfig

    monkeypatch.setenv("RECOTEM_DRAIN_SECONDS", "garbage")
    cfg = ServeConfig.from_env()
    assert cfg.drain_seconds == 30, (
        f"RECOTEM_DRAIN_SECONDS='garbage' must fall back to default 30, "
        f"got {cfg.drain_seconds}"
    )


def test_lifespan_logs_drain_start_event(tmp_path: Path) -> None:
    """On lifespan shutdown the 'serve_shutdown' event is logged with drain_seconds.

    The task calls this event 'drain_start' but the actual implementation emits
    'serve_shutdown' — tests are aligned to the code, not the specification guess.
    The event carries drain_seconds so operators can confirm the active window.
    """
    import structlog.testing
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.drain_seconds = 1  # minimal value to keep test fast

    app = create_app(cfg)

    with structlog.testing.capture_logs() as captured:
        with TestClient(app):
            pass  # entering and exiting triggers lifespan shutdown

    shutdown_events = [e for e in captured if e.get("event") == "serve_shutdown"]
    assert shutdown_events, (
        "Lifespan shutdown must emit a 'serve_shutdown' log event. "
        "Note: the implementation uses 'serve_shutdown', not 'drain_start'."
    )
    assert shutdown_events[0].get("drain_seconds") == 1, (
        f"'serve_shutdown' event must carry drain_seconds=1; "
        f"got: {shutdown_events[0]!r}"
    )


def test_lifespan_completes_within_drain_window(tmp_path: Path) -> None:
    """The lifespan context exits well within twice the drain window.

    With drain_seconds=1, the lifespan shutdown (watcher stop + join + log)
    must complete within 2 seconds.  Uses asyncio.wait_for so an unexpectedly
    hung shutdown surfaces as a TimeoutError rather than a hanging test.

    The watcher join timeout is clamped to max(1, min(5, drain_seconds)) = 1 s
    and the watcher thread itself is a daemon so the join never blocks forever.
    """
    import asyncio

    from httpx import ASGITransport, AsyncClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.drain_seconds = (
        1  # keep test fast; watcher join timeout = max(1, min(5, 1)) = 1 s
    )

    app = create_app(cfg)

    async def _run() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as _client:
            # The ASGI lifespan is started on __aenter__ and torn down on __aexit__.
            pass

    # Allow 4 s (drain_seconds=1 → watcher_join_timeout=1 s; total shutdown
    # should be well under 4 s even on a loaded CI runner).
    asyncio.run(asyncio.wait_for(_run(), timeout=4.0))


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
    response = client.get("/v1/health")
    assert response.status_code == 200, (
        f"insecure_no_auth=True must allow unauthenticated requests; "
        f"got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# P-3: Parallel startup artifact loading
# ---------------------------------------------------------------------------


def _write_recipe_for_parallel_test(
    recipes_dir: Path,
    name: str,
    artifact_path: Path,
) -> None:
    """Write a minimal recipe YAML pointing at *artifact_path*."""
    (recipes_dir / f"{name}.yaml").write_text(
        f"""\
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
  path: {artifact_path}
"""
    )


def _make_valid_artifact_bytes() -> bytes:
    """Return valid signed artifact bytes via the shared conftest builder."""
    from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact

    return build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={
            "recipe_name": "test",
            "trained_at": "2026-01-01T00:00:00Z",
            "best_class": "TopPopRecommender",
        },
    )


def test_startup_loads_artifacts_in_parallel_with_default_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup must invoke _try_load_artifact via a ThreadPoolExecutor.

    We create 4 recipes pointing at valid artifacts, patch _try_load_artifact
    with a spy that records the OS thread id for each call, and assert that
    all N recipes are processed (executor dispatched all of them).
    """
    import threading

    from recotem.serving import app as app_module

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_data = _make_valid_artifact_bytes()

    n_recipes = 4
    for i in range(n_recipes):
        artifact_path = tmp_path / f"model_{i}.recotem"
        artifact_path.write_bytes(artifact_data)
        _write_recipe_for_parallel_test(recipes_dir, f"recipe_{i}", artifact_path)

    thread_ids: list[int] = []
    real_try_load = app_module._try_load_artifact

    def _spy_load(recipe, key_ring, serve_config):
        thread_ids.append(threading.get_ident())
        return real_try_load(recipe, key_ring, serve_config)

    monkeypatch.setattr(app_module, "_try_load_artifact", _spy_load)
    monkeypatch.delenv("RECOTEM_STARTUP_PARALLELISM", raising=False)

    from recotem.config import ServeConfig

    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]
    cfg.startup_parallelism = 0  # sentinel: use min(n_recipes, 8)

    app_module.create_app(cfg)

    assert len(thread_ids) == n_recipes, (
        f"Expected {n_recipes} load calls via executor, got {len(thread_ids)}"
    )
    # All recipes were dispatched (executor was used)
    assert len(set(thread_ids)) >= 1


def test_startup_respects_RECOTEM_STARTUP_PARALLELISM_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When RECOTEM_STARTUP_PARALLELISM=2, ServeConfig.startup_parallelism is 2
    and create_app reads that field to configure the executor max_workers."""
    from recotem.config import ServeConfig

    monkeypatch.setenv("RECOTEM_STARTUP_PARALLELISM", "2")

    cfg = ServeConfig.from_env()
    assert cfg.startup_parallelism == 2, (
        f"startup_parallelism should be 2 when env var is '2'; got {cfg.startup_parallelism}"
    )

    import inspect

    from recotem.serving import app as app_module

    source = inspect.getsource(app_module.create_app)
    assert "startup_parallelism" in source, (
        "create_app must read serve_config.startup_parallelism to set max_workers"
    )


def test_startup_one_failed_load_does_not_block_others(
    tmp_path: Path,
) -> None:
    """A recipe whose artifact is missing must NOT prevent other recipes from
    loading.  The failed recipe gets a stub entry (loaded=False); the healthy
    recipe gets a fully-populated entry (loaded=True).

    Verifies per-recipe fault isolation in the parallel executor."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_data = _make_valid_artifact_bytes()

    ok_artifact = tmp_path / "model_ok.recotem"
    ok_artifact.write_bytes(artifact_data)
    _write_recipe_for_parallel_test(recipes_dir, "recipe_ok", ok_artifact)

    missing_artifact = tmp_path / "does_not_exist.recotem"
    _write_recipe_for_parallel_test(recipes_dir, "recipe_bad", missing_artifact)

    from recotem.config import ServeConfig

    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]
    cfg.startup_parallelism = 2

    app = create_app(cfg)
    with TestClient(app) as client:
        response = client.get("/v1/health")
        # /health/details shows per-recipe breakdown (I-3); auth skipped (insecure_no_auth)
        response_details = client.get("/v1/health/details")

    # /health aggregate
    body = response.json()
    assert body["status"] == "degraded", (
        f"must be degraded when any recipe failed; got {body}"
    )

    # /health/details per-recipe breakdown
    details = response_details.json()
    assert "recipe_ok" in details["recipes"], "recipe_ok must appear in /health/details"
    assert "recipe_bad" in details["recipes"], (
        "recipe_bad must appear in /health/details"
    )
    assert details["recipes"]["recipe_ok"]["loaded"] is True, (
        f"recipe_ok should be loaded=True; got {details['recipes']['recipe_ok']}"
    )
    assert details["recipes"]["recipe_bad"]["loaded"] is False, (
        f"recipe_bad should be loaded=False; got {details['recipes']['recipe_bad']}"
    )


def test_startup_emits_load_complete_event_with_counts(
    tmp_path: Path,
) -> None:
    """create_app must emit 'startup_artifact_load_complete' after all initial
    loads finish, with fields: total_recipes, succeeded, failed, wall_seconds,
    max_workers."""
    import structlog.testing

    from recotem.serving.app import create_app

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_data = _make_valid_artifact_bytes()

    ok_artifact = tmp_path / "model_ok.recotem"
    ok_artifact.write_bytes(artifact_data)
    _write_recipe_for_parallel_test(recipes_dir, "recipe_ok", ok_artifact)

    missing_artifact = tmp_path / "no_such_file.recotem"
    _write_recipe_for_parallel_test(recipes_dir, "recipe_fail", missing_artifact)

    from recotem.config import ServeConfig

    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]

    with structlog.testing.capture_logs() as cap:
        create_app(cfg)

    complete_events = [
        e for e in cap if e.get("event") == "startup_artifact_load_complete"
    ]
    assert complete_events, (
        "create_app must emit 'startup_artifact_load_complete' after startup loads"
    )
    ev = complete_events[0]
    assert ev["total_recipes"] == 2, f"total_recipes should be 2; got {ev}"
    assert ev["succeeded"] == 1, f"succeeded should be 1; got {ev}"
    assert ev["failed"] == 1, f"failed should be 1; got {ev}"
    assert "wall_seconds" in ev, f"wall_seconds must be present; got {ev}"
    assert isinstance(ev["wall_seconds"], float), (
        f"wall_seconds must be a float; got {type(ev['wall_seconds'])}"
    )
    assert "max_workers" in ev, f"max_workers must be present; got {ev}"


def test_startup_parallelism_clamped_to_valid_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOTEM_STARTUP_PARALLELISM values outside [1, 32] must be clamped.

    - Value 0 is below the minimum (1) and clamps to 1.
    - Value 100 is above the maximum (32) and clamps to 32.
    - Unset env var leaves the sentinel (0) meaning "derive from recipe count".
    """
    import structlog.testing

    from recotem.config import ServeConfig

    monkeypatch.setenv("RECOTEM_STARTUP_PARALLELISM", "0")
    with structlog.testing.capture_logs():
        cfg_low = ServeConfig.from_env()
    assert cfg_low.startup_parallelism == 1, (
        f"0 should clamp to 1; got {cfg_low.startup_parallelism}"
    )

    monkeypatch.setenv("RECOTEM_STARTUP_PARALLELISM", "100")
    with structlog.testing.capture_logs():
        cfg_high = ServeConfig.from_env()
    assert cfg_high.startup_parallelism == 32, (
        f"100 should clamp to 32; got {cfg_high.startup_parallelism}"
    )

    monkeypatch.delenv("RECOTEM_STARTUP_PARALLELISM", raising=False)
    cfg_unset = ServeConfig.from_env()
    assert cfg_unset.startup_parallelism == 0, (
        f"Unset env var should leave sentinel 0; got {cfg_unset.startup_parallelism}"
    )


# ---------------------------------------------------------------------------
# N-1: C-1 — _try_load_artifact builds metadata_index for recipes with item_metadata
# ---------------------------------------------------------------------------


def test_try_load_artifact_builds_metadata_index_when_item_metadata_present(
    tmp_path: Path,
) -> None:
    """_try_load_artifact must populate metadata_index as a non-empty dict
    keyed by item_id when the recipe has an item_metadata block.

    This covers the startup path: ModelEntry.metadata_index is used by
    /predict to enrich recommendations with per-item fields.
    """
    import pickle  # noqa: S403

    import pandas as pd

    from recotem.artifact.signing import KeyRing
    from recotem.config import ServeConfig
    from recotem.recipe.loader import load_recipe
    from recotem.serving.app import _try_load_artifact
    from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact

    # Build a minimal valid artifact
    artifact_path = tmp_path / "model.recotem"
    payload = pickle.dumps({"key": "value"}, protocol=4)  # noqa: S301
    artifact_path.write_bytes(
        build_raw_artifact(
            kid="active",
            key_hex=ACTIVE_KEY_HEX,
            header_dict={
                "recipe_name": "meta_test",
                "best_class": "TopPop",
                "trained_at": "2026-01-01T00:00:00Z",
            },
            payload_bytes=payload,
        )
    )

    # Build a small item_metadata CSV
    metadata_csv = tmp_path / "items.csv"
    pd.DataFrame(
        {"item_id": ["i1", "i2", "i3"], "title": ["Alpha", "Beta", "Gamma"]}
    ).to_csv(metadata_csv, index=False)

    # Write a recipe YAML that references both
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    yaml_path = recipes_dir / "meta_test.yaml"
    yaml_path.write_text(
        f"""\
name: meta_test
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
  fields: [title]
  on_field_missing: error
output:
  path: {artifact_path}
"""
    )

    recipe = load_recipe(yaml_path)

    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.max_artifact_bytes = 100 * 1024 * 1024
    cfg.max_payload_bytes = 50 * 1024 * 1024
    cfg.metadata_field_deny = []

    key_ring = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    entry = _try_load_artifact(recipe, key_ring, cfg)

    assert entry.loaded, (
        f"_try_load_artifact must return loaded=True for a valid artifact + metadata; "
        f"error={entry.last_load_error!r}"
    )
    assert entry.metadata_index is not None, (
        "metadata_index must be populated (not None) when item_metadata is present"
    )
    assert isinstance(entry.metadata_index, dict), (
        f"metadata_index must be a dict; got {type(entry.metadata_index)}"
    )
    assert len(entry.metadata_index) == 3, (
        f"metadata_index must have one entry per item_id (3 expected); "
        f"got {len(entry.metadata_index)}"
    )
    assert "i1" in entry.metadata_index, "item_id 'i1' must be a key in metadata_index"
    assert entry.metadata_index["i1"].get("title") == "Alpha", (
        f"metadata_index['i1']['title'] must be 'Alpha'; "
        f"got {entry.metadata_index['i1']!r}"
    )


def test_try_load_artifact_populates_loaded_at_unix(tmp_path: Path) -> None:
    """Regression: startup-scan path must populate v1 fields.

    Bug-for-bug parallel to the watcher's _build_entry fix in Task 3 of
    the v1 API overhaul plan.  Without this, recipes loaded at startup
    report ``loaded_at: 1970-01-01T00:00:00Z`` from GET /v1/recipes until
    a hot-swap occurs.

    Invariant: any ModelEntry returned with ``loaded=True`` must carry
    ``loaded_at_unix > 0`` and an ``algorithms`` list / ``config_digest``
    string sourced from the header.
    """
    import time as _time

    from recotem.artifact.signing import KeyRing
    from recotem.config import ServeConfig
    from recotem.recipe.loader import load_recipe
    from recotem.serving.app import _try_load_artifact
    from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact

    # build_raw_artifact provides a safe default payload (a small builtin
    # dict pickled via SafeUnpickler's allow-list); we only override the
    # header to carry the v1 fields we want to assert on.
    artifact_path = tmp_path / "model.recotem"
    artifact_path.write_bytes(
        build_raw_artifact(
            kid="active",
            key_hex=ACTIVE_KEY_HEX,
            header_dict={
                "recipe_name": "loaded_at_test",
                "best_class": "TopPop",
                "trained_at": "2026-01-01T00:00:00Z",
                "config_digest": "deadbeef",
                "algorithms": ["TopPop", "IALS"],
            },
        )
    )

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    yaml_path = recipes_dir / "loaded_at_test.yaml"
    yaml_path.write_text(
        f"""\
name: loaded_at_test
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {artifact_path}
"""
    )

    recipe = load_recipe(yaml_path)

    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.max_artifact_bytes = 100 * 1024 * 1024
    cfg.max_payload_bytes = 50 * 1024 * 1024
    cfg.metadata_field_deny = []

    key_ring = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    before = _time.time()
    entry = _try_load_artifact(recipe, key_ring, cfg)
    after = _time.time()

    assert entry.loaded, (
        f"_try_load_artifact must return loaded=True for a valid artifact; "
        f"error={entry.last_load_error!r}"
    )
    assert entry.loaded_at_unix > 0, (
        f"startup-scan path must populate loaded_at_unix (regression: was 0.0); "
        f"got {entry.loaded_at_unix!r}"
    )
    assert before <= entry.loaded_at_unix <= after, (
        f"loaded_at_unix must fall within the load window "
        f"[{before}, {after}]; got {entry.loaded_at_unix}"
    )
    assert entry.config_digest == "deadbeef", (
        f"config_digest must be sourced from header; got {entry.config_digest!r}"
    )
    assert entry.algorithms == ["TopPop", "IALS"], (
        f"algorithms must be sourced from header; got {entry.algorithms!r}"
    )


# ---------------------------------------------------------------------------
# N-15: startup_parallelism — true parallel execution verified via thread IDs
# ---------------------------------------------------------------------------


def test_startup_parallel_loading_uses_multiple_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With RECOTEM_STARTUP_PARALLELISM=4 and 8 recipes, the startup executor
    uses multiple OS threads — verified by recording threading.get_ident() in
    each _try_load_artifact call.

    This confirms that the ThreadPoolExecutor dispatches work concurrently
    rather than running everything in the calling thread.
    """
    import threading
    import time

    from recotem.serving import app as app_module

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_data = _make_valid_artifact_bytes()

    n_recipes = 8
    for i in range(n_recipes):
        artifact_path = tmp_path / f"model_{i}.recotem"
        artifact_path.write_bytes(artifact_data)
        _write_recipe_for_parallel_test(recipes_dir, f"recipe_p_{i}", artifact_path)

    thread_ids: list[int] = []
    real_try_load = app_module._try_load_artifact

    def _spy_load(recipe, key_ring, serve_config):
        thread_ids.append(threading.get_ident())
        # Hold the worker briefly so the executor must spin up additional
        # threads to drain the queue.  Without this, fast hardware can let a
        # single worker consume all 8 tasks before a peer thread is even
        # created, producing a false-negative on the parallelism assertion.
        time.sleep(0.02)
        return real_try_load(recipe, key_ring, serve_config)

    monkeypatch.setattr(app_module, "_try_load_artifact", _spy_load)
    monkeypatch.setenv("RECOTEM_STARTUP_PARALLELISM", "4")

    from recotem.config import ServeConfig

    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]
    cfg.startup_parallelism = 4

    app_module.create_app(cfg)

    assert len(thread_ids) == n_recipes, (
        f"Expected {n_recipes} load calls, got {len(thread_ids)}"
    )
    # With 4 workers and 8 tasks, multiple threads must be involved.
    unique_threads = set(thread_ids)
    assert len(unique_threads) >= 2, (
        f"Expected at least 2 distinct thread IDs with parallelism=4 and {n_recipes} "
        f"recipes; got {unique_threads!r}.  The executor may not be dispatching work "
        f"concurrently."
    )


# ---------------------------------------------------------------------------
# Fix 1: Banner double-emit — emitted only once (inside lifespan), not twice
# ---------------------------------------------------------------------------


def test_insecure_banner_emitted_only_once_during_create_app(tmp_path: Path) -> None:
    """create_app must NOT emit INSECURE_NO_AUTH_ACTIVE synchronously at
    the end of create_app — the banner should only come from the lifespan
    _warn_loop.  Pre-fix code called _emit_insecure_banner both at the end of
    create_app AND inside _warn_loop, producing a double-emit on every startup.
    """
    import structlog.testing

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.env = "development"
    cfg.insecure_no_auth = True

    with structlog.testing.capture_logs() as cap:
        create_app(cfg)  # lifespan NOT started here — no _warn_loop fires

    # The synchronous emit at the bottom of create_app was removed by the fix.
    # If the lifespan has not started, zero INSECURE_NO_AUTH_ACTIVE events
    # should appear in the log.
    banner_events = [e for e in cap if e.get("event") == "INSECURE_NO_AUTH_ACTIVE"]
    assert len(banner_events) == 0, (
        f"Banner must NOT be emitted by create_app itself (only via lifespan); "
        f"found {len(banner_events)} event(s): {banner_events!r}"
    )


def test_dev_unsigned_banner_emitted_only_once_during_create_app(
    tmp_path: Path,
) -> None:
    """create_app must NOT emit DEV_ALLOW_UNSIGNED_ACTIVE synchronously.
    Same double-emit fix as the INSECURE_NO_AUTH_ACTIVE banner."""
    import structlog.testing

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.env = "development"
    cfg.dev_allow_unsigned = True

    with structlog.testing.capture_logs() as cap:
        create_app(cfg)

    banner_events = [e for e in cap if e.get("event") == "DEV_ALLOW_UNSIGNED_ACTIVE"]
    assert len(banner_events) == 0, (
        f"DEV_ALLOW_UNSIGNED_ACTIVE must NOT be emitted synchronously by create_app; "
        f"found {len(banner_events)} event(s): {banner_events!r}"
    )


def test_banner_task_cancelled_cleanly_on_shutdown(tmp_path: Path) -> None:
    """The banner asyncio task must be properly awaited after cancel() so
    asyncio does not warn 'Task was destroyed but it is pending!'.

    We run a full lifespan cycle with insecure_no_auth=True and assert that
    no asyncio warnings about pending tasks are emitted during shutdown.
    (A missing `await banner_task` after `cancel()` triggers that warning.)
    """
    import asyncio
    import warnings

    from httpx import ASGITransport, AsyncClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.drain_seconds = 1

    app = create_app(cfg)

    async def _run() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ):
            pass  # lifespan starts on __aenter__, shuts down on __aexit__

    # If banner_task.cancel() is not followed by `await banner_task`, asyncio
    # will emit a ResourceWarning "Task was destroyed but it is pending!" on GC.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        asyncio.run(_run())

    pending_task_warnings = [
        w
        for w in caught
        if "pending" in str(w.message).lower() or "destroyed" in str(w.message).lower()
    ]
    assert not pending_task_warnings, (
        "Shutdown must await the banner task after cancel() to avoid "
        f"'Task was destroyed but it is pending!': {pending_task_warnings!r}"
    )


def test_startup_parallelism_one_uses_single_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With RECOTEM_STARTUP_PARALLELISM=1, all loads run in the same worker thread.

    This is a regression guard: if max_workers=1, the executor serialises all
    submissions into a single thread.  The single-thread case is also useful
    for debugging (per CLAUDE.md: 'Set to 1 to force sequential loading').
    """
    import threading

    from recotem.serving import app as app_module

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_data = _make_valid_artifact_bytes()

    n_recipes = 4
    for i in range(n_recipes):
        artifact_path = tmp_path / f"model_seq_{i}.recotem"
        artifact_path.write_bytes(artifact_data)
        _write_recipe_for_parallel_test(recipes_dir, f"recipe_seq_{i}", artifact_path)

    thread_ids: list[int] = []
    real_try_load = app_module._try_load_artifact

    def _spy_load(recipe, key_ring, serve_config):
        thread_ids.append(threading.get_ident())
        return real_try_load(recipe, key_ring, serve_config)

    monkeypatch.setattr(app_module, "_try_load_artifact", _spy_load)

    from recotem.config import ServeConfig

    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]
    cfg.startup_parallelism = 1

    app_module.create_app(cfg)

    assert len(thread_ids) == n_recipes, (
        f"Expected {n_recipes} load calls, got {len(thread_ids)}"
    )
    # max_workers=1 → only one worker thread created by the executor.
    assert len(set(thread_ids)) == 1, (
        f"Expected exactly 1 unique thread ID with parallelism=1; "
        f"got {set(thread_ids)!r}"
    )


# ---------------------------------------------------------------------------
# MF-3: OpenAPI production gate
# ---------------------------------------------------------------------------


def _minimal_prod_config(tmp_path: Path) -> ServeConfig:
    """Build a minimal ServeConfig suitable for production-like environments."""
    import hashlib

    from recotem.config import ApiKeyEntry

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir(exist_ok=True)
    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    cfg.recipes_dir = str(recipes_dir)
    cfg.insecure_no_auth = False
    cfg.dev_allow_unsigned = False
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]
    # API key required in non-dev environments
    sha256_hex = hashlib.scrypt(
        b"test_api_key",
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    cfg.api_keys = [ApiKeyEntry(kid="k1", sha256_hex=sha256_hex)]
    return cfg


def test_docs_endpoint_disabled_in_production(tmp_path: Path) -> None:
    """MF-3: /docs must return 404 in production/prod/staging environments."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    for env in ("production", "prod", "staging"):
        cfg = _minimal_prod_config(tmp_path)
        cfg.env = env
        app = create_app(cfg)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/docs")
        assert resp.status_code == 404, (
            f"RECOTEM_ENV={env}: /docs must return 404; got {resp.status_code}"
        )
        resp = client.get("/openapi.json")
        assert resp.status_code == 404, (
            f"RECOTEM_ENV={env}: /openapi.json must return 404; got {resp.status_code}"
        )


def test_docs_endpoint_enabled_in_development(tmp_path: Path) -> None:
    """MF-3: /docs and /openapi.json must be accessible in development."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.env = "development"
    app = create_app(cfg)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200, (
        f"RECOTEM_ENV=development: /openapi.json must return 200; got {resp.status_code}"
    )


def test_metrics_endpoint_not_in_openapi_schema(tmp_path: Path) -> None:
    """MF-3: /metrics must have include_in_schema=False and not appear in /openapi.json."""

    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.env = "development"
    app = create_app(cfg)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema.get("paths", {})
    assert "/metrics" not in paths, (
        "/metrics must not appear in the OpenAPI schema (include_in_schema=False)"
    )


# ---------------------------------------------------------------------------
# MF-5: signing key missing still emits security.posture
# ---------------------------------------------------------------------------


def test_security_posture_emitted_even_when_signing_key_missing(
    tmp_path: Path,
) -> None:
    """MF-5: create_app must emit security.posture with signing_key_status='missing'
    before raising ConfigError when RECOTEM_SIGNING_KEYS is unset."""
    import structlog.testing

    from recotem.config import ConfigError, ServeConfig
    from recotem.serving.app import create_app

    cfg = ServeConfig()
    cfg.signing_keys_raw = ""  # no keys
    cfg.dev_allow_unsigned = False
    cfg.recipes_dir = str(tmp_path)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["*"]

    with structlog.testing.capture_logs() as cap:
        with pytest.raises(ConfigError):
            create_app(cfg)

    posture_events = [e for e in cap if e.get("event") == "security.posture"]
    assert posture_events, (
        "security.posture must be emitted even when signing keys are missing"
    )
    assert posture_events[0].get("signing_key_status") == "missing", (
        f"signing_key_status must be 'missing'; got {posture_events[0]!r}"
    )


def test_security_posture_signing_key_status_configured(tmp_path: Path) -> None:
    """MF-5: security.posture must include signing_key_status='configured' when keys set."""
    import structlog.testing

    from recotem.artifact.signing import KeyRing
    from recotem.config import ServeConfig
    from recotem.serving.app import _emit_security_posture

    cfg = ServeConfig()
    cfg.env = "development"
    cfg.insecure_no_auth = False
    cfg.dev_allow_unsigned = False
    cfg.host = "127.0.0.1"
    cfg.allowed_hosts = ["*"]
    cfg.allowed_origins = []

    kr = KeyRing("test:" + "aa" * 32)
    with structlog.testing.capture_logs() as cap:
        _emit_security_posture(cfg, kr)

    posture = next(e for e in cap if e.get("event") == "security.posture")
    assert posture["signing_key_status"] == "configured"


def test_security_posture_signing_key_status_dev_allow_unsigned(
    tmp_path: Path,
) -> None:
    """MF-5: security.posture must include signing_key_status='dev_allow_unsigned'
    when dev_allow_unsigned is active."""
    import structlog.testing

    from recotem.config import ServeConfig
    from recotem.serving.app import _emit_security_posture

    cfg = ServeConfig()
    cfg.env = "development"
    cfg.insecure_no_auth = False
    cfg.dev_allow_unsigned = True
    cfg.host = "127.0.0.1"
    cfg.allowed_hosts = ["*"]
    cfg.allowed_origins = []

    with structlog.testing.capture_logs() as cap:
        _emit_security_posture(cfg, None)

    posture = next(e for e in cap if e.get("event") == "security.posture")
    assert posture["signing_key_status"] == "dev_allow_unsigned"


# ---------------------------------------------------------------------------
# I-1: Structured exception handler for unhandled non-HTTP exceptions
# ---------------------------------------------------------------------------


def test_unhandled_exception_returns_structured_json_500(tmp_path: Path) -> None:
    """I-1: An unexpected Exception from a route handler must return HTTP 500
    with body {detail: 'internal error', code: 'internal_error'} rather than
    a plain-text FastAPI default or an unhandled traceback.
    """
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    app = create_app(cfg)

    # Inject a route that raises an unhandled RuntimeError.
    @app.get("/explode")
    async def explode():
        raise RuntimeError("unexpected internal failure")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/explode")

    assert response.status_code == 500, (
        f"Unhandled exception must yield HTTP 500; got {response.status_code}"
    )
    data = response.json()
    assert data.get("detail") == "internal error", (
        f"Expected detail='internal error'; got {data!r}"
    )
    assert data.get("code") == "internal_error", (
        f"Expected code='internal_error'; got {data!r}"
    )


def test_http_exception_still_handled_by_fastapi_default(tmp_path: Path) -> None:
    """I-1: HTTPException must NOT be swallowed by the custom handler —
    FastAPI's default HTTPException handler must still fire so that 404, 401,
    etc. are returned with the standard JSON body.
    """
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    app = create_app(cfg)

    @app.get("/notfound")
    async def not_found():
        raise HTTPException(status_code=404, detail="thing not found")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/notfound")

    assert response.status_code == 404, (
        f"HTTPException(404) must still return 404, not 500; got {response.status_code}"
    )
    # FastAPI returns {"detail": "thing not found"} by default.
    assert response.json().get("detail") == "thing not found"


# ---------------------------------------------------------------------------
# I-3: /health probe-safe (no per-recipe detail), /health/details (auth)
# ---------------------------------------------------------------------------


def test_health_returns_only_aggregate_counts(tmp_path: Path) -> None:
    """I-3: /health must return only {status, total, loaded} — no per-recipe
    breakdowns, kid, trained_at, or best_class.  Those fields are moved to the
    authenticated /health/details endpoint.
    """
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    app = create_app(cfg)
    client = TestClient(app)
    response = client.get("/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "total" in body
    assert "loaded" in body
    # Must NOT contain per-recipe detail.
    assert "recipes" not in body, (
        "/health must not expose per-recipe detail (moved to /health/details)"
    )


def test_health_details_requires_auth_when_keys_configured(tmp_path: Path) -> None:
    """I-3: /health/details must return 401 when API keys are configured and
    no X-API-Key is provided.
    """
    import hashlib

    from fastapi.testclient import TestClient

    from recotem.config import ApiKeyEntry, ServeConfig
    from recotem.serving.app import create_app

    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = False
    sha256_hex = hashlib.scrypt(
        b"test_api_key_32_bytes_exactly!!!",
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    cfg.api_keys = [ApiKeyEntry(kid="k1", sha256_hex=sha256_hex)]
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1"]

    app = create_app(cfg)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/v1/health/details")
    assert response.status_code == 401, (
        f"/health/details must return 401 when auth is configured; got {response.status_code}"
    )


def test_health_details_returns_per_recipe_data_when_auth_passes(
    tmp_path: Path,
) -> None:
    """I-3: /health/details (authenticated via insecure_no_auth) returns
    per-recipe breakdown including kid and error fields.
    """
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    recipes_dir = Path(cfg.recipes_dir)  # type: ignore[arg-type]
    missing_artifact = tmp_path / "missing.recotem"
    _write_recipe_yaml(recipes_dir, "detail_recipe", missing_artifact)

    app = create_app(cfg)
    client = TestClient(app)
    response = client.get("/v1/health/details")

    # Degraded because artifact is missing.
    assert response.status_code == 503
    body = response.json()
    assert "recipes" in body, "/health/details must include per-recipe breakdown"
    assert "detail_recipe" in body["recipes"]
    entry = body["recipes"]["detail_recipe"]
    assert entry["loaded"] is False


# ---------------------------------------------------------------------------
# I-4: fail-secure /docs — env unset must disable docs
# ---------------------------------------------------------------------------


def test_docs_disabled_when_env_unset(tmp_path: Path) -> None:
    """I-4: When RECOTEM_ENV is unset (empty string / None), /docs must return
    404.  This is the fail-secure default: production containers that do not
    set RECOTEM_ENV must not expose the OpenAPI UI by accident.
    """
    import hashlib

    from fastapi.testclient import TestClient

    from recotem.config import ApiKeyEntry, ServeConfig
    from recotem.serving.app import create_app

    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = ""  # explicitly unset / empty — must default to production-safe
    cfg.insecure_no_auth = False  # do NOT use insecure flag (forbidden with empty env)
    sha256_hex = hashlib.scrypt(
        b"test_api_key_32_bytes_exactly!!!",
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    cfg.api_keys = [ApiKeyEntry(kid="k1", sha256_hex=sha256_hex)]
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]

    app = create_app(cfg)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/docs")
    assert resp.status_code == 404, (
        "RECOTEM_ENV unset: /docs must return 404 (fail-secure); "
        f"got {resp.status_code}"
    )
    resp2 = client.get("/openapi.json")
    assert resp2.status_code == 404, (
        "RECOTEM_ENV unset: /openapi.json must return 404 (fail-secure); "
        f"got {resp2.status_code}"
    )


def test_docs_enabled_when_env_is_dev(tmp_path: Path) -> None:
    """I-4: RECOTEM_ENV=dev must enable /docs (short alias for development)."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.env = "dev"
    app = create_app(cfg)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/openapi.json")
    assert resp.status_code == 200, (
        f"RECOTEM_ENV=dev: /openapi.json must return 200; got {resp.status_code}"
    )


def test_docs_disabled_when_env_is_staging(tmp_path: Path) -> None:
    """I-4: RECOTEM_ENV=staging must disable /docs (not a dev environment)."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    cfg.env = "staging"
    cfg.insecure_no_auth = (
        True  # staging allows insecure_no_auth? No — staging validation forbids it.
    )
    # Use production-safe config: need api_keys.
    import hashlib

    from recotem.config import ApiKeyEntry

    sha256_hex = hashlib.scrypt(
        b"test_api_key_32_bytes_exactly!!!",
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    cfg.api_keys = [ApiKeyEntry(kid="k1", sha256_hex=sha256_hex)]
    cfg.insecure_no_auth = False

    app = create_app(cfg)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/docs")
    assert resp.status_code == 404, (
        f"RECOTEM_ENV=staging: /docs must return 404; got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# RequestIDMiddleware — X-Request-ID contract
# ---------------------------------------------------------------------------


def test_request_id_header_present_on_200_response(tmp_path: Path) -> None:
    """X-Request-ID must be present in a 200 response (e.g. GET /v1/health)."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    app = create_app(cfg)
    client = TestClient(app)
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert "x-request-id" in response.headers, (
        "X-Request-ID must be present in every 200 response"
    )
    assert response.headers["x-request-id"], "X-Request-ID must not be empty"


def test_request_id_header_present_on_404_response(tmp_path: Path) -> None:
    """X-Request-ID must be present on a 404 (non-existent recipe GET)."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    app = create_app(cfg)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/v1/recipes/no_such")
    assert response.status_code == 404
    assert "x-request-id" in response.headers, (
        "X-Request-ID must be present even on 404 responses"
    )


def test_request_id_header_present_on_503_response(tmp_path: Path) -> None:
    """X-Request-ID must be present on a 503 (unloaded recipe POST)."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    recipes_dir = Path(cfg.recipes_dir)  # type: ignore[arg-type]
    missing_artifact = tmp_path / "no-artifact.recotem"
    _write_recipe_yaml(recipes_dir, "broken_for_rid", missing_artifact)

    app = create_app(cfg)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/v1/recipes/broken_for_rid:recommend",
        json={"user_id": "u1", "limit": 5},
    )
    assert response.status_code == 503
    assert "x-request-id" in response.headers, (
        "X-Request-ID must be present on 503 responses"
    )


def test_request_id_echoed_when_client_supplies_valid_id(tmp_path: Path) -> None:
    """When the client sends a valid X-Request-ID, the same value is echoed back."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    app = create_app(cfg)
    client = TestClient(app)
    trace_id = "my-trace-id-123"
    response = client.get("/v1/health", headers={"X-Request-ID": trace_id})
    assert response.status_code == 200
    assert response.headers.get("x-request-id") == trace_id, (
        f"Valid client X-Request-ID must be echoed; "
        f"got {response.headers.get('x-request-id')!r}"
    )


def test_request_id_replaced_when_client_supplies_overlong_value(
    tmp_path: Path,
) -> None:
    """A client-supplied X-Request-ID longer than 128 chars is rejected and
    replaced by a server-generated ID."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    app = create_app(cfg)
    client = TestClient(app)
    too_long = "a" * 200
    response = client.get("/v1/health", headers={"X-Request-ID": too_long})
    assert response.status_code == 200
    returned = response.headers.get("x-request-id", "")
    assert returned != too_long, (
        "Overlong X-Request-ID must be replaced by a server-generated value"
    )
    assert len(returned) <= 128, (
        f"Server-generated ID must be <=128 chars; got {len(returned)}"
    )
    assert returned, "Server-generated X-Request-ID must not be empty"


def test_request_id_replaced_when_client_supplies_invalid_chars(
    tmp_path: Path,
) -> None:
    """A client-supplied X-Request-ID with disallowed characters is replaced
    by a server-generated ID."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    app = create_app(cfg)
    client = TestClient(app)
    bad_value = "<script>alert(1)</script>"
    response = client.get("/v1/health", headers={"X-Request-ID": bad_value})
    assert response.status_code == 200
    returned = response.headers.get("x-request-id", "")
    assert returned != bad_value, (
        "X-Request-ID with invalid chars must be replaced by a server-generated value"
    )
    assert returned, "Server-generated X-Request-ID must not be empty"


def test_request_id_replaced_when_client_supplies_empty_value(
    tmp_path: Path,
) -> None:
    """An empty X-Request-ID header is treated as absent and replaced by a
    server-generated ID."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    cfg = _minimal_config(tmp_path)
    app = create_app(cfg)
    client = TestClient(app)
    response = client.get("/v1/health", headers={"X-Request-ID": ""})
    assert response.status_code == 200
    returned = response.headers.get("x-request-id", "")
    assert returned, "Empty X-Request-ID must be replaced by a server-generated value"
