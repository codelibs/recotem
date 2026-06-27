# Source reference: BigQuery

Use for interaction data in BigQuery — either a **GA4 export** (`events_*`
date-sharded tables, the common case) or a **custom dataset/table** you own.

## Authentication (ADC)

recotem does not read credentials from the recipe; the Google client walks the
standard Application Default Credentials chain. Confirm one is in place:

```bash
gcloud auth application-default login                 # local user creds, or
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json   # service account
```

The principal needs `roles/bigquery.jobUser` (project) +
`roles/bigquery.dataViewer` (dataset). `source.project` is the **billing**
project; when omitted the client uses the ADC ambient project.

## Inputs to gather

- Billing **project** and the **table** (`project.dataset.table`, or
  `project.dataset.events_*` for GA4).
- **Date range** to train on (keep short while iterating).
- How each `schema` column is produced (see "Shaping item_id" below):
  - `user_id` — GA4: `user_pseudo_id`. Custom: your user/customer key.
  - `item_id` — what identifies an item and **how it is encoded**.
  - `time_column` — GA4: `TIMESTAMP_MICROS(event_timestamp)`. Custom: your
    timestamp column.
- What counts as a positive interaction (GA4: which `event_name`; custom: which
  rows).

## `source:` block

```yaml
source:
  type: bigquery
  project: my-gcp-project            # or "${RECOTEM_RECIPE_GCP_PROJECT}" to keep it out of the file
  query: |
    SELECT
      user_pseudo_id AS user_id,
      <item_id expression> AS item_id,
      TIMESTAMP_MICROS(event_timestamp) AS ts
    FROM `my-project.analytics_XXXXXXXXX.events_*`
    WHERE _TABLE_SUFFIX BETWEEN
            FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL @lookback_days DAY))
            AND FORMAT_DATE('%Y%m%d', CURRENT_DATE())
      AND event_name = 'page_view'
  query_parameters:
    lookback_days: 30                # int -> INT64; quote -> STRING. No dates/lists/null.
```

For a **custom table**, drop the GA4 specifics and select your columns directly:

```yaml
  query: |
    SELECT customer_id AS user_id, product_id AS item_id, purchased_at AS ts
    FROM `my-project.shop.purchases`
    WHERE purchased_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @lookback_days DAY)
```

Use `@name` bind parameters for per-run values; `${...}` expansion is blocked
inside the query.

## Shaping `item_id` (extraction happens in SQL)

GA4 has no single "item" — choose what to recommend:

- **URL path** (works on raw GA4 with zero extra tagging; most portable):
  ```sql
  REGEXP_EXTRACT(
    (SELECT value.string_value FROM UNNEST(event_params) WHERE key='page_location'),
    r'^https?://[^/]+([^?#]*)')        -- path only
  ```
- **Stable ID embedded in the URL** — anchor on a delimiter so unrelated digits
  (e.g. a `/2026/04/` date) are not matched:
  ```sql
  REGEXP_EXTRACT(page_location, r'/articles/(\d+)')          -- numeric ID after a segment
  REGEXP_EXTRACT(page_location, r'[（(]([0-9A-Z]{4})[）)]')   -- 4-char alnum ID in parens
  ```
- **Custom event parameter** (requires GA4/GTM setup) — read it from
  `event_params`, matching the value accessor to how it was sent
  (`value.int_value` / `value.string_value`).

Combine with `cleansing.drop_null_ids: true` so rows where `REGEXP_EXTRACT`
returned NULL are dropped. See `docs/data-sources/bigquery.md` for the full GA4
section.

## Cost / volume

`validate` runs a **free dry run**. recotem does not surface the byte estimate,
so check it yourself before the real run (still free):

```python
# uv run python - <<'PY'
from google.cloud import bigquery
client = bigquery.Client(project="my-gcp-project")
q = "<the exact query the recipe will run>"
job = client.query(q, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
gb = job.total_bytes_processed / 1024**3
print(f"scan {gb:.4f} GiB ~= ${gb/1024*6.25:.5f} on-demand")
# PY
```

If larger than expected, shrink the date window or pre-aggregate. recotem does
not set `maximum_bytes_billed`; add a project-level cost guard rail if runaway
cost is a concern. GA4 `events_*` with `_TABLE_SUFFIX` bounds the scan to the
selected day-shards — referencing `events_*` twice (e.g. a multi-interaction
filter subquery) roughly doubles the scan.
