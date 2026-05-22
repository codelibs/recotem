# recotem v1 API Reference

Authoritative reference for the v1 HTTP surface mounted under `/v1`.

## Authentication

All endpoints except `/v1/health` require the `X-API-Key` header.  See
`docs/security.md` for key rotation procedures.

## Endpoints

### `POST /v1/recipes/{name}:recommend`
Single-user recommendation.

**Path parameters:** `name` matches `^[A-Za-z0-9_-]{1,64}$` (same as the
recipe-name constraint enforced by the recipe loader).

**Request body:**

| field | type | required | default | notes |
|---|---|---|---|---|
| `user_id` | string | yes | – | 1-256 chars |
| `limit` | int | no | 10 | 1..1000 |
| `exclude_items` | string[] \| null | no | null | ≤1000 items |

**Response body:** see `RecommendResponse` in `src/recotem/serving/schemas.py`.

**Status codes:** 200, 401, 404 (`UNKNOWN_USER` | `RECIPE_NOT_FOUND`), 422 (`VALIDATION_ERROR`), 503 (`RECIPE_UNAVAILABLE`).

### `POST /v1/recipes/{name}:recommend-related`
Seed-item → items.

**Request body:**

| field | type | required | default | notes |
|---|---|---|---|---|
| `seed_items` | string[] | yes | – | 1-100 items |
| `limit` | int | no | 10 | 1..1000 |
| `exclude_items` | string[] \| null | no | null |  |

**Status codes:** 200, 401, 404 (`UNKNOWN_SEED_ITEMS` | `NO_CANDIDATES` | `RECIPE_NOT_FOUND`), 422 (`VALIDATION_ERROR`), 503 (`RECIPE_UNAVAILABLE`).

`UNKNOWN_SEED_ITEMS` means none of the supplied `seed_items` were known
to the model id-map (typically a client-side data issue).
`NO_CANDIDATES` means at least one seed was known but the ranker did not
produce any survivors after its internal filtering — typically a data
distribution issue rather than a client mistake.

### `POST /v1/recipes/{name}:batch-recommend`
Multi-user batch.  Body: `{ "requests": RecommendRequest[], "include_metadata": bool }` (1..256).
Response: `BatchRecommendResponse`.  Per-element `status` ∈ {ok, error}.
HTTP 200 on partial failure; HTTP 503 only when the recipe itself is
unavailable.

`include_metadata` (default `false`): when `true`, each `ok` result
includes per-item metadata fields (same join as the single-recommend
endpoint).  Default `false` preserves the performance-first default for
bulk callers.

The aggregate `sum(requests[].limit)` must not exceed **5000**.  When a
sub-request would push the running aggregate over the cap, that element
surfaces as `status=error, code=VALIDATION_ERROR` and processing of
subsequent elements continues — earlier elements are unaffected.  The
list size cap (1..256) is enforced at the schema level (whole-request
422 if violated); per-element schema failures are surfaced per-element
so a single bad entry never 422s the whole batch.

**Status codes:** 200, 401, 404 (`RECIPE_NOT_FOUND`), 422 (`VALIDATION_ERROR` — only for whole-request shape, e.g. missing `requests` key, list too large), 503 (`RECIPE_UNAVAILABLE`).

> **Note:** batch endpoints return `{item_id, score}` only by default
> (`include_metadata=false`).  Set `include_metadata: true` to include
> per-item metadata fields (same join as single-recommend endpoints).
> Be aware that metadata enrichment increases response size; for bulk callers
> that do not need metadata the default `false` is recommended.

### `POST /v1/recipes/{name}:batch-recommend-related`
Multi-seed batch.  Body: `{ "requests": RecommendRelatedRequest[], "include_metadata": bool }` (1..256).
Same aggregate-limit, per-element validation rules, and `include_metadata`
semantics as `:batch-recommend`.

**Status codes:** 200, 401, 404 (`RECIPE_NOT_FOUND`), 422 (`VALIDATION_ERROR` — only for whole-request shape), 503 (`RECIPE_UNAVAILABLE`).

### `GET /v1/recipes`
Authenticated.  Returns `RecipesListResponse` with one entry per loaded
recipe.

### `GET /v1/recipes/{name}`
Authenticated.  Returns `RecipeDetailResponse` or 404 (`RECIPE_NOT_FOUND`).

**Status codes:** 200, 401, 404 (`RECIPE_NOT_FOUND`), 503 (`RECIPE_UNAVAILABLE`).

### `GET /v1/health`
Unauthenticated.  Returns `{status, total, loaded}`.  Body status is
`"ok"` when every registered recipe is loaded, `"degraded"` otherwise.
The HTTP response code mirrors body status: **200 OK** when ok, **503
Service Unavailable** when degraded — so K8s readiness probes pointing
at this endpoint mark the pod NotReady whenever any recipe is
unloaded.

### `GET /v1/health/details`
Authenticated.  Returns `{status, recipes: {name: health}}`.  Same 200
/ 503 status-code rule as `/v1/health`.

### `GET /v1/metrics`
Prometheus exposition.  Excluded from OpenAPI.  Requires
`RECOTEM_METRICS_ENABLED` to be truthy at startup.

**Requires `X-API-Key`** — unlike the alpha `/metrics` endpoint, which was
unauthenticated.  Configure your Prometheus scraper with an `authorization`
block or `http_headers` before upgrading.  See
[docs/migration-v1.md](migration-v1.md#v1metrics-now-requires-x-api-key) for
the scrape-config snippet.

## Headers

- `X-Request-ID` — accepted (regex `^[A-Za-z0-9_-]{1,128}$`) or generated;
  always echoed in the response.  When missing or invalid the server
  substitutes a 12-char hex string.  Handlers read the validated value
  from `request.state.request_id`, so the body field and response header
  always agree.
- `X-Recotem-Model-Version` — present on every successful recommend
  response; mirrors `model_version` in the body.

## Error body shape

All v1 error responses share a flat envelope at the top of the body:

```json
{"detail": "<human-readable message>", "code": "<MACHINE_CODE>"}
```

There is no nested `{"detail": {"detail": ..., "code": ...}}` form —
clients parse `body["detail"]` and `body["code"]` directly.

**422 validation errors** add a per-field breakdown from FastAPI /
Pydantic and include the request ID so the body is correlatable with the
`X-Request-ID` response header:

```json
{
  "request_id": "<id matching X-Request-ID>",
  "detail": "Request validation failed",
  "code": "VALIDATION_ERROR",
  "errors": [{"loc": ["body", "limit"], "msg": "...", "type": "..."}]
}
```

**500 unhandled errors** flatten to:

```json
{"detail": "internal error", "code": "INTERNAL_ERROR"}
```

Each endpoint above lists the status codes it can emit; the body shape
in every error case is one of the three forms above.

## Error Code Table

| code | HTTP | when |
|---|---|---|
| `RECIPE_UNAVAILABLE` | 503 | recipe not loaded |
| `RECIPE_NOT_FOUND`   | 404 | no such recipe in registry |
| `UNKNOWN_USER`       | 404 | user not in idmap |
| `UNKNOWN_SEED_ITEMS` | 404 | none of seed_items known to model |
| `NO_CANDIDATES`      | 404 | seeds known, but ranker produced no survivors |
| `VALIDATION_ERROR`   | 422 | Pydantic schema rejected the request (also used per-element inside batch responses) |
| `MISSING_API_KEY`    | 401 | `X-API-Key` header missing |
| `INVALID_API_KEY`    | 401 | `X-API-Key` header present but did not match any configured digest (also covers short-key / oversize-key rejections so callers cannot fingerprint the guard) |
| `INTERNAL_ERROR`     | 500 / batch | unhandled server-side exception, or unexpected recommender internal layout (`recommender_layout_unexpected`) — status=500 on single endpoints; per-element `status=error` inside batch responses |

All v1 codes use `UPPER_SNAKE_CASE`.
