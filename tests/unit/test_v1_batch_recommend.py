# tests/unit/test_v1_batch_recommend.py
"""POST /v1/recipes/{name}:batch-recommend — multi-user bulk."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.routes import make_router


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
    app.include_router(make_router(registry, []), prefix="/v1")
    return TestClient(app)


def test_batch_recommend_mixed_success_and_failure():
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = [
        [("i1", 0.9)],
        KeyError("u2"),
        [("i3", 0.5)],
    ]
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={
            "requests": [
                {"user_id": "u1"},
                {"user_id": "u2"},
                {"user_id": "u3"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recipe"] == "demo"
    assert len(body["results"]) == 3
    assert body["results"][0] == {
        "index": 0,
        "status": "ok",
        "items": [{"item_id": "i1", "score": 0.9}],
        "error": None,
    }
    assert body["results"][1]["status"] == "error"
    assert body["results"][1]["error"]["code"] == "UNKNOWN_USER"
    assert body["results"][2]["status"] == "ok"


def test_batch_recommend_503_when_recipe_unavailable():
    rec = MagicMock()
    stub = ModelEntry(
        name="demo",
        recommender=None,
        header={},
        kid="",
        loaded=False,
    )
    registry = ModelRegistry()
    registry.replace("demo", stub)
    app = FastAPI()
    app.include_router(make_router(registry, []), prefix="/v1")
    client = TestClient(app)
    r = client.post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1"}]},
    )
    assert r.status_code == 503


def test_batch_recommend_404_when_recipe_missing_from_registry():
    rec = MagicMock()
    r = _client(rec).post(
        "/v1/recipes/unknown:batch-recommend",
        json={"requests": [{"user_id": "u1"}]},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RECIPE_NOT_FOUND"


def test_batch_recommend_422_on_too_many_requests():
    rec = MagicMock()
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": f"u{i}"} for i in range(257)]},
    )
    assert r.status_code == 422


def test_batch_recommend_sets_model_version_response_header():
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1"}]},
    )
    assert r.status_code == 200, r.text
    header_val = r.headers.get("x-recotem-model-version")
    assert header_val, "X-Recotem-Model-Version header must be present and non-empty"
    assert header_val == r.json()["model_version"]


def test_batch_recommend_per_request_limit_validation():
    rec = MagicMock()
    # limit=0 is below the minimum of 1
    r_zero = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1", "limit": 0}]},
    )
    assert r_zero.status_code == 422

    # limit=1001 exceeds the maximum of 1000
    r_over = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1", "limit": 1001}]},
    )
    assert r_over.status_code == 422
