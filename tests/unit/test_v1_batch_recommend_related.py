# tests/unit/test_v1_batch_recommend_related.py
"""POST /v1/recipes/{name}:batch-recommend-related — multi-seed bulk."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app


def _client(rec, known_items: list[str] | None = None) -> TestClient:
    """Wrap *rec* in a ModelEntry whose id-map advertises *known_items*.

    The router pre-checks ``entry.recommender._mapper.item_id_to_index``
    to distinguish ``UNKNOWN_SEED_ITEMS`` from ``NO_CANDIDATES``; tests
    that exercise the happy path need at least one seed in the map.
    """
    rec._mapper.item_id_to_index = {iid: i for i, iid in enumerate(known_items or [])}
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
    """Mix known + unknown seeds: known seeds → ok, fully-unknown → UNKNOWN_SEED_ITEMS."""
    rec = MagicMock()

    def _side_effect(seed_items, limit):
        return [("i9", 0.7)] if "7203" in seed_items or "9984" in seed_items else []

    rec.get_recommendation_for_new_user.side_effect = _side_effect
    r = _client(rec, known_items=["7203", "9984"]).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={
            "requests": [
                {"seed_items": ["7203"]},
                {"seed_items": ["zzz"]},  # unknown — UNKNOWN_SEED_ITEMS
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


def test_batch_related_empty_seed_in_one_entry_is_per_element_error():
    """Empty seed list fails the sub-schema; under per-element validation
    this surfaces as ``status=error, code=VALIDATION_ERROR`` rather than
    a whole-batch 422 (was 422 in the all-or-nothing mode)."""
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = []
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={
            "requests": [{"seed_items": []}],
        },
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["status"] == "error"
    assert results[0]["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# G. Partial failure parity (I1)
# ---------------------------------------------------------------------------


def test_batch_related_element_unknown_seeds_yields_error() -> None:
    """A seed_items list with no known id-map members → UNKNOWN_SEED_ITEMS."""
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i1", 0.9)]

    r = _client(rec, known_items=["good-seed", "good-seed2"]).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={
            "requests": [
                {"seed_items": ["good-seed"]},
                {"seed_items": ["unknown-seed"]},
                {"seed_items": ["good-seed2"]},
            ]
        },
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "error"
    assert results[1]["error"]["code"] == "UNKNOWN_SEED_ITEMS"
    assert results[2]["status"] == "ok"


def test_batch_related_element_runtime_error_yields_internal_error() -> None:
    rec = MagicMock()

    def _side_effect(seed_items, limit):
        if seed_items == ["bad-seed"]:
            raise RuntimeError("exploded")
        return [("i1", 0.9)]

    rec.get_recommendation_for_new_user.side_effect = _side_effect
    r = _client(rec, known_items=["ok-seed", "bad-seed"]).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={
            "requests": [
                {"seed_items": ["ok-seed"]},
                {"seed_items": ["bad-seed"]},
            ]
        },
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "error"
    assert results[1]["error"]["code"] == "INTERNAL_ERROR"


def test_batch_related_aggregate_limit_cap_exceeded() -> None:
    """Aggregate cap is enforced per-element; element 10 (running sum 5010 > 5000)
    surfaces as VALIDATION_ERROR while earlier elements succeed."""
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i1", 0.9)]
    r = _client(rec, known_items=["s1"]).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={"requests": [{"seed_items": ["s1"], "limit": 501} for _ in range(10)]},
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["status"] == "ok"
    assert results[-1]["status"] == "error"
    assert results[-1]["error"]["code"] == "VALIDATION_ERROR"


def test_batch_recommend_related_sets_model_version_response_header():
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i9", 0.7)]
    r = _client(rec, known_items=["seed1"]).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={"requests": [{"seed_items": ["seed1"]}]},
    )
    assert r.status_code == 200, r.text
    header_val = r.headers.get("x-recotem-model-version")
    assert header_val, "X-Recotem-Model-Version header must be present and non-empty"
    assert header_val == r.json()["model_version"]
