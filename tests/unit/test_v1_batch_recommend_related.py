# tests/unit/test_v1_batch_recommend_related.py
"""POST /v1/recipes/{name}:batch-recommend-related — multi-seed bulk."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.v1_router import make_v1_router


def _client(rec) -> TestClient:
    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="t",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc"),
        loaded_at_unix=1.0,
    )
    registry = ModelRegistry()
    registry.replace("demo", entry)
    app = FastAPI()
    app.include_router(make_v1_router(registry, []), prefix="/v1")
    return TestClient(app)


def test_batch_related_mixed_success_and_failure():
    rec = MagicMock()
    rec.get_recommendation_for_new_user.side_effect = [
        [("i9", 0.7)],
        [],  # all-unknown seeds
        [("i3", 0.5)],
    ]
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={
            "requests": [
                {"seed_items": ["7203"]},
                {"seed_items": ["zzz"]},
                {"seed_items": ["9984"]},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["status"] for e in body["results"]] == ["ok", "error", "ok"]
    assert body["results"][1]["error"]["code"] == "UNKNOWN_SEED_ITEMS"


def test_batch_related_422_on_empty_seed_in_one_entry():
    rec = MagicMock()
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={
            "requests": [{"seed_items": []}],
        },
    )
    assert r.status_code == 422
