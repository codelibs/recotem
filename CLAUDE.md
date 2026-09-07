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
├── _lightfm_compat.py  Silences LightFM's no-OpenMP import warning (bprfm extra)
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

helm/recotem/           serve-only chart with optional CronJob train
examples/               quickstart/, csv-local/, sql-sqlite/, ga4-bigquery/, feature-aware/, k8s/, plugins/echo-source/, tutorial-purchase-log/
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
# --port/-p and --host/-H override RECOTEM_PORT / RECOTEM_HOST for one run;
# the no-API-keys loopback force still wins over --host.
uv run recotem serve --recipes ./recipes/ --host 127.0.0.1 --port 8080

# Recommend
curl -X POST http://localhost:8080/v1/recipes/news_articles:recommend \
     -H "X-API-Key: <plaintext>" \
     -H "Content-Type: application/json" \
     -d '{"user_id":"u1","limit":10}'
```

## Recipe model

A recipe is the single source of truth: 1 YAML = 1 model = 1 `/v1/recipes/{name}:recommend` (plus the related/batch verbs).
See `https://recotem.org/2.1/docs/recipe-reference` for the full schema. Highlights:

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
  See `https://recotem.org/2.1/docs/recipe-reference#features`.

## Artifact format

Binary container `magic | version | reserved | kid | hmac | header_json | payload`.

- HMAC scope: `kid_bytes || header_json || payload`. Tampering inside those
  bytes fails verify. The 4-byte `header_len` field is **not** covered — it
  only says where the header stops and the payload starts, and both halves
  are authenticated as one run of bytes. Moving that boundary therefore
  still passes `verify_hmac` (`recotem inspect` prints `HMAC: OK`) and is
  caught one layer later by the header JSON parse or the deserializer,
  reported as exit 5. It shifts a split point; it cannot inject a byte.
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
  See `https://recotem.org/2.1/docs/security`.

## Documentation policy

- **The documentation lives in another repository.** This repo carries no
  `docs/` tree. User- and operator-facing documentation is published at
  https://recotem.org from
  [codelibs/recotem-docs](https://github.com/codelibs/recotem-docs), expected at
  `../recotem-docs`. When a change alters documented behaviour, the doc fix is a
  PR there, against the in-development version directory (`2.1/docs/…`,
  `2.1/guide/…`) **and its `2.1/ja/…` twin** — that site ships both languages.
- **Links to it are versioned**: `https://recotem.org/2.1/docs/<page>`. The
  segment is MAJOR.MINOR, and it is bumped in this repo at the **dev bump**, not
  at release. `.github/scripts/check-release-tag.sh` refuses a tag whose version
  disagrees with the URLs in the tree, because several of them ship where nobody
  can correct them afterwards — the text of a `DataSourceError`, the JSON Schema
  `recotem schema` emits, the `/v1/metrics` HELP string, `README.md` on PyPI.
- **Documentation is managed as documentation.** Never write a test that reads a
  markdown file (`README.md`, `CLAUDE.md`, an example README, a skill's
  `SKILL.md`) and asserts on its prose, headings, tables or phrasing. When a
  change alters documented behaviour, fix the doc and stop there; test the
  behaviour itself (an exit code, a response, a rendered manifest), never the
  sentence describing it. Asserting that a *code-produced string* contains a
  documentation URL is fine — that is a behaviour assertion.
- **There is no `CHANGELOG.md`.** The change record is the
  [GitHub Release](https://github.com/codelibs/recotem/releases) for each tag,
  written at release time from `git log vPREV..main`. A PR does not add a
  changelog entry.
- Operator-facing upgrade steps go in the recotem-docs page published at
  https://recotem.org/2.1/docs/upgrading (edit `2.1/docs/upgrading.md` and
  `2.1/ja/docs/upgrading.md` there), under a `## <prev> → <this>` heading — that
  page outlives the release notes.

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

0 success, 1 unmapped, 2 recipe, 3 datasource, 4 training, 5 artifact,
6 lock contested, 7 http fetch, 8 config.

The table, the constants and the exception mapping live together in
`src/recotem/_exit_codes.py` — read its module docstring rather than a copy.
It also records the three codes that are narrower than their name suggests
(6 needs `--fail-on-busy`; 7 is scoped to the HTTP fetch pipeline; 8 absorbs
several `TrainingError` codes). Why a `serve` bind failure is 8 and not
uvicorn's own 3 is at the `except SystemExit` branch in `src/recotem/cli.py`.

Operator-facing version: https://recotem.org/2.1/docs/exit-codes

## Test commands

```bash
uv run pytest tests                          # full suite (~5s without slow)
uv run pytest tests -m slow                  # MovieLens100K end-to-end
uv run pytest tests/integration tests/fuzz   # cross-module + hypothesis
uv run bash tests/e2e/run.sh                 # train → serve → curl (see below)
uv run ruff check src tests
uv run ruff format --check src tests
```

`tests/e2e/run.sh` calls the `recotem` console script directly, so it must be
launched through `uv run` (or from an activated venv). Under bare `bash` it
exits **127** with `run.sh: line 72: recotem: command not found` immediately
after `[e2e] Generating API key...`, which reads like a keygen failure rather
than a PATH problem.

## Environment variables

`src/recotem/config.py` is the index — its module docstring enumerates every
`RECOTEM_*` variable with its default and clamp, says which five are fatal to
mis-set (the rest warn and silently use the default), and names the reading
site for the ones this module does not own.

Operator-facing version: https://recotem.org/2.1/docs/environment-variables

## CI

`.github/workflows/`:
- `test.yml` — ruff + pytest unit/integration + e2e + secrets-in-logs grep
- `docker.yml` — build + push multi-arch image to ghcr.io on main + tags;
  build-only on PRs
- `codeql.yml` — Python CodeQL, push/PR + weekly schedule

## Reference docs

All published at https://recotem.org — source in `../recotem-docs`, under `2.1/`.

- Getting started: https://recotem.org/2.1/guide/
- Operations runbook: https://recotem.org/2.1/docs/operations
- Security model: https://recotem.org/2.1/docs/security
- Upgrade paths: https://recotem.org/2.1/docs/upgrading
