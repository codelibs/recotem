"""Availability-contract tests for the v1 surface.

Two invariants that must not regress between releases:

1. **Stale-but-loaded keeps serving.**  An entry where the most recent
   hot-swap failed (``last_load_error`` is set) but the previous model is
   still in memory (``loaded=True``, ``recommender`` non-None) must keep
   answering 200.  Treating ``last_load_error`` as a 503 trigger would
   silently take healthy traffic offline on a single bad artifact.

2. **``/v1/health`` returns 503 when degraded.**  K8s readiness probes
   point at this endpoint.  The body status mirrors HTTP status — 503 if
   any registered recipe is unloaded.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app


def _loaded_entry(name: str = "demo") -> ModelEntry:
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    rec._mapper = MagicMock()
    rec._mapper.item_id_to_index = {"i1": 0}
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


def _stub_entry(name: str) -> ModelEntry:
    return ModelEntry(
        name=name,
        recommender=None,
        header={},
        kid="",
        metadata_df=None,
        last_load_error="initial load failed",
        artifact_path="",
        loaded=False,
    )


# ---------------------------------------------------------------------------
# M-6: stale-but-loaded keeps serving
# ---------------------------------------------------------------------------


def test_stale_but_loaded_recipe_keeps_serving_recommend() -> None:
    """``last_load_error`` set + ``loaded=True`` → ``:recommend`` returns 200.

    The watcher sets ``last_load_error`` via ``set_load_error()`` after a
    hot-swap fails; this does NOT flip ``loaded`` to False.  The 200 path
    must remain reachable so a single bad artifact does not page oncall.
    """
    entry = _loaded_entry()
    entry.last_load_error = "hot-swap failed: HMAC verify failed"
    registry = ModelRegistry()
    registry.replace("demo", entry)
    client = TestClient(build_v1_app(registry))

    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["item_id"] == "i1"


def test_stale_but_loaded_recipe_counts_as_loaded_in_health() -> None:
    """A stale-but-loaded entry must count toward the /v1/health loaded total."""
    entry = _loaded_entry()
    entry.last_load_error = "transient stat failure"
    registry = ModelRegistry()
    registry.replace("demo", entry)
    client = TestClient(build_v1_app(registry))

    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["loaded"] == 1
    assert body["status"] == "ok"


def test_stale_but_loaded_recipe_shown_in_health_details() -> None:
    """``last_load_error`` must surface in the per-recipe health detail.

    Even though ``/v1/health`` aggregate stays "ok", operators must be able
    to see the underlying error string in ``/v1/health/details`` for
    debugging.
    """
    # No api_keys → health/details is reachable without an X-API-Key header.
    entry = _loaded_entry()
    entry.last_load_error = "transient stat failure"
    registry = ModelRegistry()
    registry.replace("demo", entry)
    client = TestClient(build_v1_app(registry))

    r = client.get("/v1/health/details")
    # /v1/health/details flips to 503 when any error string is set, even
    # if loaded=True — this is the documented behavior so degraded entries
    # are visible without scraping the aggregate.
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["recipes"]["demo"]["loaded"] is True
    assert "transient stat failure" in body["recipes"]["demo"]["error"]


# ---------------------------------------------------------------------------
# M-7: /v1/health returns 503 when degraded
# ---------------------------------------------------------------------------


def test_health_returns_503_when_loaded_lt_total() -> None:
    """K8s readiness contract: any unloaded recipe → HTTP 503 on /v1/health."""
    registry = ModelRegistry()
    registry.replace("demo", _loaded_entry("demo"))
    registry.replace("broken", _stub_entry("broken"))
    client = TestClient(build_v1_app(registry))

    r = client.get("/v1/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["total"] == 2
    assert body["loaded"] == 1


def test_health_returns_200_when_all_loaded() -> None:
    registry = ModelRegistry()
    registry.replace("a", _loaded_entry("a"))
    registry.replace("b", _loaded_entry("b"))
    client = TestClient(build_v1_app(registry))

    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["total"] == body["loaded"] == 2


def test_health_returns_200_when_registry_empty() -> None:
    """No recipes is "ok" — there is no failure to be degraded by.

    This is the boot-time state before the watcher's first successful
    poll on an empty recipes directory.  K8s should mark the pod Ready so
    traffic can route to it; serving 503 on an empty registry would
    create a deadlock between startup and registration.
    """
    registry = ModelRegistry()
    client = TestClient(build_v1_app(registry))

    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
