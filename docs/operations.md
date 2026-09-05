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

   Restart `recotem serve`. Any artifact still signed with the old kid will fail to load and will show up as `loaded: false` in `/v1/health/details`. Retrain those recipes.

   Confirm all recipes loaded successfully. Per-recipe state lives behind the authenticated `/v1/health/details` endpoint — the public `/v1/health` returns only `{status, total, loaded}` aggregates (plus `skipped` when any recipe file is unparseable — see [Unparseable recipe files](#unparseable-recipe-files)), not the `recipes` map:

   ```bash
   # -f / --fail returns exit 22 on 4xx/5xx, which would mask a 503.
   # Use -w to capture the status code instead.
   HTTP_STATUS=$(curl -s -o /tmp/health.json -w "%{http_code}" \
     -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
     http://localhost:8080/v1/health/details)
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

The server continues running and returns 503 (`RECIPE_UNAVAILABLE`) for
that recipe's `/v1/recipes/{name}:recommend` (and sibling verbs)
endpoints.

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
   curl -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
     http://localhost:8080/v1/health/details | jq '.recipes.my_recipe'
   # {"loaded": true, ...}
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
| 2 | RecipeError | YAML syntax, schema violation, invalid `--env-var` |
| 3 | DataSourceError | Source-layer failure NOT during HTTP fetch — CSV/Parquet format error, required column missing, local-FS path not found, BigQuery schema mismatch, `sha256` mismatch on a local or object-store path, and every DSN the SQL source's SSRF guard refuses (private/loopback host, unresolvable host, `?service=`, `?unix_socket=`, absolute-path host, no host) |
| 4 | TrainingError | Includes subcodes `signing_key_missing`, `min_data_violation`, `time_unit_required`, `time_column_parse_error`, `cutoff_exceeds_item_count`, `split_error`, `search_error`, `unknown_algorithm`, `final_training_error`, `no_completed_trials`, `zero_score`, `excessive_per_trial_timeouts`, `feature_table_error`, `feature_axis_error`, `feature_cholesky_error`. A `TrainingError` carrying no more specific code reports the generic `training_error` |
| 5 | ArtifactError | Magic mismatch, kid unknown, HMAC mismatch, payload over cap, disallowed FQCN, header JSON over cap. A **malformed** `RECOTEM_SIGNING_KEYS` value is exit 8, not 5 — the artifact is fine, the environment is not |
| 6 | LockContestedError | Recipe lock held by another process when `--fail-on-busy` is set |
| 7 | HttpFetchError | Any failure during HTTP/HTTPS source fetch — SSRF guard refused the destination, connect/read timeout, HTTP 4xx/5xx, body cap exceeded, redirect cap, scheme-changing redirect, sha256 mismatch on a network-fetched source. Only an `http://` / `https://` fetch reports 7; the same checks on other schemes report 3 |
| 8 | Configuration error | Missing `RECOTEM_SIGNING_KEYS` (also for `recotem inspect` when signing keys are absent and `--dev-allow-unsigned` not passed), a malformed `RECOTEM_SIGNING_KEYS` entry (non-hex, wrong length, no separator, empty kid, empty key) on `train` / `serve` / `inspect`, bind port already in use, an unwritable local `output.path` (the per-recipe lock cannot be created) or a remote one that cannot be written — credentials that do not resolve (`code=artifact_write_credentials`) and a missing bucket/container or refused credentials (`code=artifact_write_destination`), other env-var misconfiguration such as `RECOTEM_MAX_PAYLOAD_BYTES` exceeding `RECOTEM_MAX_ARTIFACT_BYTES`, `--dev-allow-unsigned` without its companion confirmation flag, `--dev-allow-unsigned` outside `RECOTEM_ENV=development` |

`--fail-on-busy` surfaces as exit 6, not exit 4 — `LockContestedError` is
raised outside the `TrainingError` hierarchy. Without `--fail-on-busy`
(the default), a lock contention exits 0 with the structured event
`recipe_lock_contended_skipping`. Alert on that event rather than the exit
code when you need visibility into skipped runs without treating them as errors.

On any non-zero exit, `recotem train` emits a single `train_error` JSON log
event with `code=<subcode>` so log aggregators can alert by subcode without
re-parsing exit strings. For non-domain exceptions (bugs, unexpected library
errors) the code field is `internal_error` and the event carries the
traceback. The subcode is read only from recotem's own exception types, so a
third-party exception that happens to define its own `code` attribute (for
example SQLAlchemy's documentation-shortlink slug) is still reported as
`internal_error`.

## Training pipeline events

A successful training run emits these structured events in order. Use them
as the basis for SLO and alerting rules.

| Event | Phase | Significant fields |
|-------|-------|--------------------|
| `training_started` | start | `recipe`, `run_id` |
| `fetching_data` | datasource | — |
| `data_fetched` | datasource | `n_rows` |
| `feature_table_loaded` | features | `side`, `n_rows`, `n_features`, `columns` (names only — feature values are user PII and are never logged). Only with a `features:` block; emitted before cleansing, since the feature tables are fetched up front. |
| `data_cleansed` | cleansing | `n_rows`, `drop_count` |
| `splitting_data` / `split_done` | split | `val_offset` |
| `feature_axis_coverage` | features | `side`, `matched`, `total` — how many ids of the axis being encoded the feature table covers. Emitted per side per phase (once for search, once for the final refit). Zero coverage does not emit this event; it aborts with `feature_axis_error` instead. |
| `search_started` | tuning | `algorithms`, `n_trials` |
| `search_done` | tuning | `best_class`, `best_score`, `n_completed` |
| `training_final_model` / `final_model_trained` | refit | `recommender` |
| `artifact_written` | persist | `versioning`, `artifact`, `pointer` (append_sha), `kid`, `payload_bytes`, `artifact_bytes` |
| `artifact_payload_exceeds_serve_cap` | persist | WARN. `payload_bytes`, `max_payload_bytes`, `env_var`. The artifact was written, but its payload is larger than `RECOTEM_MAX_PAYLOAD_BYTES` **as resolved on the training host**, so a `recotem serve` configured the same way will refuse it (`ArtifactError`, exit 5) and never become ready. See [Sizing `recotem serve` memory](#sizing-recotem-serve-memory). |
| `artifact_size_exceeds_serve_cap` | persist | WARN. `artifact_bytes`, `max_artifact_bytes`, `env_var`. Same, for the outer container against `RECOTEM_MAX_ARTIFACT_BYTES`. |
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
| `recipe_load_error_skipped` | WARN | `recipe/loader.py` lenient loader | A recipe file failed to load for non-security reasons (schema error, YAML parse error). The recipe is skipped — see [Unparseable recipe files](#unparseable-recipe-files). Logged once on transition, not on every watcher tick. |
| `size_cap_probe_failed` | WARN | `_size_cap.py` | An fsspec `info()` call on an object-store path failed unexpectedly (not `FileNotFoundError` / `PermissionError`). The size cap check was skipped; the subsequent read proceeds but is unbounded by the pre-read cap. Indicates degraded-but-bounded behavior. |
| `auth_anonymous_bypass` | DEBUG | `serving/auth.py` | Every request that passes without an API key (when `RECOTEM_API_KEYS` is empty). Emitted on every request for access-log correlation. The `mode` field distinguishes `"insecure_no_auth"` (explicit flag) from `"loopback_no_keys"` (no keys configured). |
| `auth_anonymous_bypass_first_seen` | INFO | `serving/auth.py` | First anonymous request from a given `client_host` (per process). The LRU cache tracking first-seen IPs is bounded to 1024 entries to prevent unbounded memory growth. |
| `kid_extraction_failed` | WARN | `serving/watcher.py` | An artifact's kid bytes could not be parsed from the raw bytes (too short, out-of-range length, decode error). The kid shown in subsequent log fields is `\x00<unparseable>` — intentionally not collidable with any real kid. |
| `artifact_stat_timeout` | WARN | `serving/watcher.py` | A stat() future did not complete within the per-future timeout (`min(watch_interval, 30)` seconds). Hung object-store stats no longer block tick progress or delay SIGTERM handling. |
| `recommender_layout_unexpected` | WARN | `serving/routes.py` | `_resolve_recommend` / `_resolve_recommend_related` encountered an `AttributeError` on `recommender._mapper.user_id_to_index` / `item_id_to_index`. The request is treated as `INTERNAL_ERROR`. Increment counter: `recotem_recommender_layout_unexpected_total`. |
| `set_load_error_no_entry` | WARN | `serving/watcher.py` | The watcher tried to mark a load error on a recipe with no registry entry. Counter: `recotem_watcher_state_divergence_total`. |
| `sidecar_disappeared` | WARN | `serving/watcher.py` | A `.sha256` sidecar file was present on the previous poll but raised ENOENT on the current read — emitted once per disappearance transition. |
| `metadata_index_row_error` | WARN | `metadata/loader.py` | A per-row exception occurred during `build_metadata_index`. The row is skipped. Counted by `recotem_metadata_index_build_errors_total{recipe}`. |

The `train_error` event uses `name=` (not `recipe=`) for the recipe name field and includes `kid=` when the signing kid is known, matching the `train_done` event's field names.

> **Note.** Metadata enrichment is indexed at artifact-load time.
> Use `recotem_metadata_index_build_errors_total{recipe}` for load-time
> per-row build failures and `recotem_metadata_serialization_errors_total{recipe,verb}`
> for request-time per-item serialization failures.  When per-item
> metadata enrichment fails at request time, the item is served with
> `item_id` and `score` only (fallback) or dropped; the
> `X-Recotem-Items-Degraded` response header indicates how many items
> were degraded, and `recotem_v1_metadata_degraded_items_total{kind}` counts
> them by kind (`fallback` / `dropped`).

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
`postgresql+psycopg://`, or `mysql+pymysql://`. The `+driver` suffix is
required: a bare `postgresql://` routes to the uninstalled `psycopg2`, and
`postgres://` is a dialect SQLAlchemy 2.x removed. Recotem pre-flights the
value — dialect, then the DBAPI the URL actually routes to — and refuses an
unusable one with **exit 8** (`storage_path_unusable`), naming the spelling
that works. `recotem validate` runs the identical check and prints
`Optuna storage: OK (<dialect>, driver '<driver>')`, so a broken study
backend is caught before the dataset is fetched. Without that pre-flight the
failure surfaced from inside Optuna in `run_search`, which runs *after* fetch,
cleansing and split — on a BigQuery- or SQL-backed recipe the scan was already
billed — and reached the shell as an unmapped **exit 1**, which supervisor and
CronJob retry logic reads as an unknown crash worth retrying. Recotem opens the study
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
| `RECOTEM_MAX_BODY_BYTES` | Hard cap on each HTTP **request** body (default 128 MiB, clamped [1 MiB, 2 GiB]). A `413 PAYLOAD_TOO_LARGE` is returned before Starlette buffers/parses the body, so **no single request** can make the process allocate more than the cap. It bounds one request, not the process: nothing limits how many such requests are in flight at once, so one authenticated client sending them concurrently can still reach multiple GB — see [Concurrent request bodies are unbounded](#concurrent-request-bodies-are-unbounded) below. The default clears the largest single-verb request — `:recommend-related` tops out near 52 MiB with maximal cold-start feature mappings — with headroom. It deliberately does **not** clear the largest schema-valid *batch* body: once `user_features` / `item_features` are filled to their per-field caps, `:batch-recommend` tops out near 196 MiB and `:batch-recommend-related` near 13 GiB, the latter beyond even the 2 GiB clamp. Such bodies are refused with `413`; raise the cap if you genuinely send batches that large. Reduce it if your legitimate batch sizes are small and you want a tighter bound; the cap applies both to a declared `Content-Length` and to chunked bodies with no length header. |
| Number of recipes | Each recipe loads one model. 10 recipes × 500 MiB of **artifact** is ~24 GiB resident, not 5 GiB — see the multiplier below. |
| Number of replicas | Each replica is independent. 2 replicas = 2× memory. |
| Item metadata | DataFrame in-memory per recipe. Size ≈ rows × columns × 8 bytes. |

Rough formula:

```
RAM per pod ≈ (4.8 × avg_artifact_size_GiB × n_recipes) + (avg_metadata_size_GiB × n_recipes) + 0.25 GiB process baseline
```

**A loaded artifact costs several times its size on disk, not its size on disk.**
An earlier revision of this page counted it at 1× and added a flat 1 GiB, which
is conservative only while the models are small enough for that constant to
dominate. Measured, one recipe, no item metadata, `RECOTEM_MAX_PAYLOAD_BYTES`
raised where needed:

| interactions | users × items | artifact on disk | serve RSS once ready | old formula |
|---|---|---|---|---|
| 100k | 5,000 × 1,000 | 1.4 MiB | 228 MiB | 1,025 MiB |
| 1M | 50,000 × 5,000 | 55.9 MiB | 488 MiB | 1,080 MiB |
| 10M | 500,000 × 50,000 | 644.5 MiB | 3,292 MiB | 1,668 MiB |

The three points fit `RSS ≈ 4.8 × artifact + 0.22 GiB`. The old formula crosses
from over- to under-estimating at roughly a **213 MiB** artifact, and at 644 MiB
it predicts half the true figure — a direction that shows up in production as an
OOMKill during startup, not as a slow response.

Where the multiplier comes from, measured in-process on the 644.5 MiB artifact
above:

| after | RSS |
|---|---|
| interpreter + imports | 39 MB |
| `read_artifact` returns | 1,328 MB |
| payload deserialized | 2,793 MB |

`read_artifact` holds the whole file *and* the payload slice of it at once
(`payload = resolved_data[header.payload_offset:]` copies), so the raw bytes are
resident twice — 2 × 645 MB is the entire step from 39 to 1,328 MB. The
deserialized model is then a third copy's worth on top. Dropping the payload
bytes afterwards does not return the memory to the OS in the same process, so
**size the container on this figure, not on a steady state you hope to settle
into**.

`RECOTEM_MAX_PAYLOAD_BYTES` bounds this, which is what it is for — but it bounds
it at ~4.8× the cap, not at the cap. At the 512 MiB default, plan for roughly
2.5 GiB of resident memory per recipe that actually reaches the cap.

For large models (IALS with many components, large item sets), use `recotem inspect` to read `data_stats` and `best_params` from the header before committing to a host size. Note that `n_components` — the term that dominates artifact size, since the factor matrices are `(n_users + n_items) × n_components × 4` bytes — is an Optuna-searched parameter over irspack's `[4, 300]`, so it is a property of the run rather than of the recipe: the same recipe on the same data can produce a materially different artifact size on the next train. `recotem train` logs `artifact_bytes` on `artifact_written` for every run.

> Measured on macOS/arm64 (16 cores, 128 GB). The double-resident raw bytes are
> arithmetic and hold on any platform; whether a freed buffer is returned to the
> OS is allocator-specific, so on Linux the settling behaviour may differ. The
> peak is the same either way.

`recotem serve` is sized for ≤ 100 recipes per process. Beyond that, shard recipes across multiple `serve` processes (separate `--recipes` directories, separate ports, load-balance at the proxy layer).

---

## Feature-aware iALS sizing

A recipe's [`features:`](recipe-reference.md#features) block adds costs that
scale differently from the rest of a recotem recipe. All four points below
apply only when `features:` is present.

### Vocabulary scales with catalog size, not interaction count

The most surprising operational property of this feature: the encoded
dimension is built from the **whole fetched feature table**, not from the
subset of items/users that actually appear in the interaction data — this is
what lets a cold-start item or user be scored at serve time even though it
never appears in training. The consequence is that a 1M-item catalog whose
interactions cover only 1,000 of those items still pays the full encoded
dimension — and the full per-trial training cost below — for the other
999,000 items, even though their columns are only ever useful for cold-start
requests that may never arrive.

`RECOTEM_MAX_FEATURE_DIM` (default 5000, clamped [16, 100000]) caps the
encoded dimension per side (item and user are checked independently). The
encoded dimension is the sum of every column's width **plus one** for the
implicit bias column, so a single `categorical` column with 5,000 distinct
values encodes to 5,001 and is refused by one under the default — the largest
cardinality that fits is 4,999. Exceeding the cap raises `TrainingError`
(exit 4) at the point the encoder state is built. `min_frequency` (recipe-level, per column; see
[recipe-reference.md](recipe-reference.md#features)) is the operator's
**only** lever against this cap — raise it on high-cardinality `categorical` /
`multi_label` columns to shrink the vocabulary. There is no way to restrict
the vocabulary to interaction-covered rows from the recipe.

Be precise about what that lever moves: `min_frequency` bounds the resulting
**dimension**, not the memory spent discovering it. `_vocabulary` counts every
token of the fetched column into a dict and only then prunes, and the
`multi_label` branch first flattens every row's tokens into a single list, so
a high-cardinality column pays its full transient counting cost no matter how
aggressive `min_frequency` is — a column with hundreds of thousands of
distinct values costs tens of MB to count even when the pruned vocabulary
comes back empty. The `RECOTEM_MAX_FEATURE_DIM` check runs **after** every
column's vocabulary is built, so that transient is paid in full even on the
run the cap then rejects. `min_frequency` protects the trials; it does not
protect the encoder-state build.

Raising `min_frequency` to clear the cap has a cost the dimension number does
not show: every value it prunes takes its rows' signal with it. Those rows
encode to an all-zero block for that column and become indistinguishable from
each other on that axis. When pruning leaves **20% or more** of a column's rows
with no signal, training logs

```
feature_vocabulary_pruned  column=category min_frequency=5 distinct_values=41
  kept_values=1 rows_without_signal=40 n_rows=50
```

Treat it as a real warning, not noise: a column that keeps only its head value
still varies across rows — so it is not "dead" and the
`feature_empty_vocabulary_column` check stays silent — while contributing
nothing for the long tail it just dropped. Lower `min_frequency` and find the
dimension elsewhere (drop a column, or raise the cap and pay a cost whose
exponent rises with the cap you already have — a doubling measured 5.07× from
5,000 and 7.46× from 10,000; see the per-doubling table below),
or drop the column if its tail genuinely carries no signal. Pruning that costs
fewer than 20% of rows is the intended use of the lever and is not reported.

### Per-trial time grows faster than the dimension, memory quadratically, and both multiply with `training.parallelism`

irspack forms a dense `Fᵀ F` Gram matrix per side and solves it by Cholesky
decomposition. The two costs scale differently and are worth keeping apart
when sizing a host: **time** grows **super-linearly** with the encoded
dimension, and — this is the part that trips up sizing — **the exponent itself
rises with the dimension**, so no single power fits the whole range. Below the
default 5,000 cap the feature work is not yet what the trial spends its time on
and a doubling costs under 2×; from 5,000 upward the Gram matrix and its
Cholesky take over and a doubling approaches the 8× of a pure cubic. Measured
per doubling, one fixture, `parallelism: 1`, median of three alternating passes
over the whole ladder:

| doubling | cost | implied exponent |
|---|---|---|
| 1,251 → 2,501 | 1.74× | 0.80 |
| 2,501 → 5,001 | 1.85× | 0.89 |
| 5,001 → 10,001 | 5.07× | 2.34 |
| 10,001 → 20,001 | **7.46×** | **2.90** |

An earlier revision of this page summarised the whole range as a flat `dim^2.4`
and put a doubling at 5.1–5.8×, explicitly ruling out the cubic case. That is
right for the 5,000 → 10,000 step and wrong at both ends: it over-states the cost
of raising a small cap and under-states the cost of raising the default one. The
10,000 → 20,000 doubling measured 7.46× — effectively the cubic case — and that
is precisely the step an operator takes when the default cap refuses their
catalogue. Budget **two** doublings from 5,000 to 20,000 at ~38×, not ~30×.

Memory has no such complication: it grows **quadratically** —
the Gram matrix is `dim² × 8` bytes at float64. Treat that as a **floor, not an
estimate**: it gives 200 MB / 800 MB / 3.2 GB where the measured peak-RSS
increase over the same run without features is **287 MB / 960 MB / 3.5 GB**,
i.e. the formula runs 10-43% low and is furthest off at the default cap of
5,000. The Gram matrix dominates but the encoder state, the feature matrix
itself and the solver's working set are also live. irspack never errors from
either — it only degrades. Measured per trial:

| Encoded dimension | Time | Memory |
|---|---|---|
| 5,000 | 0.6–2.4 s | ~200 MB |
| 10,000 | 4.2–12 s | ~800 MB |
| 20,000 | 43–70 s | ~3.2 GB |

The time column is a range because it depends on the interaction data the
trial also has to fit, not on the dimension alone; the low figures come from a
small fixture and the high ones from a 100k-row one. Memory is stable across
both, as the Gram formula predicts. Size on the upper figure.

Both columns were re-measured independently on a different 100k-row fixture and
held: 1.9 s / 9.5 s / 70.8 s per trial, and a peak-RSS increase over the same run
without features of 272 MB / 922 MB / 3,290 MB against the Gram floor's 191 MB /
763 MB / 3,052 MB — 42% / 21% / 8% low, again furthest off at the default cap,
exactly as the paragraph above says. Note that the rising exponent is already
visible in this table: 4.2/0.6 and 43/4.2 are 7.0× and 10.2× per doubling. The
old `dim^2.4` summary narrowed its own table to the bottom of its range.

At the **default** `RECOTEM_MAX_FEATURE_DIM` of 5,000, a features run on that
100k-row fixture took 16.3 s against 4.3 s without features — a 3.8× increase
in total training time and +211 MB of peak RSS. That is affordable, but it is
not free: the default is a ceiling chosen to keep a mistake survivable, not a
target to encode up to.

`training.parallelism` is Optuna `n_jobs` — **in-process threads**, not
processes — so each concurrently-running trial builds and solves its own
dense Gram matrix independently. At `parallelism=4, dim=10k` that is roughly
4 × 771 MB ≈ 3 GB of Gram matrices alone, on top of everything else the
search holds in memory. Size training hosts (or set `parallelism` and
`RECOTEM_MAX_FEATURE_DIM`) with this multiplication in mind.

### Concurrent request bodies are unbounded

`RECOTEM_MAX_BODY_BYTES` caps **one** request. There is no in-flight byte
budget and no concurrency limit, so resident memory grows linearly with how
many large bodies arrive together — and every one of them is accepted.

Measured on a 1M-interaction model (250 MB idle) at the default 128 MiB cap:

| concurrent requests | body each | RSS before → peak | per request | HTTP |
|---|---|---|---|---|
| 1 | 63.5 MiB | 250 → 462 MB | 213 MB (3.35× the body) | 200 |
| 4 | 63.5 MiB | 620 → 866 MB | 61 MB | all 200 |
| 8 | 63.5 MiB | 866 → 1,413 MB | 68 MB | all 200 |
| 4 | 127 MiB | 1,413 → 1,928 MB | **129 MB** | all 200 |

Nothing was refused and nothing queued.

**Read the first row, not the later ones.** Rows 2-4 start from an `RSS before`
that the row above already inflated (620, 866, 1,413 MB), so their apparent
per-request cost is depressed by allocator arena that was going to be reused
anyway. Only the first row starts from a clean server, and it is the one to
size against: **213 MB for a 63.5 MiB body, 3.35× the body**. Re-measured on
servers restarted before each run, four concurrent maximal bodies cost 3.1-3.3×
each — 207 MB per 63.5 MiB request on a small model, 418 MB per 127 MiB request
on a 1M-interaction model. A workable estimate for a replica's peak is
therefore:

```
peak RSS ≈ idle + (concurrent large bodies) × (body size) × 3.3
```

The multiplier is not 1.1. An earlier revision of this page said it was, which
under-estimated the peak by a factor of about 2.2 — a direction that shows up
in production as an OOMKill rather than as a slow response.

Against the chart's default `limits.memory: 4Gi` and the default 128 MiB body
cap, roughly **8 concurrent maximal requests** reach the limit
(`(4096 − 250) / (128 × 3.3) ≈ 9`, and less once the model itself is larger
than the 250 MB idle assumed here). If your clients can send large batch
bodies, either lower `RECOTEM_MAX_BODY_BYTES` to what your legitimate batches
actually need, or bound concurrency in front of the pod (an ingress or sidecar
limit), or raise the memory limit to match.

The allocation is arena reuse rather than a leak — repeating one 63.5 MiB body
settles at a 620 MB high-water mark, and ordinary traffic afterwards returns
memory to the OS (1,928 MB fell to 1,039 MB over 33k small requests, flat from
40 s onward). But it does not return to the 250 MB idle figure, so **size
container limits on the high-water mark, not the steady state**.

### Payload and serve-side RSS grow with catalog size, not just dimension

irspack retains `self.item_features` (and `self.user_features`) on the
trained recommender and defines no `__getstate__`, so the encoded feature
matrix is pickled into the artifact payload verbatim. Size scales with
`n_items × nnz_per_row`, not with the encoded dimension alone: projected,
1M items × 500 encoded dimensions × 5 non-zero entries/row ≈ 42 MiB; 1M items
× 5,000 dimensions × 10 non-zero entries/row ≈ 80 MiB — material against the
512 MiB `RECOTEM_MAX_PAYLOAD_BYTES` default but not by itself fatal.
`RECOTEM_MAX_FEATURE_DIM` caps **columns**; nothing caps `n_items ×
nnz_per_row`, so a very large catalog with dense per-row encodings (many
`multi_label` tags, low `min_frequency`) can still produce a large payload
even with a modest encoded dimension. The identical bytes also count against
serve-side resident memory (see
[Sizing `recotem serve` memory](#sizing-recotem-serve-memory) above) once the
artifact is loaded.

### Cold-start latency, and `n_threads`

Cold-start scoring is an iterative CG solve, not a matrix lookup. Measured
latency (1,000 items, 64 components): a single cold-start request takes
300–500 µs median; batching amortizes this to **8–12 µs/user** — a 30–40×
per-user improvement, which is why the batch verbs
(`:batch-recommend` / `:batch-recommend-related`) are the recommended path
for any bulk cold-start workload.

A high `n_threads` measurably hurts **single-request** latency: median
734–857 µs and p95 2.0–2.2 ms at `n_threads=16`, versus faster at
`n_threads` 1–4. irspack has no fixed default here —
`IALSRecommender(n_threads=None)` resolves through
`irspack._threading.get_n_threads` to `$IRSPACK_NUM_THREADS_DEFAULT`, falling
back to `os.cpu_count()`, so the effective default is the training host's
core count (16 on the machine these numbers were measured on). Recotem never
sets `n_threads`, and the resolved value is baked into the pickled model at
training time — there is no serve-time override. If single-request cold-start
latency matters for your workload, set `IRSPACK_NUM_THREADS_DEFAULT` in the
**training** environment; it is a training-time decision, not a serving-time
one.

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
| `RECOTEM_MAX_BODY_BYTES` | 128 MiB | serve | Max HTTP request body size (clamped [1 MiB, 2 GiB]). Over-cap requests get `413 PAYLOAD_TOO_LARGE` before the body is buffered/parsed. See [Sizing `recotem serve` memory](#sizing-recotem-serve-memory). |
| `RECOTEM_MAX_DOWNLOAD_BYTES` | 256 MiB | train | Raw I/O bytes cap for HTTP/HTTPS, local, and object-store source reads (clamped [1 MiB, 16 GiB]). Does **not** cap the decompressed DataFrame. |
| `RECOTEM_HTTP_TIMEOUT_SECONDS` | 30 | train | Connect/read timeout for HTTP/HTTPS source fetch (clamped [1, 600]). |
| `RECOTEM_HTTP_ALLOW_PRIVATE` | (unset) | train | Truthy (`1`/`true`/`yes`/`on`) allows HTTP fetches to private/loopback/link-local destinations. Leave unset in production to block SSRF against cloud-metadata services. |
| `RECOTEM_MAX_SQL_ROWS` | 50 000 000 | train | Hard cap on rows returned by the SQL data source (clamped [1 000, 500 000 000]). Caps the **row count**, not DataFrame resident memory — see [sql.md](data-sources/sql.md). |
| `RECOTEM_SQL_ALLOW_PRIVATE` | (unset) | train | Truthy (`1`/`true`/`yes`/`on`) allows SQL DSNs whose host is private/loopback/link-local. Leave unset in production to block SSRF; opting in also disables the DNS-rebinding re-check before each probe/fetch. |
| `RECOTEM_ALLOWED_HOSTS` | 127.0.0.1,localhost | serve | `TrustedHostMiddleware` allow-list (comma-separated). Whitespace-only input falls back to default. |
| `RECOTEM_ALLOWED_ORIGINS` | (empty) | serve | CORS allow-list (comma-separated). Empty = deny. |
| `RECOTEM_ENV` | (empty) | serve, train | Deployment environment tag. `--insecure-no-auth` is permitted only when set to `development`, `dev`, or `test`; `--dev-allow-unsigned` only when set to `development`. The `/docs`, `/redoc`, and `/openapi.json` endpoints are gated by an **allow-list**: they are served only when this is `development`, `dev`, or `test` (case-insensitive) and return `404` for every other value, including unset. |
| `RECOTEM_DRAIN_SECONDS` | 30 | serve | SIGTERM graceful drain window (clamped [1, 300]). Set `terminationGracePeriodSeconds` ≥ this + 5 in Kubernetes. |
| `RECOTEM_LOG_FORMAT` | auto | train + serve | `auto` / `json` / `console`. |
| `RECOTEM_METADATA_FIELD_DENY` | (empty) | serve | Comma-separated columns stripped from `/v1/recipes/{name}:recommend` and `:recommend-related` responses after the metadata join. |
| `RECOTEM_METRICS_ENABLED` | (unset) | serve | Truthy enables the Prometheus endpoint at **`/v1/metrics`** (a bare `/metrics` is `404`). The route carries `Depends(_require_auth)`, so a scrape without a valid `X-API-Key` gets `401`. Requires `recotem[metrics]` extra. |
| `RECOTEM_ARTIFACT_ROOT` | (empty) | train | Local `output.path` must lie under this directory (symlink escapes rejected). |
| `RECOTEM_LOCK_DIR` | (empty) | train | Override directory for per-recipe training lock files. Needed when `output.path` is a remote URI (`s3://`, `gs://`, …); falls back to `<tempdir>/recotem-locks/`. |
| `RECOTEM_STARTUP_PARALLELISM` | (auto) | serve | Threads used to load artifacts at startup (clamped [1, 32]). Default: `min(len(recipes), 8)`. Setting to `0` clamps to 1 with a warning. |
| `RECOTEM_BQ_REQUIRE_STORAGE_API` | (unset) | train | Truthy raises `DataSourceError` instead of falling back to the REST path when the BigQuery Storage Read API fails. |
| `RECOTEM_ALLOW_IRSPACK_VERSION_SKEW` | (unset) | serve | Truthy downgrades the irspack version-skew refusal to a warning and lets the payload reach the deserializer. Does not make an incompatible payload loadable. See [irspack version skew](#irspack-version-skew). |
| `RECOTEM_MAX_FEATURE_DIM` | 5000 | train | Cap on the encoded feature dimension per side (item and user are checked independently), clamped [16, 100000]. See [Feature-aware iALS sizing](#feature-aware-ials-sizing). |
| `RECOTEM_RECIPE_*` | — | train | Allow-listed prefix for `${...}` recipe env-var expansion. See [recipe-reference.md](recipe-reference.md#environment-variable-expansion). |

> **Note on `signing_key_status` in logs.** The `security.posture` log line emitted at every `recotem serve` startup includes a `signing_key_status` field: `configured` (keys present), `dev_allow_unsigned` (no keys, dev-unsigned mode), or `missing` (keys absent; startup will fail). Use this in SIEM rules to alert on misconfigured deployments.

---

## SLOs

Recotem does not enforce SLOs internally. Recommended baseline targets for production:

| Metric | Target |
|--------|--------|
| `/v1/recipes/{name}:recommend` p99 latency | < 50 ms (pure recommender, no metadata join) |
| `/v1/recipes/{name}:recommend-related` p99 latency | < 50 ms |
| `/v1/recipes/{name}:batch-recommend` and `:batch-recommend-related` p99 latency | budget separately per verb — track via `recotem_v1_request_latency_seconds{recipe,verb}` |
| `/v1/health` p99 latency | < 5 ms |
| Availability (per recipe) | Measure via `recotem_model_loaded{recipe}` Prometheus gauge |
| Artifact hot-swap time | ≤ `RECOTEM_WATCH_INTERVAL` + model load time |
| Train-to-serve lag | Schedule train; serve detects in ≤ `RECOTEM_WATCH_INTERVAL` seconds |

SLO budgets above describe each v1 verb individually (`recommend`,
`recommend-related`, `batch-recommend`, `batch-recommend-related`). Use
the `verb` label on `recotem_v1_requests_total` /
`recotem_v1_request_latency_seconds` to break out per-verb rates and
quantiles.

Enable Prometheus metrics:

```bash
pip install "recotem[metrics]"
```

The `/v1/metrics` endpoint is opt-in and off by default (a bare `/metrics` returns `404` — the route is mounted under the `/v1` router prefix). Set `RECOTEM_METRICS_ENABLED` to a truthy value (`1`, `true`, `yes`, `on`) to activate.

> **Network exposure.** Three endpoints are unauthenticated by design — the
> posture Kubernetes probes expect: `/v1/health` (startup), `/v1/health/ready`
> (readiness) and `/v1/health/live` (liveness). The first two return
> `{status, total, loaded}` and so disclose the recipe count and how many
> loaded, without naming any recipe; `/v1/health/live` returns
> `{"status": "alive"}` and reads no state at all. `/v1/metrics` is
> **not**: it carries `Depends(_require_auth)`, like every `/v1` route outside
> that list of three, so a scrape without a valid `X-API-Key` gets `401`.
> Configure Prometheus with the key (e.g. `http_headers` in the scrape
> config), or run the server in its unauthenticated posture (no
> `RECOTEM_API_KEYS`, which forces the loopback-only bind). `/v1/metrics` and
> `/v1/health/details` surface recipe names, kid IDs, load-error strings,
> model-load timestamps, and per-verb latency histograms.
> **Restrict them with the cluster's NetworkPolicy** (`/v1/metrics` to
> the Prometheus namespace, the three probe paths to kubelet probes) rather
> than relying on the API-key middleware — and note that the shipped chart
> probes `/v1/health/ready` and `/v1/health/live`, not only `/v1/health`, so a
> rule written around the single path predates the probe split. The
> `helm/recotem` chart's NetworkPolicy template ships with a deny-all
> baseline; allow only the scrapers and probes you actually need.

Available metrics:

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `recotem_v1_requests_total` | Counter | `recipe`, `verb`, `status` | v1 request volume; `status` ∈ {`ok`, `unknown_user`, `unknown_seed_items`, `no_candidates`, `recipe_not_found`, `unavailable`, `validation_error`, `features_not_supported`, `feature_value_unusable`, `related_not_supported`, `error`}. Every value except `error` is client-caused and expected in normal operation; `error` is reserved for genuine server faults (HTTP 500) — see [Monitoring SLIs](#monitoring-slis) |
| `recotem_v1_request_latency_seconds` | Histogram | `recipe`, `verb` | per-verb end-to-end latency |
| `recotem_v1_batch_size` | Histogram | `recipe`, `verb` | observed batch fan-out (only for `batch-recommend` / `batch-recommend-related`) |
| `recotem_v1_batch_element_errors_total` | Counter | `recipe`, `verb`, `code` | per-element errors inside batch HTTP-200 responses; `code` ∈ {`UNKNOWN_USER`, `UNKNOWN_SEED_ITEMS`, `NO_CANDIDATES`, `VALIDATION_ERROR`, `FEATURES_NOT_SUPPORTED`, `FEATURE_VALUE_UNUSABLE`, `RELATED_NOT_SUPPORTED`, `INTERNAL_ERROR`} |
| `recotem_v1_metadata_degraded_items_total` | Counter | `recipe`, `verb`, `kind` | items served with degraded metadata; `kind` ∈ {`fallback` (item_id/score only), `dropped` (omitted entirely)} |
| `recotem_v1_validation_errors_outside_verb_total` | Counter | — | 422 errors on non-inference paths (e.g. `/v1/recipes` list with bad query) |
| `recotem_v1_feature_unknown_value_total` | Counter | `recipe`, `side`, `column` | `side` ∈ {`item`, `user`}. Fires on a `categorical` value absent from the training vocabulary, a `multi_label` value where any supplied token misses, or a non-finite `numerical` value (`+inf`/`-inf`, or a `NaN` reached via a string); a `numerical` value that is missing or fails to parse as a number at all still degrades the recommendation silently and is **not** counted — see [Feature-aware cold start](api-reference.md#feature-aware-cold-start) for the per-encoding breakdown |
| `recotem_v1_feature_unknown_column_total` | Counter | `recipe`, `side` | cold-start requests carrying at least one feature key the recipe does not declare (e.g. a typo). The encoder never reads such a key, so the request degrades toward a bias-only profile and still returns 200 — this counter is the only signal. Counted **once per request per side**, not per key. Deliberately **not** labelled by column name: unlike `..._unknown_value_total`'s `column` (bounded by your recipe), an undeclared name is unbounded request input and would be a cardinality DoS. To find the offending key, diff the client payload against the recipe's `features:` block |
| `recotem_v1_cold_start_requests_total` | Counter | `recipe`, `case` | cold-start requests served from side features; `case` ∈ {`features_only` (A), `features_and_history` (B), `cold_seeds` (C)} |
| `recotem_model_loaded` | Gauge | `recipe` | 1 if the recipe is currently loaded |
| `recotem_artifact_load_failures_total` | Counter | `recipe`, `reason` | artifact-load failures since process start; `reason` ∈ {`read`, `parse`, `hmac`, `header_json`, `deserialize`, `metadata`, `yaml`, `unexpected`, `dir_scan`, `timeout`, `version_skew`, `feature_version`, `feature_state`, `recipe_name`, `size_cap`} |
| `recotem_active_recipes` | Gauge | — | total recipes in the registry |
| `recotem_swap_total` | Counter | `recipe`, `result` | hot-swap attempts (`ok` / `error`) |
| `recotem_artifact_stat_failures_total` | Counter | `recipe` | watcher stat() failures |
| `recotem_watcher_unhandled_errors_total` | Counter | — | watcher loop crashes |
| `recotem_metadata_index_build_errors_total` | Counter | `recipe` | per-row errors during `build_metadata_index` at artifact-load time (load-time) |
| `recotem_metadata_serialization_errors_total` | Counter | `recipe`, `verb` | per-item metadata serialization failures during response building (request-time) |
| `recotem_recipe_rescan_errors_total` | Counter | `recipe` | recipe rescan failures |
| `recotem_recipes_dir_scan_failures_total` | Counter | `error_class` | recipes-dir scan failures |
| `recotem_recommender_layout_unexpected_total` | Counter | `recipe` | `AttributeError` on `recommender._mapper.user_id_to_index` (user axis) or `recommender._mapper.item_id_to_index` (item axis) — indicates irspack API incompatibility. Both axes increment the same counter and it carries no axis label, so it cannot tell you which one fired; the accompanying `recommender_layout_unexpected` log event names the `verb` |
| `recotem_watcher_state_divergence_total` | Counter | — | watcher tried to mark an error on a non-existent registry entry (ordering bug) |

> **Not listed: `recotem_bigquery_storage_fallback_total`.** The BigQuery
> Storage Read API fallback counter is incremented only by the data source,
> which runs inside `recotem train` — a batch process with no HTTP server.
> `/v1/metrics` is served by `recotem serve`, which never fetches data, so the
> series is never populated in a scrapeable process. Alert on the
> `bigquery_storage_fallback` **log event** instead; see
> [BigQuery Storage Read API fallback](#bigquery-storage-read-api-fallback).

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
  its `last_load_error` field is set so `/v1/health/details` shows the staleness while
  `/v1/recipes/{name}:recommend` continues to return the previous good model.
- On `_stat_marker` returning None (file disappeared), the existing entry
  keeps serving and an `artifact_disappeared` warning is logged once.

### Initial load failure

When an artifact fails to load at startup the recipe is still registered as
a stub (`loaded=false`, `error=<reason>`). The server starts, `/v1/health`
reports `degraded`, and `/v1/recipes/{name}:recommend` (and sibling verbs)
return 503 (`RECIPE_UNAVAILABLE`). This is intentional: a partial outage
is recoverable by retraining without restarting the process.

### Unparseable recipe files

A file that cannot be parsed *at all* — YAML syntax error, schema violation —
is treated differently from a recipe whose artifact failed to load. It
declares no recipe: it has no name, no artifact, and nothing to serve. Such a
file is **skipped**:

- It is **excluded from the `total` and `loaded` counts** in `/v1/health`, and
  reported under a separate `skipped` count instead. `/v1/health` returns `ok`
  (HTTP 200) when every *loadable* recipe is loaded, so a typo in one file
  cannot fail a Kubernetes readiness probe for every other recipe in the pod.
- It **remains visible in `/v1/health/details`**, keyed by its file stem, with
  `"skipped": true` and an `error` string naming the offending **filename** and
  the parse error. The file stem is a fabrication — the recipe name is
  unreadable — so the filename is the identifier that leads back to the cause.
- `skipped` entries do **not** set `/v1/health/details` to `degraded`; nothing
  stopped serving.

```json
{"status": "ok", "total": 3, "loaded": 3, "skipped": 1}
```

> **Alerting.** `recotem_model_loaded` is `0` for a skipped recipe as well as for one that
failed to load, so an alert written as `recotem_model_loaded == 0` — the
obvious formulation — pages on exactly the condition this section says not to
page on. Distinguish them with the reason label:
`recotem_artifact_load_failures_total{reason="yaml"}` accompanies a skip,
other `reason` values accompany a real load failure.

Do not page on the `skipped` count — it is a config-quality
> signal, not an availability one. Alert on it as a warning
> (`skipped > 0` for more than one deploy cycle) so a broken file is noticed
> and fixed, while readiness stays keyed to `status`.

The failure is logged once when it first appears and once more on the first
rescan, then demoted to DEBUG while the file is unchanged: an unfixed file
would otherwise emit roughly 17k lines a day at the default 5-second watch
interval, and the condition is already visible in `/v1/health/details`. A
*different* failure on the same file logs immediately at its normal level.

Fixing or deleting the file clears the entry on the next watcher tick — no
restart needed.

The startup-only event variants are:

| Event | Trigger |
|-------|---------|
| `initial_artifact_read_failed` / `initial_artifact_read_error` | I/O failure or cap exceeded |
| `initial_artifact_parse_failed` | Magic / version / header structural error |
| `initial_artifact_hmac_failed` | HMAC mismatch or unknown kid |
| `initial_artifact_version_skew` | WARNING. The artifact's `(best_class, irspack transition)` is not verified compatible with the running irspack. Reason label `version_skew`; see [irspack version skew](#irspack-version-skew). The guard emits its own `irspack_version_skew` WARNING carrying both versions; this event adds the `kid`. Skew is operational, so neither is ERROR — alert on the `version_skew` metric, not on log level. |
| `initial_artifact_feature_version_refused` | The artifact header has a `features` object, but its `version` sub-field is missing, non-integer, or does not equal this build's known `FEATURE_STATE_VERSION`. Reason label `feature_version`. Fails closed — an unrecognized encoder-state shape would otherwise be silently mis-encoded rather than refused, producing wrong (not missing) recommendations. A header with **no `features` key at all** fails **open** (old artifact, or a model trained without a `features:` block) — it has no state to mis-encode. See [Feature-aware iALS sizing](#feature-aware-ials-sizing). |
| `initial_artifact_deserialize_failed` | FQCN allow-list rejection or payload decode error |
| `initial_artifact_feature_state_refused` | The artifact's `features` header contradicts the encoder state its payload carries — an undeclared state (including a deleted `features` key), a declared side the payload does not back, an `n_features` / `columns` mismatch, a payload state version this build does not implement, an unrecognized descriptor key, or an `active` flag that disagrees with the winning recommender. Reason label `feature_state`. Distinct from `feature_version`, which means "this build cannot read that shape at all" and is remedied by a retrain; this one means the artifact is internally inconsistent — a mis-built or partially-tampered file. Runs after deserialization, since it is the first point where both halves exist. See [security.md — Feature header/payload reconciliation](security.md#feature-headerpayload-reconciliation). |
| `initial_artifact_recipe_name_mismatch` | WARNING. The artifact header's `recipe_name` names a different recipe than the one loading it — two recipes are pointing at one `output.path`, so each training run overwrites the other's model. Reason label `recipe_name`. The gate emits its own `artifact_recipe_name_mismatch` WARNING with both names; this event adds the `kid`. A header with **no `recipe_name` at all** fails **open** (pre-2.0 artifact) and logs `artifact_recipe_name_absent_from_header`. Not ERROR: the HMAC verified, so this is a configuration error, not a security event. |
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
| HMAC verification failures | `rate(recotem_artifact_load_failures_total{reason="hmac"}[5m])` | page — security signal (wrong key or tampered artifact) |
| Artifact bound to another recipe | `rate(recotem_artifact_load_failures_total{reason="recipe_name"}[5m])` | warn — two recipes share one `output.path`. The refused swap keeps the previous model serving, but the recipe is stuck on it until the paths are separated and both are retrained |
| irspack version skew | `rate(recotem_artifact_load_failures_total{reason="version_skew"}[5m])` | warn — train and serve have drifted apart. A hot-swap skew keeps serving the old model, but the same artifact fails the recipe at the next restart; see [irspack version skew](#irspack-version-skew) |
| Batch per-element error rate | `rate(recotem_v1_batch_element_errors_total[5m]) / rate(recotem_v1_requests_total{verb=~"batch-.*"}[5m])` | warn at sustained > 1% per recipe |
| Artifact stat failures (watcher poll) | `recotem_artifact_stat_failures_total{recipe=...}` increase | warn |
| Watcher unhandled errors | `recotem_watcher_unhandled_errors_total` increase | warn |
| Recommend error rate | `rate(recotem_v1_requests_total{status="error"}[5m]) / rate(recotem_v1_requests_total[5m])` | warn at 1%, page at 10%. `status="error"` is **only** genuine server faults (HTTP 500) — filter on it exactly, never on `status!="ok"`. Client-caused outcomes (`unknown_user`, `features_not_supported`, `feature_value_unusable`, `validation_error`, ...) carry their own labels precisely so a malformed client cannot page on-call |
| Cold-start client errors | `rate(recotem_v1_requests_total{status=~"features_not_supported\|feature_value_unusable"}[5m])` | warn only, never page — a sustained rate means a client is sending `user_features`/`item_features` to a recipe without a matching `features:` block, or values that cannot be standardized. The remedy is on the caller's side; the model is healthy |
| Recommend latency | `histogram_quantile(0.99, sum by (le, recipe, verb) (rate(recotem_v1_request_latency_seconds_bucket[5m])))` | per-recipe, per-verb SLO |
| Batch fan-out | `histogram_quantile(0.95, sum by (le, recipe, verb) (rate(recotem_v1_batch_size_bucket[5m])))` | watch for clients approaching the 256-element cap |
| Active recipes | `recotem_active_recipes` drop > 0 since last scrape | warn (recipe removed or all stub) |
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
- The FQCN allow-list is frozen per release. Re-train if your artifacts
  encode a class that has been removed.
- **The irspack pickle format is not covered by any of the above.** irspack
  does not keep its pickle format stable across its own minors, so a Recotem
  upgrade that moves irspack across a minor can refuse existing artifacts —
  by algorithm, per transition. This axis is **bidirectional**: it cannot be
  staged serve-first, and it does not roll back. See
  [irspack version skew](#irspack-version-skew) for the allow-list rule, which
  algorithms are refused, and the upgrade procedure.
- **scikit-learn is a further axis, unguarded.** `TruncatedSVD` artifacts embed
  an sklearn estimator; sklearn does not guarantee correctness when unpickling
  across its own minors. Recotem range-pins `scikit-learn>=1.8,<1.10`, which
  narrows the window but does not close it (two installs inside the range can
  differ), and no runtime check covers it.

For zero-downtime upgrade of the serve fleet, deploy new pods with both
the old and new signing kids configured (rotation-style), let new pods
become healthy, then drain old pods (relying on `RECOTEM_DRAIN_SECONDS`).

> **This procedure assumes the new pods can load the existing artifacts.**
> It holds for a signing-key rotation, but not across an irspack minor: new
> pods running irspack 0.5.0 will never become healthy against 0.4.x-trained
> IALS or BPRFM artifacts — they are refused before deserialization, the
> recipe stays `loaded: false`, and `/v1/health` returns 503. Retrain those
> recipes on the new irspack version *first*, or upgrade train and serve
> together and accept the retrain window. Check
> [irspack version skew](#irspack-version-skew) before any upgrade that moves
> irspack.

## Troubleshooting

### `recotem serve` starts but recipe is `loaded: false`

```bash
curl -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  http://localhost:8080/v1/health/details | jq '.recipes'
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
| `irspack version skew: ...` | The artifact's algorithm is not verified compatible across the irspack **major.minor** transition between train and serve (e.g. an IALS or BPRFM artifact across 0.4 ↔ 0.5) | Retrain the recipe on the serving host's irspack version. See [irspack version skew](#irspack-version-skew). |

### irspack version skew

irspack does not guarantee a stable pickle format across minor releases. Recotem records the training-time `irspack_version` in every artifact header and checks it against the running irspack **before** deserializing the payload.

The rule is an **allow-list**, not a deny-list:

- **Same major.minor** → always loaded. Patch drift (`0.5.0` → `0.5.3`) is tolerated and the verified table is never consulted.
- **Different major.minor** → loaded only if the artifact's `best_class` *and* that exact transition appear in Recotem's verified-compatible table. Anything absent is refused.

Verified compatible across **0.4 ↔ 0.5, in both directions**: `CosineKNNRecommender`, `TopPopRecommender`, `RP3betaRecommender`, `DenseSLIMRecommender`, `TruncatedSVDRecommender`. A row earns its place only when an artifact trained under one version was loaded under the other — irspack the only variable — and the recommendation scores compared bit-exact.

Refused across 0.4 ↔ 0.5:

| `best_class` | Why |
|--------------|-----|
| `IALSRecommender` | **Known break**, both directions. 0.5.0 added feature-aware iALS, growing `IALSModelConfig`'s pickled state from a 7-tuple to a 10-tuple; `__setstate__` is a strict-arity binding. |
| `BPRFMRecommender` | **Unverified** — trainable since the `bprfm` extra shipped, so the interchange experiment is now possible, but it has not been run (it needs an irspack 0.4.x environment, and a BPRFM payload embeds a LightFM object, adding a second version axis this table does not model). Absence from the table means *unproven*, not known-broken. |
| missing / non-string `best_class` | Fails **closed**: a header that cannot name its algorithm cannot match the table. |

On a refusal the recipe is marked `loaded: false` with reason `version_skew` and this error (recipe `news`, an IALS artifact trained on 0.4.2, served by 0.5.0):

```
irspack version skew: retrain recipe 'news' with irspack 0.5.0 — IALSRecommender
0.4.2→0.5.0 is not verified compatible. Recotem allows only (algorithm, irspack
transition) pairs it has empirically verified load correctly; unverified is not
proof of breakage — the one known break is IALSRecommender at irspack 0.5.0,
whose pickled model state changed shape. Retrain and redeploy, or if you know
this artifact is unaffected set RECOTEM_ALLOW_IRSPACK_VERSION_SKEW=1 to
downgrade this to a warning.
```

The remedy is deliberately front-loaded: serve truncates the stored `last_load_error` to 200 characters before it surfaces as `error` in `/v1/health/details`, so the fix, the recipe name, the algorithm, and both versions all have to land inside that budget. The full text still reaches the logs.

**Every future irspack minor starts out refused.** Because the guard consults a table of *verified* pairs, a later 0.5 → 0.6 upgrade refuses artifacts for **all** algorithms — including the five listed above — until someone verifies that transition and adds the rows. This is intended: it keeps the safety default of refusing what has not been tested.

**Fail-open cases.** A header with no `irspack_version` (pre-2.0 artifacts) or an unparseable version on either side logs a warning and loads: an unverifiable version is not evidence of incompatibility, and the deserializer remains the backstop. Note the asymmetry — an unusable *version* fails open, an unusable *`best_class`* on a real skew fails closed.

**Why the check exists.** Without it the failure surfaces from inside irspack's C++ layer as a bare `TypeError: __setstate__(): incompatible function arguments`, which names neither the recipe nor the remedy.

**Upgrade procedure.** Upgrade train and serve together, then retrain every IALS and BPRFM recipe. The break is bidirectional, so you cannot stage the upgrade by moving serve first, and you cannot roll serve back to 0.4.x once artifacts are retrained on 0.5.x. There is no in-place artifact migration: the missing fields are internal C++ state that only a retrain produces correctly.

**Blast radius — degraded now, down later.** Serve does not crash; the affected recipe is marked failed and every other recipe keeps serving. During a **hot-swap** the previously loaded model stays in memory (the load error is annotated onto the entry without clearing its `loaded` flag), so a skewed artifact dropped into a running fleet degrades to "still serving the old model" rather than an outage, and the count-based `/v1/health` stays `200`. Only `/v1/health/details`, which also scans error strings, reports `degraded`.

That resilience is **per-process and does not survive a restart.** At startup there is no previously loaded model to fall back on: the recipe is registered as a stub with `loaded: false`, `/v1/health` returns **503**, and any readiness or liveness probe pointed at `/v1/health` fails. So a skewed artifact sits harmless in a running fleet and takes pods down at the next restart, node drain, or scale-up — potentially long after the deploy that introduced it.

For the shipped Helm chart (`replicaCount: 2`, no `strategy:` block) Kubernetes' rolling-update defaults give `maxUnavailable = floor(0.25 × 2) = 0`, so a rolling update **stalls** with the old pods still serving rather than causing an immediate outage — new pods never become ready, and no old pod may be torn down to make room. The hazard is not the stalled rollout; it is that the degraded state ends at the next *involuntary* restart. The chart also ships `pdb.enabled: false`, so a node drain can take both replicas at once.

**Escape hatch.** `RECOTEM_ALLOW_IRSPACK_VERSION_SKEW=1` downgrades the refusal to an `irspack_version_skew_allowed` warning and lets the payload reach the deserializer. Use it only when you know the artifact is unaffected — most defensibly for an algorithm that is merely *unverified* rather than known-broken. It does not make an incompatible payload loadable: a genuinely broken artifact then fails with the bare `TypeError` this guard exists to replace.

Monitor `recotem_artifact_load_failures_total{reason="version_skew"}` to catch fleets where train and serve have drifted apart.

**A separate axis the guard does not cover: scikit-learn.** `TruncatedSVDRecommender` pickles an sklearn estimator into the payload, and sklearn warns (`InconsistentVersionWarning`) that unpickling across its own minors "might lead to breaking code or invalid results". Recotem range-pins `scikit-learn>=1.8,<1.10` to bound this, but a range **narrows the axis without closing it** — two installs inside the range can still differ, and the irspack guard never inspects the sklearn version. If TruncatedSVD artifacts must be reproducible bit-exact, pin sklearn exactly or build train and serve from the same lock file.

### `recotem train` exits 3 (DataSourceError)

For BigQuery: run `gcloud auth application-default print-access-token` to confirm ADC is working. Check the exact error in the JSON stderr line:

```bash
recotem train recipe.yaml 2>&1 | grep '"event":"train_error"' | jq .
```

#### BigQuery Storage Read API fallback

When the service account lacks `bigquery.readSessions.create`, the BigQuery source logs a `bigquery_storage_fallback` warning and falls back to the slower REST API. The same event is logged (with a different `reason`) when `google-cloud-bigquery-storage` is not installed at all. Monitor for this event in your log aggregator — sustained fallbacks indicate a missing IAM permission or a missing extra.

This is a **log-only** signal. `recotem_bigquery_storage_fallback_total` exists in the code but is incremented only in the `recotem train` process, which serves no `/v1/metrics` endpoint, so it is not scrapeable; do not build an alert rule on it.

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

- The split produced an empty test set (too few users or interactions). **Raise** `split.heldout_ratio` — do not lower it. The holdout is `floor(distinct_items × ratio)` *per user*, so a smaller ratio demands *more* history per user before anything can be held out (`0.2` needs 5 distinct items, `0.1` needs 10). The error message names the smallest value that would have worked for the data it saw. See [Per-user holdout depth](recipe-reference.md#per-user-holdout-depth).
- The data after cleansing has too few items for the cutoff. Lower `training.cutoff`.

### `recotem train` succeeds and picks `TopPopRecommender`

Not an error, and the most likely thing to be mistaken for one. Every other
entry in this section is a non-zero exit; this one exits 0, serves 200s, and
returns the same popular items to everybody.

Optuna picked TopPop because TopPop scored highest on **your held-out split**.
That is a statement about the data and the split, not about Recotem — and it
is usually recoverable. Check, in this order:

1. **Is the metric near zero for everything?** `recotem inspect` reports
   `best_score`; the structured log reports each trial. If the personalised
   algorithms scored ~0 as well, the split is the suspect, not the data — see
   the `zero_score` entry below and [Per-user holdout
   depth](recipe-reference.md#per-user-holdout-depth).

2. **Are preferences stable over the axis you split on?** `time_user` holds
   out each user's *most recent* interactions, so it asks "can the past
   predict this user's future?". For a catalog people browse by mood, session,
   or news cycle, the honest answer can be no — and then a popularity model
   genuinely does win. Re-run with `split.scheme: random` and compare: if
   personalised algorithms beat TopPop under `random` but not under
   `time_user`, the signal exists but is not persistent per user, and a
   recommender trained on stable long-run preferences is the wrong tool for
   that surface.

3. **Is there enough per-user history?** Collaborative filtering needs
   co-occurrence. Users with two or three interactions carry almost none, and
   a catalog where most users are that shallow will favour TopPop no matter
   which algorithm you list. `data_stats` in the artifact header gives
   `n_rows / n_users`; below roughly 5 the odds are poor.

4. **Is the catalog too uniform?** If every item is consumed about equally
   often there is nothing for popularity to exploit *and* little for
   collaborative signal to latch onto; if one item dominates, TopPop wins by
   construction.

A worked contrast, measured on two synthetic news-reading datasets of the same
shape (800 readers, 400 articles, ~67k interactions, `time_user`, identical
recipe): with each reader's topic preference resampled daily, TopPop won at
ndcg@10 **0.058**. With each reader given a stable preference, `RP3beta` won at
**0.317** — 10.9× TopPop's 0.029 on the same split. The recipe, the algorithms
and the budget were identical; only the persistence of preference differed.

### Choosing a model on a small dataset

Also not an error, and harder to spot than the TopPop case above, because the
model that ships is not obviously degenerate — it is personalised, it returns
varied items, and its `best_score` looks like a normal number.

The search picks a winner by scoring each trial on the held-out validation
interactions. **`data_stats.n_heldout_interactions` in the artifact header is
how many that was.** When the number is small, the ranking it produces is
mostly noise, and the algorithm that wins the search is not reliably the
algorithm that serves your users best.

Measured on a 25-user, 118-item tenant (3 departments, `heldout_ratio: 0.2`,
`algorithms: [IALS, CosineKNN, RP3beta, TopPop]`, `n_trials: 20`), scored
against a holdout the search never saw:

| model | search score (ndcg@10) | true recall@10 |
|---|---|---|
| what the search shipped | 0.2618 | **0.0600** |
| IALS alone | 0.2778 | 0.2867 |
| CosineKNN alone | 0.2459 | 0.3600 |
| RP3beta alone — **the search ranked it last** | 0.2278 | **0.3600** |
| popularity baseline | — | 0.0867 |
| deterministic random | — | 0.0933 |

The shipped model scored **below the popularity baseline** on real held-out
data, and the algorithm the search ranked last was the best one. The run exited
0, `/v1/health` reported `ok`, and `:recommend` returned 200.

Two things combined, each documented on its own:

- The validation set held **50 interactions**. That is not enough to separate
  four algorithms.
- `n_trials` is a global budget [split evenly across
  algorithms](recipe-reference.md#training), so four algorithms at
  `n_trials: 20` get five trials each. The same recipe with `algorithms:
  [IALS]` and the full 20 trials reached 0.2867 rather than 0.0600.

What to do:

1. **Read `n_heldout_interactions` before you trust `best_score`.** For scale,
   the shipped examples hold out 12 (`csv-local`), 60 (`quickstart`) and 803
   (`tutorial-purchase-log`) interactions. The first two are fine for learning
   the tool and are not a basis for choosing between algorithms.
2. **Compare against a popularity baseline yourself.** Recotem does not do this
   for you, and it is the check that would have caught the case above. Hold out
   a slice the training never sees, score the served model and a
   most-popular-items list on it, and require the model to win.
3. **Narrow `algorithms` when the budget is small**, or raise `n_trials` so
   each algorithm still gets a meaningful number of trials.
4. **Prefer one model over many tiny ones.** A per-tenant recipe for every
   small customer is the pattern that produces this failure; pooling small
   tenants into one model, where that is acceptable, gives the search something
   to work with.

Recotem does not warn about this on its own. Any threshold that flagged the
tenant above would also flag the shipped tutorials, so the number is reported
rather than judged — the judgement is yours.

### `recotem train` exits 4 with `feature_axis_error`

A [`features:`](recipe-reference.md#features) side's feature table has **zero** id overlap with the interaction data — not one id matched. This aborts a run that previously succeeded if the id column's type changed at the source, so it is worth recognising on sight. The message samples ids from both sides, which usually names the cause by itself:

```
features.item: none of the 1200 item ids in the interaction data were found in
the feature table's 'item_id' column, so every item would encode to the bias
column alone ... feature-table ids look like ['1.0', '2.0', '3.0']; interaction
ids look like ['1', '2', '3'].
```

It is fatal rather than a warning because the failure is otherwise **silent**: every entity would encode to the bias column alone, so training would run to completion and sign an artifact whose header advertises `features` for what is really plain iALS. The model would serve, and score worse, with nothing in the logs to say why.

Two causes account for essentially all occurrences:

- **Id dtype mismatch** — what the sample above shows. A single blank cell in an otherwise-integer id column makes pandas infer `float64`, so `1` reads back as `1.0` while the interaction axis carries `"1"`. Pin the type at the source rather than cleaning the data: on a `csv` feature table add `dtype: {item_id: str}`. `dtype` is csv-only — on `bigquery` / `sql` cast in the query (`CAST(item_id AS STRING)`), and on `parquet` fix the type in the file's schema.
- **A wrong-but-existing `id_column`** — a column that exists but does not hold the entity id passes the presence check at fetch time and fails only here. Check that `features.<side>.id_column` names the same id space as `schema.item_column` / `schema.user_column`.

recotem deliberately does not coerce the id column for you. By the time the frame is fetched, pandas has already inferred `float64` and the original text is unrecoverable — a column reading `1.0` is indistinguishable from one whose ids are literally `"1.0"` — so reformatting integral floats back to ints would silently rewrite ids on a catalog that legitimately uses that form, trading a detectable failure for a quiet corruption. It would also not catch the wrong-`id_column` case at all.

Only **zero** overlap aborts. Partial coverage is legitimate and expected: an id absent from the feature table encodes to bias-only and degrades to plain iALS for that one entity, which is the same mechanism that makes cold-start scoring possible. There is deliberately no low-coverage warning threshold — a dtype or `id_column` mistake is a property of the whole column and always lands at exactly 0%, so any threshold above zero would fire on correct configurations. Alert on the `feature_axis_coverage` event (`side`, `matched`, `total`) yourself if you want to track coverage.

### `recotem train` exits 4 with `feature_table_error`

Every declared feature column on one side encoded to nothing, so that side
collapsed to the implicit bias column alone. Training refuses rather than sign
an artifact that advertises features for what is really plain iALS.

Three routes reach it, and the message names which:

- **`min_frequency` pruned every token.** The usual cause after raising it to
  fit under `RECOTEM_MAX_FEATURE_DIM`. Lower it, or declare a second column so
  the side survives on that one (see
  [`min_frequency`](recipe-reference.md#min_frequency-is-the-dimension-cap-lever)).
- **A lone `numerical` column has zero variance.** Every row carries the same
  value, so standardization leaves nothing to encode.
- **The feature table's values are all null** for the declared columns.

`recotem validate` does **not** predict this: the vocabulary is only known
once the table has been read, so validation passes at exit 0 in all three
cases. Its sibling `feature_axis_error` (zero id overlap) is a different
failure — see the entry above.

### 401 on `/v1/recipes/{name}:recommend`

- Trailing or leading whitespace in the `X-API-Key` header is treated as part of the key and will not match. Trim client-side.
- Confirm the hash in `RECOTEM_API_KEYS` was produced by `recotem keygen --type api` for the plaintext you are sending. The wire prefix is `sha256:` but the digest is **scrypt** (`hashlib.scrypt(plaintext, salt=b"recotem.api-key.v1", n=2, r=8, p=1, dklen=32)`). A plain `sha256(plaintext)` will not match.

### 503 on `/v1/recipes/{name}:recommend` (or any sibling verb)

The recipe is unhealthy (`loaded: false`) — response body carries
`{"detail": "...", "code": "RECIPE_UNAVAILABLE"}`. See
`/v1/health/details` for the underlying error. Usually a signing
mismatch or corrupt artifact.

### Recipe file present but the endpoint 404s and `/v1/health` does not count it

Check the file extension. `--recipes <dir>` enumerates only direct `*.yaml`
children of the directory; a `*.yml` file is not a recipe file as far as
Recotem is concerned. It is not loaded, it is **not** reported under `skipped`
(that count is for `*.yaml` files that failed to parse), and nothing is logged
about it — the loader simply never sees it. A directory holding only `.yml`
files therefore looks like an empty directory:

```json
{"status": "ok", "total": 0, "loaded": 0}
```

and every verb on the recipe returns `404` with
`{"detail": "...", "code": "RECIPE_NOT_FOUND"}`. Rename the file to `.yaml`.

The same applies to recipes in subdirectories (enumeration is non-recursive)
and to symlinks that resolve outside `<dir>` — except that a rejected symlink
*is* reported. See
[recipe-reference.md — Loading a directory of recipes](recipe-reference.md#loading-a-directory-of-recipes).

### 404 on `/v1/recipes/{name}:recommend`

Response body carries `{"detail": "...", "code": "UNKNOWN_USER"}` — the
`user_id` was not present in training data. This is expected for new
users; handle it in your application layer (fall back to popularity-based
recommendations, for example).

### 404 on `/v1/recipes/{name}:recommend-related`

Response body carries `{"detail": "...", "code": "UNKNOWN_SEED_ITEMS"}` —
none of the supplied `seed_items` are known to the trained model.

### 422 on any `/v1/recipes/{name}:*` verb

Request validation failed before the handler executed. The body is
`{"detail": "Request validation failed", "code": "VALIDATION_ERROR",
"errors": [...]}` and the request is counted as `status="validation_error"`
in `recotem_v1_requests_total`.

### Partial failure in `/v1/recipes/{name}:batch-recommend` / `:batch-recommend-related`

Batch endpoints accept up to 256 requests per call and return per-element
`status` so a single bad input does not fail the whole batch. The HTTP
response is **200** when *any* element succeeded (failed elements carry
`status: "error"` with a `code` field). HTTP **503** is reserved for the
case where the recipe itself is unavailable (no element can be served).

### Watcher does not pick up new artifact

- Check `RECOTEM_WATCH_INTERVAL`. Default is 5 s.
- For object stores, check that the IAM role on the serve process has `GetObject` (S3) or `storage.objects.get` (GCS) on the artifact bucket.
- Run `recotem inspect` on the artifact path to confirm it is valid and signed with a kid the server knows. `recotem inspect` accepts both local paths and fsspec URIs (e.g. `s3://bucket/key.recotem`, `gs://bucket/key.recotem`).

### Log redaction

All log events are processed by the redaction processor before output. If you see `[REDACTED]` in a log line where you expected a value, the field name matched the redaction pattern (see [security.md](security.md#log-redaction)). This is intentional.
