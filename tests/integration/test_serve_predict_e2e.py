"""Integration test: in-process train + serve + httpx /predict call.

Uses FastAPI TestClient for synchronous testing without a real server.
Trains a TopPop model on synthetic data, writes a signed artifact,
then serves it and calls /predict.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
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
    # Mirror recotem.serving.auth._hash_api_key (scrypt KDF with the
    # ``recotem.api-key.v1`` domain-separation salt at minimum cost).
    sha256 = hashlib.scrypt(
        plaintext.encode(),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
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


# ---------------------------------------------------------------------------
# Full artifact roundtrip: train → write → SafeUnpickler read
# ---------------------------------------------------------------------------


def _make_tiny_synthetic_csv(tmp_path: Path, n_users: int = 10, n_items: int = 10) -> Path:
    """Create a minimal synthetic CSV with n_users users and n_items items.

    Each user rates every item exactly once, yielding n_users * n_items rows
    with no duplicates.  With 10 users and 10 items the default cutoff=5
    stays safely below the item count.
    """
    rows = ["user_id,item_id"]
    for u in range(n_users):
        for i in range(n_items):
            rows.append(f"u{u},i{i}")
    csv_file = tmp_path / "synthetic.csv"
    csv_file.write_text("\n".join(rows) + "\n")
    return csv_file


def test_train_then_serve_full_artifact_roundtrip(tmp_path: Path) -> None:
    """Train on synthetic CSV with dev_allow_unsigned, write a real artifact,
    then read it back via SafeUnpickler and assert numpy / irspack submodule
    paths deserialize end-to-end without ArtifactError.
    """
    from recotem.artifact.io import read_artifact
    from recotem.artifact.signing import KeyRing, unpickle_payload
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.training._compat import IDMappedRecommender
    from recotem.training.pipeline import run_training

    # 10 users × 10 items = 100 rows, no duplicates.
    # cutoff=5 safely below the 10 unique items after split.
    csv_file = _make_tiny_synthetic_csv(tmp_path, n_users=10, n_items=10)
    artifact_path = str(tmp_path / "synthetic.recotem")

    recipe = Recipe(
        name="synthetic-e2e",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=["TopPop"],
            n_trials=1,
            cutoff=5,  # must be < n_items to avoid irspack ValueError
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=0),
        ),
        output=OutputConfig(path=artifact_path, versioning="always_overwrite"),
    )

    # Use an in-memory dev key so no RECOTEM_SIGNING_KEYS env var is required.
    kr = KeyRing("dev:" + ("0" * 64))
    result = run_training(
        recipe,
        key_ring=kr,
        signing_key="dev",
        no_lock=True,
        dev_allow_unsigned=True,
        quiet=True,
    )

    assert result is not None, "run_training returned None unexpectedly"
    assert result.best_class is not None

    # Read the artifact back with the same KeyRing — exercises write_artifact's
    # positional-arg signature and the full binary format end-to-end.
    written_path = result.artifact_path
    header, payload_bytes = read_artifact(written_path, kr)

    # Deserialize via SafeUnpickler.  This exercises the numpy / irspack prefix
    # allow-list with a real trained model object (numpy arrays, scipy sparse
    # matrices, irspack recommender classes).
    recommender = unpickle_payload(payload_bytes)
    assert recommender is not None

    # Must be the IDMappedRecommender wrapper that irspack training produces.
    assert isinstance(recommender, IDMappedRecommender)

    # The recommender has user/item mappings populated.
    assert len(recommender.user_ids) > 0
    assert len(recommender.item_ids) > 0

    # Try recommendations for a new (cold-start) user with a subset of items
    # seen in training — this always works for TopPop (non-personalised).
    known_items = recommender.item_ids[:3]
    recs = recommender.get_recommendation_for_new_user(known_items, cutoff=3)
    assert len(recs) > 0
    # Each recommendation is a (item_id, score) pair.
    assert isinstance(recs[0][0], str)
    assert isinstance(recs[0][1], float)
