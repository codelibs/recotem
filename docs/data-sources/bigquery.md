# BigQuery Data Source

## Install

```bash
pip install "recotem[bigquery]"
```

Without this extra, `recotem train` exits with:

```
DataSourceError: BigQuery source requires 'recotem[bigquery]'. Install with: pip install "recotem[bigquery]"
```

## Authentication

Recotem uses Application Default Credentials (ADC). No credentials are embedded in recipes.

Set up ADC with one of:

```bash
# Local development
gcloud auth application-default login

# Service account key (not recommended for production)
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

# GCE / GKE / Cloud Run / Vertex AI
# No action needed. The metadata server provides credentials automatically.
```

Required IAM role on the BigQuery dataset: `roles/bigquery.dataViewer` + `roles/bigquery.jobUser` on the project.

For the Storage Read API (used for large result sets): `roles/bigquery.readSessionUser`.

Recommended minimum set for a service account used by Recotem:

| Role | Scope |
|------|-------|
| `roles/bigquery.jobUser` | Project |
| `roles/bigquery.dataViewer` | Dataset(s) queried |
| `roles/bigquery.readSessionUser` | Project (for Storage Read API) |

## Recipe configuration

```yaml
source:
  type: bigquery
  query: |
    SELECT ...
  query_parameters:        # optional
    key: value
  project: my-gcp-project  # optional; falls back to ADC ambient project
```

## Parameter binding

Use BigQuery named parameters (`@name`) for any value that varies between runs. Do **not** use Python string formatting or `${...}` expansion in `query` — neither is supported and the latter is explicitly blocked.

```yaml
source:
  type: bigquery
  query: |
    SELECT user_id, item_id, ts
    FROM `proj.dataset.events`
    WHERE event_date BETWEEN @start_date AND @end_date
      AND event_name = @event_name
  query_parameters:
    start_date: "2026-04-01"
    end_date: "2026-05-07"
    event_name: "purchase"
```

Parameter types are inferred from the Python type of the value (`str`, `int`, `float`, `bool`).

## GA4 events_* pattern

GA4 exports to BigQuery using date-sharded tables named `events_YYYYMMDD`. Use `_TABLE_SUFFIX` to filter by date range without a full table scan.

```yaml
source:
  type: bigquery
  query: |
    SELECT
      user_pseudo_id                                                   AS user_id,
      (SELECT value.int_value
         FROM UNNEST(event_params)
        WHERE key = 'article_id')                                      AS item_id,
      TIMESTAMP_MICROS(event_timestamp)                                AS ts
    FROM
      `my-project.analytics_123456789.events_*`
    WHERE
      _TABLE_SUFFIX BETWEEN
        FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
        AND FORMAT_DATE('%Y%m%d', CURRENT_DATE())
      AND event_name = 'select_content'
      AND (SELECT value.int_value
             FROM UNNEST(event_params)
            WHERE key = 'article_id') IS NOT NULL
  project: my-project
```

This query:
- Covers the rolling 30-day window with no parameter binding needed (dates are computed in SQL).
- Filters to `select_content` events with a non-null `article_id`.
- Produces three columns: `user_id`, `item_id`, `ts`.

Map the output columns in `schema`:

```yaml
schema:
  user_column: user_id
  item_column: item_id
  time_column: ts
```

## Errors and exit codes

| Error | Exit | Message pattern |
|-------|------|----------------|
| ADC credentials not found | 3 | `DataSourceError: Could not obtain credentials. Run 'gcloud auth application-default login' or set GOOGLE_APPLICATION_CREDENTIALS.` |
| Permission denied on dataset | 3 | `DataSourceError: Access Denied: Dataset my-project:analytics_123456789` |
| Query syntax error | 3 | `DataSourceError: Syntax error: ...` |
| Column missing after query | 2 | `RecipeError: column 'item_id' not found in query result` |
| Extra not installed | 3 | `DataSourceError: BigQuery source requires 'recotem[bigquery]'` |

All BigQuery exceptions are wrapped in `DataSourceError` and produce exit 3. The full BigQuery error message is included in the stderr JSON line.

## Notes

- `recotem validate recipes/my_recipe.yaml` probes ADC authentication and runs a `LIMIT 1` dry-run against the query before any training starts.
- Query results are streamed via the Storage Read API when available. Very large result sets (> 10 M rows) should be pre-aggregated in your data warehouse before handing off to Recotem.
- `GOOGLE_*` and `GCP_*` env vars are blacklisted from recipe `${...}` expansion. Cloud credentials must come from ADC, not from the recipe file.
