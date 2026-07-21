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
  (authenticated) ─────►│  POST /v1/recipes/{name}:*  X-API-Key      │
                        │  GET  /v1/health                           │
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

> **fsspec input schemes inherit cloud credentials.** When `source.path` uses `s3://`, `gs://`, `az://`, or `abfs(s)://`, the Pod's ambient IAM or service-account credentials are used directly by fsspec — there is no additional credential gate inside Recotem. The SSRF guard applies only to HTTP/HTTPS fetches. In environments where recipe authors are not fully trusted, scope the IAM role or service account to read-only access on the specific bucket(s) and prefix(es) used by your recipes.

## Threat model summary

| Threat | Mitigation |
|--------|-----------|
| Malicious artifact file (serialization RCE) | HMAC-SHA256 verify before any deserialization; signing key required; no legacy unsigned fallback |
| HMAC bypass leading to arbitrary class construction | Hand-enumerated FQCN allow-list as backstop (see below) |
| Artifact-size DoS | `RECOTEM_MAX_ARTIFACT_BYTES` cap (default 2 GiB); header length cap (64 KiB); both enforced before deserialization |
| Request-body DoS (multi-GB body buffered before validation) | `RECOTEM_MAX_BODY_BYTES` cap (default 128 MiB) enforced by `BodySizeLimitMiddleware` before Starlette buffers/parses the body — on both `Content-Length` and chunked bodies; over-cap → `413 PAYLOAD_TOO_LARGE`. All request fields (ids, `exclude_items`, `seed_items`, batch size, and feature-dict key/value lengths + key count) are individually bounded. See [Rate limiting and DoS](#rate-limiting-and-dos) |
| Stat-then-read TOCTOU on artifact | Read-once protocol: bytes read into memory once, sha256 computed, then HMAC-verified from the same buffer |
| Key material in logs | structlog redaction processor runs first in chain; unit test asserts no key material at any log level |
| API key brute-force / timing attack | `hmac.compare_digest` constant-time compare; no logging of plaintext or hash |
| Credential injection via recipe env expansion | `RECOTEM_SIGNING_KEYS`, `RECOTEM_API_KEYS`, `*_SECRET*`, `*_PASSWORD*`, `*_TOKEN*`, `*_KEY*`, `AWS_*`, `GOOGLE_*`, `GCP_*` are blacklisted from `${...}` expansion |
| SQL injection via recipe | Env expansion never performed inside `source.query`; dynamic values must use `@param` BigQuery placeholders |
| Path traversal via recipe | `name` validated with `^[A-Za-z0-9_-]{1,64}$` at load and before every filesystem use; artifact root confinement via `RECOTEM_ARTIFACT_ROOT` |
| Tampered or rotated network-fetched data | `sha256` integrity pin is **mandatory** on `source.path` / `item_metadata.path` when the scheme is `http://` or `https://`; mismatch raises `DataSourceError` (exit 3) before the bytes reach the parser |
| Resource exhaustion via giant network fetch | `RECOTEM_MAX_DOWNLOAD_BYTES` (default 256 MiB) caps the raw I/O body during fetch; cap exceeded → `DataSourceError` mid-stream. Does NOT cap the decompressed DataFrame — see [Decompressed-size cap not enforced](#decompressed-size-cap-not-enforced-medium-5) |
| Plaintext HTTP source on the public internet | Operator policy. `http://` is allowed (legitimate inside trusted networks) but operators MUST avoid plaintext on the public internet; sha256 mitigates content tampering for any reachable response |
| Unrecognised plugin loading arbitrary code | Conflicting plugin `type_name` fails startup; installed plugins are treated as trusted code (pin versions) |
| Unauthenticated external access | Default bind `127.0.0.1`; `--insecure-no-auth` gated by `RECOTEM_ENV` in `{development, dev, test}`; `TrustedHostMiddleware` blocks unrecognized hosts |

## Decompressed-size cap not enforced (MEDIUM-5)

`RECOTEM_MAX_DOWNLOAD_BYTES` caps the number of raw bytes read from any source
path (HTTP/HTTPS body, local file I/O, object-store stream). It does **not**
cap the size of the pandas DataFrame that Pandas constructs after decompression
and parsing.

### How the gap arises

Compressed CSV files (`.gz`, `.bz2`, `.zip`, `.xz`) and columnar Parquet files
with aggressive compression can expand by an order of magnitude or more when
decompressed. A 256 MiB `.csv.gz` that compresses at 20:1 produces a ~5 GiB
in-memory DataFrame without any raw I/O byte ever being refused by the cap.
`item_metadata.path` is subject to the same gap.

### Attack scenario

A recipe author who has permission to create or modify recipes can point
`source.path` at a highly compressed CSV and submit it to `recotem train`.
The train process will accept the raw bytes (under the cap), decompress the
file, and attempt to build a DataFrame that exceeds the available process
memory, causing the train process to be killed by the OOM killer. No signing
key is required; recipe-authoring permission is the only prerequisite.

### Current mitigations (incomplete)

The raw I/O cap (`RECOTEM_MAX_DOWNLOAD_BYTES`) prevents unbounded network
downloads but does not constrain decompressed size. There is no DataFrame-level
memory cap in the current implementation.

### Recommended operator-side mitigations

Until a DataFrame-level cap is implemented in a future release, operators
should apply one or more of the following controls:

| Control | How to apply |
|---------|-------------|
| Restrict recipe-authoring permission | Treat recipe creation and modification as a privileged action. Only operators or CI pipelines with write access to the recipes directory should be able to submit new recipes to `recotem train`. |
| cgroup memory limit | Run `recotem train` inside a cgroup with a hard memory limit (`MemoryMax=` in a systemd unit, `docker run --memory`, or an equivalent). The OOM kill remains, but it is scoped to the train container rather than the host. |
| `RLIMIT_AS` | Set `resource.setrlimit(resource.RLIMIT_AS, (limit, limit))` in a wrapper before invoking the train binary, or use `ulimit -v` in the wrapper shell. This caps the virtual address space of the process. |
| Kubernetes `resources.limits.memory` | Set a memory limit on the train Pod or CronJob. The Pod is evicted rather than the node being destabilised. Example: `resources: { limits: { memory: "4Gi" } }`. See `docs/deployment/k8s.md`. |

**Note:** cgroup / RLIMIT controls do not prevent the OOM event — they contain it. A deliberately malicious recipe will still abort the current training run. The real prevention is restricting who can author recipes.

## Network-source fetch behaviour

`recotem train` fetches `http://` and `https://` source paths via stdlib
`urllib`. The fetch path enforces:

- **Redirect cap**: at most 5 redirects (urllib's default 10 is overridden);
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

## Feature-aware iALS

### Feature-source path and integrity rules

A recipe's `features.item.source` / `features.user.source` are full
DataSource configs — same registry as the top-level `source` — and are
**not** a lower-trust surface just because they feed side features instead
of interactions. The recipe loader applies the identical rules to
`features.item.source.path` / `features.user.source.path` that it applies
to `source.path`:

- The same [path-scheme allow-list](recipe-reference.md#path-rules) (bare
  local path, `file://`, `s3://`, `gs://`, `az://`, `abfs(s)://`, `http://`,
  `https://`; chained fsspec protocols rejected).
- The same mandatory `sha256` integrity pin whenever the scheme is `http://`
  or `https://`.
- Embedded URI credentials are rejected on feature-source paths exactly as
  on `source.path` / `item_metadata.path`.

`recotem validate` probes feature-source connectivity the same way it
probes `source` (see [recipe-reference.md](recipe-reference.md#features)),
so a missing extra or an unreachable feature source is caught before
`recotem train` does real work.

### Feature-encoder version gate

Every artifact trained with a `features:` block carries a small
`features.version` field in its (unencrypted, HMAC-covered) header. Before
serve deserializes the payload, it checks that field against this build's
known encoder-state version:

- **`features` key absent** → load proceeds (fail **open**). This is a
  pre-feature artifact or a model trained without `features:`; there is no
  encoder state to misinterpret.
- **`features` present but `version` missing, non-integer, or not the exact
  version this build knows** → refuse to load (fail **closed**), reason
  `feature_version`.

The asymmetry is deliberate, and mirrors the posture of the pre-existing
irspack version-skew guard (see
[operations.md — irspack version skew](operations.md#irspack-version-skew)):
an old serve with no feature code never reads the encoder state and keeps
serving known-user recommendations correctly — safe by ignorance. A serve
that *does* have feature code but does not recognize the state's shape is
the one that must be stopped, because silently proceeding would encode a
request's `user_features` / `item_features` into the wrong vector space and
return **incorrect recommendations that look like correct ones** — the one
failure mode a request-count or error-rate metric cannot catch. See
[operations.md — Feature-aware iALS sizing](operations.md#feature-aware-ials-sizing)
for the operational detail (event name, metric label).

### Request-side PII: `user_features` / `item_features`

`user_features` (on `:recommend` and `:recommend-related`) and per-seed
`item_features` (on `:recommend-related`) are attacker- or client-supplied
request fields that carry personal data **by construction** — an age band,
a country, a device category. This is a request-side PII vector distinct
from anything else in the v1 API surface, and recotem's posture is:

1. **Raw feature values are never logged.** The code paths that touch
   feature values (encoding, the unknown-category counter) log column names
   and counts only — never the value itself.
2. `log_redaction.py`'s key-based redaction also strips `user_features` /
   `item_features` wholesale, as defense in depth in case a future code path
   ever logs a raw request body.
3. Feature values are never echoed back in a response body, so no
   response-side deny-list is needed for them. `RECOTEM_METADATA_FIELD_DENY`
   (see [recipe-reference.md](recipe-reference.md#item_metadata)) is the
   existing **response-side** counterpart for a different field: it strips
   configured item-metadata columns from `:recommend` /
   `:recommend-related` responses. The two controls address opposite
   directions of PII flow — one on the way in, one on the way out — and
   neither substitutes for the other.

### Extreme numerical feature values map to a 400, not a 500

A client-supplied `numerical` feature value that is extreme but still a
finite float (e.g. `1e22`) is not rejected by schema validation — it is a
legal float. Standardized against the training column's mean/std
(`recotem._features._row_values`), such a value can produce a magnitude
large enough to make irspack's per-request conjugate-gradient cold-start
solve numerically ill-conditioned. irspack's native core raises a bare
`RuntimeError` ("Conjugate-gradient solver encountered a singular system.")
in that case, with no awareness that the offending value came from an
untrusted client rather than a bug.

`recotem._idmap` catches that `RuntimeError` at each of the three cold-start
call sites that feed a features-derived matrix into irspack's solver
(`get_score_cold_user_from_features`, `get_score_cold_user`,
`compute_item_embedding_from_features`) and re-raises
`ColdStartNumericalError`, which `serving/routes.py` maps to `400
FEATURE_VALUE_UNUSABLE` (see [api-reference.md](api-reference.md#feature-aware-cold-start))
rather than letting it surface as an unhandled `500`.

**What this does and does not guarantee.** The catch is **signature-gated**:
`_is_numerical_cold_start_failure` re-raises only when the `RuntimeError`'s
message matches one of the irspack numerical-failure signatures verified
present in the installed binary and enumerated in
`_NUMERICAL_FAILURE_SIGNATURES` (`recotem/_idmap`). That narrowness is
deliberate — a bare `except RuntimeError` would silently reattribute an
unrelated irspack bug to client input — but it means the mapping is only as
complete as that list. An irspack release that rewords one of those messages
would re-raise past the gate and surface as a `500`. Equally out of scope is
any path where a client value fails as something other than a `RuntimeError`
from the solver — and this PR fixed a live instance of exactly that shape: a
`numerical` value supplied as a JSON integer literal of 309 or more digits
raised `OverflowError` (an `ArithmeticError`, not a `ValueError`) out of
`float()`, escaped the `except (TypeError, ValueError)` around the parse, and
reached the generic 500 handler with nothing but a valid API key
(`_features._parse_number`). The honest claim is therefore narrower than
"cannot crash the request": the **known** ill-conditioning paths are mapped
to a 400, and both the signature list and the parse-path exception handling
are the places to extend when a new one is found.

The fix is otherwise conservative: it does not change what value a
`numerical` column standardizes to, at either train or serve time — the same
extreme value flowing through training-time `encode()` is untouched, and a
resulting final-refit Cholesky failure on an ill-conditioned *training*
matrix already surfaces as `TrainingError` (exit 4) through an unrelated code
path. Only the three serve-time cold-start solves are wrapped.

### Why hand-rolled encoding, not scikit-learn preprocessing

Feature encoding (`recotem._features`) deliberately reimplements one-hot,
standardization, and multi-hot encoding rather than persisting a fitted
`sklearn.preprocessing.OneHotEncoder` / `StandardScaler` inside the artifact.
[operations.md](operations.md#upgrades) already documents scikit-learn as a
**further, unguarded** compatibility axis: `TruncatedSVDRecommender` pickles
an sklearn estimator into the payload, and sklearn's own
`InconsistentVersionWarning` says unpickling across its own minor versions
"might lead to breaking code or invalid results" — recotem range-pins
`scikit-learn` to narrow this window but cannot close it. Pickling
`OneHotEncoder` / `StandardScaler` into the feature-encoder state would
**voluntarily widen** that same unguarded axis, and would do so via private
sklearn module paths (e.g. `sklearn.preprocessing._data`) that have no entry
in the FQCN allow-list's narrow prefix list to absorb a future rename.

The encoder state is instead plain Python data — nested `dict` / `list`,
`str` vocabularies, and `int` / `float` scalars, with no numpy or pandas
object anywhere in it (`build_encoder_state` constructs every scalar through
`str()` / `float()` / `int()`; the numpy arrays in `_features.py` are built
inside `encode()` at call time and are not part of the persisted state).
Verified to round-trip through the existing `SafeUnpickler` with **no
allow-list change**.

The allow-list is only a **partial** backstop for that invariant, and the
limit is worth stating precisely, because it is what makes those coercions
load-bearing. A stray `pandas.Index` really would be refused at load time
(`pandas.core.indexes.base._new_Index` is not allow-listed — verified). A
`numpy.str_` would **not**: it pickles via `numpy._core.multiarray.scalar`
plus `numpy.dtype`, both allow-listed (the former via the `numpy._core.*`
module-prefix list, the latter via its explicit FQCN entry), so it loads and
keeps its type (verified). Nothing
downstream catches it either — `numpy.str_` subclasses `str` and hashes and
compares equal to it, so every vocabulary lookup keeps working and the leak
stays invisible at runtime. The `str()` coercions in `build_encoder_state`
are therefore the only thing keeping numpy's scalar types out of the state,
not a belt-and-braces gesture on top of a gate that would fail closed anyway.
See the module docstring in `recotem/_features.py` for the reasoning, and
`tests/unit/test_features.py::test_vocabulary_keys_are_exactly_str_not_numpy_str`
for the enforcement — it asserts the exact key type, because an `isinstance`
check cannot distinguish the two.

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

The allow-list is frozen per irspack 0.5.x. If irspack adds or renames recommender classes, the list is updated with the corresponding Recotem release.

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
irspack.recommenders.ials.IALSTrainer
irspack.recommenders.ials.IALSConfigScaling
irspack.recommenders._ials_core.IALSTrainer
irspack.recommenders._ials_core.IALSModelConfig
irspack.recommenders._ials_core.IALSSolverConfig
irspack.recommenders._ials_core.LossType
irspack.recommenders._ials_core.SolverType
irspack.recommenders.knn.FeatureWeightingScheme
sklearn.decomposition._truncated_svd.TruncatedSVD
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

This list is frozen per Recotem release. It includes the `*Recommender`
classes **and** the trainer, config, enum, and scikit-learn estimator objects
those recommenders embed as attributes (e.g. `IALSTrainer`, `LossType`,
`FeatureWeightingScheme`, scikit-learn's `TruncatedSVD`). A trained artifact is
a single pickle graph: if any embedded class is absent the artifact is rejected
at serve time even though training and signing succeeded, so the list must
cover the full object graph, not just the entry-point recommender.

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

A deny-list removes high-risk submodules that fall under an allowed prefix
but expose code-execution gadgets (test runners, build helpers, foreign
function bindings, file-IO constructors). The following modules are
explicitly deny-listed as a defence-in-depth trip-wire independent of
the prefix allow-list:

- `numpy.testing`, `numpy.distutils`, `numpy.f2py`, `numpy.ctypeslib`,
  `numpy.lib`, `numpy.compat`, `numpy.random`, `numpy._core._exceptions`
- `scipy.sparse.linalg`, `scipy.sparse.tests`, `scipy.sparse.csgraph`

`numpy.random` is denied defensively: RNG state objects are not needed in
Recotem artifacts, and a future numpy release could introduce a
reduce-callable in that module with side-effects. Any legitimate RNG class
required by a future irspack version should be added by exact FQCN to the
hand-enumerated allow-list rather than widening the deny-list.
`numpy._core._exceptions` is denied to shrink the internal attack surface
exposed through the broad `numpy._core.*` prefix allow-list.

Submodules not on any prefix (e.g. `numpy.linalg`,
`numpy.fft`, `numpy.polynomial`) are blocked implicitly — they are neither
on the FQCN list nor the prefix allow-list, so they never reach the
deny-list check.

HMAC verification remains the primary defence; the prefix allow-list is
the secondary layer scoped to the scientific stack only.

`recotem inspect <artifact>` runs the full HMAC verify path and prints the header JSON without invoking the deserializer. It is safe to run on untrusted artifacts. The argument accepts both local paths and fsspec URIs (`s3://bucket/key.recotem`, `gs://bucket/key.recotem`, `az://container/key.recotem`, `https://host/key.recotem`, `file:///abs/path.recotem`).

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

## Recipe env-var expansion blacklist

Only variables matching `RECOTEM_RECIPE_*` are candidates for `${...}` expansion. A secondary blacklist blocks sensitive names even if they satisfy the prefix. Rules are checked in order — first match wins:

| Rule | Patterns (case-insensitive) |
|------|----------------------------|
| Exact match | `RECOTEM_SIGNING_KEYS`, `RECOTEM_API_KEYS` |
| Prefix match | `AWS_*`, `GCP_*`, `GOOGLE_*`, `AZURE_*` |
| Substring match | `*SECRET*`, `*PASSWORD*`, `*PASSWD*`, `*TOKEN*`, `*KEY*`, `*AUTH*`, `*BEARER*`, `*CRED*`, `*PRIVATE*` |

The `*KEY*` substring is intentionally broad. Any `RECOTEM_RECIPE_*` variable whose uppercased name contains the substring `KEY` (no underscore boundary required) is rejected — this includes `RECOTEM_RECIPE_PARTITION_KEY`, `RECOTEM_RECIPE_APIKEY`, and `RECOTEM_RECIPE_KEYBOARD`. Use a name that does not contain `KEY` (e.g. `RECOTEM_RECIPE_PARTITION_COLUMN`). A blacklisted reference raises `RecipeError` (exit 2) and the error message names the variable but never includes its value.

> **`RECOTEM_RECIPE_GCP_PROJECT` is allowed.** The `GCP_*` prefix blacklist matches only names that *start* with `GCP_` — it does not match `RECOTEM_RECIPE_GCP_PROJECT`, which starts with `RECOTEM_RECIPE_`. The `examples/ga4-bigquery/` recipe uses this variable to pass a GCP project ID. The variable is safe because `GCP_PROJECT` contains none of the blocked substrings (`KEY`, `SECRET`, `TOKEN`, etc.). Be careful not to accidentally include a blacklisted substring in the tail portion of a `RECOTEM_RECIPE_*` variable name.

**Operational hardening.** The blacklist is a _secondary_ defence that catches accidental name collisions. The _primary_ safety property is operational: **never store secrets in `RECOTEM_RECIPE_*` environment variables.** The `RECOTEM_RECIPE_` prefix should be reserved for non-sensitive configuration values (dataset names, date ranges, partition columns, feature flags). If a secret were placed under this prefix with a name that does not match any blacklisted pattern (e.g. `RECOTEM_RECIPE_DB_ENDPOINT`), the blacklist would not catch it. Treat the prefix as a namespace for recipe parameterisation, not as a secrets namespace.

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

## API key minimum length

Recotem enforces a 32-character minimum on the `X-API-Key` header value. Plaintext keys shorter than 32 chars are rejected with a 401 (`invalid_api_key`) before any digest comparison is attempted. The error message does not reveal the minimum threshold to the caller.

The recommended workflow is `recotem keygen --type api`, which generates a 43-char base64url plaintext (32 raw bytes of `os.urandom`). Operator-chosen passphrases or passwords must be at least 32 chars; shorter values will silently fail authentication at runtime with no configuration error at startup.

## `recotem keygen` output format

The two key types produce different output and must not be confused:

**Signing key** (`--type signing`):

```
kid=prod-2026-q3
plaintext=<64 hex chars>        # 32 raw bytes; THIS is the signing key
fingerprint=ddeeff00            # sha256(key_bytes)[:8]; matches /security.posture log
env_entry=RECOTEM_SIGNING_KEYS=prod-2026-q3:<64 hex chars>
```

- Copy the `env_entry=` value into `RECOTEM_SIGNING_KEYS`.
- The `fingerprint=` value is `sha256(key_bytes)[:8]`. It matches the `fingerprint` field in the `security.posture` log line emitted at startup. Use it to confirm the correct key is loaded — it does not expose the key material.
- The `fingerprint=` line is informational only and must **not** be used in `RECOTEM_SIGNING_KEYS` or any config value.

**API key** (`--type api`):

```
kid=client-a
plaintext=<43-char base64url>   # share with the API client (shown once)
hash=sha256:<64 hex chars>      # put this in RECOTEM_API_KEYS
env_entry=RECOTEM_API_KEYS=client-a:sha256:<64 hex chars>
```

- Copy the `env_entry=` value into `RECOTEM_API_KEYS`.
- The `hash=sha256:<hex>` line is the scrypt digest that goes into `RECOTEM_API_KEYS`. The `sha256:` prefix is a digest-family label, not the algorithm name — the actual digest uses `hashlib.scrypt`.
- The `plaintext` is shown once at generation time. Store it in a password manager; there is no recovery path.

The two key types use incompatible formats. Putting an API key hash into `RECOTEM_SIGNING_KEYS` (or vice versa) will fail at startup with a configuration error.

## Log redaction

A structlog processor strips the following keys (case-insensitive) from every log event before output:

```
x-api-key
authorization
cookie
recotem_signing_key
recotem_signing_keys
recotem_api_keys
*secret*
*password*
*passwd*
*token*
*key*  (but NOT *keys* — plural avoids false-positives on list fields)
*auth*
*bearer*
*cred*
*private*
aws_*
gcp_*
google_*
azure_*
```

The redaction processor is the first in the chain and runs at every log level including trace. A CI check asserts that none of these patterns appear in captured log output across a full training and serving lifecycle.

If a value is replaced with `[REDACTED]` in a log line you are debugging, the field name matched one of the patterns above. This is intentional.

**URL userinfo redaction.** Any URL containing embedded credentials (e.g. `https://user:pass@host/path`) is logged as `https://[REDACTED]@host/path` at the HTTP-fetcher boundary via `redact_url_userinfo`. The recipe loader rejects userinfo-bearing URLs at parse time, so this redaction applies only to internally-constructed URLs and redirect targets. Do not log raw URLs with userinfo in your own application code — strip credentials before logging.

## Artifact security posture flags

`recotem serve` emits a `security.posture` structured log line at every startup:

```json
{
  "event": "security.posture",
  "auth_enabled": true,
  "bind_host": "0.0.0.0",
  "signing_keys": [{"kid": "prod-2026-q3", "fingerprint": "ddeeff00"}],
  "signing_kids": ["prod-2026-q3"],
  "signing_key_status": "configured",
  "env": "production",
  "allowed_hosts": ["api.example.com"],
  "allowed_origins": ["https://app.example.com"],
  "unsafe_mode": false
}
```

Ship this line to your SIEM. Alert on `auth_enabled: false` or `unsafe_mode: true` in non-development environments.

The `signing_key_status` field takes one of three values:

| Value | Meaning |
|-------|---------|
| `configured` | Signing keys are present and the KeyRing was built successfully. |
| `dev_allow_unsigned` | Running in dev-unsigned mode; no keys are required or loaded. |
| `missing` | No signing keys configured and `--dev-allow-unsigned` not set. Startup will fail immediately after this log line. |

Alert on `signing_key_status: missing` — this event is always immediately followed by a startup failure, but the log line fires unconditionally so SIEM rules that require it still trigger.

Two unsafe flags exist and are gated by `RECOTEM_ENV`:

| Flag | Requirement | Effect |
|------|-------------|--------|
| `--insecure-no-auth` | `RECOTEM_ENV` in `development`, `dev`, `test` | Disables API key check; also disables the no-auth → `127.0.0.1` forced bind so `RECOTEM_HOST` is honoured (e.g. for dev containers); repeating warn banner every 60 s |
| `--dev-allow-unsigned` | `RECOTEM_ENV=development` AND `--i-understand-this-loads-arbitrary-code` | Skips HMAC verify; never use outside controlled testing |

> **OpenAPI schema in production.** When `RECOTEM_ENV` is set to `production`, `prod`, or `staging`, the `/docs`, `/redoc`, and `/openapi.json` endpoints are disabled at app construction time. Requests to those paths return 404. Development and test environments keep the endpoints enabled for developer ergonomics.

Both flags are rejected at startup in any environment not matching the requirement, with an explicit error message.

`--dev-allow-unsigned` is strictly more dangerous than `--insecure-no-auth`:
on the train side it signs artifacts with a deterministic in-memory dev key
(`dev:0000…`); on the serve side it loads any artifact, including ones
produced by another developer or a hostile process. Treat any artifact
written under this flag as untrusted and never copy it into a production
environment.

## Authentication failure events

| Event | Level | Trigger | Status |
|-------|-------|---------|--------|
| `auth_missing_header` | WARN | Request with no `X-API-Key` header (and `RECOTEM_API_KEYS` is non-empty) | 401, code `missing_api_key` |
| `auth_invalid_key` | WARN | Header present but no kid hashes match | 401, code `invalid_api_key` |
| `auth_anonymous_bypass` | DEBUG | Every request when `RECOTEM_API_KEYS` is empty (no-auth mode) | — |
| `auth_anonymous_bypass_first_seen` | INFO | First request from a given `client_host` in no-auth mode | — |

Both `auth_missing_header` and `auth_invalid_key` log `path=<request.url.path>` only; the candidate header value is never logged in any form. The matching kid is attached to `request.state.kid` (and to subsequent log lines via `structlog.contextvars`) on success.

When `RECOTEM_API_KEYS` is empty, `auth_anonymous_bypass` fires on **every** request (DEBUG) so access-log correlation is possible. `auth_anonymous_bypass_first_seen` fires once per unique `client_host` (INFO) for a first-seen audit trail. The LRU cache tracking first-seen client IPs is bounded to 1024 entries to prevent unbounded memory growth under high IP churn (e.g. rotating CI IPs or attacker scanning).

## Inference response: information leakage

`POST /v1/recipes/{name}:recommend` (and its siblings `:recommend-related`,
`:batch-recommend`, `:batch-recommend-related`) returns:

- 503 (`RECIPE_UNAVAILABLE`) — recipe stub or stale entry; visible without auth context only at `/v1/health`.
- 404 (`RECIPE_NOT_FOUND`) — the recipe name is not registered at all. Distinct from `UNKNOWN_USER` (same status, different `code`).
- 404 (`UNKNOWN_USER`) on `:recommend` — `user_id` was not in training data. This response distinguishes "known user, no recommendations" from "unknown user". If user-existence is sensitive in your application, mask 404 responses at your reverse proxy and return a generic empty-recommendation body.
- 404 (`UNKNOWN_SEED_ITEMS`) on `:recommend-related` — none of the supplied `seed_items` are known to the trained model.
- 200 — recommendations, optionally joined with item metadata. Field stripping is configured via `RECOTEM_METADATA_FIELD_DENY` (case-**insensitive** column names — `"Internal_ID"` in metadata is stripped if `"internal_id"` is in the deny list). Use this to keep PII columns out of API responses even when they are present in the metadata file.

`limit` is bounded at `[1, 1000]` by the request schema; oversized requests
receive a 422 from FastAPI before reaching the recommender.

## Rate limiting and DoS

Recotem itself does not implement request-rate limiting. Operators **must**
front `recotem serve` with a reverse proxy (nginx `limit_req`, Caddy
`rate_limit`, ALB / Cloud Armor) and apply per-IP or per-API-key quotas on
the `/v1/` surface. This is not optional in production.

**Why the proxy layer is responsible — scrypt amplification.** Every
authentication attempt (valid or not) runs a scrypt key-derivation check
(`hashlib.scrypt` with n=2, r=8, p=1, dklen=32) per stored API key. An
unauthenticated attacker who can send requests at the network layer can
therefore trigger CPU-bound scrypt work on every failed authentication, at a
rate bounded only by the network rather than by the application. Recotem does
not implement its own rate limiter; that is the proxy's responsibility.

The v1 inference verbs (`:recommend`, `:recommend-related`,
`:batch-recommend`, `:batch-recommend-related`) are also CPU-bound for
recommendation inference; sustained request rates above the recommender's
inference throughput will queue under uvicorn and cause request latency to
climb. Measure and cap at the proxy.

**Cold-start solves are bounded per request.** Case C of the feature-aware
cold start (a `:recommend-related` seed carrying `item_features`, see
[api-reference.md](api-reference.md#feature-aware-cold-start)) runs one
irspack conjugate-gradient solve **per cold seed** — measured ~0.25–0.45 ms
each. That per-solve cost is effectively **flat in model size**: 0.27 ms at
`n_components=8`, 0.30 ms at 128, 0.45 ms at 256, and flat across encoded
feature dimensions from 3 to 501. The solve is call-overhead-dominated rather
than Cholesky-dominated at every size a recipe can produce, so a
production-sized model does not make this bound materially worse. The
aggregate is capped at 512 solves per request by `BATCH_COLD_SEED_SOLVE_LIMIT`
(`serving/schemas.py`) on `:batch-recommend-related` — roughly 230 ms of
single-threaded CPU in the worst case. An element that would exceed the cap
receives a per-element `VALIDATION_ERROR` inside a 200, matching
`BATCH_AGGREGATE_LIMIT`'s existing posture, rather than failing the whole
request with a 422. The single verbs need no cap of their own: they are
structurally bounded at 100 solves by `seed_items`' `max_length`. As with
everything else in this section, that bounds the work a **single request** can
demand and says nothing about the rate; sustained rates remain the proxy's
job.

**Request body is size-capped before it is parsed.** A `BodySizeLimitMiddleware`
(`serving/app.py`) rejects any request body larger than `RECOTEM_MAX_BODY_BYTES`
(default 128 MiB, clamped [1 MiB, 2 GiB]) with a `413 PAYLOAD_TOO_LARGE`
**before** Starlette buffers and JSON-parses it. Without this an authenticated
client could send a multi-GB body and force the process to allocate and parse it
in full ahead of any pydantic validation. The middleware enforces the cap at two
points so the header cannot be omitted to bypass it: a declared `Content-Length`
over the cap is refused outright, and a chunked/streamed body with no
`Content-Length` is counted as it arrives and cut off the moment the running
total crosses the cap. The default preserves the entire legitimate request space
— the largest well-formed body the API accepts is ~72 MiB (a 256-element batch,
each sub-request carrying 1000 `exclude_items` of up to 256 chars) — while
blocking GB-scale bodies. This bounds a **single request**; sustained rates are
still the proxy's job.

**Per-request input fields are all length- and count-bounded.** Every
client-controlled request field has an explicit cap so a well-formed but huge
body cannot amplify inside validation or the recommender: `user_id` / item ids
are 1–256 chars (`_ItemStr`), `exclude_items` ≤ 1000, `seed_items` ≤ 100, batch
`requests` ≤ 256. The cold-start feature mappings are bounded on all three axes:
`Field(max_length=64)` caps the number of keys, each string **value** is capped
at 8192 chars (`_MAX_FEATURE_VALUE_CHARS`), and each **key** is capped at 1–256
chars (`_MAX_FEATURE_KEY_CHARS`) — covering `user_features` column names, the
`item_features` outer seed-id keys (typed `_ItemStr`), and the nested per-seed
feature keys. Before the key cap the dict keys were the one length-unbounded
field left: `max_length` bounded only the key *count*, and only *values* were
length-checked, so an attacker could send megabyte-scale keys. An over-length
key now yields a `422` reporting only its length, never its text, so it cannot
amplify into the error body or logs.

**Recommended nginx configuration:**

```nginx
# Define a rate-limit zone keyed by IP address (adjust burst/rate as needed).
limit_req_zone $binary_remote_addr zone=recotem_v1:10m rate=20r/s;

server {
    # ... TLS and upstream configuration ...

    location /v1/ {
        limit_req zone=recotem_v1 burst=40 nodelay;
        limit_req_status 429;
        proxy_pass http://recotem_backend;
    }
}
```

Operators who want to exempt `/v1/health` or `/v1/metrics` from the limit
can carve them out with a more specific `location` block; the recommended
default is to rate-limit the entire `/v1/` surface.

For per-API-key limiting, key on the `$http_x_api_key` variable or use a WAF
(AWS WAF, GCP Cloud Armor, Cloudflare) that can enforce quotas per header
value.

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

By default, `recotem serve` binds to `127.0.0.1`. When `RECOTEM_API_KEYS` is empty the bind is **forced** to `127.0.0.1` regardless of `RECOTEM_HOST` — the only way to bind to another interface is to either configure `RECOTEM_API_KEYS` or pass `--insecure-no-auth` (which is itself gated on `RECOTEM_ENV` in `{development, dev, test}`). To expose externally:

1. Configure `RECOTEM_API_KEYS` (otherwise the bind is forced to `127.0.0.1`).
2. Set `RECOTEM_HOST=0.0.0.0`.
3. Set `RECOTEM_ALLOWED_HOSTS` to the exact hostnames clients will use.
4. Set `RECOTEM_ALLOWED_ORIGINS` if browser clients send CORS requests.
5. Put a TLS-terminating reverse proxy (nginx, Caddy, ALB, Cloud Run) in front.

`recotem serve` does not terminate TLS. Do not expose it directly on a public port without a TLS proxy.

`TrustedHostMiddleware` blocks requests with unrecognized `Host` headers, defending against host-header injection. Set `RECOTEM_ALLOWED_HOSTS` explicitly in production.
