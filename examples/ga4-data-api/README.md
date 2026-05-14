# GA4 Data API example

Train a recommender directly from a GA4 property via the Data API. No
BigQuery Export required.

## Files

- `recipe.yaml` — GA4 recipe pointing at a numeric property ID
- `README.md` — this file

## Prerequisites

1. **GA4 property** with the events you care about (e.g. `purchase`,
   `view_item`, `add_to_cart`).
2. **Numeric property ID** (NOT the measurement ID `G-XXXX`). Find it under
   GA4 admin → Property settings.
3. **ADC** for a service account that has `roles/analytics.viewer` on the
   property:
   - Local dev: `gcloud auth application-default login`
   - GKE: Workload Identity bound to the service account
   - Cloud Run: deploy with `--service-account=...`

## Run

From the repository root:

```bash
# 1. Install the GA4 extra (if not already installed).
uv add 'recotem[ga4]'

# 2. Generate a signing key and export it.
#    keygen prints four lines; grep out just the env_entry line.
#    The env_entry value already starts with `RECOTEM_SIGNING_KEYS=`,
#    so we only strip the leading `env_entry=` prefix.
export $(uv run recotem keygen --type signing | grep '^env_entry=' | sed 's/^env_entry=//')

# 3. Edit the recipe to point at your GA4 property.
#    Replace REPLACE_WITH_NUMERIC_PROPERTY_ID with your numeric GA4 property ID.
# nano examples/ga4-data-api/recipe.yaml

# 4. Train.
mkdir -p artifacts
uv run recotem train examples/ga4-data-api/recipe.yaml
```

The artifact is written to `./artifacts/ga4_demo.recotem`.

## What it demonstrates

- A `ga4` data source with event aggregation.
- Service account ADC (Application Default Credentials) for authentication;
  no API keys in the recipe.
- TopPop, RP3beta, and IALS compared via Optuna NDCG@10.
- `dedup: none` — GA4 returns one row per `(user, item, time, event)`, so
  we keep all records to preserve the weight from multiple event types.
- `always_overwrite` versioning — suitable for local iteration.

## Notes

- `dedup: none` is recommended: GA4 returns one row per
  `(user, item, time, eventName)` and `keep_first` / `keep_last` would
  discard the weight from the other event types.
- The `weight_column` in `source` (default `event_count`) determines how
  interaction weights are aggregated. The schema does not need to specify it
  separately.
- `RECOTEM_GA4_MAX_PAGES` (default 500) hard-bounds the response — raise it
  for very high-volume properties only after confirming GA4 Data API quota.
