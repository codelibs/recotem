# tests/unit/test_v1_batch_recommend_related.py
"""POST /v1/recipes/{name}:batch-recommend-related — multi-seed bulk."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app


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
    return TestClient(build_v1_app(registry))


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


def test_batch_related_404_when_recipe_missing_from_registry():
    rec = MagicMock()
    r = _client(rec).post(
        "/v1/recipes/unknown:batch-recommend-related",
        json={"requests": [{"seed_items": ["i1"]}]},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "RECIPE_NOT_FOUND"
    assert isinstance(body["detail"], str)


def test_batch_related_503_when_recipe_stub_not_loaded():
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
        "/v1/recipes/demo:batch-recommend-related",
        json={"requests": [{"seed_items": ["i1"]}]},
    )
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "RECIPE_UNAVAILABLE"
    assert isinstance(body["detail"], str)


def test_batch_related_422_on_empty_seed_in_one_entry():
    rec = MagicMock()
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={
            "requests": [{"seed_items": []}],
        },
    )
    assert r.status_code == 422


def test_batch_recommend_related_sets_model_version_response_header():
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i9", 0.7)]
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={"requests": [{"seed_items": ["seed1"]}]},
    )
    assert r.status_code == 200, r.text
    header_val = r.headers.get("x-recotem-model-version")
    assert header_val, "X-Recotem-Model-Version header must be present and non-empty"
    assert header_val == r.json()["model_version"]
