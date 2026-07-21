# Recipe Reference

A recipe is a YAML file that defines what data to fetch, how to train, and where to write the artifact. One recipe produces one model and one set of `/v1/recipes/{name}:*` endpoints.

## Top-level fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Endpoint name. Pattern: `^[A-Za-z0-9_-]{1,64}$`. Becomes `/v1/recipes/{name}:*`. |
| `source` | object | yes | Data source config. `type` field is the discriminator (`csv`, `parquet`, `bigquery`, `sql`, or any plugin). Validated in two stages: the rest of the recipe is parsed first, then the source dict is dispatched to the plugin's `Config` class. As a result, errors in `source.*` surface *after* errors elsewhere in the recipe; an unknown `source.type` raises a `DataSourceError` listing all registered type names. |
| `schema` | object | yes | Column mapping. |
| `cleansing` | object | no | Data quality gates. |
| `item_metadata` | object | no | Metadata joined into predict responses. |
| `features` | object | no | Item/user side features for feature-aware iALS training and cold-start. |
| `training` | object | yes | Algorithm and tuning settings. |
| `output` | object | yes | Artifact path and versioning. |

`name` is validated at YAML load via the `^[A-Za-z0-9_-]{1,64}$` regex. The Recipe pydantic model uses `validate_assignment=True`, so any post-construction mutation of `name` re-runs the validator and raises `ValidationError` on illegal values. The helper `recotem.recipe.models.validate_for_filesystem(name)` is exported for callers who construct names programmatically without pydantic.

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
| `path` | string | required | Local path, `file://`, `s3://`, `gs://`, `az://`, `abfs(s)://`, `http://`, or `https://` URI. HTTP/HTTPS requires a `sha256` integrity pin; see [Path rules](#path-rules) and [data-sources/csv.md](data-sources/csv.md#path-schemes). |
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

### `source.type = sql`

```yaml
source:
  type: sql
  dsn_env: RECOTEM_RECIPE_DATABASE_DSN
  query: |
    SELECT user_id, item_id, created_at AS ts
    FROM events
    WHERE created_at >= :min_date
  query_parameters:
    min_date: "2026-04-01"
  connect_timeout_seconds: 10
  statement_timeout_seconds: 300
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `dsn_env` | string | required | Environment variable name containing the SQLAlchemy connection URL. Must be a `RECOTEM_RECIPE_*` variable (e.g. `RECOTEM_RECIPE_DATABASE_DSN`). The URL can use any SQLAlchemy-supported dialect: PostgreSQL, MySQL, MariaDB, SQLite. The connection is read-only and attempts to set transaction isolation appropriately per dialect. |
| `query` | string | required | SQL. Trusted code — not env-expanded. Use parameterized queries to pass dynamic values. Results are read in chunks (100,000 rows per chunk) and are capped by `RECOTEM_MAX_SQL_ROWS` (default configurable). |
| `query_parameters` | map | `{}` | Named parameters bound via SQLAlchemy `text().bindparams(...)`. Use `:name` placeholders in `query`. Type values: `str`, `int`, `float`, or `bool`. |
| `connect_timeout_seconds` | int | `10` | Connection establishment timeout in seconds. Valid range [1, 60] (out-of-range raises ValidationError). |
| `statement_timeout_seconds` | int | `300` | Per-statement execution timeout in seconds. Valid range [1, 1800] (out-of-range raises ValidationError). |

Install the extra for your SQL dialect: `pip install "recotem[postgres]"`, `recotem[mysql]`, or `recotem[sqlite]`.

See [docs/data-sources/sql.md](data-sources/sql.md) for full reference and examples.

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
| `time_unit` | string | conditional | Required when `time_column` contains integer (numeric) values. One of `s`, `ms`, `us`, `ns`. Omitting this field for a numeric time column raises a `TrainingError` (`code: time_unit_required`) to avoid silent nanosecond interpretation of Unix timestamps. String and datetime columns are unaffected by this field. |

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

## `features`

```yaml
features:
  item:
    source:                                    # datasource discriminated union — same registry as `source`
      type: bigquery
      query: SELECT item_id, genres, release_year, country FROM items
    id_column: item_id
    columns:
      - {name: genres,       encoding: multi_label, delimiter: "|"}
      - {name: release_year, encoding: numerical}
      - {name: country,      encoding: categorical, min_frequency: 5}
  user:
    source: {type: csv, path: ./users.csv}
    id_column: user_id
    columns:
      - {name: age_band, encoding: categorical}
```

The mere presence of this block enables feature-aware iALS training — there
is no separate flag. Item and user side features are declared, encoded, fed
to `IALSRecommender` during Optuna search and the final refit, and persisted
so that `:recommend` / `:recommend-related` can score unknown users and
unknown seed items from their attributes alone. See
[api-reference.md](api-reference.md) for the serving-side cold-start
contract.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `features.item` | object | conditional | Item-side feature table. At least one of `features.item` / `features.user` must be present. |
| `features.user` | object | conditional | User-side feature table. |

Each side (`FeatureSideConfig`) has:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `source` | object | yes | Same datasource discriminated union as top-level `source` (`csv`, `parquet`, `bigquery`, `sql`, or any plugin). Reuses the datasource registry — `FetchContext` carries no interaction-specific fields, so any registered source can serve as a feature table. |
| `id_column` | string | yes | Column in the fetched table that holds the entity id (item id for `features.item`, user id for `features.user`). Non-empty, non-whitespace. Must **not** also appear in `columns` — the id column is consumed as the index and cannot also be a feature. |
| `columns` | list | yes, non-empty | One entry per source column to encode. Column names must be unique within a side. |

**Null and duplicate ids are dropped before the vocabulary is built.** A row
whose `id_column` is null or empty is dropped and logged as
`feature_table_null_ids_dropped` (`side`, `drop_count`). A row whose
`id_column` repeats an id already seen is also dropped — the **first**
occurrence wins (`keep="first"`) — and logged as
`feature_table_duplicate_ids_dropped` (`side`, `drop_count`). Both log lines
carry only a count, never the offending ids or column values, which are
treated as user PII.

Each entry in `columns` (`FeatureColumn`):

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `name` | string | yes | — | Column name in the fetched feature table. |
| `encoding` | string | yes | — | One of `categorical`, `numerical`, `multi_label`. |
| `delimiter` | string | conditional | `"\|"` | Only valid when `encoding: multi_label`; rejected on any other encoding. Must not be empty. |
| `min_frequency` | int | no | `1` | Must be `>= 1` — `min_frequency: 0` is rejected at schema-validation time; there is no upper bound. Only valid for `categorical` / `multi_label` (vocabulary-based encodings); rejected on `numerical`. Values occurring fewer than N times in the fetched feature table are dropped from the vocabulary. For `categorical` this is a row count (one value per row); for `multi_label` it counts token **occurrences** — a single row with `a\|a` contributes 2 toward the threshold. |

### Ids are matched as strings, and zero overlap is fatal

`id_column` values are matched against the interaction data's
`schema.item_column` / `schema.user_column` **as strings** — both sides are
normalized with `str()` before comparison. So `1` matches `"1"`, but `1.0` does
**not** match `"1"`.

An interaction id that is absent from the feature table is not an error: it
encodes to the implicit bias column alone, degrading to plain iALS for that one
entity. Partial coverage is expected and legitimate — the vocabulary is built
from the whole fetched table precisely so that entities missing from the
interaction data stay representable for cold-start scoring.

**Zero** overlap is different: it aborts training with `TrainingError`
(`feature_axis_error`, exit 4). If not one id matches, every entity encodes to
bias-only, and the run would otherwise succeed and sign an artifact whose header
advertises `features` for what is really plain iALS — a silent downgrade. The
error samples ids from both sides so the mismatch is visible. Two causes account
for essentially all of these:

- An **id dtype mismatch**: one blank cell in an otherwise-integer id column
  makes pandas infer `float64`, so `1` reads back as `1.0`. Pin the type at the
  source — `dtype: {item_id: str}` on a `csv` source (`dtype` is csv-only; on
  `bigquery` / `sql` cast in the query instead). recotem will not coerce it for
  you: a column reading `1.0` is indistinguishable from one whose ids are
  literally `"1.0"`, so coercion would risk silently rewriting valid ids.
- An `id_column` naming a **wrong-but-existing column**, which passes the
  presence check at fetch time and fails only at encode time.

Coverage is logged per side per phase as `feature_axis_coverage` (`side`,
`matched`, `total`). See
[operations.md](operations.md#recotem-train-exits-4-with-feature_axis_error).

### Encodings, and their missing/unknown behavior

| Encoding | Behavior | Row missing entirely | Value missing / unknown |
|---|---|---|---|
| `categorical` | One-hot over the training vocabulary. | All-zero segment. | All-zero segment. |
| `numerical` | Standardized by the training mean/std. | `0` (i.e. the mean). | `0` (i.e. the mean). |
| `multi_label` | Split on `delimiter`, multi-hot. | All-zero segment. | Known tokens are retained; unknown tokens are dropped. |

The `multi_label` distinction matters: `genres: "Action|Zzz"` with `Action`
known yields `Action=1` and drops `Zzz` — it is not an all-zero segment.
"Row missing" and "value unknown" coincide only for `categorical`.

The same `str()`-matching caveat that applies to `id_column` above also
applies to a `categorical` or `multi_label` **value** column — the vocabulary
is fit from each value's string rendering, and a serve-time request value is
matched the same way. If a blank cell makes pandas infer `float64` for an
otherwise-integer column, its vocabulary is trained from `"1990.0"` (a
`multi_label` column's tokens the same way), and a serve-time request sending
the JSON integer `1990` (matched as `"1990"`) misses every key. So **declare
id-like or numeric-looking attribute columns as strings at the source** — one
blank cell is enough to flip the whole column to `float64` inference. Prefer
consistent types over relying on the counter after the fact. Unlike the id
axis, this is **not** refused at train time — the column varies across rows, so
training stays self-consistent — so pin the type at the source (`dtype: {year:
str}` on `csv`; `CAST(... AS STRING)` on `bigquery` / `sql`; fix the schema on
`parquet`) exactly as for the id column. The mismatch is not silent, though: at
serve time each such miss increments
`recotem_v1_feature_unknown_value_total` (labelled by recipe / side / column —
see [operations.md](operations.md#feature-aware-ials-sizing)), so a spike on a
column you expected to match is the signal to check its source dtype. The **id
axis** dtype trap is the stricter, train-time analogue: there the same `"1.0"`
vs `"1"` mismatch drives coverage to 0% and aborts training with the
[zero-overlap refusal](#ids-are-matched-as-strings-and-zero-overlap-is-fatal)
(`feature_axis_error`, exit 4) rather than degrading silently at serve time.

At serve time, each cold-start feature value supplied to `:recommend` /
`:recommend-related` (`user_features`, and each `item_features` seed mapping) is
length-capped: a string value longer than **8192 characters** is rejected with
`422` (the error names the offending column, never the value). This bounds the
`multi_label` tokenization work per request — 8192 characters is generous for a
real token list while blocking megabyte-scale amplification. The same cap
applies on the batch verbs, but a violation there surfaces as a per-element
`VALIDATION_ERROR` inside the `200` batch response rather than failing the
whole batch with `422`. Non-string scalar values are unaffected.

If a `numerical` column is constant — or merely **near**-constant — in the
training data, its segment is emitted as zeros and a warning is logged
(`feature_zero_variance_column`). The trigger is not an exact `std == 0.0`
check but a floor relative to the column's own scale: `std <= 1e-8 ×
max(abs(mean), 1.0)`. A column whose values differ only by floating-point
rounding noise (std ~1e-15) would survive an exact check and then divide
serve-time standardization by a near-zero denominator, turning an ordinary
request value into an astronomically large standardized one — which trips the
cold-start solver's numerical guard for a reason the client cannot see or
control. Such a column degrades exactly like a missing value instead. See
[api-reference.md](api-reference.md#feature-aware-cold-start).

An implicit all-ones **bias column** is appended per side (irspack adds no
intercept on its own). It is deliberately collinear with every
`categorical` column's one-hot block — a drop-first encoding was considered
and rejected because it would make an unknown/missing value (all-zero
segment) indistinguishable from the dropped reference level. The ridge
(`lambda_*_feature`, below) absorbs the resulting rank deficiency at the
tuned range. One consequence: if training fails with `Feature ridge
Cholesky decomposition failed`, the message deliberately does not suggest
dropping a column — recotem's own bias column is the more likely structural
cause, and it cannot be removed from the recipe. See
[operations.md](operations.md#feature-aware-ials-sizing) for the remedy
(`min_frequency`).

### `min_frequency` is the dimension-cap lever

The encoder vocabulary is built from the **whole fetched feature table**,
not restricted to items/users present in the interaction data — this
maximizes cold-start coverage. Consequently the encoded dimension scales
with **catalog size, not interaction count**: a 1M-item catalog whose
interactions cover only 1k items still pays the full encoded dimension (and
the full training cost — see below) for the other 999k items. Raising
`min_frequency` on high-cardinality columns is the only lever against
`RECOTEM_MAX_FEATURE_DIM` (default 5000; see
[operations.md](operations.md#feature-aware-ials-sizing)); there is no
recipe-level way to restrict the vocabulary to interaction-covered rows.

Raising it too far fails **loudly but not fatally**. `min_frequency` has no
upper bound and nothing cross-checks it against the catalog, so
`min_frequency: 50` against a 3-row feature table validates happily and
prunes every token. The column then encodes to `width=0` and contributes
nothing — every row falls back to the implicit bias column — while the
`feature_encoder_state_built` INFO event still lists the column as though it
were active. Training logs a `feature_empty_vocabulary_column` **warning**
(carrying the column name, its `encoding` and `min_frequency`, and the
distinct/occurrence counts — never the token values) and continues. An
all-null column reaches the same "contributes nothing" state by a different
route and warns identically. Check the training logs after raising
`min_frequency` aggressively.

### `lambda_item_feature` / `lambda_user_feature` — the one exception to "not user-tunable"

`training.algorithms`' hyperparameter ranges normally come from each
recommender's `default_suggest_parameter` in irspack and are **not**
user-tunable from the recipe (see the `algorithms` row above). The
feature-ridge coefficients are the first exception: `lambda_item_feature`
and `lambda_user_feature` are **recotem's own** search range —
`suggest_float(..., 5e-2, 1e6, log=True)` — applied only to the side(s)
that have a `features.item` / `features.user` block, and only when the
trial's class is `IALSRecommender`. They are not present as recipe fields;
they cannot be set explicitly, only tuned.

Two reasons this range is recotem's own rather than irspack's: irspack ships
no default range for these parameters (`default_suggest_parameter` never
suggests them), and the constructor default of `0.0` is a **hard error**
whenever the matching feature matrix is non-empty (`ValueError: Feature
weight regularization must be positive.`) — so leaving it untuned is not an
option once a features block is present.

### Validation

Recipe load rejects, with `RecipeError` (exit 2):

- An `encoding` outside `categorical` / `numerical` / `multi_label`.
- `delimiter` set on a column whose `encoding` is not `multi_label`.
- `min_frequency` set (to anything other than the default) on a `numerical` column.
- Duplicate column names within one side's `columns` list.
- An `id_column` that also appears as a `columns[].name` on the same side —
  the id column is consumed as the index, so a feature column of the same name
  would be missing at encode time. Caught at load rather than at train time.
- `features:` present but `training.algorithms` contains no feature-capable
  algorithm (today: `IALS`). Either add `IALS` to `algorithms` or remove the
  `features` block.
- `features.item.source` / `features.user.source` fail the same
  [path-scheme allow-list and mandatory-sha256-for-network-paths rules](#path-rules)
  as the top-level `source`.

`recotem validate` probes `features.item.source` / `features.user.source`
connectivity the same way it probes `source` — each reported line carries a
`[features.item.source]` / `[features.user.source]` label so a failure
names which source failed.

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
| `algorithms` | list[string] | required | `IALS`, `CosineKNN` (alias `CosinekNN`), `TopPop`, `RP3beta`, `DenseSLIM`, `TruncatedSVD`, `BPRFM`. Full irspack class names (e.g. `IALSRecommender`) are also accepted. Hyperparameter ranges come from each recommender's `default_suggest_parameter` in irspack — they are not user-tunable from the recipe, **with one exception**: when a [`features`](#features) block is present, `lambda_item_feature` / `lambda_user_feature` are tuned over recotem's own range (`5e-2`–`1e6`, log-scale), because irspack ships no default range for them and their constructor default of `0.0` is a hard error whenever the matching feature matrix is non-empty. |
| `metric` | string | `ndcg` | One of `ndcg`, `map`, `recall`, `hit`. |
| `cutoff` | int | `20` | Recommendation list length for evaluation (must be ≥ 1). |
| `n_trials` | int | `40` | Total Optuna trial budget (must be ≥ 1). |
| `per_algorithm_trials` | map | `null` | Per-algorithm trial overrides. **Explicit `0` disables that algorithm** (it is dropped from the search entirely). Algorithms in `algorithms` that are *unspecified* in this map split whatever budget remains after honouring the explicit values. If the explicit values sum to more than `n_trials`, positive values are scaled down proportionally (each remains ≥ 1 *when at least n_trials slots exist*; otherwise the first `n_trials` non-zero classes get one trial each and the remainder are skipped — the total budget never exceeds `n_trials`). **Unknown algorithm keys are rejected at recipe-load time with a ValidationError** — each key must be a valid alias or class name present in `algorithms`. When `parallelism > 1`, the actual per-algorithm trial count may exceed the configured budget by up to `parallelism - 1` trials due to in-flight concurrent trials; a warning is logged on each run where this condition applies. |
| `per_trial_timeout_seconds` | int | `null` | Soft per-trial wall-clock cap. Implemented by running the trial in a worker thread; if it overshoots, Optuna prunes the trial but the underlying thread is daemonised and may continue until it finishes naturally (CPU/memory still spent). The count of threads still running at the time the study finishes is reported as `n_orphaned` in the `train_done` structured log event. Operators can monitor this field to detect trials that consistently exceed the timeout and adjust `per_trial_timeout_seconds` or `timeout_seconds` accordingly. |
| `timeout_seconds` | int | `null` | Overall tuning wall-clock cap. |
| `parallelism` | int | `1` | Optuna `n_jobs` (Python threads, not processes). Algorithms whose hot loop is GIL-bound see little speed-up; native-code learners (IALS, RP3beta) benefit most. |
| `storage_path` | string | `""` | Empty = in-memory (no resume). A bare path becomes a SQLite URL (`sqlite:///<path>`); explicit `sqlite://`, `postgresql://`, `postgres://`, and `mysql://` URLs are also accepted. Study name is `recotem_<recipe_name>_<run_id>` and `load_if_exists=True`, so a fresh `run_id` per train invocation always starts a new study (resume requires reusing the same `run_id` — pass `recotem train --run-id <stable>`). **SQLite over NFS corrupts** — keep SQLite databases on a local filesystem. **URLs must not embed credentials** (`postgresql://user:pass@host/db` is rejected with `SearchError` so userinfo cannot leak through SQLAlchemy tracebacks). Provide credentials via `PGPASSFILE` / `~/.pgpass` / SQLAlchemy env vars instead. |
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

Path schemes for `source.path` and `item_metadata.path` are restricted to an
explicit allow-list: bare local path (no scheme prefix), `file://`, `s3://`,
`gs://`, `az://`, `abfs://`, `abfss://`, `http://`, `https://`. Schemes are
explicitly enumerated rather than relying on fsspec's full registry to prevent
unvetted handlers from being reachable via recipe content. Chained fsspec
protocols (paths containing `::`) are also rejected. Schemes `http://` and
`https://` additionally require an `sha256` integrity pin on the same config
block.

> **Decompressed-size cap not enforced.** `RECOTEM_MAX_DOWNLOAD_BYTES` caps
> raw I/O bytes only. Compressed CSV and columnar Parquet sources can expand
> to a multiple of the raw size after decompression; the resulting DataFrame
> is not size-capped. Run `recotem train` inside a cgroup or Kubernetes Pod
> with a memory limit to contain the impact. See
> [security.md — Decompressed-size cap not enforced](security.md#decompressed-size-cap-not-enforced-medium-5).

`output.path` is restricted to the following schemes: bare local path (no prefix),
`file://`, `s3://`, `gs://`, `az://`, `abfs://`, `abfss://`. Other schemes are
rejected: `http://`, `https://`, `ftp://`, and `ftps://` because Recotem does
not support writing artifacts over those protocols; `memory://` because it is
process-local and would not survive past the training run.

Embedded credentials (`s3://AKIA...:secret@bucket/`) are rejected at recipe
load on every path field.

Local paths are resolved to absolute. If `RECOTEM_ARTIFACT_ROOT` is set,
`output.path` must resolve to a path under it after `realpath` resolution
(symlink escapes are rejected).

---

## Environment variable expansion

Syntax: `${RECOTEM_RECIPE_VAR}`. Only variables matching the prefix `RECOTEM_RECIPE_*` are expanded. Matching is case-insensitive (the *upper-cased* name is checked against the prefix and blacklist). Additional values can be injected without exporting to the shell environment using `recotem train --env-var KEY=VALUE` (repeatable). The `KEY` must still start with `RECOTEM_RECIPE_` and pass the blacklist check. Example: `recotem train recipe.yaml --env-var RECOTEM_RECIPE_DATE=20260501`.

Blacklisted (never expanded regardless of prefix): exact names `RECOTEM_SIGNING_KEYS` and `RECOTEM_API_KEYS`; names starting with `AWS_`, `GCP_`, `GOOGLE_`, or `AZURE_`; and any name containing the substrings `SECRET`, `PASSWORD`, `PASSWD`, `TOKEN`, `KEY`, `AUTH`, `BEARER`, `CRED`, or `PRIVATE` (all comparisons case-insensitive).

The `*KEY*` substring match is intentionally broad — any variable whose uppercased name contains the substring `KEY` (no underscore boundary) is rejected. This includes `RECOTEM_RECIPE_PARTITION_KEY`, `RECOTEM_RECIPE_APIKEY`, and `RECOTEM_RECIPE_KEYBOARD`. Use a name that does not contain `KEY` (e.g. `RECOTEM_RECIPE_PARTITION_COLUMN`).

Expansion is **never** performed inside any key named `query` or `query_parameters` at any nesting level (not just under `source`). All other strings — including `source.path`, `output.path`, and `item_metadata.path` — are expanded.

> **Prefix vs. blacklist interaction.** The `RECOTEM_RECIPE_` prefix check is applied to the full variable name. Only the *tail* portion (after `RECOTEM_RECIPE_`) is not subject to any additional prefix check — only the blacklist substring rules apply. For example, `RECOTEM_RECIPE_GCP_PROJECT` satisfies the prefix check; it is **not** blocked by the `GCP_*` blacklist-prefix rule because that rule matches only names whose uppercased form starts with `GCP_` (e.g. `GCP_SOMETHING`). The variable `RECOTEM_RECIPE_GCP_PROJECT` starts with `RECOTEM_RECIPE_`, not `GCP_`. The `examples/ga4-bigquery/` recipe uses this pattern legitimately. However, it **would** be blocked if its name contained `KEY`, `TOKEN`, `SECRET`, or any other blacklisted substring (case-insensitive).

Expansion is single-pass and runs once at YAML load time. There is no escape syntax (a literal `${...}` in the YAML cannot be preserved unless the variable name fails the prefix check, which raises an error), no default-value syntax (`${VAR:-default}` is not supported and would attempt to expand the literal name `VAR:-default`), and substituted values are not re-scanned for further `${...}` references.

A missing, malformed, or blacklisted variable produces a `RecipeError` (exit 2). The error message names the variable but never includes its value.

### Loading a directory of recipes

`recotem serve --recipes <dir>` and `load_recipes_directory()` enumerate only direct `*.yaml` children of `<dir>` (non-recursive). Subdirectories are ignored. Each recipe file must remain inside the directory after `realpath` resolution — symlinks pointing outside are rejected.

Duplicate `name` field handling differs by call site:

- **`recotem train` / `load_recipes_directory()` (strict)**: a duplicate `name` across any two files raises `RecipeError` immediately and aborts the entire load.
- **`recotem serve` / `load_recipes_directory_lenient()` (lenient)**: the first file loaded wins; any subsequent file with the same `name` is skipped and a `recipe_duplicate_name_skipped` warning is emitted to the structured log. The serve process continues with the surviving recipe.

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
