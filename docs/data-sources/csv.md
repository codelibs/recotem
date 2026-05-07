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

## Path schemes

Any fsspec-supported scheme is accepted on `source.path` and
`item_metadata.path`:

```yaml
# Local (relative or absolute)
path: ./data/interactions.csv
path: /mnt/data/interactions.csv

# Object storage (uses cloud SDK auth — instance profile / ADC / env vars)
path: s3://my-bucket/data/interactions.csv.gz
path: gs://my-bucket/data/interactions.parquet
path: az://my-container/interactions.parquet

# HTTP / HTTPS — `sha256` integrity pin is REQUIRED
path: https://files.example.com/2025-01/interactions.csv
sha256: 945fc769205a5976d38c5783500ae473afbb04608043b703951a699993c8f8be

# file:// is treated as a bare local path
path: file:///mnt/data/interactions.csv
```

Embedded credentials in URIs (e.g. `s3://AKIA...:secret@bucket/`) are
rejected at recipe load. Credentials must come from the environment
(instance profile, ADC, `AWS_*` env vars, etc.).

`output.path` is more restrictive — `http://`, `https://`, `ftp://`,
`ftps://`, and `memory://` are rejected because writes are not supported
on those schemes. Use a bare local path, `file://`, or a writeable
object-store scheme.

## Network-scheme integrity (HTTP / HTTPS)

When `source.path` (or `item_metadata.path`) uses `http://` or `https://`:

- `sha256` is **mandatory** on the same config block. Recipe load fails
  with `RecipeError` if it is missing.
- The fetch is performed via stdlib `urllib.request` — no extra runtime
  deps required. Up to 5 redirects are followed (using a custom opener
  that bypasses urllib's default redirect handler), with TLS verification
  always on for `https://`. Redirects to non-`http(s)://` schemes are
  rejected.
- The downloaded payload is capped at `RECOTEM_MAX_DOWNLOAD_BYTES` (default
  256 MiB; clamped to [1 MiB, 16 GiB]).
- The connect/read timeout is `RECOTEM_HTTP_TIMEOUT_SECONDS` (default 30,
  clamped to [1, 600]).
- `recotem validate` issues a HEAD-like check (`fs.exists()` for non-network
  schemes); the integrity check is performed at fetch time, not validate.

Compute the sha256 once when authoring the recipe:

```bash
curl -sL <url> | shasum -a 256
```

If the upstream file rotates, regenerate the value and update the recipe.
The mismatch is the alert.

## sha256 on non-network paths

`sha256` is also valid (but optional) on local, `file://`, and object-store
paths. When set, the bytes are hashed and compared post-read. Useful for
internal reproducibility audits even when the network is not involved.
On non-network paths, when `sha256` is unset, pandas streams via fsspec
without buffering the full file (preserving large-file performance).

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
