"""Verify ``recotem_v1_requests_total`` labels are set by the route handler.

The metric ``status`` label values documented in ``docs/operations.md``
must be reachable from the HTTP handler — otherwise alert rules filtering
on ``status="unavailable"`` / ``status="unknown_user"`` /
``status="recipe_not_found"`` silently never fire.  This file exercises
each branch via the HTTP layer (not by calling ``record_v1_request``
directly) so a regression that mis-labels the metric is caught.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from recotem.serving import metrics as _metrics
from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app


@pytest.fixture(autouse=True)
def _enable_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force metrics to be enabled and wipe v1 metric state between tests.

    The Prometheus default registry is process-global and `_ensure_v1_initialized`
    is idempotent on the first non-None counter — so we must (a) unregister
    pre-existing collectors before each test and (b) reset the v1 module-level
    counters to None so the next ``record_v1_request`` re-creates them on the
    cleaned registry.
    """
    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "1")
    monkeypatch.setattr(_metrics, "metrics_enabled", lambda: True)

    import prometheus_client  # noqa: PLC0415

    # Unregister any collectors carrying our v1 metric names so the next
    # ``_ensure_v1_initialized`` succeeds with fresh Counters.
    _v1_names = {
        "recotem_v1_requests",
        "recotem_v1_request_latency_seconds",
        "recotem_v1_batch_size",
        "recotem_v1_batch_element_errors",
    }
    for collector in list(prometheus_client.REGISTRY._collector_to_names):
        names = prometheus_client.REGISTRY._collector_to_names.get(collector, set())
        if any(n.startswith(tuple(_v1_names)) for n in names):
            try:
                prometheus_client.REGISTRY.unregister(collector)
            except Exception:  # noqa: BLE001
                pass

    for attr in (
        "_V1_REQUEST_COUNTER",
        "_V1_REQUEST_LATENCY",
        "_V1_BATCH_SIZE",
        "_V1_BATCH_ELEMENT_ERRORS",
    ):
        monkeypatch.setattr(_metrics, attr, None, raising=False)

    yield

    # Best-effort cleanup so adjacent test files do not see our counters.
    for collector in list(prometheus_client.REGISTRY._collector_to_names):
        names = prometheus_client.REGISTRY._collector_to_names.get(collector, set())
        if any(n.startswith(tuple(_v1_names)) for n in names):
            try:
                prometheus_client.REGISTRY.unregister(collector)
            except Exception:  # noqa: BLE001
                pass


_FAKE_SHA256_HEX = "e" * 64  # 64 lowercase hex chars for a valid Sha256Hex marker


def _loaded_entry(name: str = "demo") -> ModelEntry:
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    rec.get_recommendation_for_new_user.return_value = [("i2", 0.8)]
    rec._mapper = MagicMock()
    rec._mapper.item_id_to_index = {"i1": 0, "i2": 1, "seed-known": 2}
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


def _stub_entry(name: str = "stub") -> ModelEntry:
    return ModelEntry(
        name=name,
        recommender=None,
        header={},
        kid="",
        metadata_df=None,
        last_load_error="not loaded",
        artifact_path="",
        loaded=False,
    )


def _label_value(verb: str, status: str, recipe: str = "demo") -> float:
    counter = _metrics._V1_REQUEST_COUNTER
    assert counter is not None, "v1 request counter must be initialised"
    return counter.labels(recipe=recipe, verb=verb, status=status)._value.get()


def test_recommend_records_ok_status() -> None:
    registry = ModelRegistry()
    registry.replace("demo", _loaded_entry())
    client = TestClient(build_v1_app(registry))

    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 200
    assert _label_value("recommend", "ok") == 1.0


def test_recommend_records_unknown_user_status() -> None:
    entry = _loaded_entry()
    entry.recommender.get_recommendation_for_known_user_id.side_effect = KeyError("u1")
    registry = ModelRegistry()
    registry.replace("demo", entry)
    client = TestClient(build_v1_app(registry))

    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 404
    assert r.json()["code"] == "UNKNOWN_USER"
    assert _label_value("recommend", "unknown_user") == 1.0


def test_recommend_records_unavailable_when_stub() -> None:
    registry = ModelRegistry()
    registry.replace("demo", _stub_entry("demo"))
    client = TestClient(build_v1_app(registry))

    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 503
    assert r.json()["code"] == "RECIPE_UNAVAILABLE"
    assert _label_value("recommend", "unavailable") == 1.0


def test_recommend_records_recipe_not_found_when_missing() -> None:
    registry = ModelRegistry()
    client = TestClient(build_v1_app(registry))

    r = client.post("/v1/recipes/ghost:recommend", json={"user_id": "u1"})
    assert r.status_code == 404
    assert r.json()["code"] == "RECIPE_NOT_FOUND"
    assert _label_value("recommend", "recipe_not_found", recipe="ghost") == 1.0


def test_recommend_related_records_unknown_seed_items() -> None:
    entry = _loaded_entry()
    # id_map empty so no seed is known.
    entry.recommender._mapper.item_id_to_index = {}
    registry = ModelRegistry()
    registry.replace("demo", entry)
    client = TestClient(build_v1_app(registry))

    r = client.post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": ["i-unknown"]},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "UNKNOWN_SEED_ITEMS"
    assert _label_value("recommend-related", "unknown_seed_items") == 1.0


def test_recommend_related_records_no_candidates() -> None:
    entry = _loaded_entry()
    # seed-known is in id_map but ranker returns []
    entry.recommender.get_recommendation_for_new_user.return_value = []
    registry = ModelRegistry()
    registry.replace("demo", entry)
    client = TestClient(build_v1_app(registry))

    r = client.post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": ["seed-known"]},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "NO_CANDIDATES"
    assert _label_value("recommend-related", "no_candidates") == 1.0


def test_validation_error_records_metric_for_matching_v1_path() -> None:
    registry = ModelRegistry()
    registry.replace("demo", _loaded_entry())
    client = TestClient(build_v1_app(registry))

    # limit=0 fails the schema; whole-request 422.
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 0})
    assert r.status_code == 422
    assert _label_value("recommend", "validation_error") == 1.0


# ---------------------------------------------------------------------------
# Finding 4: recipe_not_found metric across all verbs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb,path,body",
    [
        ("recommend", "ghost:recommend", {"user_id": "u1"}),
        (
            "recommend-related",
            "ghost:recommend-related",
            {"seed_items": ["i1"]},
        ),
        (
            "batch-recommend",
            "ghost:batch-recommend",
            {"requests": [{"user_id": "u1"}]},
        ),
        (
            "batch-recommend-related",
            "ghost:batch-recommend-related",
            {"requests": [{"seed_items": ["i1"]}]},
        ),
    ],
)
def test_recipe_not_found_metric_across_verbs(verb: str, path: str, body: dict) -> None:
    """404 on missing recipe must record recipe_not_found label for every verb."""
    registry = ModelRegistry()
    client = TestClient(build_v1_app(registry))
    r = client.post(f"/v1/recipes/{path}", json=body)
    assert r.status_code == 404
    assert r.json()["code"] == "RECIPE_NOT_FOUND"
    assert _label_value(verb, "recipe_not_found", recipe="ghost") == 1.0


# ---------------------------------------------------------------------------
# Finding 5: model_version header absent on error responses
# ---------------------------------------------------------------------------


def test_model_version_header_absent_on_404_recipe_not_found() -> None:
    """When :recommend returns 404 RECIPE_NOT_FOUND, X-Recotem-Model-Version
    must NOT be present — there is no loaded model to report."""
    registry = ModelRegistry()
    client = TestClient(build_v1_app(registry))
    r = client.post("/v1/recipes/ghost:recommend", json={"user_id": "u1"})
    assert r.status_code == 404
    assert "x-recotem-model-version" not in r.headers, (
        "404 RECIPE_NOT_FOUND must not carry X-Recotem-Model-Version"
    )


def test_model_version_header_absent_on_503_recipe_unavailable() -> None:
    """503 RECIPE_UNAVAILABLE must not carry X-Recotem-Model-Version."""
    registry = ModelRegistry()
    registry.replace("demo", _stub_entry("demo"))
    client = TestClient(build_v1_app(registry))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 503
    assert "x-recotem-model-version" not in r.headers, (
        "503 RECIPE_UNAVAILABLE must not carry X-Recotem-Model-Version"
    )


def test_model_version_header_present_on_200_recommend() -> None:
    """200 response must carry a non-empty X-Recotem-Model-Version header."""
    registry = ModelRegistry()
    registry.replace("demo", _loaded_entry())
    client = TestClient(build_v1_app(registry))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 200, r.text
    assert r.headers.get("x-recotem-model-version"), (
        "200 :recommend must carry X-Recotem-Model-Version"
    )


def test_batch_recommend_records_outer_ok_when_partial_failure() -> None:
    """A batch with mixed ok/error elements still records the OUTER request
    as ``status=ok`` (HTTP 200) — per-element errors are observable via
    the separate ``_v1_batch_element_errors_total`` counter.
    """
    entry = _loaded_entry()

    def _side(user_id, limit):  # noqa: ARG001
        if user_id == "bad":
            raise KeyError(user_id)
        return [("i1", 0.5)]

    entry.recommender.get_recommendation_for_known_user_id.side_effect = _side
    registry = ModelRegistry()
    registry.replace("demo", entry)
    client = TestClient(build_v1_app(registry))

    r = client.post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1"}, {"user_id": "bad"}]},
    )
    assert r.status_code == 200
    body = r.json()
    statuses = [e["status"] for e in body["results"]]
    assert statuses == ["ok", "error"]
    assert _label_value("batch-recommend", "ok") == 1.0
    counter = _metrics._V1_BATCH_ELEMENT_ERRORS
    assert counter is not None
    assert (
        counter.labels(
            recipe="demo", verb="batch-recommend", code="UNKNOWN_USER"
        )._value.get()
        == 1.0
    )
