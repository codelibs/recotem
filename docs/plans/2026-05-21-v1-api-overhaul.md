# recotem v1 API Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace recotem's alpha `POST /predict/{name}` HTTP surface with a versioned v1 API that exposes 4 inference verbs (single/batch × user/related) plus recipe discovery and lifted health/metrics endpoints, while preserving artifact format, signing, hot-swap, and `X-API-Key` auth.

**Architecture:** All routes live under `/v1`. Inference verbs use AIP-136 colon-suffix custom verbs (`/v1/recipes/{name}:recommend`, `:recommend-related`, `:batch-recommend`, `:batch-recommend-related`). Single endpoints reuse `RecommendResponse`; batch endpoints return per-element `BatchResultEntry` with `ok|error` status (HTTP 200 on partial failure, HTTP 503 only when the whole recipe is unavailable). The internal recommender delegates to `IDMappedRecommender.get_recommendation_for_known_user_id` (user) and `get_recommendation_for_new_user` (related). Schemas live in a new `serving/schemas.py` module; the legacy `routes.py::make_router` is deleted.

**Tech Stack:** FastAPI / Starlette, Pydantic v2, pytest, ruff, mypy, structlog, prometheus_client.

**Related Spec:** `docs/specs/2026-05-21-v1-api-overhaul-design.md`

---

## Notes for the implementer

- Work on a feature branch (e.g. `feat/v1-api`).
- Run `make test` (or `pytest -q`) between tasks; the project's pre-commit hook will run ruff & mypy on `git commit`.
- The plan uses TDD: write the failing test first, run it to verify it fails, implement, run again. Commit per task.
- After each test step, when this plan says "Expected: PASS", run the explicit command shown and verify exit code 0 and the test name(s) listed.
- All file paths are absolute or repo-relative from `/Users/shinsuke/workspace/recotem/`.
- The legacy `routes.py::_lookup_metadata` helper (lines 415-474) is preserved and reused as-is by the new v1 router.

---

## Task 1: Branch + baseline + colon-path POC

**Files:**
- Test: `tests/unit/test_v1_colon_path_poc.py` (Create — temporary; deleted in Task 13)

**Goal:** Confirm FastAPI/Starlette accept `:`-suffix paths and OpenAPI publishes them. If not, the plan switches to `/recommend` slash-form fallback (covered in §"Risks" of the spec) — but proceed only after verifying.

- [ ] **Step 1: Create a feature branch and run baseline tests**

```bash
cd /Users/shinsuke/workspace/recotem
git checkout -b feat/v1-api
pytest -q tests/unit tests/integration -x
```
Expected: green (current main is green per CI). Note any pre-existing skip/xfail counts so later regressions are visible.

- [ ] **Step 2: Write the colon-path POC test**

```python
# tests/unit/test_v1_colon_path_poc.py
"""Temporary POC: confirm FastAPI accepts AIP-136 colon-verb paths.

Deleted in Task 13 once the real v1 endpoints replace it.
"""

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


def test_colon_path_routes_and_appears_in_openapi():
    router = APIRouter()

    @router.post("/recipes/{name}:recommend")
    def _recommend(name: str) -> dict[str, str]:
        return {"name": name, "verb": "recommend"}

    @router.post("/recipes/{name}:recommend-related")
    def _related(name: str) -> dict[str, str]:
        return {"name": name, "verb": "recommend-related"}

    app = FastAPI()
    app.include_router(router, prefix="/v1")
    client = TestClient(app)

    r1 = client.post("/v1/recipes/demo:recommend")
    assert r1.status_code == 200
    assert r1.json() == {"name": "demo", "verb": "recommend"}

    r2 = client.post("/v1/recipes/demo:recommend-related")
    assert r2.status_code == 200
    assert r2.json() == {"name": "demo", "verb": "recommend-related"}

    spec = client.get("/openapi.json").json()
    assert "/v1/recipes/{name}:recommend" in spec["paths"]
    assert "/v1/recipes/{name}:recommend-related" in spec["paths"]
```

- [ ] **Step 3: Run the POC to verify FastAPI accepts colon paths**

```bash
pytest -q tests/unit/test_v1_colon_path_poc.py -v
```
Expected: PASS. If this fails, **stop** and re-plan with `/recommend` slash-form paths (update spec §8 first); do not proceed.

- [ ] **Step 4: Commit the POC**

```bash
git add tests/unit/test_v1_colon_path_poc.py
git commit -m "test: POC for AIP-136 colon-verb paths in FastAPI

Confirms /v1/recipes/{name}:recommend style paths route and appear
in OpenAPI before refactoring routes.py. Removed in Task 13."
```

---

## Task 2: New v1 schemas module

**Files:**
- Create: `src/recotem/serving/schemas.py`
- Test: `tests/unit/test_serving_schemas.py`

- [ ] **Step 1: Write the failing schema-validation tests**

```python
# tests/unit/test_serving_schemas.py
"""Unit tests for recotem.serving.schemas (v1)."""

import pytest
from pydantic import ValidationError

from recotem.serving.schemas import (
    BatchRecommendRelatedRequest,
    BatchRecommendRequest,
    BatchRecommendResponse,
    BatchResultEntry,
    ErrorDetail,
    RecommendItem,
    RecommendRelatedRequest,
    RecommendRequest,
    RecommendResponse,
    RecipeDetailResponse,
    RecipesListResponse,
    RecipeSummary,
)


def test_recommend_request_defaults_limit_10():
    req = RecommendRequest(user_id="u1")
    assert req.limit == 10
    assert req.exclude_items is None
    assert req.context is None


def test_recommend_request_rejects_empty_user_id():
    with pytest.raises(ValidationError):
        RecommendRequest(user_id="")


def test_recommend_request_limit_bounds():
    with pytest.raises(ValidationError):
        RecommendRequest(user_id="u1", limit=0)
    with pytest.raises(ValidationError):
        RecommendRequest(user_id="u1", limit=1001)


def test_recommend_related_request_requires_non_empty_seed():
    with pytest.raises(ValidationError):
        RecommendRelatedRequest(seed_items=[])


def test_recommend_related_request_caps_seed_at_100():
    RecommendRelatedRequest(seed_items=[f"i{i}" for i in range(100)])
    with pytest.raises(ValidationError):
        RecommendRelatedRequest(seed_items=[f"i{i}" for i in range(101)])


def test_recommend_item_allows_extra_metadata_fields():
    item = RecommendItem(item_id="i1", score=0.5, title="Hello")
    dumped = item.model_dump()
    assert dumped["title"] == "Hello"
    assert dumped["item_id"] == "i1"


def test_batch_recommend_request_requires_at_least_one():
    with pytest.raises(ValidationError):
        BatchRecommendRequest(requests=[])


def test_batch_recommend_request_caps_at_256():
    BatchRecommendRequest(requests=[RecommendRequest(user_id=f"u{i}") for i in range(256)])
    with pytest.raises(ValidationError):
        BatchRecommendRequest(requests=[RecommendRequest(user_id=f"u{i}") for i in range(257)])


def test_batch_recommend_related_request_caps_at_256():
    seeds = [RecommendRelatedRequest(seed_items=[f"i{i}"]) for i in range(256)]
    BatchRecommendRelatedRequest(requests=seeds)
    with pytest.raises(ValidationError):
        BatchRecommendRelatedRequest(requests=seeds + [seeds[0]])


def test_batch_result_entry_status_enum():
    BatchResultEntry(index=0, status="ok", items=[])
    BatchResultEntry(index=0, status="error", error=ErrorDetail(code="X", message="m"))
    with pytest.raises(ValidationError):
        BatchResultEntry(index=0, status="invalid")  # type: ignore[arg-type]


def test_recommend_response_round_trip():
    r = RecommendResponse(
        request_id="req_1",
        recipe="r",
        model_version="sha256:abc",
        items=[RecommendItem(item_id="i1", score=0.9)],
    )
    assert r.model_dump()["items"][0]["item_id"] == "i1"


def test_recipe_summary_supports_verb_list():
    s = RecipeSummary(
        name="r",
        model_version="sha256:abc",
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=["recommend", "recommend-related"],
        kind="user-item",
    )
    assert "recommend" in s.supported_verbs


def test_recipes_list_response_is_serialisable():
    s = RecipeSummary(
        name="r",
        model_version="v1",
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=[],
        kind="user-item",
    )
    payload = RecipesListResponse(recipes=[s]).model_dump()
    assert payload["recipes"][0]["name"] == "r"


def test_recipe_detail_response_includes_config_digest():
    d = RecipeDetailResponse(
        name="r",
        model_version="v1",
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=[],
        kind="user-item",
        config_digest="sha256:cfg",
        algorithms=["TopPop"],
        best_algorithm="TopPop",
    )
    assert d.config_digest == "sha256:cfg"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest -q tests/unit/test_serving_schemas.py
```
Expected: FAIL with `ModuleNotFoundError: No module named 'recotem.serving.schemas'`.

- [ ] **Step 3: Create the schemas module**

```python
# src/recotem/serving/schemas.py
"""Pydantic v2 request/response models for the recotem v1 HTTP API."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Single-request inputs
# ---------------------------------------------------------------------------

class RecommendRequest(BaseModel):
    user_id: Annotated[str, Field(min_length=1, max_length=256)]
    limit: Annotated[int, Field(ge=1, le=1000)] = 10
    exclude_items: Annotated[list[str] | None, Field(max_length=1000)] = None
    context: dict[str, Any] | None = None


class RecommendRelatedRequest(BaseModel):
    seed_items: Annotated[list[str], Field(min_length=1, max_length=100)]
    limit: Annotated[int, Field(ge=1, le=1000)] = 10
    exclude_items: Annotated[list[str] | None, Field(max_length=1000)] = None
    context: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Batch-request inputs
# ---------------------------------------------------------------------------

class BatchRecommendRequest(BaseModel):
    requests: Annotated[list[RecommendRequest], Field(min_length=1, max_length=256)]


class BatchRecommendRelatedRequest(BaseModel):
    requests: Annotated[list[RecommendRelatedRequest], Field(min_length=1, max_length=256)]


# ---------------------------------------------------------------------------
# Common response building blocks
# ---------------------------------------------------------------------------

class RecommendItem(BaseModel):
    item_id: str
    score: float
    # Extra metadata fields are passed through (join result from registry).
    model_config = ConfigDict(extra="allow")


class ErrorDetail(BaseModel):
    code: str
    message: str


class RecommendResponse(BaseModel):
    request_id: str
    recipe: str
    model_version: str
    items: list[RecommendItem]


class BatchResultEntry(BaseModel):
    index: int
    status: Literal["ok", "error"]
    items: list[RecommendItem] | None = None
    error: ErrorDetail | None = None


class BatchRecommendResponse(BaseModel):
    request_id: str
    recipe: str
    model_version: str
    results: list[BatchResultEntry]


# ---------------------------------------------------------------------------
# Recipe discovery
# ---------------------------------------------------------------------------

class RecipeSummary(BaseModel):
    name: str
    model_version: str
    loaded_at: str  # ISO-8601 UTC timestamp at last hot-swap
    supported_verbs: list[str]
    kind: str  # "user-item" | "item-item" | future kinds


class RecipesListResponse(BaseModel):
    recipes: list[RecipeSummary]


class RecipeDetailResponse(RecipeSummary):
    config_digest: str
    algorithms: list[str]
    best_algorithm: str
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest -q tests/unit/test_serving_schemas.py -v
```
Expected: PASS (all tests defined in Step 1).

- [ ] **Step 5: Commit**

```bash
git add src/recotem/serving/schemas.py tests/unit/test_serving_schemas.py
git commit -m "feat(serving): add v1 schema module (Pydantic v2)

Introduces RecommendRequest/Response, batch variants, RecipeSummary, etc.
Used by the upcoming v1 router. No behaviour change yet."
```

---

## Task 3: Extend ModelRegistry / ModelEntry with v1 metadata

**Files:**
- Modify: `src/recotem/serving/registry.py`
- Test: `tests/unit/test_serving_registry.py` (add cases)

**Goal:** Add `supported_verbs`, `kind`, `model_version`, `loaded_at` accessors so the v1 router can emit them without touching artifact internals.

- [ ] **Step 1: Write the failing registry tests**

Add to the end of `tests/unit/test_serving_registry.py` (verify file exists; if so, append):

```python
# Append to tests/unit/test_serving_registry.py

from datetime import datetime, timezone

import pytest

from recotem.serving.registry import ModelEntry


def test_model_entry_supported_verbs_default_for_user_item_kind():
    e = _stub_entry()  # helper defined elsewhere in this test module
    assert "recommend" in e.supported_verbs
    assert "recommend-related" in e.supported_verbs
    assert "batch-recommend" in e.supported_verbs
    assert "batch-recommend-related" in e.supported_verbs


def test_model_entry_kind_defaults_to_user_item():
    e = _stub_entry()
    assert e.kind == "user-item"


def test_model_entry_model_version_sha256_prefixed():
    e = _stub_entry()
    assert e.model_version.startswith("sha256:")
    assert len(e.model_version) > len("sha256:")  # not empty hex


def test_model_entry_loaded_at_iso8601_utc():
    e = _stub_entry()
    # Must round-trip parse with Python's fromisoformat (3.11+ supports trailing 'Z')
    parsed = datetime.fromisoformat(e.loaded_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
```

Replace `_stub_entry()` with the existing helper in the test module (look up the current helper at the top of `tests/unit/test_serving_registry.py` and reuse it).

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest -q tests/unit/test_serving_registry.py -v -k "supported_verbs or kind or model_version or loaded_at"
```
Expected: FAIL (`AttributeError: 'ModelEntry' object has no attribute 'supported_verbs'` etc.).

- [ ] **Step 3: Extend ModelEntry**

`ModelEntry` already stores the artifact SHA-256 in `_loaded_marker: tuple[Any, str]` (the second element, populated by the watcher). Use that — do **not** add a parallel field. The only new dataclass field is `loaded_at_unix`.

In `src/recotem/serving/registry.py`, inside `class ModelEntry`:

After `_loaded_marker: tuple[Any, str] = field(...)` (around line 83), add:

```python
    # v1 additions. The watcher sets loaded_at_unix on every successful
    # (re-)load.  Stays at 0.0 for stub entries that never loaded.
    loaded_at_unix: float = 0.0
    # Optional artifact-derived metadata used by /v1/recipes/{name}.
    config_digest: str = ""
    algorithms: list[str] = field(default_factory=list)
```

Then, before `__post_init__`, add the four v1 properties:

```python
    # --- v1 API additions ---
    @property
    def artifact_sha256(self) -> str:
        """SHA-256 of the artifact bytes (hex, no prefix).

        Derived from ``_loaded_marker[1]`` which the watcher populates
        at every successful (re-)load.  Empty for stub entries.
        """
        return self._loaded_marker[1] if self._loaded_marker else ""

    @property
    def model_version(self) -> str:
        """Deterministic artifact identifier exposed via the v1 API.

        Format: ``sha256:<hex>``.  Stub entries return ``sha256:``.
        """
        return f"sha256:{self.artifact_sha256}"

    @property
    def loaded_at(self) -> str:
        """ISO-8601 UTC timestamp of the last successful (re-)load.

        Falls back to the unix epoch for stub entries.
        """
        from datetime import datetime, timezone

        return (
            datetime.fromtimestamp(self.loaded_at_unix or 0.0, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )

    @property
    def kind(self) -> str:
        """Inference kind exposed via /v1/recipes.

        Currently every irspack algorithm shipped by recotem is a
        user-item collaborative filter, so this returns "user-item"
        unconditionally.
        """
        return "user-item"

    @property
    def supported_verbs(self) -> list[str]:
        """List of v1 verbs this entry can serve."""
        if self.kind == "user-item":
            return [
                "recommend",
                "recommend-related",
                "batch-recommend",
                "batch-recommend-related",
            ]
        return []
```

- [ ] **Step 4: Wire watcher to populate `loaded_at_unix`**

Open `src/recotem/serving/watcher.py` and locate every `ModelEntry(` call site that represents a successful load (search with `grep -n 'ModelEntry(' src/recotem/serving/watcher.py`). For each successful-load construction, add `loaded_at_unix=_time.time()`:

```python
import time as _time

# At each successful-load ModelEntry(...) call:
ModelEntry(
    name=name,
    recommender=recommender,
    header=header,
    kid=kid,
    metadata_df=meta_df,
    metadata_index=meta_index,
    artifact_path=artifact_path,
    loaded=True,
    _loaded_marker=(marker_token, sha256_hex),  # existing
    loaded_at_unix=_time.time(),                # new
    config_digest=header.get("config_digest", ""),
    algorithms=header.get("algorithms", []) or [],
)
```

The artifact SHA-256 hex is already passed as `_loaded_marker[1]` — no new computation needed. `header.get("config_digest", "")` and `header.get("algorithms", [])` are best-effort: if the artifact header does not yet include these keys, both fall back to empty values and Task 11's test still passes (it only asserts key presence).

- [ ] **Step 5: Run the tests**

```bash
pytest -q tests/unit/test_serving_registry.py tests/unit/test_serving_watcher.py -v
```
Expected: PASS (the new cases plus all existing watcher tests).

- [ ] **Step 6: Commit**

```bash
git add src/recotem/serving/registry.py src/recotem/serving/watcher.py tests/unit/test_serving_registry.py
git commit -m "feat(serving): expose model_version, loaded_at, kind, supported_verbs

ModelEntry now carries the artifact SHA-256 and load timestamp so the
upcoming v1 /recipes endpoint can publish them without re-reading
artifact files. kind/supported_verbs default to user-item with all
four inference verbs."
```

---

## Task 4: Extend metrics with verb label and batch_size histogram

**Files:**
- Modify: `src/recotem/serving/metrics.py`
- Test: `tests/unit/test_serving_metrics.py`

- [ ] **Step 1: Write the failing metrics tests**

Append to `tests/unit/test_serving_metrics.py`:

```python
# Append to tests/unit/test_serving_metrics.py

from recotem.serving import metrics as _m


def test_record_v1_request_accepts_verb_label(reset_metrics_registry):
    _m.record_v1_request("smartstocknotes", "recommend", "ok", 0.012)
    _m.record_v1_request("smartstocknotes", "recommend-related", "unknown_seed_items", 0.005)
    out, _ = _m.generate_latest()
    text = out.decode()
    assert 'verb="recommend"' in text
    assert 'verb="recommend-related"' in text
    assert 'status="unknown_seed_items"' in text


def test_observe_batch_size_records_histogram(reset_metrics_registry):
    _m.observe_batch_size("smartstocknotes", "batch-recommend", 7)
    out, _ = _m.generate_latest()
    text = out.decode()
    assert "recotem_v1_batch_size_bucket" in text
```

The `reset_metrics_registry` fixture should already exist in `tests/unit/test_serving_metrics.py` (if not, copy the fixture from existing tests that reset the Prometheus default registry between runs).

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest -q tests/unit/test_serving_metrics.py -v -k "record_v1_request or observe_batch_size"
```
Expected: FAIL with `AttributeError: module 'recotem.serving.metrics' has no attribute 'record_v1_request'`.

- [ ] **Step 3: Add the new metric functions**

In `src/recotem/serving/metrics.py`, after the existing `record_predict` function (around line 141), append:

```python
# ---------------------------------------------------------------------------
# v1 API metrics
# ---------------------------------------------------------------------------

_V1_REQUEST_COUNTER: Any = None
_V1_REQUEST_LATENCY: Any = None
_V1_BATCH_SIZE: Any = None


def _ensure_v1_initialized() -> None:
    """Lazily create the v1 counter/histogram families.

    Called from record_v1_request and observe_batch_size.  Mirrors the
    pattern used by _ensure_initialized() for the legacy metrics.
    """
    global _V1_REQUEST_COUNTER, _V1_REQUEST_LATENCY, _V1_BATCH_SIZE
    if _V1_REQUEST_COUNTER is not None:
        return
    if not metrics_enabled():
        return
    from prometheus_client import Counter, Histogram

    _V1_REQUEST_COUNTER = Counter(
        "recotem_v1_requests_total",
        "Total number of v1 API requests by recipe, verb, and status.",
        ["recipe", "verb", "status"],
    )
    _V1_REQUEST_LATENCY = Histogram(
        "recotem_v1_request_latency_seconds",
        "End-to-end latency of v1 API requests.",
        ["recipe", "verb"],
    )
    _V1_BATCH_SIZE = Histogram(
        "recotem_v1_batch_size",
        "Number of elements in a batch v1 request.",
        ["recipe", "verb"],
        buckets=(1, 2, 4, 8, 16, 32, 64, 128, 256),
    )


def record_v1_request(
    recipe: str, verb: str, status: str, latency_seconds: float
) -> None:
    """Record a v1 API request.

    *verb* ∈ {"recommend", "recommend-related", "batch-recommend",
    "batch-recommend-related"}.  *status* ∈ {"ok", "unknown_user",
    "unknown_seed_items", "unavailable", "validation_error", "error"}.
    """
    _ensure_v1_initialized()
    if _V1_REQUEST_COUNTER is None:
        return  # metrics disabled
    _V1_REQUEST_COUNTER.labels(recipe=recipe, verb=verb, status=status).inc()
    _V1_REQUEST_LATENCY.labels(recipe=recipe, verb=verb).observe(latency_seconds)


def observe_batch_size(recipe: str, verb: str, size: int) -> None:
    """Record a sample for the batch-size histogram."""
    _ensure_v1_initialized()
    if _V1_BATCH_SIZE is None:
        return
    _V1_BATCH_SIZE.labels(recipe=recipe, verb=verb).observe(size)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest -q tests/unit/test_serving_metrics.py -v
```
Expected: PASS (including pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/recotem/serving/metrics.py tests/unit/test_serving_metrics.py
git commit -m "feat(serving): add v1 request counter + latency + batch_size metrics

Introduces recotem_v1_requests_total{recipe,verb,status},
recotem_v1_request_latency_seconds{recipe,verb}, and
recotem_v1_batch_size{recipe,verb} histograms. Legacy
record_predict() remains untouched and will be removed in Task 12."
```

---

## Task 5: New v1 router module (skeleton)

**Files:**
- Create: `src/recotem/serving/v1_router.py`
- Test: `tests/unit/test_v1_router_basics.py`

**Goal:** A `make_v1_router(registry, api_keys, metadata_field_deny=None) -> APIRouter` factory mirroring the legacy `make_router` shape, but exposing zero routes yet — just the dependency wiring and skeleton. Subsequent tasks add the actual endpoints.

- [ ] **Step 1: Write the failing skeleton test**

```python
# tests/unit/test_v1_router_basics.py
"""v1 router skeleton tests.

Confirms the factory wires auth and registry without exposing
any routes other than the four health/discovery endpoints to be
added in Tasks 6 and 11.  Inference verbs are added in Tasks 7-10.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelRegistry
from recotem.serving.v1_router import make_v1_router


def test_make_v1_router_returns_routable_apiroute_factory():
    registry = ModelRegistry()
    router = make_v1_router(registry=registry, api_keys=[])
    app = FastAPI()
    app.include_router(router, prefix="/v1")

    client = TestClient(app)
    # The skeleton has no inference routes yet — but an unknown path
    # returns 404, confirming the router is mounted at /v1.
    r = client.post("/v1/recipes/x:recommend")
    assert r.status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest -q tests/unit/test_v1_router_basics.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'recotem.serving.v1_router'`.

- [ ] **Step 3: Create the skeleton module**

```python
# src/recotem/serving/v1_router.py
"""FastAPI router for the recotem v1 HTTP API.

This module replaces the legacy `routes.py::make_router` after Task 12.
Routes are added incrementally across Tasks 6-11.
"""

from __future__ import annotations

import re

import structlog
from fastapi import APIRouter, Request

from recotem.config import ApiKeyEntry
from recotem.serving.auth import verify_api_key
from recotem.serving.registry import ModelRegistry

logger = structlog.get_logger(__name__)

# Allowed characters for the X-Request-ID echo (preserved from routes.py).
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def make_v1_router(
    registry: ModelRegistry,
    api_keys: list[ApiKeyEntry],
    metadata_field_deny: list[str] | None = None,
) -> APIRouter:
    """Build and return the v1 API router (mounted under `/v1`)."""
    router = APIRouter()
    _deny_set: frozenset[str] = frozenset(
        s.lower() for s in (metadata_field_deny or [])
    )

    def _require_auth(request: Request) -> str:
        return verify_api_key(request, api_keys)

    # Endpoints are appended in subsequent tasks.  Keep the closure
    # variables (`registry`, `_deny_set`, `_require_auth`) live for them.
    # Suppress unused-warning by exposing on the router (no-op).
    router.dependency_overrides_provider = None
    _ = registry, _deny_set, _require_auth
    return router
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest -q tests/unit/test_v1_router_basics.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/recotem/serving/v1_router.py tests/unit/test_v1_router_basics.py
git commit -m "feat(serving): skeleton v1_router factory

Mirrors the legacy make_router signature so app.py can swap routers
in Task 12. Inference endpoints land in Tasks 7-10."
```

---

## Task 6: `/v1/health`, `/v1/health/details`, `/v1/metrics`

**Files:**
- Modify: `src/recotem/serving/v1_router.py`
- Test: `tests/unit/test_v1_health_metrics.py`

**Goal:** Port the existing `/health`, `/health/details`, `/metrics` handlers into the v1 router unchanged in behaviour but mounted under `/v1`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_v1_health_metrics.py
"""Verify /v1/health, /v1/health/details, and /v1/metrics behave like
their legacy counterparts but mounted under /v1.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelRegistry
from recotem.serving.v1_router import make_v1_router


def _client(monkeypatch=None) -> TestClient:
    registry = ModelRegistry()
    router = make_v1_router(registry=registry, api_keys=[])
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    return TestClient(app)


def test_health_returns_ok_with_empty_registry():
    r = _client().get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["total"] == 0
    assert body["loaded"] == 0


def test_health_details_requires_auth():
    r = _client().get("/v1/health/details")
    assert r.status_code == 401
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest -q tests/unit/test_v1_health_metrics.py -v
```
Expected: FAIL with `404` on `/v1/health`.

- [ ] **Step 3: Add the handlers**

In `src/recotem/serving/v1_router.py`, inside `make_v1_router` (just before the trailing `return router`), append:

```python
    from typing import Any

    from fastapi import Depends, Response

    from recotem.serving import metrics as _metrics

    @router.get("/health", summary="Overall health status (probe-safe)")
    def health(response: Response) -> dict[str, Any]:
        snapshot = registry.health_snapshot()
        total = len(snapshot)
        loaded_count = sum(
            1
            for entry_health in snapshot.values()
            if entry_health.get("loaded", False) and not entry_health.get("error")
        )
        overall = (
            "ok"
            if (loaded_count == total and total > 0 or total == 0)
            else "degraded"
        )
        for entry_health in snapshot.values():
            if not entry_health.get("loaded", True) or entry_health.get("error"):
                overall = "degraded"
                break
        if overall == "degraded":
            response.status_code = 503
        return {"status": overall, "total": total, "loaded": loaded_count}

    @router.get(
        "/health/details",
        summary="Per-recipe health detail (authenticated)",
    )
    def health_details(
        response: Response,
        kid: str = Depends(_require_auth),
    ) -> dict[str, Any]:
        snapshot = registry.health_snapshot()
        overall = "ok"
        for entry_health in snapshot.values():
            if not entry_health.get("loaded", True) or entry_health.get("error"):
                overall = "degraded"
                break
        if overall == "degraded":
            response.status_code = 503
        return {"status": overall, "recipes": snapshot}

    if _metrics.metrics_enabled():

        @router.get(
            "/metrics",
            summary="Prometheus metrics",
            include_in_schema=False,
        )
        def metrics_endpoint() -> Any:
            data, content_type = _metrics.generate_latest()
            return Response(content=data, media_type=content_type)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest -q tests/unit/test_v1_health_metrics.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/recotem/serving/v1_router.py tests/unit/test_v1_health_metrics.py
git commit -m "feat(serving): port health + metrics into v1_router"
```

---

## Task 7: `POST /v1/recipes/{name}:recommend`

**Files:**
- Modify: `src/recotem/serving/v1_router.py`
- Test: `tests/unit/test_v1_recommend.py`

**Goal:** First inference endpoint. user→items single. Behaviour parallels the legacy `/predict/{name}` but emits the new response shape (`RecommendResponse`) with `model_version`/`recipe` instead of `model: {recipe, kid, trained_at, best_class}`. The legacy `kid`/`trained_at`/`best_class` move to `GET /v1/recipes/{name}` (Task 11).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_v1_recommend.py
"""POST /v1/recipes/{name}:recommend — single user→items."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.v1_router import make_v1_router


def _entry_with_recommender(recommender) -> ModelEntry:
    """Build a loaded ModelEntry around the given recommender mock.

    ModelEntry already stores the artifact SHA-256 in
    `_loaded_marker[1]`; we set it through that field rather than
    a parallel attribute.
    """
    return ModelEntry(
        name="demo",
        recommender=recommender,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc123"),
        loaded_at_unix=1747800000.0,
    )


def _app_with_entry(entry: ModelEntry) -> TestClient:
    registry = ModelRegistry()
    registry.replace("demo", entry)
    app = FastAPI()
    app.include_router(
        make_v1_router(registry=registry, api_keys=[]),
        prefix="/v1",
    )
    return TestClient(app)


def test_recommend_returns_items_and_envelope():
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [
        ("i1", 0.9), ("i2", 0.5)
    ]
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recipe"] == "demo"
    assert body["model_version"] == "sha256:abc123"
    assert [i["item_id"] for i in body["items"]] == ["i1", "i2"]
    assert "request_id" in body
    rec.get_recommendation_for_known_user_id.assert_called_once_with("u1", 2)


def test_recommend_404_when_user_unknown():
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = KeyError("u1")
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 404
    body = r.json()
    assert body["detail"]["code"] == "UNKNOWN_USER"


def test_recommend_503_when_recipe_not_loaded():
    stub = ModelEntry(
        name="demo", recommender=None, header={}, kid="", loaded=False,
    )
    client = _app_with_entry(stub)
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "RECIPE_UNAVAILABLE"


def test_recommend_422_on_empty_user_id():
    rec = MagicMock()
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "", "limit": 5})
    assert r.status_code == 422


def test_recommend_404_when_recipe_missing():
    rec = MagicMock()
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/unknown:recommend", json={"user_id": "u1"})
    # Missing recipe name yields 503 (unavailable), matching the
    # spec's RECIPE_UNAVAILABLE for "no entry" cases — keep behaviour
    # parallel with the legacy /predict endpoint.
    assert r.status_code == 503
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest -q tests/unit/test_v1_recommend.py -v
```
Expected: all 5 FAIL with `404 Not Found` on the route.

- [ ] **Step 3: Add the `:recommend` handler**

In `v1_router.py`, append the following handler block inside `make_v1_router` (before `return router`):

```python
    import time
    import uuid
    from typing import Annotated, Any

    from fastapi import HTTPException, Path, Request
    from fastapi.responses import JSONResponse

    from recotem.serving import metrics as _metrics
    from recotem.serving.routes import _lookup_metadata  # reuse legacy helper
    from recotem.serving.schemas import (
        RecommendItem,
        RecommendRequest,
        RecommendResponse,
    )

    @router.post(
        "/recipes/{name}:recommend",
        response_model=RecommendResponse,
        summary="Recommend items for a single user",
    )
    def recommend(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        body: RecommendRequest,
        request: Request,
        kid: str = Depends(_require_auth),
    ) -> Any:
        raw_rid = request.headers.get("x-request-id", "")
        request_id = (
            raw_rid if _REQUEST_ID_RE.match(raw_rid) else str(uuid.uuid4())
        )
        start = time.monotonic()
        status = "error"
        verb = "recommend"

        try:
            entry = registry.get(name)
            if entry is None or not entry.loaded or entry.recommender is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is not loaded or unhealthy",
                        "code": "RECIPE_UNAVAILABLE",
                    },
                )

            try:
                raw_results: list[tuple[str, float]] = (
                    entry.recommender.get_recommendation_for_known_user_id(
                        body.user_id, body.limit
                    )
                )
            except KeyError:
                status = "unknown_user"
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": (
                            f"User '{body.user_id}' was not seen during training"
                        ),
                        "code": "UNKNOWN_USER",
                    },
                ) from None

            # Build items (with optional metadata join from existing
            # registry entry — reuse the legacy helper).
            items: list[dict[str, Any]] = []
            meta_index = entry.metadata_index
            meta_df = entry.metadata_df if meta_index is None else None
            for item_id, score in raw_results:
                fields: dict[str, Any] = {}
                if meta_index is not None:
                    fields.update(meta_index.get(item_id, {}))
                elif meta_df is not None:
                    fields.update(
                        _lookup_metadata(meta_df, item_id, _deny_set, name)
                    )
                fields["item_id"] = item_id
                fields["score"] = float(score)
                items.append(fields)

            status = "ok"
            return JSONResponse(
                content={
                    "request_id": request_id,
                    "recipe": name,
                    "model_version": entry.model_version,
                    "items": items,
                },
                headers={
                    "X-Request-ID": request_id,
                    "X-Recotem-Model-Version": entry.model_version,
                },
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "v1_recommend_unexpected_error",
                name=name,
                request_id=request_id,
                kid=kid,
            )
            raise
        finally:
            _metrics.record_v1_request(name, verb, status, time.monotonic() - start)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest -q tests/unit/test_v1_recommend.py -v
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/recotem/serving/v1_router.py tests/unit/test_v1_recommend.py
git commit -m "feat(serving): POST /v1/recipes/{name}:recommend"
```

---

## Task 8: `POST /v1/recipes/{name}:recommend-related`

**Files:**
- Modify: `src/recotem/serving/v1_router.py`
- Test: `tests/unit/test_v1_recommend_related.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_v1_recommend_related.py
"""POST /v1/recipes/{name}:recommend-related — single items→items."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.v1_router import make_v1_router


def _client_with_recommender(rec) -> TestClient:
    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc123"),
        loaded_at_unix=1747800000.0,
    )
    registry = ModelRegistry()
    registry.replace("demo", entry)
    app = FastAPI()
    app.include_router(make_v1_router(registry, []), prefix="/v1")
    return TestClient(app)


def test_related_returns_items():
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = [("i9", 0.7), ("i8", 0.6)]
    r = _client_with_recommender(rec).post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": ["7203"], "limit": 5},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["item_id"] for i in body["items"]] == ["i9", "i8"]
    rec.get_recommendation_for_new_user.assert_called_once_with(["7203"], 5)


def test_related_422_on_empty_seed_items():
    rec = MagicMock()
    r = _client_with_recommender(rec).post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": []},
    )
    assert r.status_code == 422


def test_related_404_when_all_seeds_unknown_returns_empty():
    rec = MagicMock()
    rec.get_recommendation_for_new_user.return_value = []
    r = _client_with_recommender(rec).post(
        "/v1/recipes/demo:recommend-related",
        json={"seed_items": ["zzz"]},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "UNKNOWN_SEED_ITEMS"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest -q tests/unit/test_v1_recommend_related.py -v
```
Expected: FAIL with 404 on route (handler not implemented).

- [ ] **Step 3: Add the `:recommend-related` handler**

Append inside `make_v1_router` (after the `:recommend` handler):

```python
    from recotem.serving.schemas import RecommendRelatedRequest

    @router.post(
        "/recipes/{name}:recommend-related",
        response_model=RecommendResponse,
        summary="Recommend items related to a seed list",
    )
    def recommend_related(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        body: RecommendRelatedRequest,
        request: Request,
        kid: str = Depends(_require_auth),
    ) -> Any:
        raw_rid = request.headers.get("x-request-id", "")
        request_id = (
            raw_rid if _REQUEST_ID_RE.match(raw_rid) else str(uuid.uuid4())
        )
        start = time.monotonic()
        status = "error"
        verb = "recommend-related"

        try:
            entry = registry.get(name)
            if entry is None or not entry.loaded or entry.recommender is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is not loaded or unhealthy",
                        "code": "RECIPE_UNAVAILABLE",
                    },
                )

            raw_results = entry.recommender.get_recommendation_for_new_user(
                body.seed_items, body.limit
            )

            if not raw_results:
                status = "unknown_seed_items"
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": (
                            f"None of the seed_items {body.seed_items!r} "
                            "were known to the model"
                        ),
                        "code": "UNKNOWN_SEED_ITEMS",
                    },
                )

            items: list[dict[str, Any]] = []
            meta_index = entry.metadata_index
            meta_df = entry.metadata_df if meta_index is None else None
            for item_id, score in raw_results:
                fields: dict[str, Any] = {}
                if meta_index is not None:
                    fields.update(meta_index.get(item_id, {}))
                elif meta_df is not None:
                    fields.update(
                        _lookup_metadata(meta_df, item_id, _deny_set, name)
                    )
                fields["item_id"] = item_id
                fields["score"] = float(score)
                items.append(fields)

            status = "ok"
            return JSONResponse(
                content={
                    "request_id": request_id,
                    "recipe": name,
                    "model_version": entry.model_version,
                    "items": items,
                },
                headers={
                    "X-Request-ID": request_id,
                    "X-Recotem-Model-Version": entry.model_version,
                },
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "v1_recommend_related_unexpected_error",
                name=name,
                request_id=request_id,
                kid=kid,
            )
            raise
        finally:
            _metrics.record_v1_request(name, verb, status, time.monotonic() - start)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest -q tests/unit/test_v1_recommend_related.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/recotem/serving/v1_router.py tests/unit/test_v1_recommend_related.py
git commit -m "feat(serving): POST /v1/recipes/{name}:recommend-related"
```

---

## Task 9: `POST /v1/recipes/{name}:batch-recommend`

**Files:**
- Modify: `src/recotem/serving/v1_router.py`
- Test: `tests/unit/test_v1_batch_recommend.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_v1_batch_recommend.py
"""POST /v1/recipes/{name}:batch-recommend — multi-user bulk."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.v1_router import make_v1_router


def _client(rec) -> TestClient:
    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="t",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc"),
        loaded_at_unix=1.0,
    )
    registry = ModelRegistry()
    registry.replace("demo", entry)
    app = FastAPI()
    app.include_router(make_v1_router(registry, []), prefix="/v1")
    return TestClient(app)


def test_batch_recommend_mixed_success_and_failure():
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = [
        [("i1", 0.9)],
        KeyError("u2"),
        [("i3", 0.5)],
    ]
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={
            "requests": [
                {"user_id": "u1"},
                {"user_id": "u2"},
                {"user_id": "u3"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recipe"] == "demo"
    assert len(body["results"]) == 3
    assert body["results"][0] == {
        "index": 0, "status": "ok",
        "items": [{"item_id": "i1", "score": 0.9}],
        "error": None,
    }
    assert body["results"][1]["status"] == "error"
    assert body["results"][1]["error"]["code"] == "UNKNOWN_USER"
    assert body["results"][2]["status"] == "ok"


def test_batch_recommend_503_when_recipe_unavailable():
    rec = MagicMock()
    stub = ModelEntry(
        name="demo", recommender=None, header={}, kid="", loaded=False,
    )
    registry = ModelRegistry()
    registry.replace("demo", stub)
    app = FastAPI()
    app.include_router(make_v1_router(registry, []), prefix="/v1")
    client = TestClient(app)
    r = client.post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": "u1"}]},
    )
    assert r.status_code == 503


def test_batch_recommend_422_on_too_many_requests():
    rec = MagicMock()
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend",
        json={"requests": [{"user_id": f"u{i}"} for i in range(257)]},
    )
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest -q tests/unit/test_v1_batch_recommend.py -v
```
Expected: FAIL with 404 (handler not implemented).

- [ ] **Step 3: Add the `:batch-recommend` handler**

Append inside `make_v1_router`:

```python
    from recotem.serving.schemas import (
        BatchRecommendRequest,
        BatchRecommendResponse,
    )

    @router.post(
        "/recipes/{name}:batch-recommend",
        response_model=BatchRecommendResponse,
        summary="Recommend items for multiple users",
    )
    def batch_recommend(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        body: BatchRecommendRequest,
        request: Request,
        kid: str = Depends(_require_auth),
    ) -> Any:
        raw_rid = request.headers.get("x-request-id", "")
        request_id = (
            raw_rid if _REQUEST_ID_RE.match(raw_rid) else str(uuid.uuid4())
        )
        start = time.monotonic()
        status = "error"
        verb = "batch-recommend"

        try:
            entry = registry.get(name)
            if entry is None or not entry.loaded or entry.recommender is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is not loaded or unhealthy",
                        "code": "RECIPE_UNAVAILABLE",
                    },
                )

            _metrics.observe_batch_size(name, verb, len(body.requests))

            results: list[dict[str, Any]] = []
            for idx, single in enumerate(body.requests):
                try:
                    raw = entry.recommender.get_recommendation_for_known_user_id(
                        single.user_id, single.limit
                    )
                    items = [
                        {"item_id": item_id, "score": float(score)}
                        for item_id, score in raw
                    ]
                    results.append({
                        "index": idx,
                        "status": "ok",
                        "items": items,
                        "error": None,
                    })
                except KeyError:
                    results.append({
                        "index": idx,
                        "status": "error",
                        "items": None,
                        "error": {
                            "code": "UNKNOWN_USER",
                            "message": (
                                f"User '{single.user_id}' "
                                "was not seen during training"
                            ),
                        },
                    })

            status = "ok"
            return JSONResponse(
                content={
                    "request_id": request_id,
                    "recipe": name,
                    "model_version": entry.model_version,
                    "results": results,
                },
                headers={
                    "X-Request-ID": request_id,
                    "X-Recotem-Model-Version": entry.model_version,
                },
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "v1_batch_recommend_unexpected_error",
                name=name,
                request_id=request_id,
                kid=kid,
            )
            raise
        finally:
            _metrics.record_v1_request(name, verb, status, time.monotonic() - start)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest -q tests/unit/test_v1_batch_recommend.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/recotem/serving/v1_router.py tests/unit/test_v1_batch_recommend.py
git commit -m "feat(serving): POST /v1/recipes/{name}:batch-recommend"
```

---

## Task 10: `POST /v1/recipes/{name}:batch-recommend-related`

**Files:**
- Modify: `src/recotem/serving/v1_router.py`
- Test: `tests/unit/test_v1_batch_recommend_related.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_v1_batch_recommend_related.py
"""POST /v1/recipes/{name}:batch-recommend-related — multi-seed bulk."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.v1_router import make_v1_router


def _client(rec) -> TestClient:
    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="t",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc"),
        loaded_at_unix=1.0,
    )
    registry = ModelRegistry()
    registry.replace("demo", entry)
    app = FastAPI()
    app.include_router(make_v1_router(registry, []), prefix="/v1")
    return TestClient(app)


def test_batch_related_mixed_success_and_failure():
    rec = MagicMock()
    rec.get_recommendation_for_new_user.side_effect = [
        [("i9", 0.7)],
        [],  # all-unknown seeds
        [("i3", 0.5)],
    ]
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={
            "requests": [
                {"seed_items": ["7203"]},
                {"seed_items": ["zzz"]},
                {"seed_items": ["9984"]},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["status"] for e in body["results"]] == ["ok", "error", "ok"]
    assert body["results"][1]["error"]["code"] == "UNKNOWN_SEED_ITEMS"


def test_batch_related_422_on_empty_seed_in_one_entry():
    rec = MagicMock()
    r = _client(rec).post(
        "/v1/recipes/demo:batch-recommend-related",
        json={
            "requests": [{"seed_items": []}],
        },
    )
    assert r.status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest -q tests/unit/test_v1_batch_recommend_related.py -v
```
Expected: FAIL with 404.

- [ ] **Step 3: Add the `:batch-recommend-related` handler**

Append inside `make_v1_router`:

```python
    from recotem.serving.schemas import BatchRecommendRelatedRequest

    @router.post(
        "/recipes/{name}:batch-recommend-related",
        response_model=BatchRecommendResponse,
        summary="Recommend items related to multiple seed lists",
    )
    def batch_recommend_related(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        body: BatchRecommendRelatedRequest,
        request: Request,
        kid: str = Depends(_require_auth),
    ) -> Any:
        raw_rid = request.headers.get("x-request-id", "")
        request_id = (
            raw_rid if _REQUEST_ID_RE.match(raw_rid) else str(uuid.uuid4())
        )
        start = time.monotonic()
        status = "error"
        verb = "batch-recommend-related"

        try:
            entry = registry.get(name)
            if entry is None or not entry.loaded or entry.recommender is None:
                status = "unavailable"
                raise HTTPException(
                    status_code=503,
                    detail={
                        "detail": f"Recipe '{name}' is not loaded or unhealthy",
                        "code": "RECIPE_UNAVAILABLE",
                    },
                )

            _metrics.observe_batch_size(name, verb, len(body.requests))

            results: list[dict[str, Any]] = []
            for idx, single in enumerate(body.requests):
                raw = entry.recommender.get_recommendation_for_new_user(
                    single.seed_items, single.limit
                )
                if not raw:
                    results.append({
                        "index": idx,
                        "status": "error",
                        "items": None,
                        "error": {
                            "code": "UNKNOWN_SEED_ITEMS",
                            "message": (
                                f"None of the seed_items "
                                f"{single.seed_items!r} were known to the model"
                            ),
                        },
                    })
                    continue
                items = [
                    {"item_id": item_id, "score": float(score)}
                    for item_id, score in raw
                ]
                results.append({
                    "index": idx,
                    "status": "ok",
                    "items": items,
                    "error": None,
                })

            status = "ok"
            return JSONResponse(
                content={
                    "request_id": request_id,
                    "recipe": name,
                    "model_version": entry.model_version,
                    "results": results,
                },
                headers={
                    "X-Request-ID": request_id,
                    "X-Recotem-Model-Version": entry.model_version,
                },
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "v1_batch_recommend_related_unexpected_error",
                name=name,
                request_id=request_id,
                kid=kid,
            )
            raise
        finally:
            _metrics.record_v1_request(name, verb, status, time.monotonic() - start)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest -q tests/unit/test_v1_batch_recommend_related.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/recotem/serving/v1_router.py tests/unit/test_v1_batch_recommend_related.py
git commit -m "feat(serving): POST /v1/recipes/{name}:batch-recommend-related"
```

---

## Task 11: `GET /v1/recipes` and `GET /v1/recipes/{name}`

**Files:**
- Modify: `src/recotem/serving/v1_router.py`
- Test: `tests/unit/test_v1_recipes_discovery.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_v1_recipes_discovery.py
"""GET /v1/recipes and GET /v1/recipes/{name} discovery endpoints."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.v1_router import make_v1_router


def _client_with_entries(entries: list[ModelEntry]) -> TestClient:
    registry = ModelRegistry()
    for e in entries:
        registry.replace(e.name, e)
    app = FastAPI()
    app.include_router(make_v1_router(registry, []), prefix="/v1")
    return TestClient(app)


def _stub(name: str) -> ModelEntry:
    return ModelEntry(
        name=name,
        recommender=object(),
        header={},
        kid="t",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, "abc"),
        loaded_at_unix=1747800000.0,
    )


def test_recipes_list_returns_summaries():
    r = _client_with_entries([_stub("a"), _stub("b")]).get("/v1/recipes")
    assert r.status_code == 200
    body = r.json()
    names = {x["name"] for x in body["recipes"]}
    assert names == {"a", "b"}
    a = next(x for x in body["recipes"] if x["name"] == "a")
    assert a["model_version"] == "sha256:abc"
    assert a["kind"] == "user-item"
    assert "recommend" in a["supported_verbs"]


def test_recipe_detail_returns_404_for_unknown():
    r = _client_with_entries([_stub("a")]).get("/v1/recipes/unknown")
    assert r.status_code == 404


def test_recipe_detail_returns_full_summary_for_known():
    r = _client_with_entries([_stub("a")]).get("/v1/recipes/a")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "a"
    assert body["model_version"] == "sha256:abc"
    # algorithms / best_algorithm / config_digest may be empty for the
    # stub but the keys MUST exist (contract).
    assert "algorithms" in body
    assert "best_algorithm" in body
    assert "config_digest" in body
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest -q tests/unit/test_v1_recipes_discovery.py -v
```
Expected: FAIL with 404.

- [ ] **Step 3: Add the discovery handlers**

Append inside `make_v1_router`:

```python
    from recotem.serving.schemas import (
        RecipeDetailResponse,
        RecipeSummary,
        RecipesListResponse,
    )

    @router.get(
        "/recipes",
        response_model=RecipesListResponse,
        summary="List loaded recipes",
    )
    def list_recipes(kid: str = Depends(_require_auth)) -> dict[str, Any]:
        summaries: list[dict[str, Any]] = []
        for e in registry.list():
            if not e.loaded:
                continue
            summaries.append({
                "name": e.name,
                "model_version": e.model_version,
                "loaded_at": e.loaded_at,
                "supported_verbs": e.supported_verbs,
                "kind": e.kind,
            })
        return {"recipes": summaries}

    @router.get(
        "/recipes/{name}",
        response_model=RecipeDetailResponse,
        summary="Get recipe detail",
    )
    def recipe_detail(
        name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        kid: str = Depends(_require_auth),
    ) -> dict[str, Any]:
        e = registry.get(name)
        if e is None or not e.loaded:
            raise HTTPException(
                status_code=404,
                detail={
                    "detail": f"Recipe '{name}' is not loaded",
                    "code": "RECIPE_NOT_FOUND",
                },
            )
        # algorithms / best_algorithm / config_digest are populated from
        # the artifact header when available; the watcher should now copy
        # these onto the ModelEntry at load time.  Fall back to empty/zero
        # values for stubs so the response shape remains stable.
        return {
            "name": e.name,
            "model_version": e.model_version,
            "loaded_at": e.loaded_at,
            "supported_verbs": e.supported_verbs,
            "kind": e.kind,
            "config_digest": getattr(e, "config_digest", "") or "",
            "algorithms": getattr(e, "algorithms", None) or [],
            "best_algorithm": e.best_class or "",
        }
```

If the dataclass does not yet carry `config_digest` and `algorithms`, add them as optional fields (default `""` / `None`) in `registry.py` ModelEntry; the watcher can populate them later from the artifact header. For the test in Step 1 to pass, only the *presence* of the keys is asserted.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest -q tests/unit/test_v1_recipes_discovery.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/recotem/serving/v1_router.py src/recotem/serving/registry.py tests/unit/test_v1_recipes_discovery.py
git commit -m "feat(serving): GET /v1/recipes and /v1/recipes/{name}"
```

---

## Task 12: Mount v1 router in `app.py`; delete legacy router

**Files:**
- Modify: `src/recotem/serving/app.py`
- Modify (delete content of): `src/recotem/serving/routes.py`
- Delete: existing legacy route tests (see Step 5)

- [ ] **Step 1: Audit current app.py mount**

```bash
grep -n "include_router\|make_router\|app.mount" /Users/shinsuke/workspace/recotem/src/recotem/serving/app.py
```
Note the line numbers where `make_router(...)` is currently called and `app.include_router(router)` is invoked.

- [ ] **Step 2: Switch to v1 router and prefix /v1**

In `app.py`, replace:

```python
from recotem.serving.routes import make_router
# ...
router = make_router(
    registry=registry,
    api_keys=api_keys,
    metadata_field_deny=cfg.metadata_field_deny,
)
app.include_router(router)
```

with:

```python
from recotem.serving.v1_router import make_v1_router
# ...
v1_router = make_v1_router(
    registry=registry,
    api_keys=api_keys,
    metadata_field_deny=cfg.metadata_field_deny,
)
app.include_router(v1_router, prefix="/v1")
```

If the legacy code preserves an `app.include_router(router)` (no prefix), remove the call entirely.

- [ ] **Step 3: Reduce `routes.py` to the metadata helper only**

Delete everything in `src/recotem/serving/routes.py` except imports, the `_REQUEST_ID_RE` constant (if still referenced elsewhere — confirm with `grep -r _REQUEST_ID_RE src/`), and the `_lookup_metadata` helper (lines 415-474). Replace the module docstring with:

```python
"""Helper utilities preserved from the legacy serving routes.

This module previously hosted `make_router` (alpha v0 API).  After the
v1 overhaul that lives in `v1_router.py`.  The metadata-join helper
`_lookup_metadata` remains here because both modules use it.
"""
```

If `_REQUEST_ID_RE` is no longer referenced, remove it.

- [ ] **Step 4: Run the full test suite**

```bash
pytest -q tests/unit tests/integration
```
Expected: a handful of legacy tests will fail (they hit `/predict/{name}`). Note the list of failing files — they are removed in the next step.

- [ ] **Step 5: Remove legacy test files**

```bash
# Confirm the list first:
grep -lE "POST .+/predict/|/predict/\{name\}" tests/unit/test_serving_routes.py tests/integration/test_serve_predict_e2e.py 2>/dev/null
```

Delete:

```bash
git rm tests/unit/test_serving_routes.py
# Don't delete the e2e file outright — convert it in Task 14.
```

Also delete the POC test from Task 1 if it still exists:

```bash
git rm tests/unit/test_v1_colon_path_poc.py
```

- [ ] **Step 6: Run the test suite again**

```bash
pytest -q tests/unit tests/integration --ignore=tests/integration/test_serve_predict_e2e.py
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A src/recotem/serving/app.py src/recotem/serving/routes.py tests/unit
git commit -m "feat(serving): mount v1 router under /v1, retire legacy make_router

- app.py now wires make_v1_router(...) at prefix=/v1
- routes.py reduced to the _lookup_metadata helper (still imported by
  v1_router)
- legacy tests/unit/test_serving_routes.py removed
- POC test removed"
```

---

## Task 13: Convert end-to-end integration test to v1 paths

**Files:**
- Modify: `tests/integration/test_serve_predict_e2e.py`

- [ ] **Step 1: Read the existing e2e test**

```bash
sed -n '1,80p' /Users/shinsuke/workspace/recotem/tests/integration/test_serve_predict_e2e.py
```
Note: the test posts to `/predict/{name}` and asserts `body["items"]` and `body["model"]["kid"]`. We need to:
- change the URL to `/v1/recipes/{name}:recommend`
- replace `body["model"]["kid"]` assertion with `body["model_version"].startswith("sha256:")`
- add a parallel assertion against `/v1/recipes/{name}:recommend-related`

- [ ] **Step 2: Update each `requests.post(...)` call**

Replace, line by line, every URL like:

```python
resp = requests.post(
    f"{base}/predict/{recipe_name}",
    headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    json={"user_id": user_id, "cutoff": 5},
)
```

with:

```python
resp = requests.post(
    f"{base}/v1/recipes/{recipe_name}:recommend",
    headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    json={"user_id": user_id, "limit": 5},
)
```

Replace each `body["model"]["kid"]` assertion with:

```python
assert body["recipe"] == recipe_name
assert body["model_version"].startswith("sha256:")
```

Add a new test function `test_v1_related_endpoint_returns_items` that posts to `/v1/recipes/{recipe_name}:recommend-related` with `{"seed_items": [<known item from fixture>], "limit": 5}` and asserts at least one item is returned.

- [ ] **Step 3: Run the e2e test**

```bash
pytest -q tests/integration/test_serve_predict_e2e.py -v
```
Expected: PASS. (Requires the example artifact in `examples/quickstart/` to be runnable; if pre-existing CI skips this when ports are unavailable, that's fine.)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_serve_predict_e2e.py
git commit -m "test(integration): retarget e2e suite at v1 endpoints

Replaces /predict/{name} calls with /v1/recipes/{name}:recommend and
adds a :recommend-related coverage case using the existing quickstart
artifact."
```

---

## Task 14: README + docs + migration guide

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/operations.md`
- Create: `docs/api-reference.md`
- Create: `docs/migration-v1.md`

- [ ] **Step 1: Patch README Quickstart**

Open `README.md` and locate the `# 3. Predict` block (around line 88 — re-grep with `grep -n 'curl -X POST http://localhost:8080/predict' README.md`). Replace the curl block with:

```bash
# 3a. Recommend for a known user
curl -X POST http://localhost:8080/v1/recipes/top_picks:recommend \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "u01", "limit": 5}'

# 3b. Recommend items related to a seed item
curl -X POST http://localhost:8080/v1/recipes/top_picks:recommend-related \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"seed_items": ["i00"], "limit": 5}'
```

Below the JSON example block, replace:

```json
{
  "items": [{"item_id": "i00", "score": 0.91}],
  "model": {"recipe": "top_picks", "trained_at": "...",
            "best_class": "TopPopRecommender", "kid": "dev"},
  "request_id": "..."
}
```

with:

```json
{
  "request_id": "req_01HZX...",
  "recipe": "top_picks",
  "model_version": "sha256:abc...",
  "items": [{"item_id": "i00", "score": 0.91}]
}
```

- [ ] **Step 2: Update docs/getting-started.md**

```bash
grep -n "predict/{name}\|POST /predict" /Users/shinsuke/workspace/recotem/docs/getting-started.md
```
Replace every occurrence of `/predict/{name}` with the new verb paths and update the JSON examples to the v1 shape (`recipe`, `model_version`).

- [ ] **Step 3: Update docs/operations.md**

```bash
grep -n "predict\|status.*ok\|user_not_found" /Users/shinsuke/workspace/recotem/docs/operations.md
```
- Replace `recotem_predict_*` metric names with `recotem_v1_requests_total{recipe,verb,status}` in the SLO section.
- Add a row to the SLO table: `recotem_v1_batch_size{recipe,verb}` for monitoring batch fan-out.

- [ ] **Step 4: Create docs/api-reference.md**

```markdown
# recotem v1 API Reference

Authoritative reference for the v1 HTTP surface mounted under `/v1`.

## Authentication

All endpoints except `/v1/health` require the `X-API-Key` header.  See
`docs/security.md` for key rotation procedures.

## Endpoints

### `POST /v1/recipes/{name}:recommend`
Single-user recommendation.

**Path parameters:** `name` matches `^[A-Za-z0-9_-]{1,64}$`.

**Request body:**

| field | type | required | default | notes |
|---|---|---|---|---|
| `user_id` | string | yes | – | 1-256 chars |
| `limit` | int | no | 10 | 1..1000 |
| `exclude_items` | string[] \| null | no | null | ≤1000 items |
| `context` | object \| null | no | null | reserved |

**Response body:** see `RecommendResponse` in `src/recotem/serving/schemas.py`.

**Status codes:** 200, 401, 403, 404 (`UNKNOWN_USER`), 422, 503 (`RECIPE_UNAVAILABLE`).

### `POST /v1/recipes/{name}:recommend-related`
Seed-item → items.

**Request body:**

| field | type | required | default | notes |
|---|---|---|---|---|
| `seed_items` | string[] | yes | – | 1-100 items |
| `limit` | int | no | 10 | 1..1000 |
| `exclude_items` | string[] \| null | no | null |  |
| `context` | object \| null | no | null |  |

**Status codes:** 200, 401, 403, 404 (`UNKNOWN_SEED_ITEMS`), 422, 503.

### `POST /v1/recipes/{name}:batch-recommend`
Multi-user batch.  Body: `{ "requests": RecommendRequest[] }` (1..256).
Response: `BatchRecommendResponse`.  Per-element `status` ∈ {ok, error}.
HTTP 200 on partial failure; HTTP 503 only when the recipe itself is
unavailable.

### `POST /v1/recipes/{name}:batch-recommend-related`
Multi-seed batch.  Body: `{ "requests": RecommendRelatedRequest[] }` (1..256).

### `GET /v1/recipes`
Authenticated.  Returns `RecipesListResponse` with one entry per loaded
recipe.

### `GET /v1/recipes/{name}`
Authenticated.  Returns `RecipeDetailResponse` or 404 (`RECIPE_NOT_FOUND`).

### `GET /v1/health`
Unauthenticated.  Returns `{status, total, loaded}`.

### `GET /v1/health/details`
Authenticated.  Returns `{status, recipes: {name: health}}`.

### `GET /v1/metrics`
Prometheus exposition.  Excluded from OpenAPI.  Requires
`RECOTEM_METRICS_ENABLED` to be truthy at startup.

## Headers

- `X-Request-ID` — accepted (regex `^[A-Za-z0-9_-]{1,64}$`) or generated;
  always echoed in the response.
- `X-Recotem-Model-Version` — present on every successful recommend
  response; mirrors `model_version` in the body.
- `X-Recotem-Metadata-Degraded` — `"1"` when a per-item metadata lookup
  failed during the request.

## Error Code Table

| code | HTTP | when |
|---|---|---|
| `RECIPE_UNAVAILABLE` | 503 | recipe not loaded |
| `RECIPE_NOT_FOUND`   | 404 | no such recipe in registry |
| `UNKNOWN_USER`       | 404 | user not in idmap |
| `UNKNOWN_SEED_ITEMS` | 404 | none of seed_items known to model |
| `VALIDATION_ERROR`   | 422 | Pydantic schema rejected |
```

- [ ] **Step 5: Create docs/migration-v1.md**

```markdown
# Migrating from alpha to v1

recotem v1 removes the alpha-era `/predict/{name}` surface.  Update
clients per the table below.

| Old (alpha) | New (v1) |
|---|---|
| `POST /predict/{name}` body `{user_id, cutoff}` | `POST /v1/recipes/{name}:recommend` body `{user_id, limit}` |
| `GET /health` | `GET /v1/health` |
| `GET /health/details` | `GET /v1/health/details` |
| `GET /models` | `GET /v1/recipes` (now authenticated; payload shape changed) |
| `GET /metrics` | `GET /v1/metrics` (path only changed) |

## Response shape changes

`POST /v1/recipes/{name}:recommend` no longer exposes `model.kid` /
`model.trained_at` / `model.best_class`.  Move those reads to
`GET /v1/recipes/{name}`.  The recipe name and a deterministic
artifact identifier are available as `recipe` and `model_version`
(prefixed `sha256:`) on every recommend response.

## New capability

The "related items" use case is now first-class:

```bash
curl -X POST http://localhost:8080/v1/recipes/<recipe>:recommend-related \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -d '{"seed_items": ["<item_id>"], "limit": 10}'
```

Batch variants (`:batch-recommend`, `:batch-recommend-related`) accept up
to 256 requests in a single call and return per-element status so
partial failures (e.g. one unknown user) do not fail the whole batch.
```

- [ ] **Step 6: Commit**

```bash
git add README.md docs/getting-started.md docs/operations.md docs/api-reference.md docs/migration-v1.md
git commit -m "docs: refresh README + getting-started + add api-reference and migration-v1

Aligns published documentation with the v1 API surface."
```

---

## Task 15: CHANGELOG and final smoke check

**Files:**
- Modify or create: `CHANGELOG.md`

- [ ] **Step 1: Verify CHANGELOG exists or create one**

```bash
test -f /Users/shinsuke/workspace/recotem/CHANGELOG.md && head -20 /Users/shinsuke/workspace/recotem/CHANGELOG.md || echo "missing"
```

If missing, create with:

```markdown
# Changelog

All notable changes to recotem are documented here.  Format roughly
follows Keep a Changelog (https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added
- v1 HTTP API mounted at `/v1` with four inference verbs
  (`:recommend`, `:recommend-related`, `:batch-recommend`,
  `:batch-recommend-related`), recipe discovery
  (`GET /v1/recipes` / `GET /v1/recipes/{name}`), and lifted health
  and metrics endpoints.
- `recotem_v1_requests_total{recipe,verb,status}` counter and
  `recotem_v1_request_latency_seconds` histogram.

### Removed
- The alpha-era `POST /predict/{name}` surface and the
  `GET /models` endpoint.  See `docs/migration-v1.md`.

### Changed
- Recommend responses now expose `model_version` (artifact SHA-256
  prefixed `sha256:`) instead of `model.kid` / `trained_at` /
  `best_class`.  Those values now live on `GET /v1/recipes/{name}`.
```

If a CHANGELOG already exists, prepend an `## Unreleased` section with the same content.

- [ ] **Step 2: Run the full suite**

```bash
pytest -q
```
Expected: green.

- [ ] **Step 3: Smoke-test the quickstart with v1**

```bash
export RECOTEM_SIGNING_KEYS="dev:0000000000000000000000000000000000000000000000000000000000000000"
export RECOTEM_API_PLAINTEXT="recotem-quickstart-demo-key-0000"
export RECOTEM_API_KEYS="dev:sha256:21be5c3be85b8d68123df9f9b6a26d8e307db30350ea8bcc844883e22ebcf125"

uv run recotem train examples/quickstart/recipe.yaml
uv run recotem serve --recipes examples/quickstart/ &
SERVE_PID=$!

until curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/health | grep -q "200"; do sleep 1; done

curl -fsS -X POST http://localhost:8080/v1/recipes/top_picks:recommend \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u01","limit":5}' | tee /tmp/v1-recommend.json

curl -fsS -X POST http://localhost:8080/v1/recipes/top_picks:recommend-related \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"seed_items":["i00"],"limit":5}' | tee /tmp/v1-related.json

curl -fsS -X POST http://localhost:8080/v1/recipes/top_picks:batch-recommend \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"requests":[{"user_id":"u01","limit":3},{"user_id":"u_does_not_exist"}]}' | tee /tmp/v1-batch.json

kill $SERVE_PID
```

Verify each response has the expected shape (recipe, model_version, items / results).

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: record v1 API overhaul in CHANGELOG"
```

- [ ] **Step 5: Final ruff/mypy/test pass**

```bash
pre-commit run --all-files || true
pytest -q
```
Expected: green.  Fix any flagged issues inline and amend the latest commit if needed.

---

## Intentionally deferred

- **`X-Recotem-Metadata-Degraded` header** (mentioned in spec §4.6). The legacy `/predict/{name}` set this to `"1"` when a per-item metadata lookup failed mid-request. The v1 handlers in Tasks 7-10 reuse the same `_lookup_metadata` helper, so server-side observability (`recotem_metadata_lookup_errors_total`) is preserved, but the header is not re-emitted. Add this only if a real consumer needs it — track as a separate small change (~5 lines per single endpoint, ~10 for batch).

## Done

The branch `feat/v1-api` now contains:
- New v1 router with all 9 endpoints (4 inference + 2 discovery + 2 health + 1 metrics)
- Updated schemas, registry, metrics
- Removed alpha `/predict/{name}` surface
- Regenerated tests
- Refreshed README, getting-started, operations, api-reference, migration-v1, CHANGELOG
- Quickstart verified end-to-end with v1 paths

Push and open a PR.  Reviewers should focus on:
1. The four request/response shapes vs the spec's §4.3.
2. Partial-failure semantics in batch endpoints.
3. Whether the `_lookup_metadata` reuse between `routes.py` and `v1_router.py` should be moved to a dedicated `metadata.py` (out of scope for this plan).
