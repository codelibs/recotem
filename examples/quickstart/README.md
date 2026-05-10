# Quickstart example

The smallest runnable Recotem example. Trains a TopPop recommender from a
tiny synthetic CSV (60 users, 20 items, ~850 rows). No network, no extras.

## Files

- `recipe.yaml` — minimal recipe (`name`, `source`, `schema`, `training`, `output`)
- `interactions.csv` — synthetic interaction log with two columns: `user_id`, `item_id`
- `artifacts/` — created on first train; gitignored

## Run

From the repository root:

```bash
# 1. Generate keys (once per machine). Copy the values into the exports below.
recotem keygen --type signing --kid dev
recotem keygen --type api     --kid dev

export RECOTEM_SIGNING_KEYS="dev:<signing-plaintext>"   # signing: env_entry value
export RECOTEM_API_KEYS="dev:sha256:<api-hash>"         # api:     env_entry value
export RECOTEM_API_PLAINTEXT="<api-plaintext>"          # api:     plaintext, for curl

# 2. Train
recotem train examples/quickstart/recipe.yaml
# → examples/quickstart/artifacts/top_picks.<sha>.recotem (signed)

# 3. Serve (foreground)
recotem serve --recipes examples/quickstart/

# 4. Predict (in another terminal)
curl -X POST http://localhost:8080/predict/top_picks \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "u01", "cutoff": 5}'
```

## What's next

- Widen the algorithm search:
  `training.algorithms: [IALS, CosineKNN, TopPop]`
- Add a `time_column` and switch to `split.scheme: time_user`
- See [`docs/recipe-reference.md`](../../docs/recipe-reference.md) for every field.
- See [`examples/csv-local`](../csv-local/README.md) for a richer local-CSV setup
  and [`examples/tutorial-purchase-log`](../tutorial-purchase-log/README.md) for
  the end-to-end Docker walkthrough.
