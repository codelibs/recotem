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
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(
    entry: ModelEntry,
    metadata_field_deny: list[str] | None = None,
) -> TestClient:
    registry = ModelRegistry()
    registry.replace(entry.name, entry)
    return TestClient(
        build_v1_app(registry, metadata_field_deny=metadata_field_deny),
    )


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
        _loaded_marker=(None, "abc123"),
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
        _loaded_marker=(None, "abc123"),
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
