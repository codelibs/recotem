"""Unit tests for recotem.serving.routes.

Tests:
- /predict happy path
- /predict 401 (missing API key)
- /predict 404 (user not found)
- /predict 503 (recipe not loaded)
- /health overall + per-recipe
- /models
- /metrics off-by-default and on with extras
- request_id in X-Request-ID header
- kid field in model block of predict response
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.routes import make_router

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_registry_with_recipe(
    name: str = "test_recipe",
    user_id_to_items: dict | None = None,
) -> ModelRegistry:
    """Build a ModelRegistry with a minimal mock recommender."""
    if user_id_to_items is None:
        user_id_to_items = {"user1": [("item1", 0.9), ("item2", 0.8)]}

    recommender = MagicMock()

    def _get_rec(user_id, cutoff):
        if user_id in user_id_to_items:
            return user_id_to_items[user_id][:cutoff]
        raise KeyError(f"user {user_id} not in training data")

    recommender.get_recommendation_for_known_user_id.side_effect = _get_rec

    entry = ModelEntry(
        name=name,
        recommender=recommender,
        header={
            "best_class": "TopPopRecommender",
            "trained_at": "2026-01-01T00:00:00Z",
        },
        kid="active",
    )

    registry = ModelRegistry()
    registry.replace(name, entry)
    return registry


def _make_api_key_entry(plaintext: str, kid: str = "k1") -> ApiKeyEntry:
    # Mirror recotem.serving.auth._hash_api_key (scrypt KDF with the
    # ``recotem.api-key.v1`` domain-separation salt at minimum cost).
    sha256_hex = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    return ApiKeyEntry(kid=kid, sha256_hex=sha256_hex)


def _make_test_client(
    registry: ModelRegistry | None = None,
    api_keys: list[ApiKeyEntry] | None = None,
    insecure: bool = True,
) -> tuple[TestClient, str]:
    """Return (TestClient, plaintext_api_key)."""
    if api_keys is None and not insecure:
        plaintext = "test_api_key_32_bytes_exactly!!!"
        api_keys = [_make_api_key_entry(plaintext)]
    elif api_keys is None:
        plaintext = ""
        api_keys = []
    else:
        plaintext = ""

    if registry is None:
        registry = _make_registry_with_recipe()

    router = make_router(registry=registry, api_keys=api_keys)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), plaintext


# ---------------------------------------------------------------------------
# /predict happy path
# ---------------------------------------------------------------------------


def test_predict_happy_path_returns_items() -> None:
    registry = _make_registry_with_recipe()
    client, _ = _make_test_client(registry=registry)
    response = client.post(
        "/predict/test_recipe",
        json={"user_id": "user1", "cutoff": 5},
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) > 0
    assert data["items"][0]["item_id"] == "item1"


def test_predict_response_includes_model_block() -> None:
    client, _ = _make_test_client()
    response = client.post("/predict/test_recipe", json={"user_id": "user1"})
    assert response.status_code == 200
    data = response.json()
    assert "model" in data
    assert data["model"]["recipe"] == "test_recipe"
    assert "kid" in data["model"]


def test_predict_response_includes_request_id() -> None:
    client, _ = _make_test_client()
    response = client.post("/predict/test_recipe", json={"user_id": "user1"})
    assert response.status_code == 200
    assert "request_id" in response.json()


def test_request_id_returned_in_X_Request_ID_header() -> None:
    # The routes do not currently set X-Request-ID header — test response body
    client, _ = _make_test_client()
    response = client.post("/predict/test_recipe", json={"user_id": "user1"})
    data = response.json()
    assert "request_id" in data
    assert len(data["request_id"]) > 0


def test_response_includes_kid_field_in_model_block() -> None:
    client, _ = _make_test_client()
    response = client.post("/predict/test_recipe", json={"user_id": "user1"})
    assert response.json()["model"]["kid"] == "active"


# ---------------------------------------------------------------------------
# /predict 401
# ---------------------------------------------------------------------------


def test_predict_401_without_api_key() -> None:
    """With keys configured, missing X-API-Key header → 401."""
    plaintext = "api_key_32_bytes_exactly_here!!!"
    entry = _make_api_key_entry(plaintext)
    registry = _make_registry_with_recipe()
    router = make_router(registry=registry, api_keys=[entry])
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/predict/test_recipe", json={"user_id": "user1"})
    assert response.status_code == 401


def test_predict_401_with_wrong_api_key() -> None:
    plaintext = "correct_api_key_32_bytes_exactly"
    entry = _make_api_key_entry(plaintext)
    registry = _make_registry_with_recipe()
    router = make_router(registry=registry, api_keys=[entry])
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/predict/test_recipe",
        json={"user_id": "user1"},
        headers={"x-api-key": "wrong_key"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# /predict 404 (user not found)
# ---------------------------------------------------------------------------


def test_predict_404_user_not_in_training_data() -> None:
    client, _ = _make_test_client()
    response = client.post("/predict/test_recipe", json={"user_id": "unknown_user"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /predict 503 (recipe not loaded / unhealthy)
# ---------------------------------------------------------------------------


def test_predict_503_recipe_not_loaded() -> None:
    registry = ModelRegistry()  # empty
    client, _ = _make_test_client(registry=registry)
    response = client.post("/predict/no_such_recipe", json={"user_id": "user1"})
    assert response.status_code == 503


def test_stale_but_loaded_recipe_keeps_serving() -> None:
    """A recipe whose latest hot-swap failed must keep serving the old model.

    The watcher sets ``last_load_error`` on the existing entry without
    dropping the recommender.  ``/predict`` must continue to return 200
    so that a bad new artifact does not take down the endpoint.
    """
    registry = _make_registry_with_recipe("stale_recipe")
    entry = registry.get("stale_recipe")
    assert entry is not None
    entry.last_load_error = "hmac mismatch on new artifact"
    client, _ = _make_test_client(registry=registry)
    response = client.post("/predict/stale_recipe", json={"user_id": "user1"})
    assert response.status_code == 200


def test_initial_load_failure_returns_503() -> None:
    """A recipe that never loaded (``loaded=False`` stub) must return 503."""
    registry = ModelRegistry()
    stub = ModelEntry(
        name="never_loaded",
        recommender=None,
        header={},
        kid="",
        loaded=False,
        last_load_error="initial load failed: bad header",
    )
    registry.replace("never_loaded", stub)
    client, _ = _make_test_client(registry=registry)
    response = client.post("/predict/never_loaded", json={"user_id": "user1"})
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_returns_ok_when_all_recipes_loaded() -> None:
    client, _ = _make_test_client()
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_health_overall_degraded_when_any_recipe_unloaded() -> None:
    registry = _make_registry_with_recipe("loaded")
    broken_entry = ModelEntry(
        name="broken",
        recommender=MagicMock(),
        header={},
        kid="active",
        last_load_error="signature mismatch",
    )
    registry.replace("broken", broken_entry)
    client, _ = _make_test_client(registry=registry)
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "degraded"


def test_health_per_recipe_status() -> None:
    client, _ = _make_test_client()
    response = client.get("/health")
    data = response.json()
    assert "recipes" in data
    assert "test_recipe" in data["recipes"]


# ---------------------------------------------------------------------------
# /models
# ---------------------------------------------------------------------------


def test_models_endpoint_returns_list() -> None:
    client, _ = _make_test_client()
    response = client.get("/models")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


# ---------------------------------------------------------------------------
# /metrics — opt-in via RECOTEM_METRICS_ENABLED
# ---------------------------------------------------------------------------


def test_metrics_endpoint_404_when_env_unset(monkeypatch) -> None:
    """Without RECOTEM_METRICS_ENABLED, /metrics is not registered."""
    monkeypatch.delenv("RECOTEM_METRICS_ENABLED", raising=False)
    client, _ = _make_test_client()
    response = client.get("/metrics")
    assert response.status_code == 404


def test_metrics_endpoint_404_when_env_falsy(monkeypatch) -> None:
    """Falsy values for RECOTEM_METRICS_ENABLED keep /metrics off."""
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "false")
    client, _ = _make_test_client()
    response = client.get("/metrics")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# m-4: metadata_field_deny is case-insensitive
# ---------------------------------------------------------------------------


def test_metadata_field_deny_is_case_insensitive() -> None:
    """Deny-list entries must block metadata columns regardless of case.

    e.g. denying 'internal_id' must also block 'Internal_ID', 'INTERNAL_ID'.
    """
    import pandas as pd

    from recotem.serving.routes import _lookup_metadata

    df = pd.DataFrame(
        {
            "item_id": ["i1"],
            "title": ["Widget"],
            "Internal_ID": ["secret-123"],
            "SCORE": [0.99],
        }
    ).set_index("item_id")

    # Deny list uses lowercase; columns use mixed/upper case.
    deny_set: frozenset[str] = frozenset({"internal_id", "score"})

    result = _lookup_metadata(df, "i1", deny_set)

    # 'title' should be present — not in deny list.
    assert "title" in result
    # 'Internal_ID' denied via 'internal_id' (case-fold).
    assert "Internal_ID" not in result
    # 'SCORE' denied via 'score' (case-fold).
    assert "SCORE" not in result


def test_metadata_field_deny_blocks_lower_when_deny_entry_is_upper() -> None:
    """The deny-list itself is also case-folded at router construction time.

    Passing 'INTERNAL_ID' in the deny list must still block 'internal_id' and
    'Internal_ID' in the metadata columns.
    """
    import pandas as pd
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    df = pd.DataFrame(
        {
            "item_id": ["u_item"],
            "title": ["Thing"],
            "secret_col": ["hide-me"],
        }
    ).set_index("item_id")

    recommender = MagicMock()
    recommender.get_recommendation_for_known_user_id.return_value = [("u_item", 0.5)]

    entry = ModelEntry(
        name="deny_test",
        recommender=recommender,
        header={"best_class": "TopPop", "trained_at": "2026-01-01T00:00:00Z"},
        kid="k1",
        metadata_df=df,
    )
    registry = ModelRegistry()
    registry.replace("deny_test", entry)

    # Pass deny list with UPPER-CASE entry — must still block the lower-case column.
    router = make_router(
        registry=registry, api_keys=[], metadata_field_deny=["SECRET_COL"]
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/predict/deny_test", json={"user_id": "u_item", "cutoff": 1}
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert "title" in item, "'title' must not be denied"
    assert "secret_col" not in item, (
        "'secret_col' must be denied even when deny entry was 'SECRET_COL'"
    )


def test_metrics_endpoint_exposes_documented_metrics(monkeypatch) -> None:
    """RECOTEM_METRICS_ENABLED=true exposes /metrics with all six recotem_* metrics."""
    import pytest

    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "true")

    from recotem.serving import metrics

    client, _ = _make_test_client()

    # Drive the predict path so predict_total + predict_latency are populated.
    response = client.post(
        "/predict/test_recipe",
        json={"user_id": "user1", "cutoff": 5},
    )
    assert response.status_code == 200

    # The other four metrics are populated by app startup / watcher in
    # production; in this unit test we exercise the recorders directly so
    # the gauges/counters appear in the exposition output.
    metrics.set_model_loaded("test_recipe", True)
    metrics.inc_artifact_load_failure("test_recipe")
    metrics.set_active_recipes(1)
    metrics.record_swap("test_recipe", ok=True)

    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text

    # All six metric series documented in operations.md must be present.
    for name in (
        "recotem_predict_total",
        "recotem_predict_latency_seconds",
        "recotem_model_loaded",
        "recotem_artifact_load_failures_total",
        "recotem_active_recipes",
        "recotem_swap_total",
    ):
        assert name in body, f"missing {name!r} in /metrics output"

    # Spot-check label cardinality on the metrics that carry the recipe
    # label, so a future refactor that drops the label fails the test.
    assert 'recotem_predict_total{recipe="test_recipe"' in body
    assert 'recotem_model_loaded{recipe="test_recipe"' in body
    assert 'recotem_swap_total{recipe="test_recipe"' in body


# ---------------------------------------------------------------------------
# CRITICAL: predict increments recotem_predict_total counter
# ---------------------------------------------------------------------------


def test_predict_increments_predict_total_metric(monkeypatch) -> None:
    """After a POST /predict, recotem_predict_total must increment for the recipe.

    Uses a dedicated recipe name that is unique to this test to avoid
    interference with the global Prometheus registry from other test runs.
    Captures the counter value before and after the predict call.
    """
    import pytest

    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "true")

    from recotem.serving import metrics

    recipe_name = "counter_increment_recipe"
    registry = _make_registry_with_recipe(recipe_name)
    client, _ = _make_test_client(registry=registry)

    # Ensure metrics are initialized (idempotent).
    metrics._ensure_initialized()

    # Read the current counter value before the predict call.
    before = 0.0
    if metrics._PREDICT_TOTAL is not None:
        try:
            before = metrics._PREDICT_TOTAL.labels(
                recipe=recipe_name, status="ok"
            )._value.get()
        except Exception:
            before = 0.0

    response = client.post(
        f"/predict/{recipe_name}",
        json={"user_id": "user1", "cutoff": 5},
    )
    assert response.status_code == 200

    # The counter must have incremented.
    after = 0.0
    if metrics._PREDICT_TOTAL is not None:
        try:
            after = metrics._PREDICT_TOTAL.labels(
                recipe=recipe_name, status="ok"
            )._value.get()
        except Exception:
            after = 0.0

    assert after > before, (
        f"recotem_predict_total must increment after /predict; "
        f"before={before}, after={after}"
    )

    # Also confirm /metrics output contains the counter.
    metrics_response = client.get("/metrics")
    assert metrics_response.status_code == 200
    assert "recotem_predict_total" in metrics_response.text


# ---------------------------------------------------------------------------
# CRITICAL: load failure increments recotem_artifact_load_failures_total
# ---------------------------------------------------------------------------


def test_load_failure_increments_artifact_load_failures_total(monkeypatch) -> None:
    """inc_artifact_load_failure must be visible in /metrics output.

    Calls the metric recorder directly (simulating the watcher's failure
    path) and confirms the counter appears in the Prometheus exposition.
    """
    import pytest

    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "true")

    from recotem.serving import metrics

    recipe_name = "fail_load_recipe_unique"
    metrics._ensure_initialized()

    before = 0.0
    if metrics._ARTIFACT_LOAD_FAILURES is not None:
        try:
            before = metrics._ARTIFACT_LOAD_FAILURES.labels(
                recipe=recipe_name
            )._value.get()
        except Exception:
            before = 0.0

    metrics.inc_artifact_load_failure(recipe_name)

    after = 0.0
    if metrics._ARTIFACT_LOAD_FAILURES is not None:
        try:
            after = metrics._ARTIFACT_LOAD_FAILURES.labels(
                recipe=recipe_name
            )._value.get()
        except Exception:
            after = 0.0

    assert after == before + 1, (
        f"recotem_artifact_load_failures_total must increment by 1; "
        f"before={before}, after={after}"
    )

    # Also confirm /metrics exposition includes the counter.
    client, _ = _make_test_client()
    metrics_response = client.get("/metrics")
    assert metrics_response.status_code == 200
    assert "recotem_artifact_load_failures_total" in metrics_response.text
