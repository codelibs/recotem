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
  `recotem_v1_request_latency_seconds` histogram. `status` ∈
  `{ok, unknown_user, unknown_seed_items, no_candidates,
  recipe_not_found, unavailable, validation_error, error}`.
- `recotem_v1_batch_size{recipe,verb}` histogram for batch-size
  monitoring.
- `recotem_v1_batch_element_errors_total{recipe,verb,code}` counter so
  per-element batch failures (HTTP 200 with `status=error` entries) are
  observable in Prometheus without scraping response bodies.
- `NO_CANDIDATES` error code (404) emitted by `:recommend-related` and
  per-element in `:batch-recommend-related` when at least one supplied
  `seed_item` is known to the model id-map but the ranker produces no
  survivors.  Distinguished from `UNKNOWN_SEED_ITEMS` (no known seed).
- `recotem_artifact_load_failures_total` now carries a `reason` label
  (`read | parse | hmac | header_json | deserialize | metadata | yaml |
  unexpected`) so HMAC failures (a security signal) can be alerted on
  independently of operational failures.

### Removed
- The alpha-era `POST /predict/{name}` surface and the
  `GET /models` endpoint.
- Prometheus metrics `recotem_predict_total` and
  `recotem_predict_latency_seconds` (use `recotem_v1_requests_total` /
  `recotem_v1_request_latency_seconds` instead).
- `X-Recotem-Metadata-Degraded` header.  The fallback path that emitted
  it became unreachable once `metadata_index` was populated at every
  artifact load, so the header and its in-router dead code have been
  removed.  `recotem_metadata_lookup_errors_total` is still emitted from
  the artifact-load path.

### Changed
- Recommend responses now expose `model_version` (artifact SHA-256
  prefixed `sha256:`) instead of `model.kid` / `trained_at` /
  `best_class`.  Those values now live on `GET /v1/recipes/{name}`.
- Health/metrics endpoints moved from unprefixed paths to `/v1/health`,
  `/v1/health/details`, and `/v1/metrics`.
- Unknown recipes now return **HTTP 404 `RECIPE_NOT_FOUND`** instead of
  the alpha API's HTTP 503 `recipe_unavailable`.  Clients should treat
  404 as a hard failure (recipe simply does not exist) and 503 as
  retryable (recipe known but not currently loaded).
- All error codes now use `UPPER_SNAKE_CASE`.  Previously the auth and
  unhandled-exception paths emitted `missing_api_key`,
  `invalid_api_key`, `internal_error`; they now emit
  `MISSING_API_KEY`, `INVALID_API_KEY`, `INTERNAL_ERROR`.
- `X-Request-ID` echo regex relaxed from `{1,64}` to `{1,128}` chars to
  match common tracing-vendor ID lengths.
- `/v1/recipes/{name}` path regex relaxed from
  `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` to `^[A-Za-z0-9_-]{1,64}$` to
  match the recipe-name constraint enforced by the recipe loader.
- Batch endpoints now validate sub-requests **per-element** rather than
  rejecting the whole batch on the first invalid entry.  Bad elements
  surface as `status=error, code=VALIDATION_ERROR`; valid elements
  continue to be processed.  The list-size cap (1..256) is still
  enforced at the schema level (whole-request 422 if violated).
- Aggregate `sum(limit)` cap of **5000** across `requests[]` is now
  enforced per-element: an element that would push the running aggregate
  over the cap surfaces as `VALIDATION_ERROR`; subsequent elements are
  still processed.  Documented in `docs/api-reference.md`.
- Batch per-element failures now log at `logger.exception(...)` with
  `exc_type=type(exc).__name__` (previously hard-coded as
  `"Exception"`), so a 500 inside a batch element is diagnosable from
  the structured logs.
- Initial-startup HMAC verification failures are now logged at `ERROR`
  level with `exc_info=True` (previously `WARNING` without traceback),
  matching the security-event severity of the signal.
- `recipe_unavailable` 503 responses now record `status="unavailable"`
  on `recotem_v1_requests_total` (previously fell through to
  `status="error"`).  `RECIPE_NOT_FOUND` 404s record
  `status="recipe_not_found"`.

### Notes
- Batch endpoints (`:batch-recommend`, `:batch-recommend-related`)
  return items as `{item_id, score}` only — no per-item metadata
  join.  Single endpoints continue to join metadata.
