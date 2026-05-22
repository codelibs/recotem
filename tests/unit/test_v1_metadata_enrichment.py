# tests/unit/test_v1_metadata_enrichment.py
"""Verify that item metadata is included in :recommend responses.

Production serving builds ``entry.metadata_index`` at artifact load time
(see ``app.py:_try_load_artifact`` and ``watcher.py:_build_entry``) and
the router reads from it via ``meta_index.get(item_id, {})``.  The
deny-set is already applied by ``build_metadata_index`` at load time, so
the router does NOT re-apply it at serve time.  Deny-set semantics are
covered separately in ``tests/unit/test_metadata_loader.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(
    entry: ModelEntry,
) -> TestClient:
    registry = ModelRegistry()
    registry.replace(entry.name, entry)
    return TestClient(
        build_v1_app(registry),
    )


_FAKE_SHA256_HEX = "3" * 64  # 64 lowercase hex chars for a valid Sha256Hex marker


def _entry_with_metadata_index(
    metadata_index: dict[str, dict],
    recommender: MagicMock,
) -> ModelEntry:
    """Return a loaded entry that uses the fast dict-based metadata path."""
    return ModelEntry(
        name="demo",
        recommender=recommender,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=metadata_index,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )


def _entry_with_loaded_metadata(
    df: pd.DataFrame,
    recommender: MagicMock,
    metadata_field_deny: list[str] | None = None,
) -> ModelEntry:
    """Return a loaded entry whose ``metadata_index`` is built from *df*.

    Mirrors production behaviour where the index is built at load time by
    ``build_metadata_index`` (deny-set applied), so tests that set up an
    entry-with-metadata path go through the same code as serving does.
    """
    from recotem.metadata.loader import build_metadata_index

    deny_set: frozenset[str] = frozenset(s.lower() for s in (metadata_field_deny or []))
    index = build_metadata_index(df, deny_set=deny_set)
    return ModelEntry(
        name="demo",
        recommender=recommender,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=index,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )


# ---------------------------------------------------------------------------
# Task D.1 — metadata_index path: fields appear in response items
# ---------------------------------------------------------------------------


def test_recommend_includes_metadata_fields_from_index() -> None:
    """Items returned by :recommend carry metadata from metadata_index."""
    meta_index = {
        "i1": {"title": "Widget A", "category": "tools"},
        "i2": {"title": "Widget B", "category": "home"},
    }
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9), ("i2", 0.5)]

    client = _make_client(_entry_with_metadata_index(meta_index, rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 2})

    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 2

    item1 = next(x for x in items if x["item_id"] == "i1")
    assert item1["title"] == "Widget A"
    assert item1["category"] == "tools"
    assert item1["score"] == 0.9

    item2 = next(x for x in items if x["item_id"] == "i2")
    assert item2["title"] == "Widget B"


def test_recommend_item_without_metadata_entry_has_no_extra_fields() -> None:
    """Items with no matching metadata_index entry carry only item_id and score."""
    meta_index: dict[str, dict] = {}  # empty — no metadata for any item
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i_unknown", 0.3)]

    client = _make_client(_entry_with_metadata_index(meta_index, rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})

    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["item_id"] == "i_unknown"
    assert item["score"] == 0.3
    # Only the two mandatory fields should be present
    assert set(item.keys()) == {"item_id", "score"}


# ---------------------------------------------------------------------------
# Task D.2 — load-time deny-set: denied fields are stripped at serve time
# ---------------------------------------------------------------------------
# The deny-set is applied by ``build_metadata_index`` at artifact-load
# time; the router only reads the pre-flattened index.  These tests
# pre-build an index with the deny-set applied to mirror the production
# load path.  Lower-level deny-set semantics (case-insensitivity, NaN
# handling) are covered in ``tests/unit/test_metadata_loader.py``.


def test_recommend_strips_denied_fields_pre_built_into_index() -> None:
    df = pd.DataFrame(
        {
            "title": ["Widget A", "Widget B"],
            "internal_score": [99.0, 88.0],
            "category": ["tools", "home"],
        },
        index=pd.Index(["i1", "i2"], name="item_id"),
    )
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9), ("i2", 0.5)]

    client = _make_client(
        _entry_with_loaded_metadata(df, rec, metadata_field_deny=["internal_score"]),
    )
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 2})

    assert r.status_code == 200, r.text
    for item in r.json()["items"]:
        assert "internal_score" not in item
        assert "title" in item
        assert "category" in item


def test_recommend_deny_is_case_insensitive_pre_built() -> None:
    df = pd.DataFrame(
        {"Secret": ["s1", "s2"], "name": ["n1", "n2"]},
        index=pd.Index(["i1", "i2"], name="item_id"),
    )
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]

    client = _make_client(
        _entry_with_loaded_metadata(df, rec, metadata_field_deny=["SECRET"]),
    )
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})

    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert "Secret" not in item
    assert "name" in item


# ---------------------------------------------------------------------------
# H. score/item_id precedence, extra fields
# ---------------------------------------------------------------------------


def test_recommender_score_wins_over_metadata_score() -> None:
    meta_index = {
        "i1": {"item_id": "WRONG", "score": 999.0, "title": "Widget A"},
    }
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    entry = _entry_with_metadata_index(meta_index, rec)
    client = _make_client(entry)
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["item_id"] == "i1"
    assert item["score"] == 0.9


def test_response_preserves_extra_metadata_through_pydantic_roundtrip() -> None:
    meta_index = {
        "i1": {"foo bar": "extra-value", "title": "Widget"},
    }
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    entry = _entry_with_metadata_index(meta_index, rec)
    client = _make_client(entry)
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item.get("foo bar") == "extra-value"


# ---------------------------------------------------------------------------
# T5: :recommend-related enriches with metadata + respects RECOTEM_METADATA_FIELD_DENY
# ---------------------------------------------------------------------------


def _entry_related_with_metadata_index(
    metadata_index: dict[str, dict],
    recommender: MagicMock,
) -> ModelEntry:
    """Return a loaded entry suitable for :recommend-related with a metadata_index."""
    # :recommend-related pre-checks _mapper.item_id_to_index for known seeds.
    recommender._mapper.item_id_to_index = {"seed1": 0, "seed2": 1}
    return ModelEntry(
        name="demo",
        recommender=recommender,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=metadata_index,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )


def _entry_related_with_loaded_metadata(
    df: pd.DataFrame,
    recommender: MagicMock,
    metadata_field_deny: list[str] | None = None,
) -> ModelEntry:
    """Return a loaded entry whose metadata_index is built from *df* (deny applied)."""
    from recotem.metadata.loader import build_metadata_index

    recommender._mapper.item_id_to_index = {"seed1": 0}
    deny_set: frozenset[str] = frozenset(s.lower() for s in (metadata_field_deny or []))
    index = build_metadata_index(df, deny_set=deny_set)
    return ModelEntry(
        name="demo",
        recommender=recommender,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=index,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )


def test_recommend_related_includes_metadata_fields() -> None:
    """Items returned by :recommend-related carry metadata from metadata_index."""
    meta_index = {
        "i1": {"title": "Widget A", "category": "tools"},
        "i2": {"title": "Widget B", "category": "home"},
    }
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i1", 0.9), ("i2", 0.5)]

    entry = _entry_related_with_metadata_index(meta_index, rec)
    client = _make_client(entry)

    r = client.post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": ["seed1"], "limit": 2},
    )

    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 2

    item1 = next(x for x in items if x["item_id"] == "i1")
    assert item1["title"] == "Widget A"
    assert item1["category"] == "tools"
    assert item1["score"] == 0.9

    item2 = next(x for x in items if x["item_id"] == "i2")
    assert item2["title"] == "Widget B"


def test_recommend_related_strips_denied_fields() -> None:
    """Denied fields (applied at load time) must not appear in :recommend-related items."""
    df = pd.DataFrame(
        {
            "title": ["Widget A", "Widget B"],
            "internal_score": [99.0, 88.0],
            "category": ["tools", "home"],
        },
        index=pd.Index(["i1", "i2"], name="item_id"),
    )
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i1", 0.9), ("i2", 0.5)]

    entry = _entry_related_with_loaded_metadata(
        df, rec, metadata_field_deny=["internal_score"]
    )
    client = _make_client(entry)

    r = client.post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": ["seed1"], "limit": 2},
    )

    assert r.status_code == 200, r.text
    for item in r.json()["items"]:
        assert "internal_score" not in item, (
            "Denied field 'internal_score' must not appear in :recommend-related items"
        )
        assert "title" in item
        assert "category" in item


# ---------------------------------------------------------------------------
# C1: _build_items fallback / drop / X-Recotem-Items-Degraded header
# ---------------------------------------------------------------------------


def test_build_items_fallback_path_via_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario A (fallback): first model_validate (full fields) raises
    ValidationError; second call (bare item_id/score) succeeds.

    Asserts:
    - Response is 200.
    - X-Recotem-Items-Degraded header equals the fallback count.
    - recotem_v1_metadata_degraded_items_total{kind="fallback"} increments.
    - metadata_serialization_failed log event is captured.
    - recotem_metadata_serialization_errors_total{recipe,verb} increments.
    """
    import structlog.testing
    from pydantic import ValidationError

    import recotem.serving.metrics as _metrics_mod
    from recotem.serving.schemas import RecommendItem

    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [
        ("i1", 0.9),
        ("i2", 0.5),
    ]
    meta_index = {"i1": {"title": "Widget A"}, "i2": {"title": "Widget B"}}
    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=meta_index,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )
    client = _make_client(entry)

    original_validate = RecommendItem.model_validate
    call_state: dict[str, int] = {}

    def _failing_first_with_title(data, *args, **kwargs):
        key = (data.get("item_id"), "has_title" if "title" in data else "bare")
        if key[0] == "i1" and key[1] == "has_title":
            call_state.setdefault("fail_count", 0)
            call_state["fail_count"] += 1
            raise original_validate(
                {"item_id": "i1", "score": float("inf")}  # triggers allow_inf_nan=False
            ).__class__  # won't reach here — need real ValidationError below

    del _failing_first_with_title

    first_call: dict[str, bool] = {}

    def _patched_validate(data, *args, **kwargs):
        if isinstance(data, dict) and data.get("item_id") == "i1" and "title" in data:
            first_call["seen"] = True
            try:
                return original_validate({"item_id": "i1", "score": float("inf")})
            except ValidationError as exc:
                raise exc
        return original_validate(data, *args, **kwargs)

    monkeypatch.setattr(
        RecommendItem, "model_validate", staticmethod(_patched_validate)
    )

    degraded_calls: list[tuple[str, str, str, int]] = []
    real_inc = _metrics_mod.inc_metadata_degraded_items

    def _spy_degraded(recipe, verb, kind, count=1):
        degraded_calls.append((recipe, verb, kind, count))

    monkeypatch.setattr(_metrics_mod, "inc_metadata_degraded_items", _spy_degraded)

    serialization_calls: list[tuple[str, str]] = []
    real_serialization = _metrics_mod.inc_metadata_serialization_error

    def _spy_serialization(recipe, verb):
        serialization_calls.append((recipe, verb))

    monkeypatch.setattr(
        _metrics_mod, "inc_metadata_serialization_error", _spy_serialization
    )

    with structlog.testing.capture_logs() as cap:
        r = client.post(
            "/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 2}
        )

    assert r.status_code == 200, r.text
    degraded_val = r.headers.get("x-recotem-items-degraded")
    assert degraded_val is not None, (
        "X-Recotem-Items-Degraded must be set when fallback occurs"
    )
    assert int(degraded_val) >= 1, (
        f"X-Recotem-Items-Degraded must be >= 1; got {degraded_val!r}"
    )

    fallback_events = [e for e in degraded_calls if e[2] == "fallback"]
    assert fallback_events, (
        f"inc_metadata_degraded_items must be called with kind='fallback'; got {degraded_calls!r}"
    )

    log_events = [e for e in cap if e.get("event") == "metadata_serialization_failed"]
    assert log_events, (
        f"metadata_serialization_failed log event must be emitted; got {[e.get('event') for e in cap]!r}"
    )

    assert serialization_calls, (
        f"inc_metadata_serialization_error must be called; got {serialization_calls!r}"
    )


def test_build_items_dropped_path_via_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario B (dropped): both model_validate calls (full and bare) raise
    ValidationError for a specific item.

    Asserts:
    - Response is 200 (other items served).
    - X-Recotem-Items-Degraded header equals the dropped count.
    - recotem_v1_metadata_degraded_items_total{kind="dropped"} increments.
    - metadata_serialization_failed log event is captured.
    - recotem_metadata_serialization_errors_total{recipe,verb} increments.
    """
    import structlog.testing
    from pydantic import ValidationError

    import recotem.serving.metrics as _metrics_mod
    from recotem.serving.schemas import RecommendItem

    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [
        ("bad-item", 0.9),
        ("i2", 0.5),
    ]
    meta_index: dict[str, dict] = {}
    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=meta_index,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )
    client = _make_client(entry)

    original_validate = RecommendItem.model_validate

    def _patched_validate(data, *args, **kwargs):
        if isinstance(data, dict) and data.get("item_id") == "bad-item":
            try:
                return original_validate({"item_id": "bad-item", "score": float("inf")})
            except ValidationError as exc:
                raise exc
        return original_validate(data, *args, **kwargs)

    monkeypatch.setattr(
        RecommendItem, "model_validate", staticmethod(_patched_validate)
    )

    degraded_calls: list[tuple[str, str, str, int]] = []

    def _spy_degraded(recipe, verb, kind, count=1):
        degraded_calls.append((recipe, verb, kind, count))

    monkeypatch.setattr(_metrics_mod, "inc_metadata_degraded_items", _spy_degraded)

    serialization_calls: list[tuple[str, str]] = []

    def _spy_serialization(recipe, verb):
        serialization_calls.append((recipe, verb))

    monkeypatch.setattr(
        _metrics_mod, "inc_metadata_serialization_error", _spy_serialization
    )

    with structlog.testing.capture_logs() as cap:
        r = client.post(
            "/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 2}
        )

    assert r.status_code == 200, r.text
    items = r.json()["items"]
    item_ids = [it["item_id"] for it in items]
    assert "bad-item" not in item_ids, "Dropped item must not appear in response"
    assert "i2" in item_ids, "Valid item must still be served"

    degraded_val = r.headers.get("x-recotem-items-degraded")
    assert degraded_val is not None, (
        "X-Recotem-Items-Degraded must be set when drop occurs"
    )
    assert int(degraded_val) >= 1

    dropped_events = [e for e in degraded_calls if e[2] == "dropped"]
    assert dropped_events, (
        f"inc_metadata_degraded_items must be called with kind='dropped'; got {degraded_calls!r}"
    )

    log_events = [e for e in cap if e.get("event") == "metadata_serialization_failed"]
    assert log_events, (
        f"metadata_serialization_failed log event must be emitted; got {[e.get('event') for e in cap]!r}"
    )

    assert serialization_calls, (
        f"inc_metadata_serialization_error must be called; got {serialization_calls!r}"
    )


def test_build_items_fallback_and_degraded_header() -> None:
    """_build_items with no degradation must not set X-Recotem-Items-Degraded.

    This confirms the "all items OK" baseline is clean before the degradation
    scenarios in the monkeypatch tests above.
    """
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [
        ("i1", 0.9),
        ("i2", 0.5),
    ]
    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )
    client = _make_client(entry)
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 2})

    assert r.status_code == 200, r.text
    assert "x-recotem-items-degraded" not in r.headers, (
        "No degradation must not set X-Recotem-Items-Degraded"
    )
    assert len(r.json()["items"]) == 2


def test_build_items_no_degraded_header_when_all_items_ok() -> None:
    """When all items serialize cleanly, X-Recotem-Items-Degraded must not
    be set on the response."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    meta_index = {"i1": {"title": "OK Item"}}
    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=meta_index,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )
    client = _make_client(entry)
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})

    assert r.status_code == 200, r.text
    assert "x-recotem-items-degraded" not in r.headers, (
        "X-Recotem-Items-Degraded must NOT be present when all items are OK"
    )
