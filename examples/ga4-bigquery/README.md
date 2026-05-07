# GA4 → BigQuery example

Trains a news-article recommender from Google Analytics 4 events exported to
BigQuery. Demonstrates the `bigquery` source type, `@param` query parameters,
and writing artifacts to GCS.

## Files

- `recipe.yaml` — selects `select_content` events from the GA4 export tables,
  joins with a per-user activity threshold, and trains IALS / CosineKNN /
  TopPop on the result. Output goes to `gs://my-ml-bucket/...`.

## Prerequisites

```bash
pip install "recotem[bigquery]"
gcloud auth application-default login
# Or set GOOGLE_APPLICATION_CREDENTIALS to a service-account key path.
```

The service account / ADC identity needs:

- `bigquery.jobs.create` on the BigQuery project running the query
- `bigquery.tables.getData` on the `analytics_<id>.events_*` tables
- `storage.objects.create` on the destination GCS bucket

## Run

```bash
RECOTEM_RECIPE_GCP_PROJECT=my-gcp-project \
  uv run recotem train examples/ga4-bigquery/recipe.yaml
```

The `RECOTEM_RECIPE_*` prefix is the only env-var family allowed inside
recipe field expansion (see [docs/recipe-reference.md](../../docs/recipe-reference.md)).
Credentials are intentionally NOT expanded — use ADC / Workload Identity
instead.

## What it demonstrates

- `bigquery` source with a multi-line `query` and typed `query_parameters`
  (`@lookback_days`, `@min_events`) — env-var expansion is disabled inside
  the query block to foreclose SQL injection.
- `${RECOTEM_RECIPE_GCP_PROJECT}` env expansion on the `project` field.
- `time_user` split with a non-trivial cleansing block
  (`min_rows: 1000`, `min_users: 100`, `min_items: 50`).
- `per_algorithm_trials` to tune the Optuna budget per recommender.
- Writing the signed artifact directly to GCS (no local disk needed on the
  training host).

## Adapting to your project

1. Replace `analytics_123` with your GA4 export dataset name.
2. Tune `lookback_days` / `min_events` for your traffic volume.
3. Replace the GCS bucket in `output.path` with one your training principal
   can write to.
4. Keep `RECOTEM_SIGNING_KEYS` in your scheduler's secret store — the
   training host must have it set in env to produce a signed artifact.
