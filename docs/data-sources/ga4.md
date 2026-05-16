# GA4 data source

The `ga4` source trains a recommender directly from the
[Google Analytics 4 Data API](https://developers.google.com/analytics/devguides/reporting/data/v1),
skipping the BigQuery Export hop. Intended for properties that don't have
BQ Export enabled.

See `examples/ga4-data-api/` for a working starting point.

## Install

```bash
pip install 'recotem[ga4]'
```

Without this extra, `recotem train` exits with:

```
DataSourceError: google-analytics-data is required for GA4Source. Install with: pip install 'recotem[ga4]'
```

## Recipe shape

```yaml
source:
  type: ga4
  property_id: "123456789"          # numeric, NOT the G-XXXX measurement ID
  user_dimension: userPseudoId      # userPseudoId or userId
  item_dimension: itemId            # itemId | itemName | itemCategory
  time_dimension: date              # date | dateHour | dateHourMinute
  event_names: [purchase, view_item, add_to_cart]
  # Pick exactly one of (lookback_days) OR (start_date + end_date):
  lookback_days: 90
  # start_date: "2026-01-01"
  # end_date:   "2026-05-01"
  max_rows: 1_000_000               # required
  weight_column: event_count
  api_timeout_seconds: 60
```

| Field | Required | Default | Notes |
|---|---|---|---|
| `property_id` | yes | — | Numeric only. `^\d+$`. |
| `user_dimension` | yes | — | `userId` requires User-ID feature; `userPseudoId` is the cookie-bound default. |
| `item_dimension` | no | `itemId` | Any GA4 item-scoped dimension. |
| `time_dimension` | no | `date` | Granularity of the time bucket. |
| `event_names` | yes | — | 1–50 event names; each matches `^[A-Za-z_][A-Za-z0-9_]{0,39}$`. |
| `lookback_days` | XOR | — | 1–3650; rolling window ending at `yesterday` (the previous complete day in the property's timezone). |
| `start_date` / `end_date` | XOR | — | ISO dates. Both required if either is set; `start <= end`. |
| `max_rows` | yes | — | Hard cap on rows returned. Valid range `[1, 50_000_000]` (out-of-range raises ValidationError). |
| `weight_column` | no | `event_count` | Output DataFrame column name for the `eventCount` metric. Must match `schema.weight_column`. Validation rejects values that collide with any of the four dimension keys (`user_dimension`, `item_dimension`, `time_dimension`, or the literal `eventName`) — a collision would silently overwrite a dimension or the metric in the per-row dict. |
| `api_timeout_seconds` | no | 60 | Valid range `[5, 600]` (out-of-range raises ValidationError). |

## Authentication

ADC only. Configure one of:

- **Local dev**: `gcloud auth application-default login`
- **GKE**: Workload Identity binding the pod's service account to a Google
  service account
- **Cloud Run**: `--service-account=...` at deploy time

The service account needs `roles/analytics.viewer` on the GA4 property.

## How rows reach the DataFrame

A GA4 request asks for four dimensions and one metric:

```
dimensions = [<user_dimension>, <item_dimension>, <time_dimension>, eventName]
metric     = eventCount
```

The response is paginated (page size 100_000). Each row becomes a DataFrame
row with the recipe-schema column names. The internal `eventName` column is
dropped before `fetch()` returns, so multiple event types for the same
`(user, item, time)` show up as multiple rows.

**Set `cleansing.dedup: none`** for GA4 — `keep_first` / `keep_last` would
discard the weight from the other event types. irspack aggregates repeated
`(user, item)` weights internally.

## Quotas, pagination, retries

- Page size 100_000 (Data API hard maximum).
- Loop until `row_count` is drained, `max_rows` is hit, or
  `RECOTEM_GA4_MAX_PAGES` (default 500) is reached.
- Retries `RESOURCE_EXHAUSTED` / `UNAVAILABLE` gRPC codes via
  `google.api_core.retry.Retry` (initial 1 s, exponential backoff up to 30 s,
  total budget = 3 × `api_timeout_seconds`).
- `PERMISSION_DENIED` → immediate `DataSourceError` naming the role
  (`roles/analytics.viewer`) and the property ID.
- All other `GoogleAPICallError` subclasses (e.g. `NOT_FOUND`, `INVALID_ARGUMENT`)
  → immediate `DataSourceError` carrying the API error class name and message.
- A per-fetch wall-clock budget of `10 × api_timeout_seconds` bounds the entire
  paginated run.  The deadline is checked **before** and **after** every
  `run_report` call, so an unlucky page that consumes the retry budget cannot
  overshoot by another full retry cycle.
- **Budget interaction:** the per-page `Retry(timeout=3 × api_timeout_seconds)`
  budget can in the worst case consume the full 3× wait before raising.
  Combined with the per-attempt `timeout=api_timeout_seconds`, a single page
  in worst-case retry can therefore burn ~`3 × api_timeout_seconds`.  The
  outer 10× wall-clock budget is intentionally a circuit-breaker, not a
  generous cap: under sustained `RESOURCE_EXHAUSTED` back-pressure it will
  abort after roughly three exhausted pages rather than letting the run
  drift unboundedly.  Tighten the query or raise `api_timeout_seconds`
  (which also raises the wall-clock budget linearly) if your workload
  legitimately requires more retried pages.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | (unset) | ADC key file path. Empty = use the default chain. |
| `RECOTEM_GA4_MAX_PAGES` | 500 | Hard ceiling on pagination loops. Clamp `[1, 10_000]`. |
| `RECOTEM_METRICS_ENABLED` | (unset) | Truthy emits `recotem_ga4_pages_fetched_total`, `..._rows_fetched_total`, and `..._quota_remaining` (requires `recotem[metrics]`). |

## Troubleshooting

| Error | Likely cause | Fix |
|---|---|---|
| `google-analytics-data is required for GA4Source. Install with: pip install 'recotem[ga4]'` | Missing extra. | `pip install 'recotem[ga4]'` |
| `GA4 access denied for property ...` | Service account lacks the role. | Grant `roles/analytics.viewer` on the GA4 property. |
| `set exactly one of lookback_days OR (start_date + end_date)` | Both or neither set. | Pick one. |
| `GA4 result exceeds max_rows=...` | Genuinely huge result. | Narrow `event_names` or shorten the window. |
| `GA4 fetch reached max_pages=<n> without seeing a short page; increase RECOTEM_GA4_MAX_PAGES or tighten the query` | Property is too large for default ceiling. | Raise `RECOTEM_GA4_MAX_PAGES` after confirming quota. |
