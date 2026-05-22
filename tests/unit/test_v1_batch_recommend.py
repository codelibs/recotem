# tests/unit/test_v1_batch_recommend.py
"""POST /v1/recipes/{name}:batch-recommend — multi-user bulk."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app

_FAKE_SHA256_HEX = "b" * 64  # 64 lowercase hex chars for a valid Sha256Hex marker


def _client(rec) -> TestClient:
    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="t",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
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
    # Under the discriminated-union schema, BatchResultOk has extra="forbid"
    # and does NOT carry an "error" field.  Assert field by field rather than
    # doing an equality check against a literal dict that includes "error": None.
    ok_result = body["results"][0]
    assert ok_result["index"] == 0
    assert ok_result["status"] == "ok"
    assert ok_result["items"] == [{"item_id": "i1", "score": 0.9}]
    assert "error" not in ok_result, (
        "BatchResultOk (discriminated union) must not carry an 'error' key"
    )
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


# ---------------------------------------------------------------------------
# Finding 3: model_version header on partial-failure batch response
# ---------------------------------------------------------------------------


def _client_with_metadata(rec, meta_index: dict | None = None) -> TestClient:
    """Build a client whose entry has a metadata_index pre-populated."""
    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="t",
        metadata_df=None,
        metadata_index=meta_index,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1.0,
    )
    registry = ModelRegistry()
    registry.replace("demo", entry)
    return TestClient(build_v1_app(registry))


def test_batch_recommend_sets_model_version_on_partial_failure():
    """When a batch has one ok and one error element, the 200 response must
    carry X-Recotem-Model-Version and the body model_version must match."""
    rec = MagicMock()

    def _side(user_id, limit):
        if user_id == "known-user":
            return [("i1", 0.9)]
        raise KeyError(user_id)

    rec.get_recommendation_for_known_user_id.side_effect = _side
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={
            "requests": [
                {"user_id": "known-user"},
                {"user_id": "unknown-user"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # One ok, one error
    statuses = [e["status"] for e in body["results"]]
    assert "ok" in statuses
    assert "error" in statuses
    # Header must be set
    header_val = r.headers.get("x-recotem-model-version")
    assert header_val, (
        "X-Recotem-Model-Version must be present on partial-failure batch"
    )
    assert header_val == body["model_version"], (
        "Header value must match body model_version"
    )


# ---------------------------------------------------------------------------
# Finding 9: include_metadata opt-in on batch-recommend
# ---------------------------------------------------------------------------


def test_batch_recommend_include_metadata_false_no_extra_fields():
    """Default include_metadata=False: items carry only item_id and score."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    meta_index = {"i1": {"title": "Widget A", "category": "tools"}}
    r = _client_with_metadata(rec, meta_index).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1"}]},
        # include_metadata defaults to False — no key in JSON
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["status"] == "ok"
    items = results[0]["items"]
    assert len(items) == 1
    item = items[0]
    assert set(item.keys()) == {"item_id", "score"}, (
        f"With include_metadata=False, items must have only item_id+score; got {set(item.keys())!r}"
    )


def test_batch_recommend_include_metadata_false_explicit():
    """Explicit include_metadata=False must also omit metadata fields."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    meta_index = {"i1": {"title": "Widget A"}}
    r = _client_with_metadata(rec, meta_index).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1"}], "include_metadata": False},
    )
    assert r.status_code == 200, r.text
    item = r.json()["results"][0]["items"][0]
    assert "title" not in item, (
        "include_metadata=False must not include metadata fields"
    )


def test_batch_recommend_include_metadata_true_adds_fields():
    """include_metadata=True: items carry the same metadata as single :recommend."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    meta_index = {"i1": {"title": "Widget A", "category": "tools"}}
    r = _client_with_metadata(rec, meta_index).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1"}], "include_metadata": True},
    )
    assert r.status_code == 200, r.text
    item = r.json()["results"][0]["items"][0]
    assert item["item_id"] == "i1"
    assert item["score"] == 0.9
    assert item.get("title") == "Widget A", (
        "include_metadata=True must include metadata fields in batch items"
    )
    assert item.get("category") == "tools"


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
    # The message must mention the violating field name so callers can diagnose
    # which sub-field failed without re-parsing the full schema error.
    assert "limit" in rs[1]["error"]["message"], (
        f"VALIDATION_ERROR message should mention 'limit'; got {rs[1]['error']['message']!r}"
    )

    r_over = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1", "limit": 1001}]},  # above ceiling
    )
    assert r_over.status_code == 200, r_over.text
    assert r_over.json()["results"][0]["status"] == "error"
    assert r_over.json()["results"][0]["error"]["code"] == "VALIDATION_ERROR"
    assert "limit" in r_over.json()["results"][0]["error"]["message"], (
        "VALIDATION_ERROR message should mention 'limit'"
    )
