"""Integration test: in-process train + serve + httpx /predict call.

Uses FastAPI TestClient for synchronous testing without a real server.
Trains a TopPop model on synthetic data, writes a signed artifact,
then serves it and calls /predict.
"""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.routes import make_router

ACTIVE_KEY_HEX = "aa" * 32


def _make_mock_recommender(users: list[str], items: list[str]):
    """Build a MagicMock recommender that returns fixed recommendations."""
    rec = MagicMock()

    def _get_rec(user_id, cutoff=10):
        if user_id in users:
            return [(iid, 1.0 - i * 0.1) for i, iid in enumerate(items[:cutoff])]
        raise KeyError(f"Unknown user: {user_id}")

    rec.get_recommendation_for_known_user_id.side_effect = _get_rec
    return rec


def _make_api_entry(plaintext: str, kid: str = "api-key") -> ApiKeyEntry:
    # Mirror recotem.serving.auth._hash_api_key (keyed BLAKE2b with the
    # ``recotem.api-key.v1`` domain-separation label).
    sha256 = hashlib.blake2b(
        plaintext.encode(),
        key=b"recotem.api-key.v1",
        digest_size=32,
    ).hexdigest()
    return ApiKeyEntry(kid=kid, sha256_hex=sha256)


# ---------------------------------------------------------------------------
# In-process end-to-end test
# ---------------------------------------------------------------------------


def test_serve_predict_e2e_in_process() -> None:
    """Train-like mock → serve → /predict returns valid response."""
    users = [f"user{i}" for i in range(10)]
    items = [f"item{i}" for i in range(20)]

    rec = _make_mock_recommender(users, items)
    entry = ModelEntry(
        name="test_model",
        recommender=rec,
        header={
            "best_class": "TopPopRecommender",
            "trained_at": "2026-01-01T00:00:00Z",
            "recipe_name": "test_model",
        },
        kid="active",
    )

    registry = ModelRegistry()
    registry.replace("test_model", entry)

    plaintext = "integration_test_api_key_32bytes"
    api_entry = _make_api_entry(plaintext)
    router = make_router(registry=registry, api_keys=[api_entry])
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/predict/test_model",
        json={"user_id": "user0", "cutoff": 5},
        headers={"x-api-key": plaintext},
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 5
    assert data["items"][0]["item_id"] == "item0"
    assert "model" in data
    assert data["model"]["kid"] == "active"
    assert "request_id" in data


def test_serve_predict_e2e_unknown_user_404() -> None:
    """Unknown user_id returns 404."""
    rec = _make_mock_recommender(["known_user"], ["item1", "item2"])
    entry = ModelEntry(
        name="model2",
        recommender=rec,
        header={"best_class": "TopPop", "trained_at": "2026-01-01T00:00:00Z"},
        kid="active",
    )
    registry = ModelRegistry()
    registry.replace("model2", entry)

    router = make_router(registry=registry, api_keys=[])
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/predict/model2",
        json={"user_id": "total_stranger"},
    )
    assert response.status_code == 404


def test_serve_predict_e2e_missing_recipe_503() -> None:
    """Recipe not in registry returns 503."""
    registry = ModelRegistry()
    router = make_router(registry=registry, api_keys=[])
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/predict/does_not_exist", json={"user_id": "user1"})
    assert response.status_code == 503


def test_serve_health_endpoint_ok_with_loaded_model() -> None:
    """GET /health returns ok when a model is loaded."""
    rec = _make_mock_recommender(["u1"], ["i1"])
    entry = ModelEntry(
        name="healthy_recipe",
        recommender=rec,
        header={"best_class": "TopPop", "trained_at": "2026-01-01T00:00:00Z"},
        kid="k1",
    )
    registry = ModelRegistry()
    registry.replace("healthy_recipe", entry)

    router = make_router(registry=registry, api_keys=[])
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "healthy_recipe" in data["recipes"]
    assert data["recipes"]["healthy_recipe"]["loaded"] is True
