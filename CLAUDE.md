# Recotem

Recipe-driven recommender training and serving on irspack. Distributed as a
single Python package (`pip install recotem`) plus a single Docker image.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                  recotem (single Python package)                 │
├──────────────────────────────────────────────────────────────────┤
│  CLI (Typer)                                                     │
│  ├─ recotem train   <recipe.yaml>      batch: fetch→train→sign   │
│  ├─ recotem serve   --recipes <dir>    FastAPI /v1/recipes/:*   │
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
```

`train` and `serve` communicate **only via signed artifact files**. They can
run on different machines. Hot-swap is file-mtime-driven and recipe-scoped.

## Directory Layout

```
src/recotem/
├── cli.py              Typer entry; thin orchestration only
├── recipe/             pydantic v2 Recipe + YAML loader + env expansion
├── datasource/         DataSource Protocol + entry_points discovery (csv / parquet / bigquery / sql)
├── training/           Optuna search + irspack train; per-recipe file lock
│   └── features.py     fetch feature tables, build encoder state, encode per phase (search vs. final refit)
├── artifact/           HMAC-signed binary container with FQCN allow-list
├── metadata/           item metadata loader (CSV/Parquet via fsspec)
├── serving/            FastAPI app, ModelRegistry, ArtifactWatcher
├── _idmap.py           Neutral home for IDMappedRecommender (canonical FQCN)
├── _features.py        Neutral home for side-feature encoder state + pure encode logic (shared by training/ and serving/)
├── _irspack_compat.py  Verified-compatible allow-list guarding irspack pickle skew
├── _http_fetch.py      SSRF-guarded HTTP/HTTPS fetcher with sha256 verify
├── _size_cap.py        Shared download-size cap helper (used by csv source + metadata loader)
├── _metrics_bigquery.py  Neutral Prometheus counter for BQ Storage API fallbacks (no serving dep)
├── _metrics_watcher.py   Neutral Prometheus counter for recipes-dir scan failures (no serving dep)
├── log_redaction.py    structlog processor stripping API/signing keys + creds
├── config.py           ServeConfig / TrainConfig from env vars
└── logging.py          structlog setup with redaction processor first

tests/
├── unit/               per-module tests (recipe, artifact, training, ...)
├── integration/        in-process train + serve + recommend
├── fuzz/               hypothesis byte mutations on artifact / recipe loaders
└── e2e/                bash script: train → serve → curl /v1/recipes/{name}:recommend

docs/
├── getting-started.md  Docker / pip walkthrough → train → /v1/recipes/{name}:recommend
├── recipe-reference.md every recipe field, type, default, validation
├── data-sources/       bigquery.md, csv.md, sql.md
├── deployment/         docker.md, k8s.md, cron.md
├── operations.md       key rotation, recovery, sizing, troubleshooting
├── security.md         trust boundaries, FQCN allow-list, threat model
└── plugin-authoring.md DataSource plugin contract walkthrough

helm/recotem/           serve-only chart with optional CronJob train
examples/               quickstart/, csv-local/, sql-sqlite/, ga4-bigquery/, k8s/, plugins/echo-source/, tutorial-purchase-log/
Dockerfile              multi-stage python:3.12-slim, appuser:1000
compose.yaml            train one-shot + serve long-running (tutorial)
```

## Quick Start (development)

```bash
# Install (uv handles the venv)
uv sync --all-extras

# Generate a signing key + (optional) API key
uv run recotem keygen --type signing
uv run recotem keygen --type api

export RECOTEM_SIGNING_KEYS="dev:<hex64>"
export RECOTEM_API_KEYS="key1:sha256:<hex64>"

# Train from a recipe
uv run recotem train examples/tutorial-purchase-log/recipe.yaml

# Serve from a directory of recipes
uv run recotem serve --recipes ./recipes/ --port 8080

# Recommend
curl -X POST http://localhost:8080/v1/recipes/news_articles:recommend \
     -H "X-API-Key: <plaintext>" \
     -H "Content-Type: application/json" \
     -d '{"user_id":"u1","limit":10}'
```

## Recipe model

A recipe is the single source of truth: 1 YAML = 1 model = 1 `/v1/recipes/{name}:recommend` (plus the related/batch verbs).
See `docs/recipe-reference.md` for the full schema. Highlights:

- `source.type` is a discriminator (`csv` | `parquet` | `bigquery` | `sql` | plugins).
- Env-var expansion is restricted to `${RECOTEM_RECIPE_*}` and never applied
  inside `source.query` / `source.query_parameters` (forecloses SQL injection).
- Path scheme: `source.path` and `item_metadata.path` accept an explicit
  allow-list of schemes: `""` (bare local path), `file://`, `s3://`, `gs://`,
  `az://`, `abfs://`, `abfss://`, `http://`, `https://`. Schemes are
  explicitly enumerated rather than relying on fsspec's full registry to
  prevent unvetted handlers from being reachable via recipe content.
  Chained fsspec protocols (containing `::`) are rejected. `output.path`
  is a strict subset of the above — it rejects `http://`, `https://`,
  `ftp://`, `ftps://`, and `memory://` (write not supported). For
  network-scheme inputs (`http://`, `https://`), `sha256` is mandatory and
  `RECOTEM_MAX_DOWNLOAD_BYTES` (default 256 MiB) caps the body. Embedded
  URI credentials are rejected.
- Cleansing block: `drop_null_ids`, `dedup` policy, `min_rows / min_users /
  min_items` data preconditions.
- Multi-algorithm Optuna search with optional per-algorithm trial budgets.
- Optional `features:` block (sibling to `source:` / `item_metadata:`) turns
  on feature-aware iALS — no separate flag. `features.item` / `features.user`
  each declare a `source` (same datasource registry as the top-level
  `source`), an `id_column`, and a `columns` list of `{name, encoding,
  delimiter?, min_frequency?}` (`categorical` | `numerical` | `multi_label`).
  See `docs/recipe-reference.md#features`.

## Artifact format

Binary container `magic | version | reserved | kid | hmac | header_json | payload`.

- HMAC scope: `kid_bytes || header_json || payload`. Tampering anywhere fails verify.
- Header JSON carries `recipe_name`, `recipe_hash`, `best_class`, `best_params`,
  `best_score`, `metric`, `cutoff`, `tuning`, `data_stats`, `recotem_version`,
  `irspack_version`, `trained_at`. Inspectable without deserialisation via
  `recotem inspect`. `best_class` + `irspack_version` are the two fields
  `_irspack_compat.py` reads to decide whether the payload is safe to
  deserialize on this host.
- Multi-kid `KeyRing` (env: `RECOTEM_SIGNING_KEYS=kid1:hex,kid2:hex`) enables
  zero-downtime key rotation. Operations doc has the four-step procedure (Step 4 includes verification).
- Payload uses Python's native binary serialisation because irspack's
  `IDMappedRecommender` carries scipy sparse matrices and numpy arrays. Defence
  in depth: HMAC verify before any byte is interpreted, plus a hand-enumerated
  FQCN allow-list augmented by a narrow `numpy.*` / `scipy.sparse.*` module-
  prefix allow-list (with a deny-list for high-risk submodules) during load.
  See `docs/security.md`.

## Conventions

- Python 3.12+, `uv` for dependency management. Never use `pip` / `python`
  directly — always `uv add` / `uv run python`.
- Ruff is the linter and formatter (`uv run ruff check src tests` /
  `uv run ruff format src tests`). Line-length 88. Selected rules in
  `pyproject.toml`.
- pytest 8 + hypothesis 6. `@pytest.mark.slow` deselected by default.
- `from __future__ import annotations` is used everywhere, including the
  serving router. FastAPI dependency arguments are written as
  `kid: str = Depends(_require_auth)` (not `Annotated[...]`) in
  `serving/routes.py` so that `Depends` is resolved as a runtime
  default rather than a stringified annotation.
- structlog logger per module; the redaction processor in
  `recotem.log_redaction` is first in the chain and strips API keys, signing
  keys, and cloud creds. Lives at the top level so `train`-only invocations do
  not pull in the serving package.
- Modules `training/` and `serving/` never import each other; they communicate
  only via artifact files. Shared classes such as `IDMappedRecommender` live
  in neutral top-level modules (`recotem._idmap`) so neither sub-package
  depends on the other. Exception: `cli.py` imports from both sides, but all
  sub-package imports there are **function-local deferred imports** (inside each
  command function body), so neither sub-package is loaded at module import time.
- The IPython stub required by irspack's transitive
  `fastprogress -> IPython.display` is installed idempotently by both
  `recotem.training._compat` (for training-package callers) and
  `recotem._idmap` (for direct importers, e.g. serving).

## CLI exit codes

| Code | Constant | Meaning |
|------|----------|---------|
| 0 | `_EXIT_SUCCESS` | success |
| 1 | `_EXIT_UNKNOWN` | unhandled / unmapped exception |
| 2 | `_EXIT_RECIPE` | `RecipeError` (schema, env, path scheme) |
| 3 | `_EXIT_DATASOURCE` | `DataSourceError` (CSV parse, missing column, BQ access) |
| 4 | `_EXIT_TRAINING` | `TrainingError` (all trials failed, min-data violation) |
| 5 | `_EXIT_ARTIFACT` | `ArtifactError` (magic / version / HMAC verify) |
| 6 | `_EXIT_LOCK_CONTESTED` | per-recipe training lock held by another process |
| 7 | `_EXIT_HTTP_FETCH` | `HttpFetchError` (SSRF guard / sha256 mismatch / scheme-changing redirect / byte cap) |
| 8 | `_EXIT_CONFIG` | configuration error (e.g. signing keys missing without `--dev-allow-unsigned`) |

## Test commands

```bash
uv run pytest tests                          # full suite (~5s without slow)
uv run pytest tests -m slow                  # MovieLens100K end-to-end
uv run pytest tests/integration tests/fuzz   # cross-module + hypothesis
uv run ruff check src tests
uv run ruff format --check src tests
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `RECOTEM_SIGNING_KEYS` | (required) | `kid:hex64,kid2:hex64` for HMAC sign/verify (64 hex = 32 bytes). |
| `RECOTEM_API_KEYS` | (empty) | `kid:sha256:hex64,...` for serve auth. Empty forces 127.0.0.1 bind. |
| `RECOTEM_HOST` / `RECOTEM_PORT` | 127.0.0.1 / 8080 | uvicorn bind. Must be `0.0.0.0` inside Docker; overridden to 127.0.0.1 when no API keys are set. |
| `RECOTEM_WATCH_INTERVAL` | 5 | Watcher poll seconds (clamped 1–30). |
| `RECOTEM_MAX_ARTIFACT_BYTES` | 2 GiB | Per-artifact size cap. Clamped [1 MiB, 16 GiB]. |
| `RECOTEM_MAX_DOWNLOAD_BYTES` | 256 MiB | Raw I/O bytes cap on source-path reads (HTTP/HTTPS, local, and object-store). Clamped [1 MiB, 16 GiB]. Does NOT cap the decompressed DataFrame size — see `docs/security.md#decompressed-size-cap-not-enforced-medium-5`. |
| `RECOTEM_HTTP_TIMEOUT_SECONDS` | 30 | Connect/read timeout for HTTP/HTTPS source fetch. Clamped [1, 600]. |
| `RECOTEM_HTTP_ALLOW_PRIVATE` | (empty) | Truthy (`1`/`true`/`yes`/`on`) opts the HTTP fetcher into accepting private/loopback/link-local destinations. Default refuses RFC1918 / `127.0.0.0/8` / `169.254.0.0/16` to block SSRF on cloud-metadata services. |
| `RECOTEM_ALLOWED_HOSTS` | 127.0.0.1,localhost | TrustedHostMiddleware list. Whitespace-only comma input falls back to default. |
| `RECOTEM_ALLOWED_ORIGINS` | (empty) | CORS allow-list. Empty = deny. |
| `RECOTEM_ENV` | (empty) | `--insecure-no-auth` permitted when set to `development`, `dev`, or `test`; `--dev-allow-unsigned` permitted only when set to `development`. See `docs/security.md`. |
| `RECOTEM_DRAIN_SECONDS` | 30 | SIGTERM grace window. Clamped [1, 300]. |
| `RECOTEM_LOG_FORMAT` | auto | `auto` / `json` / `console`. |
| `RECOTEM_MAX_PAYLOAD_BYTES` | 512 MiB | Per-payload cap (post-HMAC-verify) for serve-side deserialization. Clamped [1 MiB, 16 GiB]. Smaller than `RECOTEM_MAX_ARTIFACT_BYTES` to bound deserialization memory expansion. |
| `RECOTEM_MAX_BODY_BYTES` | 128 MiB | Max serve-side HTTP **request** body size. Clamped [1 MiB, 2 GiB]. A `BodySizeLimitMiddleware` returns `413 PAYLOAD_TOO_LARGE` when the declared `Content-Length` exceeds the cap, and enforces a running byte count on chunked/streamed bodies with no `Content-Length` so the header cannot be omitted to bypass it. Default preserves the entire legitimate request space (largest well-formed body main accepts is ~72 MiB) while blocking GB-scale bodies that Starlette would buffer and parse before validation. |
| `RECOTEM_ARTIFACT_ROOT` | (empty) | If set, local `output.path` must lie under it. |
| `RECOTEM_RECIPE_*` | — | Allow-listed for `${...}` recipe expansion. |
| `RECOTEM_METADATA_FIELD_DENY` | (empty) | Comma-separated columns stripped from `/v1/recipes/{name}:recommend` and `:recommend-related` responses. |
| `RECOTEM_METRICS_ENABLED` | (empty) | Opt-in Prometheus `/metrics` endpoint. Truthy values: `1`, `true`, `yes`, `on`. Requires `recotem[metrics]` extra. |
| `RECOTEM_LOCK_DIR` | (empty) | Override directory for per-recipe training lock files. Local outputs always lock at `<output_path>.lock`; remote outputs (`s3://`, `gs://`, ...) need a host-local path and fall back to `<tempdir>/recotem-locks/`. `flock` is host-local — across hosts use scheduler-level mutex (`concurrencyPolicy: Forbid`). |
| `RECOTEM_BQ_REQUIRE_STORAGE_API` | (empty) | When truthy (`1`/`true`/`yes`/`on`), the BigQuery source raises `DataSourceError` instead of falling back to the REST path when the Storage Read API fails. Requires the service account to hold `bigquery.readSessions.create`. |
| `RECOTEM_ALLOW_IRSPACK_VERSION_SKEW` | (empty) | Truthy downgrades the serve-side irspack version-skew check from `ArtifactError` to a warning. The default rule is an **allow-list** (`_irspack_compat.py`): same **major.minor** always loads (patch drift tolerated); a differing major.minor loads only when `(best_class, header_mm, running_mm)` is in the verified table — CosineKNN / TopPop / RP3beta / DenseSLIM / TruncatedSVD across (0,4)↔(0,5), both directions. IALS (known break: `IALSModelConfig.__setstate__` arity 7→10 at 0.5.0) and BPRFM (unverifiable: gated behind the separately installed `lightfm` package, which has no py3.12 release, so irspack never exports it) are refused, as is a missing/non-str `best_class` on a real skew (fail-closed) and every not-yet-verified future transition. Missing/unparseable version fails **open**. The remedy is to retrain; this flag is for operators who know their artifact is unaffected. |
| `RECOTEM_STARTUP_PARALLELISM` | (empty = auto) | Number of parallel threads used to load artifacts at `recotem serve` startup. Leave unset (default) for auto-sizing (`min(len(recipes), 8)`). Setting to `0` is NOT a sentinel — it clamps to 1 and emits an `env_var_clamped` warning. Clamped [1, 32]. Set to `1` to force sequential loading for debugging. |
| `RECOTEM_MAX_SQL_ROWS` | 50_000_000 | Hard cap on rows returned by the SQL data source. Clamped [1_000, 500_000_000]. Caps **row count**, not DataFrame resident memory — see `docs/data-sources/sql.md` for the memory-bound caveat. |
| `RECOTEM_SQL_ALLOW_PRIVATE` | (empty) | Truthy opts the SQL source into private/loopback DSN hosts (default deny, for SSRF). Covers every driver-routing form — netloc, `?host=`, `?hostaddr=`, `?service=`, `?unix_socket=`, absolute-path host, and network DSNs with no host info — all default-deny without this flag. Also disables the DNS-rebinding re-check before each probe/fetch — opting in means trusting the host end-to-end. |
| `RECOTEM_MAX_FEATURE_DIM` | 5000 | Cap on the encoded feature dimension per side (item and user checked independently) for feature-aware iALS. Clamped [16, 100000]. Vocabulary is built from the whole fetched feature table (not just interaction-covered rows), so dimension scales with **catalog size, not interaction count**; `min_frequency` on high-cardinality `categorical`/`multi_label` columns is the only recipe-level lever. Cost is cubic in this number (dense `Fᵀ F` Cholesky) and multiplies with `training.parallelism`. See `docs/operations.md#feature-aware-ials-sizing`. |

## CI

`.github/workflows/`:
- `test.yml` — ruff + pytest unit/integration + e2e + secrets-in-logs grep
- `docker.yml` — build + push multi-arch image to ghcr.io on main + tags;
  build-only on PRs
- `codeql.yml` — Python CodeQL, push/PR + weekly schedule

## Reference docs

- Getting started: `docs/getting-started.md`
- Operations runbook: `docs/operations.md`
- Security model: `docs/security.md`
