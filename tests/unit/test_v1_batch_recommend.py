# tests/unit/test_v1_batch_recommend.py
"""POST /v1/recipes/{name}:batch-recommend — multi-user bulk."""

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
    stub = ModelEntry(
        name="demo",
        recommender=None,
        header={},
        kid="",
        loaded=False,
    )
    registry = ModelRegistry()
    registry.replace("demo", stub)
    client = TestClient(build_v1_app(registry))
    r = client.post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1"}]},
    )
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "RECIPE_UNAVAILABLE"
    assert isinstance(body["detail"], str)


def test_batch_recommend_404_when_recipe_missing_from_registry():
    rec = MagicMock()
    r = _client(rec).post(
        "/v1/recipes/unknown:batch-recommend",
        json={"requests": [{"user_id": "u1"}]},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "RECIPE_NOT_FOUND"
    assert isinstance(body["detail"], str)


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


# ---------------------------------------------------------------------------
# F. Aggregate cap, non-KeyError handling, extra field
# ---------------------------------------------------------------------------


def test_batch_aggregate_limit_cap_exceeded() -> None:
    """Aggregate limit cap is now enforced per-element rather than at the
    schema level: 10 × 501 = 5010 > 5000, so the LAST element (and only the
    last) is rejected with VALIDATION_ERROR. Earlier elements still execute."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = []
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": f"u{i}", "limit": 501} for i in range(10)]},
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    # First 9 elements (sum = 9*501 = 4509) succeed; the 10th would push
    # the running aggregate to 5010, so it is rejected with
    # VALIDATION_ERROR.
    assert results[0]["status"] == "ok"
    assert results[-1]["status"] == "error"
    assert results[-1]["error"]["code"] == "VALIDATION_ERROR"


def test_batch_aggregate_limit_cap_boundary() -> None:
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = []
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": f"u{i}", "limit": 500} for i in range(10)]},
    )
    assert r.status_code == 200, r.text


def test_batch_element_runtime_error_yields_internal_error() -> None:
    rec = MagicMock()

    def _side_effect(user_id, limit):
        if user_id == "bad-user":
            raise RuntimeError("exploded")
        return [("i1", 0.9)]

    rec.get_recommendation_for_known_user_id.side_effect = _side_effect
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={
            "requests": [
                {"user_id": "ok-user"},
                {"user_id": "bad-user"},
                {"user_id": "ok-user2"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "error"
    assert results[1]["error"]["code"] == "INTERNAL_ERROR"
    assert "bad-user" not in results[1]["error"].get("message", "")
    assert results[2]["status"] == "ok"


def test_batch_rejects_extra_field_on_request_element() -> None:
    """A bad sub-element now becomes status=error, code=VALIDATION_ERROR
    rather than 422'ing the whole batch — that's the contract documented
    for ``:batch-recommend``."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = []
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1", "extra_field": "boom"}]},
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["status"] == "error"
    assert results[0]["error"]["code"] == "VALIDATION_ERROR"


def test_batch_recommend_per_request_limit_validation():
    """Per-element schema violations surface as VALIDATION_ERROR in the
    individual result entry; valid siblings continue to be processed."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = []

    r_zero = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={
            "requests": [
                {"user_id": "u-good"},
                {"user_id": "u1", "limit": 0},  # below the floor
            ]
        },
    )
    assert r_zero.status_code == 200, r_zero.text
    rs = r_zero.json()["results"]
    assert rs[0]["status"] == "ok"
    assert rs[1]["status"] == "error"
    assert rs[1]["error"]["code"] == "VALIDATION_ERROR"

    r_over = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1", "limit": 1001}]},  # above ceiling
    )
    assert r_over.status_code == 200, r_over.text
    assert r_over.json()["results"][0]["status"] == "error"
    assert r_over.json()["results"][0]["error"]["code"] == "VALIDATION_ERROR"
