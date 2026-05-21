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

> **Note:** batch endpoints (`:batch-recommend` and
> `:batch-recommend-related`) return items as `{item_id, score}` only —
> they do **not** include the per-item metadata fields that single
> recommendations join via `metadata_index` / `metadata_df`.  If you
> need enriched items, call the single-recommendation endpoint per
> user/seed.

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
  failed during the request.  **Currently reserved**: no v1 endpoint
  emits this header today.  Server-side metadata-lookup errors are
  still recorded in the `recotem_metadata_lookup_errors_total` metric.

## Error Code Table

| code | HTTP | when |
|---|---|---|
| `RECIPE_UNAVAILABLE` | 503 | recipe not loaded |
| `RECIPE_NOT_FOUND`   | 404 | no such recipe in registry |
| `UNKNOWN_USER`       | 404 | user not in idmap |
| `UNKNOWN_SEED_ITEMS` | 404 | none of seed_items known to model |
| `VALIDATION_ERROR`   | 422 | Pydantic schema rejected |
