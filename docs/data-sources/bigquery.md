# BigQuery Data Source

## Install

```bash
pip install "recotem[bigquery]"
```

Without this extra, `recotem train` exits with exit code 3 (`DataSourceError`).
The `bigquery` extra pulls in two packages that are checked independently, so
the message names whichever is missing first:

```
DataSourceError: google-cloud-bigquery is required for BigQuerySource. Install it with: pip install recotem[bigquery]
```

```
DataSourceError: db-dtypes is required for BigQuerySource. Install it with: pip install recotem[bigquery]
```

Installing `recotem[bigquery]` resolves both.

## Authentication

Recotem uses Application Default Credentials (ADC). No credentials are embedded in recipes. The `google-cloud-bigquery` client itself walks the standard ADC chain (`GOOGLE_APPLICATION_CREDENTIALS` → `gcloud` user creds → metadata server) — Recotem does not consult any of these env vars directly.

Set up ADC with one of:

```bash
# Local development
gcloud auth application-default login

# Service account key (not recommended for production)
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

# GCE / GKE / Cloud Run / Vertex AI
# No action needed. The metadata server provides credentials automatically.
```

`source.project` (recipe field) is forwarded as the BigQuery client's billing project. When omitted, the client uses the ADC ambient project (`gcloud config get project` for user creds, or the service account's project). There is no recipe field for `location` — BigQuery infers location from the dataset referenced in the query.

Required IAM role on the BigQuery dataset: `roles/bigquery.dataViewer` + `roles/bigquery.jobUser` on the project.

For the Storage Read API (used for large result sets): `roles/bigquery.readSessionUser`. This role is **optional** — the download path tries `create_bqstorage_client=True` first. Storage Read API failures map to fallback **only for IAM-shape failures** (PermissionDenied / Forbidden / 403); quota errors, 5xx backend failures, and other non-permission errors raise `DataSourceError` so REST fallback does not double-bill. Set `RECOTEM_BQ_REQUIRE_STORAGE_API=1` to require the fast path (needs both the `google-cloud-bigquery-storage` package and the `bigquery.readSessions.create` permission). See [Storage Read API fallback policy](#storage-read-api-fallback-policy).

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

Parameter types are inferred from the Python type of the value:

| YAML / Python type | BigQuery type |
|--------------------|---------------|
| `bool` (`true` / `false`) | `BOOL` |
| `int` | `INT64` |
| `float` | `FLOAT64` |
| `str` | `STRING` |

`bool` is checked before `int` (so YAML `true` does not become `INT64 1`). Lists, dicts, `null`, dates, and timestamps are **not** supported and raise `DataSourceError` whenever the parameter dispatcher runs — that means both at `recotem validate` (via `probe()`) and at fetch time. Encode dates as `STRING` (e.g. `"2026-04-01"`) and parse them in SQL with `PARSE_DATE`, or compute date ranges in SQL via `CURRENT_DATE()` / `DATE_SUB()` (see the GA4 example below).

YAML quoting matters: `lookback_days: 30` is `INT64`, `lookback_days: "30"` is `STRING`. Mismatching the SQL parameter type fails the dry-run. The message depends on where the mismatch bites; the common shape — a quoted number compared against an integer column — is rejected by the type checker, e.g. `No matching signature for operator >= for argument types: INT64, STRING`.

## GA4 events_* pattern

GA4 exports to BigQuery using date-sharded tables named `events_YYYYMMDD`. Use `_TABLE_SUFFIX` to filter by date range without a full table scan.

### Where `item_id` comes from

Recotem reads an already-fetched DataFrame and expects the columns named in
`schema` to exist verbatim — there is **no recipe-level field for regex,
expressions, or derived columns**. Any extraction or reshaping must therefore
happen **inside the SQL query** using BigQuery functions such as
`REGEXP_EXTRACT`. The query below produces three columns (`user_id`, `item_id`,
`ts`) that map directly onto `schema`.

### Recommended: derive `item_id` from `page_location`

`page_location` (the page URL) is recorded on every `page_view` event in any GA4
export with **no extra tagging or GTM configuration**, which makes it the most
portable signal for building a "users who viewed this also viewed…" recommender
straight from raw access logs. The simplest, fully general choice is to use the
URL **path** as the item:

```yaml
source:
  type: bigquery
  project: my-project
  query: |
    SELECT
      user_pseudo_id                                                    AS user_id,
      REGEXP_EXTRACT(
        (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location'),
        r'^https?://[^/]+([^?#]*)'                       -- path only; drop host, query, fragment
      )                                                                 AS item_id,
      TIMESTAMP_MICROS(event_timestamp)                                 AS ts
    FROM
      `my-project.analytics_123456789.events_*`
    WHERE
      _TABLE_SUFFIX BETWEEN
        FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
        AND FORMAT_DATE('%Y%m%d', CURRENT_DATE())
      AND event_name = 'page_view'
```

```yaml
schema:
  user_column: user_id
  item_column: item_id
  time_column: ts
cleansing:
  drop_null_ids: true   # REGEXP_EXTRACT returns NULL on no match — drop those rows
```

This covers a rolling 30-day window with no parameter binding (dates are
computed in SQL) and treats each distinct path as one item.

If your URLs embed a **stable identifier** (product / article / content ID) you
can extract just that ID for a tighter, slug-independent item space. Match on a
delimiter so unrelated digits in the URL (e.g. the `2026` in a `/2026/04/12/`
date path) are not picked up:

```sql
-- .../articles/12345-some-title       -> "12345"  (numeric ID after a path segment)
REGEXP_EXTRACT(page_location, r'/articles/(\d+)')

-- .../some-title-(A12B)/              -> "A12B"   (4-char alphanumeric ID in parentheses;
--                                                  also matches full-width （ ）)
REGEXP_EXTRACT(page_location, r'[（(]([0-9A-Z]{4})[）)]')
```

Adapt the pattern to your own URL scheme. RE2 (BigQuery's engine) supports
`\d`, character classes, and UTF-8 literals such as full-width parentheses.

### Alternative: a custom event parameter

If you already emit a dedicated identifier as a custom event parameter (this
requires GA4 / GTM configuration on the site), read it from `event_params`
instead. Replace the type accessor (`value.int_value` /
`value.string_value`) to match how the parameter was sent:

```yaml
source:
  type: bigquery
  project: my-project
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
```

## Serving and recommending

Once `recotem train` has written a signed artifact, point `recotem serve` at a
directory of recipes and call the recipe's `:recommend` endpoint. The verb in
the path is the recipe `name`:

```bash
curl -X POST http://localhost:8080/v1/recipes/{name}:recommend \
     -H "X-API-Key: <plaintext-api-key>" \
     -H "Content-Type: application/json" \
     -d '{"user_id": "<a user value seen during training>", "limit": 10}'
```

```json
{
  "request_id": "…",
  "recipe": "{name}",
  "model_version": "sha256:…",
  "items": [
    {"item_id": "/some/page-path", "score": 0.0469},
    {"item_id": "/another/page",   "score": 0.0371}
  ]
}
```

- `user_id` is whatever you mapped in `schema.user_column` (for GA4 this is
  commonly `user_pseudo_id`). A user not seen during training returns
  `404 UNKNOWN_USER`.
- To get item-to-item recommendations without a user, call `:recommend-related`
  with a `seed_items` list of known `item_id` values.
- Without `RECOTEM_API_KEYS` configured the server binds to loopback and accepts
  unauthenticated requests; see [getting-started](../getting-started.md) and
  [operations](../operations.md) for serving setup and API-key configuration.

## Errors and exit codes

| Error | Exit | Message pattern |
|-------|------|----------------|
| ADC credentials not found | 3 | `DataSourceError: Failed to create BigQuery client: ... Ensure Application Default Credentials (ADC) are configured.` |
| Permission denied on dataset | 3 | `DataSourceError: BigQuery query execution failed: 403 Access Denied: Table my-project:analytics_123456789.events ...` |
| Query syntax error | 3 | `DataSourceError: BigQuery query execution failed: 400 Syntax error: ...` |
| Table not found | 3 | `DataSourceError: BigQuery query execution failed: 404 Table my-project:dataset.tbl was not found in location US` |
| Query returned no rows | 3 | `DataSourceError: source 'bigquery' returned no rows for recipe '<name>'; the query or file matched no data. ...` |
| Column missing after query | 3 | `DataSourceError: schema column(s) ['ts'] not found in the fetched data for recipe '<name>'; available columns: [...]` |
| Storage Read API download failure (non-IAM) | 3 | `DataSourceError: BigQuery Storage Read API failed with ResourceExhausted: ... The query itself completed — this is a result-download failure. REST fallback skipped ...` |
| Extra not installed | 3 | `DataSourceError: google-cloud-bigquery is required for BigQuerySource` (or `db-dtypes ...`) |

All BigQuery failures are wrapped in `DataSourceError` and produce exit 3 — including a missing `schema:` column, which is a data-source problem (the query did not produce what the recipe names), not a recipe-schema problem. The full BigQuery error message is included in the stderr JSON line.

**Query execution and result download are reported separately.** `client.query()` returns as soon as the job is submitted, so the query has not run yet at that point. Recotem waits for the job explicitly, which keeps the two failure domains apart:

- Anything wrong with the query — bad SQL, a missing table, a permission denial on the *table*, a query quota — is reported as `BigQuery query execution failed: ...`.
- Only a failure while downloading the finished result rows gets the Storage Read API framing and the `bigquery.readSessions.create` advice.

## Storage Read API fallback policy

Recotem tries the BigQuery Storage Read API (`create_bqstorage_client=True`) first for efficiency with large result sets. This applies to the **result download only** — a query that fails to execute never reaches this stage.

Two things can make the fast path unavailable:

1. **`google-cloud-bigquery-storage` is not installed.** Recotem checks for the dependency itself rather than waiting for an error, because `google-cloud-bigquery` does not raise one: it emits a `UserWarning` and downloads over REST. Without strict mode this is a silent, logged fallback (`bigquery_storage_fallback`, reason: not installed).
2. **The download itself fails.** The fallback to REST is then **selective**, not unconditional:
   - **IAM-shape failures** (PermissionDenied / Forbidden / HTTP 403): the Storage Read API is skipped and the REST path is used instead. This covers the common case where `roles/bigquery.readSessionUser` is not granted.
   - **All other failures** (quota exceeded, 5xx backend errors, network timeouts, etc.): `DataSourceError` is raised immediately without attempting the REST fallback. This prevents a quota-exceeded Storage Read API call from silently double-billing by retrying over REST.

To require the Storage Read API and disable both silent paths, set:

```bash
export RECOTEM_BQ_REQUIRE_STORAGE_API=1
```

When this variable is truthy (`1`, `true`, `yes`, `on`):

- A missing `google-cloud-bigquery-storage` raises `DataSourceError` naming the extra to install. This is checked **before the query is submitted**, so a misconfigured strict-mode run costs nothing.
- A Storage Read API download failure raises `DataSourceError` instead of falling back to REST.

Strict mode governs the **download transport only**. It never changes how a query-execution failure is reported: a SQL typo under `RECOTEM_BQ_REQUIRE_STORAGE_API=1` is still `BigQuery query execution failed: 400 Syntax error: ...`, not IAM advice.

Use this setting when the service account is expected to hold `bigquery.readSessions.create` and you want hard enforcement.

## Notes

- `recotem validate recipes/my_recipe.yaml` probes ADC authentication and submits the query as a BigQuery dry-run job (`use_query_cache=False`) before any training starts. Dry-run jobs are not billed and do not execute the query. The dry-run also validates `query_parameters` types — invalid types surface here rather than at fetch.
- The dry-run does **not** expose its `total_bytes_processed` estimate to the user. Recotem also does not set `maximum_bytes_billed`, so a runaway query is bounded only by your project's BigQuery quotas. Add `--maximum-bytes-billed`-style guard rails at the GCP project level if cost runaway is a concern.
- **Nothing on the Recotem side bounds a BigQuery result, in bytes billed or in rows returned.** `RECOTEM_MAX_SQL_ROWS` applies to the `sql` source only and has no effect here; `RECOTEM_MAX_DOWNLOAD_BYTES` applies to path-shaped sources (`csv` / `parquet` / item metadata) only. The entire result is materialised as a DataFrame in the `recotem train` process, so a mistyped query can both bill a large scan and exhaust memory. Bound it at the GCP project level (custom quotas, `maximum_bytes_billed` on the reservation) and by writing the query with explicit `WHERE` predicates and, where appropriate, `LIMIT`.
- Query results are streamed via the Storage Read API when available. Very large result sets (> 10 M rows) should be pre-aggregated in your data warehouse before handing off to Recotem.
- `GOOGLE_*` and `GCP_*` env vars are blacklisted from recipe `${...}` expansion (case-insensitive). Cloud credentials must come from ADC, not from the recipe file. `source.query` and `source.query_parameters` are unconditionally exempt from `${...}` expansion regardless of variable name.
