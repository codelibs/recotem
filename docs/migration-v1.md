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

## Removed legacy metrics

The alpha `/predict/{name}` endpoint exposed two Prometheus metrics that have
been removed.  Update any Prometheus alerting rules or Grafana dashboards to
use the v1 equivalents:

| Old (`/predict/{name}`) | New (`/v1/…`) |
|---|---|
| `recotem_predict_total` | `recotem_v1_requests_total{verb="recommend"}` |
| `recotem_predict_latency_seconds` | `recotem_v1_request_latency_seconds{verb="recommend"}` |

**Action required:** rename any alert rules or recording rules that reference
`recotem_predict_total` or `recotem_predict_latency_seconds`; those series
will no longer be emitted after upgrading to v1.

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

**Note:** batch endpoints currently return items as
`{item_id, score}` only.  They do **not** include the metadata fields
that single-recommendation endpoints provide.  If you need enriched
items, call the single endpoint per user/seed.
