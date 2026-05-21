# tests/unit/test_v1_router_basics.py
"""v1 router smoke tests.

Confirms the factory wires auth and registry and that the router is
mounted at ``/v1``.  Inference verbs and the discovery endpoints are
added incrementally across Tasks 6-11.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app


def _client_with_entry(entry: ModelEntry) -> TestClient:
    registry = ModelRegistry()
    registry.replace(entry.name, entry)
    return TestClient(build_v1_app(registry))


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
    client = TestClient(build_v1_app(registry))
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


# ---------------------------------------------------------------------------
# J. Path regex — must accept every valid recipe name
# ---------------------------------------------------------------------------
# The router path regex must mirror ``Recipe.name`` (^[A-Za-z0-9_-]{1,64}$)
# so any recipe accepted at load time is also routable.  Recipes with a
# leading "_" or "-" are valid per the recipe loader, so the router must
# NOT 422 on those — instead they get a normal 404 when the registry is
# empty (the name passes the path regex; the registry has no entry).


def test_recipe_path_accepts_leading_hyphen() -> None:
    client = _client_with_entry(_loaded_entry("-bad"))
    r = client.post("/v1/recipes/-bad:recommend", json={"user_id": "u1"})
    # 422 (regex rejection) would be a regression; 200/404 both indicate
    # the regex accepted the name and the request reached the handler.
    assert r.status_code != 422


def test_recipe_path_accepts_leading_underscore() -> None:
    client = _client_with_entry(_loaded_entry("_bad"))
    r = client.post("/v1/recipes/_bad:recommend", json={"user_id": "u1"})
    assert r.status_code != 422


def test_recipe_path_accepts_alphanumeric_first_char() -> None:
    client = _client_with_entry(_loaded_entry("abc"))
    r = client.post("/v1/recipes/abc:recommend", json={"user_id": "u1"})
    assert r.status_code != 422


# ---------------------------------------------------------------------------
# L. kid contextvar binding on recipe_detail
# ---------------------------------------------------------------------------


def test_recipe_detail_binds_kid_to_logs() -> None:
    import structlog
    import structlog.testing

    captured_kwargs: list[dict] = []

    def _spy(logger, name, event_dict):
        captured_kwargs.append(dict(event_dict))
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _spy,
            structlog.processors.KeyValueRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        cache_logger_on_first_use=False,
    )

    entry = _loaded_entry("kidtest")
    client = _client_with_entry(entry)
    client.get("/v1/recipes/noexist")

    has_kid = any("kid" in e for e in captured_kwargs)
    assert has_kid, (
        "Expected at least one log event with a 'kid' key bound via contextvars "
        f"during /v1/recipes/{{name}}; captured events: {captured_kwargs!r}"
    )
