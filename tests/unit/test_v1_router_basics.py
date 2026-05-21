# tests/unit/test_v1_router_basics.py
"""v1 router smoke tests.

Confirms the factory wires auth and registry and that the router is
mounted at ``/v1``.  Inference verbs and the discovery endpoints are
added incrementally across Tasks 6-11.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.routes import make_router


def _client_with_entry(entry: ModelEntry) -> TestClient:
    registry = ModelRegistry()
    registry.replace(entry.name, entry)
    app = FastAPI()
    app.include_router(make_router(registry=registry, api_keys=[]), prefix="/v1")
    return TestClient(app)


def _loaded_entry(name: str = "demo") -> ModelEntry:
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = []
    return ModelEntry(
        name=name,
        recommender=rec,
        header={},
        kid="t",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc"),
        loaded_at_unix=1747800000.0,
    )


def test_make_router_returns_routable_apiroute_factory():
    registry = ModelRegistry()
    router = make_router(registry=registry, api_keys=[])
    app = FastAPI()
    app.include_router(router, prefix="/v1")

    client = TestClient(app)
    # An entirely unknown verb on the recipes path must return 404,
    # confirming the router rejects undefined verbs (rather than e.g.
    # routing through a catch-all).  A GET request is used so we are not
    # confused with the POST-only colon-verb endpoints.
    r = client.get("/v1/totally-unknown-path")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Recipe name path-constraint tests (^[A-Za-z0-9_-]{1,64}$)
# ---------------------------------------------------------------------------

_INVALID_RECIPE_NAMES = [
    "my recipe",  # space
    "../etc/passwd",  # slashes and dots
    "recipe.yaml",  # dot in name
    "日本語",  # non-ASCII
]


@pytest.mark.parametrize("bad_name", _INVALID_RECIPE_NAMES)
def test_recommend_rejects_invalid_recipe_name(bad_name: str) -> None:
    """POST :recommend with a name that fails the path regex must return 404 or 422."""
    client = _client_with_entry(_loaded_entry())
    r = client.post(
        f"/v1/recipes/{bad_name}:recommend",
        json={"user_id": "u1"},
    )
    # FastAPI returns 422 when the Path regex match fails at the router level;
    # it may return 404 when the URL is parsed differently (e.g. slashes split
    # the path into segments that don't match any route).
    assert r.status_code in {404, 422}, (
        f"Expected 404 or 422 for invalid name {bad_name!r}, got {r.status_code}"
    )


@pytest.mark.parametrize("bad_name", _INVALID_RECIPE_NAMES)
def test_recommend_related_rejects_invalid_recipe_name(bad_name: str) -> None:
    """POST :recommend-related with invalid name must return 404 or 422."""
    client = _client_with_entry(_loaded_entry())
    r = client.post(
        f"/v1/recipes/{bad_name}:recommend-related",
        json={"seed_items": ["i1"]},
    )
    assert r.status_code in {404, 422}, (
        f"Expected 404 or 422 for invalid name {bad_name!r}, got {r.status_code}"
    )


@pytest.mark.parametrize("bad_name", _INVALID_RECIPE_NAMES)
def test_recipe_detail_rejects_invalid_recipe_name(bad_name: str) -> None:
    """GET /v1/recipes/{name} with invalid name must return 404 or 422."""
    client = _client_with_entry(_loaded_entry())
    r = client.get(f"/v1/recipes/{bad_name}")
    assert r.status_code in {404, 422}, (
        f"Expected 404 or 422 for invalid name {bad_name!r}, got {r.status_code}"
    )
