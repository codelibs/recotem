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
```

`train` and `serve` communicate **only via signed artifact files**. They can
run on different machines. Hot-swap is file-mtime-driven and recipe-scoped.

## Directory Layout

```
src/recotem/
├── cli.py              Typer entry; thin orchestration only
├── recipe/             pydantic v2 Recipe + YAML loader + env expansion
├── datasource/         DataSource Protocol + entry_points discovery (csv / bq)
├── training/           Optuna search + irspack train; per-recipe file lock
├── artifact/           HMAC-signed binary container with FQCN allow-list
├── metadata/           item metadata loader (CSV/Parquet via fsspec)
├── serving/            FastAPI app, ModelRegistry, ArtifactWatcher
├── config.py           ServeConfig / TrainConfig from env vars
└── logging.py          structlog setup with redaction processor first

tests/
├── unit/               per-module tests (recipe, artifact, training, ...)
├── integration/        in-process train + serve + predict
├── fuzz/               hypothesis byte mutations on artifact / recipe loaders
└── e2e/                bash script: train → serve → curl /predict

docs/
├── quickstart.md       5-minute install → recipe → train → /predict
├── recipe-reference.md every recipe field, type, default, validation
├── data-sources/       bigquery.md, csv.md
├── deployment/         docker.md, k8s.md, cron.md
├── operations.md       key rotation, recovery, sizing, troubleshooting
├── security.md         trust boundaries, FQCN allow-list, threat model
├── plugin-authoring.md DataSource plugin contract walkthrough
└── superpowers/specs/  architectural specs (source of truth)

helm/recotem/           serve-only chart with optional CronJob train
examples/               csv-local, ga4-bigquery, k8s/, plugins/echo-source/
Dockerfile              multi-stage python:3.12-slim, appuser:1000
docker-compose.example.yaml   train one-shot + serve long-running
```

## Quick Start (development)

```bash
# Install (uv handles the venv)
uv sync --all-extras

# Generate a signing key + (optional) API key
uv run recotem keygen --type signing
uv run recotem keygen --type api

export RECOTEM_SIGNING_KEYS="active:<hex>"
export RECOTEM_API_KEYS="key1:sha256:<hex64>"

# Train from a recipe
uv run recotem train examples/csv-local/recipe.yaml

# Serve from a directory of recipes
uv run recotem serve --recipes ./recipes/ --port 8080

# Predict
curl -H "X-API-Key: <plaintext>" \
     -d '{"user_id":"u1","cutoff":10}' \
     http://localhost:8080/predict/news_articles
```

## Recipe model

A recipe is the single source of truth: 1 YAML = 1 model = 1 `/predict/{name}`.
See `docs/recipe-reference.md` for the full schema. Highlights:

- `source.type` is a discriminator (`csv` | `parquet` | `bigquery` | plugins).
- Env-var expansion is restricted to `${RECOTEM_RECIPE_*}` and never applied
  inside `source.query` / `source.query_parameters` (forecloses SQL injection).
- Path scheme allow-list: bare local | `s3://` | `gs://` | `az://`. No
  `file://`, `http(s)://`, `ftp(s)://`, `memory://`. Embedded URI credentials
  are rejected.
- Cleansing block: `drop_null_ids`, `dedup` policy, `min_rows / min_users /
  min_items` data preconditions.
- Multi-algorithm Optuna search with optional per-algorithm trial budgets.

## Artifact format

Binary container `magic | version | reserved | kid | hmac | header_json | payload`.

- HMAC scope: `kid_bytes || header_json || payload`. Tampering anywhere fails verify.
- Header JSON carries `recipe_name`, `recipe_hash`, `best_class`, `best_params`,
  `best_score`, `metric`, `cutoff`, `tuning`, `data_stats`. Inspectable without
  deserialisation via `recotem inspect`.
- Multi-kid `KeyRing` (env: `RECOTEM_SIGNING_KEYS=kid1:hex,kid2:hex`) enables
  zero-downtime key rotation. Operations doc has the four-step procedure.
- Payload uses Python's native binary serialisation because irspack's
  `IDMappedRecommender` carries scipy sparse matrices and numpy arrays. Defence
  in depth: HMAC verify before any byte is interpreted, plus a hand-enumerated
  FQCN allow-list during load. See `docs/security.md`.

## Conventions

- Python 3.12+, `uv` for dependency management. Never use `pip` / `python`
  directly — always `uv add` / `uv run python`.
- Ruff is the linter and formatter (`uv run ruff check src tests` /
  `uv run ruff format src tests`). Line-length 88. Selected rules in
  `pyproject.toml`.
- pytest 8 + hypothesis 6. `@pytest.mark.slow` deselected by default.
- `from __future__ import annotations` is used everywhere except where it
  breaks FastAPI dependency introspection (e.g. `routes.py` uses
  `kid: str = Depends(_require_auth)` instead of `Annotated[...]`).
- structlog logger per module; the redaction processor in
  `recotem.serving.log_redaction` is first in the chain and strips API keys,
  signing keys, and cloud creds.
- Modules `training/` and `serving/` never import each other; they
  communicate only via artifact files.
- `recotem.training` imports `_compat` first to install the IPython stub
  required by irspack's transitive `fastprogress -> IPython.display`.

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
| `RECOTEM_SIGNING_KEYS` | (required) | `kid:hex32,kid2:hex32` for HMAC sign/verify. |
| `RECOTEM_API_KEYS` | (empty) | `kid:sha256:hex64,...` for serve auth. Empty forces 127.0.0.1 bind. |
| `RECOTEM_HOST` / `RECOTEM_PORT` | 0.0.0.0 / 8080 | uvicorn bind. Overridden by 127.0.0.1 when no API keys. |
| `RECOTEM_WATCH_INTERVAL` | 5 | Watcher poll seconds (clamped 1–30). |
| `RECOTEM_MAX_ARTIFACT_BYTES` | 2 GiB | Per-artifact size cap. |
| `RECOTEM_ALLOWED_HOSTS` | 127.0.0.1,localhost | TrustedHostMiddleware list. |
| `RECOTEM_ALLOWED_ORIGINS` | (empty) | CORS allow-list. Empty = deny. |
| `RECOTEM_ENV` | (empty) | Gates `--insecure-no-auth` and `--dev-allow-unsigned`. |
| `RECOTEM_DRAIN_SECONDS` | 30 | SIGTERM grace window. |
| `RECOTEM_LOG_FORMAT` | auto | `json` | `console`. |
| `RECOTEM_ARTIFACT_ROOT` | (empty) | If set, local `output.path` must lie under it. |
| `RECOTEM_RECIPE_*` | — | Allow-listed for `${...}` recipe expansion. |
| `RECOTEM_METADATA_FIELD_DENY` | (empty) | Comma-separated columns stripped from `/predict` responses. |

## CI

`.github/workflows/`:
- `test.yml` — ruff + pytest unit/integration + e2e + secrets-in-logs grep
- `release.yml` — build + push multi-arch image to ghcr.io on main + tags;
  build-only on PRs
- `codeql.yml` — Python CodeQL, push/PR + weekly schedule

## Reference docs

- Spec: `docs/superpowers/specs/2026-05-07-recotem-2-design.md`
- Quickstart: `docs/quickstart.md`
- Operations runbook: `docs/operations.md`
- Security model: `docs/security.md`
