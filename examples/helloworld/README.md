# Hello world

The smallest runnable Recotem example. Trains a TopPop recommender from a
tiny synthetic CSV (60 users, 20 items, ~850 rows). No network, no extras.

## Files

- `recipe.yaml` — minimal recipe (`name`, `source`, `schema`, `training`, `output`)
- `interactions.csv` — synthetic interaction log with two columns: `user_id`, `item_id`
- `artifacts/` — created on first train; gitignored

## Run

From the repository root:

```bash
# Generate keys (once per machine)
recotem keygen --type signing --kid dev   # → copy the env_entry plaintext
recotem keygen --type api     --kid dev   # → copy env_entry hash + plaintext

export RECOTEM_SIGNING_KEYS="dev:<plaintext-hex-from-signing>"
export RECOTEM_API_KEYS="dev:sha256:<hash-hex-from-api>"

# Train
recotem train examples/helloworld/recipe.yaml
# → examples/helloworld/artifacts/top_picks.<sha>.recotem (signed)

# Serve
recotem serve --recipes examples/helloworld/

# Predict (in another terminal)
curl -X POST http://localhost:8080/predict/top_picks \
  -H "X-API-Key: <api-plaintext-from-keygen>" \
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
