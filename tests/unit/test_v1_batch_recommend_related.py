# tests/unit/test_v1_batch_recommend_related.py
"""POST /v1/recipes/{name}:batch-recommend-related — multi-seed bulk."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app

_FAKE_SHA256_HEX = "4" * 64  # 64 lowercase hex chars for a valid Sha256Hex marker


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
        _loaded_marker=(None, _FAKE_SHA256_HEX),
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
    # The message should mention the violating field so callers can diagnose
    # which sub-field failed without re-parsing the full schema error.
    assert "seed_items" in results[0]["error"]["message"], (
        f"VALIDATION_ERROR message should mention 'seed_items'; "
        f"got {results[0]['error']['message']!r}"
    )


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


# ---------------------------------------------------------------------------
# Finding 1: empty outer requests list → 422
# ---------------------------------------------------------------------------


def test_batch_recommend_related_rejects_empty_outer_requests_list():
    """POST :batch-recommend-related with {"requests": []} must return 422.

    The schema enforces min_length=1 on the outer list; an empty list must
    fail at the schema level (HTTP 422), not reach the handler.
    """
    rec = MagicMock()
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={"requests": []},
    )
    assert r.status_code == 422, (
        f"Empty requests list must produce 422; got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# T1: include_metadata flag for :batch-recommend-related
# ---------------------------------------------------------------------------


def _client_with_metadata(rec, meta_index: dict | None = None) -> TestClient:
    """Build a client whose entry has a metadata_index pre-populated."""
    known_items = list(meta_index.keys()) if meta_index else []
    rec._mapper.item_id_to_index = {iid: i for i, iid in enumerate(known_items)}
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


def test_batch_recommend_related_include_metadata_default_false() -> None:
    """Default include_metadata=False: items in :batch-recommend-related carry
    only item_id and score even when metadata_index is populated."""
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i1", 0.9)]
    meta_index = {"i1": {"title": "Widget A", "category": "tools"}}
    r = _client_with_metadata(rec, meta_index).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={"requests": [{"seed_items": ["i1"]}]},
    )
    assert r.status_code == 200, r.text
    item = r.json()["results"][0]["items"][0]
    assert set(item.keys()) == {"item_id", "score"}, (
        f"With include_metadata=False (default), items must have only item_id+score; "
        f"got {set(item.keys())!r}"
    )


def test_batch_recommend_related_include_metadata_explicit_false() -> None:
    """Explicit include_metadata=False must not include metadata fields."""
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i1", 0.9)]
    meta_index = {"i1": {"title": "Widget A"}}
    r = _client_with_metadata(rec, meta_index).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={"requests": [{"seed_items": ["i1"]}], "include_metadata": False},
    )
    assert r.status_code == 200, r.text
    item = r.json()["results"][0]["items"][0]
    assert "title" not in item, (
        "include_metadata=False must not include metadata fields"
    )


def test_batch_recommend_related_include_metadata_true_adds_fields() -> None:
    """include_metadata=True: items carry the same metadata as single :recommend-related."""
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i1", 0.9)]
    meta_index = {"i1": {"title": "Widget A", "category": "tools"}}
    r = _client_with_metadata(rec, meta_index).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={"requests": [{"seed_items": ["i1"]}], "include_metadata": True},
    )
    assert r.status_code == 200, r.text
    item = r.json()["results"][0]["items"][0]
    assert item["item_id"] == "i1"
    assert item["score"] == 0.9
    assert item.get("title") == "Widget A", (
        "include_metadata=True must include metadata fields"
    )
    assert item.get("category") == "tools"


# ---------------------------------------------------------------------------
# T2: exclude_items in :batch-recommend-related
# ---------------------------------------------------------------------------


def test_batch_recommend_related_exclude_items_removes_item() -> None:
    """When a batch element specifies exclude_items, those items must not
    appear in the result for that element."""
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [
        ("i1", 0.9),
        ("i2", 0.5),
        ("i3", 0.3),
    ]
    r = _client(rec, known_items=["seed1"]).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={
            "requests": [
                {"seed_items": ["seed1"], "exclude_items": ["i2"]},
            ]
        },
    )
    assert r.status_code == 200, r.text
    items = r.json()["results"][0]["items"]
    item_ids = [item["item_id"] for item in items]
    assert "i2" not in item_ids, (
        f"exclude_items=['i2'] must remove i2; got {item_ids!r}"
    )
    assert "i1" in item_ids
    assert "i3" in item_ids


# ---------------------------------------------------------------------------
# F4: X-Recotem-Items-Degraded must NOT be set on batch endpoints
# ---------------------------------------------------------------------------


def test_batch_recommend_related_no_items_degraded_header_even_when_metadata_degrades() -> (
    None
):
    """Even when metadata serialization produces a fallback, :batch-recommend-related
    must NOT set X-Recotem-Items-Degraded.  The header is reserved for single
    endpoints only.

    We use ``include_metadata=True`` with a metadata_index entry whose NaN
    score field triggers the fallback path in _build_items; the batch handler
    does not call _apply_build_items_degraded, so the header is never set.
    """
    import math

    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("seed1", 0.9)]

    meta_index = {"seed1": {"score": math.nan, "title": "Widget"}}
    rec._mapper.item_id_to_index = {"seed1": 0}
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
    client = TestClient(build_v1_app(registry))

    r = client.post(
        "/v1/recipes/demo:batch-recommend-related",
        json={"requests": [{"seed_items": ["seed1"]}], "include_metadata": True},
    )
    assert r.status_code == 200, r.text
    assert "x-recotem-items-degraded" not in r.headers, (
        ":batch-recommend-related must NOT set X-Recotem-Items-Degraded even when "
        "metadata serialization degrades"
    )
