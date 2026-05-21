# tests/unit/test_v1_recommend.py
"""POST /v1/recipes/{name}:recommend — single user→items."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.routes import make_router


def _entry_with_recommender(recommender) -> ModelEntry:
    """Build a loaded ModelEntry around the given recommender mock.

    The artifact SHA-256 lives on `_loaded_marker[1]`; pass it through
    that field rather than introducing a parallel attribute.
    """
    return ModelEntry(
        name="demo",
        recommender=recommender,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc123"),
        loaded_at_unix=1747800000.0,
    )


def _app_with_entry(entry: ModelEntry) -> TestClient:
    registry = ModelRegistry()
    registry.replace("demo", entry)
    app = FastAPI()
    app.include_router(
        make_router(registry=registry, api_keys=[]),
        prefix="/v1",
    )
    return TestClient(app)


def test_recommend_returns_items_and_envelope():
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9), ("i2", 0.5)]
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recipe"] == "demo"
    assert body["model_version"] == "sha256:abc123"
    assert [i["item_id"] for i in body["items"]] == ["i1", "i2"]
    assert "request_id" in body
    rec.get_recommendation_for_known_user_id.assert_called_once_with("u1", 2)


def test_recommend_404_when_user_unknown():
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = KeyError("u1")
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 404
    body = r.json()
    assert body["detail"]["code"] == "UNKNOWN_USER"


def test_recommend_503_when_recipe_not_loaded():
    stub = ModelEntry(
        name="demo",
        recommender=None,
        header={},
        kid="",
        loaded=False,
    )
    client = _app_with_entry(stub)
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "RECIPE_UNAVAILABLE"


def test_recommend_422_on_empty_user_id():
    rec = MagicMock()
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "", "limit": 5})
    assert r.status_code == 422


def test_recommend_404_when_recipe_missing_from_registry():
    rec = MagicMock()
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/unknown:recommend", json={"user_id": "u1"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RECIPE_NOT_FOUND"


def test_recommend_sets_model_version_response_header():
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 1})
    assert r.status_code == 200, r.text
    header_val = r.headers.get("x-recotem-model-version")
    assert header_val, "X-Recotem-Model-Version header must be present and non-empty"
    assert header_val == r.json()["model_version"]
