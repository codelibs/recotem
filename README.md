# Recotem 2.0

Fetch data from a source on a schedule, train a recommender, and serve it as an API.

One recipe YAML → one trained model → one `/predict/{name}` endpoint.

## What it is

- **CLI** (`recotem train`, `recotem serve`) — no web UI, no database, no message broker.
- **Plugin data sources** — builtin BigQuery (GA4-ready) and CSV/Parquet; extend via entry points.
- **Hyperparameter search** — irspack algorithms + Optuna; just pick a metric.
- **FastAPI inference server** — hot-swaps models on artifact change, no restart needed.
- **HMAC-signed artifacts** — multi-key rotation, FQCN allow-listed deserialization.
- **API-key auth** — header `X-API-Key`; keys never stored in plaintext.

## What it is not

No web admin UI, no multi-user projects, no A/B testing, no PostgreSQL, no Redis, no Celery.

## Install

```bash
pip install recotem

# BigQuery support
pip install "recotem[bigquery]"

# Prometheus metrics endpoint
pip install "recotem[metrics]"
```

Requires Python 3.12+.

## Hello world (CSV)

The repository ships with a self-contained example at
[`examples/helloworld/`](examples/helloworld/) — recipe, dataset, and
output directory all in one place. For a runnable end-to-end tutorial that
fetches a real dataset over HTTPS via Docker Compose, see
[docs/getting-started.md](docs/getting-started.md).

**1. Generate keys**

```bash
recotem keygen --type signing --kid dev   # → copy the env_entry plaintext
recotem keygen --type api     --kid dev   # → copy the env_entry hash; keep plaintext for X-API-Key

export RECOTEM_SIGNING_KEYS="dev:<plaintext-hex-from-signing>"
export RECOTEM_API_KEYS="dev:sha256:<hash-hex-from-api>"
```

**2. Train and serve**

From the repository root:

```bash
recotem train examples/helloworld/recipe.yaml
# → examples/helloworld/artifacts/top_picks.<sha>.recotem (signed; gitignored)

recotem serve --recipes examples/helloworld/
# FastAPI on :8080, hot-swaps on file change
```

**3. Predict**

```bash
curl -X POST http://localhost:8080/predict/top_picks \
  -H "X-API-Key: <api-plaintext-from-step-1>" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "u01", "cutoff": 5}'
```

```json
{
  "items": [{"item_id": "i00", "score": 0.91}, ...],
  "model": {"recipe": "top_picks", "trained_at": "2026-05-07T01:23:45Z",
            "best_class": "TopPopRecommender", "kid": "dev"},
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

The recipe itself is 11 lines — every other field has a sensible default.
See [`examples/helloworld/recipe.yaml`](examples/helloworld/recipe.yaml) for
the source of truth and
[docs/recipe-reference.md](docs/recipe-reference.md) for the full schema.

## Scheduling retrains

`recotem train` is a plain process — schedule it with whatever you already use:

```
# cron
0 3 * * * RECOTEM_SIGNING_KEYS=... recotem train /etc/recotem/recipe.yaml
```

```yaml
# Kubernetes CronJob
schedule: "0 3 * * *"
```

The server detects the new artifact and hot-swaps automatically. No restart needed.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (or lock-contention skip) |
| 2 | RecipeError — bad YAML, missing env, column mismatch |
| 3 | DataSourceError — auth failure, query error, network |
| 4 | TrainingError — split failed, all scores zero, min_data_violation |
| 5 | ArtifactError — signing key missing, magic mismatch |
| 1 | Unexpected error |

## Further reading

- [docs/getting-started.md](docs/getting-started.md) — Docker Compose / pip walkthrough end-to-end
- [docs/recipe-reference.md](docs/recipe-reference.md) — every field documented
- [docs/operations.md](docs/operations.md) — key rotation, sizing, troubleshooting
- [docs/security.md](docs/security.md) — threat model, IAM scopes, secrets handling
- [docs/README.md](docs/README.md) — full docs index
