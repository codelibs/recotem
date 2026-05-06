# Recotem 2.0 Design

- **Status**: Draft
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
- HMAC-signed model artifacts with safe deserialization.
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

### Non-goals

- Real-time feature ingestion or online learning (still batch retrain).
- Multi-tenant SaaS hosting.
- Hyperparameter management UIs (config is the YAML, history is the artifact
  files in your storage).

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
- Config: pydantic v2 (typed YAML loader with env expansion).
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
│   ├── models.py       pydantic Recipe, SourceConfig, SchemaConfig,
│   │                   ItemMetadataConfig, TrainingConfig, SplitConfig,
│   │                   OutputConfig
│   ├── loader.py       YAML to Recipe, env expansion, path normalization
│   └── errors.py       RecipeError with line-number context
├── datasource/
│   ├── base.py         DataSource protocol: fetch() -> pd.DataFrame
│   ├── csv.py          CSVSource (covers parquet via pandas)
│   ├── bigquery.py     BigQuerySource (google-cloud-bigquery, ADC)
│   └── registry.py     entry_points group "recotem.datasources"
├── training/
│   ├── pipeline.py     run_training(recipe) -> TrainResult (public)
│   ├── search.py       Optuna driver
│   ├── split.py        irspack split wrapper
│   ├── evaluate.py     Evaluator setup
│   └── algorithms.py   alias resolution (IALS to IALSRecommender)
├── artifact/
│   ├── format.py       binary container layout constants + dataclasses
│   ├── io.py           write/read via fsspec (local / s3 / gs)
│   └── signing.py      HMAC-SHA256 sign + safe loader whitelist
├── metadata/
│   └── loader.py       item metadata DataFrame indexed by item_id
├── serving/
│   ├── app.py          create_app(serve_config) -> FastAPI
│   ├── registry.py     ModelRegistry: name -> ModelEntry (RLock)
│   ├── watcher.py      mtime + sha256 polling, atomic registry replace
│   ├── auth.py         X-API-Key dependency
│   └── routes.py       /predict/{name}, /health, /models
├── config.py           ServeConfig from env vars
├── logging.py          structlog setup (json | console)
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
- `datasource/registry.py` discovers third-party plugins via entry points so
  users add adapters without forking.

## 5. Recipe schema

A recipe is the single source of truth for what to train and what to serve.
One recipe → one model → one `/predict/{name}` endpoint.

```yaml
name: news_articles                           # required, unique within --recipes dir.
                                              # Becomes /predict/{name}.

source:
  type: bigquery                              # discriminator: bigquery | csv | parquet
  query: |
    SELECT user_pseudo_id AS user_id,
           (SELECT value.int_value FROM UNNEST(event_params) WHERE key='article_id') AS item_id,
           TIMESTAMP_MICROS(event_timestamp) AS ts
    FROM   `proj.analytics_123.events_*`
    WHERE  _TABLE_SUFFIX BETWEEN
           FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
       AND FORMAT_DATE('%Y%m%d', CURRENT_DATE())
       AND event_name = 'select_content'
  project: my-gcp-project                     # optional; falls back to GOOGLE_CLOUD_PROJECT

schema:
  user_column: user_id
  item_column: item_id
  time_column: ts                             # optional; required for time_user / time_global split

item_metadata:                                # optional
  type: parquet
  path: gs://bucket/items.parquet             # local / s3:// / gs:// via fsspec
  fields: [title, category, image_url]       # allow-list of columns to return

training:
  algorithms: [IALS, CosineKNN, TopPop]       # >=1. Multiple → cross-algorithm search.
  metric: ndcg                                # ndcg | map | recall | hit
  cutoff: 20
  n_trials: 40
  timeout_seconds: 1800
  storage_path: ""                            # empty → in-memory Optuna; set for resumable parallel
  split:
    scheme: time_user                         # random | time_global | time_user
    heldout_ratio: 0.1
    test_user_ratio: 1.0
    seed: 42

output:
  path: ./artifacts/news_articles.recotem     # train writes, serve reads
```

Schema rules:

- `name` must match `^[A-Za-z0-9_-]{1,64}$`.
- `source.type` is a pydantic discriminator; each source has its own typed
  config. Unknown `type` is a `RecipeError`.
- Environment variables of the form `${VAR}` are expanded in any string field
  during loading. Missing variables are a `RecipeError` (no silent empty
  string).
- Secrets must not be embedded. GCP auth uses ADC; API keys are configured
  separately on the serve side. A recipe is meant to be checked into git.
- `output.path` accepts local paths, `s3://`, `gs://`, etc. via fsspec.

## 6. `recotem train` data flow

```
1. Load recipe YAML → Recipe (pydantic).
   Failure: stderr human message + JSON error line, exit 2.

2. DataSource.fetch() → pandas DataFrame.
   - csv:      pandas.read_csv (gz/zip transparent, fsspec)
   - parquet:  pandas.read_parquet
   - bigquery: google-cloud-bigquery + Storage Read API
   Validate schema columns exist and types coerce.
   Failure: exit 3.

3. Split (training/split.py):
   irspack.split_dataframe_partial_user_holdout. scheme=time_user requires
   time_column.
   Failure: exit 2 (recipe/data mismatch) or 4 (split error).

4. Tuning (training/search.py):
   - Optuna study, default in-memory; if recipe.training.storage_path is set,
     use SQLite RDBStorage at that path (allows parallel workers + resumable).
   - If algorithms has one entry, search params within that class only.
   - If multiple, sample recommender_class_name as a categorical at trial root.
   - Stop on n_trials or timeout_seconds. Score == 0.0 across all trials → exit 4.

5. Train final (training/pipeline.py):
   - Construct best_class(X_full, **best_params).learn().
   - Wrap in irspack IDMappedRecommender (uid/iid as str).

6. Artifact write (artifact/io.py):
   - payload = serialized recommender (irspack IDMappedRecommender; required
     because it contains scipy sparse matrices and numpy arrays).
   - header_json = {recipe_name, recipe_hash, irspack_version, recotem_version,
                    trained_at, best_class, best_params, best_score, metric,
                    cutoff, tuning, data_stats}
   - sign (HMAC-SHA256 over header || payload) and write to tempfile, fsync,
     atomic rename to recipe.output.path.

7. Stdout: one JSON line summary plus a human summary block.
```

Error contract for schedulers:

- exit 0: success.
- exit 2: RecipeError (config / schema / missing env / data column mismatch).
- exit 3: DataSourceError (auth, query failure, network).
- exit 4: TrainingError (split, tuning, score==0).
- exit 5: ArtifactError (signing key missing, magic bytes, version unsupported).
- exit 1: anything else.

## 7. `recotem serve` data flow and hot-swap

Startup:

1. Load `ServeConfig` from env vars: `RECOTEM_API_KEYS` (comma-separated
   `sha256:<hex>`), `RECOTEM_HOST`, `RECOTEM_PORT`, `RECOTEM_WATCH_INTERVAL`,
   `RECOTEM_LOG_FORMAT`, `RECOTEM_SIGNING_KEY`.
   - If `RECOTEM_API_KEYS` is empty and `--insecure-no-auth` is not passed,
     bind host is forced to `127.0.0.1` and a warning is logged.
2. Read every `*.yaml` under `--recipes` into a `Recipe[]`.
3. For each recipe, attempt artifact load via `artifact.io.read(output.path)`.
   On verify failure, log WARN and skip (server still starts).
4. For each recipe with item_metadata, load and index by item_id.
5. Build `ModelRegistry`, then start `Watcher` thread (default poll 5s).
6. Start uvicorn.

Per-request (`POST /predict/{name}`):

1. `auth.verify_api_key(request)` (constant-time compare against
   `RECOTEM_API_KEYS`). 401 on mismatch.
2. `registry.get(name)` → `ModelEntry | None`. 503 if missing/unhealthy.
3. `entry.recommender.get_recommendation_for_known_user_id(user_id, cutoff)`.
   404 on KeyError (user not seen in training data).
4. If `entry.metadata_df` exists, left-join by item_id and project to
   `recipe.item_metadata.fields`.
5. Return: `{"items": [{"item_id": ..., "score": ..., "<fields>": ...}, ...],
             "model": {"recipe": name, "trained_at": ..., "best_class": ...},
             "request_id": "<uuid>"}`. Set `X-Request-ID` response header.

Watcher loop:

- Every `RECOTEM_WATCH_INTERVAL` seconds:
  - For each known recipe, stat `output.path`. If mtime changed and the file
    has stabilised (size unchanged across two polls), recompute sha256.
  - If sha256 changed, attempt `artifact.io.read()`. On success build a new
    `ModelEntry` and atomically replace via `registry.replace(name, entry)`.
    The previous entry stays alive until in-flight requests finish (Python
    refcount handles this).
  - On verify or load failure, log ERROR and keep the old entry. Mark
    `last_load_error` on the entry for `/health`.
- Watcher also rescans the recipes directory itself: new YAML appears →
  add recipe; YAML removed → remove from registry. This avoids restart on
  recipe additions.

Other endpoints:

- `GET /health` returns per-recipe status:
  ```json
  {"status": "ok",
   "recipes": {
     "news_articles": {"loaded": true, "trained_at": "...", "best_class": "IALSRecommender"},
     "broken":        {"loaded": false, "error": "signature mismatch"}
   }}
  ```
- `GET /models` returns a richer view of registry entries (header JSON).
- OpenAPI is published at `/openapi.json` automatically by FastAPI.

## 8. Artifact format and security

```
.recotem file layout:

  Magic:               "RECOTEM\0"           8 bytes
  Format version:      uint16 LE             2 bytes  (= 1)
  Reserved:            uint16 LE             2 bytes  (= 0)
  HMAC-SHA256:         over (header_json || payload)
                       32 bytes
  Header JSON length:  uint32 LE             4 bytes
  Header JSON:         UTF-8                 N bytes
  Payload:             serialized model      M bytes
```

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

  "data_stats": { "n_rows": 1234567, "n_users": 23456, "n_items": 7890 }
}
```

Why best_params lives in both the header and the payload:

- The payload contains the trained recommender, with its parameters baked in,
  but invisible without deserialization.
- The header carries best_params as a duplicate so `recotem inspect` and any
  log-shipping pipeline can read them without touching the payload.
- Single source of truth for serving is the payload (always wins). The header
  is for humans and debugging.

Security posture:

- Signing key (`RECOTEM_SIGNING_KEY`, 32 bytes recommended) is required for
  both train and serve. Without it, both commands fail unless
  `--dev-allow-unsigned` is explicitly passed. `recotem keygen` emits a key.
- HMAC covers `header_json` + `payload` so neither can be tampered with
  independently.
- `find_class` whitelist on deserialization: only `irspack.*`, `numpy.*`,
  `scipy.sparse.*`, `recotem.*` may be resolved. Any other class triggers an
  ArtifactError before construction.
- Magic + version guard against unrelated files and future format changes.
- No legacy unsigned compatibility path. Recotem 2.0 is a clean rewrite.
- `recotem inspect <path>` reads only the header (after HMAC verification) and
  never deserializes the payload. It is safe to run on attacker-controlled
  artifacts (the HMAC step rejects them before anything else).

NOTE on payload format: irspack's `IDMappedRecommender` depends on scipy
sparse matrices and numpy arrays which cannot be expressed in JSON without
losing structure. Python's pickle is the irspack-native format and is
unavoidable here. The risk is mitigated to acceptable levels by:

1. HMAC signature verification before any deserialization (a tampered or
   unsigned file is rejected before bytes are interpreted).
2. A class allow-list during deserialization (only the four module prefixes
   above can resolve to real classes).
3. Required signing key for both train and serve, with no env-default. A
   misconfigured deployment will fail closed rather than load arbitrary files.

## 9. Auth and access control

- Single scope: `predict`. Multiple scopes (predict / train / admin) from 1.x
  are dropped — there is no admin API and there is no separate train API.
- API keys are configured server-side via `RECOTEM_API_KEYS`, a
  comma-separated list of `sha256:<hex>` entries. Plaintext keys are not
  stored anywhere. `recotem keygen` outputs `(plaintext, sha256:hex)` pairs.
- Constant-time compare against the configured set.
- If `RECOTEM_API_KEYS` is empty, server refuses to bind to anything other
  than `127.0.0.1`. Explicit override: `recotem serve --insecure-no-auth`.

## 10. Observability

Logging:

- structlog with two output formats (json | console) selected by
  `RECOTEM_LOG_FORMAT`. Default `console` for terminals, `json` in containers.
- Every train log line carries `{recipe, run_id}`. Every serve log line carries
  `{recipe, request_id}`.
- Optuna callback emits one structured line per trial:
  `{"event": "trial_done", "trial": N, "score": ..., "params": {...}}`.

Metrics (opt-in via `pip install recotem[metrics]`):

- `/metrics` Prometheus exposition on the serve app.
- `recotem_predict_total{recipe,status}`,
  `recotem_predict_latency_seconds{recipe}` (histogram),
  `recotem_model_loaded{recipe}`,
  `recotem_artifact_load_failures_total{recipe}`.

Error model:

- Train: stderr 1-line JSON `{"error": "...", "code": "..."}` followed by a
  human block; exit code per Section 6.
- Serve: FastAPI HTTPException JSON `{"detail": "...", "code": "..."}`.
- No DRF-style envelope. No HTML error pages.

Health:

- `GET /health` is per-recipe (Section 7). A single overall ok flag is also
  included for trivial liveness probes.

## 11. Test strategy

Unit + integration (pytest):

- `recipe/loader.py`: env expansion, missing fields, invalid types, line numbers.
- `datasource/csv.py`: against MovieLens100K.
- `datasource/bigquery.py`: against mocked BigQuery client (no real GCP in CI).
- `training/pipeline.py`: end-to-end on a small MovieLens slice with
  `n_trials=2`. Asserts artifact writes successfully and verifies.
- `artifact/`: sign/verify roundtrip; a 1-byte tamper rejects; magic / version
  / class whitelist all rejected paths.
- `serving/`: FastAPI `TestClient` covers `/predict`, `/health`, `/models`,
  hot-swap (writes a new artifact in tempdir, reduces watch interval to 0.05s,
  asserts replacement within 0.5s), auth (configured / empty / wrong key).
- `cli.py`: Typer `CliRunner` smoke tests for each subcommand.

End-to-end (one):

- A bash script that runs `recotem train tests/e2e/recipe.yaml`, then
  `recotem serve --recipes tests/e2e/recipes &`, then `curl /predict/test`.

CI:

- GitHub Actions: ruff + pytest + e2e + docker build.
- Drop the existing `frontend` Playwright suite together with the frontend.
- Keep Trivy / CodeQL / pre-commit.

## 12. Repository layout and migration

This work happens on branch `docs/recotem-2-design`. The branch first lands
this design document. A subsequent PR (or set of PRs) replaces 1.x content.

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
│   └── e2e/
├── docs/
│   ├── README.md (overview)
│   ├── recipe-reference.md
│   ├── data-sources/{bigquery,csv}.md
│   ├── deployment/{docker,k8s,cron}.md
│   └── plugin-authoring.md
├── examples/
│   ├── ga4-bigquery/recipe.yaml
│   ├── csv-local/recipe.yaml
│   └── k8s/{cronjob.yaml,serve-deployment.yaml,serve-service.yaml}
└── helm/recotem/                     # trimmed from 1.x: only ServiceAccount,
                                      # serve Deployment + Service, optional
                                      # CronJob template, NetworkPolicy, PDB, HPA
```

Removals from 1.x (deleted entirely in the implementation PR):

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
- Replace `run-test.yml` with pytest-only against `tests/`.
- Adjust `release.yml` to build the single Docker image and publish to PyPI.
- `codeql.yml` keeps Python only.

## 13. Open questions

These are deliberately deferred to the implementation plan, not the spec:

- Whether to ship a thin `recotem.datasources.gsheets` builtin alongside CSV
  for very small organisations (pluggable, can come later).
- Concrete fsspec extras packaging matrix (`recotem[s3]`, `recotem[gcs]`).
- Whether `recotem inspect` should also print a sha256 of the file for
  artifact-registry integrations.
- Helm chart values schema for `serve.recipes` source (PVC vs object store).
