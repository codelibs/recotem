# Tutorial-grade HTTPS source + getting-started guide

Design doc for adding an end-to-end tutorial that runs from `git clone` to a
live `/predict` call using a CSV fetched directly over HTTPS, plus the
data-source changes that make it viable.

- Status: draft
- Date: 2026-05-07
- Branch: `docs/recotem-2-design`

## 1. Problem

Recotem 2.0 has no tutorial that lets a new operator try the full pipeline
without first preparing their own dataset. `docs/quickstart.md` assumes the
reader already has a CSV on disk and is willing to install via `pip`. The
sample at `examples/csv-local/` references a bundled `interactions.csv.gz`
but is not wired into the docker-compose example, so even with that file
the operator must hand-author a recipe and copy files into a named volume.

The recipe loader also rejects the `http://` and `https://` path schemes
outright, so a tutorial that fetches a small public CSV via HTTPS is not
expressible at all.

## 2. Goals

- A new operator can run, in order, three commands and get a working
  `/predict` response. Reproducible, no manual data prep:

  1. `export RECOTEM_SIGNING_KEYS_SECRET=$(recotem keygen --type signing | grep ^plaintext | cut -d= -f2)`-equivalent setup
  2. `docker compose -f docker-compose.example.yaml run --rm train`
  3. `docker compose -f docker-compose.example.yaml up serve` + a `curl` to `/predict/{name}`

- `recotem` accepts `https://`, `http://`, and other fsspec-supported schemes
  for `source.path` and `item_metadata.path`, with safety controls scaled to
  the scheme's risk profile.

- The new tutorial doc replaces the existing `docs/quickstart.md` as the
  canonical "first 10 minutes" entry point. README links to it.

- `examples/csv-local/` is preserved as a separate "fully local" sample and
  is not coupled to the tutorial.

## 3. Non-goals

- Authenticated HTTPS fetch (`Authorization` header, mTLS). Authenticated
  sources continue to use the cloud-SDK path (`s3://`, `gs://`, `bigquery`).
- HTTPS redirect filtering by destination IP class (no RFC1918 / link-local
  block list). Operator owns the URL.
- Persistent on-disk caching of fetched CSVs. Operators who care use a CDN,
  caching proxy, or copy to object storage.
- Removing the `examples/csv-local/` sample.

## 4. Non-changes

- HMAC-signed artifact format, FQCN allow-list, signing-key handling.
- Recipe env-var expansion rules (allow-list still excludes `RECOTEM_SIGNING_KEY*`,
  `RECOTEM_API_KEYS`, `*_SECRET*`, `*_PASSWORD*`, `AWS_*`, `GOOGLE_*`, `GCP_*`).
- API authentication (`X-API-Key` + scrypt-hashed allow-list).
- Train and serve communicate only via signed artifact files.

## 5. Approach: replace path-scheme allow-list with direction-aware policy

### 5.1 Why the allow-list goes away

Today `src/recotem/recipe/loader.py` enforces:

```python
_ALLOWED_SCHEMES = frozenset({"s3", "gs", "az"})
_REJECTED_SCHEMES = frozenset({"file", "http", "https", "ftp", "ftps", "memory"})
```

Each rejection is examined:

| Scheme | Original justification | Holds up? |
|--------|------------------------|-----------|
| `file://` | redundant with bare local paths | No: identical attack surface to bare paths, no extra risk. |
| `http://` | plaintext / MITM | Partial: real risk on the public internet; legitimate use inside a trusted private network or `localhost`. Rejection is paternalistic. |
| `https://` | "out of scope" | No: standard distribution channel, fsspec-native, asymmetric with `s3://`/`gs://` (which themselves use HTTPS underneath). |
| `ftp://` / `ftps://` | legacy | Inconsistent with allowing `http://`. fsspec supports it. |
| `memory://` | useless in production | True but harmless. Not a security control. |

The allow-list as a whole was conservatism, not a defence. The actual security
boundary in Recotem 2.0 is:

1. The signing key (artifact integrity).
2. URL userinfo rejection (no embedded credentials).
3. Output containment via `RECOTEM_ARTIFACT_ROOT`.
4. Recipes are operator-authored and trusted; signing-key configuration is the
   trust boundary, not the recipe contents.

The allow-list as a security control adds nothing to (1)-(3) for **input**
paths and is dropped there. For **output** paths, however, several schemes
are rejected for a different reason: they are not writeable (HTTP/FTP have
no fsspec write implementation; `memory://` is process-local and useless
between train and serve). This is a usability / fail-fast control, not a
security control, but it lives in the same loader code so we describe it
together. See §5.3.

### 5.2 What stays

- URL userinfo rejection (`urlparse(path).username/password` non-empty → `RecipeError`).
- `RECOTEM_ARTIFACT_ROOT` containment for local `output.path`.
- Recipe `name` regex enforced before any path use.
- Env-expansion blacklist for credentials.

### 5.3 Input vs output: directionality matters

The allow-list is dropped for **input paths only** (`source.path`,
`item_metadata.path`). For **output paths** (`output.path`) we keep a
narrower rejection list, because writing to those schemes is not just
inadvisable — it is unsupported by fsspec:

- `http://`, `https://`, `ftp://`, `ftps://` → fsspec's HTTP / FTP
  filesystems do not implement write (and stdlib `urllib.request` is GET-only
  for our purposes); allowing these would cause `recotem train` to fail at
  the final write step. Reject at recipe load with a clear message.
- `memory://` → process-local; meaningless for inter-process artifact
  exchange between train and serve. Reject.
- `file://` → equivalent to bare local; allow (the existing local-path
  behaviour and `RECOTEM_ARTIFACT_ROOT` containment apply).

So the loader exposes:

```python
_OUTPUT_REJECTED_SCHEMES: frozenset[str] = frozenset(
    {"http", "https", "ftp", "ftps", "memory"}
)
```

`source.path` and `item_metadata.path` have no scheme rejection list.

### 5.4 What is added (input-side safety net)

For paths that involve an unauthenticated network fetch
(scheme ∈ {`http`, `https`}; "network schemes" hereafter), two controls are
added:

- A new **mandatory** `sha256` field on the source / metadata config when
  the path uses a network scheme. Recipe load fails with `RecipeError` if
  `sha256` is missing for such a path. This guarantees reproducibility and
  detects content tampering by any party in the network path.
- A new **byte cap** `RECOTEM_MAX_DOWNLOAD_BYTES` (default `268435456` = 256 MiB)
  enforced during the fetch. Exceeding the cap raises `DataSourceError`
  before the bytes are handed to pandas / the deserializer.

For non-network schemes (bare local, `file://`, `s3://`, `gs://`, `az://`,
`memory://`, BigQuery, plus `ftp://` / `ftps://` if the operator installs the
necessary fsspec extras themselves), `sha256` is **optional** — when set, it
is verified; when unset, no integrity check is performed. The byte cap does
not apply to these schemes, since they have their own size constraints
(filesystem quotas, object-store limits, query-result limits).

Scoping `_NETWORK_SCHEMES` to `{http, https}` (as opposed to also including
`ftp` / `ftps`) is deliberate:

- `urllib.request` (stdlib) covers HTTP and HTTPS without new runtime deps.
  The same module powers the existing docker-compose healthcheck, keeping
  the slim image curl-free. fsspec's HTTP filesystem would require pulling
  in `aiohttp` (~several MiB), which we avoid here.
- FTP / FTPS would require either custom code or a new fsspec extra
  (`aioftp`). Neither is needed for the tutorial. Operators with FTP
  sources can already use any scheme via fsspec; we just don't apply the
  sha256-required rule to it in v1 of this feature. (If FTP becomes a
  common request, future work can add it to `_NETWORK_SCHEMES`.)

## 6. Detailed changes

### 6.1 `src/recotem/recipe/loader.py`

Remove `_ALLOWED_SCHEMES` and `_REJECTED_SCHEMES`. Replace with a narrower
output-only rejection list and a network-scheme helper:

```python
_OUTPUT_REJECTED_SCHEMES: frozenset[str] = frozenset(
    {"http", "https", "ftp", "ftps", "memory"}
)
_NETWORK_SCHEMES: frozenset[str] = frozenset({"http", "https"})

def _network_scheme(path: str) -> bool:
    return urlparse(path).scheme.lower() in _NETWORK_SCHEMES
```

`_validate_path` is split into `_validate_input_path` (userinfo check only)
and `_validate_output_path` (userinfo + output-rejection list):

```python
def _validate_input_path(path: str, field_name: str) -> None:
    parsed = urlparse(path)
    if parsed.username or parsed.password:
        raise RecipeError(
            f"'{field_name}' contains embedded credentials in the URI. "
            "Use environment-based authentication instead."
        )

def _validate_output_path(path: str, field_name: str) -> None:
    parsed = urlparse(path)
    if parsed.username or parsed.password:
        raise RecipeError(
            f"'{field_name}' contains embedded credentials in the URI."
        )
    scheme = (parsed.scheme or "").lower()
    if scheme in _OUTPUT_REJECTED_SCHEMES:
        raise RecipeError(
            f"'{field_name}' uses scheme '{scheme}://' which does not support "
            "writes. Use a bare local path, s3://, gs://, az://, or file://."
        )
```

`_validate_path_fields` is updated to call the input variant for
`source.path` and `item_metadata.path`, and the output variant for
`output.path`.

After Recipe validation, add a post-validator that walks `source` and
`item_metadata` and raises `RecipeError` if either uses a network scheme but
omits `sha256`. This is enforced in the loader, not the pydantic model,
because (a) the rule is cross-field and (b) the source config is built via the
dynamic discriminated union and per-source pydantic validators would need to
duplicate the rule.

### 6.2 `src/recotem/datasource/csv.py`

Both `CSVConfig` and `ParquetConfig` gain an optional `sha256: str | None = None`
field, validated against `^[0-9a-f]{64}$` when set.

`CSVSource.fetch()` and `ParquetSource.fetch()` are restructured to dispatch
on scheme:

- **HTTP / HTTPS** (network schemes): use stdlib `urllib.request.urlopen`
  with an explicit `User-Agent: recotem/<version>` header and a connection
  timeout (default 30 s; configurable via `RECOTEM_HTTP_TIMEOUT_SECONDS`).
  Read the response in chunks of 1 MiB into a `BytesIO`, accumulating into
  a `hashlib.sha256()` and tracking byte count. If `byte_count >
  RECOTEM_MAX_DOWNLOAD_BYTES`, abort with `DataSourceError`. After the read
  completes, if `sha256` is set, compare via `hmac.compare_digest`; mismatch
  → `DataSourceError`. Then hand `BytesIO` to pandas (compression is
  detected from the URL path's extension and passed as `compression=…`,
  since `BytesIO` has no name; mapping: `.gz` → `gzip`, `.bz2` → `bz2`,
  `.zip` → `zip`, `.xz` → `xz`, else `None`). Redirects: `urllib` follows
  HTTP redirects up to its default of 30; we cap at 5 to keep it simple
  and emit a `csv_source_redirect` log per hop. TLS verification is enabled
  (default `urllib` SSL context); plaintext HTTP is allowed and is the
  operator's responsibility.

- **All other schemes** (bare local, `file://`, `s3://`, `gs://`, `az://`,
  `memory://`, etc.): unchanged path through `fsspec.open(path, "rb")` →
  pandas. If `sha256` is set on the config, the bytes are pre-read into a
  `BytesIO` (chunked) for verification before pandas parses; if not set,
  pandas reads via fsspec directly (the existing fast path). The byte cap
  does not apply.

The byte-cap and sha256 checks happen **before** pandas parses, so a 4 GB
file with the wrong magic bytes is still bounded at the cap on the network
path.

Memory: the entire fetched payload is held in memory until pandas finishes
parsing. With `RECOTEM_MAX_DOWNLOAD_BYTES` defaulting to 256 MiB, peak memory
during fetch is bounded at roughly that value plus pandas' own working set.
Operators with multi-GiB CSVs should use object-store schemes (`s3://`,
`gs://`) instead of HTTP/HTTPS — those bypass the byte cap and stream
directly into pandas via fsspec.

`probe()` continues to use `fs.exists()` (or HEAD via fsspec for HTTP/HTTPS).
For network schemes, `probe()` does not download the body and does not verify
sha256 — that is deferred to fetch time.

### 6.3 `src/recotem/recipe/models.py`

Add `sha256` to `ItemMetadataConfig`:

```python
class ItemMetadataConfig(BaseModel, extra="forbid"):
    type: str = Field(pattern=r"^(csv|parquet)$")
    path: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    fields: list[str] = Field(min_length=1)
    on_field_missing: str = Field(default="error", pattern=r"^(error|null)$")
```

The same field name on the source configs lets the loader's post-validator
treat them uniformly.

### 6.4 `src/recotem/config.py`

Add readers for two new env vars:

- `RECOTEM_MAX_DOWNLOAD_BYTES`
  - Default: `268_435_456` (256 MiB)
  - Clamping: minimum 1 MiB; maximum 16 GiB (sanity bounds; not a security
    invariant — operator can override by editing the env var, but absurd values
    like `0` or negatives are clamped).
- `RECOTEM_HTTP_TIMEOUT_SECONDS`
  - Default: `30`
  - Clamping: minimum 1, maximum 600.
  - Applied to both connect and read operations on HTTP/HTTPS fetch.

### 6.5 Logging

Existing `csv_source_fetch_start` / `csv_source_fetch_done` events gain:

- `bytes` — actual byte count read
- `sha256_verified` — true when a `sha256` was configured and matched
- `path` — present, but URL userinfo is stripped at the call site (since the
  existing structlog redactor is *key-name* based and `path` is not a
  redacted key, the value would otherwise leak credentials). A small helper
  `_redact_url_userinfo(path: str) -> str` lives in `csv.py` and is applied
  before any log call. Although recipe load already rejects userinfo, this
  is defence in depth for any path field that bypasses the loader (e.g.,
  ad-hoc CLI invocations).

A new event `csv_source_size_exceeded` is emitted just before raising the
size-cap `DataSourceError`, carrying `bytes_read`, `cap`, `recipe`, `run_id`.

### 6.6 `src/recotem/datasource/base.py`

No protocol changes. `_probe_fsspec_path` is unchanged but is exercised against
HTTP/HTTPS in tests; the fsspec http filesystem responds to `exists()` via HEAD.

Side-effects of `recotem validate` for network sources:

- `validate` runs `probe()` which calls `fs.exists()`, which for HTTP/HTTPS
  issues a single HEAD (or GET-with-Range) request to the upstream. This is
  a network side-effect during recipe validation. The doc (`docs/data-sources/csv.md`)
  notes this, since on-air-gapped CI runs operators may want to skip
  `validate` or set up a local mirror.
- `validate` does **not** download the body and does **not** verify
  `sha256`. Both are deferred to `train` time.

### 6.7 `recotem schema`

The Typer subcommand `recotem schema` emits the JSON Schema for the Recipe
model. Adding `sha256` to the source / item_metadata configs causes the
schema output to include the new field. Downstream IDE schema validators
pick it up automatically. No CLI flag changes.

## 7. Tutorial assets

### 7.1 `examples/tutorial-purchase-log/recipe.yaml`

```yaml
# Tutorial recipe — fetches a small public CSV over HTTPS and trains.
# Step-by-step instructions: docs/getting-started.md

name: purchase_log

source:
  type: csv
  path: https://raw.githubusercontent.com/codelibs/recotem/refs/tags/v1.0.0/frontend/e2e/test_data/purchase_log.csv
  # Required for network-scheme paths. Computed once at tutorial-authoring time;
  # if upstream rotates the file, update both this value and getting-started.md.
  sha256: 945fc769205a5976d38c5783500ae473afbb04608043b703951a699993c8f8be
  dtype:
    user_id: str
    item_id: str

schema:
  user_column: user_id
  item_column: item_id
  # No time_column on this dataset → split.scheme must be `random`.

cleansing:
  drop_null_ids: true
  dedup: keep_last
  min_rows: 100
  min_users: 10
  min_items: 10

training:
  algorithms: [IALS, TopPop]
  metric: ndcg
  cutoff: 10
  n_trials: 10
  parallelism: 1
  split:
    scheme: random
    heldout_ratio: 0.2
    seed: 42

output:
  path: ./artifacts/purchase_log.recotem
  versioning: append_sha
```

Sized for the dataset (≈4 988 interactions, ≈37 KiB):

- `n_trials: 10` keeps the train-time well under a minute on a laptop.
- `min_rows: 100` matches the dataset's order of magnitude.
- `cleansing.min_users / min_items: 10` blocks degenerate runs without
  rejecting this dataset.
- `IALS, TopPop` covers a "real" matrix-factorization model and a baseline.
- `output.path` is **CWD-relative** (`./artifacts/...`) so the same recipe
  works for both Docker (CWD = `/workspace` by container `working_dir`) and
  pip (`mkdir artifacts && recotem train …` from any directory). The
  docker-compose example bind-mounts the `artifacts` volume at
  `/workspace/artifacts` (was `/artifacts`) so this resolves correctly.

### 7.2 `examples/tutorial-purchase-log/README.md`

A 10-line orientation file, pointing to `docs/getting-started.md` for the full
walkthrough. No duplicated instructions; this file just establishes what the
recipe is and lists the sha256 / source URL.

### 7.3 `docker-compose.example.yaml` (update, not replace)

Replace the named-volume `recipes` with a bind mount of
`./examples/tutorial-purchase-log` to `/recipes` (read-only). Replace the
`train` command argument with `train /recipes/recipe.yaml`. The `artifacts`
named volume stays as-is, but its mount point moves from `/artifacts` to
`/workspace/artifacts` (matching the recipe's CWD-relative `./artifacts/...`
output path and the existing `working_dir: /workspace`). Both `train` and
`serve` services mount it at the same path so hot-swap continues to work.

Header comment is rewritten to be a runnable three-step tutorial:

1. `recotem keygen --type signing --kid dev` (and api equivalent)
2. `RECOTEM_SIGNING_KEYS_SECRET=… RECOTEM_API_KEYS_SECRET=… docker compose -f docker-compose.example.yaml run --rm train`
3. `docker compose -f docker-compose.example.yaml up serve` + `curl … /predict/purchase_log`

The compose file remains explicitly an example, not for production.

## 8. Documentation

### 8.1 New: `docs/getting-started.md`

Replaces `docs/quickstart.md`. Single canonical entry point for new operators.
Structure:

1. **Prerequisites** — Docker (or Python 3.12+ for the local path), nothing else.
2. **Path A: Docker Compose** (primary)
   - Generate keys (`recotem keygen` via the docker image: `docker run --rm ghcr.io/codelibs/recotem:latest keygen --type signing --kid dev`)
   - Export `RECOTEM_SIGNING_KEYS_SECRET` and `RECOTEM_API_KEYS_SECRET`
   - Run `train`, then `up serve`, then `curl /predict/purchase_log`
   - Expected output excerpt
3. **Path B: pip install** (alternative)
   - `pip install recotem`
   - Same recipe, but `output.path: ./artifacts/purchase_log.recotem`
   - `recotem train`, `recotem serve`, `curl`
4. **What's next** — link to recipe-reference, deployment/docker, security.

Each step includes a "what just happened" sentence so the user understands the
artifact / signing / hot-swap mechanics by the time they reach `/predict`.

### 8.2 Updated: `README.md`

- "Further reading": `docs/quickstart.md` link → `docs/getting-started.md`.
- "Hello world (CSV)" section is preserved as a self-contained README quickstart;
  it gains a one-line "for the full Docker-Compose walkthrough, see
  docs/getting-started.md".

### 8.3 Updated: `docs/data-sources/csv.md`

- Add an "Network schemes" subsection covering `http://`, `https://`, `ftp://`,
  `ftps://`. Document `sha256` as **required** for these schemes and recommended
  for any externally-distributed file.
- Add `sha256` to the field table.
- Add `RECOTEM_MAX_DOWNLOAD_BYTES` to the env-var reference paragraph.
- Replace the "Rejected schemes" paragraph with a short "Scheme support" note
  pointing at fsspec.

### 8.4 Updated: `docs/security.md`

- "Path traversal via recipe" row: keep the `name` regex / artifact-root content;
  drop the implicit "scheme allow-list" claim.
- New row in the threat table: **Tampered or rotated network-fetched data** →
  mitigation: mandatory `sha256` for network schemes; failed match raises
  `DataSourceError` before bytes reach the parser.
- New row: **Resource exhaustion via giant network fetch** → mitigation:
  `RECOTEM_MAX_DOWNLOAD_BYTES`.
- New short subsection: **Operator responsibilities for network sources**:
  - Operator chooses the URL; recipe is in the trusted boundary.
  - Recipes that point at metadata services (`http://169.254.169.254/...`) or
    other privileged endpoints are operator misuse, not a Recotem flaw.
  - HTTP (plaintext) is appropriate only inside a trusted network.

### 8.5 Updated: `docs/recipe-reference.md`

- Document the new `source.sha256` and `item_metadata.sha256` fields.
- Document that `sha256` is required when `path` uses a network scheme.

### 8.6 Updated: `CLAUDE.md`

The "Recipe model" section's "Path scheme allow-list" bullet is rewritten:

> Path scheme: any fsspec-supported scheme is accepted. For network schemes
> (`http://`, `https://`, `ftp://`, `ftps://`) a `sha256` integrity pin is
> required, and `RECOTEM_MAX_DOWNLOAD_BYTES` (default 256 MiB) caps the
> downloaded payload. Embedded URI credentials are rejected.

## 9. Testing

### 9.1 Unit

- `tests/unit/test_recipe_loader.py`:
  - Replace existing "rejected scheme" tests with the input/output split:
    - **Input** `source.path` with HTTP/HTTPS/FTP/FTPS + valid `sha256` → loads cleanly.
    - **Input** `source.path` with HTTP/HTTPS/FTP/FTPS + missing `sha256` → `RecipeError` mentioning the field name.
    - **Input** `source.path` with `file://` or bare local → loads cleanly; sha256 optional.
    - **Output** `output.path` with HTTP/HTTPS/FTP/FTPS/`memory://` → `RecipeError` (write not supported).
    - **Output** `output.path` with `file://`, bare local, `s3://` → loads cleanly.
    - Embedded userinfo (`https://user:pass@host/...`) on either input or output → `RecipeError`.
    - Existing line-201 test ("output rejected http") is preserved in spirit by the output-rejection list; assertion message text is updated.
- `tests/unit/test_csv_source.py`:
  - sha256 match → fetch succeeds.
  - sha256 mismatch → `DataSourceError` with the actual + expected hashes
    redacted to first 8 chars of each.
  - Byte cap exceeded (mock fsspec stream returning > cap bytes) →
    `DataSourceError` and `csv_source_size_exceeded` log event.
- `tests/unit/test_recipe_models.py`:
  - `sha256` on item_metadata accepted in valid form.
  - Invalid hex / wrong length → pydantic ValidationError.

### 9.2 Integration

- `tests/integration/test_https_csv_source.py` (new):
  - Uses `pytest_httpserver` (added to dev deps) to serve a small CSV over
    HTTP (the same urllib code path serves HTTP and HTTPS — TLS handshake is
    exercised separately in the optional networked e2e mode).
  - Exercises `recotem train` end-to-end against an `http://127.0.0.1:<port>`
    URL: recipe load → fetch → byte-cap check → sha256 verify → train → sign.
  - sha256-mismatch case: server returns mutated content; train exits 3.
  - Byte-cap-exceeded case: server returns oversized content with the env
    var set low (e.g. 1 KiB); fetch aborts mid-stream.
  - Redirect-loop / >5 redirects case: train exits 3 with redirect-cap log.

### 9.3 e2e

- `tests/e2e/` shell script gains a `--tutorial` mode that runs against the
  in-tree `examples/tutorial-purchase-log/recipe.yaml`. By default the script
  only runs in CI when `RECOTEM_E2E_NETWORK=1` is set, so the offline
  developer flow stays offline.

### 9.4 Fuzz

- `tests/fuzz/test_recipe_loader_fuzz.py`: existing strategies cover arbitrary
  path strings; nothing to add. Verify that randomly-generated `https://...`
  inputs without `sha256` fail with `RecipeError` (not a stack trace).

## 10. CI

- `.github/workflows/test.yml`:
  - The new integration test runs offline (httpserver fixture), so no CI
    workflow change is required to keep CI green.
  - The "secrets in logs" grep step covers any new logging.
- No new workflow files.

## 11. File-by-file summary

| File | Change |
|------|--------|
| `src/recotem/recipe/loader.py` | drop input allow-list; keep narrower output-only rejection list; userinfo check on both directions; add `sha256`-required-for-network post-validator |
| `src/recotem/recipe/models.py` | add `sha256` to `ItemMetadataConfig` |
| `src/recotem/datasource/csv.py` | add `sha256` to CSV/Parquet configs; chunked fetch with byte-cap and sha256 verify; `_redact_url_userinfo` helper for log paths |
| `src/recotem/datasource/base.py` | (verify only) — `_probe_fsspec_path` works with HTTP fsspec |
| `src/recotem/config.py` | read `RECOTEM_MAX_DOWNLOAD_BYTES`, `RECOTEM_HTTP_TIMEOUT_SECONDS` |
| `examples/tutorial-purchase-log/recipe.yaml` | new tutorial recipe |
| `examples/tutorial-purchase-log/README.md` | new orientation file |
| `examples/csv-local/` | unchanged |
| `docker-compose.example.yaml` | bind-mount tutorial recipe; volume mount moves to `/workspace/artifacts`; rewritten header |
| `docs/getting-started.md` | new (replaces quickstart.md) |
| `docs/quickstart.md` | deleted |
| `docs/README.md` | replace "Quickstart" link with "Getting started" pointing to `getting-started.md` |
| `docs/data-sources/csv.md` | document network schemes, `sha256`, `RECOTEM_MAX_DOWNLOAD_BYTES`; rewrite "Rejected schemes" paragraph |
| `docs/security.md` | drop allow-list claim; add network-fetch threats and mitigations; document operator responsibility for network sources |
| `docs/recipe-reference.md` | document `sha256` and the network-scheme requirement; rewrite the "Rejected schemes" paragraph (lines 213-216) into the new input/output policy |
| `docs/deployment/docker.md` | confirm/update any reference to the docker-compose example so volume-mount path change is consistent |
| `README.md` | re-link to `getting-started.md`; add cross-reference from "Hello world" |
| `CLAUDE.md` | rewrite path-scheme bullet (lines 102-104); update directory listing line 54 (`quickstart.md` → `getting-started.md`); update example command line 83 (use tutorial example); update reference docs section lines 181-182 (drop deleted spec link or repoint to new spec; switch quickstart link to getting-started); add `tutorial-purchase-log` to examples list line 64; add `RECOTEM_MAX_DOWNLOAD_BYTES` and `RECOTEM_HTTP_TIMEOUT_SECONDS` rows to env-var table |
| `tests/unit/test_recipe_loader.py` | rewrite scheme tests for input/output split; existing line 201 test (`output_path="http://example.com/..."`) becomes "output rejects http" assertion under the new policy; add new "input accepts http with sha256" assertions |
| `tests/unit/test_csv_source.py` | add sha256 / byte-cap tests |
| `tests/unit/test_recipe_models.py` | add `item_metadata.sha256` test |
| `tests/integration/test_https_csv_source.py` | new |
| `tests/e2e/` | tutorial mode added |
| `pyproject.toml` (dev deps) | add `pytest-httpserver` for integration tests |

## 12. Migration / compatibility

This is a `0.x` change to a not-yet-released spec; no migration path is owed
to existing users. The visible behaviours are:

- Recipes that previously failed with "scheme http(s)/file/... not allowed"
  will now load. If they relied on the rejection as configuration validation,
  they will fail later (sha256 missing → RecipeError; or fetch error if URL
  is unreachable).
- Recipes using `s3://` / `gs://` / `az://` / bare local paths are unaffected.

## 13. Risks

1. **Operator misconfigures HTTP and exposes plaintext fetch on the public internet.**
   Mitigation: `docs/security.md` explicit guidance; `sha256` mandatory blocks
   silent tampering; running `recotem validate` surfaces unreachable URLs.

2. **CSV upstream rotates without sha256 update → train exits 3.**
   This is intended behaviour. The threat we are explicitly defending against.
   Tutorial doc tells the operator how to refresh both values together.

3. **Byte cap default (256 MiB) is too small for a real workload.**
   Operator override via `RECOTEM_MAX_DOWNLOAD_BYTES`. Default sized for
   "tutorial-shaped" workloads; production users typically use object-store
   schemes which are uncapped.

4. **Inconsistent sha256 enforcement between source and item_metadata.**
   Mitigation: same field name on both configs; same post-validator covers both.

## 14. Subagent allocation (implementation)

| Phase | Subagent |
|-------|----------|
| Loader / models / config refactor | `marevol:backend-engineer` |
| CSV / Parquet fetch refactor | `marevol:backend-engineer` |
| Tutorial recipe + compose update | `marevol:devops-engineer` |
| Doc updates (`getting-started.md`, `csv.md`, `security.md`, etc.) | `marevol:tech-writer` |
| Test additions (unit / integration / fuzz / e2e) | `marevol:test-engineer` |
| Security review of allow-list removal + sha256 enforcement | `marevol:security-engineer` |
| Final review gate | `marevol:code-reviewer` then `codex-review` |

## 15. Open questions

None at draft commit. If implementation surfaces any, they are tracked as
follow-up tasks during the implementation plan.
