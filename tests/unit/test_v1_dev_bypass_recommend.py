# tests/unit/test_v1_dev_bypass_recommend.py
"""T3: Dev-bypass (insecure_no_auth=True) reaches v1 recommend verbs.

Scenario: build a router with insecure_no_auth=True and no api_keys, then
call :recommend, :recommend-related, and :batch-recommend without any
X-API-Key header and assert 200 with valid response bodies.

These tests cover the prediction-path bypass — existing dev-bypass tests
only exercised /v1/health/details.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from recotem.serving import metrics as _metrics
from recotem.serving.app import (
    _DEFAULT_DETAIL_FOR,
    _V1_VERB_PATH_RE,
    RequestIDMiddleware,
)
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.routes import make_router

_FAKE_SHA256_HEX = "d" * 64  # 64 lowercase hex chars


def _build_dev_app(registry: ModelRegistry) -> TestClient:
    """Build a FastAPI app with insecure_no_auth=True (empty api_keys)."""
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            content: dict[str, Any] = dict(exc.detail)
            content.setdefault(
                "detail", _DEFAULT_DETAIL_FOR.get(exc.status_code, "Error")
            )
        else:
            content = {"detail": exc.detail}
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def _val_err(request: Request, exc: RequestValidationError) -> JSONResponse:
        match = _V1_VERB_PATH_RE.match(request.url.path)
        if match is not None:
            _metrics.record_v1_request(
                recipe=match.group("name"),
                verb=match.group("verb"),
                status="validation_error",
                latency_seconds=0.0,
            )
        request_id = getattr(request.state, "request_id", "")
        sanitized = [
            {k: v for k, v in err.items() if k not in ("input", "ctx")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "request_id": request_id,
                "detail": "Request validation failed",
                "code": "VALIDATION_ERROR",
                "errors": sanitized,
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "")
        headers = {"X-Request-ID": request_id} if request_id else None
        return JSONResponse(
            status_code=500,
            content={"detail": "internal error", "code": "INTERNAL_ERROR"},
            headers=headers,
        )

    app.add_middleware(RequestIDMiddleware)

    # insecure_no_auth=True: api_keys is empty and bypass_mode becomes "insecure_no_auth"
    router = make_router(
        registry=registry,
        api_keys=[],
        insecure_no_auth=True,
    )
    app.include_router(router, prefix="/v1")
    return TestClient(app)


def _make_loaded_entry(name: str = "demo") -> ModelEntry:
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9), ("i2", 0.7)]
    rec._mapper = MagicMock()
    rec._mapper.user_id_to_index = {"u1": 0}
    rec._mapper.item_id_to_index = {"i1": 0, "i2": 1}
    rec.get_recommendation_for_new_user.return_value = [("i3", 0.8), ("i4", 0.6)]
    return ModelEntry(
        name=name,
        recommender=rec,
        header={},
        kid="active",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )


# ---------------------------------------------------------------------------
# T3.1 :recommend without X-API-Key returns 200
# ---------------------------------------------------------------------------


def test_dev_bypass_recommend_returns_200_without_api_key() -> None:
    """insecure_no_auth=True: :recommend succeeds without X-API-Key header."""
    registry = ModelRegistry()
    registry.replace("demo", _make_loaded_entry("demo"))
    client = _build_dev_app(registry)

    # No X-API-Key header
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 2})

    assert r.status_code == 200, (
        f"Dev bypass must allow :recommend without auth; got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["recipe"] == "demo"
    assert isinstance(body["items"], list) and len(body["items"]) > 0
    assert "model_version" in body
    assert "request_id" in body


# ---------------------------------------------------------------------------
# T3.2 :recommend-related without X-API-Key returns 200
# ---------------------------------------------------------------------------


def test_dev_bypass_recommend_related_returns_200_without_api_key() -> None:
    """insecure_no_auth=True: :recommend-related succeeds without X-API-Key header."""
    registry = ModelRegistry()
    registry.replace("demo", _make_loaded_entry("demo"))
    client = _build_dev_app(registry)

    r = client.post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": ["i1"], "limit": 2},
    )

    assert r.status_code == 200, (
        f"Dev bypass must allow :recommend-related without auth; got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["recipe"] == "demo"
    assert isinstance(body["items"], list)


# ---------------------------------------------------------------------------
# T3.3 :batch-recommend without X-API-Key returns 200
# ---------------------------------------------------------------------------


def test_dev_bypass_batch_recommend_returns_200_without_api_key() -> None:
    """insecure_no_auth=True: :batch-recommend succeeds without X-API-Key header."""
    registry = ModelRegistry()
    registry.replace("demo", _make_loaded_entry("demo"))
    client = _build_dev_app(registry)

    r = client.post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1", "limit": 1}]},
    )

    assert r.status_code == 200, (
        f"Dev bypass must allow :batch-recommend without auth; got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["recipe"] == "demo"
    assert isinstance(body["results"], list) and len(body["results"]) == 1
