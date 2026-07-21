# Feature-aware iALS example

Trains an `IALSRecommender` with an item-side `features:` block, then serves
a cold-start recommendation for an item the model never saw during
training. Small enough to run in a few seconds; no network access required.

Field reference: [docs/recipe-reference.md#features](../../docs/recipe-reference.md#features).

## Files

- `recipe.yaml` — interactions from `interactions.csv`, item features from
  `items.csv`.
- `interactions.csv` — 144 rows, 30 users, 14 items (`user_id`, `item_id`,
  `timestamp`).
- `items.csv` — 15 items, one column per encoding:
  - `category` (`categorical`) — `action` / `comedy` / `drama`.
  - `release_year` (`numerical`) — standardized at train time.
  - `tags` (`multi_label`, `|`-delimited) — e.g. `space|thriller`; some rows
    are blank on purpose, to exercise the "missing row" behavior.
- Item **`i15`** appears in `items.csv` but is deliberately **absent** from
  `interactions.csv` — it is the cold item used in the cold-start step below.

## Run

From the repository root:

```bash
# 1. Generate keys (once per machine). Copy the values into the exports below.
recotem keygen --type signing --kid dev
recotem keygen --type api     --kid dev

export RECOTEM_SIGNING_KEYS="dev:<signing-hex64>"       # signing: env_entry value
export RECOTEM_API_KEYS="dev:sha256:<api-hash>"         # api:     env_entry value
export RECOTEM_API_PLAINTEXT="<api-plaintext>"          # api:     plaintext, for curl

# 2. Validate — probes both the interaction source AND features.item.source
recotem validate examples/feature-aware/recipe.yaml
```

```
Recipe 'feature_aware_demo': schema OK
DataSource: probe OK (csv) [source]
DataSource: probe OK (csv) [features.item.source]
Validation passed.
```

```bash
# 3. Train
mkdir -p artifacts
recotem train examples/feature-aware/recipe.yaml
# → ./artifacts/feature_aware_demo.<sha>.recotem (signed)
```

```bash
# 4. Inspect the header — confirm the "features" block is present
recotem inspect ./artifacts/feature_aware_demo.recotem
```

Example output (the structural fields below — `best_class`, `n_items: 14`,
`features.version: 1`, `n_features: 13`, `columns` — are stable across
runs; `best_params`' numeric values are **not**: `training.split.scheme:
random` picks a fresh item/user vocabulary order per Python process for
string ids, so Optuna explores the search space in a different order each
run and lands on different-but-comparable hyperparameters and score. This
is pre-existing recotem behavior, unrelated to `features:`):

```json
{
  "best_class": "IALSRecommender",
  "best_params": {
    "train_epochs": 115,
    "n_components": 94,
    "alpha0": 0.0632438212511315,
    "reg": 0.001976218934028009,
    "lambda_item_feature": 6.687175060313052
  },
  "data_stats": {"n_rows": 144, "n_users": 30, "n_items": 14, ...},
  "features": {
    "version": 1,
    "item": {
      "n_features": 13,
      "columns": ["category", "release_year", "tags"]
    }
  }
}
```

`best_params` carries `lambda_item_feature` alongside iALS's usual
hyperparameters — this is recotem's own tuned range, not irspack's (see
[recipe-reference.md](../../docs/recipe-reference.md#features)). `n_items:
14` (not 15): item `i15` never appears in `interactions.csv`, so it is not
part of the trained id-map — that is what makes it a genuine cold item for
step 6 below. `n_features: 13` is item `i15`'s only footprint in this
header: it and every other item contributed to the vocabulary that produced
that number, even though it is otherwise invisible to training.

```bash
# 5. Serve (foreground)
recotem serve --recipes examples/feature-aware/ --port 8080
```

```bash
# 6. Recommend for a known user (ordinary path, unchanged by features)
curl -X POST http://localhost:8080/v1/recipes/feature_aware_demo:recommend \
     -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
     -H "Content-Type: application/json" \
     -d '{"user_id": "u01", "limit": 5}'
```

```bash
# 7. Cold-start :recommend-related for item i15 — never trained on, scored
#    purely from its item_features (case C: compute its embedding from
#    features, then rank by similarity to that embedding)
curl -X POST http://localhost:8080/v1/recipes/feature_aware_demo:recommend-related \
     -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
     -H "Content-Type: application/json" \
     -d '{
           "seed_items": ["i15"],
           "limit": 5,
           "item_features": {
             "i15": {"category": "drama", "release_year": 2016, "tags": "crime|period"}
           }
         }'
```

Example output (exact item ids and scores vary run to run — see the
determinism note above — but the shape and the HTTP 200 do not):

```json
{
  "recipe": "feature_aware_demo",
  "items": [
    {"item_id": "i11", "score": 0.000384},
    {"item_id": "i06", "score": 0.000319},
    {"item_id": "i07", "score": 0.000219},
    {"item_id": "i01", "score": 0.000192},
    {"item_id": "i09", "score": 0.000133}
  ]
}
```

200, with real recommendations, for an item the model was never trained on.
Compare with the same call **without** `item_features`:

```bash
curl -X POST http://localhost:8080/v1/recipes/feature_aware_demo:recommend-related \
     -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
     -H "Content-Type: application/json" \
     -d '{"seed_items": ["i15"], "limit": 5}'
# → 404 {"detail":"no known seed_items","code":"UNKNOWN_SEED_ITEMS"}
```

Without `item_features`, `i15` is simply an unknown seed — the pre-existing
behavior. Supplying its feature values is what makes the cold-start path
reachable.

## What it demonstrates

- A `features.item` block exercising all three encodings
  (`categorical` / `numerical` / `multi_label`) plus the missing-row case
  (`tags` blank for `i05` / `i10` / `i15`).
- `recotem validate` and `recotem inspect` surfacing feature-source probing
  and the `features` header block respectively.
- Case C cold-start (`:recommend-related` + `item_features` for an unseen
  seed item) — see
  [api-reference.md#feature-aware-cold-start](../../docs/api-reference.md#feature-aware-cold-start)
  for the full case A/B/C table, including the user-feature cases this
  example does not exercise (no `features.user` block here).

## What it does not cover

This example only declares `features.item`. `features.user` follows the
identical shape (`source` + `id_column` + `columns`) and is independently
optional — adding it would additionally enable case A
(`:recommend` + `user_features` for an unknown user) and case B
(`:recommend-related` + `user_features` as a profile prior on top of an
ad-hoc seed history).

## When to reach for this over the other examples

Use this example to learn the `features:` block specifically. For a plain
(non-feature) local-CSV walkthrough see
[`examples/csv-local`](../csv-local/README.md); for the smallest possible
recipe see [`examples/quickstart`](../quickstart/README.md).
