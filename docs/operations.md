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

   Confirm all recipes loaded successfully. Per-recipe state lives behind the authenticated `/v1/health/details` endpoint — the public `/v1/health` returns only `{status, total, loaded}` aggregates, not the `recipes` map:

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
| 2 | RecipeError | YAML syntax, schema violation, invalid `--env-var`, `--dev-allow-unsigned` without companion confirmation flag, `--dev-allow-unsigned` outside `RECOTEM_ENV=development` |
| 3 | DataSourceError | Source-layer failure NOT during HTTP fetch — CSV/Parquet format error, required column missing, local-FS path not found, BigQuery schema mismatch |
| 4 | TrainingError | Includes subcodes `signing_key_missing`, `min_data_violation`, `time_column_parse_error`, `final_training_error`, `no_completed_trials`, `zero_score`, `excessive_per_trial_timeouts`, `feature_table_error`, `feature_axis_error`, `feature_cholesky_error` |
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
| `feature_table_loaded` | features | `side`, `n_rows`, `n_features`, `columns` (names only — feature values are user PII and are never logged). Only with a `features:` block; emitted before cleansing, since the feature tables are fetched up front. |
| `data_cleansed` | cleansing | `n_rows`, `drop_count` |
| `splitting_data` / `split_done` | split | `val_offset` |
| `feature_axis_coverage` | features | `side`, `matched`, `total` — how many ids of the axis being encoded the feature table covers. Emitted per side per phase (once for search, once for the final refit). Zero coverage does not emit this event; it aborts with `feature_axis_error` instead. |
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
| `RECOTEM_MAX_BODY_BYTES` | Hard cap on each HTTP **request** body (default 128 MiB, clamped [1 MiB, 2 GiB]). A `413 PAYLOAD_TOO_LARGE` is returned before Starlette buffers/parses the body, so a single authenticated client cannot make the process allocate a multi-GB request. The default clears the largest well-formed request `serve` accepts (~72 MiB: a 256-element batch, each carrying 1000 exclude_items of up to 256 chars) with headroom. Reduce it if your legitimate batch sizes are small and you want a tighter bound; the cap applies both to a declared `Content-Length` and to chunked bodies with no length header. |
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
encoded dimension per side (item and user are checked independently);
exceeding it raises `TrainingError` (exit 4) at the point the encoder state is
built. `min_frequency` (recipe-level, per column; see
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

### Per-trial time is cubic, memory is quadratic, and both multiply with `training.parallelism`

irspack forms a dense `Fᵀ F` Gram matrix per side and solves it by Cholesky
decomposition. The two costs scale differently and are worth keeping apart
when sizing a host: **time** grows **cubically** with the encoded dimension
(the decomposition itself), while **memory** grows only **quadratically** —
the Gram matrix is `dim² × 8` bytes at float64, which is exactly what the
Memory column below reports. irspack never errors from either — it only
degrades. Measured per trial:

| Encoded dimension | Time | Memory |
|---|---|---|
| 5,000 | ~0.6 s | ~200 MB |
| 10,000 | ~4.2 s | ~771 MB |
| 20,000 | ~43 s | ~3 GB |

`training.parallelism` is Optuna `n_jobs` — **in-process threads**, not
processes — so each concurrently-running trial builds and solves its own
dense Gram matrix independently. At `parallelism=4, dim=10k` that is roughly
4 × 771 MB ≈ 3 GB of Gram matrices alone, on top of everything else the
search holds in memory. Size training hosts (or set `parallelism` and
`RECOTEM_MAX_FEATURE_DIM`) with this multiplication in mind.

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

The recommender's default `n_threads=16` measurably hurts **single-request**
latency: median 734–857 µs and p95 2.0–2.2 ms at the default, versus faster
at `n_threads` 1–4. `n_threads` is baked into the pickled model at training
time, and there is currently no serve-time override — if single-request
cold-start latency matters for your workload, this is a training-time
decision, not a serving-time one.

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
| `RECOTEM_ALLOWED_HOSTS` | 127.0.0.1,localhost | serve | `TrustedHostMiddleware` allow-list (comma-separated). Whitespace-only input falls back to default. |
| `RECOTEM_ALLOWED_ORIGINS` | (empty) | serve | CORS allow-list (comma-separated). Empty = deny. |
| `RECOTEM_ENV` | (empty) | serve | Deployment environment tag. `--insecure-no-auth` is permitted only when set to `development`, `dev`, or `test`; `--dev-allow-unsigned` only when set to `development`. When set to `production`, `prod`, or `staging`, the `/docs`, `/redoc`, and `/openapi.json` endpoints are disabled. |
| `RECOTEM_DRAIN_SECONDS` | 30 | serve | SIGTERM graceful drain window (clamped [1, 300]). Set `terminationGracePeriodSeconds` ≥ this + 5 in Kubernetes. |
| `RECOTEM_LOG_FORMAT` | auto | train + serve | `auto` / `json` / `console`. |
| `RECOTEM_METADATA_FIELD_DENY` | (empty) | serve | Comma-separated columns stripped from `/v1/recipes/{name}:recommend` and `:recommend-related` responses after the metadata join. |
| `RECOTEM_METRICS_ENABLED` | (unset) | serve | Truthy enables the Prometheus `/metrics` endpoint. Requires `recotem[metrics]` extra. |
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

The `/metrics` endpoint is opt-in and off by default. Set `RECOTEM_METRICS_ENABLED` to a truthy value (`1`, `true`, `yes`, `on`) to activate.

> **Network exposure.** Both `/v1/metrics` and `/v1/health` are
> unauthenticated by design — the same posture Prometheus and Kubernetes
> liveness/readiness probes expect. The endpoints surface recipe names,
> kid IDs, load-error strings, model-load timestamps, and per-verb
> latency histograms.
> **Restrict them with the cluster's NetworkPolicy** (`/v1/metrics` to
> the Prometheus namespace, `/v1/health` to kubelet probes) rather than
> relying on the API-key middleware. The `helm/recotem` chart's
> NetworkPolicy template ships with a deny-all baseline; allow only the
> scrapers and probes you actually need.

Available metrics:

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `recotem_v1_requests_total` | Counter | `recipe`, `verb`, `status` | v1 request volume; `status` ∈ {`ok`, `unknown_user`, `unknown_seed_items`, `no_candidates`, `recipe_not_found`, `unavailable`, `validation_error`, `features_not_supported`, `feature_value_unusable`, `error`}. Every value except `error` is client-caused and expected in normal operation; `error` is reserved for genuine server faults (HTTP 500) — see [Monitoring SLIs](#monitoring-slis) |
| `recotem_v1_request_latency_seconds` | Histogram | `recipe`, `verb` | per-verb end-to-end latency |
| `recotem_v1_batch_size` | Histogram | `recipe`, `verb` | observed batch fan-out (only for `batch-recommend` / `batch-recommend-related`) |
| `recotem_v1_batch_element_errors_total` | Counter | `recipe`, `verb`, `code` | per-element errors inside batch HTTP-200 responses; `code` ∈ {`UNKNOWN_USER`, `UNKNOWN_SEED_ITEMS`, `NO_CANDIDATES`, `VALIDATION_ERROR`, `FEATURES_NOT_SUPPORTED`, `FEATURE_VALUE_UNUSABLE`, `INTERNAL_ERROR`} |
| `recotem_v1_metadata_degraded_items_total` | Counter | `recipe`, `verb`, `kind` | items served with degraded metadata; `kind` ∈ {`fallback` (item_id/score only), `dropped` (omitted entirely)} |
| `recotem_v1_validation_errors_outside_verb_total` | Counter | — | 422 errors on non-inference paths (e.g. `/v1/recipes` list with bad query) |
| `recotem_v1_feature_unknown_value_total` | Counter | `recipe`, `side`, `column` | `side` ∈ {`item`, `user`}. Fires on a `categorical` value absent from the training vocabulary, a `multi_label` value where any supplied token misses, or a non-finite `numerical` value (`+inf`/`-inf`, or a `NaN` reached via a string); a `numerical` value that is missing or fails to parse as a number at all still degrades the recommendation silently and is **not** counted — see [Feature-aware cold start](api-reference.md#feature-aware-cold-start) for the per-encoding breakdown |
| `recotem_v1_feature_unknown_column_total` | Counter | `recipe`, `side` | cold-start requests carrying at least one feature key the recipe does not declare (e.g. a typo). The encoder never reads such a key, so the request degrades toward a bias-only profile and still returns 200 — this counter is the only signal. Counted **once per request per side**, not per key. Deliberately **not** labelled by column name: unlike `..._unknown_value_total`'s `column` (bounded by your recipe), an undeclared name is unbounded request input and would be a cardinality DoS. To find the offending key, diff the client payload against the recipe's `features:` block |
| `recotem_v1_cold_start_requests_total` | Counter | `recipe`, `case` | cold-start requests served from side features; `case` ∈ {`features_only` (A), `features_and_history` (B), `cold_seeds` (C)} |
| `recotem_model_loaded` | Gauge | `recipe` | 1 if the recipe is currently loaded |
| `recotem_artifact_load_failures_total` | Counter | `recipe`, `reason` | artifact-load failures since process start; `reason` ∈ {`read`, `parse`, `hmac`, `header_json`, `deserialize`, `metadata`, `yaml`, `unexpected`, `dir_scan`, `timeout`, `version_skew`, `feature_version`} |
| `recotem_active_recipes` | Gauge | — | total recipes in the registry |
| `recotem_swap_total` | Counter | `recipe`, `result` | hot-swap attempts (`ok` / `error`) |
| `recotem_artifact_stat_failures_total` | Counter | `recipe` | watcher stat() failures |
| `recotem_watcher_unhandled_errors_total` | Counter | — | watcher loop crashes |
| `recotem_metadata_index_build_errors_total` | Counter | `recipe` | per-row errors during `build_metadata_index` at artifact-load time (load-time) |
| `recotem_metadata_serialization_errors_total` | Counter | `recipe`, `verb` | per-item metadata serialization failures during response building (request-time) |
| `recotem_recipe_rescan_errors_total` | Counter | `recipe` | recipe rescan failures |
| `recotem_bigquery_storage_fallback_total` | Counter | `reason` | BQ Storage Read API fell back to REST |
| `recotem_recipes_dir_scan_failures_total` | Counter | `error_class` | recipes-dir scan failures |
| `recotem_recommender_layout_unexpected_total` | Counter | `recipe` | `AttributeError` on `recommender._mapper.user_id_to_index` (user axis) or `recommender._mapper.item_id_to_index` (item axis) — indicates irspack API incompatibility. Both axes increment the same counter and it carries no axis label, so it cannot tell you which one fired; the accompanying `recommender_layout_unexpected` log event names the `verb` |
| `recotem_watcher_state_divergence_total` | Counter | — | watcher tried to mark an error on a non-existent registry entry (ordering bug) |

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

The startup-only event variants are:

| Event | Trigger |
|-------|---------|
| `initial_artifact_read_failed` / `initial_artifact_read_error` | I/O failure or cap exceeded |
| `initial_artifact_parse_failed` | Magic / version / header structural error |
| `initial_artifact_hmac_failed` | HMAC mismatch or unknown kid |
| `initial_artifact_version_skew` | WARNING. The artifact's `(best_class, irspack transition)` is not verified compatible with the running irspack. Reason label `version_skew`; see [irspack version skew](#irspack-version-skew). The guard emits its own `irspack_version_skew` WARNING carrying both versions; this event adds the `kid`. Skew is operational, so neither is ERROR — alert on the `version_skew` metric, not on log level. |
| `initial_artifact_feature_version_refused` | The artifact header has a `features` object, but its `version` sub-field is missing, non-integer, or does not equal this build's known `FEATURE_STATE_VERSION`. Reason label `feature_version`. Fails closed — an unrecognized encoder-state shape would otherwise be silently mis-encoded rather than refused, producing wrong (not missing) recommendations. A header with **no `features` key at all** fails **open** (old artifact, or a model trained without a `features:` block) — it has no state to mis-encode. See [Feature-aware iALS sizing](#feature-aware-ials-sizing). |
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
| HMAC verification failures | `rate(recotem_artifact_load_failures_total{reason="hmac"}[5m])` | page — security signal (wrong key or tampered artifact) |
| irspack version skew | `rate(recotem_artifact_load_failures_total{reason="version_skew"}[5m])` | warn — train and serve have drifted apart. A hot-swap skew keeps serving the old model, but the same artifact fails the recipe at the next restart; see [irspack version skew](#irspack-version-skew) |
| Batch per-element error rate | `rate(recotem_v1_batch_element_errors_total[5m]) / rate(recotem_v1_requests_total{verb=~"batch-.*"}[5m])` | warn at sustained > 1% per recipe |
| Artifact stat failures (watcher poll) | `recotem_artifact_stat_failures_total{recipe=...}` increase | warn |
| Watcher unhandled errors | `recotem_watcher_unhandled_errors_total` increase | warn |
| Recommend error rate | `rate(recotem_v1_requests_total{status="error"}[5m]) / rate(recotem_v1_requests_total[5m])` | warn at 1%, page at 10%. `status="error"` is **only** genuine server faults (HTTP 500) — filter on it exactly, never on `status!="ok"`. Client-caused outcomes (`unknown_user`, `features_not_supported`, `feature_value_unusable`, `validation_error`, ...) carry their own labels precisely so a malformed client cannot page on-call |
| Cold-start client errors | `rate(recotem_v1_requests_total{status=~"features_not_supported\|feature_value_unusable"}[5m])` | warn only, never page — a sustained rate means a client is sending `user_features`/`item_features` to a recipe without a matching `features:` block, or values that cannot be standardized. The remedy is on the caller's side; the model is healthy |
| Recommend latency | `histogram_quantile(0.99, sum by (le, recipe, verb) (rate(recotem_v1_request_latency_seconds_bucket[5m])))` | per-recipe, per-verb SLO |
| Batch fan-out | `histogram_quantile(0.95, sum by (le, recipe, verb) (rate(recotem_v1_batch_size_bucket[5m])))` | watch for clients approaching the 256-element cap |
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
| `BPRFMRecommender` | **Unverifiable** — irspack gates it behind the separately installed `lightfm` package, which has no Python 3.12-compatible release, so irspack does not export the class and recotem cannot train it. Absence from the table means *unproven*, not known-broken. |
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

### 401 on `/v1/recipes/{name}:recommend`

- Trailing or leading whitespace in the `X-API-Key` header is treated as part of the key and will not match. Trim client-side.
- Confirm the hash in `RECOTEM_API_KEYS` was produced by `recotem keygen --type api` for the plaintext you are sending. The wire prefix is `sha256:` but the digest is **scrypt** (`hashlib.scrypt(plaintext, salt=b"recotem.api-key.v1", n=2, r=8, p=1, dklen=32)`). A plain `sha256(plaintext)` will not match.

### 503 on `/v1/recipes/{name}:recommend` (or any sibling verb)

The recipe is unhealthy (`loaded: false`) — response body carries
`{"detail": "...", "code": "RECIPE_UNAVAILABLE"}`. See
`/v1/health/details` for the underlying error. Usually a signing
mismatch or corrupt artifact.

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
