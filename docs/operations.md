# Operations

## Signing key rotation

Signing keys are configured in `RECOTEM_SIGNING_KEYS` as a comma-separated list of `<kid>:<hex64>` entries (64 hex characters = 32 raw bytes). The server verifies against any entry; `recotem train` always signs with the **first** entry (the active key).

This multi-kid pattern enables zero-downtime rotation:

### Step-by-step rotation

1. **Generate a new key.**

   ```bash
   recotem keygen --type signing --kid prod-2026-q3
   # kid=prod-2026-q3
   # plaintext=<64 hex chars>       <-- 32 raw bytes; this IS the signing key
   # fingerprint=ddeeff00           <-- sha256(key_bytes)[:8]; matches /security.posture log
   # env_entry=RECOTEM_SIGNING_KEYS=prod-2026-q3:<64 hex chars>
   ```

   For signing keys, the **`plaintext`** line is the actual key — copy it (or the ready-made `env_entry=` line) into `RECOTEM_SIGNING_KEYS`. The `fingerprint=` line is `sha256(key_bytes)[:8]` and matches the `fingerprint` field in the `security.posture` log line; it is informational only and must not be used in `RECOTEM_SIGNING_KEYS`. (The `sha256:` wire prefix is reserved for `RECOTEM_API_KEYS` entries — see "API key rotation" below.)

2. **Add the new kid as the first entry, keeping the old one.**

   ```bash
   # Before:
   RECOTEM_SIGNING_KEYS="prod-2026-q2:aabbcc..."

   # After (new key first):
   RECOTEM_SIGNING_KEYS="prod-2026-q3:ddeeff...,prod-2026-q2:aabbcc..."
   ```

   Restart (or reload) `recotem serve` with the updated env. The server now accepts artifacts signed by either kid.

3. **Retrain all models.**

   Run `recotem train` for each recipe. Each new artifact is signed with `prod-2026-q3` (the first entry). The server hot-swaps each model as the new artifact appears. Old artifacts signed with `prod-2026-q2` continue to serve until each recipe is retrained.

4. **Remove the old kid and verify.**

   Once all recipes have been retrained and hot-swapped, remove the old entry:

   ```bash
   RECOTEM_SIGNING_KEYS="prod-2026-q3:ddeeff..."
   ```

   Restart `recotem serve`. Any artifact still signed with the old kid will fail to load and appear as `loaded: false` in `/health`. Retrain those recipes.

   Confirm all recipes loaded successfully:

   ```bash
   # -f / --fail returns exit 22 on 4xx/5xx, which would mask a 503.
   # Use -w to capture the status code instead.
   HTTP_STATUS=$(curl -s -o /tmp/health.json -w "%{http_code}" http://localhost:8080/health)
   echo "HTTP $HTTP_STATUS"
   jq '.recipes | to_entries[] | select(.value.loaded == false)' /tmp/health.json
   ```

   Empty output from the `jq` command means all recipes loaded successfully under the new key.

### Key fingerprint

At startup, `recotem serve` logs a `security.posture` event that includes `sha256(key)[:8]` per kid. You can confirm the correct key is active without ever exposing the key itself:

```json
{"event": "security.posture", "signing_keys": [{"kid": "prod-2026-q3", "fingerprint": "ddeeff00"}], ...}
```

---

## API key rotation

API keys live in `RECOTEM_API_KEYS` as `<kid>:sha256:<hex64>` entries. Rotation is additive: add the new entry, update clients, then remove the old entry.

1. **Generate a new key.**

   ```bash
   recotem keygen --type api --kid client-a-v2
   # kid=client-a-v2
   # plaintext=<43-char base64url — share with the client>
   # hash=sha256:<64-hex — put this in RECOTEM_API_KEYS>
   # env_entry=RECOTEM_API_KEYS=client-a-v2:sha256:<64-hex>
   ```

   `--type api` is required — without it `recotem keygen` defaults to
   `--type signing` and would emit the wrong key format.

2. **Add the new entry alongside the old one.**

   ```bash
   # Before:
   RECOTEM_API_KEYS="client-a:sha256:oldhhh..."

   # After:
   RECOTEM_API_KEYS="client-a:sha256:oldhhh...,client-a-v2:sha256:newhhh..."
   ```

   Restart `recotem serve`. Both keys are valid simultaneously. Share the new plaintext with the client.

3. **Client switches to the new key.**

4. **Remove the old entry.**

   ```bash
   RECOTEM_API_KEYS="client-a-v2:sha256:newhhh..."
   ```

   Restart `recotem serve`.

The plaintext is shown only once at generation time. If lost, generate a new key — there is no recovery.

---

## Recovery from a corrupt artifact

If an artifact is corrupt (truncated write, disk error, storage-side corruption), `recotem serve` logs an error and marks the recipe as `loaded: false`. At startup the event name is `initial_artifact_parse_failed` (or `initial_artifact_read_failed`); during watcher hot-swaps it is `artifact_load_failed`:

```json
{"event": "artifact_load_failed", "name": "my_recipe", "error": "magic bytes mismatch", "kid": "<unknown>"}
```

The `kid` field reads `"<unknown>"` only when the artifact is too short to
hold a full kid (truncated writes, zero-byte files). For a tampered or
wrong-magic file of the expected length, the parsed kid string is shown
verbatim instead — useful for grepping which signing key the offending
artifact was written with.

The server continues running and returns 503 for that recipe's `/predict/{name}` endpoint.

**Recovery steps:**

1. **Inspect the artifact** (safe even on corrupt files — HMAC and size checks reject before deserialization).
   `recotem inspect` accepts both local paths and fsspec URIs (`s3://`, `gs://`,
   `az://`, `https://`, `file://`):

   ```bash
   recotem inspect ./artifacts/my_recipe.recotem
   # local path — exit 5: ArtifactError: magic bytes mismatch

   recotem inspect s3://my-bucket/artifacts/my_recipe.recotem
   # object-store URI — same exit codes apply
   ```

2. **Retrain.**

   ```bash
   recotem train ./recipes/my_recipe.yaml
   ```

   This writes a fresh, signed artifact. The server detects the new file at the next poll and hot-swaps.

3. **Verify.**

   ```bash
   curl http://localhost:8080/health | jq '.recipes.my_recipe'
   # {"loaded": true, "best_class": "IALSRecommender", ...}
   ```

If the artifact was written with `versioning: append_sha`, the old corrupt file is still present with its sha-suffix name. You can delete it after confirming the new artifact loaded:

```bash
ls ./artifacts/
# my_recipe.recotem           <- pointer file (points to current)
# my_recipe.abc12345.recotem  <- old corrupt file (safe to delete)
# my_recipe.def67890.recotem  <- new good file (current)
rm ./artifacts/my_recipe.abc12345.recotem
```

---

## CLI flag reference

### `recotem train` flags

| Flag | Default | Description |
|------|---------|-------------|
| `--no-lock` | `false` | Skip per-recipe POSIX file lock acquisition. Only safe when you guarantee no concurrent writers through another mechanism (e.g. scheduler-level mutex). |
| `--fail-on-busy` | `false` | Exit 6 (`LockContestedError`) immediately if the recipe lock is held, instead of the default behaviour (exit 0, log `recipe_lock_contended_skipping`). Use this in orchestrators that treat non-zero as "retry elsewhere". |
| `--lock-timeout <seconds>` | `0.0` | Seconds to wait for the per-recipe lock before failing. `0.0` = non-blocking immediate failure (default). `-1` = wait indefinitely. Has no effect when `--no-lock` is set. |
| `-q` / `--quiet` | `false` | Suppress per-trial output from Optuna. Reduces log volume during large search budgets. |
| `-v` / `--verbose` | `false` | Dump per-trial hyperparameter values to the log. Useful for debugging search behaviour; avoid in production (can produce large log volumes). |
| `--run-id <id>` | random 12-hex | Stable run identifier. Reuse the same value across invocations to resume a persistent Optuna study (requires `training.storage_path` set in the recipe). Pattern: `[A-Za-z0-9_.-]{1,64}`. If omitted, a fresh random id is generated each run. |
| `--env-var KEY=VALUE` | — | Inject additional `RECOTEM_RECIPE_*` values for recipe env-var expansion without exporting them to the shell environment. The `KEY` must start with `RECOTEM_RECIPE_` and must not match the expansion blacklist. Repeatable: `--env-var A=x --env-var B=y`. See [recipe-reference.md](recipe-reference.md#environment-variable-expansion). |
| `--dev-allow-unsigned` | `false` | Skip HMAC signing and use a deterministic in-memory dev key. Requires `RECOTEM_ENV=development` AND `--i-understand-this-loads-arbitrary-code`. Never use outside controlled local testing. |

### `recotem inspect` flags

`recotem inspect` accepts both local paths and fsspec URIs as the artifact argument:

```bash
recotem inspect ./artifacts/my_recipe.recotem           # local path
recotem inspect s3://my-bucket/artifacts/my.recotem     # S3 URI
recotem inspect gs://my-bucket/artifacts/my.recotem     # GCS URI
recotem inspect az://my-container/artifacts/my.recotem  # Azure Blob URI
recotem inspect https://host/artifacts/my.recotem        # HTTPS URI
```

Requires `RECOTEM_SIGNING_KEYS` to be set (or `--dev-allow-unsigned` with
`RECOTEM_ENV=development`). When signing keys are absent and `--dev-allow-unsigned`
is not passed, `inspect` exits 8 (`_EXIT_CONFIG`) — not 5.

| Flag | Default | Description |
|------|---------|-------------|
| `--dev-allow-unsigned` | `false` | Verify against the deterministic in-memory dev key (`dev:0000…`) when `RECOTEM_SIGNING_KEYS` is unset. Useful for inspecting artifacts produced by `recotem train --dev-allow-unsigned`. |

---

## CLI exit codes

`recotem train`, `serve`, `inspect`, `validate` all map exceptions to a
small set of exit codes. Use these in CI / cron / Kubernetes Job restart
logic instead of grepping stderr.

| Code | Meaning | Typical cause |
|------|---------|---------------|
| 0 | Success | — |
| 1 | Unknown error | Bug, environment issue, schema generation failure |
| 2 | RecipeError | YAML syntax, schema violation, invalid `--env-var`, `--dev-allow-unsigned` without companion confirmation flag, `--dev-allow-unsigned` outside `RECOTEM_ENV=development` |
| 3 | DataSourceError | Source-layer failure NOT during HTTP fetch — CSV/Parquet format error, required column missing, local-FS path not found, BigQuery schema mismatch |
| 4 | TrainingError | Includes subcodes `signing_key_missing`, `min_data_violation`, `time_column_parse_error`, `final_training_error`, `no_completed_trials`, `zero_score`, `excessive_per_trial_timeouts` |
| 5 | ArtifactError | Magic mismatch, kid unknown, HMAC mismatch, payload over cap, disallowed FQCN, header JSON over cap |
| 6 | LockContestedError | Recipe lock held by another process when `--fail-on-busy` is set |
| 7 | HttpFetchError | Any failure during HTTP/HTTPS source fetch — SSRF guard refused the destination, connect/read timeout, HTTP 4xx/5xx, body cap exceeded, redirect cap, scheme-changing redirect, sha256 mismatch on a network-fetched source |
| 8 | Configuration error | Missing `RECOTEM_SIGNING_KEYS` (also for `recotem inspect` when signing keys are absent and `--dev-allow-unsigned` not passed), bind port already in use, other env-var misconfiguration |

`--fail-on-busy` surfaces as exit 6, not exit 4 — `LockContestedError` is
raised outside the `TrainingError` hierarchy. Without `--fail-on-busy`
(the default), a lock contention exits 0 with the structured event
`recipe_lock_contended_skipping`. Alert on that event rather than the exit
code when you need visibility into skipped runs without treating them as errors.

On any non-zero exit, `recotem train` emits a single `train_error` JSON log
event with `code=<subcode>` so log aggregators can alert by subcode without
re-parsing exit strings. For non-domain exceptions (bugs, unexpected library
errors) the code field is `internal_error`.

## Training pipeline events

A successful training run emits these structured events in order. Use them
as the basis for SLO and alerting rules.

| Event | Phase | Significant fields |
|-------|-------|--------------------|
| `training_started` | start | `recipe`, `run_id` |
| `fetching_data` | datasource | — |
| `data_fetched` | datasource | `n_rows` |
| `data_cleansed` | cleansing | `n_rows`, `drop_count` |
| `splitting_data` / `split_done` | split | `val_offset` |
| `search_started` | tuning | `algorithms`, `n_trials` |
| `search_done` | tuning | `best_class`, `best_score`, `n_completed` |
| `training_final_model` / `final_model_trained` | refit | `recommender` |
| `artifact_written` | persist | `versioning`, `artifact`, `pointer` (append_sha), `kid` |
| `train_done` | end | `name`, `run_id`, `exit_code`, `artifact`, `best_class`, `best_score`, `trials`, `n_orphaned`, `trained_at`, `kid`, `recipe_hash`, `n_rows`, `n_users`, `n_items` |
| `train_error` | failure | `error`, `code` (`internal_error` for non-domain exceptions), `recipe`, `run_id`, `exit_code`, `trained_at`; additionally `n_rows`, `n_users`, `n_items`, `min_rows`, `min_users`, `min_items` when `code=min_data_violation` |
| `recipe_lock_contended_skipping` | start | `recipe`, `run_id` (default `--fail-on-busy=False` exits 0) |
| `csv_source_redirect`, `csv_source_size_exceeded` | datasource | `path`, `status`, `cap` |
| `metadata_source_redirect`, `metadata_source_size_exceeded` | datasource | `path`, `status`, `cap` |

Operators alerting on `csv_source_redirect` / `csv_source_size_exceeded` should add equivalent alerts for `metadata_source_redirect` / `metadata_source_size_exceeded`. Both event families fire when an HTTP/HTTPS fetch hits a redirect cap or byte cap — the former for the interaction data source, the latter for item-metadata loading.

### Watcher and loader structured-log events

Additional events emitted by the watcher, recipe loader, and size-cap helper that are useful for alerting:

| Event | Level | Emitted by | Significance |
|-------|-------|-----------|--------------|
| `recipe_security_violation_skipped` | ERROR | `recipe/loader.py` lenient loader | A recipe file contains a security-category error (path traversal, disallowed scheme, embedded credentials). The recipe is skipped but the server keeps running. **Alertable** — indicates a misconfigured or potentially hostile recipe file. |
| `recipe_load_error_skipped` | WARN | `recipe/loader.py` lenient loader | A recipe file failed to load for non-security reasons (schema error, YAML parse error). The recipe is skipped. |
| `size_cap_probe_failed` | WARN | `_size_cap.py` | An fsspec `info()` call on an object-store path failed unexpectedly (not `FileNotFoundError` / `PermissionError`). The size cap check was skipped; the subsequent read proceeds but is unbounded by the pre-read cap. Indicates degraded-but-bounded behavior. |
| `auth_anonymous_bypass` | DEBUG | `serving/auth.py` | Every request that passes without an API key (when `RECOTEM_API_KEYS` is empty). Emitted on every request for access-log correlation. |
| `auth_anonymous_bypass_first_seen` | INFO | `serving/auth.py` | First anonymous request from a given `client_host` (per process). The LRU cache tracking first-seen IPs is bounded to 1024 entries to prevent unbounded memory growth. |
| `kid_extraction_failed` | WARN | `serving/watcher.py` | An artifact's kid bytes could not be parsed from the raw bytes (too short, out-of-range length, decode error). The kid shown in subsequent log fields is `\x00<unparseable>` — intentionally not collidable with any real kid. |
| `artifact_stat_timeout` | WARN | `serving/watcher.py` | A stat() future did not complete within the per-future timeout (`min(watch_interval, 30)` seconds). Hung object-store stats no longer block tick progress or delay SIGTERM handling. |

The `train_error` event uses `name=` (not `recipe=`) for the recipe name field and includes `kid=` when the signing kid is known, matching the `train_done` event's field names.

## Concurrent training and persistent search storage

`recotem train` acquires a per-recipe POSIX `flock` at
`<recipe.output.path>.lock` before any work. The lock is **host-local**:
`flock` only coordinates processes on the same host, so when
`output.path` is a remote URI (`s3://`, `gs://`, `http(s)://`, …) the
lock file is created at a host-local path derived from the URI and does
*not* prevent another pod or another node from writing the same artifact
concurrently. Use the scheduler (Kubernetes `concurrencyPolicy: Forbid`,
Argo `synchronization.mutex`, Airflow `max_active_runs=1`, etc.) for
cross-host single-writer guarantees; Recotem logs `recipe_lock_local_only`
on every remote-scheme run so the limitation is visible.

Defaults:

- Non-blocking: a contended lock returns immediately and the run exits 0
  with `recipe_lock_contended_skipping` (cron-friendly: a slow run cannot
  pile up overlapping jobs).
- `--fail-on-busy` flips this to exit 6 (`LockContestedError`) so an
  orchestrator can route the work elsewhere. `LockContestedError` is
  intentionally outside the `TrainingError` hierarchy — it is an
  orchestration condition, not a training failure.
- `--no-lock` skips lock acquisition entirely. Only safe when you guarantee
  no concurrent writers via some other mechanism.

For multi-process Optuna search (parallelism on a single host or a
distributed cluster), set `training.storage_path` in the recipe. Accepted
forms: a bare path → SQLite, or a URL beginning with `sqlite://`,
`postgresql://`, `postgres://`, or `mysql://`. Recotem opens the study
with `load_if_exists=True` so multiple `recotem train` invocations against
the same recipe converge on a shared trial pool rather than duplicating
work.
The study name is `recotem_<recipe.name>_<run_id>` and `run_id` is a
fresh random hex per `recotem train` invocation, so by default each call
opens a fresh study. To resume a study across processes, share the same
`storage_path` and invoke `recotem.training.run_training(...)` directly
from a wrapper script that pins `run_id`.

## Atomic write guarantees

`recotem train` writes artifacts via a tempfile in the same directory,
`fsync()`s the data, then `os.replace()`s — POSIX-atomic on local FS so
readers never see a partial file. On object stores (S3 / GCS / Azure)
the artifact is written with `put_object` semantics (last-write-wins);
in `versioning: append_sha` mode the immutable sha-suffixed object is
written first, then the small pointer object is overwritten. A reader
that opens the pointer mid-rotation sees either the old or the new
target name, never a partial pointer.

## SIGTERM / drain sequence

When uvicorn receives `SIGTERM` (or `SIGINT`):

1. uvicorn stops accepting new connections.
2. The FastAPI lifespan exits: `ArtifactWatcher.stop()` is called and the
   poll thread exits on its next tick (≤ `RECOTEM_WATCH_INTERVAL` seconds);
   the recurring `--insecure-no-auth` / `--dev-allow-unsigned` warning task
   is cancelled.
3. In-flight requests are given up to `RECOTEM_DRAIN_SECONDS` (default 30)
   to complete; uvicorn then closes remaining connections.
4. A final `serve_shutdown` event is logged with `drain_seconds`.

For Kubernetes, set `terminationGracePeriodSeconds` ≥ `RECOTEM_DRAIN_SECONDS + 5`
to allow the watcher tick plus the drain window before SIGKILL.

## Sizing `recotem serve` memory

Each model replica holds every loaded model in RAM. Plan accordingly.

| Factor | Impact |
|--------|--------|
| `RECOTEM_MAX_ARTIFACT_BYTES` | Hard cap per artifact file (default 2 GiB, clamped [1 MiB, 16 GiB]). Reduce this if you have many small models. |
| `RECOTEM_MAX_PAYLOAD_BYTES` | Cap on the deserialised payload per artifact (default 512 MiB, post-HMAC-verify). Must be ≤ `RECOTEM_MAX_ARTIFACT_BYTES`; if not, `recotem serve` fails at startup with `ConfigError` (exit 8). Reduces the memory spike from deserialization relative to the raw file size. |
| Number of recipes | Each recipe loads one model. 10 recipes × 500 MiB = 5 GiB baseline. |
| Number of replicas | Each replica is independent. 2 replicas = 2× memory. |
| Item metadata | DataFrame in-memory per recipe. Size ≈ rows × columns × 8 bytes. |

Rough formula:

```
RAM per pod ≈ (avg_artifact_size_GiB × n_recipes) + (avg_metadata_size_GiB × n_recipes) + 1 GiB OS overhead
```

For large models (IALS with many components, large item sets), use `recotem inspect` to read `data_stats` and `best_params` from the header before committing to a host size.

`recotem serve` is sized for ≤ 100 recipes per process. Beyond that, shard recipes across multiple `serve` processes (separate `--recipes` directories, separate ports, load-balance at the proxy layer).

---

## Environment variable reference

Full list of environment variables recognised by Recotem. Variables marked `serve` apply only to `recotem serve`; those marked `train` apply only to `recotem train`; those with no marking apply to both.

| Variable | Default | Scope | Description |
|---|---|---|---|
| `RECOTEM_SIGNING_KEYS` | (required) | train + serve | `kid:hex64,kid2:hex64` — HMAC sign/verify keys (64 hex = 32 bytes). Multi-entry enables zero-downtime rotation; `train` always signs with the **first** entry. |
| `RECOTEM_API_KEYS` | (empty) | serve | `kid:sha256:hex64,...` — API key allow-list. Empty forces 127.0.0.1 bind. |
| `RECOTEM_HOST` | 127.0.0.1 | serve | uvicorn bind host. Must be `0.0.0.0` inside Docker/Kubernetes when `RECOTEM_API_KEYS` is set. Forced to `127.0.0.1` when no API keys are configured (a `host_forced_to_loopback` warning is emitted). |
| `RECOTEM_PORT` | 8080 | serve | uvicorn bind port. |
| `RECOTEM_WATCH_INTERVAL` | 5 | serve | Artifact watcher poll interval in seconds (clamped 1–30). |
| `RECOTEM_MAX_ARTIFACT_BYTES` | 2 GiB | serve | Per-artifact size cap (clamped [1 MiB, 16 GiB]). |
| `RECOTEM_MAX_PAYLOAD_BYTES` | 512 MiB | serve | Per-payload cap post-HMAC-verify (clamped [1 MiB, 16 GiB]). Must be ≤ `RECOTEM_MAX_ARTIFACT_BYTES`. |
| `RECOTEM_MAX_DOWNLOAD_BYTES` | 256 MiB | train | Raw I/O bytes cap for HTTP/HTTPS, local, and object-store source reads (clamped [1 MiB, 16 GiB]). Does **not** cap the decompressed DataFrame. |
| `RECOTEM_HTTP_TIMEOUT_SECONDS` | 30 | train | Connect/read timeout for HTTP/HTTPS source fetch (clamped [1, 600]). |
| `RECOTEM_HTTP_ALLOW_PRIVATE` | (unset) | train | Truthy (`1`/`true`/`yes`/`on`) allows HTTP fetches to private/loopback/link-local destinations. Leave unset in production to block SSRF against cloud-metadata services. |
| `RECOTEM_ALLOWED_HOSTS` | 127.0.0.1,localhost | serve | `TrustedHostMiddleware` allow-list (comma-separated). Whitespace-only input falls back to default. |
| `RECOTEM_ALLOWED_ORIGINS` | (empty) | serve | CORS allow-list (comma-separated). Empty = deny. |
| `RECOTEM_ENV` | (empty) | serve | Deployment environment tag. `--insecure-no-auth` is permitted only when set to `development`, `dev`, or `test`; `--dev-allow-unsigned` only when set to `development`. When set to `production`, `prod`, or `staging`, the `/docs`, `/redoc`, and `/openapi.json` endpoints are disabled. |
| `RECOTEM_DRAIN_SECONDS` | 30 | serve | SIGTERM graceful drain window (clamped [1, 300]). Set `terminationGracePeriodSeconds` ≥ this + 5 in Kubernetes. |
| `RECOTEM_LOG_FORMAT` | auto | train + serve | `auto` / `json` / `console`. |
| `RECOTEM_METADATA_FIELD_DENY` | (empty) | serve | Comma-separated columns stripped from `/predict` responses after the metadata join. |
| `RECOTEM_METRICS_ENABLED` | (unset) | serve | Truthy enables the Prometheus `/metrics` endpoint. Requires `recotem[metrics]` extra. |
| `RECOTEM_ARTIFACT_ROOT` | (empty) | train | Local `output.path` must lie under this directory (symlink escapes rejected). |
| `RECOTEM_LOCK_DIR` | (empty) | train | Override directory for per-recipe training lock files. Needed when `output.path` is a remote URI (`s3://`, `gs://`, …); falls back to `<tempdir>/recotem-locks/`. |
| `RECOTEM_STARTUP_PARALLELISM` | (auto) | serve | Threads used to load artifacts at startup (clamped [1, 32]). Default: `min(len(recipes), 8)`. Setting to `0` clamps to 1 with a warning. |
| `RECOTEM_BQ_REQUIRE_STORAGE_API` | (unset) | train | Truthy raises `DataSourceError` instead of falling back to the REST path when the BigQuery Storage Read API fails. |
| `RECOTEM_RECIPE_*` | — | train | Allow-listed prefix for `${...}` recipe env-var expansion. See [recipe-reference.md](recipe-reference.md#environment-variable-expansion). |

> **Note on `signing_key_status` in logs.** The `security.posture` log line emitted at every `recotem serve` startup includes a `signing_key_status` field: `configured` (keys present), `dev_allow_unsigned` (no keys, dev-unsigned mode), or `missing` (keys absent; startup will fail). Use this in SIEM rules to alert on misconfigured deployments.

---

## SLOs

Recotem does not enforce SLOs internally. Recommended baseline targets for production:

| Metric | Target |
|--------|--------|
| `/predict/{name}` p99 latency | < 50 ms (pure recommender, no metadata join) |
| `/health` p99 latency | < 5 ms |
| Availability (per recipe) | Measure via `recotem_model_loaded{recipe}` Prometheus gauge |
| Artifact hot-swap time | ≤ `RECOTEM_WATCH_INTERVAL` + model load time |
| Train-to-serve lag | Schedule train; serve detects in ≤ `RECOTEM_WATCH_INTERVAL` seconds |

Enable Prometheus metrics:

```bash
pip install "recotem[metrics]"
```

The `/metrics` endpoint is opt-in and off by default. Set `RECOTEM_METRICS_ENABLED` to a truthy value (`1`, `true`, `yes`, `on`) to activate.

> **Network exposure.** Both `/metrics` and `/health` are unauthenticated by
> design — the same posture Prometheus and Kubernetes liveness/readiness
> probes expect. The endpoints surface recipe names, kid IDs, load-error
> strings, model-load timestamps, and predict-latency histograms.
> **Restrict them with the cluster's NetworkPolicy** (`/metrics` to the
> Prometheus namespace, `/health` to kubelet probes) rather than relying
> on the API-key middleware. The `helm/recotem` chart's NetworkPolicy
> template ships with a deny-all baseline; allow only the scrapers and
> probes you actually need.

Available metrics:

| Metric | Type | Labels |
|--------|------|--------|
| `recotem_predict_total` | Counter | `recipe`, `status` |
| `recotem_predict_latency_seconds` | Histogram | `recipe` |
| `recotem_model_loaded` | Gauge | `recipe` |
| `recotem_artifact_load_failures_total` | Counter | `recipe` |
| `recotem_active_recipes` | Gauge | — |
| `recotem_swap_total` | Counter | `recipe`, `result` |
| `recotem_artifact_stat_failures_total` | Counter | `recipe` |
| `recotem_watcher_unhandled_errors_total` | Counter | — |
| `recotem_metadata_lookup_errors_total` | Counter | `recipe` |
| `recotem_recipe_rescan_errors_total` | Counter | `recipe` |
| `recotem_bigquery_storage_fallback_total` | Counter | `reason` |
| `recotem_recipes_dir_scan_failures_total` | Counter | `error_class` |

---

## Watcher and registry semantics

`ArtifactWatcher` runs as a daemon thread inside the serve process:

- Polls every `RECOTEM_WATCH_INTERVAL` seconds (clamped 1–30) with ±10%
  jitter. Up to 16 stat() calls are issued in parallel via a thread pool.
  Each parallel stat() future is subject to a per-future timeout of
  `min(RECOTEM_WATCH_INTERVAL, 30)` seconds so a hung object-store stat
  (e.g. S3 TCP blackhole) cannot block the entire tick. Timed-out futures
  emit `artifact_stat_timeout` (WARN) and the recipe is marked with a
  load error until the next successful poll.
- On `recotem serve` shutdown (SIGTERM), `ArtifactWatcher.stop()` calls
  `executor.shutdown(wait=False, cancel_futures=True)` so queued-but-not-
  started futures are discarded immediately. In-flight OS-level I/O (e.g.
  a `fs.info()` waiting for a TCP response) is not interruptible but no
  new work is queued, allowing the process to exit promptly after the
  `RECOTEM_DRAIN_SECONDS` window.
- A change is detected from the artifact pointer's mtime/size (local FS) or
  ETag/VersionId (object stores). When the marker changes the watcher reads
  the full bytes once, computes sha256, and **only reloads if the sha256
  also changed** — so replacing a file with identical content bumps mtime
  but does not trigger an unnecessary swap.
- Recipes directory is rescanned each tick: new `*.yaml` files trigger
  `recipe_discovered` + an immediate forced load; removed files trigger
  `recipe_removed` and the entry is dropped from the registry.
- On any failure during reload (`artifact_load_failed`,
  `artifact_load_unexpected_error`), the existing entry remains served and
  its `last_load_error` field is set so `/health` shows the staleness while
  `/predict` continues to return the previous good model.
- On `_stat_marker` returning None (file disappeared), the existing entry
  keeps serving and an `artifact_disappeared` warning is logged once.

### Initial load failure

When an artifact fails to load at startup the recipe is still registered as
a stub (`loaded=false`, `error=<reason>`). The server starts, `/health`
reports `degraded`, and `/predict/{name}` returns 503. This is intentional:
a partial outage is recoverable by retraining without restarting the
process.

The startup-only event variants are:

| Event | Trigger |
|-------|---------|
| `initial_artifact_read_failed` / `initial_artifact_read_error` | I/O failure or cap exceeded |
| `initial_artifact_parse_failed` | Magic / version / header structural error |
| `initial_artifact_hmac_failed` | HMAC mismatch or unknown kid |
| `initial_artifact_deserialize_failed` | FQCN allow-list rejection or payload decode error |
| `initial_artifact_hmac_skipped_dev` | `--dev-allow-unsigned` |

## Backups and disaster recovery

Artifacts are self-contained, signed binaries — back them up like any other
binary asset:

- **Local FS**: snapshot the artifact root (or the directory containing
  every recipe's `output.path`). `versioning: append_sha` preserves prior
  versions automatically; the pointer file is the only mutable bit.
- **Object stores**: enable bucket versioning. Combined with `append_sha`
  this gives you immutable per-train-run history.
- **Recipes**: commit the recipes directory to version control. Together
  with `RECOTEM_SIGNING_KEYS` (stored separately in a secrets manager),
  the recipe + key reproduce any artifact via `recotem train`.

After a host failure, restoring `recotem serve` requires only the recipes
directory and the signing keys. Re-run training to regenerate any missing
artifacts; the watcher picks them up without restart.

## Monitoring SLIs

The high-signal metrics for production alerting:

| Signal | Source | Alert threshold (suggested) |
|--------|--------|-----------------------------|
| Recipe is unloaded | `recotem_model_loaded{recipe=...} == 0` for > `RECOTEM_WATCH_INTERVAL × 3` | page on-call |
| Hot-swap failures | `rate(recotem_swap_total{result="error"}[5m]) > 0` | warn |
| Artifact load failures since restart | `recotem_artifact_load_failures_total{recipe=...}` increase | warn (often paired with the unloaded alert above) |
| Artifact stat failures (watcher poll) | `recotem_artifact_stat_failures_total{recipe=...}` increase | warn |
| Watcher unhandled errors | `recotem_watcher_unhandled_errors_total` increase | warn |
| Predict error rate | `rate(recotem_predict_total{status="error"}[5m]) / rate(recotem_predict_total[5m])` | warn at 1%, page at 10% |
| Predict latency | `histogram_quantile(0.99, recotem_predict_latency_seconds_bucket)` | per-recipe SLO |
| Active recipes | `recotem_active_recipes` drop > 0 since last scrape | warn (recipe removed or all stub) |
| BigQuery Storage API fallback | `rate(recotem_bigquery_storage_fallback_total{reason="api_error"}[5m]) > 0` | warn — grant `bigquery.readSessions.create` to restore fast path |
| Recipes-dir scan failures | `rate(recotem_recipes_dir_scan_failures_total[5m]) > 0` | warn — broken recipe YAML or artifact path; check `error_class` label for `RecipeError` (schema), `OSError` (permissions), or `sidecar_stale` (artifact read failed after sidecar change) |

Pair these with the structured log events `artifact_load_failed`,
`artifact_disappeared`, `recipe_not_loaded_at_startup`, `auth_invalid_key`
for context on the underlying cause.

## Upgrades

Recotem follows semver. Within a major version (`2.x`):

- Recipes remain valid; the recipe loader is backward-compatible.
- The artifact format version is `1`. Older readers refuse newer formats
  with `unsupported format version`. When the format bumps, retrain after
  upgrading the writer; readers can be upgraded first.
- The FQCN allow-list is frozen per release; changes appear in the
  CHANGELOG. Re-train if your artifacts encode a class that has been
  removed.

For zero-downtime upgrade of the serve fleet, deploy new pods with both
the old and new signing kids configured (rotation-style), let new pods
become healthy, then drain old pods (relying on `RECOTEM_DRAIN_SECONDS`).

## Troubleshooting

### `recotem serve` starts but recipe is `loaded: false`

```bash
curl http://localhost:8080/health | jq '.recipes'
```

```json
{"my_recipe": {"loaded": false, "error": "signature mismatch"}}
```

Causes and fixes:

| Error | Cause | Fix |
|-------|-------|-----|
| `signature mismatch` | Artifact signed with a key not in `RECOTEM_SIGNING_KEYS` | Add the signing kid used at train time |
| `unknown kid: prod-old` | The kid in the artifact is not in the server's key list | Add that kid or retrain with a known kid |
| `magic bytes mismatch` | Corrupt or truncated artifact | Retrain |
| `payload exceeds max bytes` | Payload exceeds `RECOTEM_MAX_PAYLOAD_BYTES` (512 MiB default) or artifact exceeds `RECOTEM_MAX_ARTIFACT_BYTES` (2 GiB default) | Increase the relevant cap or reduce model size. Note: `RECOTEM_MAX_PAYLOAD_BYTES` must remain ≤ `RECOTEM_MAX_ARTIFACT_BYTES`. |
| `header JSON too large` | Malformed artifact | Retrain |

### `recotem train` exits 3 (DataSourceError)

For BigQuery: run `gcloud auth application-default print-access-token` to confirm ADC is working. Check the exact error in the JSON stderr line:

```bash
recotem train recipe.yaml 2>&1 | grep '"event":"train_error"' | jq .
```

#### BigQuery Storage Read API fallback

When the service account lacks `bigquery.readSessions.create`, the BigQuery source logs a `bigquery_storage_fallback` warning and falls back to the slower REST API. Monitor for this event in your log aggregator — sustained fallbacks indicate a missing IAM permission.

To grant the permission:

```bash
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:<SA>@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/bigquery.readSessionUser"
```

To disable the fallback and surface the error instead, set `RECOTEM_BQ_REQUIRE_STORAGE_API=1`. When set, a `PermissionDenied` from the Storage Read API raises `DataSourceError` (exit 3) rather than silently retrying via REST.

### `recotem train` exits 4 with `min_data_violation`

The cleaned dataset fell below a threshold. The JSON error line includes observed counts:

```json
{"event": "train_error", "code": "min_data_violation", "n_rows": 842, "min_rows": 1000, ...}
```

Lower `cleansing.min_rows` in the recipe or investigate why fewer rows arrived from the source.

### `recotem train` exits 4 with `zero_score`

All Optuna trials scored 0.0. Common causes:

- The split produced an empty test set (too few users or interactions). Try `split.scheme: random` or lower `split.heldout_ratio`.
- The data after cleansing has too few items for the cutoff. Lower `training.cutoff`.

### 401 on `/predict`

- Trailing or leading whitespace in the `X-API-Key` header is treated as part of the key and will not match. Trim client-side.
- Confirm the hash in `RECOTEM_API_KEYS` was produced by `recotem keygen --type api` for the plaintext you are sending. The wire prefix is `sha256:` but the digest is **scrypt** (`hashlib.scrypt(plaintext, salt=b"recotem.api-key.v1", n=2, r=8, p=1, dklen=32)`). A plain `sha256(plaintext)` will not match.

### 503 on `/predict/{name}`

The recipe is unhealthy (`loaded: false`). See `/health` for the error. Usually a signing mismatch or corrupt artifact.

### 404 on `/predict/{name}`

The `user_id` in the request was not present in training data. This is expected for new users. Handle it in your application layer (fall back to popularity-based recommendations, for example).

### Watcher does not pick up new artifact

- Check `RECOTEM_WATCH_INTERVAL`. Default is 5 s.
- For object stores, check that the IAM role on the serve process has `GetObject` (S3) or `storage.objects.get` (GCS) on the artifact bucket.
- Run `recotem inspect` on the artifact path to confirm it is valid and signed with a kid the server knows. `recotem inspect` accepts both local paths and fsspec URIs (e.g. `s3://bucket/key.recotem`, `gs://bucket/key.recotem`).

### Log redaction

All log events are processed by the redaction processor before output. If you see `[REDACTED]` in a log line where you expected a value, the field name matched the redaction pattern (see [security.md](security.md#log-redaction)). This is intentional.
