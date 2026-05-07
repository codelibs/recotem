# CSV / Parquet Data Source

The builtin `csv` and `parquet` sources read tabular interaction data via pandas and fsspec. No extra install is required for local files. Cloud storage requires the appropriate fsspec backend.

## Cloud storage extras

| Scheme | Install |
|--------|---------|
| `s3://` | `pip install "recotem[s3]"` |
| `gs://` | `pip install "recotem[gcs]"` |
| `az://` | `pip install "recotem[azure]"` |

## CSV source

```yaml
source:
  type: csv
  path: ./data/interactions.csv
  delimiter: ","          # default ","
  encoding: utf-8         # default utf-8
  header: 0               # row index of the header, default 0
  dtype:
    user_id: str
    item_id: str
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `path` | string | required | Local path, `s3://`, `gs://`, or `az://`. |
| `delimiter` | string | `","` | Single character. |
| `encoding` | string | `"utf-8"` | Any encoding accepted by pandas. |
| `header` | int | `0` | Row number containing column names. |
| `dtype` | map | `{}` | Explicit column type overrides. |

Compressed files (`.gz`, `.bz2`, `.zip`, `.xz`) are decompressed transparently.

## Parquet source

```yaml
source:
  type: parquet
  path: s3://my-bucket/interactions.parquet
  dtype:
    user_id: str
    item_id: str
```

`delimiter`, `encoding`, and `header` are ignored for Parquet. All other fields are the same as CSV.

## fsspec paths

Any path field accepts fsspec URIs. Local paths may be relative (resolved from the working directory at train time) or absolute.

```yaml
# Local (relative)
path: ./data/interactions.csv

# Local (absolute)
path: /mnt/data/interactions.csv

# S3 (uses AWS credentials from instance profile or environment)
path: s3://my-bucket/data/interactions.csv.gz

# GCS (uses ADC)
path: gs://my-bucket/data/interactions.parquet

# Azure Blob Storage
path: az://my-container/interactions.parquet
```

Rejected schemes: `file://`, `http://`, `https://`, `ftp://`, `ftps://`, `memory://`. These are rejected at recipe load with a `RecipeError`.

Embedded credentials in URIs (e.g. `s3://AKIA...:secret@bucket/`) are also rejected at recipe load. Credentials must come from the environment (instance profile, ADC, `AWS_*` env vars, etc.).

## dtype overrides

By default, user and item ID columns are read as whatever type pandas infers. If your IDs look like integers (`1234`, `5678`) but you want them treated as strings, add explicit overrides:

```yaml
dtype:
  user_id: str
  item_id: str
```

This ensures consistent string-coercion between training and serving. Recotem string-coerces both columns internally after load, but setting `dtype: str` avoids pandas misparse of leading-zero IDs like `"0042"`.

## Errors and exit codes

| Error | Exit | Message pattern |
|-------|------|----------------|
| File not found | 3 | `DataSourceError: No such file or path: ./data/interactions.csv` |
| Column missing | 2 | `RecipeError: column 'user_id' not found` |
| Empty file (after header) | 3 | `DataSourceError: file has no data rows` |
| Parse error | 3 | `DataSourceError: ParserError: Error tokenizing data...` |
| Corrupt Parquet | 3 | `DataSourceError: ArrowInvalid: ...` |
| Rejected scheme | 2 | `RecipeError: path scheme 'http' is not allowed` |
| Embedded credentials | 2 | `RecipeError: embedded credentials in path are not allowed` |

## Encoding tips

If your CSV uses a non-UTF-8 encoding (common with data exported from Windows or Excel), set `encoding` explicitly:

```yaml
source:
  type: csv
  path: ./data/interactions.csv
  encoding: cp932       # Shift-JIS (Windows Japanese)
```

Accepted values are any encoding name recognised by Python's `codecs` module: `utf-8`, `utf-8-sig` (UTF-8 with BOM), `latin-1`, `cp932`, `iso-8859-1`, etc.
