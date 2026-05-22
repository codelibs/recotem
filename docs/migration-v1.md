# Alpha → v1 Migration Guide

## Overview

The alpha API exposed a single inference verb (`POST /predict/{name}`) and a
model-list endpoint (`GET /models`) under no version prefix. The v1 API mounts
everything under `/v1` and introduces four inference verbs, recipe discovery
endpoints, and lifted health/metrics paths. Authentication, error codes, and
Prometheus metric names all changed in ways that require client and operator
updates before cutover. There is **no dual-emit period** — the alpha surface is
removed entirely.

---

## Endpoint mapping

| Alpha | v1 |
|---|---|
| `POST /predict/{name}` | `POST /v1/recipes/{name}:recommend` |
| `GET /models` | `GET /v1/recipes` (list) — plus `GET /v1/recipes/{name}` for detail |
| `GET /health` | `GET /v1/health` |
| `GET /health/details` | `GET /v1/health/details` |
| `GET /metrics` | `GET /v1/metrics` (**now requires `X-API-Key`** — was unauthenticated in alpha) |
| _(none)_ | `POST /v1/recipes/{name}:recommend-related` |
| _(none)_ | `POST /v1/recipes/{name}:batch-recommend` |
| _(none)_ | `POST /v1/recipes/{name}:batch-recommend-related` |

---

## Field renames

### Request body

| Alpha field | v1 field | Notes |
|---|---|---|
| `cutoff` | `limit` | Same 1..1000 range |

### Response body

The alpha response embedded model metadata under a nested `model` object:

```json
{
  "model": {
    "recipe": "news_articles",
    "trained_at": "2025-01-01T00:00:00Z",
    "best_class": "IALSRecommender",
    "kid": "prod-2025-q1"
  },
  "items": [...]
}
```

v1 collapses this to a single opaque string:

```json
{
  "recipe": "news_articles",
  "model_version": "sha256:<hex>",
  "items": [...]
}
```

`kid`, `trained_at`, and `best_class` are now available via
`GET /v1/recipes/{name}` (authenticated). They are no longer present on every
recommend response.

### Response header

`X-Recotem-Model-Version` (new in v1) — present on every successful recommend
response; value mirrors `model_version` in the body.

---

## Error envelope changes

### Envelope shape

Alpha used a nested envelope:

```json
{"detail": {"detail": "recipe not loaded", "code": "recipe_unavailable"}}
```

v1 uses a flat envelope. Parse `body["detail"]` and `body["code"]` directly:

```json
{"detail": "recipe not loaded", "code": "RECIPE_UNAVAILABLE"}
```

### HTTP status change: typoed recipe name

A recipe name that does not exist in the registry now returns **HTTP 404
`RECIPE_NOT_FOUND`**. In alpha this was conflated with 503. Clients relying
on naive 5xx-retry logic will pass through 404s silently — update to treat
404 as a hard failure (the recipe does not exist) and 503 as retryable (the
recipe exists but is not currently loaded).

### Error code renames

All v1 codes use `UPPER_SNAKE_CASE`.

| Alpha code | v1 code | HTTP |
|---|---|---|
| `recipe_unavailable` | `RECIPE_UNAVAILABLE` | 503 |
| `user_not_found` | `UNKNOWN_USER` | 404 |
| _(alpha: 503, conflated)_ | `RECIPE_NOT_FOUND` | 404 (new, distinct) |
| `missing_api_key` | `MISSING_API_KEY` | 401 |
| `invalid_api_key` | `INVALID_API_KEY` | 401 |
| `internal_error` | `INTERNAL_ERROR` | 500 |
| _(none)_ | `UNKNOWN_SEED_ITEMS` | 404 |
| _(none)_ | `NO_CANDIDATES` | 404 |
| _(none)_ | `VALIDATION_ERROR` | 422 |

---

## Dropped without replacement

### `X-Recotem-Metadata-Degraded` response header

Alpha set this header when any per-item metadata lookup failed during a
request. v1 does not emit it. Metadata enrichment now happens entirely at
artifact-load time (not per-request), so there is no per-request signal to
report.

The replacement signal is the load-time counter
`recotem_metadata_lookup_errors_total{recipe}`, which fires once per row error
at artifact load — not per recommend request. Dashboards or alerts keyed on
the header must move to this counter or accept reduced fidelity.

---

## Metrics changes

There is **no dual-emit window** for Prometheus metrics. Update Prometheus
rules, Grafana dashboards, and alerts before rolling out v1.

| Alpha metric | v1 metric | Change |
|---|---|---|
| `recotem_predict_total{status}` | `recotem_v1_requests_total{recipe,verb,status}` | New `recipe` and `verb` labels; expanded `status` taxonomy |
| `recotem_predict_latency_seconds` | `recotem_v1_request_latency_seconds{recipe,verb}` | Per-recipe, per-verb histogram |
| `recotem_artifact_load_failures_total{recipe}` | `recotem_artifact_load_failures_total{recipe,reason}` | New `reason` label (`read`, `parse`, `hmac`, `header_json`, `deserialize`, `metadata`, `yaml`, `unexpected`, `dir_scan`) |

See `docs/operations.md` for the full metrics table and recommended alert
thresholds.

### `/v1/metrics` now requires `X-API-Key`

The alpha `/metrics` endpoint was unauthenticated. `/v1/metrics` requires a
valid `X-API-Key`. Update your Prometheus scrape configuration before
upgrading:

```yaml
scrape_configs:
  - job_name: recotem
    static_configs:
      - targets: ["recotem:8080"]
    authorization:
      type: ""
      credentials_file: /etc/prometheus/recotem-api-key
    # OR, using http_headers:
    # http_headers:
    #   X-API-Key:
    #     values: ["<plaintext>"]
```

The file referenced by `credentials_file` should contain the raw API key
plaintext (no trailing newline). Generate a dedicated scrape key with
`recotem keygen --type api`; do not reuse application keys.

---

## Batch endpoints — metadata opt-in

Single endpoints (`:recommend`, `:recommend-related`) always join per-item
metadata (subject to `RECOTEM_METADATA_FIELD_DENY`).

Batch endpoints (`:batch-recommend`, `:batch-recommend-related`) default to
`include_metadata: false` for throughput. Opt in per-request:

```json
{
  "requests": [{"user_id": "u1", "limit": 10}],
  "include_metadata": true
}
```

---

## Batch partial-failure semantics

Batch endpoints return HTTP 200 with a per-element `status` field:

```json
{
  "results": [
    {"status": "ok", "items": [...]},
    {"status": "error", "code": "UNKNOWN_USER", "detail": "..."}
  ]
}
```

- **HTTP 200** — at least one element was processed (including all-error batches where the recipe itself is healthy).
- **HTTP 503** — the recipe is unavailable for the entire batch (`RECIPE_UNAVAILABLE`). This is retryable.
- **HTTP 404** — the recipe does not exist (`RECIPE_NOT_FOUND`). This is a hard failure.

Clients that treat any non-200 as a full-batch error need no changes for the
common path, but must handle per-element `status: "error"` entries in 200
responses.
