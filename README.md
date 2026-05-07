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

**1. Generate a signing key**

```bash
recotem keygen
# kid:        my-key
# plaintext:  <43-char base64url>
# hash:       sha256:<64-hex>
export RECOTEM_SIGNING_KEYS="my-key:<hex-from-above>"
```

**2. Write a recipe**

```yaml
# recipe.yaml
name: top_picks

source:
  type: csv
  path: ./interactions.csv   # columns: user_id, item_id

schema:
  user_column: user_id
  item_column: item_id

training:
  algorithms: [IALS, TopPop]
  metric: ndcg
  cutoff: 10
  n_trials: 20

output:
  path: ./artifacts/top_picks.recotem
```

**3. Train**

```bash
recotem train recipe.yaml
# exit 0 → artifacts/top_picks.recotem written and signed
```

**4. Serve**

```bash
export RECOTEM_API_KEYS="my-key:sha256:<hash>"
recotem serve --recipes ./recipes/
```

**5. Predict**

```bash
curl -X POST http://localhost:8000/predict/top_picks \
  -H "X-API-Key: <plaintext>" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "u123", "cutoff": 5}'
```

```json
{
  "items": [{"item_id": "i42", "score": 0.91}, ...],
  "model": {"recipe": "top_picks", "trained_at": "2026-05-07T01:23:45Z",
            "best_class": "IALSRecommender", "kid": "my-key"},
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

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

- [docs/quickstart.md](docs/quickstart.md) — 5-minute walkthrough
- [docs/recipe-reference.md](docs/recipe-reference.md) — every field documented
- [docs/operations.md](docs/operations.md) — key rotation, sizing, troubleshooting
- [docs/security.md](docs/security.md) — threat model, IAM scopes, secrets handling
- [docs/README.md](docs/README.md) — full docs index
