# Source reference: CSV / Parquet files

Use when interactions already exist as a file — a data export, a warehouse
unload, or a hand-built table. Local paths and object stores
(`s3://`, `gs://`, `az://`/`abfs://`/`abfss://`) are supported, plus
`http(s)://` for CSV/Parquet.

## Key difference from query sources

There is **no query and no transform step**. The file must already contain the
columns named in `schema` (`user_column`, `item_column`, and optionally
`time_column`); recotem reads them by name. Any extraction (e.g. an ID out of a
URL), filtering (which rows count as a positive), or aggregation must be done
**upstream when the file is produced**. If you control the producing query, that
is also where you would apply the "users with ≥2 distinct items" filter from
Step 5.

## Inputs to gather

- **Path** and scheme (local / `s3://` / `gs://` / `az://` / `http(s)://`).
- The **column names** in the file for user, item, and (optionally) time — these
  go straight into `schema`.
- CSV only: `delimiter`, `encoding`, header row index if non-standard.
- For `http(s)://`: a **`sha256`** of the file is required, and the body is
  capped by `RECOTEM_MAX_DOWNLOAD_BYTES` (default 256 MiB).

## `source:` block

CSV:

```yaml
source:
  type: csv
  path: /abs/path/interactions.csv     # or s3://bucket/key.csv, gs://..., https://...
  delimiter: ","                       # optional (default ",")
  encoding: "utf-8"                    # optional
  # header: 0                          # optional row index of the header
  # dtype: { item_id: str }            # optional per-column dtype hints
  # sha256: "<64 hex>"                  # REQUIRED for http(s):// sources
```

Parquet:

```yaml
source:
  type: parquet
  path: /abs/path/interactions.parquet # or s3://..., gs://..., https://...
  # sha256: "<64 hex>"                  # REQUIRED for http(s):// sources
```

Then map the file's own column names:

```yaml
schema:
  user_column: user_id     # whatever the file calls them
  item_column: item_id
  time_column: ts          # omit if the file has no timestamp
```

Embedded URI credentials are rejected; configure object-store auth through the
environment (fsspec / cloud SDK), not the path. Local `output.path` may be
constrained to `RECOTEM_ARTIFACT_ROOT` if that is set.

## Cost / volume

There is no billing dry run. `validate` confirms the path exists and is
readable. The practical limits are memory and download size: the whole file is
read into a DataFrame, and `RECOTEM_MAX_DOWNLOAD_BYTES` caps the raw bytes for
network/object-store reads (it does **not** cap the decompressed DataFrame).
Keep an eye on row count for large exports; pre-aggregate upstream if needed.

See `docs/data-sources/csv.md` for details.
