# Recipe Reference

A recipe is a YAML file that defines what data to fetch, how to train, and where to write the artifact. One recipe produces one model and one `/predict/{name}` endpoint.

## Top-level fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Endpoint name. Pattern: `^[A-Za-z0-9_-]{1,64}$`. Becomes `/predict/{name}`. |
| `source` | object | yes | Data source config. `type` field is the discriminator (`csv`, `parquet`, `bigquery`, or any plugin). Validated in two stages: the rest of the recipe is parsed first, then the source dict is dispatched to the plugin's `Config` class. As a result, errors in `source.*` surface *after* errors elsewhere in the recipe; an unknown `source.type` raises a `DataSourceError` listing all registered type names. |
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
| `delimiter` | string | `","` | Passed straight to pandas `sep=`. Multi-character separators trigger pandas' Python parser (slower); a single character uses the C parser. CSV only. |
| `encoding` | string | `"utf-8"` | Any encoding accepted by pandas. |
| `header` | int | `0` | Row number of the header. |
| `dtype` | map | `null` | Key = column name, value = pandas dtype string. |
| `sha256` | string | optional (required when `path` is `http://` or `https://`) | 64-char lowercase hex; verified against the fetched bytes; mismatch raises `DataSourceError` |

For Parquet files use `type: parquet`. Only `path` and (optional) `sha256` are accepted — `delimiter`, `encoding`, `header`, and `dtype` are not valid keys on a parquet source and will fail recipe load.

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
| `project` | string | `null` | GCP project ID. Falls back to ADC ambient project. |

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
| `min_rows` | int | `null` (no check) | Minimum row count after cleansing. |
| `min_users` | int | `null` (no check) | Minimum distinct user count. |
| `min_items` | int | `null` (no check) | Minimum distinct item count. |

Violation of any `min_*` threshold exits with code 4 and `"code": "min_data_violation"` in the JSON error line.

`dedup` values:

| Value | Behaviour |
|-------|-----------|
| `keep_first` | Keep the first occurrence of each (user, item) pair. |
| `keep_last` | Keep the last occurrence of each (user, item) pair by row order in the source DataFrame. |
| `none` | No deduplication. |

`keep_first` / `keep_last` use the row order returned by the data source — they do **not** sort by `time_column`. If you need time-ordered deduplication, sort in the source query (BigQuery `ORDER BY ts`) or pre-sort the CSV before training.

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
| `on_field_missing` | string | `error` | What to do if a `fields` entry is absent in the file. `error` fails the model load (at startup the recipe registers as `loaded=false` with `last_load_error` set; on hot-swap the previous model keeps serving and the failure is surfaced via `/health` and the `recotem_artifact_load_failures_total` metric); `null` fills the column with `null`. |
| `sha256` | string | optional (required when `path` is `http://` or `https://`) | 64-char lowercase hex; verified against the fetched bytes; mismatch raises `DataSourceError` |
| `item_id_column` | string | `"item_id"` | Column name in the metadata file that holds item identifiers. Override when your metadata file uses a different column name (e.g. `product_id`). Must be a non-empty, non-whitespace string. |

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
| `algorithms` | list[string] | required | `IALS`, `CosineKNN` (alias `CosinekNN`), `TopPop`, `RP3beta`, `DenseSLIM`, `TruncatedSVD`, `BPRFM`. Full irspack class names (e.g. `IALSRecommender`) are also accepted. Hyperparameter ranges come from each recommender's `default_suggest_parameter` in irspack — they are not user-tunable from the recipe. |
| `metric` | string | `ndcg` | One of `ndcg`, `map`, `recall`, `hit`. |
| `cutoff` | int | `20` | Recommendation list length for evaluation (must be ≥ 1). |
| `n_trials` | int | `40` | Total Optuna trial budget (must be ≥ 1). |
| `per_algorithm_trials` | map | `null` | Per-algorithm trial overrides. **Explicit `0` disables that algorithm** (it is dropped from the search entirely). Algorithms in `algorithms` that are *unspecified* in this map split whatever budget remains after honouring the explicit values. If the explicit values sum to more than `n_trials`, positive values are scaled down proportionally (each remains ≥ 1 *when at least n_trials slots exist*; otherwise the first `n_trials` non-zero classes get one trial each and the remainder are skipped — the total budget never exceeds `n_trials`). Unknown algorithm keys are silently ignored. |
| `per_trial_timeout_seconds` | int | `null` | Soft per-trial wall-clock cap. Implemented by running the trial in a worker thread; if it overshoots, Optuna prunes the trial but the underlying thread is daemonised and may continue until it finishes naturally (CPU/memory still spent). |
| `timeout_seconds` | int | `null` | Overall tuning wall-clock cap. |
| `parallelism` | int | `1` | Optuna `n_jobs` (Python threads, not processes). Algorithms whose hot loop is GIL-bound see little speed-up; native-code learners (IALS, RP3beta) benefit most. |
| `storage_path` | string | `""` | Empty = in-memory (no resume). A bare path becomes a SQLite URL (`sqlite:///<path>`); explicit `sqlite://`, `postgresql://`, `postgres://`, and `mysql://` URLs are also accepted. Study name is `recotem_<recipe_name>_<run_id>` and `load_if_exists=True`, so a fresh `run_id` per train invocation always starts a new study (resume requires reusing the same `run_id`). **SQLite over NFS corrupts** — keep SQLite databases on a local filesystem. **URLs must not embed credentials** (`postgresql://user:pass@host/db` is rejected with `SearchError` so userinfo cannot leak through SQLAlchemy tracebacks). Provide credentials via `PGPASSFILE` / `~/.pgpass` / SQLAlchemy env vars instead. |
| `split.scheme` | string | `random` | `random`, `time_global`, or `time_user`. See semantics below. |
| `split.heldout_ratio` | float | `0.1` | Fraction of interactions held out. Must be in (0, 1). |
| `split.test_user_ratio` | float | `1.0` | Fraction of users included in the test split. Must be in (0, 1]. |
| `split.seed` | int | `42` | Random seed for the split (passed to irspack as `random_state`). |

Split scheme semantics:

- `random` — interactions are held out uniformly at random per user. `time_column` is unused.
- `time_user` — for each user, the most recent `heldout_ratio` of that user's interactions (ranked by `time_column`) are held out. Cutoff is computed per user.
- `time_global` — a single global cutoff at the `1 - heldout_ratio` quantile of `time_column` over the whole dataset; every interaction at or after the cutoff is held out, regardless of user. Users with no post-cutoff interactions become train-only.

`time_user` and `time_global` require `schema.time_column`. Missing `time_column` with these schemes is a recipe validation error and exits with code 2.

If a search produces no completed trials, training exits with code 4 and `"code": "no_completed_trials"`. If every completed trial scores exactly 0.0, exit 4 with `"code": "zero_score"` (typically caused by too short a `per_trial_timeout_seconds` or a too-small validation set).

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

Path schemes for `source.path` and `item_metadata.path`: any fsspec-supported
scheme is accepted. Schemes `http://` and `https://` additionally require an
`sha256` integrity pin on the same config block.

`output.path` rejects schemes that fsspec does not implement for writes:
`http://`, `https://`, `ftp://`, `ftps://`, `memory://`. Acceptable output
schemes: bare local, `file://`, `s3://`, `gs://`, `az://`.

Embedded credentials (`s3://AKIA...:secret@bucket/`) are rejected at recipe
load on every path field.

Local paths are resolved to absolute. If `RECOTEM_ARTIFACT_ROOT` is set,
`output.path` must resolve to a path under it after `realpath` resolution
(symlink escapes are rejected).

---

## Environment variable expansion

Syntax: `${RECOTEM_RECIPE_VAR}`. Only variables matching the prefix `RECOTEM_RECIPE_*` are expanded. Matching is case-insensitive (the *upper-cased* name is checked against the prefix and blacklist). Additional values can be injected with `recotem train --env-var KEY=value`; the `KEY` must still match the `RECOTEM_RECIPE_*` prefix and pass the blacklist check.

Blacklisted (never expanded regardless of prefix): `RECOTEM_SIGNING_KEY`, `RECOTEM_API_KEYS`, and any name matching `*_SECRET*`, `*_PASSWORD*`, `AWS_*`, `GOOGLE_*`, `GCP_*`.

Expansion is **never** performed inside any key named `query` or `query_parameters` at any nesting level (not just under `source`). All other strings — including `source.path`, `output.path`, and `item_metadata.path` — are expanded.

Expansion is single-pass and runs once at YAML load time. There is no escape syntax (a literal `${...}` in the YAML cannot be preserved unless the variable name fails the prefix check, which raises an error), no default-value syntax (`${VAR:-default}` is not supported and would attempt to expand the literal name `VAR:-default`), and substituted values are not re-scanned for further `${...}` references.

A missing, malformed, or blacklisted variable produces a `RecipeError` (exit 2). The error message names the variable but never includes its value.

### Loading a directory of recipes

`recotem serve --recipes <dir>` and `load_recipes_directory()` enumerate only direct `*.yaml` children of `<dir>` (non-recursive). Subdirectories are ignored. Each recipe file must remain inside the directory after `realpath` resolution — symlinks pointing outside are rejected. Two recipes with the same `name` field abort the load.

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
