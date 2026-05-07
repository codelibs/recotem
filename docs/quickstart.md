# Quickstart

Install, write a recipe, train a model, and call `/predict` in about 5 minutes.

## Prerequisites

- Python 3.12+
- A CSV file with at least `user_id` and `item_id` columns (1 000+ rows recommended)

## 1. Install

```bash
pip install recotem
```

Verify:

```bash
recotem --help
```

## 2. Generate a signing key

Every artifact is HMAC-signed. You need a key before training.

```bash
recotem keygen --type signing --kid dev-key
```

Output (the plaintext is a 64-char hex string — 32 raw bytes):

```
kid=dev-key
plaintext=ab19d1c735a2b2432b4ac7374c3edc0f75bfecd090002d59549a0d203b9ccce8
hash=sha256:04cba87ddb7ba43b40d114bc50ce2e419d18bcdc67235ca85ef7980b4b61c296
env_entry=RECOTEM_SIGNING_KEYS=dev-key:ab19d1c735a2b2432b4ac7374c3edc0f75bfecd090002d59549a0d203b9ccce8
```

Export the key for training:

```bash
export RECOTEM_SIGNING_KEYS="dev-key:ab19d1c735a2b2432b4ac7374c3edc0f75bfecd090002d59549a0d203b9ccce8"
```

The plaintext is shown once. Store it securely — `recotem train` and `recotem
inspect` need the same key. API keys for `/predict` are separate (Step 6).

## 3. Write a recipe

Create `recipes/top_picks.yaml`:

```yaml
name: top_picks

source:
  type: csv
  path: ./data/interactions.csv

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

Validate the recipe (checks schema and, for BigQuery, connectivity):

```bash
recotem validate recipes/top_picks.yaml
```

## 4. Train

```bash
mkdir -p artifacts
recotem train recipes/top_picks.yaml
```

Expected output on success:

```
{"event":"train_done","name":"top_picks","run_id":"...","exit_code":0,
 "artifact":"./artifacts/top_picks.recotem","best_class":"IALSRecommender",
 "best_score":0.38,"trials":20,"trained_at":"2026-05-07T01:23:45Z","kid":"dev-key"}
```

Common errors:

| Exit | Message | Fix |
|------|---------|-----|
| 2 | `RecipeError: column 'user_id' not found` | Check `schema.user_column` matches CSV header |
| 4 | `TrainingError: min_data_violation` | Need ≥ 1000 rows (default); lower `cleansing.min_rows` |
| 5 | `ArtifactError: RECOTEM_SIGNING_KEYS not set` | Export the env var (step 2) |

## 5. Inspect the artifact

```bash
recotem inspect artifacts/top_picks.recotem
```

Prints the header JSON (HMAC-verified, payload never deserialized):

```json
{
  "recipe_name": "top_picks",
  "best_class": "IALSRecommender",
  "best_score": 0.38,
  "trained_at": "2026-05-07T01:23:45Z",
  "kid": "dev-key"
}
```

## 6. Serve

API keys are independent from signing keys. Generate one with `--type api` —
this produces a 43-char base64url plaintext for clients and a scrypt-derived
hash for the server's allow-list:

```bash
recotem keygen --type api --kid dev-key
# kid=dev-key
# plaintext=ovz_MUSdz1eHLf1Em5RhaDFdulYMznqj0rvVD_H4rvs   ← clients send this
# hash=sha256:a698a0b3ee823c2b23612a560b0154459e033982883648e45b74298128a30e76
# env_entry=RECOTEM_API_KEYS=dev-key:sha256:a698a0b3ee823c2b23612a560b0154459e033982883648e45b74298128a30e76

export RECOTEM_API_KEYS="dev-key:sha256:a698a0b3ee823c2b23612a560b0154459e033982883648e45b74298128a30e76"
recotem serve --recipes ./recipes/
```

The server starts on `http://127.0.0.1:8080` by default
(override with `--port` or `RECOTEM_PORT`). Check health:

```bash
curl http://127.0.0.1:8080/health
```

```json
{"status": "ok", "recipes": {"top_picks": {"loaded": true, "best_class": "IALSRecommender", "kid": "dev-key"}}}
```

## 7. Call predict

```bash
curl -s -X POST http://127.0.0.1:8080/predict/top_picks \
  -H "X-API-Key: ovz_MUSdz1eHLf1Em5RhaDFdulYMznqj0rvVD_H4rvs" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "u123", "cutoff": 5}' | jq .
```

```json
{
  "items": [
    {"item_id": "i42", "score": 0.91},
    {"item_id": "i17", "score": 0.87}
  ],
  "model": {
    "recipe": "top_picks",
    "trained_at": "2026-05-07T01:23:45Z",
    "best_class": "IALSRecommender",
    "kid": "dev-key"
  },
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

A 404 means the `user_id` was not present in training data. A 401 means wrong or missing API key.

## Next steps

- Add item metadata (titles, categories) — see [recipe-reference.md](recipe-reference.md#item_metadata)
- Use BigQuery as a source — see [data-sources/bigquery.md](data-sources/bigquery.md)
- Run on a schedule — see [deployment/cron.md](deployment/cron.md)
- Deploy with Docker — see [deployment/docker.md](deployment/docker.md)
- Rotate signing keys — see [operations.md](operations.md#signing-key-rotation)
