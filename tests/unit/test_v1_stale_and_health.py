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

_FAKE_SHA256_HEX = "2" * 64  # 64 lowercase hex chars for a valid Sha256Hex marker


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
        _loaded_marker=(None, _FAKE_SHA256_HEX),
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

    `/v1/health` answers "is every registered recipe present?", and with
    nothing registered the answer is vacuously yes.  This is deliberately NOT
    the readiness answer: whether the Service should route to this replica is
    a different question, and for an empty registry it is no — see
    `test_ready_returns_503_when_registry_empty`.  No probe in the shipped
    chart reads `/v1/health`; it is for alerting.
    """
    registry = ModelRegistry()
    client = TestClient(build_v1_app(registry))

    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# An EMPTY recipes directory must not be Ready
#
# `/v1/health` above answers "is every registered recipe present?" and has
# nothing to be degraded by when nothing is registered.  `/v1/health/ready`
# answers a different question -- "should the Service send traffic here?" --
# and for an empty registry the answer is no: the replica can serve nothing,
# and every request to it returns 404 RECIPE_NOT_FOUND.
#
# Measured on a 3-node cluster with the shipped chart: `recipes.source:
# objectStore` whose sync container exited 0 having copied nothing left both
# replicas 1/1 Ready, in the Service's endpoints, restartCount 0, no event and
# no WARN in the log -- while 100% of traffic 404'd.  A ConfigMap whose keys
# are not `*.yaml` and an empty PVC produce the same fleet.  The train CronJob
# guards the identical case explicitly ("no recipe files found under
# /recipes", exit 1); serve did not.
# ---------------------------------------------------------------------------


def test_ready_returns_503_when_registry_empty() -> None:
    """An empty registry is a cold fleet: 503, not 200.

    Dies if `health_ready` regains its `total == 0 or ...` short-circuit.
    """
    registry = ModelRegistry()
    client = TestClient(build_v1_app(registry))

    r = client.get("/v1/health/ready")
    assert r.status_code == 503, (
        "a replica with zero recipes can serve nothing and must stay out of "
        f"the Service; got {r.status_code} {r.text}"
    )
    body = r.json()
    assert body["status"] == "unready"
    assert body["total"] == 0
    assert body["loaded"] == 0


def test_ready_returns_503_when_every_recipe_file_was_unparseable() -> None:
    """Skipped files are excluded from `total`, so this is the empty case too.

    A recipes directory in which every YAML fails to parse leaves `total == 0`
    with `skipped > 0`.  That replica has no model either, so it must not be
    Ready — and under the old `total == 0` short-circuit it was.
    """
    registry = ModelRegistry()
    broken = _stub_entry("broken")
    broken.skipped = True
    registry.replace("broken", broken)
    assert registry.health_counts() == (0, 0, 1)
    client = TestClient(build_v1_app(registry))

    r = client.get("/v1/health/ready")
    assert r.status_code == 503, f"got {r.status_code} {r.text}"
    body = r.json()
    assert body["status"] == "unready"
    assert body["skipped"] == 1
