# tests/unit/test_v1_recommend_related.py
"""POST /v1/recipes/{name}:recommend-related — single items→items."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app


def _client_with_recommender(rec) -> TestClient:
    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc123"),
        loaded_at_unix=1747800000.0,
    )
    registry = ModelRegistry()
    registry.replace("demo", entry)
    return TestClient(build_v1_app(registry))


def test_related_returns_items():
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i9", 0.7), ("i8", 0.6)]
    r = _client_with_recommender(rec).post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": ["7203"], "limit": 5},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["item_id"] for i in body["items"]] == ["i9", "i8"]
    rec.get_recommendation_for_new_user.assert_called_once_with(["7203"], 5)


def test_related_422_on_empty_seed_items():
    rec = MagicMock()
    r = _client_with_recommender(rec).post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": []},
    )
    assert r.status_code == 422


def test_related_404_when_all_seeds_unknown_returns_empty():
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = []
    r = _client_with_recommender(rec).post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": ["zzz"]},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "UNKNOWN_SEED_ITEMS"
    assert isinstance(body["detail"], str)


def test_related_404_when_recipe_missing_from_registry():
    rec = MagicMock()
    r = _client_with_recommender(rec).post(
        "/v1/recipes/unknown:recommend-related",
        json={"seed_items": ["i1"]},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "RECIPE_NOT_FOUND"
    assert isinstance(body["detail"], str)


def test_related_503_when_recipe_stub_not_loaded():
    stub = ModelEntry(
        name="demo",
        recommender=None,
        header={},
        kid="",
        loaded=False,
    )
    registry = ModelRegistry()
    registry.replace("demo", stub)
    r = TestClient(build_v1_app(registry)).post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": ["i1"]},
    )
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "RECIPE_UNAVAILABLE"
    assert isinstance(body["detail"], str)


def test_recommend_related_sets_model_version_response_header():
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i9", 0.7)]
    r = _client_with_recommender(rec).post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": ["seed1"], "limit": 1},
    )
    assert r.status_code == 200, r.text
    header_val = r.headers.get("x-recotem-model-version")
    assert header_val, "X-Recotem-Model-Version header must be present and non-empty"
    assert header_val == r.json()["model_version"]
