# Local CSV example

Minimal recipe that trains a recommender from a local gzip-compressed CSV
shipped alongside the recipe. No network access required.

## Files

- `recipe.yaml` — uses local path `examples/csv-local/interactions.csv.gz`
- `interactions.csv.gz` — small synthetic dataset (`user_id`, `item_id`,
  `timestamp`): 221 rows, 20 distinct users, 15 distinct items

## Run

From the repository root:

```bash
# 1. Generate a signing key (once per machine) and export it.
#    `recotem train` refuses to write an unsigned artifact: without this it
#    exits 8 with `RECOTEM_SIGNING_KEYS is not set`.
export $(uv run recotem keygen --type signing | grep '^env_entry=' | sed 's/^env_entry=//')

# 2. Train.
mkdir -p artifacts
uv run recotem train examples/csv-local/recipe.yaml
```

The artifact is written to `./artifacts/csv_local_example.<sha>.recotem`
(the `.<sha>` suffix comes from `versioning: append_sha`).

## What it demonstrates

- A `csv` data source with explicit `dtype` overrides for ID columns.
- `time_user` train/test split (requires `time_column` on the schema).
- IALS, CosineKNN, and TopPop algorithms compared via Optuna ndcg@10.

`training.cutoff` is 10 because the evaluation cutoff must not exceed the
number of distinct items (15 here). Otherwise training stops before the first
trial with exit 4 (`TrainingError`, code `cutoff_exceeds_item_count`) naming
both numbers. Raise it only alongside a larger dataset.

## When to prefer this over `tutorial-purchase-log`

Use this example when you want to develop offline, or to study the recipe
schema without an HTTPS round-trip. The
[tutorial-purchase-log](../tutorial-purchase-log/README.md) example is the
runnable end-to-end walkthrough that the [getting-started
guide](../../docs/getting-started.md) is built around.
