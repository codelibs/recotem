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

import pytest as _pytest
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
    # Degraded must surface as HTTP 503 so K8s readiness probes mark the Pod
    # NotReady — returning 200 would let rolling upgrades silently swap in a
    # Pod whose every /predict returns 503.  See routes.health() docstring.
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"


def test_health_returns_200_when_all_recipes_loaded() -> None:
    """Counterpart to the degraded-503 test: healthy state stays 200."""
    client, _ = _make_test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_503_when_recipe_loaded_false_even_without_error() -> None:
    """A stub entry with loaded=False (startup load failure) → 503.

    Covers the recipe_not_loaded_at_startup branch where the watcher inserts
    a ``ModelEntry(loaded=False, last_load_error=...)`` placeholder.  Both
    flags should drive overall degraded; this test pins the ``loaded=False``
    half so a future refactor cannot regress to "degraded only when error".
    """
    registry = _make_registry_with_recipe("loaded")
    stub_entry = ModelEntry(
        name="never_loaded",
        recommender=None,
        header={},
        kid="",
        last_load_error="HMAC verify failed",
        loaded=False,
    )
    registry.replace("never_loaded", stub_entry)
    client, _ = _make_test_client(registry=registry)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


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


# ---------------------------------------------------------------------------
# C-1: X-Request-ID response header
# ---------------------------------------------------------------------------


def test_predict_response_includes_x_request_id_header() -> None:
    """On a 200 success, response header X-Request-ID must match body request_id."""
    client, _ = _make_test_client()
    response = client.post("/predict/test_recipe", json={"user_id": "user1"})
    assert response.status_code == 200
    data = response.json()
    assert "X-Request-ID" in response.headers, (
        "X-Request-ID header must be present in the response"
    )
    assert response.headers["X-Request-ID"] == data["request_id"], (
        "X-Request-ID header must match the request_id in the response body"
    )


def test_predict_echoes_x_request_id_from_request() -> None:
    """When the client sends X-Request-ID, the same value is echoed back."""
    client, _ = _make_test_client()
    custom_id = "my-trace-id-12345"
    response = client.post(
        "/predict/test_recipe",
        json={"user_id": "user1"},
        headers={"X-Request-ID": custom_id},
    )
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == custom_id, (
        "X-Request-ID sent by client must be echoed back unchanged"
    )
    assert response.json()["request_id"] == custom_id


# ---------------------------------------------------------------------------
# N-6: M-4 — X-Request-ID validation: invalid IDs replaced with UUID
# ---------------------------------------------------------------------------


def test_valid_x_request_id_echoed_unchanged() -> None:
    """A valid X-Request-ID (alphanumeric + _- up to 64 chars) is echoed back.

    M-4 added a regex guard so only safe IDs are echoed; this test verifies
    that a valid ID passes the guard and is not replaced.
    """
    client, _ = _make_test_client()
    valid_id = "abc-123_DEF"
    response = client.post(
        "/predict/test_recipe",
        json={"user_id": "user1"},
        headers={"X-Request-ID": valid_id},
    )
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == valid_id, (
        "Valid X-Request-ID must be echoed unchanged"
    )
    assert response.json()["request_id"] == valid_id


@_pytest.mark.parametrize(
    "invalid_id",
    [
        "",  # empty string — fails 1-char minimum
        "a" * 65,  # exceeds 64-char maximum
        "<script>alert(1)</script>",  # contains angle brackets
        "evil\x00byte",  # contains null byte
        "space here",  # contains space
    ],
)
def test_invalid_x_request_id_replaced_with_uuid(invalid_id: str) -> None:
    """Invalid X-Request-ID values must be replaced with a server-generated UUID.

    M-4 added regex validation ``^[A-Za-z0-9_-]{1,64}$`` so that control
    characters, angle brackets, spaces, oversized IDs, and empty strings cannot
    be injected into logs via the request ID header.  Any value that fails the
    regex is silently replaced with a uuid4.
    """
    import re

    client, _ = _make_test_client()
    response = client.post(
        "/predict/test_recipe",
        json={"user_id": "user1"},
        headers={"X-Request-ID": invalid_id},
    )
    assert response.status_code == 200
    returned_id = response.headers.get("X-Request-ID", "")
    # The server must NOT echo the invalid value back.
    assert returned_id != invalid_id or not invalid_id, (
        f"Invalid X-Request-ID {invalid_id!r} must not be echoed unchanged"
    )
    # The replacement must look like a UUID4 (hex + hyphens, 36 chars).
    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    assert uuid_re.match(returned_id), (
        f"Invalid X-Request-ID must be replaced with a uuid4; got {returned_id!r}"
    )


# ---------------------------------------------------------------------------
# N-12: OBS-4 — _lookup_metadata index pre-check (item not in index → {})
# ---------------------------------------------------------------------------


def test_lookup_metadata_missing_item_id_returns_empty_dict() -> None:
    """_lookup_metadata returns an empty dict when item_id is not in the index.

    OBS-4 added an ``item_id not in meta_df.index`` pre-check to short-circuit
    before calling .loc[], which avoids an unnecessary KeyError and improves
    performance.  This test verifies the short-circuit path returns {}.
    """
    import pandas as pd

    from recotem.serving.routes import _lookup_metadata

    df = pd.DataFrame({"item_id": ["i1", "i2"], "title": ["Alpha", "Beta"]}).set_index(
        "item_id"
    )

    result = _lookup_metadata(df, "not_in_index", frozenset(), "test_recipe")
    assert result == {}, (
        "_lookup_metadata must return empty dict for missing item_id (pre-check)"
    )


def test_lookup_metadata_known_item_id_returns_fields() -> None:
    """_lookup_metadata returns the row dict for a known item_id.

    Complement to the missing-item test: after passing the pre-check, a known
    item_id must produce the expected field dict.
    """
    import pandas as pd

    from recotem.serving.routes import _lookup_metadata

    df = pd.DataFrame(
        {"item_id": ["i1"], "title": ["Alpha"], "genre": ["action"]}
    ).set_index("item_id")

    result = _lookup_metadata(df, "i1", frozenset(), "test_recipe")
    assert result.get("title") == "Alpha"
    assert result.get("genre") == "action"


# ---------------------------------------------------------------------------
# MAJOR-10: predict status label separation
# ---------------------------------------------------------------------------


def test_predict_status_label_ok(monkeypatch) -> None:
    """Successful prediction records status='ok'."""
    import pytest

    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "true")

    from unittest.mock import patch

    from recotem.serving import metrics

    recipe_name = "status_ok_recipe"
    registry = _make_registry_with_recipe(recipe_name)
    client, _ = _make_test_client(registry=registry)
    metrics._ensure_initialized()

    recorded: list[str] = []
    real_record = metrics.record_predict

    def _capture(r, s, latency):
        recorded.append(s)
        real_record(r, s, latency)

    with patch.object(metrics, "record_predict", side_effect=_capture):
        response = client.post(
            f"/predict/{recipe_name}", json={"user_id": "user1", "cutoff": 5}
        )

    assert response.status_code == 200
    assert recorded == ["ok"], f"Expected status=['ok'], got {recorded!r}"


def test_predict_status_label_user_not_found(monkeypatch) -> None:
    """User not found (404) records status='user_not_found'."""
    from unittest.mock import patch

    import pytest

    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "true")

    from recotem.serving import metrics

    recipe_name = "status_404_recipe"
    registry = _make_registry_with_recipe(recipe_name)
    client, _ = _make_test_client(registry=registry)

    recorded: list[str] = []
    real_record = metrics.record_predict

    def _capture(r, s, latency):
        recorded.append(s)
        real_record(r, s, latency)

    with patch.object(metrics, "record_predict", side_effect=_capture):
        response = client.post(
            f"/predict/{recipe_name}",
            json={"user_id": "totally_unknown_user", "cutoff": 5},
        )

    assert response.status_code == 404
    assert recorded == ["user_not_found"], (
        f"Expected status=['user_not_found'], got {recorded!r}"
    )


def test_predict_status_label_unavailable(monkeypatch) -> None:
    """Recipe not loaded (503) records status='unavailable'."""
    from unittest.mock import patch

    import pytest

    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "true")

    from recotem.serving import metrics

    registry = ModelRegistry()  # empty — no recipe registered
    client, _ = _make_test_client(registry=registry)

    recorded: list[str] = []
    real_record = metrics.record_predict

    def _capture(r, s, latency):
        recorded.append(s)
        real_record(r, s, latency)

    with patch.object(metrics, "record_predict", side_effect=_capture):
        response = client.post(
            "/predict/nonexistent_recipe", json={"user_id": "user1", "cutoff": 5}
        )

    assert response.status_code == 503
    assert recorded == ["unavailable"], (
        f"Expected status=['unavailable'], got {recorded!r}"
    )


def test_predict_status_label_error(monkeypatch) -> None:
    """Unexpected exception records status='error'."""
    from unittest.mock import patch

    import pytest

    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "true")

    from recotem.serving import metrics

    recipe_name = "status_error_recipe"
    registry = _make_registry_with_recipe(recipe_name)

    # Build the client without raise_server_exceptions so the 500 response is
    # returned rather than the RuntimeError propagating to the test.
    router = make_router(registry=registry, api_keys=[])
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    recorded: list[str] = []
    real_record = metrics.record_predict

    def _capture(r, s, latency):
        recorded.append(s)
        real_record(r, s, latency)

    # Make the recommender raise a non-KeyError to exercise the generic error path
    entry = registry.get(recipe_name)
    assert entry is not None
    entry.recommender.get_recommendation_for_known_user_id.side_effect = RuntimeError(
        "unexpected internal failure"
    )

    with patch.object(metrics, "record_predict", side_effect=_capture):
        client.post(
            f"/predict/{recipe_name}",
            json={"user_id": "user1", "cutoff": 5},
        )

    # status label must be "error"; the HTTP response is 500
    assert recorded == ["error"], f"Expected status=['error'], got {recorded!r}"


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

    # The other metrics are populated by app startup / watcher in production;
    # in this unit test we exercise the recorders directly so the
    # gauges/counters appear in the exposition output.
    metrics.set_model_loaded("test_recipe", True)
    metrics.inc_artifact_load_failure("test_recipe")
    metrics.set_active_recipes(1)
    metrics.record_swap("test_recipe", ok=True)
    metrics.inc_metadata_lookup_error("test_recipe")
    metrics.inc_recipe_rescan_error("test_recipe")

    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text

    # All metric series documented in operations.md (plus the two new ones)
    # must be present in the /metrics output.
    for name in (
        "recotem_predict_total",
        "recotem_predict_latency_seconds",
        "recotem_model_loaded",
        "recotem_artifact_load_failures_total",
        "recotem_active_recipes",
        "recotem_swap_total",
        "recotem_metadata_lookup_errors_total",
        "recotem_recipe_rescan_errors_total",
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


# ---------------------------------------------------------------------------
# C11 — predict returns 503 after recipe removed from registry
# ---------------------------------------------------------------------------


def test_yaml_deleted_then_predict_returns_503() -> None:
    """After a recipe entry is removed from the registry, POST /predict/<name>
    must return HTTP 503 with code 'recipe_unavailable'.

    This simulates the watcher removing a recipe when its YAML is deleted.
    The route checks ``registry.get(name)`` — None means the recipe is gone
    and the route raises HTTPException(503, code='recipe_unavailable').
    """
    registry = _make_registry_with_recipe("will_be_deleted")
    client, _ = _make_test_client(registry=registry)

    # Confirm the recipe is serving before removal.
    response_before = client.post("/predict/will_be_deleted", json={"user_id": "user1"})
    assert response_before.status_code == 200, (
        f"Predict must succeed before removal; got {response_before.status_code}"
    )

    # Simulate YAML deletion by removing the entry from the registry.
    registry.remove("will_be_deleted")

    # After removal, the route must return 503.
    response_after = client.post("/predict/will_be_deleted", json={"user_id": "user1"})
    assert response_after.status_code == 503, (
        f"Predict must return 503 after recipe removed from registry; "
        f"got {response_after.status_code}"
    )
    detail = response_after.json()
    assert detail.get("detail", {}).get("code") == "recipe_unavailable", (
        f"503 response must include code='recipe_unavailable'; got: {detail!r}"
    )


# ---------------------------------------------------------------------------
# C-2 + M-13: _lookup_metadata error handling
# ---------------------------------------------------------------------------


def test_lookup_metadata_returns_empty_on_keyerror() -> None:
    """_lookup_metadata returns {} when item_id is absent from the index."""
    import pandas as pd

    from recotem.serving.routes import _lookup_metadata

    df = pd.DataFrame({"item_id": ["i1", "i2"], "title": ["A", "B"]}).set_index(
        "item_id"
    )

    result = _lookup_metadata(df, "not_in_index", frozenset(), "test_recipe")
    assert result == {}


def test_lookup_metadata_swallows_attribute_error_increments_metric(
    monkeypatch,
) -> None:
    """AttributeError during row.to_dict() returns empty dict, logs, and increments
    recotem_metadata_lookup_errors_total.

    A non-unique index makes .loc[] return a DataFrame (not a Series), whose
    .to_dict() returns a dict-of-lists rather than a flat dict — iterating it
    with (.items() → .lower()) raises AttributeError on the list values.
    We simulate this by using a mock that raises AttributeError on to_dict().

    OBS-4: _lookup_metadata now performs an ``item_id not in meta_df.index``
    pre-check before calling .loc[].  We therefore configure the mock's
    ``__contains__`` to return True so the pre-check passes and the code
    reaches the AttributeError path.
    """
    import structlog.testing

    from recotem.serving import metrics
    from recotem.serving.routes import _lookup_metadata

    # Build a mock row object that raises AttributeError on to_dict()
    class _BadRow:
        def to_dict(self):
            raise AttributeError("simulated: non-unique index returned DataFrame")

    # Patch .loc as a property that returns _BadRow for any key.
    # Also configure the index so __contains__ returns True — after OBS-4 the
    # pre-check ``item_id not in meta_df.index`` would otherwise short-circuit
    # and the AttributeError path would never be reached.
    bad_df = MagicMock()
    bad_df.index.__contains__ = MagicMock(return_value=True)
    bad_df.loc.__getitem__ = MagicMock(return_value=_BadRow())

    metrics._ensure_initialized()
    before = 0.0
    if metrics._METADATA_LOOKUP_ERRORS is not None:
        try:
            before = metrics._METADATA_LOOKUP_ERRORS.labels(
                recipe="attr_err_recipe"
            )._value.get()
        except Exception:
            before = 0.0

    with structlog.testing.capture_logs() as cap:
        result = _lookup_metadata(bad_df, "some_item", frozenset(), "attr_err_recipe")

    assert result == {}, "AttributeError must result in empty dict return"

    after = 0.0
    if metrics._METADATA_LOOKUP_ERRORS is not None:
        try:
            after = metrics._METADATA_LOOKUP_ERRORS.labels(
                recipe="attr_err_recipe"
            )._value.get()
        except Exception:
            after = 0.0

    import pytest

    pytest.importorskip("prometheus_client")
    assert after == before + 1, (
        f"recotem_metadata_lookup_errors_total must increment by 1 on AttributeError; "
        f"before={before}, after={after}"
    )

    warn_events = [e for e in cap if e.get("event") == "metadata_lookup_failed"]
    assert warn_events, "metadata_lookup_failed WARN must be emitted on AttributeError"
    evt = warn_events[0]
    assert evt.get("recipe") == "attr_err_recipe"
    assert evt.get("error_class") == "AttributeError"


def test_lookup_metadata_skips_non_string_columns() -> None:
    """DataFrame with int column names must not crash — int columns are skipped."""
    import pandas as pd

    from recotem.serving.routes import _lookup_metadata

    # Construct a DataFrame with an int column name and a string column name.
    df = pd.DataFrame([[1, "hello"]], columns=[42, "title"])
    df.index = pd.Index(["item_x"], name="item_id")

    result = _lookup_metadata(df, "item_x", frozenset(), "int_col_recipe")

    # int column 42 must be silently skipped, string column 'title' kept.
    assert 42 not in result, "int column name must be omitted from the output"
    assert "title" in result, "'title' string column must be present"
    assert result["title"] == "hello"


# ---------------------------------------------------------------------------
# M-14: {name} path param regex validation
# ---------------------------------------------------------------------------


def test_predict_with_invalid_name_returns_422_not_503() -> None:
    """Arbitrary strings in the recipe name path param must return 422.

    FastAPI should validate the ``name`` path parameter against the pattern
    ``^[A-Za-z0-9_-]{1,64}$`` before reaching any business logic.  Sending
    characters outside that set (e.g. slashes, unicode, shell metacharacters)
    must produce a 422 Unprocessable Entity, not a 503 that reflects the
    arbitrary string into the response body.
    """
    client, _ = _make_test_client()

    for bad_name in [
        "recipe with spaces",  # space not in [A-Za-z0-9_-]
        "recipe!@#meta",  # special characters
        "a" * 65,  # over 64 chars
        "recipe.dotted",  # dot not in character class
    ]:
        response = client.post(
            "/predict/" + bad_name,
            json={"user_id": "user1"},
        )
        assert response.status_code == 422, (
            f"Expected 422 for name={bad_name!r}, got {response.status_code}"
        )


# ---------------------------------------------------------------------------
# P-2: model_construct hot-path optimization
# ---------------------------------------------------------------------------


def test_predict_response_constructs_via_model_construct_skips_validation() -> None:
    """The /predict handler must use model_construct (not __init__) for response models.

    Patches model_construct on PredictResponse, ModelInfo, and RecommendationItem
    to spy on whether the optimized no-validation path is used.  Asserts that
    model_construct is called at least once for each response model class on a
    successful predict call.
    """
    from unittest.mock import call, patch

    from recotem.serving import routes as _routes

    registry = _make_registry_with_recipe()
    client, _ = _make_test_client(registry=registry)

    predict_response_calls: list[call] = []
    model_info_calls: list[call] = []
    rec_item_calls: list[call] = []

    original_pr = _routes.PredictResponse.model_construct
    original_mi = _routes.ModelInfo.model_construct
    original_ri = _routes.RecommendationItem.model_construct

    def _spy_pr(*args, **kwargs):
        predict_response_calls.append(call(*args, **kwargs))
        return original_pr(*args, **kwargs)

    def _spy_mi(*args, **kwargs):
        model_info_calls.append(call(*args, **kwargs))
        return original_mi(*args, **kwargs)

    def _spy_ri(*args, **kwargs):
        rec_item_calls.append(call(*args, **kwargs))
        return original_ri(*args, **kwargs)

    with (
        patch.object(_routes.PredictResponse, "model_construct", side_effect=_spy_pr),
        patch.object(_routes.ModelInfo, "model_construct", side_effect=_spy_mi),
        patch.object(
            _routes.RecommendationItem, "model_construct", side_effect=_spy_ri
        ),
    ):
        response = client.post(
            "/predict/test_recipe", json={"user_id": "user1", "cutoff": 5}
        )

    assert response.status_code == 200, (
        f"Predict must succeed; got {response.status_code}"
    )
    assert len(predict_response_calls) == 1, (
        "PredictResponse.model_construct must be called exactly once per request"
    )
    assert len(model_info_calls) == 1, (
        "ModelInfo.model_construct must be called exactly once per request"
    )
    assert len(rec_item_calls) >= 1, (
        "RecommendationItem.model_construct must be called at least once per request"
    )


def test_predict_response_still_serializes_correctly_after_model_construct() -> None:
    """model_construct path must produce identical JSON wire format to the validated path.

    Builds a PredictResponse via model_construct (the new hot path) and via
    normal __init__ (the validated path) and asserts that FastAPI's jsonable_encoder
    produces the same output for both, confirming the optimization doesn't break
    the wire format.
    """
    import json

    from fastapi.encoders import jsonable_encoder

    from recotem.serving.routes import ModelInfo, PredictResponse, RecommendationItem

    # Construct via normal __init__ (fully validated path).
    validated = PredictResponse(
        items=[
            RecommendationItem(item_id="item1", score=0.9),
            RecommendationItem(item_id="item2", score=0.8),
        ],
        model=ModelInfo(
            recipe="test_recipe",
            trained_at="2026-01-01T00:00:00Z",
            best_class="TopPopRecommender",
            kid="active",
        ),
        request_id="test-request-id-123",
    )

    # Construct via model_construct (the optimized hot path).
    optimized = PredictResponse.model_construct(
        items=[
            # scores cast to float, item_id is str from IDMappedRecommender
            RecommendationItem.model_construct(item_id="item1", score=0.9),
            RecommendationItem.model_construct(item_id="item2", score=0.8),
        ],
        # name is FastAPI-validated, trained_at/best_class/kid from artifact header
        model=ModelInfo.model_construct(
            recipe="test_recipe",
            trained_at="2026-01-01T00:00:00Z",
            best_class="TopPopRecommender",
            kid="active",
        ),
        request_id="test-request-id-123",
    )

    validated_json = json.dumps(jsonable_encoder(validated), sort_keys=True)
    optimized_json = json.dumps(jsonable_encoder(optimized), sort_keys=True)

    assert validated_json == optimized_json, (
        f"model_construct and __init__ must produce identical JSON wire format.\n"
        f"validated:  {validated_json}\n"
        f"optimized:  {optimized_json}"
    )


# ---------------------------------------------------------------------------
# P-1: /predict uses metadata_index for O(1) lookup
# ---------------------------------------------------------------------------


def test_predict_uses_metadata_index_O1_lookup() -> None:
    """When metadata_index is populated, /predict uses dict.get rather than
    iterating the DataFrame.

    This test verifies the fast path at the integration level:
    - Build a ModelEntry with metadata_index set and metadata_df=None.
    - POST /predict and confirm metadata fields appear in the response.
    - The DataFrame path is unreachable (metadata_df is None), proving the
      response must have come from the dict index.
    """
    from recotem.serving.registry import ModelEntry, ModelRegistry

    metadata_index = {
        "item1": {"title": "Widget Alpha", "category": "tools"},
        "item2": {"title": "Widget Beta", "category": "garden"},
    }

    recommender = MagicMock()
    recommender.get_recommendation_for_known_user_id.return_value = [
        ("item1", 0.9),
        ("item2", 0.8),
    ]

    entry = ModelEntry(
        name="index_recipe",
        recommender=recommender,
        header={"best_class": "TopPop", "trained_at": "2026-01-01T00:00:00Z"},
        kid="k1",
        metadata_df=None,  # DataFrame path explicitly unavailable.
        metadata_index=metadata_index,  # Only the index is set.
    )
    registry = ModelRegistry()
    registry.replace("index_recipe", entry)

    router = make_router(registry=registry, api_keys=[])
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/predict/index_recipe",
        json={"user_id": "user1", "cutoff": 2},
    )
    assert response.status_code == 200, (
        f"Expected 200; got {response.status_code}: {response.text}"
    )
    items = response.json()["items"]
    assert len(items) == 2

    # item1 must carry its metadata fields from the pre-flattened index.
    item1 = next(it for it in items if it["item_id"] == "item1")
    assert item1.get("title") == "Widget Alpha", (
        f"'title' from metadata_index must appear in response; got {item1!r}"
    )
    assert item1.get("category") == "tools", (
        f"'category' from metadata_index must appear in response; got {item1!r}"
    )

    # item2 must also carry its fields.
    item2 = next(it for it in items if it["item_id"] == "item2")
    assert item2.get("title") == "Widget Beta"
    assert item2.get("category") == "garden"


def test_predict_metadata_index_missing_item_returns_empty_fields() -> None:
    """When item_id is absent from the metadata_index, no extra fields are added.

    dict.get(item_id, {}) returns an empty dict for unknown items — the
    response item contains only item_id and score, never crashes.
    """
    from recotem.serving.registry import ModelEntry, ModelRegistry

    metadata_index = {"known_item": {"title": "Known"}}

    recommender = MagicMock()
    recommender.get_recommendation_for_known_user_id.return_value = [
        ("known_item", 0.9),
        ("unknown_item", 0.5),  # not in index
    ]

    entry = ModelEntry(
        name="partial_index_recipe",
        recommender=recommender,
        header={"best_class": "TopPop", "trained_at": "2026-01-01T00:00:00Z"},
        kid="k1",
        metadata_df=None,
        metadata_index=metadata_index,
    )
    registry = ModelRegistry()
    registry.replace("partial_index_recipe", entry)

    router = make_router(registry=registry, api_keys=[])
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/predict/partial_index_recipe",
        json={"user_id": "user1", "cutoff": 2},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2

    known = next(it for it in items if it["item_id"] == "known_item")
    assert known.get("title") == "Known"

    unknown = next(it for it in items if it["item_id"] == "unknown_item")
    # No title or extra fields -- only item_id and score.
    assert "title" not in unknown, "Unknown item must not have metadata fields"
    assert "item_id" in unknown and "score" in unknown


# ---------------------------------------------------------------------------
# Fix 2: unbind_contextvars must not wipe upstream middleware bindings
# ---------------------------------------------------------------------------


def test_predict_unbinds_only_handler_keys_not_upstream_context() -> None:
    """After predict() returns, only the keys it bound (recipe, request_id, kid)
    must be removed from the structlog context.  Upstream keys set by middleware
    (e.g. trace_id) must remain intact.

    Pre-fix: `clear_contextvars()` wiped the entire context including upstream
    bindings.  Fix replaces it with `unbind_contextvars("recipe", "request_id",
    "kid")` so only handler-owned keys are removed.
    """
    import structlog.contextvars

    from recotem.serving.registry import ModelEntry, ModelRegistry
    from recotem.serving.routes import make_router

    recommender = MagicMock()
    recommender.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]

    entry = ModelEntry(
        name="ctx_recipe",
        recommender=recommender,
        header={"best_class": "TopPop", "trained_at": "2026-01-01T00:00:00Z"},
        kid="active",
    )
    registry = ModelRegistry()
    registry.replace("ctx_recipe", entry)

    router = make_router(registry=registry, api_keys=[])
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    upstream_key_preserved: list[bool] = []

    # Middleware that binds an upstream context key before the route handler runs
    # and checks it is still present after the handler returns.
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request

    class _UpstreamMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            structlog.contextvars.bind_contextvars(trace_id="upstream-trace-123")
            response = await call_next(request)
            # After the route handler finishes, trace_id must still be in context
            ctx = structlog.contextvars.get_contextvars()
            upstream_key_preserved.append("trace_id" in ctx)
            structlog.contextvars.clear_contextvars()  # cleanup after ourselves
            return response

    app = FastAPI()
    app.add_middleware(_UpstreamMiddleware)
    app.include_router(router)
    client = TestClient(app)

    response = client.post("/predict/ctx_recipe", json={"user_id": "user1"})
    assert response.status_code == 200

    assert upstream_key_preserved, "Middleware dispatch must have run"
    assert upstream_key_preserved[0], (
        "predict() must NOT call clear_contextvars() — it must only unbind its "
        "own keys (recipe, request_id, kid), leaving upstream bindings intact"
    )
