# tests/unit/test_v1_health_metrics.py
"""Verify /v1/health, /v1/health/details, and /v1/metrics behave like
their legacy counterparts but mounted under /v1.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app


def _client(api_keys: list[ApiKeyEntry] | None = None) -> TestClient:
    registry = ModelRegistry()
    return TestClient(build_v1_app(registry, api_keys=api_keys or []))


def _make_api_entry(plaintext: str, kid: str = "k1") -> ApiKeyEntry:
    sha256_hex = hashlib.scrypt(
        plaintext.encode(),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    return ApiKeyEntry(kid=kid, sha256_hex=sha256_hex)


def _entry() -> ApiKeyEntry:
    return _make_api_entry("api_key_32_bytes_exactly_here!!!")


def test_health_returns_ok_with_empty_registry():
    r = _client().get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["total"] == 0
    assert body["loaded"] == 0


def test_health_details_requires_auth():
    r = _client(api_keys=[_entry()]).get("/v1/health/details")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# A. /v1/metrics endpoint gating (I4)
# ---------------------------------------------------------------------------


def test_metrics_endpoint_404_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RECOTEM_METRICS_ENABLED", raising=False)
    from recotem.serving import metrics as _m

    monkeypatch.setattr(_m, "metrics_enabled", lambda: False)
    client = _client(api_keys=[_entry()])
    r = client.get("/v1/metrics")
    assert r.status_code == 404


def test_metrics_endpoint_404_when_env_falsy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "0")
    from recotem.serving import metrics as _m

    monkeypatch.setattr(_m, "metrics_enabled", lambda: False)
    client = _client(api_keys=[_entry()])
    r = client.get("/v1/metrics")
    assert r.status_code == 404


def test_metrics_endpoint_requires_auth_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "1")
    from recotem.serving import metrics as _m

    monkeypatch.setattr(_m, "metrics_enabled", lambda: True)
    plaintext = "metrics_test_key_32_bytes_padded!"
    api_entry = _make_api_entry(plaintext)
    client = _client(api_keys=[api_entry])
    r = client.get("/v1/metrics")
    assert r.status_code == 401


def test_metrics_endpoint_200_with_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "1")
    from recotem.serving import metrics as _m

    monkeypatch.setattr(_m, "metrics_enabled", lambda: True)
    plaintext = "metrics_test_key_32_bytes_padded!"
    api_entry = _make_api_entry(plaintext)
    client = _client(api_keys=[api_entry])
    r = client.get("/v1/metrics", headers={"X-API-Key": plaintext})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "# HELP" in r.text or "# TYPE" in r.text


# ---------------------------------------------------------------------------
# T7: /v1/health/details per-recipe full shape
# ---------------------------------------------------------------------------


def _make_loaded_entry_with_header(name: str) -> ModelEntry:
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    rec._mapper = MagicMock()
    rec._mapper.user_id_to_index = {"u1": 0}
    return ModelEntry(
        name=name,
        recommender=rec,
        header={
            "best_class": "TopPop",
            "trained_at": "2026-01-01T00:00:00Z",
        },
        kid="active",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "e" * 64),
        loaded_at_unix=1747800000.0,
    )


def _make_stub_entry_with_error(name: str) -> ModelEntry:
    return ModelEntry(
        name=name,
        recommender=None,
        header={},
        kid="",
        metadata_df=None,
        last_load_error="artifact load failed: HMAC verify failed",
        artifact_path="",
        loaded=False,
    )


def test_health_details_per_recipe_shape_with_healthy_recipe() -> None:
    """A healthy (loaded=True) recipe entry in /v1/health/details includes:
    - loaded: True
    - best_class (from header)
    - trained_at (from header)
    - kid
    - no 'error' field
    """
    registry = ModelRegistry()
    registry.replace("healthy_recipe", _make_loaded_entry_with_header("healthy_recipe"))
    client = TestClient(build_v1_app(registry))

    r = client.get("/v1/health/details")
    # No api_keys → accessible without auth; only loaded recipe → status ok.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    recipes = body["recipes"]
    assert "healthy_recipe" in recipes, (
        f"healthy_recipe must appear in /v1/health/details; got {list(recipes.keys())}"
    )
    entry_health = recipes["healthy_recipe"]
    assert entry_health["loaded"] is True
    assert entry_health.get("best_class") == "TopPop"
    assert entry_health.get("trained_at") == "2026-01-01T00:00:00Z"
    assert entry_health.get("kid") == "active"
    assert "error" not in entry_health, (
        f"Healthy entry must not have 'error' field; got {entry_health!r}"
    )


def test_health_details_per_recipe_shape_with_failed_recipe() -> None:
    """A stub (loaded=False) recipe entry in /v1/health/details includes:
    - loaded: False
    - error string
    - no best_class, trained_at, or kid (they default to empty/absent)
    """
    registry = ModelRegistry()
    registry.replace("broken_recipe", _make_stub_entry_with_error("broken_recipe"))
    client = TestClient(build_v1_app(registry))

    r = client.get("/v1/health/details")
    # Unloaded recipe → degraded → 503.
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "degraded"
    recipes = body["recipes"]
    assert "broken_recipe" in recipes
    entry_health = recipes["broken_recipe"]
    assert entry_health["loaded"] is False
    assert "error" in entry_health, (
        "Stub entry with last_load_error must expose 'error' in health details"
    )
    assert "HMAC" in entry_health["error"] or "artifact load" in entry_health["error"]


def test_health_details_shows_two_recipes_one_healthy_one_stub() -> None:
    """When 2 recipes exist (1 healthy, 1 stub), /v1/health/details returns both
    and the overall status is 'degraded'.
    """
    registry = ModelRegistry()
    registry.replace("good", _make_loaded_entry_with_header("good"))
    registry.replace("bad", _make_stub_entry_with_error("bad"))
    client = TestClient(build_v1_app(registry))

    r = client.get("/v1/health/details")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "degraded"
    recipes = body["recipes"]
    assert "good" in recipes
    assert "bad" in recipes

    # Good recipe is loaded and has no error.
    assert recipes["good"]["loaded"] is True
    assert "error" not in recipes["good"]

    # Bad recipe is not loaded and has an error.
    assert recipes["bad"]["loaded"] is False
    assert "error" in recipes["bad"]
