# Recipe Reference

A recipe is a YAML file that defines what data to fetch, how to train, and where to write the artifact. One recipe produces one model and one `/predict/{name}` endpoint.

## Top-level fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Endpoint name. Pattern: `^[A-Za-z0-9_-]{1,64}$`. Becomes `/predict/{name}`. |
| `source` | object | yes | Data source config. `type` field is the discriminator. |
| `schema` | object | yes | Column mapping. |
| `cleansing` | object | no | Data quality gates. |
| `item_metadata` | object | no | Metadata joined into predict responses. |
| `training` | object | yes | Algorithm and tuning settings. |
| `output` | object | yes | Artifact path and versioning. |

`name` is validated at YAML load and again immediately before any filesystem or URL use.

---

## `source`

### `source.type: csv` (also `parquet`)

```yaml
source:
  type: csv
  path: gs://bucket/interactions.csv.gz
  delimiter: ","         # default ","
  encoding: utf-8        # default utf-8
  header: 0              # row index of the header row, default 0
  dtype:                 # optional explicit column dtypes
    user_id: str
    item_id: str
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `path` | string | required | Local path, `s3://`, `gs://`, or `az://`. See [Path rules](#path-rules). |
| `delimiter` | string | `","` | Single character. CSV only. |
| `encoding` | string | `"utf-8"` | Any encoding accepted by pandas. |
| `header` | int | `0` | Row number of the header. |
| `dtype` | map | `{}` | Key = column name, value = pandas dtype string. |

For Parquet files use `type: parquet`. The `delimiter`, `encoding`, and `header` fields are ignored.

### `source.type: bigquery`

```yaml
source:
  type: bigquery
  query: |
    SELECT user_pseudo_id AS user_id, item_id, TIMESTAMP_MICROS(event_timestamp) AS ts
    FROM `proj.analytics_123.events_*`
    WHERE _TABLE_SUFFIX BETWEEN @start_date AND @end_date
  query_parameters:
    start_date: "20260401"
    end_date: "20260507"
  project: my-gcp-project   # optional; falls back to ADC project
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `query` | string | required | SQL. Trusted code — not env-expanded. Use `@param` for dynamic values. |
| `query_parameters` | map | `{}` | BigQuery named parameters bound to `@name` placeholders. |
| `project` | string | `""` | GCP project ID. Falls back to ADC ambient project. |

Install the extra: `pip install "recotem[bigquery]"`.

Environment variable expansion is **never** performed inside `query` or `query_parameters`. Use `@param` placeholders to keep SQL injection foreclosed.

---

## `schema`

```yaml
schema:
  user_column: user_id    # required
  item_column: item_id    # required
  time_column: ts         # required when split.scheme is time_user or time_global
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `user_column` | string | yes | Column name in the fetched DataFrame. |
| `item_column` | string | yes | Column name in the fetched DataFrame. |
| `time_column` | string | conditional | Required for `time_user` and `time_global` split schemes. |

---

## `cleansing`

```yaml
cleansing:
  drop_null_ids: true        # default true
  dedup: keep_last           # keep_first | keep_last | none
  min_rows: 1000             # exit 4 with min_data_violation if below
  min_users: 10
  min_items: 10
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `drop_null_ids` | bool | `true` | Drop rows where `user_id` or `item_id` is null. |
| `dedup` | string | `keep_last` | How to handle duplicate (user, item) pairs. |
| `min_rows` | int | `1000` | Minimum row count after cleansing. |
| `min_users` | int | `10` | Minimum distinct user count. |
| `min_items` | int | `10` | Minimum distinct item count. |

Violation of any `min_*` threshold exits with code 4 and `"code": "min_data_violation"` in the JSON error line.

`dedup` values:

| Value | Behaviour |
|-------|-----------|
| `keep_first` | Keep the first occurrence of each (user, item) pair. |
| `keep_last` | Keep the last occurrence (by row order or time if `time_column` is set). |
| `none` | No deduplication. |

---

## `item_metadata`

```yaml
item_metadata:
  type: parquet            # csv | parquet
  path: gs://bucket/items.parquet
  fields: [title, category, image_url]   # non-empty allow-list
  on_field_missing: error  # error | null (default error)
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `type` | string | required | `csv` or `parquet`. |
| `path` | string | required | See [Path rules](#path-rules). |
| `fields` | list[string] | required | Non-empty. Only listed fields are returned in predict responses. |
| `on_field_missing` | string | `error` | What to do if a `fields` entry is absent in the file. `error` fails startup; `null` fills with `null`. |

Server-side field suppression is also available via `RECOTEM_METADATA_FIELD_DENY` (comma-separated column names), applied as a post-join column drop.

---

## `training`

```yaml
training:
  algorithms: [IALS, CosineKNN, TopPop]    # at least one required
  metric: ndcg                              # ndcg | map | recall | hit
  cutoff: 20
  n_trials: 40
  per_algorithm_trials:                     # optional per-algorithm budget
    IALS: 24
    CosineKNN: 12
    TopPop: 4
  per_trial_timeout_seconds: 600
  timeout_seconds: 1800
  parallelism: 1
  storage_path: ""                          # "" = in-memory Optuna; path = SQLite resume
  split:
    scheme: time_user                       # random | time_global | time_user
    heldout_ratio: 0.1
    test_user_ratio: 1.0
    seed: 42
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `algorithms` | list[string] | required | `IALS`, `CosineKNN`, `TopPop`, `RP3beta`, `DenseSLIM`, `TruncatedSVD`, `BPRFM`. |
| `metric` | string | required | Evaluation metric. |
| `cutoff` | int | required | Recommendation list length for evaluation. |
| `n_trials` | int | required | Total Optuna trial budget (must be ≥ 1). |
| `per_algorithm_trials` | map | `{}` | Per-algorithm trial overrides. Sum need not equal `n_trials`. |
| `per_trial_timeout_seconds` | int | `null` | Soft per-trial time cap (Optuna callback). |
| `timeout_seconds` | int | `null` | Overall tuning wall-clock cap. |
| `parallelism` | int | `1` | In-process worker threads sharing the Optuna study. |
| `storage_path` | string | `""` | Empty = in-memory (no resume). SQLite path enables resume. **Must be local FS** — SQLite over NFS corrupts. Postgres/Redis URLs are also accepted. |
| `split.scheme` | string | required | `random`, `time_global`, or `time_user`. |
| `split.heldout_ratio` | float | required | Fraction of interactions held out. Must be in (0, 1). |
| `split.test_user_ratio` | float | `1.0` | Fraction of users included in the test split. |
| `split.seed` | int | `42` | Random seed. |

`time_user` and `time_global` require `schema.time_column`. Missing `time_column` with these schemes exits with code 2.

---

## `output`

```yaml
output:
  path: ./artifacts/news_articles.recotem
  versioning: append_sha     # always_overwrite | append_sha (default append_sha)
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `path` | string | required | Artifact destination. See [Path rules](#path-rules). |
| `versioning` | string | `append_sha` | How artifacts are written. |

`versioning` modes:

| Mode | Behaviour |
|------|-----------|
| `always_overwrite` | Writes directly to `<path>`. |
| `append_sha` | Writes to `<path>.<sha8>.recotem`, then atomically updates a pointer file at `<path>`. The server reads through the pointer. |

---

## Path rules

Applies to `output.path`, `source.path`, and `item_metadata.path`.

Allowed schemes: bare local path, `s3://`, `gs://`, `az://`.

Rejected schemes: `file://`, `http://`, `https://`, `ftp://`, `ftps://`, `memory://`.

Embedded credentials (`s3://AKIA...:secret@bucket/`) are rejected at recipe load.

Local paths are resolved to absolute. If `RECOTEM_ARTIFACT_ROOT` is set, `output.path` must resolve to a path under it after `realpath` resolution (symlink escapes are rejected).

---

## Environment variable expansion

Syntax: `${RECOTEM_RECIPE_VAR}`. Only variables matching the prefix `RECOTEM_RECIPE_*` are expanded by default. Additional variables can be injected with `recotem train --env-var KEY=value`.

Blacklisted (never expanded regardless of prefix): `RECOTEM_SIGNING_KEY`, `RECOTEM_API_KEYS`, and any name matching `*_SECRET*`, `*_PASSWORD*`, `AWS_*`, `GOOGLE_*`, `GCP_*`.

Expansion is **never** performed inside `source.query` or `source.query_parameters`.

A missing or blacklisted variable produces a `RecipeError`. The error message redacts the variable value.

---

## Full example

```yaml
name: news_articles

source:
  type: bigquery
  query: |
    SELECT user_pseudo_id AS user_id,
           (SELECT value.int_value FROM UNNEST(event_params) WHERE key='article_id') AS item_id,
           TIMESTAMP_MICROS(event_timestamp) AS ts
    FROM   `proj.analytics_123.events_*`
    WHERE  _TABLE_SUFFIX BETWEEN @start_date AND @end_date
      AND  event_name = 'select_content'
  query_parameters:
    start_date: "20260401"
    end_date: "20260507"
  project: my-gcp-project

schema:
  user_column: user_id
  item_column: item_id
  time_column: ts

cleansing:
  drop_null_ids: true
  dedup: keep_last
  min_rows: 5000
  min_users: 100
  min_items: 50

item_metadata:
  type: parquet
  path: gs://my-bucket/items.parquet
  fields: [title, category]
  on_field_missing: error

training:
  algorithms: [IALS, CosineKNN, TopPop]
  metric: ndcg
  cutoff: 20
  n_trials: 40
  timeout_seconds: 1800
  split:
    scheme: time_user
    heldout_ratio: 0.1
    seed: 42

output:
  path: gs://my-bucket/artifacts/news_articles.recotem
  versioning: append_sha
```
