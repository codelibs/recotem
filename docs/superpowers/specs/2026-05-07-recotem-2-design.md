# Recotem 2.0 Design

- **Status**: Draft v2 (post gap-analysis review)
- **Date**: 2026-05-07
- **Author**: Shinsuke Sugaya / Claude (brainstorming session)
- **Branch**: `docs/recotem-2-design`

## 1. Motivation

Recotem 1.x has accumulated many features around recommender hosting (multi-user
projects, A/B testing, deployment slots, conversion event tracking, web admin
SPA, scheduled retraining UI, WebSocket job progress, item metadata UI, API key
management UI). The user feedback is:

- Too many features, too complex.
- Real users typically pull data from popular sources (GA4 / BigQuery-exported
  GA4) rather than uploading CSV through a UI.
- The desired core value is "fetch data from a source on a schedule, train a
  recommender, and serve it as an API" — everything else is incidental.

Recotem 2.0 is a clean rewrite. Backward compatibility is explicitly out of
scope. The bar is "smallest possible system that delivers the core loop above
with high quality".

## 2. Scope

### In scope

- Library + CLI distribution (`pip install recotem`, plus a single Docker image).
- DataSource plugin model with builtin BigQuery and CSV/Parquet sources.
- Hyperparameter search across multiple irspack algorithms via Optuna.
- Item metadata join into prediction responses.
- FastAPI prediction server (`recotem serve`) with hot-swap on artifact change.
- HMAC-signed model artifacts with allow-listed deserialization.
- API-key auth (header `X-API-Key`).

### Out of scope (explicitly dropped from 1.x)

- Web admin UI / SPA (Vue 3, PrimeVue, Tailwind, Pinia, TanStack Query).
- Multi-user / login / project ownership concepts.
- A/B testing, DeploymentSlot, ConversionEvent.
- WebSocket-based job progress.
- In-product RetrainingSchedule (replaced by external schedulers — cron, K8s
  CronJob, Airflow, GitHub Actions).
- TaskLog DB table (replaced by structured stdout/stderr logging).
- PostgreSQL, Redis, Celery broker/beat, Channels, Daphne, Nginx proxy.
- DRF, Django, dj-rest-auth, simplejwt, drf-spectacular, django-celery-results.
- Inference rate-limiter / slowapi (delegated to upstream proxy).
- Per-deployment-slot scopes; only a single `predict` scope exists implicitly.
- 1.x → 2.0 data migration. There is no exporter. 2.0 users start fresh:
  re-author each project as a recipe and re-train. 1.x and 2.0 cannot share a
  process or database. Operators run 1.x in parallel until each project's next
  scheduled retrain runs through 2.0.
- `last_used_at` / per-key audit. API keys live as hashes in env; the design
  trades observability for simplicity. Operators rotate keys on a schedule
  rather than reacting to per-key activity.

### Non-goals

- Real-time feature ingestion or online learning (still batch retrain).
- Multi-tenant SaaS hosting.
- Hyperparameter management UIs (config is the YAML, history is the artifact
  files in your storage).

### Operational boundaries

- `recotem serve` is sized for ≤ 100 recipes per process. Beyond that, shard
  the recipes across multiple `serve` processes by directory.
- A single `ModelEntry` is sized for ≤ 2 GiB of recommender state in RAM
  (default `RECOTEM_MAX_ARTIFACT_BYTES`). Larger models require explicit opt-in
  and a host with enough RAM. The cap defends against artifact-size DoS
  (Section 8).
- `recotem train` is sized for the data volumes irspack handles well today
  (10⁷ interactions, 10⁵ users, 10⁵ items). Beyond that, users should
  pre-aggregate in their data warehouse.

## 3. Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│                  recotem (single Python package)                 │
├──────────────────────────────────────────────────────────────────┤
│  CLI (Typer)                                                     │
│  ├─ recotem train   <recipe.yaml>      batch: fetch→train→sign   │
│  ├─ recotem serve   --recipes <dir>    FastAPI /predict          │
│  ├─ recotem inspect <artifact>         read header (no payload)  │
│  ├─ recotem validate <recipe.yaml>     schema + connectivity     │
│  ├─ recotem schema                     emit JSON Schema for IDEs │
│  └─ recotem keygen                     generate signing/api key  │
│                                                                   │
│  Core layer                                                       │
│  ├─ recipe       pydantic v2 models, YAML loader, env expansion   │
│  ├─ datasource   protocol + builtin csv / bigquery (entry_points) │
│  ├─ training     irspack + Optuna driver, split, evaluate         │
│  ├─ artifact     binary container with HMAC signing               │
│  ├─ metadata     item metadata loader (CSV/Parquet, fsspec)       │
│  └─ serving      FastAPI app, ModelRegistry, file watcher, auth   │
└──────────────────────────────────────────────────────────────────┘
        │                                                ▲
        ▼                                                │
   artifact files                                  /predict/{name}
   (./artifacts/<recipe>.recotem)                  HTTP clients
```

Stack:

- Python 3.12+ (matches irspack 0.4.x requirements).
- CLI: Typer (declarative, auto-generated help).
- Config: pydantic v2 (typed YAML loader with restricted env expansion).
- Web: FastAPI + uvicorn (HTTP only, no WebSocket).
- Distribution: PyPI + a single multi-stage Docker image.

Banned dependencies (deliberately removed): PostgreSQL, Redis, Celery, Channels,
Daphne, Django, DRF, Vue / Vite / PrimeVue / Tailwind, Nginx as a coupled proxy.

## 4. Module layout and responsibilities

```
src/recotem/
├── __init__.py
├── cli.py              Typer app. Orchestration only, no domain logic.
├── recipe/
│   ├── models.py       pydantic Recipe, SourceConfig (dynamic discriminator),
│   │                   SchemaConfig, ItemMetadataConfig, TrainingConfig,
│   │                   SplitConfig, OutputConfig
│   ├── loader.py       YAML → Recipe, restricted env expansion, path checks
│   ├── envvars.py      whitelist of expandable env-var prefixes
│   └── errors.py       RecipeError with line-number context
├── datasource/
│   ├── base.py         DataSource protocol + DataSourceError + Plugin contract
│   ├── csv.py          CSVSource (covers parquet via pandas)
│   ├── bigquery.py     BigQuerySource (google-cloud-bigquery, ADC)
│   └── registry.py     entry_points group "recotem.datasources" + dynamic
│                        discriminated-union builder
├── training/
│   ├── pipeline.py     run_training(recipe) -> TrainResult (public)
│   ├── search.py       Optuna driver with per-algorithm fairness
│   ├── split.py        irspack split wrapper with min-data preconditions
│   ├── evaluate.py     Evaluator setup
│   ├── algorithms.py   alias resolution (IALS to IALSRecommender)
│   ├── lock.py         per-recipe file lock (output.path.lock)
│   └── progress.py     auto-detect TTY → rich progress; otherwise structured logs
├── artifact/
│   ├── format.py       binary container layout constants + dataclasses + caps
│   ├── io.py           write/read via fsspec; read-once; size limits enforced
│   └── signing.py      HMAC-SHA256 sign + multi-key verify (kid) +
│                       hand-enumerated FQCN allow-list for unpickling
├── metadata/
│   └── loader.py       item metadata DataFrame indexed by item_id (str-coerced)
├── serving/
│   ├── app.py          create_app(serve_config) -> FastAPI; CORS / TrustedHost
│   ├── registry.py     ModelRegistry: name -> ModelEntry (RLock, sync handlers)
│   ├── watcher.py      mtime → read-once → sha256 → verify → atomic replace
│   ├── auth.py         X-API-Key dependency (constant-time, no logging)
│   ├── routes.py       /predict/{name}, /health, /models, /metrics (opt-in)
│   └── log_redaction.py structlog processor that strips API keys / signing
│                         keys / cloud creds from every event
├── config.py           ServeConfig from env vars, with explicit posture flags
├── logging.py          structlog setup (json | console) with redaction
└── version.py
```

Allowed dependency direction (top depends on bottom; reverse is forbidden):

```
cli.py
  └─> recipe/, training/, serving/, artifact/, datasource/
training/, serving/, metadata/
  └─> artifact/, datasource/
recipe/, artifact/, datasource/
  └─> stdlib + pydantic + irspack + fsspec only
```

Key boundary decisions:

- `training/` and `serving/` never import each other. They communicate only
  through artifact files. Trainer and server can run on different machines.
- `cli.py` is thin orchestration; tests target `run_training()`,
  `create_app()`, etc. directly.
- `serving/registry.py` and `serving/watcher.py` are split (1.x conflated load,
  cache, dispatch in one module).
- `datasource/registry.py` builds the `SourceConfig` discriminated union
  dynamically after entry-point discovery (Section 13 plugin contract).

## 5. Recipe schema

A recipe is the single source of truth for what to train and what to serve.
One recipe → one model → one `/predict/{name}` endpoint.

```yaml
name: news_articles                           # required, ^[A-Za-z0-9_-]{1,64}$
                                              # Becomes /predict/{name}.

source:
  type: bigquery                              # discriminator (dynamic union)
  query: |                                    # SQL is exec-time trusted code
    SELECT user_pseudo_id AS user_id,
           (SELECT value.int_value FROM UNNEST(event_params) WHERE key='article_id') AS item_id,
           TIMESTAMP_MICROS(event_timestamp) AS ts
    FROM   `proj.analytics_123.events_*`
    WHERE  _TABLE_SUFFIX BETWEEN
           FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
       AND FORMAT_DATE('%Y%m%d', CURRENT_DATE())
       AND event_name = 'select_content'
  query_parameters:                            # optional, BigQuery @-params
    min_events: 5
  project: my-gcp-project                     # optional; falls back to ADC project

schema:
  user_column: user_id
  item_column: item_id
  time_column: ts                             # optional; required for time_user / time_global

cleansing:                                     # optional
  drop_null_ids: true                          # default true; rows with null user/item dropped
  dedup: keep_last                             # keep_first | keep_last | sum_weight | none
  min_rows: 1000                               # below → exit 4 with min_data_violation
  min_users: 10
  min_items: 10

item_metadata:                                # optional
  type: parquet                                # csv | parquet
  path: gs://bucket/items.parquet              # local / s3:// / gs:// (no file://, no http(s)://)
  fields: [title, category, image_url]        # allow-list (non-empty)
  on_field_missing: error                      # error | null (default error at startup)

training:
  algorithms: [IALS, CosineKNN, TopPop]       # ≥1. Multiple → cross-algorithm search.
  metric: ndcg                                 # ndcg | map | recall | hit
  cutoff: 20
  n_trials: 40                                 # global trial budget
  per_algorithm_trials:                        # optional override per algorithm
    IALS: 24
    CosineKNN: 12
    TopPop: 4
  per_trial_timeout_seconds: 600               # optional, soft per-trial cap
  timeout_seconds: 1800                        # overall cap
  parallelism: 1                               # in-process worker count
  storage_path: ""                             # empty → in-memory Optuna; "./optuna.db" enables resume
  split:
    scheme: time_user                          # random | time_global | time_user
    heldout_ratio: 0.1
    test_user_ratio: 1.0
    seed: 42

output:
  path: ./artifacts/news_articles.recotem      # local | s3:// | gs:// (no file://)
  versioning: append_sha                        # always_overwrite | append_sha (default append_sha)
                                                # append_sha writes <name>.<sha>.recotem and
                                                # updates a <name>.recotem pointer atomically.
```

CSV / Parquet source subschema (`source.type: csv` or `source.type: parquet`):

```yaml
source:
  type: csv
  path: gs://bucket/interactions.csv.gz
  delimiter: ","                              # default ","
  encoding: utf-8                              # default utf-8
  header: 0                                    # row index of header (default 0)
  dtype:                                       # optional explicit dtypes
    user_id: str
    item_id: str
```

Schema rules and validation:

- `name` is validated against `^[A-Za-z0-9_-]{1,64}$` at YAML load AND again
  immediately before any filesystem or URL use (defence in depth). Duplicate
  names within `--recipes` cause server start to fail (no last-write-wins).
- `source.type` is the discriminator for a dynamic pydantic discriminated
  union assembled from entry points (Section 13).
- Environment variable expansion is restricted:
  - Syntax is `${RECOTEM_RECIPE_VAR}`. Only variables matching the prefix
    `RECOTEM_RECIPE_*` (or set via the explicit allow-list passed to
    `recotem train --env-var KEY=...`) are expanded.
  - Variables `RECOTEM_SIGNING_KEY`, `RECOTEM_API_KEYS`, and any name
    matching `*_SECRET*`, `*_PASSWORD*`, `AWS_*`, `GOOGLE_*`,
    `GCP_*` are blacklisted regardless of prefix.
  - Expansion is **never** performed inside `source.query` or
    `source.query_parameters`. BigQuery callers must use BigQuery `@param`
    placeholders. This forecloses SQL injection through the recipe.
  - Missing or blacklisted variables produce a `RecipeError`. Error messages
    redact the value of any variable they reference.
- Secrets must not be embedded. Embedded credentials in fsspec URIs (e.g.
  `s3://AKIA...:secret@bucket/`) are rejected by recipe load. Cloud auth
  comes from ADC / instance profile / env.
- `output.path` and any source / metadata path must use one of the allowed
  schemes: bare local path, `s3://`, `gs://`, `az://`. The schemes
  `file://`, `http://`, `https://`, `ftp://`, `ftps://`, `memory://` are
  rejected. Local paths are resolved to absolute and (for `output.path`
  during train) must be under `RECOTEM_ARTIFACT_ROOT` if that env var is set.

## 6. `recotem train` data flow

```
1. Load recipe YAML → Recipe (pydantic).
   - Restricted env expansion (Section 5).
   - Path scheme validation, name re-validation, no embedded credentials.
   Failure: stderr human message + JSON error line, exit 2.

2. Acquire per-recipe lock at <output.path>.lock (file lock).
   - Default mode is exclusive. --no-lock disables (for distributed schedulers
     that already coordinate elsewhere).
   - If lock contention, exit 0 with skipped status (so cron retries are no-ops)
     unless --fail-on-busy is passed.

3. DataSource.fetch() → pandas DataFrame.
   - csv:      pandas.read_csv (gz/zip transparent, fsspec); dtype overrides applied.
   - parquet:  pandas.read_parquet
   - bigquery: google-cloud-bigquery + Storage Read API; @parameter placeholders bound
   Validate schema columns exist and types coerce.
   Failure: exit 3 (DataSourceError) with code in JSON line.

4. Cleansing (controlled by recipe.cleansing):
   - drop_null_ids: drop rows with null user_id or item_id; record drop_count.
   - dedup: apply policy (keep_first | keep_last | sum_weight | none).
   - String-coerce user_id and item_id (matches serve-time treatment).
   - Coerce time_column to datetime; if scheme requires time and parse fails,
     exit 2 (recipe/data mismatch) with column name in error.
   - Apply min_rows / min_users / min_items thresholds → if below, exit 4
     with code "min_data_violation" and observed counts in error.

5. Split (training/split.py):
   irspack.split_dataframe_partial_user_holdout. scheme=time_user requires
   time_column.
   Failure: exit 4 (TrainingError) with reason.

6. Tuning (training/search.py):
   - Optuna study, default in-memory; if recipe.training.storage_path is set,
     use SQLite RDBStorage at that path. Document explicitly that the storage
     path MUST be on a local filesystem (SQLite over NFS/EFS corrupts).
     Postgres / Redis / GCS-backed Optuna storage is supported by passing a
     URL with explicit scheme (`postgresql://...`).
   - parallelism > 1 launches N in-process worker threads sharing the study.
   - per_algorithm_trials, if set, partitions the global n_trials across
     classes; otherwise classes share the budget proportionally.
   - per_trial_timeout_seconds is enforced per Optuna trial via callback.
   - Stop on n_trials or timeout_seconds. Score == 0.0 across all trials → exit 4.
   - Progress UX: TTY → rich progress bar; otherwise one structured log line per
     trial. Override with --quiet (no per-trial output) / --verbose (params dump).

7. Train final (training/pipeline.py):
   - Construct best_class(X_full, **best_params).learn().
   - Wrap in irspack IDMappedRecommender (uid/iid as str).

8. Artifact write (artifact/io.py + signing.py):
   - payload = serialized recommender (pickle; required by irspack — see
     Section 8 NOTE).
   - header_json = {recipe_name, recipe_hash, recotem_version, irspack_version,
                    trained_at (UTC ISO-8601 Z), best_class, best_params,
                    best_score, metric, cutoff, tuning, data_stats, key_id}
   - Sign with active signing key (HMAC-SHA256 over header || payload).
   - Local FS:        tempfile + fsync + atomic rename to <output.path>.tmp
                       then rename to either:
                         - <output.path>                  (versioning: always_overwrite)
                         - <output.path>.<sha8>.recotem  (versioning: append_sha)
                       In append_sha mode, also write a pointer file
                       <output.path> that contains the chosen sha-suffixed name
                       (single-line). Watcher reads the pointer to find the
                       current artifact. The pointer write itself is rename-atomic.
   - Object stores:   write to a unique SHA-suffixed object key, then update a
                       small `<output.path>` pointer object (last-write-wins
                       semantics, but the watcher resolves through ETag/version).

9. Stdout: one JSON line summary plus a human summary block. Schema:
   {"event":"train_done","name":"...","run_id":"...","exit_code":0,
    "artifact":"...","best_class":"...","best_score":0.41,"trials":40,
    "trained_at":"...","kid":"prod-2026-q2"}
```

Error contract for schedulers:

- exit 0: success (or no-op skip when lock contended without `--fail-on-busy`).
- exit 2: RecipeError (config / schema / missing env / data column mismatch).
- exit 3: DataSourceError (auth, query failure, network).
- exit 4: TrainingError (split, tuning, score==0, min_data_violation).
- exit 5: ArtifactError (signing key missing, magic bytes, version unsupported).
- exit 1: anything else.

Concurrency:

- Two simultaneous `recotem train recipe.yaml` for the same recipe: the second
  invocation acquires no lock and either skips (default) or fails fast.
- `recotem train` does not require any other Recotem process to be present.

## 7. `recotem serve` data flow and hot-swap

Startup:

1. Load `ServeConfig` from env vars: `RECOTEM_API_KEYS` (comma-separated
   `<kid>:sha256:<hex>`), `RECOTEM_HOST`, `RECOTEM_PORT`,
   `RECOTEM_WATCH_INTERVAL`, `RECOTEM_LOG_FORMAT`, `RECOTEM_SIGNING_KEYS`
   (comma-separated `<kid>:hex`), `RECOTEM_MAX_ARTIFACT_BYTES` (default 2 GiB),
   `RECOTEM_ALLOWED_ORIGINS`, `RECOTEM_ALLOWED_HOSTS`, `RECOTEM_ENV`.
   - If `RECOTEM_API_KEYS` is empty and `--insecure-no-auth` is not passed,
     `RECOTEM_HOST` is forced to `127.0.0.1` and a warning is logged.
   - `--insecure-no-auth` is rejected at startup unless `RECOTEM_ENV` is one
     of `development`, `dev`, `test`. A multi-line WARN banner is emitted on
     each startup and every 60 s while running.
   - `--dev-allow-unsigned` requires `RECOTEM_ENV=development` AND a separate
     flag `--i-understand-this-loads-arbitrary-code`.
2. Emit a single canonical `security.posture` log line: `{auth_enabled,
   bind_host, signing_keys: [<kid>...], env, allowed_hosts, allowed_origins,
   unsafe_mode}` — for SIEM alerting.
3. Read every `*.yaml` directly under `--recipes` (non-recursive) into a
   `Recipe[]` (parallel up to 16). Skip non-`.yaml` files silently. Detect
   duplicate `name`s; fail to start if found. Emit per-recipe load result.
4. For each recipe, attempt artifact load via `artifact.io.read(output.path)`
   using the read-once protocol (Section 8). On verify failure, log WARN and
   skip (server still starts; recipe is `loaded=false` in `/health`).
5. For each recipe with `item_metadata`, load the file and assert all
   `fields` are present (or apply `on_field_missing: null`). Index by
   string-coerced item_id. Reject and skip on schema mismatch.
6. Build `ModelRegistry`. Capture initial mtime/sha for each artifact path
   inside the watcher's own state (so a swap between step 4 and watcher
   start is not missed).
7. Start `Watcher` thread (default poll 5 s, cap 30 s; jittered).
8. Configure FastAPI middlewares: `TrustedHostMiddleware` against
   `RECOTEM_ALLOWED_HOSTS` (default `[127.0.0.1, localhost]`), CORS against
   `RECOTEM_ALLOWED_ORIGINS` (default empty = deny). Install the log
   redaction processor.
9. Start uvicorn (single worker recommended; `--workers N` is supported but
   each worker holds its own copy of every model and runs its own watcher —
   document this).

Per-request (`POST /predict/{name}`):

1. Sync handler executed in FastAPI's threadpool. (Async would force
   `asyncio`-aware locking on `ModelRegistry`; the cost of threadpool dispatch
   is negligible compared to numpy work.)
2. `auth.verify_api_key(request)`: constant-time compare against configured
   set; on match, the matching `kid` is attached to the request context (not
   the key plaintext). 401 on mismatch. Whitespace in the header is rejected
   (no strip).
3. `registry.get(name)` → `ModelEntry | None`. 503 if missing/unhealthy.
4. `entry.recommender.get_recommendation_for_known_user_id(user_id, cutoff)`.
   404 on KeyError (user not seen in training data).
5. If `entry.metadata_df` exists, left-join by item_id and project to
   `recipe.item_metadata.fields`. Apply optional server-side field deny-list
   from `RECOTEM_METADATA_FIELD_DENY` (post-join column drop).
6. Return `{"items": [{"item_id": ..., "score": ..., "<fields>": ...}, ...],
            "model": {"recipe": name, "trained_at": ..., "best_class": ...,
                      "kid": "..."},
            "request_id": "<uuid>"}`. Set `X-Request-ID` response header.

Watcher loop:

- Every `RECOTEM_WATCH_INTERVAL` seconds (with ±10% jitter to avoid
  thundering herd against object stores):
  - For each known recipe, stat the pointer file or object. For object stores,
    use `info()` ETag/version-id rather than mtime.
  - If the pointer changed, **read the entire artifact bytes once** into
    memory (subject to `RECOTEM_MAX_ARTIFACT_BYTES`), compute sha256 over
    those bytes, then HMAC-verify and deserialize from the same in-memory
    buffer. This eliminates the stat→read TOCTOU and the file-still-being-
    written hazard.
  - Concurrent stats are bounded to 16 in flight to avoid hammering object
    stores when many recipes share storage.
  - On verify or load success, build a new `ModelEntry`, then atomically
    replace via `registry.replace(name, entry)`. Previous entry stays alive
    until in-flight requests finish (Python refcount).
  - On verify/load failure, log ERROR (with `kid`, never the key) and keep
    the old entry. Mark `last_load_error` on the entry for `/health`.
- Watcher rescans the recipes directory itself: new YAML appears →
  add recipe; YAML removed → remove from registry. This avoids restart on
  recipe additions.
- Graceful drain: SIGTERM stops the watcher and uvicorn's accept loop, but
  in-flight requests have up to `RECOTEM_DRAIN_SECONDS` (default 30 s) to
  finish. Models are not unloaded during drain.

Other endpoints:

- `GET /health` returns per-recipe status:
  ```json
  {"status": "ok",
   "recipes": {
     "news_articles": {"loaded": true, "trained_at": "...",
                       "best_class": "IALSRecommender", "kid": "prod-2026-q2"},
     "broken":        {"loaded": false, "error": "signature mismatch"}
   }}
  ```
- `GET /models` returns a richer view of registry entries (header JSON,
  redacted of any key material).
- `GET /metrics` (opt-in extras) exposes Prometheus counters.
- OpenAPI is published at `/openapi.json` automatically by FastAPI.

## 8. Artifact format and security

```
.recotem file layout:

  Magic:                "RECOTEM\0"           8 bytes
  Format version:       uint16 LE             2 bytes  (= 1)
  Reserved:             uint16 LE             2 bytes  (= 0; non-zero rejected)
  Key id length:        uint8                 1 byte
  Key id:               UTF-8                 K bytes  (matches serve config)
  HMAC-SHA256:          over (kid_bytes || header_json || payload)
                        32 bytes
  Header JSON length:   uint32 LE             4 bytes  (max 65536; larger rejected)
  Header JSON:          UTF-8                 N bytes  (must parse; non-UTF-8 rejected)
  Payload:              pickle bytes          M bytes  (size ≤ RECOTEM_MAX_ARTIFACT_BYTES)
```

`kid` is a short string (1–32 bytes) identifying the signing-key id. Including
it outside the HMAC-protected region is fine because:

- The HMAC is computed *over* the kid bytes too.
- If the kid is tampered to point at a different key, verify fails.
- If the configured serve does not know the kid, it logs ERROR with the
  presented kid (never the key) and rejects load.

Header JSON example (full):

```jsonc
{
  "recipe_name":     "news_articles",
  "recipe_hash":     "9af2…c1",
  "recotem_version": "2.0.0",
  "irspack_version": "0.4.0",
  "trained_at":      "2026-05-07T01:23:45Z",

  "best_class":      "IALSRecommender",
  "best_params":     { "alpha": 1.32, "reg": 0.0042, "n_components": 64, "n_iter": 7 },
  "best_score":      0.412,
  "metric":          "ndcg",
  "cutoff":          20,

  "tuning": {
    "tried_algorithms":  ["IALSRecommender", "CosineKNNRecommender", "TopPopRecommender"],
    "n_trials":          40,
    "n_completed":       40,
    "best_trial_number": 27,
    "search_seed":       42
  },

  "data_stats": { "n_rows": 1234567, "n_users": 23456, "n_items": 7890,
                  "drop_count": 12, "dedup_policy": "keep_last" }
}
```

Why `best_params` lives in both the header and the payload:

- The payload contains the trained recommender, with its parameters baked in,
  but invisible without deserialization.
- The header carries `best_params` as a duplicate so `recotem inspect` and any
  log-shipping pipeline can read them without touching the payload.
- Single source of truth for serving is the payload (always wins). The header
  is for humans and debugging.

Security posture:

- **Multi-key signing with rotation**: `RECOTEM_SIGNING_KEYS` is a
  comma-separated list of `<kid>:<hex32>`. `recotem train` uses the first
  entry (the active key). `recotem serve` verifies against any entry. Adding
  a new key, retraining, then removing the old key is a zero-downtime
  rotation. Each artifact's kid is logged on load (never the key).
- **HMAC scope**: kid bytes + header JSON + payload. Tampering any of the
  three fails verify. `recotem inspect` runs the *same* verify path even
  though it does not deserialize the payload.
- **Two-tier allow-list** during deserialization:
  1. A hand-enumerated FQCN list of recotem / irspack / builtins / collections
     classes (exact module + class name match).
  2. A module-prefix allow-list scoped to the scientific-stack libraries
     (`numpy.*`, `scipy.sparse.*`) whose internal layout shifts between
     releases (e.g. `numpy.core.*` → `numpy._core.*` in 2.x, plus
     reconstruction helpers like `numpy._core.numeric._frombuffer`).
  3. A deny list for code-execution-prone numpy submodules that would
     otherwise fall under the prefix:
     `numpy.testing.*`, `numpy.distutils.*`, `numpy.f2py.*`,
     `numpy.ctypeslib.*`. The deny list overrides the allow list.

  The hand-enumerated FQCN list is exactly:
  ```
  recotem.serving._compat.IDMappedRecommender
  recotem.training._compat.IDMappedRecommender   # pickle-recorded path
  irspack.utils.id_mapping.IDMapper
  irspack.recommenders.ials.IALSRecommender
  irspack.recommenders.knn.CosineKNNRecommender
  irspack.recommenders.toppop.TopPopRecommender
  irspack.recommenders.rp3.RP3betaRecommender
  irspack.recommenders.dense_slim.DenseSLIMRecommender
  irspack.recommenders.truncsvd.TruncatedSVDRecommender
  builtins.{int, float, bool, list, tuple, dict, str, bytes, complex,
            set, frozenset}
  collections.OrderedDict
  ```

  The lists are frozen per Recotem release; updates ship with a CHANGELOG
  note. HMAC verification remains the primary defence; the FQCN list is the
  RCE backstop for non-scientific code; the prefix list with explicit deny
  entries is the layered control for the scientific stack.
- **Resource caps**:
  - Header JSON length ≤ 64 KiB (declared length larger than this is rejected
    *before* allocation).
  - Payload size ≤ `RECOTEM_MAX_ARTIFACT_BYTES` (default 2 GiB). Larger files
    are rejected before download/read completes.
  - Reserved bytes must be 0; non-zero rejected (forward-compat guard).
  - Format version 0 or > current rejected.
  - Magic mismatch rejected before any further parse.
- **Read-once protocol** (Section 7) closes the stat-then-read TOCTOU.
- **No legacy unsigned fallback**. Recotem 2.0 is a clean rewrite.
- `recotem inspect <path>` reads the header (after HMAC verify) and never
  invokes the unpickler. It is safe to run on attacker-controlled artifacts
  (HMAC + size cap reject before any byte is interpreted as pickle).
- **Key fingerprint logged on serve startup**: `sha256(key)[:8]` per kid.
  Operators can confirm prod ≠ staging without ever logging the key.

NOTE on payload format: irspack's `IDMappedRecommender` depends on scipy
sparse matrices and numpy arrays which cannot be expressed in JSON without
losing structure. Python's pickle is the irspack-native format and is
unavoidable here. The risk is mitigated to acceptable levels by the layered
controls above:

1. Strong magic / version / size checks before any deserialization.
2. HMAC signature verification with multi-kid support and constant-time
   compare; keys never logged.
3. Hand-enumerated FQCN allow-list — RCE backstop independent of HMAC.
4. Required signing key for both train and serve, with no env-default. A
   misconfigured deployment fails closed rather than load arbitrary files.

### Recipe and path security

- Allowed schemes for any path field (`output.path`, `source.path`,
  `item_metadata.path`): `s3://`, `gs://`, `az://`, or a bare local path.
  `file://`, `http://`, `https://`, `ftp://`, `ftps://`, `memory://` are
  rejected at recipe load.
- Embedded credentials (`s3://AKIA…:secret@bucket/`) are rejected.
- Local `output.path` resolves to absolute and (if `RECOTEM_ARTIFACT_ROOT`
  is set) must lie under it after `realpath` resolution; symlink escapes
  are rejected.
- Recipe file paths under `--recipes` are resolved to absolute and
  asserted to remain inside the recipes root.
- `name` is regex-validated at load AND immediately before any
  filesystem/URL use.

### Sensitive-string redaction

A structlog processor strips the following keys (case-insensitive) from any
log event before output:

- `x-api-key`, `authorization`, `cookie`
- `recotem_signing_key`, `recotem_signing_keys`
- `recotem_api_keys`
- `*_secret*`, `*_password*`, `aws_*`, `gcp_*`, `google_*`

A unit test asserts none of these appear in captured log output across a
full training and serving lifecycle, including at trace level.

## 9. Auth and access control

- Single scope: `predict`. Multiple scopes (predict / train / admin) from 1.x
  are dropped — there is no admin API and there is no separate train API.
- API keys are configured server-side via `RECOTEM_API_KEYS`, a
  comma-separated list of `<kid>:sha256:<hex64>` entries. Plaintext keys
  are not stored anywhere. `recotem keygen` outputs `(kid, plaintext, hash)`
  triples, plaintext shown only on stdout.
- Keys are 32 bytes (256 bits), base64url-encoded (43 chars no padding) at
  the plaintext level. Server rejects entries whose hex hash is not 64 chars.
- `recotem keygen` refuses to emit keys shorter than 32 bytes.
- The stored hash is `scrypt(plaintext, salt=b"recotem.api-key.v1",
  n=2, r=8, p=1, dklen=32)` as 64-char hex. The fixed salt acts as a
  domain-separation label — there is no rainbow-table risk because the
  input is already a 256-bit random token, so the stored digest cannot be
  cross-substituted into any other context that happens to hash the same
  plaintext (Stripe / GitHub style).  scrypt is used at minimum cost
  parameters because additional cost is wasted on inputs that are already
  infeasible to brute-force; the choice of scrypt over HMAC / BLAKE2b is
  driven by static analysers that classify any non-KDF construction as
  weak regardless of input entropy. The wire prefix `sha256:` identifies
  the digest family / 32-byte hex digest, not the construction — bumping
  `v1` to a new label string would invalidate all stored hashes and
  require key re-issue.
- Constant-time compare via `hmac.compare_digest`.
- The matching `kid` (never the plaintext or hash) is attached to request
  context for logging.
- If `RECOTEM_API_KEYS` is empty, server refuses to bind to anything other
  than `127.0.0.1`. Explicit override: `recotem serve --insecure-no-auth`,
  gated by `RECOTEM_ENV` (Section 7).
- Whitespace inside or around the API key value is treated as part of the
  key (no implicit strip), so misconfigured clients get 401 deterministically.

## 10. Observability

Logging:

- structlog with two output formats (json | console) selected by
  `RECOTEM_LOG_FORMAT`. Default `console` for terminals, `json` in
  containers (when `sys.stderr.isatty()` is False).
- A redaction processor (Section 8) is the first processor in the chain.
- Every train log line carries `{recipe, run_id}`. Every serve log line
  carries `{recipe, request_id, kid?}`.
- Optuna callback emits one structured line per trial:
  `{"event": "trial_done", "trial": N, "score": ..., "params": {...}}`.
- A canonical `security.posture` line is emitted at serve startup
  (Section 7).
- Train emits a single `{"event":"train_done", ...}` JSON line after
  success, with stable schema (Section 6 step 9).

Metrics (opt-in via `pip install recotem[metrics]`):

- `/metrics` Prometheus exposition on the serve app.
- `recotem_predict_total{recipe,status}`,
  `recotem_predict_latency_seconds{recipe}` (histogram),
  `recotem_model_loaded{recipe}`,
  `recotem_artifact_load_failures_total{recipe}`,
  `recotem_active_recipes`,
  `recotem_swap_total{recipe,result}`.

Error model:

- Train: stderr 1-line JSON `{"event":"train_error","error":"...","code":"...",
  "recipe":"...","run_id":"...","exit_code":N,"trained_at":"..."}` followed
  by a human block; exit code per Section 6.
- Serve: FastAPI HTTPException JSON `{"detail": "...", "code": "..."}`.
- No DRF-style envelope. No HTML error pages.

Health:

- `GET /health` is per-recipe (Section 7). A single overall `status: ok|degraded`
  is also included for trivial liveness probes. `degraded` if any recipe is
  `loaded=false`.

## 11. Test strategy

Unit + integration (pytest). The cases below are exhaustive: every spec rule
in Sections 5–10 has at least one corresponding negative-path test.

### artifact/

- `test_sign_verify_roundtrip` — happy path.
- `test_one_byte_tamper_rejected` — flip a byte in payload; verify rejects.
- `test_truncated_before_hmac_rejected`.
- `test_truncated_mid_payload_rejected`.
- `test_magic_bytes_wrong_rejected_before_hmac_work`.
- `test_format_version_zero_rejected`.
- `test_format_version_unsupported_future_rejected`.
- `test_header_length_exceeding_uint32_rejected_without_allocation`.
- `test_header_length_exceeding_64KiB_rejected`.
- `test_payload_size_exceeding_max_bytes_rejected`.
- `test_hmac_valid_over_wrong_key_rejected`.
- `test_hmac_valid_with_unknown_kid_rejected`.
- `test_payload_class_outside_whitelist_rejected` — parameterised over
  `os.system`, `subprocess.Popen`, `numpy.testing.run_module_suite`,
  `builtins.exec`, `posix.system`.
- `test_header_not_valid_utf8_rejected`.
- `test_reserved_bytes_nonzero_rejected`.
- `test_atomic_local_write_via_tempfile_rename`.
- `test_versioning_append_sha_writes_pointer_atomically`.
- `test_inspect_runs_full_hmac_and_does_not_unpickle` — verifies inspect
  rejects tampered payloads even without deserialization.

### recipe/

- `test_env_var_expansion_undefined_raises_with_var_name_in_message`.
- `test_env_var_expansion_blacklisted_var_rejected` — try `RECOTEM_SIGNING_KEY`
  inside any field.
- `test_env_var_expansion_allowed_only_with_RECOTEM_RECIPE_prefix`.
- `test_env_var_expansion_recursive_does_not_loop`.
- `test_env_var_expansion_inside_query_field_rejected`.
- `test_query_parameters_bound_via_bigquery_param_placeholders`.
- `test_source_no_type_discriminator_rejected`.
- `test_source_unknown_type_rejected_with_known_types_listed`.
- `test_recipe_name_with_slash_rejected`.
- `test_recipe_name_over_64_chars_rejected`.
- `test_recipe_name_empty_rejected`.
- `test_time_user_split_without_time_column_rejected`.
- `test_duplicate_recipe_name_in_directory_rejected_at_startup`.
- `test_heldout_ratio_above_one_rejected`.
- `test_n_trials_zero_rejected`.
- `test_item_metadata_fields_empty_list_rejected`.
- `test_recipe_with_no_source_rejected`.
- `test_recipe_error_includes_yaml_line_number`.
- `test_path_field_with_file_scheme_rejected`.
- `test_path_field_with_http_scheme_rejected`.
- `test_path_field_with_embedded_credentials_rejected`.
- `test_local_output_path_outside_artifact_root_rejected`.
- `test_csv_subschema_dtype_overrides_applied`.
- `test_recipe_name_revalidated_before_filesystem_use`.
- `test_recipe_path_outside_recipes_root_rejected`.

### datasource/

- `test_two_plugins_register_same_name_rejected_at_discovery`.
- `test_bigquery_extra_not_installed_clear_error_with_extra_name`.
- `test_csv_missing_required_column_raises_DataSourceError`.
- `test_csv_empty_after_header_raises_DataSourceError`.
- `test_parquet_corrupt_wraps_in_DataSourceError`.
- `test_bigquery_credentials_failure_wraps_in_DataSourceError_exit3`.
- `test_dynamic_discriminated_union_includes_third_party_type`.

### training/

- `test_all_trials_failing_raises_TrainingError_exit4`.
- `test_all_scores_zero_raises_TrainingError_exit4`.
- `test_timeout_before_first_trial_raises_TrainingError`.
- `test_single_algorithm_with_empty_param_space_produces_artifact` — TopPop.
- `test_per_algorithm_trials_partition_global_budget`.
- `test_per_trial_timeout_kills_long_trial`.
- `test_one_structured_log_per_trial`.
- `test_split_producing_empty_test_set_raises_TrainingError`.
- `test_min_rows_violation_raises_exit4_min_data`.
- `test_min_users_violation_raises_exit4`.
- `test_min_items_violation_raises_exit4`.
- `test_dedup_keep_last_resolves_duplicates`.
- `test_dedup_sum_weight_aggregates_counts`.
- `test_drop_null_ids_default_true_records_drop_count`.
- `test_string_coerce_user_and_item_ids`.

### metadata/

- `test_warning_logged_and_row_skipped_for_null_item_id`.
- `test_field_outside_allowlist_never_in_response`.
- `test_field_in_allowlist_but_missing_in_file_with_on_field_missing_error`.
- `test_field_in_allowlist_but_missing_with_on_field_missing_null`.
- `test_metadata_field_deny_overrides_recipe_fields`.
- `test_predict_returns_null_metadata_for_unjoined_item`.
- `test_metadata_id_string_coerced_matches_recommender_ids`.

### serving/

- `test_in_flight_request_completes_with_old_model_during_swap`.
- `test_two_consecutive_swaps_register_second_artifact`.
- `test_malformed_swap_keeps_old_model_marks_health_error`.
- `test_serves_immediately_at_startup_when_artifact_valid`.
- `test_yaml_deleted_then_predict_returns_503`.
- `test_api_key_with_padding_whitespace_rejected_401`.
- `test_empty_keys_without_insecure_flag_forces_localhost_bind`.
- `test_insecure_no_auth_refused_unless_RECOTEM_ENV_dev`.
- `test_dev_allow_unsigned_requires_two_explicit_flags`.
- `test_multiple_keys_any_authenticates`.
- `test_three_configured_keys_fourth_unrecognized_rejected_401`.
- `test_malformed_api_keys_entry_fails_startup`.
- `test_api_key_compare_uses_hmac_compare_digest`.
- `test_concurrent_predict_during_swap_no_data_race`.
- `test_unhealthy_recipe_returns_503_not_404`.
- `test_non_yaml_files_in_recipes_dir_silently_ignored`.
- `test_duplicate_recipe_name_fails_startup`.
- `test_initial_mtime_captured_in_watcher_state_no_missed_swap`.
- `test_TrustedHost_blocks_unrecognized_host`.
- `test_CORS_blocks_unconfigured_origin`.
- `test_log_redaction_strips_api_key_from_all_events`.
- `test_log_redaction_strips_signing_key_from_all_events`.
- `test_log_redaction_strips_aws_creds_from_all_events`.
- `test_request_id_returned_in_X_Request_ID_header`.
- `test_kid_attached_to_request_context_not_plaintext`.
- `test_security_posture_log_emitted_at_startup`.
- `test_drain_seconds_grace_window_honoured_on_SIGTERM`.
- `test_response_includes_kid_field_in_model_block`.
- `test_health_overall_degraded_when_any_recipe_unloaded`.
- `test_metrics_endpoint_off_by_default`.

### cli/

- `test_train_exit0_on_success`.
- `test_train_exit2_on_recipe_error`.
- `test_train_exit3_on_datasource_error`.
- `test_train_exit4_on_all_trials_fail`.
- `test_train_exit4_on_min_data_violation`.
- `test_train_exit5_on_signing_key_missing_without_dev_flag`.
- `test_train_exit1_on_unexpected_exception`.
- `test_train_lock_contention_skips_with_exit0_default`.
- `test_train_fail_on_busy_exits_nonzero_when_locked`.
- `test_validate_exit0_on_valid_recipe_with_mocked_connectivity`.
- `test_validate_exit2_on_schema_error`.
- `test_validate_probes_datasource_auth`.
- `test_inspect_exit0_on_valid_artifact`.
- `test_inspect_exit5_on_wrong_magic`.
- `test_inspect_exit5_on_unknown_kid`.
- `test_keygen_emits_kid_plaintext_hash_triple`.
- `test_keygen_refuses_short_key_length`.
- `test_schema_command_emits_valid_jsonschema`.
- `test_serve_smoke_starts_and_responds_to_health`.

### e2e

- `tests/e2e/run.sh` runs `recotem train tests/e2e/recipe.yaml`, then
  `recotem serve --recipes tests/e2e/recipes &`, then `curl /predict/test`,
  asserting on JSON shape and HTTP status. Uses MovieLens100K via fixture.

### Fuzz / property

- `tests/fuzz/test_artifact_loader.py`: hypothesis-driven byte mutation of a
  valid artifact; loader must never raise an unhandled exception and must
  reject any non-bit-perfect file.
- `tests/fuzz/test_recipe_loader.py`: hypothesis YAML fragments + env states;
  loader must always either return a valid Recipe or raise RecipeError.

### CI

- GitHub Actions: ruff + pytest + e2e + docker build.
- Drop the existing `frontend` Playwright suite together with the frontend.
- Keep Trivy / CodeQL / pre-commit.
- Add a `secrets-in-logs` CI check: run e2e with `RECOTEM_LOG_FORMAT=json`,
  capture stdout/stderr, regex-grep for `sha256:[0-9a-f]{64}`, `AKIA[0-9A-Z]{16}`,
  any literal value of test signing keys; fail on hit.

## 12. Repository layout and migration

This work happens on branch `docs/recotem-2-design`. The branch lands the
spec, plan, implementation, and tests. A single PR delivers Recotem 2.0.

Target tree post-migration:

```
recotem/                              # repository root
├── pyproject.toml                    # PEP 621
├── uv.lock
├── README.md, CHANGELOG.md, LICENSE
├── Dockerfile                        # multi-stage: base / builder / runtime
├── docker-compose.example.yaml       # cron train + serve sample
├── src/recotem/                      # (Section 4)
├── tests/
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── fuzz/
├── docs/
│   ├── README.md (overview)
│   ├── quickstart.md                 # 5-minute install → train CSV → curl /predict
│   ├── recipe-reference.md
│   ├── data-sources/{bigquery,csv}.md
│   ├── deployment/{docker,k8s,cron}.md
│   ├── operations.md                 # key rotation, recovery, sizing, troubleshooting
│   ├── security.md                   # trust boundaries, threat model summary
│   └── plugin-authoring.md           # with runnable example under examples/plugins/
├── examples/
│   ├── ga4-bigquery/recipe.yaml
│   ├── csv-local/recipe.yaml
│   ├── plugins/echo-source/          # cookiecutter-style plugin template
│   └── k8s/{cronjob.yaml,serve-deployment.yaml,serve-service.yaml}
└── helm/recotem/                     # trimmed from 1.x: only ServiceAccount,
                                      # serve Deployment + Service, optional
                                      # CronJob template, NetworkPolicy, PDB, HPA
```

Removals from 1.x (deleted entirely in this PR):

- `backend/` Django app, all models, views, serializers, services, migrations,
  conftest, manage.py.
- `inference/` directory (its FastAPI app is rewritten under `src/recotem/serving/`).
- `frontend/` Vue 3 SPA in full (pages, layouts, stores, composables, types,
  build config, Playwright).
- `proxy.dockerfile`, `nginx.conf`, `nginx-inference.conf`,
  `compose-inference.yaml`.
- `compose.yaml` is replaced by `docker-compose.example.yaml`.
- `compose-dev.yaml` is removed (no infra to bring up).
- `helm/recotem` Helm chart: trim to the serve-only set listed above.
- All Postgres / Redis / Channels / Daphne / Celery / Beat references in env
  files and CI.

CI changes:

- Replace `pre-commit.yml` ruff/pre-commit content (kept) with the new repo
  layout paths.
- Replace `run-test.yml` with pytest + e2e + secrets-in-logs CI checks
  against `tests/`.
- Adjust `release.yml` to build the single Docker image and publish to PyPI.
- `codeql.yml` keeps Python only.

## 13. Plugin contract (DataSource)

A third-party DataSource plugin is a Python package that:

1. Declares an entry point in the `recotem.datasources` group:
   ```toml
   [project.entry-points."recotem.datasources"]
   echo = "recotem_echo:EchoSource"
   ```
2. Provides a class with the following attributes:
   ```python
   class EchoSource:
       type_name: ClassVar[str] = "echo"             # discriminator value
       Config: ClassVar[type[BaseModel]]              # pydantic config schema
       extras_required: ClassVar[list[str]] = []      # pip extras to suggest
                                                       # if import fails

       def __init__(self, config: "EchoSource.Config") -> None: ...

       def fetch(self, ctx: FetchContext) -> pd.DataFrame: ...
   ```
3. Raises `recotem.datasource.base.DataSourceError` from `fetch()` for any
   external/transient failure. Other exceptions surface as exit 1.
4. Must not import optional deps at module top-level. Defer imports to
   `__init__` so missing extras yield a clear `DataSourceError` mentioning
   the required extra by name (e.g. `pip install recotem[bigquery]`).

`recotem.datasource.registry.get_source_types()`:

- On first call, iterates `entry_points(group="recotem.datasources")`,
  imports each, validates the contract, and assembles a dynamic pydantic
  discriminated-union `SourceConfig = Annotated[Union[...], Field(discriminator='type')]`.
- Conflicting `type_name` values raise `DataSourceError` at discovery time,
  failing both `recotem train` and `recotem serve` startup with a clear
  enumeration of the conflicting plugins.

Trust note: installed plugins are trusted code — equivalent to `pip install`
from the same source. Operators should pin plugin versions, hash-pin via
`pip-tools` / uv, and review third-party plugin source before deployment.
This is documented in `docs/security.md`.

## 14. Open questions (deliberately deferred)

Concrete decisions left to the implementation plan, not the spec:

- Whether to ship a thin `recotem.datasources.gsheets` builtin alongside CSV
  for very small organisations (pluggable, can come later).
- Concrete fsspec extras packaging matrix (`recotem[s3]`, `recotem[gcs]`).
- Whether `recotem inspect` should also print a sha256 of the file for
  artifact-registry integrations.
- Helm chart values schema for `serve.recipes` source (PVC vs object store).
- Optional artifact-at-rest encryption (`RECOTEM_ARTIFACT_ENCRYPTION_KEY`)
  using AES-GCM wrapping. Defer until an operator asks.
