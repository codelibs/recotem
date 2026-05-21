# tests/unit/test_v1_metadata_enrichment.py
"""Verify that item metadata is included in :recommend responses.

Two enrichment paths exist in routes.py:

1. ``metadata_index`` path — when ``entry.metadata_index`` is not None the
   router calls ``meta_index.get(item_id, {})`` directly.  The deny-set has
   already been applied by ``build_metadata_index`` at load time, so it is
   NOT re-applied at serve time.

2. ``metadata_df`` path — when only ``entry.metadata_df`` is set the router
   delegates to ``_lookup_metadata(meta_df, item_id, _deny_set, name)``,
   which applies the deny-set at query time.

These tests cover:
- metadata fields appear in response items when a populated ``metadata_index``
  is provided.
- denied fields are stripped when using the ``metadata_df`` path (where the
  ``metadata_field_deny`` parameter to ``make_router`` is active).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.routes import make_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(
    entry: ModelEntry,
    metadata_field_deny: list[str] | None = None,
) -> TestClient:
    registry = ModelRegistry()
    registry.replace(entry.name, entry)
    app = FastAPI()
    app.include_router(
        make_router(
            registry=registry,
            api_keys=[],
            metadata_field_deny=metadata_field_deny,
        ),
        prefix="/v1",
    )
    return TestClient(app)


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


def _entry_with_metadata_df(
    meta_df: pd.DataFrame,
    recommender: MagicMock,
) -> ModelEntry:
    """Return a loaded entry that uses the DataFrame metadata path."""
    return ModelEntry(
        name="demo",
        recommender=recommender,
        header={},
        kid="test",
        metadata_df=meta_df,
        metadata_index=None,
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
# Task D.2 — metadata_df path: denied fields are stripped at serve time
# ---------------------------------------------------------------------------


def test_recommend_strips_denied_fields_from_metadata_df() -> None:
    """metadata_field_deny causes the matching columns to be absent from items."""
    # Build a minimal pandas DataFrame indexed by item_id (string index).
    df = pd.DataFrame(
        {
            "title": ["Widget A", "Widget B"],
            "internal_score": [99.0, 88.0],  # this field will be denied
            "category": ["tools", "home"],
        },
        index=pd.Index(["i1", "i2"], name="item_id"),
    )
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9), ("i2", 0.5)]

    # Deny "internal_score" via the make_router parameter.
    client = _make_client(
        _entry_with_metadata_df(df, rec),
        metadata_field_deny=["internal_score"],
    )
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 2})

    assert r.status_code == 200, r.text
    items = r.json()["items"]

    for item in items:
        assert "internal_score" not in item, (
            "Denied field 'internal_score' must not appear in response"
        )
        # Non-denied fields must still be present
        assert "title" in item
        assert "category" in item


def test_recommend_deny_is_case_insensitive_via_metadata_df() -> None:
    """The deny-set comparison is case-insensitive (stored as lowercase)."""
    df = pd.DataFrame(
        {"Secret": ["s1", "s2"], "name": ["n1", "n2"]},
        index=pd.Index(["i1", "i2"], name="item_id"),
    )
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]

    # Pass the deny entry in a different case than the column name.
    client = _make_client(
        _entry_with_metadata_df(df, rec),
        metadata_field_deny=["SECRET"],  # uppercase — should still match "Secret"
    )
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})

    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert "Secret" not in item
    assert "name" in item
