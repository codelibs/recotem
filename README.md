# Recotem

[![PyPI](https://img.shields.io/pypi/v/recotem.svg)](https://pypi.org/project/recotem/)
[![Python](https://img.shields.io/pypi/pyversions/recotem.svg)](https://pypi.org/project/recotem/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![CI](https://github.com/codelibs/recotem/actions/workflows/test.yml/badge.svg)](https://github.com/codelibs/recotem/actions/workflows/test.yml)

Recipe-driven recommender training and serving, built on
[irspack](https://github.com/tohtsky/irspack). One YAML recipe describes
where the data lives, how to train, and where to write the result —
`recotem train` produces a signed binary artifact, `recotem serve`
mounts it as a `/predict/{name}` HTTP endpoint and hot-swaps when a new
artifact appears. No database, no message broker, no admin UI.

## Why Recotem

Most recommender stacks pull in a service mesh of databases, queues, and
control planes before you can train your first model. Recotem keeps the
moving parts to a recipe file and a binary artifact:

- **Single binary, two commands.** `recotem train` runs as a batch job;
  `recotem serve` runs as a long-lived FastAPI process. They share
  nothing but the artifact file on disk (or object storage).
- **Reproducible by construction.** Recipes are versioned with your
  code; artifacts are HMAC-signed with a SHA-checked header you can
  inspect without loading the model.
- **Hot-swap, no restart.** The serving process watches the artifact
  directory and atomically swaps the in-memory model when training
  emits a new file.
- **Bring-your-own scheduler.** `recotem train` is a normal process —
  drive it from cron, Airflow, a Kubernetes CronJob, or anything else.

## Features

- Recipe-driven: 1 YAML = 1 model = 1 `/predict/{name}` endpoint
- Hyperparameter search across irspack algorithms via Optuna
- Pluggable data sources (built-in: CSV / Parquet / BigQuery; extend via Python entry points)
- HMAC-signed artifacts with multi-key rotation and a deterministic
  FQCN allow-list at deserialization time
- API-key authentication (`X-API-Key`); keys hashed at rest
- fsspec paths everywhere — local, S3, GCS, HTTPS, anything fsspec speaks
- Optional Prometheus metrics endpoint, structured JSON logs with
  built-in secret redaction

## Data Sources

- **CSV / Parquet** — local files or any fsspec-reachable URL (S3, GCS, Azure, HTTPS).
- **BigQuery** — SQL queries with Storage Read API support.
- **SQL** (PostgreSQL / MySQL / SQLite) — via SQLAlchemy 2. See `docs/data-sources/sql.md`.
- **Google Analytics 4** — direct Data API integration (no BigQuery Export needed). See `docs/data-sources/ga4.md`.
- **Custom plugins** — implement the `DataSource` Protocol and register via `recotem.datasources` entry-points.

## Install

```bash
pip install recotem                 # core
pip install "recotem[bigquery]"     # BigQuery data source
pip install "recotem[metrics]"      # Prometheus metrics endpoint
```

Requires Python 3.12+. A multi-arch Docker image is published to
`ghcr.io/codelibs/recotem`.

## Quickstart

The repository ships with a self-contained example at
[`examples/quickstart/`](examples/quickstart/) — recipe, dataset, and
artifact directory all in one place. Train a TopPop recommender from a
60-user CSV in under a minute.

```bash
# 1. Generate keys (once per machine). Copy the values into the exports below.
recotem keygen --type signing --kid dev
recotem keygen --type api     --kid dev

export RECOTEM_SIGNING_KEYS="dev:<signing-plaintext>"   # used by train + serve
export RECOTEM_API_KEYS="dev:sha256:<api-hash>"         # used by serve
export RECOTEM_API_PLAINTEXT="<api-plaintext>"          # used by curl below

# 2. Train, serve
recotem train examples/quickstart/recipe.yaml
recotem serve --recipes examples/quickstart/ &

# Wait for the server to become ready before sending traffic.
until curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health | grep -q "200"; do sleep 1; done

# 3. Predict
curl -X POST http://localhost:8080/predict/top_picks \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "u01", "cutoff": 5}'
```

```json
{
  "items": [{"item_id": "i00", "score": 0.91}],
  "model": {"recipe": "top_picks", "trained_at": "...",
            "best_class": "TopPopRecommender", "kid": "dev"},
  "request_id": "..."
}
```

The recipe itself is 11 lines — every other field has a sensible default.
See [`examples/quickstart/recipe.yaml`](examples/quickstart/recipe.yaml)
for the source of truth and
[docs/recipe-reference.md](docs/recipe-reference.md) for the full schema.

### Which env var is needed where?

| Variable | Required by | Purpose |
|---|---|---|
| `RECOTEM_SIGNING_KEYS` | `train` and `serve` | HMAC sign / verify artifact files (server keeps plaintext; needed for both sides) |
| `RECOTEM_API_KEYS` | `serve` | Authenticate `/predict` callers (server keeps **hash** only) |
| `X-API-Key: <plaintext>` | HTTP clients | Sent by clients on every `/predict` call; server re-hashes and compares |

Both variables accept multiple comma-separated entries (`kid:value,kid2:value,…`)
to enable zero-downtime key rotation — that is why they are pluralised.

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                  recotem (single Python package)                       │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   recipe.yaml ──▶ recotem train ──▶ artifact.recotem ──▶ recotem serve │
│                   (batch job)        (HMAC-signed)        (FastAPI,    │
│                                                            hot-swap)   │
│                                                                        │
│   any scheduler          local FS, S3,             POST /predict/{name}│
│   (cron / k8s / …)       GCS, fsspec               X-API-Key auth      │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

`train` and `serve` communicate **only via signed artifact files**. They
can run on different machines; the watcher swaps models per recipe based
on file mtime.

## Documentation

- [Getting started](docs/getting-started.md) — Docker Compose / pip walkthrough end-to-end
- [Recipe reference](docs/recipe-reference.md) — every field documented
- [Operations](docs/operations.md) — key rotation, sizing, troubleshooting
- [Security](docs/security.md) — threat model, IAM scopes, secrets handling
- [Plugin authoring](docs/plugin-authoring.md) — write a custom data source
- [Documentation index](docs/README.md)

## Contributing

Issues and pull requests welcome. Development uses
[uv](https://docs.astral.sh/uv/) for dependency management:

```bash
uv sync --all-extras
uv run pytest tests
uv run ruff check src tests
```

See `CLAUDE.md` (or the project guidelines therein) for the full
contributor workflow.

## License

[Apache License 2.0](LICENSE).
