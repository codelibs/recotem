# tests/unit/test_v1_error_handling.py
"""Tests for v1 serving error-handling, request-ID, and logging behaviour.

Covers:
- X-Request-ID echo / fallback / invalid-input handling and body/header parity.
- Flat error body shape produced by the custom HTTPException handler.
- 422 RequestValidationError handler shape and validation_error metric.
- recipe_unavailable warning log on 404 not-found / 503 not-loaded paths.
- error_class field on recommend_unexpected_error log lines.
- structlog contextvars binding (recipe / kid / request_id) during requests.
"""

from __future__ import annotations

import hashlib
import re
from unittest.mock import MagicMock

import pytest
import structlog.testing
from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Pattern matching the fallback request_id (12 lowercase hex chars).
_FALLBACK_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _loaded_entry(rec: MagicMock | None = None, name: str = "demo") -> ModelEntry:
    """Build a fully-loaded ModelEntry around the given mock recommender."""
    if rec is None:
        rec = MagicMock()
        rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    return ModelEntry(
        name=name,
        recommender=rec,
        header={},
        kid="test-kid",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc123"),
        loaded_at_unix=1747800000.0,
    )


def _stub_entry(name: str = "stub_recipe") -> ModelEntry:
    """Build a registered-but-not-loaded entry (loaded=False)."""
    return ModelEntry(
        name=name,
        recommender=None,
        header={},
        kid="",
        loaded=False,
    )


def _make_api_entry(plaintext: str, kid: str = "api-key") -> ApiKeyEntry:
    """Build an ApiKeyEntry matching ``recotem.serving.auth._hash_api_key``."""
    digest = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    return ApiKeyEntry(kid=kid, sha256_hex=digest)


def _client_with(entry: ModelEntry) -> TestClient:
    registry = ModelRegistry()
    registry.replace(entry.name, entry)
    return TestClient(build_v1_app(registry))


# ---------------------------------------------------------------------------
# 1. X-Request-ID consistency
# ---------------------------------------------------------------------------


def test_request_id_echoed_when_valid_long_value() -> None:
    """A 64-char alphanumeric X-Request-ID (the documented max) is echoed in
    both the response body and the X-Request-ID response header, and the
    two values are identical."""
    client = _client_with(_loaded_entry())
    sent = "a" * 64
    assert len(sent) == 64

    r = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1", "limit": 1},
        headers={"X-Request-ID": sent},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["request_id"] == sent
    assert r.headers["X-Request-ID"] == sent
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_request_id_regenerated_when_over_64_chars() -> None:
    """An X-Request-ID over 64 characters is rejected by the regex and the
    server generates a fresh ID instead."""
    client = _client_with(_loaded_entry())
    sent = "a" * 65

    r = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1", "limit": 1},
        headers={"X-Request-ID": sent},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["request_id"] != sent
    assert r.headers["X-Request-ID"] != sent
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_request_id_fallback_when_header_absent() -> None:
    """When no X-Request-ID is sent, the server generates a 12-hex-char ID
    and uses the same value for both the body's request_id field and the
    X-Request-ID response header."""
    client = _client_with(_loaded_entry())
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 1})
    assert r.status_code == 200, r.text

    body = r.json()
    header_value = r.headers["X-Request-ID"]
    assert _FALLBACK_REQUEST_ID_RE.match(body["request_id"]), (
        f"Fallback request_id must be 12 hex chars; got {body['request_id']!r}"
    )
    assert header_value == body["request_id"]


def test_request_id_fallback_when_header_invalid() -> None:
    """When X-Request-ID contains a disallowed character (e.g. '!') the
    server discards the input and generates a fallback ID instead.  The
    fallback is reflected in both the header and the body, and they match."""
    client = _client_with(_loaded_entry())
    r = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1", "limit": 1},
        headers={"X-Request-ID": "bad!id"},
    )
    assert r.status_code == 200, r.text

    body = r.json()
    header_value = r.headers["X-Request-ID"]
    assert body["request_id"] != "bad!id", (
        "Server must NOT echo a header value containing disallowed chars"
    )
    assert _FALLBACK_REQUEST_ID_RE.match(body["request_id"]), (
        f"Fallback request_id must be 12 hex chars; got {body['request_id']!r}"
    )
    assert header_value == body["request_id"]


# ---------------------------------------------------------------------------
# 2. Flat error body shape
# ---------------------------------------------------------------------------


def test_flat_error_body_missing_api_key() -> None:
    """401 missing-key responses have a FLAT body (top-level code + detail)."""
    plaintext = "any_valid_length_api_key_32_chrs"
    api_entry = _make_api_entry(plaintext)
    registry = ModelRegistry()
    registry.replace("demo", _loaded_entry())
    client = TestClient(build_v1_app(registry, api_keys=[api_entry]))

    # No X-API-Key header at all.
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "missing_api_key"
    assert isinstance(body["detail"], str)
    assert body["detail"]
    # No legacy nested shape.
    assert not isinstance(body.get("detail"), dict)


def test_flat_error_body_invalid_api_key() -> None:
    """401 invalid-key responses have a FLAT body (top-level code + detail)."""
    plaintext = "correct_api_key_padded_to_32_chs"
    api_entry = _make_api_entry(plaintext)
    registry = ModelRegistry()
    registry.replace("demo", _loaded_entry())
    client = TestClient(build_v1_app(registry, api_keys=[api_entry]))

    r = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1"},
        headers={"X-API-Key": "wrong_key_value_padded_to_32_chs"},
    )
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "invalid_api_key"
    assert isinstance(body["detail"], str)
    assert not isinstance(body.get("detail"), dict)


def test_flat_error_body_recipe_not_found() -> None:
    """404 RECIPE_NOT_FOUND responses have a FLAT body."""
    client = _client_with(_loaded_entry())
    r = client.post("/v1/recipes/no_such:recommend", json={"user_id": "u1"})
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "RECIPE_NOT_FOUND"
    assert isinstance(body["detail"], str)
    assert not isinstance(body.get("detail"), dict)


def test_flat_error_body_recipe_unavailable() -> None:
    """503 RECIPE_UNAVAILABLE responses have a FLAT body."""
    client = _client_with(_stub_entry("demo"))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "RECIPE_UNAVAILABLE"
    assert isinstance(body["detail"], str)
    assert not isinstance(body.get("detail"), dict)


def test_flat_error_body_unknown_user() -> None:
    """404 UNKNOWN_USER responses have a FLAT body."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = KeyError("u1")
    client = _client_with(_loaded_entry(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "UNKNOWN_USER"
    assert isinstance(body["detail"], str)
    assert not isinstance(body.get("detail"), dict)


# ---------------------------------------------------------------------------
# 3. 422 validation handler
# ---------------------------------------------------------------------------


def test_validation_error_handler_body_shape_on_recommend() -> None:
    """A malformed body on an inference verb returns the standard 422 envelope."""
    client = _client_with(_loaded_entry())
    # limit=99999 violates the RecommendRequest.limit Field(le=1000) bound.
    r = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1", "limit": 99999},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["detail"] == "Request validation failed"
    assert body["code"] == "VALIDATION_ERROR"
    assert isinstance(body["errors"], list)
    assert body["errors"], "errors list must contain at least one entry"


def test_validation_error_handler_records_metric_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When metrics are enabled, a 422 on a v1 inference verb path records a
    ``recotem_v1_requests_total{status="validation_error"}`` counter sample
    for the (recipe, verb) tuple parsed from the URL.
    """
    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "1")

    # Reload the metrics module's lazy globals — they look up
    # metrics_enabled() each call but the v1 family objects are cached.
    from recotem.serving import metrics as _metrics

    monkeypatch.setattr(_metrics, "_V1_REQUEST_COUNTER", None)
    monkeypatch.setattr(_metrics, "_V1_REQUEST_LATENCY", None)
    monkeypatch.setattr(_metrics, "_V1_BATCH_SIZE", None)

    client = _client_with(_loaded_entry(name="metric_recipe"))

    # Drive a 422 on the recommend verb.
    r = client.post(
        "/v1/recipes/metric_recipe:recommend",
        json={"user_id": "u1", "limit": 99999},
    )
    assert r.status_code == 422

    # Read the Prometheus registry directly to confirm the sample exists.
    import prometheus_client

    output = prometheus_client.generate_latest().decode("utf-8")
    # Match a counter row for our (recipe, verb, status=validation_error)
    # triple.  Sample values are floats (e.g. "1.0").
    expected_line_re = re.compile(
        r"recotem_v1_requests_total\{"
        r'(?=.*recipe="metric_recipe")'
        r'(?=.*verb="recommend")'
        r'(?=.*status="validation_error")'
        r"[^}]*\}\s+([0-9.]+)"
    )
    matches = expected_line_re.findall(output)
    assert matches, (
        "Expected a recotem_v1_requests_total sample with "
        f"recipe=metric_recipe, verb=recommend, status=validation_error in:\n{output}"
    )
    assert float(matches[0]) >= 1.0, (
        f"Counter must be >= 1 after one 422; got {matches[0]} in:\n{output}"
    )


# ---------------------------------------------------------------------------
# 4. recipe_unavailable warning log
# ---------------------------------------------------------------------------


def test_recipe_unavailable_log_on_not_found() -> None:
    """Hitting a recommend verb against a non-registered recipe emits a
    ``recipe_unavailable`` warning with ``reason="not_found"``."""
    client = _client_with(_loaded_entry())

    with structlog.testing.capture_logs() as cap:
        r = client.post("/v1/recipes/no_such:recommend", json={"user_id": "u1"})

    assert r.status_code == 404
    events = [
        e
        for e in cap
        if e.get("event") == "recipe_unavailable" and e.get("reason") == "not_found"
    ]
    assert events, (
        f"Expected at least one recipe_unavailable / not_found event; got: {cap!r}"
    )
    assert events[0]["name"] == "no_such"


def test_recipe_unavailable_log_on_not_loaded() -> None:
    """Hitting a recommend verb against a registered-but-stub recipe emits
    ``recipe_unavailable`` with ``reason="not_loaded"``."""
    client = _client_with(_stub_entry("stubby"))

    with structlog.testing.capture_logs() as cap:
        r = client.post("/v1/recipes/stubby:recommend", json={"user_id": "u1"})

    assert r.status_code == 503
    events = [
        e
        for e in cap
        if e.get("event") == "recipe_unavailable" and e.get("reason") == "not_loaded"
    ]
    assert events, (
        f"Expected at least one recipe_unavailable / not_loaded event; got: {cap!r}"
    )
    assert events[0]["name"] == "stubby"


def test_recipe_unavailable_log_on_get_recipe_detail_not_found() -> None:
    """GET /v1/recipes/{name} for an unknown name emits recipe_unavailable
    with reason=not_found (the detail handler must follow the same pattern)."""
    client = _client_with(_loaded_entry())

    with structlog.testing.capture_logs() as cap:
        r = client.get("/v1/recipes/no_such")

    assert r.status_code == 404
    events = [
        e
        for e in cap
        if e.get("event") == "recipe_unavailable" and e.get("reason") == "not_found"
    ]
    assert events, (
        f"Expected recipe_unavailable / not_found from recipe_detail; got: {cap!r}"
    )


# ---------------------------------------------------------------------------
# 5. error_class in unexpected-error log
# ---------------------------------------------------------------------------


def test_error_class_in_recommend_unexpected_error_log() -> None:
    """When the recommender raises a non-KeyError, the recommend handler logs
    ``recommend_unexpected_error`` with ``error_class`` matching the exception
    type name."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = RuntimeError("boom")
    registry = ModelRegistry()
    registry.replace("demo", _loaded_entry(rec))
    # raise_server_exceptions=False so the 500 is observable from the client side.
    client = TestClient(build_v1_app(registry), raise_server_exceptions=False)

    with structlog.testing.capture_logs() as cap:
        r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})

    assert r.status_code == 500
    events = [e for e in cap if e.get("event") == "recommend_unexpected_error"]
    assert events, f"Expected recommend_unexpected_error to be emitted; got: {cap!r}"
    assert events[0].get("error_class") == "RuntimeError", (
        f"error_class must be 'RuntimeError'; got {events[0].get('error_class')!r}"
    )


# ---------------------------------------------------------------------------
# 6. structlog contextvars binding (recipe / kid)
# ---------------------------------------------------------------------------


def test_structlog_context_binds_recipe_and_kid_during_request() -> None:
    """During an inference request, log messages emitted from inside the
    handler carry the ``recipe`` and ``kid`` keys (bound via
    ``structlog.contextvars.bind_contextvars`` in routes.py).

    Trigger: force the recommender to raise ``RuntimeError`` so the route's
    ``logger.exception("recommend_unexpected_error", ...)`` fires INSIDE the
    region between ``bind_contextvars(recipe, kid)`` and the matching
    ``unbind_contextvars`` in the finally block.  The structlog merge
    processor for contextvars will inject ``recipe`` and ``kid`` onto that
    event before our spy processor sees it.
    """
    import structlog

    captured_kwargs: list[dict] = []

    def _spy_processor(_logger, _name, event_dict):
        captured_kwargs.append(dict(event_dict))
        return event_dict

    # Replace the structlog config with a minimal pipeline that merges
    # contextvars BEFORE our spy.  cache_logger_on_first_use=False so the
    # already-imported routes.py logger picks up this configuration.
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _spy_processor,
            structlog.processors.KeyValueRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        cache_logger_on_first_use=False,
    )

    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = RuntimeError("force")
    entry = ModelEntry(
        name="ctx_recipe",
        recommender=rec,
        header={},
        kid="model-kid",  # NOTE: this is the artifact kid, NOT the auth kid
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc123"),
        loaded_at_unix=1747800000.0,
    )

    # Configure an API key so the auth kid is non-anonymous and observable.
    plaintext = "ctx_recipe_test_api_key_padding!"
    api_entry = _make_api_entry(plaintext, kid="auth-kid")
    registry = ModelRegistry()
    registry.replace("ctx_recipe", entry)
    client = TestClient(
        build_v1_app(registry, api_keys=[api_entry]),
        raise_server_exceptions=False,
    )

    r = client.post(
        "/v1/recipes/ctx_recipe:recommend",
        json={"user_id": "u1", "limit": 1},
        headers={"X-API-Key": plaintext},
    )
    # The handler raises after logging recommend_unexpected_error; the
    # outer Exception handler in app.py converts that to a 500.
    assert r.status_code == 500, r.text

    # The recommend_unexpected_error event was emitted while contextvars
    # had recipe=ctx_recipe and kid=auth-kid bound, so the spy must see
    # those keys on the merged event_dict.  (routes.py binds the AUTH kid,
    # not the model-artifact kid, since it uses the kid: str = Depends(...)
    # path-parameter value.)
    target_events = [
        e for e in captured_kwargs if e.get("event") == "recommend_unexpected_error"
    ]
    assert target_events, (
        "Expected recommend_unexpected_error event; got: "
        f"{[e.get('event') for e in captured_kwargs]!r}"
    )
    bound = target_events[0]
    assert bound.get("recipe") == "ctx_recipe", (
        f"recipe contextvar missing on recommend_unexpected_error; got {bound!r}"
    )
    assert bound.get("kid") == "auth-kid", (
        f"kid contextvar missing on recommend_unexpected_error; got {bound!r}"
    )
    # request_id is bound by the middleware — must also be present.
    assert "request_id" in bound, (
        f"request_id contextvar missing on recommend_unexpected_error; got {bound!r}"
    )


# ---------------------------------------------------------------------------
# 7. 500 body shape produced by the Exception handler
# ---------------------------------------------------------------------------


def test_500_body_shape_on_unhandled_runtime_error() -> None:
    """A non-HTTP exception raised from a handler is caught by the registered
    ``Exception`` handler and rendered as a JSON envelope, NOT FastAPI's
    default plain-text ``Internal Server Error`` body."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = RuntimeError("boom")
    registry = ModelRegistry()
    registry.replace("demo", _loaded_entry(rec))
    client = TestClient(build_v1_app(registry), raise_server_exceptions=False)

    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 500
    assert r.json() == {"detail": "internal error", "code": "internal_error"}
    assert r.headers["Content-Type"].startswith("application/json")


# ---------------------------------------------------------------------------
# 8. X-Request-ID header coverage across every error status code
# ---------------------------------------------------------------------------


def test_x_request_id_present_on_every_error_status_code() -> None:
    """Every error response — 401, 404 (recipe-not-found / unknown-user),
    422, 503, and 500 — carries an ``X-Request-ID`` header.  Where the body
    also exposes a ``request_id`` field, the two values must match."""
    # 401 — missing API key
    plaintext = "any_valid_length_api_key_32_chrs"
    api_entry = _make_api_entry(plaintext)
    registry_with_auth = ModelRegistry()
    registry_with_auth.replace("demo", _loaded_entry())
    client_auth = TestClient(build_v1_app(registry_with_auth, api_keys=[api_entry]))

    r401 = client_auth.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r401.status_code == 401
    assert r401.headers.get("X-Request-ID"), "401 must carry X-Request-ID"

    # 404 RECIPE_NOT_FOUND
    client_loaded = _client_with(_loaded_entry())
    r404a = client_loaded.post("/v1/recipes/no_such:recommend", json={"user_id": "u1"})
    assert r404a.status_code == 404
    assert r404a.json()["code"] == "RECIPE_NOT_FOUND"
    assert r404a.headers.get("X-Request-ID")

    # 404 UNKNOWN_USER
    rec_ku = MagicMock()
    rec_ku.get_recommendation_for_known_user_id.side_effect = KeyError("u1")
    client_ku = _client_with(_loaded_entry(rec_ku))
    r404b = client_ku.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r404b.status_code == 404
    assert r404b.json()["code"] == "UNKNOWN_USER"
    assert r404b.headers.get("X-Request-ID")

    # 422 VALIDATION_ERROR (body has request_id)
    r422 = client_loaded.post(
        "/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 99999}
    )
    assert r422.status_code == 422
    assert r422.headers.get("X-Request-ID")
    assert r422.json()["request_id"] == r422.headers["X-Request-ID"]

    # 503 RECIPE_UNAVAILABLE
    client_stub = _client_with(_stub_entry("demo"))
    r503 = client_stub.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r503.status_code == 503
    assert r503.json()["code"] == "RECIPE_UNAVAILABLE"
    assert r503.headers.get("X-Request-ID")

    # 500 INTERNAL_ERROR
    rec_500 = MagicMock()
    rec_500.get_recommendation_for_known_user_id.side_effect = RuntimeError("boom")
    registry_500 = ModelRegistry()
    registry_500.replace("demo", _loaded_entry(rec_500))
    client_500 = TestClient(build_v1_app(registry_500), raise_server_exceptions=False)

    r500 = client_500.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r500.status_code == 500
    assert r500.headers.get("X-Request-ID"), "500 must carry X-Request-ID"


def test_x_request_id_echoed_from_client_on_error_responses() -> None:
    """When the client sends a valid X-Request-ID and the server returns an
    error (e.g. 503), the same ID is echoed on the response header AND in
    the body's ``request_id`` field where the body has one."""
    client = _client_with(_stub_entry("demo"))
    r = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1"},
        headers={"X-Request-ID": "client-abc"},
    )
    assert r.status_code == 503
    assert r.headers["X-Request-ID"] == "client-abc"
    # The 503 RECIPE_UNAVAILABLE body is flat and does not include
    # request_id, but the header must still echo the client value.
    body = r.json()
    if "request_id" in body:
        assert body["request_id"] == "client-abc"

    # Validate the same for a 500 path: the client-provided ID must survive
    # the path through the Exception handler.
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = RuntimeError("boom")
    registry = ModelRegistry()
    registry.replace("demo", _loaded_entry(rec))
    client_500 = TestClient(build_v1_app(registry), raise_server_exceptions=False)
    r500 = client_500.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1"},
        headers={"X-Request-ID": "client-abc"},
    )
    assert r500.status_code == 500
    assert r500.headers["X-Request-ID"] == "client-abc"


# ---------------------------------------------------------------------------
# 9. 422 request_id presence in body
# ---------------------------------------------------------------------------


def test_422_body_includes_request_id_matching_header() -> None:
    """422 validation responses must include ``request_id`` in the body so
    operators can correlate the body to log lines via the header."""
    client = _client_with(_loaded_entry())
    r = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1", "limit": 99999},
    )
    assert r.status_code == 422
    body = r.json()
    assert "request_id" in body, f"422 body missing request_id; got {body!r}"
    assert body["request_id"] == r.headers["X-Request-ID"]
    assert body["request_id"], "request_id must not be empty when middleware ran"


# ---------------------------------------------------------------------------
# 10. 405 Method Not Allowed
# ---------------------------------------------------------------------------


def test_405_method_not_allowed_on_get_only_endpoint() -> None:
    """POST against a GET-only endpoint (e.g. /v1/health) returns 405 with
    FastAPI's default flat body and a populated X-Request-ID header."""
    client = _client_with(_loaded_entry())
    r = client.post("/v1/health")
    assert r.status_code == 405
    assert r.json() == {"detail": "Method Not Allowed"}
    assert r.headers.get("X-Request-ID")


# ---------------------------------------------------------------------------
# 11. 404 from unknown route
# ---------------------------------------------------------------------------


def test_404_unknown_route_has_detail_and_request_id() -> None:
    """A GET against an unmounted path returns 404 with a flat body that
    has a ``detail`` key and an X-Request-ID header set by the middleware."""
    client = _client_with(_loaded_entry())
    r = client.get("/v1/nonexistent")
    assert r.status_code == 404
    body = r.json()
    assert "detail" in body, f"404 body missing detail; got {body!r}"
    assert r.headers.get("X-Request-ID")


# ---------------------------------------------------------------------------
# 12. 422 on path-parameter validation
# ---------------------------------------------------------------------------


def test_422_on_path_parameter_validation_no_metric_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sending a recipe name that fails the ``Path(pattern=...)`` regex
    returns 422 with the standard envelope.  The recipe name does not match
    ``_V1_VERB_PATH_RE`` (which only accepts ``[A-Za-z0-9_-]{1,64}``), so
    the validation_error metric is NOT recorded for this case — there is
    nothing to attribute it to."""
    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "1")

    from recotem.serving import metrics as _metrics

    monkeypatch.setattr(_metrics, "_V1_REQUEST_COUNTER", None)
    monkeypatch.setattr(_metrics, "_V1_REQUEST_LATENCY", None)
    monkeypatch.setattr(_metrics, "_V1_BATCH_SIZE", None)

    client = _client_with(_loaded_entry())
    # 'has spaces' contains a space and so does not match the {1,64} pattern.
    r = client.post("/v1/recipes/has spaces:recommend", json={"user_id": "u1"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert isinstance(body["errors"], list)
    assert body["errors"], "errors list must contain at least one entry"

    # Confirm no validation_error counter sample was recorded for this
    # request — the path does not match _V1_VERB_PATH_RE so the (recipe,
    # verb) tuple cannot be attributed and the metric is skipped.
    import prometheus_client

    output = prometheus_client.generate_latest().decode("utf-8")
    bad_re = re.compile(
        r"recotem_v1_requests_total\{"
        r'(?=.*recipe="has spaces")'
        r"[^}]*\}\s+[0-9.]+"
    )
    assert not bad_re.search(output), (
        f"Expected no validation_error metric for invalid path; got:\n{output}"
    )


# ---------------------------------------------------------------------------
# 13. HTTPException with non-standard dict detail falls back via
#     _DEFAULT_DETAIL_FOR.
# ---------------------------------------------------------------------------


def test_http_exception_dict_detail_without_detail_key_uses_fallback() -> None:
    """``HTTPException(detail={...})`` whose dict lacks a ``detail`` key
    must still produce a body with a string ``detail`` field, populated via
    the ``_DEFAULT_DETAIL_FOR`` table (or ``"Error"`` for unmapped status
    codes)."""
    from fastapi import FastAPI, Request
    from fastapi.exceptions import HTTPException
    from fastapi.responses import JSONResponse

    from recotem.serving.app import _DEFAULT_DETAIL_FOR

    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            content = dict(exc.detail)
            content.setdefault(
                "detail", _DEFAULT_DETAIL_FOR.get(exc.status_code, "Error")
            )
        else:
            content = {"detail": exc.detail}
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.get("/raises_418")
    def raises_418() -> None:
        raise HTTPException(status_code=418, detail={"foo": "bar"})

    @app.get("/raises_400")
    def raises_400() -> None:
        raise HTTPException(status_code=400, detail={"code": "BAD"})

    client = TestClient(app)

    # 418 — not in the default map, so falls back to "Error".
    r = client.get("/raises_418")
    assert r.status_code == 418
    assert r.json() == {"foo": "bar", "detail": "Error"}

    # 400 — in the map, fills in "Bad Request" because the dict omitted detail.
    r = client.get("/raises_400")
    assert r.status_code == 400
    assert r.json() == {"code": "BAD", "detail": "Bad Request"}


# ---------------------------------------------------------------------------
# 14. Contextvars cleanup on early raise (no leakage between requests).
# ---------------------------------------------------------------------------


def test_structlog_contextvars_do_not_leak_between_requests() -> None:
    """An auth failure (no X-API-Key) must not leak ``recipe`` / ``kid`` (or
    ``request_id``) into the contextvars of a subsequent request.  This
    verifies the middleware/route ``finally`` clauses are unbinding state
    correctly even when a request short-circuits on an early raise."""
    import structlog.contextvars

    plaintext = "any_valid_length_api_key_32_chrs"
    api_entry = _make_api_entry(plaintext)
    registry = ModelRegistry()
    registry.replace("demo", _loaded_entry())
    client = TestClient(build_v1_app(registry, api_keys=[api_entry]))

    # 1) Auth failure (missing X-API-Key) — raises BEFORE the route binds
    #    recipe/kid.  After the response is returned to the test client,
    #    contextvars in this test thread must be clean.
    r1 = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r1.status_code == 401

    ctx_after_401 = dict(structlog.contextvars.get_contextvars())
    assert "recipe" not in ctx_after_401, (
        f"recipe leaked after 401; got {ctx_after_401!r}"
    )
    assert "kid" not in ctx_after_401, f"kid leaked after 401; got {ctx_after_401!r}"

    # 2) Successful request — recipe/kid bind during the handler, then unbind
    #    in finally.  After the response, contextvars must again be clean.
    r2 = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1"},
        headers={"X-API-Key": plaintext},
    )
    assert r2.status_code == 200, r2.text

    ctx_after_200 = dict(structlog.contextvars.get_contextvars())
    assert "recipe" not in ctx_after_200, (
        f"recipe leaked after 200; got {ctx_after_200!r}"
    )
    assert "kid" not in ctx_after_200, f"kid leaked after 200; got {ctx_after_200!r}"


# ---------------------------------------------------------------------------
# 15. build_v1_app parity with create_app — exception_handlers coverage.
# ---------------------------------------------------------------------------


def test_build_v1_app_registers_all_three_exception_handlers() -> None:
    """``build_v1_app`` (used by the unit-test suite) must register the same
    three exception handlers as ``create_app`` in production: HTTPException,
    RequestValidationError, and the catch-all Exception.  Without the third,
    500 responses fall back to FastAPI's default plain-text body and tests
    silently diverge from production behaviour."""
    from fastapi.exceptions import HTTPException, RequestValidationError

    registry = ModelRegistry()
    registry.replace("demo", _loaded_entry())
    app = build_v1_app(registry)

    handlers = app.exception_handlers
    assert HTTPException in handlers, (
        f"HTTPException handler missing; got {list(handlers.keys())!r}"
    )
    assert RequestValidationError in handlers, (
        f"RequestValidationError handler missing; got {list(handlers.keys())!r}"
    )
    assert Exception in handlers, (
        f"Exception handler missing — 500s will fall back to plain text. "
        f"got {list(handlers.keys())!r}"
    )
