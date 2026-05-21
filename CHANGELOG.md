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
- `recotem_v1_batch_size{recipe,verb}` histogram for batch-size
  monitoring.

### Removed
- The alpha-era `POST /predict/{name}` surface and the
  `GET /models` endpoint.  See `docs/migration-v1.md`.

### Changed
- Recommend responses now expose `model_version` (artifact SHA-256
  prefixed `sha256:`) instead of `model.kid` / `trained_at` /
  `best_class`.  Those values now live on `GET /v1/recipes/{name}`.
- Health/metrics endpoints moved from unprefixed paths to `/v1/health`,
  `/v1/health/details`, and `/v1/metrics`.

### Notes
- `X-Recotem-Metadata-Degraded` header is reserved for future use;
  v1 single-recommend endpoints currently do not emit it (server-side
  metadata-lookup-error metric is still recorded).
- Batch endpoints (`:batch-recommend`, `:batch-recommend-related`)
  return items as `{item_id, score}` only — no per-item metadata
  join.  Single endpoints continue to join metadata.
