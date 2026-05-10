# Security

## Trust boundaries

```
                        ┌───────────────────────────────────────────┐
  Operator              │  RECOTEM_SIGNING_KEYS  RECOTEM_API_KEYS   │
  (trusted)             │  env vars, secrets manager                 │
                        └──────────────┬────────────────────────────┘
                                       │ configure
                        ┌──────────────▼────────────────────────────┐
                        │          recotem serve                     │
                        │  binds to RECOTEM_HOST:RECOTEM_PORT        │
  API clients           │                                            │
  (authenticated) ─────►│  POST /predict/{name}  X-API-Key header   │
                        │  GET  /health                              │
                        └──────────────┬────────────────────────────┘
                                       │ reads (signed)
                        ┌──────────────▼────────────────────────────┐
                        │         artifact files                     │
                        │  ./artifacts/*.recotem                     │
                        │  s3:// / gs:// / az://                     │
                        └──────────────┬────────────────────────────┘
                                       │ writes (signed)
                        ┌──────────────▼────────────────────────────┐
  Scheduler             │          recotem train                     │
  (trusted)             │  batch process; no inbound network         │
                        └───────────────────────────────────────────┘
```

The internet-facing boundary is `recotem serve`. `recotem train` has no inbound network surface.

## Threat model summary

| Threat | Mitigation |
|--------|-----------|
| Malicious artifact file (serialization RCE) | HMAC-SHA256 verify before any deserialization; signing key required; no legacy unsigned fallback |
| HMAC bypass leading to arbitrary class construction | Hand-enumerated FQCN allow-list as backstop (see below) |
| Artifact-size DoS | `RECOTEM_MAX_ARTIFACT_BYTES` cap (default 2 GiB); header length cap (64 KiB); both enforced before deserialization |
| Stat-then-read TOCTOU on artifact | Read-once protocol: bytes read into memory once, sha256 computed, then HMAC-verified from the same buffer |
| Key material in logs | structlog redaction processor runs first in chain; unit test asserts no key material at any log level |
| API key brute-force / timing attack | `hmac.compare_digest` constant-time compare; no logging of plaintext or hash |
| Credential injection via recipe env expansion | `RECOTEM_SIGNING_KEY*`, `RECOTEM_API_KEYS`, `*_SECRET*`, `*_PASSWORD*`, `AWS_*`, `GOOGLE_*`, `GCP_*` are blacklisted from `${...}` expansion |
| SQL injection via recipe | Env expansion never performed inside `source.query`; dynamic values must use `@param` BigQuery placeholders |
| Path traversal via recipe | `name` validated with `^[A-Za-z0-9_-]{1,64}$` at load and before every filesystem use; artifact root confinement via `RECOTEM_ARTIFACT_ROOT` |
| Tampered or rotated network-fetched data | `sha256` integrity pin is **mandatory** on `source.path` / `item_metadata.path` when the scheme is `http://` or `https://`; mismatch raises `DataSourceError` (exit 3) before the bytes reach the parser |
| Resource exhaustion via giant network fetch | `RECOTEM_MAX_DOWNLOAD_BYTES` (default 256 MiB) caps the in-memory body during HTTP/HTTPS fetch; cap exceeded → `DataSourceError` mid-stream |
| Plaintext HTTP source on the public internet | Operator policy. `http://` is allowed (legitimate inside trusted networks) but operators MUST avoid plaintext on the public internet; sha256 mitigates content tampering for any reachable response |
| Unrecognised plugin loading arbitrary code | Conflicting plugin `type_name` fails startup; installed plugins are treated as trusted code (pin versions) |
| Unauthenticated external access | Default bind `127.0.0.1`; `--insecure-no-auth` gated by `RECOTEM_ENV=development`; `TrustedHostMiddleware` blocks unrecognized hosts |

## Network-source fetch behaviour

`recotem train` fetches `http://` and `https://` source paths via stdlib
`urllib`. The fetch path enforces:

- **Redirect cap**: at most 5 redirects (urllib's default 30 is overridden);
  visited-URL set detects redirect loops; redirects to non-`http`/`https`
  schemes are refused (e.g. `file://`, `gopher://`).
- **Cert validation**: stdlib `urllib` default — system trust store, no opt-out.
- **No proxy auto-discovery override**: respects `HTTP(S)_PROXY` env vars but
  does not use any other auto-detection.
- **User-Agent header**: set to a fixed Recotem string so origin servers can
  identify the client.
- **URL userinfo redaction**: any `https://user:pass@host/...` form is logged
  as `https://[REDACTED]@host/...` in `csv_source_*` events. The recipe
  loader rejects userinfo-bearing URLs at parse time anyway.
- **Body cap**: streamed read, refuses past `RECOTEM_MAX_DOWNLOAD_BYTES` mid-stream.
- **Timeout**: `RECOTEM_HTTP_TIMEOUT_SECONDS` per request (clamped 1–600).
- **sha256 mandatory**: refused at recipe-load time when the scheme is
  network and `sha256` is unset; verified post-fetch via `hmac.compare_digest`.

## Operator responsibilities for network sources

Recipes are operator-authored and live inside the Recotem trust boundary.
That means choices about which URLs to point at — and whether `http://`
URLs are safe to use — are operator decisions, not Recotem decisions.

Specific operator responsibilities:

- **Choose `https://` over `http://` on the public internet.** TLS prevents
  a network attacker from swapping bytes; `sha256` detects the swap, but
  TLS prevents it from happening in the first place.
- **Metadata services and private networks are blocked by default.**
  `recotem train` resolves the host of every HTTP/HTTPS source URL and
  refuses to connect when it lands on a private (RFC1918), loopback,
  link-local (`169.254.0.0/16` covers AWS IMDSv1 and GCP
  `metadata.google.internal`), reserved, multicast, or unspecified
  address. The check re-runs on every redirect so a CNAME-pointed-inwards
  trick is also refused. Operators with a legitimate internal HTTP
  origin (lab CI mirror, intranet artifact server) opt in by setting
  `RECOTEM_HTTP_ALLOW_PRIVATE=1`. Production deployments leave it unset
  so a malicious recipe cannot hit cloud-metadata services or sibling
  pods even if the operator forgot to scrub the recipe directory.
- **DNS rebinding is mitigated by IP pinning.** Without further care,
  the SSRF guard's `getaddrinfo()` and the `urllib` connect-time
  `getaddrinfo()` are independent lookups: an attacker who controls the
  authoritative DNS for a hostname can return a public IP to the first
  call (passing the SSRF check) and a private IP to the second (the
  actual TCP connect), bypassing the guard entirely. Recotem closes this
  window by feeding the IP resolved at SSRF-check time straight into a
  custom `HTTPConnection` / `HTTPSConnection` whose `connect()` method
  opens the socket against the pinned IP. The original hostname is
  preserved for the `Host:` header and (for HTTPS) for SNI plus
  certificate validation, so legitimate traffic is unaffected. Pinning
  is per-request and re-applies on every redirect hop. As a backstop in
  hostile networks, operators should also restrict outbound DNS at the
  network layer (egress firewall / VPC endpoints) so that even a
  partially-compromised resolver cannot return an attacker-controlled
  IP to either lookup.
- **IPv4-mapped IPv6 inputs are explicitly unwrapped.** Some Python
  releases classify `::ffff:169.254.169.254` as `is_link_local=False`
  because they only consult IPv6-layer attributes. The SSRF guard
  therefore additionally re-evaluates `is_private` / `is_loopback` /
  `is_link_local` on the embedded IPv4 address (when present), so
  `::ffff:127.0.0.1`, `::ffff:169.254.169.254` and any `::ffff:rfc1918`
  literal are refused regardless of stdlib semantics.
- **Compute and pin sha256 once, then alert on changes.** A mismatch is
  the signal. Don't bypass it by silently regenerating during CI.

## Artifact payload and the FQCN allow-list

irspack's `IDMappedRecommender` depends on scipy sparse matrices and numpy arrays. These cannot be expressed in JSON without losing structure. The native irspack binary serialization format is required, and it is unavoidable.

### Primary gate: HMAC-before-deserialize

**HMAC-SHA256 verification is the primary security control.** The byte sequence is verified against `RECOTEM_SIGNING_KEYS` before a single byte reaches the deserializer. A valid HMAC means the artifact was produced by a process that held the signing key — an attacker without the key cannot construct a payload that passes verification. All four controls below are applied in order; steps 3 and 4 are defence-in-depth and do not substitute for HMAC.

The four layered controls:

1. Magic bytes, format version, and size checks before any deserialization.
2. **HMAC-SHA256 signature verification** with multi-kid support and constant-time compare; signing keys are never logged (only the kid is surfaced). No legacy unsigned fallback — a misconfigured or missing `RECOTEM_SIGNING_KEYS` fails closed.
3. Hand-enumerated FQCN allow-list plus a narrow module-prefix allow-list (defence-in-depth, not the primary gate — see below).
4. Signing key required for both train and serve with no env-default.

### Defence-in-depth: FQCN allow-list

The FQCN allow-list in `SafeUnpickler.find_class` is a secondary layer that operates independently of HMAC. Its purpose is to bound the blast radius if HMAC is ever bypassed (e.g. a signing-key compromise that has not yet been rotated, or a future HMAC vulnerability). It does **not** guarantee safety by itself: a sufficiently broad allow-list still exposes whatever API surface the permitted libraries expose.

The allow-list is frozen per irspack 0.4.x. If irspack adds or renames recommender classes, the list and the CHANGELOG entry are updated together.

The FQCN allow-list permits only these classes. Any other class outside both this list and the module-prefix allow-list triggers `ArtifactError` before construction:

```
recotem._idmap.IDMappedRecommender
irspack.utils.id_mapping.IDMapper
irspack.recommenders.ials.IALSRecommender
irspack.recommenders.knn.CosineKNNRecommender
irspack.recommenders.toppop.TopPopRecommender
irspack.recommenders.rp3.RP3betaRecommender
irspack.recommenders.dense_slim.DenseSLIMRecommender
irspack.recommenders.truncsvd.TruncatedSVDRecommender
irspack.recommenders.bpr.BPRFMRecommender
numpy.ndarray
numpy.dtype
numpy.core.multiarray._reconstruct
numpy.core.multiarray.scalar
numpy._core.multiarray._reconstruct
numpy._core.multiarray.scalar
scipy.sparse._csr.csr_matrix
scipy.sparse._csc.csc_matrix
scipy.sparse._coo.coo_matrix
builtins.int
builtins.float
builtins.bool
builtins.list
builtins.tuple
builtins.dict
builtins.str
builtins.bytes
builtins.complex
builtins.set
builtins.frozenset
collections.OrderedDict
```

This list is frozen per Recotem release. Changes ship with a CHANGELOG entry.

In addition to the FQCN list, classes whose defining module sits under
one of the following narrow prefixes are permitted via the prefix
allow-list (numpy and scipy reorganise their internal layout between
releases — reconstruction helpers like `_reconstruct` move between
submodules):

```
numpy._core.       numpy 2.x reconstruction helpers + scalar / dtype machinery
numpy.core.        numpy 1.x equivalents (forward-compat with pre-2.x artifacts)
numpy.dtypes.      numpy 2.x parametric dtype classes (Float64DType, BoolDType, …)
scipy.sparse._csr. CSR matrix reconstructor + helpers
scipy.sparse._csc. CSC equivalent
scipy.sparse._coo. COO equivalent
```

The bare top-level modules (`numpy`, `scipy.sparse`) are intentionally
**not** on the prefix list. The legitimate top-level FQCNs
(`numpy.ndarray`, `numpy.dtype`) are pinned by the hand-enumerated list
instead, so callable / file-IO gadgets such as `numpy.frompyfunc`,
`numpy.vectorize`, `numpy.piecewise`, and `scipy.sparse.load_npz` are
blocked even though they live "under" the same package.

Other numpy submodules that previously fell under an allowed prefix and
were deny-listed are now simply outside the allow-list (they are not
on the prefix list at all): `numpy.testing`, `numpy.distutils`,
`numpy.f2py`, `numpy.ctypeslib`, `numpy.lib`, `numpy.compat`,
`numpy.random`, `numpy.linalg`, `numpy.fft`, `numpy.polynomial`,
`scipy.sparse.linalg`, `scipy.sparse.tests`, `scipy.sparse.csgraph`,
`scipy.sparse._compressed`, `scipy.sparse._data_matrix`. The deny-list
is retained as a defence-in-depth trip-wire in case a future patch
broadens the prefix allow-list.

HMAC verification remains the primary defence; the prefix allow-list is
the secondary layer scoped to the scientific stack only.

`recotem inspect <artifact>` runs the full HMAC verify path and prints the header JSON without invoking the deserializer. It is safe to run on untrusted artifacts.

## IAM scopes for BigQuery

Recommended minimum IAM for the service account used by `recotem train`:

| Role | Scope |
|------|-------|
| `roles/bigquery.jobUser` | Project |
| `roles/bigquery.dataViewer` | Dataset(s) queried |
| `roles/bigquery.readSessionUser` | Project (Storage Read API) |

Do not grant `roles/bigquery.admin` or `roles/bigquery.dataEditor`. Recotem only reads.

For GCS artifact storage:

| Role | Scope |
|------|-------|
| `roles/storage.objectCreator` | Artifact bucket (train service account only) |
| `roles/storage.objectViewer` | Artifact bucket (serve service account only) |

For S3:

```json
{
  "Effect": "Allow",
  "Action": ["s3:PutObject", "s3:GetObject", "s3:HeadObject"],
  "Resource": "arn:aws:s3:::my-bucket/artifacts/*"
}
```

Grant `s3:PutObject` only to the train role, not the serve role.

## Secrets handling

**What must be kept secret:**

- `RECOTEM_SIGNING_KEYS` — HMAC keys for artifact signing and verification.
- `RECOTEM_API_KEYS` — contains scrypt digests of API key plaintexts (`hashlib.scrypt` with salt `b"recotem.api-key.v1"`, n=2, r=8, p=1, dklen=32 — see `recotem.serving.auth._hash_api_key`). The wire prefix `sha256:` is a digest-family label, not the algorithm. The digests are not secret in the classical sense, but their exposure enables offline pre-image attacks. Treat them as secrets.
- API key plaintexts — shown once at `recotem keygen` time. Store in a password manager or secrets manager.

**Storage recommendations:**

| Environment | Recommendation |
|-------------|----------------|
| Local dev | Shell environment or `.env` file with mode 600 |
| Docker | Docker secrets or compose `--env-file` with mode 600 |
| Kubernetes | `Secret` objects; use External Secrets Operator for production |
| systemd | `EnvironmentFile` with mode 600, owned by service user |
| CI/CD | Repository secrets (GitHub Actions `secrets.*`); never in YAML files |

Never commit signing keys, API key hashes, or API key plaintexts to version control.

## Log redaction

A structlog processor strips the following keys (case-insensitive) from every log event before output:

```
x-api-key
authorization
cookie
recotem_signing_key
recotem_signing_keys
recotem_api_keys
*_secret*
*_password*
aws_*
gcp_*
google_*
```

The redaction processor is the first in the chain and runs at every log level including trace. A CI check asserts that none of these patterns appear in captured log output across a full training and serving lifecycle.

If a value is replaced with `[REDACTED]` in a log line you are debugging, the field name matched one of the patterns above. This is intentional.

## Artifact security posture flags

`recotem serve` emits a `security.posture` structured log line at every startup:

```json
{
  "event": "security.posture",
  "auth_enabled": true,
  "bind_host": "0.0.0.0",
  "signing_keys": [{"kid": "prod-2026-q3", "fingerprint": "ddeeff00"}],
  "env": "production",
  "allowed_hosts": ["api.example.com"],
  "allowed_origins": ["https://app.example.com"],
  "unsafe_mode": false
}
```

Ship this line to your SIEM. Alert on `auth_enabled: false` or `unsafe_mode: true` in non-development environments.

Two unsafe flags exist and are gated by `RECOTEM_ENV`:

| Flag | Requirement | Effect |
|------|-------------|--------|
| `--insecure-no-auth` | `RECOTEM_ENV` in `development`, `dev`, `test` | Disables API key check; also disables the no-auth → `127.0.0.1` forced bind so `RECOTEM_HOST` is honoured (e.g. for dev containers); repeating warn banner every 60 s |
| `--dev-allow-unsigned` | `RECOTEM_ENV=development` AND `--i-understand-this-loads-arbitrary-code` | Skips HMAC verify; never use outside controlled testing |

Both flags are rejected at startup in any environment not matching the requirement, with an explicit error message.

`--dev-allow-unsigned` is strictly more dangerous than `--insecure-no-auth`:
on the train side it signs artifacts with a deterministic in-memory dev key
(`dev:0000…`); on the serve side it loads any artifact, including ones
produced by another developer or a hostile process. Treat any artifact
written under this flag as untrusted and never copy it into a production
environment.

## Authentication failure events

| Event | Trigger | Status |
|-------|---------|--------|
| `auth_missing_header` | Request with no `X-API-Key` header (and `RECOTEM_API_KEYS` is non-empty) | 401, code `missing_api_key` |
| `auth_invalid_key` | Header present but no kid hashes match | 401, code `invalid_api_key` |

Both events log `path=<request.url.path>` only; the candidate header value
is never logged in any form. The matching kid is attached to
`request.state.kid` (and to subsequent log lines via `structlog.contextvars`)
on success.

## Predict response: information leakage

`POST /predict/{name}` returns:

- 503 (`recipe_unavailable` / `recipe_unhealthy`) — recipe stub or stale entry; visible without auth context only at `/health`.
- 404 (`user_not_found`) — `user_id` was not in training data. This response distinguishes "known user, no recommendations" from "unknown user". If user-existence is sensitive in your application, mask 404 responses at your reverse proxy and return a generic empty-recommendation body.
- 200 — recommendations, optionally joined with item metadata. Field stripping is configured via `RECOTEM_METADATA_FIELD_DENY` (case-sensitive column names). Use this to keep PII columns out of API responses even when they are present in the metadata file.

`cutoff` is bounded at `[1, 1000]` by the request schema; oversized requests
receive a 422 from FastAPI before reaching the recommender.

## Rate limiting and DoS

Recotem itself does not implement request-rate limiting. Operators must front
`recotem serve` with a reverse proxy (nginx `limit_req`, Caddy
`rate_limit`, ALB / Cloud Armor) and apply per-IP / per-API-key quotas.
`/predict` is CPU-bound; sustained request rates above the recommender's
inference throughput will queue under uvicorn and cause request latency
to climb before HTTP 429 would naturally back off — measure and cap at the
proxy.

## Signing-key entropy and storage

- **Generation**: `recotem keygen --type signing` derives keys from
  `os.urandom(32)`, i.e. 256 bits of OS entropy. Reject any operator
  attempt to use a shorter or non-random value — `KeyRing` enforces exactly
  32 bytes after hex-decoding and refuses anything else with `ArtifactError`.
- **Storage**: same controls as `RECOTEM_API_KEYS` (see "Secrets handling"
  above). On a multi-tenant host, prefer a secrets manager that injects
  the env var at process start rather than a static `.env` file.
- **Key compromise**: rotate immediately. The four-step procedure is in
  [operations.md → Signing key rotation](operations.md#signing-key-rotation).
  After all artifacts have been re-signed with the new kid, remove the
  compromised kid from `RECOTEM_SIGNING_KEYS` so any artifact still
  carrying it fails verification (event `artifact_kid_unknown` /
  `artifact_hmac_mismatch`).

## Plugin trust

Third-party DataSource plugins are installed Python packages. Installing a plugin is equivalent to running `pip install` from the same source — the plugin's code runs with full process privileges.

Operators should:

- Pin plugin versions in `pyproject.toml` or `uv.lock`.
- Hash-pin via pip-tools / uv lock file and verify the lock file in CI.
- Review third-party plugin source code before deployment.
- Use the same supply-chain controls as for any other Python dependency.

Recotem does not sandbox plugins. A malicious plugin can read env vars, including `RECOTEM_SIGNING_KEYS` and `RECOTEM_API_KEYS`. Vet your plugins.

## Network exposure

By default, `recotem serve` binds to `127.0.0.1`. When `RECOTEM_API_KEYS` is empty the bind is **forced** to `127.0.0.1` regardless of `RECOTEM_HOST` — the only way to bind to another interface is to either configure `RECOTEM_API_KEYS` or pass `--insecure-no-auth` (which is itself gated on `RECOTEM_ENV`). To expose externally:

1. Configure `RECOTEM_API_KEYS` (otherwise the bind is forced to `127.0.0.1`).
2. Set `RECOTEM_HOST=0.0.0.0`.
3. Set `RECOTEM_ALLOWED_HOSTS` to the exact hostnames clients will use.
4. Set `RECOTEM_ALLOWED_ORIGINS` if browser clients send CORS requests.
5. Put a TLS-terminating reverse proxy (nginx, Caddy, ALB, Cloud Run) in front.

`recotem serve` does not terminate TLS. Do not expose it directly on a public port without a TLS proxy.

`TrustedHostMiddleware` blocks requests with unrecognized `Host` headers, defending against host-header injection. Set `RECOTEM_ALLOWED_HOSTS` explicitly in production.
