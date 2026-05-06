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

## Threat model summary (spec section 8)

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
| Unrecognised plugin loading arbitrary code | Conflicting plugin `type_name` fails startup; installed plugins are treated as trusted code (pin versions) |
| Unauthenticated external access | Default bind `127.0.0.1`; `--insecure-no-auth` gated by `RECOTEM_ENV=development`; `TrustedHostMiddleware` blocks unrecognized hosts |

## Artifact payload and the FQCN allow-list

irspack's `IDMappedRecommender` depends on scipy sparse matrices and numpy arrays. These cannot be expressed in JSON without losing structure. The native irspack serialization format is required, and it is unavoidable.

The risk is mitigated by four layered controls:

1. Magic bytes, format version, and size checks before any deserialization.
2. HMAC-SHA256 signature verification with multi-kid support and constant-time compare; keys never logged.
3. Hand-enumerated FQCN allow-list — RCE backstop independent of HMAC.
4. Signing key is required for both train and serve, with no env-default. A misconfigured deployment fails closed rather than loading arbitrary files.

The FQCN allow-list permits only these classes. Any other class triggers `ArtifactError` before construction:

```
recotem.serving._compat.IDMappedRecommender
irspack.utils.id_mapping.IDMapper
irspack.recommenders.IALSRecommender
irspack.recommenders.CosineKNNRecommender
irspack.recommenders.TopPopRecommender
irspack.recommenders.RP3betaRecommender
irspack.recommenders.DenseSLIMRecommender
irspack.recommenders.TruncatedSVDRecommender
irspack.recommenders.BPRFMRecommender
numpy.ndarray
numpy.dtype
numpy.core.multiarray._reconstruct
numpy.core.multiarray.scalar
scipy.sparse.csr_matrix
scipy.sparse.csc_matrix
scipy.sparse.coo_matrix
builtins.int
builtins.float
builtins.bool
builtins.list
builtins.tuple
builtins.dict
builtins.str
builtins.bytes
builtins.complex
collections.OrderedDict
```

This list is frozen per Recotem release. Changes ship with a CHANGELOG entry.

Module-prefix allow-listing is explicitly rejected by the spec: it admits gadgets such as `numpy.testing.run_module_suite` or callable proxies in `numpy.distutils`. The FQCN list is exact and per-class.

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
- `RECOTEM_API_KEYS` — contains sha256 hashes of API key plaintexts. The hashes are not secret in the classical sense, but their exposure enables offline pre-image attacks. Treat them as secrets.
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
| `--insecure-no-auth` | `RECOTEM_ENV` in `development`, `dev`, `test` | Disables API key check; forces `127.0.0.1` bind; repeating warn banner every 60 s |
| `--dev-allow-unsigned` | `RECOTEM_ENV=development` AND `--i-understand-this-loads-arbitrary-code` | Skips HMAC verify; never use outside controlled testing |

Both flags are rejected at startup in any environment not matching the requirement, with an explicit error message.

## Plugin trust

Third-party DataSource plugins are installed Python packages. Installing a plugin is equivalent to running `pip install` from the same source — the plugin's code runs with full process privileges.

Operators should:

- Pin plugin versions in `pyproject.toml` or `uv.lock`.
- Hash-pin via pip-tools / uv lock file and verify the lock file in CI.
- Review third-party plugin source code before deployment.
- Use the same supply-chain controls as for any other Python dependency.

Recotem does not sandbox plugins. A malicious plugin can read env vars, including `RECOTEM_SIGNING_KEYS` and `RECOTEM_API_KEYS`. Vet your plugins.

## Network exposure

By default, `recotem serve` binds to `127.0.0.1`. To expose externally:

1. Set `RECOTEM_HOST=0.0.0.0`.
2. Set `RECOTEM_ALLOWED_HOSTS` to the exact hostnames clients will use.
3. Set `RECOTEM_ALLOWED_ORIGINS` if browser clients send CORS requests.
4. Put a TLS-terminating reverse proxy (nginx, Caddy, ALB, Cloud Run) in front.

`recotem serve` does not terminate TLS. Do not expose it directly on a public port without a TLS proxy.

`TrustedHostMiddleware` blocks requests with unrecognized `Host` headers, defending against host-header injection. Set `RECOTEM_ALLOWED_HOSTS` explicitly in production.
