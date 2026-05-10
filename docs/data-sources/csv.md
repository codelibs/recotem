# CSV / Parquet Data Source

The builtin `csv` and `parquet` sources read tabular interaction data via pandas and fsspec. No extra install is required for local files. Cloud storage requires the appropriate fsspec backend.

## Cloud storage extras

| Scheme | Install |
|--------|---------|
| `s3://` | `pip install "recotem[s3]"` |
| `gs://` | `pip install "recotem[gcs]"` |
| `az://` / `abfs(s)://` | `pip install "recotem[azure]"` |

> **Azure extra and the official Docker image.** The official Docker image does not include the Azure extra. If you need `az://` or `abfs(s)://` support, build a derived image that installs `recotem[azure]` (e.g. `FROM ghcr.io/codelibs/recotem:latest` + `RUN pip install "recotem[azure]"`).

`http://` and `https://` URIs are accepted without any extra install. A `sha256` integrity pin is **mandatory** for network-scheme paths, and the body is capped at `RECOTEM_MAX_DOWNLOAD_BYTES` (default 256 MiB). See [Network-scheme integrity](#network-scheme-integrity-http--https) below. `file://` is treated as a bare local path and requires no extra install.

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
| `path` | string | required | Accepts a local path, `file://`, `s3://`, `gs://`, `az://`, `abfs(s)://`, `http://`, or `https://` URI. HTTP/HTTPS requires a `sha256` integrity pin and applies a body size cap; see [Path schemes](#path-schemes) below. |
| `delimiter` | string | `","` | Passed straight to pandas `sep=`. Multi-character values switch pandas to its slower Python parser. |
| `encoding` | string | `"utf-8"` | Any encoding accepted by pandas. |
| `header` | int | `0` | Row number containing column names. |
| `dtype` | map | `null` | Explicit column type overrides. |

Compressed files (`.gz`, `.bz2`, `.zip`, `.xz`) are decompressed transparently.

## Parquet source

```yaml
source:
  type: parquet
  path: s3://my-bucket/interactions.parquet
```

Parquet sources accept only `path` and the optional `sha256` integrity pin. `delimiter`, `encoding`, `header`, and `dtype` are not valid keys on a parquet source and will fail recipe load.

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

Embedded credentials in URIs (e.g. `https://user:pass@host/file.csv`) are
rejected at recipe load. Credentials must come from the environment
(instance profile, ADC, `AWS_*` env vars, etc.).

The userinfo check is scheme-blind: any URI parsed by `urllib.parse` as
having `username` or `password` is rejected, regardless of scheme. This
means object-store paths must not contain a `@` before the host — for
example `gs://bucket@project/file.csv` is rejected even though `@` is
not used for credentials in GCS URIs. Use the canonical form
`gs://bucket/path/file.csv` and rely on Application Default Credentials
or the service-account file referenced by `GOOGLE_APPLICATION_CREDENTIALS`.

`${RECOTEM_RECIPE_*}` env-var expansion **is** performed inside `path`
fields (and is the recommended way to inject bucket names, dates, or
runtime-specific path components). Expansion is suppressed only inside
`query` / `query_parameters`.

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
  rejected, as are redirect loops (visited URLs are tracked).
- The downloaded payload is capped at `RECOTEM_MAX_DOWNLOAD_BYTES` (default
  256 MiB; clamped to [1 MiB, 16 GiB]). The cap is checked *during* the
  read, not afterwards — once the limit is exceeded the connection is
  dropped and `DataSourceError` is raised; partial bytes are not parsed.
  Note: the same cap also applies to local and object-store source reads
  (see below).
- The connect/read timeout is `RECOTEM_HTTP_TIMEOUT_SECONDS` (default 30,
  clamped to [1, 600]).
- The destination host is resolved before each request (and on every
  redirect). If any address resolves to a private (RFC1918), loopback,
  link-local (`169.254.0.0/16`, AWS IMDSv1 / GCP metadata server),
  reserved, multicast, or unspecified address, the fetch is refused with
  `DataSourceError`. Operators with internal HTTP origins opt in via
  `RECOTEM_HTTP_ALLOW_PRIVATE=1` (`true` / `yes` / `on` also accepted).
  Production clusters leave it unset — the SSRF guard blocks a malicious
  recipe from reaching cloud-metadata services even when the operator
  has not curated the recipe directory.
- `recotem validate` issues a connectivity check for non-network schemes
  (`fs.exists()` via fsspec). For HTTP(S) sources the check performs DNS
  resolution and runs the SSRF guard (`assert_host_public`) — so a validate
  against an unreachable or private hostname fails at DNS, not at HTTP.
  No actual HTTP request is issued during validate; the sha256 integrity
  check happens at fetch time, not validate time.
- On sha256 mismatch the error message shows only the first 8 hex characters
  of each digest (`got 1a2b3c4d…, expected 5e6f7a8b…`) to avoid leaking the
  expected ground truth into shared logs.

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

`RECOTEM_MAX_DOWNLOAD_BYTES` applies to **all** source reads, not only
HTTP/HTTPS. For local files, `Path.stat().st_size` is checked before any
I/O; for object-store paths, `fsspec.info()["size"]` is checked. If the
reported size exceeds the cap, `DataSourceError` is raised before the
file is opened. Set `RECOTEM_MAX_DOWNLOAD_BYTES` large enough to accommodate
your training data, or leave it at the default 256 MiB if all sources are
reasonably sized.

Symlinks at `source.path` are followed implicitly (no resolution check;
the symlink-escape guard applies only to `output.path` under
`RECOTEM_ARTIFACT_ROOT`). If the underlying file is replaced between
`recotem validate` and `recotem train`, training simply re-reads the new
file at fetch time — there is no caching. Conversely, the running
`recotem serve` process never re-reads `source.path`; it only reads the
artifact, so source-file mutation has no effect on a deployed model
until the next train run.

## dtype overrides

By default, user and item ID columns are read as whatever type pandas infers. If your IDs look like integers (`1234`, `5678`) but you want them treated as strings, add explicit overrides:

```yaml
dtype:
  user_id: str
  item_id: str
```

This ensures consistent string-coercion between training and serving. Recotem string-coerces both columns internally after load, but setting `dtype: str` avoids pandas misparse of leading-zero IDs like `"0042"`.

`dtype` keys that do not match a column in the CSV are silently ignored by pandas — typos will not raise. Confirm dtypes by re-reading a few rows manually if the parse looks off.

## Errors and exit codes

| Error | Exit | Message pattern |
|-------|------|----------------|
| File not found | 3 | `DataSourceError: No such file or path: ./data/interactions.csv` |
| Column missing | 2 | `RecipeError: column 'user_id' not found` |
| Empty file (after header) | 3 | `DataSourceError: file has no data rows` |
| Parse error | 3 | `DataSourceError: ParserError: Error tokenizing data...` |
| Corrupt Parquet | 3 | `DataSourceError: ArrowInvalid: ...` |
| Rejected scheme | 2 | `RecipeError: path scheme 'http' is not allowed` |
| Embedded credentials | 2 | `RecipeError: 'source.path' contains embedded credentials in the URI. Use environment-based authentication instead.` |
| sha256 mismatch | 3 | `DataSourceError: sha256 mismatch: got <8 hex>…, expected <8 hex>…` |
| Download cap exceeded | 3 | `DataSourceError: Download size cap exceeded fetching <url>: > <bytes> bytes (RECOTEM_MAX_DOWNLOAD_BYTES).` |
| HTTP redirect to disallowed scheme | 3 | `DataSourceError: Refusing redirect from <url> to disallowed scheme '<scheme>://'` |
| HTTP redirect loop / over cap | 3 | `DataSourceError: Redirect loop detected …` / `Too many redirects (>5) …` |

## Encoding tips

If your CSV uses a non-UTF-8 encoding (common with data exported from Windows or Excel), set `encoding` explicitly:

```yaml
source:
  type: csv
  path: ./data/interactions.csv
  encoding: cp932       # Shift-JIS (Windows Japanese)
```

Accepted values are any encoding name recognised by Python's `codecs` module: `utf-8`, `utf-8-sig` (UTF-8 with BOM), `latin-1`, `cp932`, `iso-8859-1`, etc.
