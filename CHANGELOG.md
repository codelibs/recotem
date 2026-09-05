# Changelog

All notable changes to Recotem are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] - Unreleased

### Upgrading from 2.0.0

**2.1.0 moves irspack from 0.4.2 to 0.5.2.** irspack 0.5.0 changed
`IALSModelConfig`'s pickled state from a 7-tuple to a 10-tuple, so **IALS
artifacts trained on 2.0.0 cannot be loaded by 2.1.0.** Every other algorithm
carries over unchanged: of the six algorithms trained under 2.0.0 and loaded
under 2.1.0, five (`CosineKNN`, `TopPop`, `RP3beta`, `DenseSLIM`,
`TruncatedSVD`) load and serve bit-identical scores; only IALS is refused. The
refusal is correct rather than over-cautious — bypassing the guard and
deserializing anyway reproduces the real `TypeError: __setstate__():
incompatible function arguments`. The mechanism and the full verified-pair table
are under **Migrating to irspack 0.5.0** below.

**"Bit-identical" is a claim about loading an existing artifact, not about
retraining.** Those five algorithms load a 2.0.0-trained artifact under 2.1.0
and serve the same scores. Retraining the same recipe on 2.1.0 and diffing
against the 2.0.0 model — the obvious way to convince yourself the upgrade is
safe — is a different comparison, and it will show small differences: measured
at roughly 8.5e-09 on `DenseSLIM` and 3e-15 on `TruncatedSVD`, with the item
ordering unchanged. That is float drift from a dependency range that admits more
than one build, not a break. Validate the upgrade by loading and serving the
artifacts you already have.

This affects more deployments than it might appear to. The shipped tutorial
recipe searches `algorithms: [IALS, TopPop]` and normally settles on IALS, so a
deployment that started from the tutorial holds an IALS artifact without anyone
having chosen IALS explicitly. The winning algorithm is a search outcome, not a
recipe setting — check each artifact with `recotem inspect` rather than reading
it off the recipe.

**If you are on the 2.0.0 *container image*, you are not running it.** The
published `ghcr.io/codelibs/recotem:2.0.0` cannot start on either architecture:
its console script carries the build stage's shebang, `#!/build/.venv/bin/python`,
a path that does not exist in the final image, so the entrypoint fails with
`exec /opt/venv/bin/recotem: no such file or directory`. Verified by digest on
both `linux/amd64` and `linux/arm64`. The Dockerfile has since built the venv at
its final path and the image published for this release starts normally —
`docker.yml` now runs the entrypoint, imports the package, and trains a recipe
on both architectures before anything is pushed. The chart, `examples/k8s/` and
`docs/deployment/k8s.md` pin the image tag and move to this release with it.

**What you will see.** `serve` starts normally — it does not crash. The IALS
recipe is registered with `"loaded": false` and an error naming the recipe, both
irspack versions, and the remedy. Requests to that recipe return `503`
(`RECIPE_UNAVAILABLE`); every other recipe keeps serving.
`recotem_artifact_load_failures_total{reason="version_skew"}` increments, and
`/v1/health/details` reports `"status": "degraded"`.

**On Kubernetes the blast radius depends on which probes you run.** `/v1/health`
is count-based: it returns `degraded` with HTTP **503** whenever `loaded <
total` — that is, whenever *any* recipe failed to load. **No probe in the 2.1.0
chart reads it.** Startup and readiness both read `/v1/health/ready`, which is
`200` while at least one recipe is loaded, so a refused IALS artifact alongside
healthy recipes lets the pod start, join the Service, and serve everything else;
only the skewed recipe returns `503`. If the skewed recipe is the *only* recipe,
nothing loads, `/v1/health/ready` stays `503`, and the startupProbe restarts the
container (12 failures × 10 s = a 120 s window) until you retrain.

**If your own manifests point any probe at `/v1/health`, the blast radius is the
whole pod instead.** A failing startupProbe restarts the container rather than
withholding traffic, so on the count-based endpoint one refused artifact keeps
every new pod from ever starting, while the recipes that would have served fine
never receive traffic. Retrain before rolling serve.

2.1.0 moves all three probes off the count-based endpoint — startup and
readiness onto `/v1/health/ready`, liveness onto `/v1/health/live` (see
**Added**) — and the chart, `examples/k8s/` and `docs/deployment/k8s.md` all
point them there. An *already-started* replica that later loses one recipe
stays Ready and keeps serving the rest, and a *new* pod now finishes starting
as long as one recipe loads.

**If your own manifests were copied from the 2.0.0 chart, all three of your
probes are still on `/v1/health`.** Move all three before you upgrade, or one
refused artifact will take every replica out of the Service and then CrashLoop
them.

**That is the picture at startup only. A hot-swap fails silently instead.** When
a skewed artifact lands in an *already-running* server, the previously loaded
model stays in memory — the watcher annotates the load error onto the registry
entry without clearing its `loaded` flag — so the count-based `/v1/health` stays
**200** and no probe fails. Nothing restarts the pod. Only
`/v1/health/details`, which reads the error strings rather than the count,
reports `degraded`, and
`recotem_artifact_load_failures_total{reason="version_skew"}` increments. The
fleet quietly keeps serving the *old* model until the next involuntary restart —
a node drain, an eviction, a scale-up — turns it into the startup case above,
potentially long after the deploy that caused it. Alert on that counter and
scrape `/v1/health/details`; a green `/v1/health` is not evidence the swap
worked. `docs/operations.md` calls this "degraded now, down later".

**Upgrade procedure.**

1. `recotem inspect` every artifact and note which report
   `"best_class": "IALSRecommender"` — only those need work.
2. Upgrade the **train** side first and retrain every IALS recipe on 2.1.0.
3. Wait for the new artifacts to land in the artifact store.
4. Upgrade the **serve** side.
5. Confirm `/v1/health/details` reports `"status": "ok"`.

The full runbook, including the zero-downtime caveat that this upgrade breaks,
is [docs/operations.md](docs/operations.md#irspack-version-skew).

If you upgrade serve first — the default rolling-deploy order — the old IALS
artifact is still on disk, so that recipe comes up `loaded: false` and returns
503 until a 2.1.0-trained artifact replaces it. Non-IALS recipes are unaffected
in themselves. This is a clean, visible outage rather than corruption, but it is
what a rolling deploy does by default, so plan around it.

**Do not reach for `RECOTEM_ALLOW_IRSPACK_VERSION_SKEW=1` here.** It only
downgrades the refusal to a warning and lets the payload reach the
deserializer; the load then fails anyway with the bare `TypeError` the guard
exists to replace. It converts an actionable error into an unattributable one
and buys nothing. The flag is for algorithms that are merely *unverified*, not
for the known IALS break.

**Rollback.** Roll serve and artifacts back together. Once a recipe has been
retrained on 2.1.0, a 2.0.0 serve cannot load its IALS artifact either — the
break is bidirectional. Keep the pre-upgrade artifacts until the upgrade is
confirmed; the default `versioning: append_sha` plus its pointer file makes this
natural — repoint, do not delete. **A recipe using the new `features:` block
cannot be rolled back at all** and must be retrained without the block to run on
2.0.0.

**Unchanged by this upgrade:** signing keys and the key-rotation procedure; the
artifact container itself (magic bytes, `FORMAT_VERSION` 1, and the header
layout — `artifact/__init__.py`, `format.py` and `io.py` are byte-identical
between the two releases);
and every existing recipe, which stays valid as written. Every recipe's
`recipe_hash` does change, but nothing gates on it — see **Changed** below.

**One thing in that area did change.** `src/recotem/artifact/signing.py` is not
byte-identical: a malformed `RECOTEM_SIGNING_KEYS` now exits **8**
(`_EXIT_CONFIG`) where 2.0.0 exited **5** (`_EXIT_ARTIFACT`), on `train`,
`serve` and `inspect` alike (see **Fixed** below). The container format is
untouched and no artifact needs anything done to it — but supervisor, CronJob
or alerting logic that branches on exit 5 to mean "the artifact is corrupt,
retrain it" will stop firing for an environment-variable typo and must learn
exit 8.

### Added

- **`BPRFM` is trainable again, via the new `bprfm` extra.** irspack drops
  `BPRFMRecommender` from its exports when `lightfm` cannot be imported, and
  upstream `lightfm` has shipped no release since 1.17 and does not build on
  Python 3.12 ([lyst/lightfm#709](https://github.com/lyst/lightfm/issues/709)).
  `recotem[bprfm]` installs
  [`lightfm-next`](https://pypi.org/project/lightfm-next/) instead — a
  maintained fork that provides the same top-level `lightfm` module, so
  irspack's import works unchanged. The published Docker image includes it;
  `pip install recotem` alone does not.

  It is an extra rather than a core dependency because `lightfm-next`'s wheel
  coverage is partial: adding it to `dependencies` would have made `pip install
  recotem` fail wherever no wheel matches, on hosts without a C toolchain,
  including for users who never train BPRFM. `lightfm-next==1.19.0` publishes
  wheels for CPython **3.12 and 3.13 only**, and on Linux for **x86_64 only**,
  so `pip install recotem[bprfm]` (and `recotem[all]`, which includes it)
  compiles from source and needs `build-essential` on every linux/arm64 host
  **and on Python 3.14 at every architecture, x86_64 included** — 3.14 being a
  supported interpreter does not mean a BPRFM wheel exists for it. On macOS the
  extension is built without OpenMP, so BPRFM training there is single-threaded.

  **The image does not ship the published wheel even on x86_64.** Upstream's
  `setup.py` compiles with `-march=native`, and their release CI does not
  disable it, so the manylinux wheel carries whatever ISA the build runner had
  — AVX2 and FMA when measured. A distributed image may not have that property:
  it would `SIGILL` on any host older than the builder. The Dockerfile builds
  lightfm from source on both architectures with `LIGHTFM_NO_CFLAGS=1`, which
  drops both `-march=native` and `-ffast-math`; the resulting extension
  disassembles to zero AVX2 and zero FMA instructions. OpenMP is still enabled,
  so training stays multi-threaded. The compiler lives in the builder stage
  only and is not in the shipped image.

- **irspack version-skew guard.** `serve` now checks an artifact header's
  `irspack_version` against the running irspack *before* deserializing, and
  refuses an unverified combination with an `ArtifactError` naming the
  algorithm, both versions, and the remedy. Previously the skew surfaced from
  irspack's C++ layer as a bare `TypeError: __setstate__(): incompatible
  function arguments`, which identified neither the recipe nor the fix. The
  rule is an **allow-list**, not a deny-list: matching major.minor is always
  accepted (patch drift within a minor is tolerated), and a differing
  major.minor is accepted only for an `(algorithm, transition)` pair Recotem
  has empirically verified. Everything else is refused — including artifacts
  whose `best_class` is missing or unreadable, and every future irspack minor
  until it is verified. The affected recipe is marked `loaded: false` (reason
  `version_skew`); serve does not crash and other recipes keep serving.
- `RECOTEM_ALLOW_IRSPACK_VERSION_SKEW` — truthy downgrades the skew check to a
  warning, for operators who know their artifact's algorithm is unaffected.
- `recotem_artifact_load_failures_total` gained a `version_skew` reason label.
- **Feature-aware iALS.** A new optional `features:` recipe block (sibling to
  `source:` / `item_metadata:`) declares item- and/or user-side attribute
  tables — `categorical` (one-hot), `numerical` (standardized), and
  `multi_label` (multi-hot) encodings, plus an implicit bias column — that
  are encoded and fed to `IALSRecommender` during Optuna search and the
  final refit. The mere presence of `features:` turns this on; there is no
  separate flag. `lambda_item_feature` / `lambda_user_feature` are tuned by
  Optuna over a recotem-owned range (`1.0`–`1e6`, log-scale) rather than
  irspack's own `default_suggest_parameter`, because irspack ships no default
  range for them and their `0.0` constructor default is a hard error whenever
  the matching feature matrix is non-empty. The bounds match upstream's only
  feature-aware example (`examples/mind/mind_small_feature_aware_ials.py`);
  the floor is also a float32-Cholesky conditioning floor, because recotem's
  always-on bias column makes the feature Gram exactly singular and
  `lambda_*_feature` is the sole eigenvalue along that direction. See
  `docs/recipe-reference.md#features`.
- **Cold-start serving from side features.** `POST
  /v1/recipes/{name}:recommend` accepts `user_features` to score an unknown
  user from their profile alone; `POST /v1/recipes/{name}:recommend-related`
  accepts `user_features` (profile prior added to an ad-hoc seed history) and
  `item_features` (keyed by seed id, for seed items absent from training).
  Both single and batch verbs support this. A known `user_id`'s supplied
  `user_features` are deliberately **ignored**, not rejected — the learned
  embedding from real interactions strictly dominates a profile prior. A
  request that supplies feature values against a model with no matching
  feature state gets a new `400 FEATURES_NOT_SUPPORTED` rather than a guess.
  Separately, a supplied `numerical` value whose *standardized* magnitude
  (raw value standardized against the column's training mean/std — not the
  raw value itself) is large enough to make irspack's per-request cold-start
  solve itself fail gets a new `400 FEATURE_VALUE_UNUSABLE` rather than an
  unhandled `500` — distinct from `FEATURES_NOT_SUPPORTED` because the model
  and feature side both support cold start here; only this particular value
  does not. The `detail` message describes the standardized value as
  numerically unusable, not the client's raw one, because a column with a
  small enough training std can make an entirely ordinary raw value (e.g.
  `10000`) standardize to an unusable magnitude just as easily as an
  actually-extreme raw value against a normal-sized std. A `numerical` value
  large enough to be meaningless but not large enough to break the solver is
  **not** caught by this and degrades silently as `200` instead — clamping
  that range was a deliberate, deferred modelling decision, not an
  oversight. A non-finite supplied value (`Infinity`/`-Infinity`, or a
  string like `"nan"`) increments `recotem_v1_feature_unknown_value_total`
  rather than degrading invisibly; a missing or otherwise unparseable value
  still degrades silently with no signal, unchanged. See
  `docs/api-reference.md#feature-aware-cold-start`.
- `RECOTEM_MAX_FEATURE_DIM` (default 5000, clamped [16, 100000]) — caps the
  encoded feature dimension per side. The vocabulary is built from the whole
  fetched feature table (so cold-start entities are representable), which
  means encoded dimension scales with **catalog size, not interaction
  count**; `min_frequency` on high-cardinality columns is the only
  recipe-level lever. Per-trial time grows super-linearly, and the exponent
  rises with the dimension: a doubling costs 1.7–1.9× below the default 5,000
  cap, 5.1× from 5,000 to 10,000, and 7.5× from 10,000 to 20,000 — effectively
  the cubic the dense `Fᵀ F` Cholesky suggests, at exactly the step an operator
  takes when the default cap refuses their catalogue. Memory grows
  quadratically; both multiply with `training.parallelism`. See
  `docs/operations.md#feature-aware-ials-sizing`.
- Artifact headers for feature-aware models gain a `features` block
  (`{"version": 1, "item": {...}, "user": {...}}`), inspectable via `recotem
  inspect`. Serve checks this version before deserializing the payload:
  absent → loads (old artifact or non-feature model); present but
  unrecognized → refused (`ArtifactError`, reason `feature_version`) rather
  than risk silently mis-encoding a request's features into the wrong vector
  space.
- New metrics: `recotem_v1_feature_unknown_value_total` (a request's
  categorical/multi_label value was absent from the training vocabulary, or
  a numerical value was non-finite — degrades to an all-zero segment /
  contributes nothing rather than failing the request),
  `recotem_v1_cold_start_requests_total` (cold-start traffic by case), and
  `recotem_v1_feature_unknown_column_total` (a request carried a feature key
  the recipe never declared — counted per request per side, with no
  column-name label, to bound cardinality; the key is silently ignored and
  the request still returns `200`).
- New example: `examples/feature-aware/` — a small interactions CSV, an item
  feature table exercising all three encodings, and a README walking
  train → serve → cold-start `:recommend-related`.
- **Request-body size cap.** `serve` now bounds the raw HTTP request body via a
  `BodySizeLimitMiddleware` before Starlette buffers and JSON-parses it: a
  declared `Content-Length` over the cap is rejected outright, and bodies with
  no `Content-Length` (chunked / streamed) are counted as they arrive so the
  header cannot be omitted to bypass the limit. Over-cap requests get a
  `413 PAYLOAD_TOO_LARGE` in the standard error envelope. Previously an
  authenticated client could make the process buffer and parse a multi-GB body.
- `RECOTEM_MAX_BODY_BYTES` (default 128 MiB, clamped [1 MiB, 2 GiB]) tunes the
  cap. It clears the largest schema-valid *single-verb* body —
  `:recommend-related` tops out near 52 MiB once `user_features` /
  `item_features` are filled to their per-field caps — but deliberately not the
  largest *batch* body: `:batch-recommend` tops out near 196 MiB and
  `:batch-recommend-related` near 13 GiB, the latter beyond even the 2 GiB
  clamp. Batches that large are refused with `413`; raise the cap if you
  genuinely send them. (The pre-feature maximum really was ~72 MiB; the new
  cold-start fields raise the schema-valid ceiling by orders of magnitude.) A
  new `PAYLOAD_TOO_LARGE` error code is added to the v1 API's `ErrorCode`
  union.
- **Cold-start feature-dict key-length caps.** Every feature-mapping KEY is now
  bounded to 1–256 characters (parity with other identifier fields):
  `user_features` column names, the `item_features` outer seed-id keys, and the
  nested per-seed feature keys. Previously only string VALUES were capped and
  `Field(max_length=64)` bounded only the key COUNT, leaving key length
  unbounded. Over-length or empty keys now get a `422`; an over-length key
  reports only its length, never its (possibly huge) text.
- **`GET /v1/health/live` and `GET /v1/health/ready`, so one unloadable recipe
  no longer takes a running fleet offline.** Both are unauthenticated, like
  `/v1/health`. `/v1/health/live` always answers `200 {"status": "alive"}`
  while the process can answer and never reads artifact state — a restart
  cannot fix a missing artifact, since the replacement pod reads the same
  recipes directory and the same store, and each restart drops the models that
  *had* loaded. `/v1/health/ready` answers `200` when at least one recipe is
  loaded and `503` when none is, so a cold fleet still stays out of the Service
  and the first-install guarantee holds. `/v1/health` itself is unchanged —
  still `503` whenever `loaded < total` — and stays the startupProbe path,
  where "every recipe present" is the right gate for a *new* pod.

  In 2.0.0 all three probes polled `/v1/health`, so copying one untrained
  recipe into a running server's recipes directory failed readiness on every
  replica at the next watcher poll (they all read the same directory), dropped
  every endpoint from the Service, and then CrashLooped the pods with no
  self-healing path. The chart, `examples/k8s/` and `docs/deployment/k8s.md`
  now wire `readinessProbe` to `/v1/health/ready` and `livenessProbe` to
  `/v1/health/live`. **Hand-written manifests do not get this for free** — see
  **Upgrading from 2.0.0** above. Full endpoint reference:
  [docs/api-reference.md](docs/api-reference.md).

### Changed

- **A numerical `features:` column with a tiny-but-nonzero training std is
  now treated as zero-variance, like an exactly-constant column.** Previously
  only an exact `std == 0.0` was floored; a column whose values differ only
  by floating-point rounding noise (e.g. `std ≈ 1e-15`) passed that check but
  still divided serve-time standardization by a near-zero denominator,
  turning an ordinary request value into an astronomically large
  standardized one and a false `400 FEATURE_VALUE_UNUSABLE`.
  `build_encoder_state` now floors any std no larger than `1e-8 ×
  max(abs(mean), 1.0)` (relative to the column's own scale) to zero. A
  column caught by this floor degrades exactly like a missing value
  (`feature_zero_variance_column` warning, unchanged) instead of ever
  reaching the standardization divide. This changes training-time encoding
  for any feature table containing such a column; retrain to pick it up. See
  `docs/api-reference.md#feature-aware-cold-start`.
- **Every recipe's `recipe_hash` changes on upgrade, features or not.** The
  hash is computed by JSON-dumping the whole recipe with no `exclude_none`,
  so adding the new optional `features` field emits `{"features": null}` for
  every existing recipe and changes its hash — the same effect
  `item_metadata` already has when absent. Nothing in Recotem compares or
  gates on `recipe_hash` today; it is carried through to the artifact header
  (`recotem inspect`) and the `GET /v1/recipes/{name}` response purely for
  operators' own SIEM/audit rules. It is readable from the `train_done` log
  event, which now carries the real digest (see **Fixed** — earlier 2.1.0
  builds logged a constant `[REDACTED-HEX64]` there). The inference verbs do
  not echo it: `:recommend` returns
  `request_id` / `recipe` / `model_version` / `items` only. If you pin or
  diff `recipe_hash` in external tooling, expect every recipe to show a
  changed hash on this upgrade even though nothing about the recipe's
  behavior changed.

- **irspack upgraded from 0.4.2 to 0.5.2.** irspack 0.5.0 adds feature-aware
  iALS, cache/Eigen performance work, and a reworked tuning API. Recotem drives
  Optuna itself and does not call `BaseRecommender.tune`, so none of irspack's
  documented breaking changes (`tune_with_study` removal, `fixed_params` →
  keyword arguments, `random_seed` → `tuning_random_seed`) affect Recotem.
  **IALS and BPRFM models trained on 0.4.x must be retrained** — see below.
  The subsequent 0.5.1 (parallelised feature-aware iALS) and 0.5.2
  (`FeatureRidgeCholeskyError`, a dedicated exception for a feature-ridge
  Cholesky failure) both land in the feature-aware path, which the new
  `features:` block *does* reach — recotem's search prunes the Optuna trial
  on that failure rather than aborting the run. They were verified not to
  change the serialised model: for six of the seven algorithms Recotem can
  build, an identically-trained recommender pickles to a byte-identical payload
  under 0.5.0 and 0.5.2 (SHA-256 compared), `IALSModelConfig.__setstate__`
  keeps its 10-element arity, and artifacts interchange in both directions with
  bit-exact recommendation scores. `BPRFM` is the seventh and was not in that
  comparison; it does not need to be, because no released Recotem could produce
  a BPRFM artifact on 0.5.0 or 0.5.1 — the algorithm and the 0.5.2 pin ship
  together in this release. That comparison was run on
  non-feature-carrying payloads only, so it does not by itself certify a
  0.5.0-trained *feature-aware* artifact on 0.5.2; train and serve on the same
  irspack minor, as the skew guard already requires. **No retrain is needed
  for a 0.5.x → 0.5.2 upgrade.**
- **scikit-learn is now a direct, range-pinned dependency** (`>=1.8,<1.10`).
  It was already reachable transitively via irspack, which asks only for
  `>=0.21.0`. `TruncatedSVDRecommender` pickles an sklearn estimator into the
  artifact payload, and sklearn does not guarantee correctness when unpickling
  across its own minors (`InconsistentVersionWarning`: "might lead to breaking
  code or invalid results"). The range keeps train and serve inside a tested
  window and forces a deliberate bump plus retest at the next sklearn minor.
  **A range narrows this axis but does not close it:** two installs inside the
  range can still differ, and the irspack version-skew guard does not check the
  sklearn axis at all. If you need TruncatedSVD artifacts to be reproducible
  bit-exact, pin sklearn exactly or build train and serve from the same lock
  file.
- **`recotem validate` now rejects a recipe whose `schema:` names a column the
  source does not have.** It used to exit **0** and leave the failure to
  `train`: `probe()` only confirmed the source was reachable, so the documented
  pre-flight gate green-lit recipes `train` then refused with exit 3. Validate
  now reads the column names where that is cheap — a CSV header row
  (`nrows=0`), a Parquet footer schema — and applies the same rule the train
  path does, so both verbs print the same message. Where the answer is not
  cheap it says so rather than implying a check it did not run: `http(s)://`
  paths are skipped (their `sha256` pin and byte cap are fetch-time controls a
  probe must not spend twice), and `bigquery`, `sql` and plugin sources are
  reported as "not checked". Feature sources are exempt — a feature table
  legitimately lacks the interaction columns.

  **This changes `validate`'s exit code, and two upgrade scenarios break on
  it**, both because validate now performs I/O it did not perform before:

  - *A validate-only CI gate run against data the scheduled `train` will not
    see.* If the column lands in the real table later, the PR is now red while
    the scheduled train stays green — the reverse of the previous behaviour.
    Point the gate at data carrying the declared columns, or move the gate to
    where the train-time data is visible.
  - *A source the process cannot read.* A CSV at `chmod 000` previously printed
    `DataSource: probe OK (csv) [source]` followed by `Validation passed.` and
    exited 0. It now prints the same `probe OK` line and then
    `Schema column check failed [source]: ... Permission denied`, exit **3**.
    Reading `probe OK` as the verdict was always wrong; nothing before this
    release made that visible.

  `recotem schema` changes alongside it. `training` now appears in the emitted
  schema's `required` list — it was always required by the loader, and the
  `default_factory` that made it look optional could never succeed — and the
  document now declares its `$schema` dialect so editors need not guess the
  draft. An editor validating against the 2.0.0 schema accepted recipes the
  product rejects.
- **`recotem validate` labels each probed data source.** Because a recipe may
  now declare feature-side sources (`features.item.source` /
  `features.user.source`) alongside the top-level `source:`, the probe output
  tags which one it is (`DataSource: probe OK (csv) [source]`, `DataSource probe
  failed [features.item.source]: ...`) and the missing-discriminator message
  reads `source is missing the 'type' discriminator.` rather than `Recipe
  source is missing the 'type' discriminator.`. Tooling that greps the exact
  `validate` output lines should update. This labelling change does not itself
  move an exit code, but `validate`'s exit codes **do** change in 2.1.0 — see
  the schema-column entry above.

- **`recotem validate` now rejects an algorithm name that does not resolve.**
  A recipe naming a nonexistent algorithm passed `validate` at exit 0 and then
  failed `train` at exit 4 with `code=unknown_algorithm` — after the whole
  dataset had been fetched, cleansed and split. `validate` now resolves
  `training.algorithms` against the same table `train` uses and exits 4 with
  the same message. Alias resolution is unchanged and still case-insensitive,
  so `toppop`, `TopPOP`, `ials` and `TopPopRecommender` all keep passing; what
  changes is that a typo, or a plausible-looking name Recotem does not ship
  (`SLIM`, `ALS`, `ItemKNN`), is now caught before any I/O. **A `validate`-only
  CI gate can go red on an upgrade** where it was previously green and the
  scheduled `train` was already failing.

- **`BPRFM` is offered only when it can actually be trained.** It is gated
  behind `lightfm`, which irspack imports unconditionally and which is absent
  from a default install. Availability is now decided by asking irspack rather
  than by a hard-coded name: without the `bprfm` extra, `BPRFM` is gone from
  the suggestion list the unknown-algorithm error prints, and naming it
  explicitly is refused with the same exit 4 as any other unavailable algorithm
  rather than failing mid-train; with the extra installed it is a normal
  choice. (Earlier in this release BPRFM was withdrawn outright, on the
  assessment that no Python 3.12 build of lightfm existed. That was true of
  upstream lightfm and remains so — see the `bprfm` extra under **Added**.)

- **`training.parallelism` and the Optuna budget see a de-duplicated algorithm
  list.** Because alias resolution is case-insensitive, `algorithms: [toppop,
  TopPOP]` was writable — and the suggestion list printed by the
  unknown-algorithm error offered both `CosineKNN` and `CosinekNN`, so it could
  be arrived at by copying. `_compute_budgets` divided by `len(class_names)`
  while keying a dict by class name, so the duplicate silently consumed a share
  of the budget that no trial then used. Measured on one dataset with
  `parallelism: 1` and `n_trials: 10`: `[TopPop]` completed 10 trials,
  `[toppop, TopPOP]` completed **5**. Duplicates are now collapsed before
  budgeting, with a `duplicate_algorithms_collapsed` warning. **The number of
  completed trials, `tuning.tried_algorithms` in the artifact header, and hence
  the selected model can all differ from 2.1.0-dev for a recipe that had
  duplicates** — in the direction of getting the trials you asked for.

- **The GHCR image is now gated on the test suite.** `docker.yml` gained a
  `test` job (ruff plus `pytest tests/unit tests/integration tests/fuzz` on
  3.12, the version the image is built from) and `build` — the only job that
  pushes — now needs it alongside `smoke` and `trivy`. Previously nothing
  connected the container push to `test.yml`: on the registry today `latest`,
  `main` and `sha-afa9bec` all resolve to one digest, so **an untested `main`
  tip moving `:latest` was observed behaviour, not a hypothetical**. From
  2.1.0, `ghcr.io/codelibs/recotem:latest` — which `compose.yaml` and
  `docs/getting-started.md` both point users at — carries that guarantee.
  Registry permissions were narrowed in the same change: `packages: write` now
  sits on `build` alone rather than at workflow top level, where `smoke` and
  `trivy` inherited a write scope neither used.

- **Chart upgrade semantics change when `hpa.enabled: true`.** The Deployment
  no longer renders `spec.replicas` in that case, so a `helm upgrade` stops
  resetting the replica count the autoscaler had chosen. Under 2.0.0's chart,
  an HPA that had scaled to 8 was knocked back to `replicaCount` on every
  upgrade and had to climb again. If you were relying on an upgrade to reset
  scale, use `kubectl scale` or a `minReplicas` change instead.

- **The PodDisruptionBudget's selector is narrowed to the serve component.**
  Both the PDB and the serve pod template gained
  `app.kubernetes.io/component: serve`; the PDB previously selected on
  name+instance alone and so also counted train CronJob pods as healthy
  members of the budget. With `pdb.enabled: true` and one serve replica, a
  running train pod raised allowed disruptions from 0 to 1 and a node drain
  could evict the only pod serving traffic. **On the first `helm upgrade` the
  budget selects nothing until the rollout has replaced every serve pod with
  one carrying the new label** — a window in which serve is unprotected but no
  request is dropped. Overriding an `app.kubernetes.io/` label via `podLabels`
  produces a duplicate key that Kubernetes resolves last-wins, which would
  detach the PDB permanently; do not.

- **`networkPolicy.extraEgress` is a new values key and now takes effect.** It
  was documented but discarded during rendering. The default policy still
  allows only 53/UDP, 53/TCP, 443/TCP and 8080/TCP, and still selects train
  CronJob pods, so a `source.type: sql` or plain-`http://` source inside the
  cluster still times out silently on a default install — that part is
  deliberate, and `values.yaml` and `docs/deployment/k8s.md` say so. What
  changed is that the documented escape hatch works.

### Fixed

- **A `serve` replica that found no recipe files at all reported itself
  Ready.** `/v1/health/ready` computed `ready = total == 0 or loaded_count > 0`,
  so a recipes directory holding no `*.yaml` file passed readiness on the
  `total == 0` half — the pod joined the Service and answered `404
  RECIPE_NOT_FOUND` to every request. Measured on a 3-node cluster with the
  shipped chart and `recipes.source: objectStore` whose sync container exited 0
  having copied nothing: both replicas `1/1` Ready, both in the Service's
  endpoints with `ready=true`, `restartCount 0`, no event, and nothing above
  INFO in the log (`recipes_directory_loaded_lenient ok=0 errors=0`, then
  `startup_artifact_load_complete total_recipes=0`). A ConfigMap whose keys are
  not `*.yaml` and an empty PVC produce the same fleet, and so does a directory
  in which every recipe file failed to *load* (those are excluded from `total`)
  -- for instance a DataSource plugin whose `type_name` collides with a
  builtin, which makes every recipe using that type fail with
  `recipe_load_error_skipped` and leaves
  `recipes_directory_loaded_lenient ok=0 errors=1` while the process keeps
  answering health checks. Two unrelated causes, one end state, which is why
  the predicate is `loaded > 0` rather than "are there recipe files". The
  lenient loading itself is correct and unchanged: one malformed recipe must
  not take down a server hosting nine good ones. What was wrong is that
  readiness could not tell 9-of-10 from 0-of-10.

  The short-circuit was there on the theory that an empty registry is the
  boot-time state before the watcher's first poll, and that answering 503 would
  deadlock startup against registration. Neither holds: `create_app` reads the
  recipes directory synchronously before uvicorn accepts a connection, so an
  empty registry means the directory really is empty; and registration needs no
  traffic, which is exactly why the documented first-install flow works — 503,
  `train` writes the artifact, the watcher picks it up, the probe passes. An
  empty registry is now `503 {"status":"unready","total":0,"loaded":0}`, the
  same answer a cold artifact store already gave, and startup emits a
  `recipes_directory_empty` **warning** naming the directory. `/v1/health` is
  unchanged: it answers "is every registered recipe present?" and keeps its
  count-based contract. Unready is deliberately not a refusal to start: the
  watcher still picks up a recipe that appears after startup, and the replica
  becomes ready the moment one loads -- measured end to end, 503 on an empty
  directory, then 200 and a serving `:recommend` within one poll of a recipe
  being dropped in, with no restart.

  **What this deliberately does not change, because readiness is the wrong
  instrument for it.** A replica that loads *some* of its recipes stays Ready,
  and it should: pulling a pod out of the Service because one recipe of ten is
  broken serves nobody. But that partial case is quieter than it looks. A
  recipe that loads and has no artifact is counted in `total`, so `loaded <
  total` and `/v1/health` answers **503 `degraded`**. A recipe that fails to
  *load* is excluded from `total` instead, so `loaded == total` and
  `/v1/health` answers **200 `ok`** — measured, with one good recipe and one
  unloadable one: `200 {"status":"ok","total":1,"loaded":1,"skipped":1}`, the
  good recipe serving `200` and the broken one `404` forever. Liveness,
  readiness, the `/v1/health` status line and the `loaded` count are all green
  there. **The signal for that case is `skipped > 0`** (equivalently `errors`
  in `recipes_directory_loaded_lenient`, and the `recipe_load_error_skipped`
  warning), and it needs no product change — `skipped` is already in the
  `/v1/health` body. So: **`loaded == 0` is a readiness question and is now
  answered as one; `skipped > 0` is an alerting question and stays one.**

- **An over-cap model was reported as a damaged file when `recotem serve`
  started, and as `size_cap` when the same file arrived by hot-swap.** The
  classifier that resolves an `ArtifactError` to a `reason` label lives in
  `serving/watcher.py` and only the watcher called it. `serving/app.py`'s
  startup path took its label from a hard-coded string per `except` block, and
  had no branch for either size cap: over `RECOTEM_MAX_PAYLOAD_BYTES` came out
  as `parse` (the payload length is checked inside `parse_header_from_bytes`)
  and over `RECOTEM_MAX_ARTIFACT_BYTES` as `read` (the file length is checked
  inside the read helper). Measured on one 8,559,920-byte artifact against a
  1 MiB payload cap in one process: present at startup it reported
  `"reason": "parse"`, dropped in afterwards for the watcher it reported
  `"reason": "size_cap"` — same file, same cap, two labels.

  Startup is the path that matters here. An over-cap artifact is met on a
  fresh deploy or a pod restart, where the pod simply never becomes ready, and
  `reason` is a Prometheus label on `recotem_artifact_load_failures_total` —
  so both the log line and the alert said `parse`, which reads as a corrupt
  artifact and sends the operator to re-train or check their signing key when
  the remedy is to raise the cap or shrink the model. Both branches now route
  through a `_size_cap_or` helper keyed on `SIZE_CAP_MSG_MARKER`, the
  discriminator that already exists for exactly this purpose. An absent file
  is still `read` and a corrupt one still `parse`.

  `docs/operations.md`'s documented `reason` enum for
  `recotem_artifact_load_failures_total` did not list `size_cap` at all, so it
  had been stale since the label was introduced. It does now, and a test reads
  the enum and the two source files together so it cannot drift again.

- **The sdist shipped nine example READMEs and none of the files they tell you
  to run.** `[tool.hatch.build.targets.sdist] include` read `["src/recotem",
  "README.md", "LICENSE"]`. Hatchling's include patterns are gitignore-style,
  so the unanchored `README.md` matched a `README.md` at *any* depth: the
  archive carried `docs/README.md` and all nine `examples/*/README.md`, while
  no pattern named `examples`, so not one `recipe.yaml`, CSV, or manifest came
  with them. Unpacking the sdist gave you nine example directories consisting
  entirely of instructions for files that were not there. Every pattern is now
  anchored with a leading `/` and `/examples` is included; all 28 tracked
  files under `examples/` ship, and `docs/README.md` — which was only ever
  collateral of the unanchored glob — no longer does.

  **The wheel deliberately still ships no `examples/`.** A wheel unpacks into
  `site-packages`, where an `examples/` directory would be neither findable by
  the relative path a document could name nor cleanly removable on uninstall,
  and it would be indistinguishable from an importable top-level package. Lean
  wheel, complete sdist: `pip download recotem --no-binary :all:` is the
  supported way to obtain the examples without a checkout, and it now works.

  `tests/unit/test_packaging_claims.py` holds two guards, each failing under a
  one-line revert of the thing it protects: the sdist include patterns must be
  anchored and must name `/examples`; and every example file that a fenced
  command in shipped prose tells you to run must exist. Both read whole files
  and assert they matched something, so a rename cannot switch them off
  silently. The file's docstring records that it owns the *distributions* and
  asserts nothing about `docs/getting-started.md`'s Path B, which belongs to
  the guard added alongside that section's rewrite.

- **A BPRFM artifact trained successfully and then could not be served.** The
  FQCN allow-list named `BPRFMRecommender` but neither `BPRFMTrainer` nor
  `lightfm.lightfm.LightFM`. irspack's early-stopping base keeps the fitted
  trainer as an attribute — `get_score` reads `self.trainer.fm` — so both are
  in every BPRFM payload, along with the five `numpy.random` RNG-state helpers
  reached through LightFM's `RandomState`. `recotem train` therefore exited 0
  and wrote a signed artifact that `recotem serve` refused with `class not
  allowed: irspack.recommenders.bpr.BPRFMTrainer`, one deploy and one process
  later. Anyone who installed lightfm themselves — the natural response to
  lyst/lightfm#709 — hit this.

  The five RNG-state helpers sit under the `numpy.random` deny-prefix. Rather
  than letting exact allow-list entries beat the deny-list generally, which
  would have given all ~40 entries and every future one the power to re-open a
  denied subtree such as `numpy.lib`'s file-IO constructors, they are declared
  in a separate `_DENY_PREFIX_EXEMPTIONS` set that is the single mechanism
  outranking the deny-list. Its exact contents are pinned by a test, so it
  cannot grow without a failure naming the addition, and everything else under
  `numpy.random` — including `__generator_ctor`, in the same module as two
  exempted entries — stays denied.

  The gap survived because the test that was supposed to catch it,
  `test_bprfm_class_is_explicitly_allowed`, asserted only the top-level class,
  and the end-to-end roundtrip test that would have caught it was skipped for
  want of lightfm. Both are fixed: the roundtrip now runs for all seven
  algorithms, and the unit test names all three classes.

- **`docs/security.md`'s FQCN list is now checked against the code.** It is the
  reader's map of what an artifact may deserialize, so a stale entry is a false
  security claim; it had drifted before with nothing comparing the two. A test
  parses the documented list and asserts set equality with `_ALLOWED_CLASSES`.

- **LightFM's no-OpenMP warning no longer fires on every recotem invocation.**
  macOS builds of lightfm ship without OpenMP and warn at import time, and
  irspack imports lightfm from `recommenders/bpr.py`, so with the `bprfm` extra
  installed the warning reached `recotem serve`, `recotem inspect`, and even a
  TopPop `recotem train` — logged through the `py.warnings` bridge, making
  every run look like it had a problem. It is filtered by message at the same
  layer that installs the IPython stub; every other `UserWarning` still
  reaches the operator.

- **Following the `RECOTEM_ALLOWED_HOSTS` guidance in `docs/deployment/k8s.md`
  put serve into CrashLoopBackOff.** All three probes send `Host: localhost`,
  and `TrustedHostMiddleware` 400s anything not on the list. The chart's
  ingress-derived branch knew this and prepended `localhost`; the explicit
  `env.RECOTEM_ALLOWED_HOSTS` override branch rendered the operator's value
  verbatim — and the override is the branch the document tells operators to
  use, with an example that lists only external hostnames. Every readiness and
  liveness check then failed with nothing in the application log but ordinary
  rejected requests. `localhost` is now prepended to whichever list the chart
  renders, deduplicated, and the document says who owns it when you write the
  env var outside the chart.

- **`kubectl apply -f examples/k8s/` never reached 2/2 on a ReadWriteOnce
  volume.** `serve-deployment.yaml` shipped `replicas: 2` with a
  `kubernetes.io/hostname` spread at `whenUnsatisfiable: DoNotSchedule`, while
  `examples/k8s/README.md` offered `ReadWriteOnce` for a single-node cluster.
  RWO binds every mounting pod to one node and the hard spread demands two, so
  the second replica stayed Pending indefinitely. The constraint is now
  `ScheduleAnyway`, and both the README and the manifest say what that costs
  (two replicas, no node-failure tolerance) and how to harden it with RWM.

- **Every shipped pod spec was refused by the `restricted` Pod Security
  Standard.** Neither the chart nor `examples/k8s/` set `seccompProfile`,
  which that profile requires — although both were otherwise
  restricted-clean. All five pod specs now set `RuntimeDefault`.

- **The regression guard for the GA4 pruning fix was blind to half of it.**
  `tests/unit/test_example_bigquery_recipes.py` attributed `--` line comments
  to their enclosing paren block, and the same change that added the guard
  added a comment explaining why the `_TABLE_SUFFIX` predicate is required —
  naming both `_TABLE_SUFFIX` and `@lookback_days`. Prose about the rule
  satisfied the check for the rule, so the outer predicate, which governs most
  of the bytes scanned, could be deleted with the suite still green (the inner
  one was caught). Line comments are now blanked before scope analysis, and a
  test deletes each predicate in turn and asserts the guard fails on both.

- **The shipped examples still offered `BPRFM`.** Four `# Choices:` comments
  listed it, while the CHANGELOG said the choice had been withdrawn and
  `docs/recipe-reference.md` said not to put it in a recipe. A reader copying
  the comment — which is where the list is first met, before the reference —
  got exit 4: irspack gates `BPRFMRecommender` behind `lightfm`, which has no
  Python 3.12 release. The comments now list what can be constructed, and a
  test checks every shipped `# Choices:` line against
  `constructible_class_names()` so the two cannot drift again.

- **Four documentation statements contradicted the product.**
  `docs/data-sources/sql.md` said `query_parameters` is subject to
  `${RECOTEM_RECIPE_*}` expansion; both `query` and `query_parameters` are on
  the loader's no-expand list, deliberately, because expansion into a SQL
  string is an injection path — a `${...}` there reaches the database as those
  literal characters. `docs/operations.md` filed both `--dev-allow-unsigned`
  misuses under exit 2 while the code exits 8 and says in its own docstring
  that it does, and scoped `RECOTEM_ENV` to `serve` although `train` gates the
  same flag on it. `docs/deployment/docker.md` showed `start_period: 15s`
  against the shipped `compose.yaml`'s `60s`. All four verified by running
  them.

- **The concurrent-body memory estimate under-counted by 2.2x.**
  `docs/operations.md` gave `peak ≈ idle + N × body × 1.1` and concluded that
  roughly 28 maximal requests fit in the chart's default `4Gi`. Its own first
  table row already said 3.35x. The coefficient came from rows 2-4, each of
  which starts from an `RSS before` the row above had already inflated, so
  their apparent per-request cost is arena that was going to be reused anyway.
  Re-measured with the server restarted before each run: 3.1-3.3x, matching
  that first row — 207 MB per 63.5 MiB request, 418 MB per 127 MiB request. The
  formula now uses 3.3 and the 4Gi figure is **8**, not 28. Under-estimating
  this shows up in production as an OOMKill.

- **`dim² × 8` is a floor for feature-aware memory, not an estimate.** The page
  said it "closely tracks" the measured column; against peak-RSS increases of
  287 MB / 960 MB / 3.5 GB at 5,000 / 10,000 / 20,000 dimensions it runs 10-43%
  low, worst at the default cap.

- **`training.parallelism` guidance was right about IALS and wrong about
  everything else.** "Raising it is as likely to cost time as to save it" held
  for IALS, which already saturates the box; the other learners have short
  trials with room to spare. Median wall time at `parallelism: 1` vs `8` on the
  same 100k-row fixture: `CosineKNN` 3.66 s → 1.99 s (1.84x), `RP3beta` 4.07 s
  → 2.15 s (1.89x), `DenseSLIM` 1.42x, `TruncatedSVD` 1.31x, `TopPop`
  unchanged. Peak RSS rises with it, so the gain is bought with memory. The
  advice is now per-algorithm rather than blanket.

- **The `RECOTEM_MAX_FEATURE_DIM` error still said the cost was cubic**, which
  the same round's documentation correction had already replaced with a
  measured exponent. That message is what an operator reads at the moment they
  decide whether to raise the cap. A test now ties the two together. (The flat
  `dim^2.4` figure that correction introduced was itself superseded later in
  this same release — the exponent rises with the dimension; see the
  `RECOTEM_MAX_FEATURE_DIM` entry above for the per-doubling measurements.)

- **`data_stats` records the size of the held-out set.**
  `n_heldout_interactions` and `n_heldout_users` now appear in the artifact
  header and in the `split_done` log line. The search metric is computed over
  exactly those interactions, so the count is what decides whether the winning
  algorithm was chosen on signal or on noise — and nothing reported it. On a
  25-user tenant with 50 held-out interactions the search shipped a model whose
  true recall@10 (0.0600) fell below a popularity baseline (0.0867), while the
  algorithm it ranked last scored 0.3600; the run exited 0 and served 200s.
  `docs/operations.md` gains **Choosing a model on a small dataset** with that
  measurement, the held-out sizes of the shipped examples for scale, and the
  baseline comparison that catches it. The number is reported rather than
  thresholded: any cutoff that flagged that tenant also flags the tutorials.

- **The PostgreSQL data source could not run a single query.** `fetch` opened
  the connection with `stream_results=True` and only then issued its two
  session-setup statements. With that option psycopg runs every statement as
  `DECLARE ... CURSOR FOR <stmt>`, and PostgreSQL cannot declare a cursor over
  `SET`, so `SET TRANSACTION READ ONLY` failed with `syntax error at or near
  "SET"` and every `source.type: sql` recipe against PostgreSQL exited 3
  before a row was read. Session setup now runs before streaming is enabled;
  read-only enforcement and server-side cursors both still apply, verified
  against a live PostgreSQL 17 (1,920 rows fetched, `stream_results` still
  `True` on the query connection, `CREATE TEMP TABLE` still refused with
  `ReadOnlySqlTransaction`). MySQL, MariaDB and SQLite were never affected —
  measured, not assumed. The failure was invisible to CI, which has no live
  PostgreSQL, so the regression test asserts the *ordering* instead: session
  setup must not see a connection that already carries `stream_results`.

- **A failed session-setup statement named only SQLAlchemy's wrapper class.**
  The message deliberately withholds `str(exc)` because driver exceptions can
  embed DSN userinfo, but `ProgrammingError` alone cannot distinguish a
  missing grant from a malformed statement — an operator hitting the bug above
  had no way to reach the cause. The DBAPI exception's class is now named too
  (`ProgrammingError (psycopg.errors.SyntaxError)`); a class name cannot carry
  a credential, and a test asserts the DSN stays out.

- **The GA4 BigQuery example scanned the entire export on every run, and
  `lookback_days` did nothing about it.** The per-user activity subquery in
  `examples/ga4-bigquery/recipe.yaml` referenced `analytics_123.events_*`
  without a `_TABLE_SUFFIX` predicate of its own. BigQuery prunes wildcard
  tables per *statement*, so that one unpruned reference dropped pruning for
  the whole query and demoted the outer `BETWEEN` to a plain row filter.
  Measured against `bigquery-public-data.ga4_obfuscated_sample_ecommerce`
  (92 days, 3.34 GiB) with dry runs, the shipped form scanned a constant
  **1,029,558,211 bytes** for a 1-day, 7-day and 31-day window alike, while the
  fixed form scans 6,010,287 / 62,536,522 / 277,526,167 bytes respectively.
  The overrun grows with how much history the export holds, not with
  `lookback_days`: roughly 3.7x at 92 days, 12x at a year, 24x at two years —
  charged on every run of a recipe the repository ships with a daily training
  CronJob and explicitly invites you to copy (`Replace analytics_123 with your
  GA4 export dataset name`). The same omission also changed the meaning of
  `min_events`, which counted lifetime events rather than events in the window,
  admitting users who last engaged years ago. Nothing on the Recotem side
  bounds a BigQuery scan — no `maximum_bytes_billed`, no row cap — so the
  correctness of the shipped example is the only thing standing between a copy
  and the bill. `tests/unit/test_example_bigquery_recipes.py` now checks every
  shipped BigQuery example statically: each wildcard table reference must carry
  a `_TABLE_SUFFIX` predicate in its own scope, and that predicate must be the
  one the lookback parameter moves.
- **`docs/data-sources/bigquery.md` offered a `REGEXP_EXTRACT` snippet that
  cannot run.** It matched on a bare `page_location`, which is not a column in
  the GA4 export — it lives inside the `event_params` array, as the complete
  query a few lines above shows. Copied as printed the snippet fails with
  `400 Unrecognized name: page_location`.
- **The BigQuery error table split 403 and 404 in a way that misdiagnoses a
  typo.** BigQuery does not disclose whether a resource exists to a caller who
  cannot see it, so a mistyped *dataset* or *project* returns
  `403 Access Denied: ... or perhaps it does not exist`, not 404. Only a
  mistyped *table* inside a readable dataset returns 404. The table now says
  so, instead of sending operators to fix IAM for a spelling mistake.
- **The ADC failure message ran two sentences together with a doubled period**
  (`... for more information.. Ensure Application Default Credentials ...`),
  because the wrapped Google exception already ends in one.
- **Load-error redaction ate the scheme allow-list, so a path-scheme mistake
  became unreadable.** `sanitize_load_error`'s URI pattern ended in `\S+`, which
  matched the bare schemes in Recotem's own help text as readily as a real
  object-store URI. An operator who wrote `output.path: http://...` got back
  `uses scheme '<redacted-uri> which is not supported. Allowed: (bare path),
  <redacted-uri> <redacted-uri> <redacted-uri> file://, <redacted-uri> s3://`
  — the offending scheme gone along with its closing quote, and the allow-list
  surviving only where the regex happened not to reach (`file://` is not in
  the alternation; `s3://` sat at end-of-string). Whether the mistake was
  legible depended on which wrong scheme you picked: `ftp://` came through
  intact for the same reason. Worse, `<redacted-uri>` is 14 characters and
  `gs://,` is 6, so redaction *grew* the message — the input-path variant went
  from 163 to 201 characters and then lost its tail to the 200-char cap that
  redaction had just pushed it over, defeating the budgeting work in the same
  release. The pattern now requires one non-delimiter character after `://`, so
  a scheme with no bucket or key behind it is left alone and real URIs are
  redacted exactly as before.
- **The release guard claimed to check "every version declaration" while
  missing the one that decides which image ships.** `check-release-tag.sh`
  compared `Chart.yaml`'s `version:` and `appVersion:` but not
  `values.yaml`'s `image.tag` — and `appVersion` is only a fallback:
  `recotem.image` renders `.Values.image.tag | default .Chart.AppVersion`, and
  values.yaml always pins a tag, so the key being checked never reaches a
  cluster and the key that does was unchecked. Bumping everything except
  values.yaml and running the guard printed `OK: v2.1.0 is a final release and
  matches every version declaration.` with rc 0, while `helm template` rendered
  `image: ghcr.io/codelibs/recotem:2.0.0`. Both `guard` jobs
  (`publish.yml`, `docker.yml`) run only this script, and the release
  procedure's final pre-tag gate does not run the pin sweep that would have
  caught it, so a chart tagged for a new release could ship pulling the
  previous image with nothing red. The guard now reads `image.tag`, refuses a
  values.yaml that omits it (a vacuous check being worse than a missing one),
  and its success message names the four files it actually checked instead of
  overclaiming.

- **A recipe-load failure could spend its whole 200-character budget on the
  recipe path and report no reason.** The string was composed as `recipe load
  failed: Recipe '<absolute path>' failed validation: - <field>: <message>`,
  naming the recipe twice — once as a basename, once as a full path — before
  reaching the part an operator needs. On a deep directory with a 64-character
  recipe name (the schema maximum) the message reached 284 characters and the
  cut landed inside the path, so `/v1/health/details` showed which file failed
  and nothing about why. The reason is now composed to fit: the same case
  surfaces at **183 characters, untruncated**, with the offending field and its
  validation message intact. The irspack version-skew message keeps both
  version numbers and its verdict inside the budget under the same conditions.

- **An artifact was never bound to the recipe serving it.** Every artifact
  header records the `recipe_name` it was trained for, but nothing on the serve
  side compared it with the recipe whose `output.path` the file was read from.
  A correctly-signed artifact therefore loaded under *any* recipe pointing at
  it, with no signal anywhere: the swap logged at INFO, `/v1/health` and
  `/v1/health/details` both said `ok`, and `/v1/recipes/{name}` reported the
  *other* recipe's `best_algorithm`, `best_params` and `recipe_hash`. Four
  recipes aimed at one artifact all reported success. HMAC does not catch this —
  both artifacts are signed by the same key ring, which is the point of a key
  ring — so the realistic trigger is mundane: copy a recipe, forget to change
  `output.path`, and two training runs overwrite one file, after which one
  endpoint serves the other's model permanently. Both load paths (serve startup
  and the watcher's hot-swap) now refuse a header whose `recipe_name` names a
  different recipe, with `reason="recipe_name"` on
  `recotem_artifact_load_failures_total` and an error naming both recipes and
  the remedy. A refused hot-swap keeps the previous model serving and flips
  `/v1/health/details` to `degraded`. A header carrying **no** `recipe_name`
  (pre-2.0 artifact) still loads — an absent field is not evidence of a
  mismatch — and logs `artifact_recipe_name_absent_from_header`.
- **A schema violation was reported as a YAML parse error.** Serving labelled
  every rejected recipe file "YAML parse", producing the self-contradictory
  `recipe YAML parse error ... failed validation: - training.metric: ...` on
  `/v1/health/details`. The file parsed; the schema rejected it, and an operator
  reading that goes looking for a syntax error that does not exist. Startup and
  both watcher-rescan paths now distinguish the two: a genuine `yaml.YAMLError`
  still reads "YAML parse failed", everything else reads "recipe load failed"
  and lets the rest of the message name the field, the security check, or the
  OS error. The `skipped` accounting is unchanged — a rejected file is still
  excluded from the readiness `total`.
- **Training progress bars owned stdout, so a redirected train log captured
  nothing else.** `recotem train recipe.yaml > train.log` wrote 4.8 KB of carriage returns and
  block-drawing characters into the file and not one of the run's 47 structured
  log events, which went to stderr. The shipped examples in
  `docs/deployment/cron.md` all redirect with `2>&1`, so they did capture the
  log — but every cron run interleaved those 4.8 KB of terminal control codes
  into it, which is what a log aggregator then ingests. The
  bars come from `fastprogress`, pulled in transitively by irspack, and were
  drawn for a redirected stdout because fastprogress's own gate, `printing()`,
  evaluates `getattr(stdout, 'isatty', False)` — which yields the bound method
  rather than calling it, so the check is truthy for pipes and files alike.
  `--quiet` and `RECOTEM_LOG_FORMAT=json` were both ignored by it as well.
  `run_training` now silences fastprogress unless stdout is a real terminal and
  neither `--quiet` nor an explicit `RECOTEM_LOG_FORMAT=json` is in force, so a
  redirected run captures the structured log alone. An interactive `recotem
  train` renders exactly as before.
- **Four failures were reported under the wrong exit code.** The exit-code
  table is what cron wrappers and CronJob retry policies branch on, so a
  permanent failure reported as a transient one is retried forever.
  - A SQL DSN refused by the SSRF guard exited **7** for six of its ten
    routing forms (netloc host, `?host=`, `?hostaddr=`, IPv6 literal,
    link-local, unresolvable hostname) and **3** for the other four
    (`?service=`, `?unix_socket=`, absolute-path host, no host) — one guard,
    one verdict, two answers, with `code=datasource_error` contradicting the
    exit code in the structured log. The guard shares its IP check with the
    HTTP fetcher and chained that `HttpFetchError`, which the exit mapper
    walks; a SQL DSN is not an HTTP fetch, so the cause is no longer chained
    and all ten forms now exit **3** as `docs/data-sources/sql.md` always
    documented.
  - A `sha256` mismatch on a local or object-store `source.path` exited **7**,
    an `HttpFetchError` for a file never fetched over HTTP. It now exits **3**
    alongside the byte-cap and read failures guarding the same read. An
    `http://` / `https://` mismatch still exits 7 with the rest of that fetch
    pipeline.
  - The `train_error` event read `code` off any exception carrying that
    attribute, so an unreachable `training.storage_path` reported SQLAlchemy's
    documentation-shortlink slug (`code="e3q8"`) and, because `exc_info` keys
    off `internal_error`, suppressed the traceback as well. `code` is now read
    only when a recotem class in the exception's MRO declares it, so every
    recotem subcode still appears and a third-party attribute of the same name
    never does.
  - An `output.path` on an object store with no usable credentials discarded
    the trained model and exited **1** with the SDK's frames in the log, while
    the local equivalent already exits **8** through the per-recipe lock's
    permission check. It now exits **8** with
    `code="artifact_write_credentials"` and a message naming the destination
    and the credential error.
- **Python warnings bypassed structured logging entirely.** Nothing in the
  source tree called `logging.captureWarnings`, so `warnings.warn` output went
  through the default `warnings.showwarning` straight to `sys.stderr`. Under
  `RECOTEM_LOG_FORMAT=json` that produced non-JSON lines interleaved with the
  log stream (a single `InconsistentVersionWarning` from serving an artifact
  trained against a different scikit-learn emits three of them), and — the
  reason this is a security fix rather than a cosmetic one — the text never
  reached the redaction processor. Any credential a third-party library
  interpolated into a warning message was logged verbatim. `configure_logging`
  now calls `logging.captureWarnings(True)` and pins the `py.warnings` logger
  to WARNING, so warnings are emitted as ordinary structured records through
  the same `foreign_pre_chain` — redaction first. This does not change how
  warnings are *filtered*; only where they are written. (Progress-bar output
  from irspack's `fastprogress` dependency goes to stdout, not stderr, so it
  never shared a stream with the JSON log; it is addressed by the preceding
  entry.)
- **`recipe_hash` was unreadable in `train_done`.** The value-side hex64 rule,
  which exists to catch a raw signing key, also matched the recipe hash and
  logged `[REDACTED-HEX64]` for every recipe. The hash is SHA-256 of the
  canonical recipe YAML, computed from config alone before any data is
  fetched, and recotem publishes it in the clear everywhere else — `recotem
  inspect` prints it in full and it is carried in the artifact header — so
  redacting it in the log removed the one field tying a running artifact back
  to the config that produced it, without protecting anything. `recipe_hash`
  and `model_version` are now exempt from value-side scrubbing, but only when
  the field name matches exactly and the value is nothing but the digest in
  the lowercase form `hexdigest()` emits (optionally `sha256:`-prefixed);
  anything else in a field of that name is still scrubbed.
- **One hyphen or one capital destroyed a whole log value.** The exemption
  that keeps long `snake_case` identifiers out of the base64url scrubber
  required the entire run to be lowercase and underscore-separated, so any
  `-` or uppercase character anywhere replaced the whole run with
  `[REDACTED-B64URL43]`. Measured on a 52-character export name: the plain
  `snake_case` form survived, while a `rt-` prefix, a trailing `-v2`, a single
  capital, or the kebab-case equivalent were all erased. Two real cases were
  observed: `recotem validate` logging a recipe path as
  `/.../[REDACTED-B64URL43].yaml`, and the remedy anchor in the feature-axis
  error message (`docs/operations.md#recotem-train-exits-4-with-feature_axis_error`)
  reduced to `#[REDACTED-B64URL43]` — losing the pointer to the fix from
  exactly the logs CI and Kubernetes capture. The exemption now covers
  kebab-case, mixed separators, embedded capitals and `UPPER_SNAKE_CASE`,
  while still requiring two or more non-empty separator-delimited segments,
  per-segment case consistency, and an essentially single-case run (at most
  two case outliers). The last condition is what keeps a
  `Title-Case-Hyphenated` secret redacted. The probability that a
  `keygen`-issued 43-character key satisfies all three conditions is about
  1.1e-9, against 2.6e-10 for the previous rule. See
  `docs/security.md#log-redaction`.
- **The published Docker image could not start.** `docker run ghcr.io/codelibs/recotem:2.0.0 --help`
  failed with `exec /opt/venv/bin/recotem: no such file or directory`, on every
  tag and both architectures. Two defects compounded: `uv sync` ran before
  `COPY src/` and left an *editable* stub in the venv (dist-info pointing at
  `/build`, no package directory), while the `uv pip install --no-deps .` that
  was meant to install the real package was redirected to the builder stage's
  `/usr/local` by `UV_SYSTEM_PYTHON=1` and never reached the runtime stage; and
  the venv was built at `/build/.venv` then copied to `/opt/venv`, leaving
  console-script shebangs pointing at a path that does not exist at runtime.
  The venv is now built directly at `/opt/venv` and every install names its
  interpreter explicitly.
- **`docker.yml` now runs the image it builds.** A `smoke` job starts the built
  image before anything is pushed (`build` gains `needs: smoke`) and asserts
  that `--help` exits 0, that `recotem` and `irspack` import, that `/v1/health`
  returns 200, that bare `/health` returns 404, and that the image's own
  HEALTHCHECK reaches `healthy`. The previous workflow only built, pushed and
  scanned — it never executed the artifact, which is how the broken image above
  shipped with a green build.
- **Health probes pointed at `/health`, which is a 404.** The API router is
  mounted under `/v1`, so the correct path is `/v1/health`. The Dockerfile
  HEALTHCHECK, the Compose healthcheck, all three Helm probes (startup,
  readiness, liveness), the `examples/k8s` probes, and the deployment docs all
  used the bare path. The Helm chart in particular could never pass its
  startupProbe and would enter CrashLoopBackOff.
- **Compose training silently did nothing.** `compose.yaml` mounts the
  artifacts volume at `/workspace/artifacts`, a path the image did not
  pre-create, so Docker created it as `root:root` and `appuser` could not write
  there. The image now creates and owns it.
- `docs/deployment/docker.md` showed a health response in the
  `/v1/health/details` shape rather than the `{status,total,loaded}` that
  `/v1/health` actually returns, and described the metrics endpoint as
  `/metrics` without noting that it is `/v1/metrics` and requires an API key.
- **Cleared the seven HIGH CVEs the container image was carrying.** The trivy
  gate had been failing since 2026-08-01 on every branch that triggers it.
  Three findings were real dependencies pulled in by the bigquery/gcs/s3
  extras and are fixed by relocking -- `aiohttp` 3.13.5 to 3.14.3
  (CVE-2026-69244), `cryptography` 49.0.0 to 50.0.1 (CVE-2026-69247), and
  `pyasn1` 0.6.3 to 0.6.4 (CVE-2026-59884/59885/59886). The other two were
  inside pip's vendor tree (`pip/_vendor/msgpack` 1.1.2 and
  `pip/_vendor/pkg_resources` from setuptools 70.3.0), which no pip release
  fixes -- 26.2.1 is the latest and still vendors both.
- **pip is no longer shipped in the Docker image.** uv performs every install,
  pip was never invoked, and it had become a standing source of HIGH findings
  that upgrading could no longer clear. **`python -m pip` no longer works
  inside the image**; rebuild to change dependencies, or run
  `python -m ensurepip` if pip is genuinely needed.

- **A scheduled `train` could exit 0 having trained nothing.** `lock.py` treated
  `EACCES`/`EPERM` on the lock path as "lock not acquireable -- same semantics
  as contention", so an unwritable lock directory made the command skip
  silently and report success. Contention is transient and skipping is right;
  a permission error is a deployment mistake no retry will fix. Permission
  failures now raise `LockPermissionError` (a `ConfigError`, so exit **8** --
  not 6, which schedulers read as "retry later"), regardless of
  `--fail-on-busy`, and name the path, the uid/gid and `RECOTEM_LOCK_DIR` in
  the message. On Windows only `EPERM` converts, because `EACCES` there also
  covers a genuine sharing violation; that case stays contention but now logs
  a warning instead of being silent.
- **`serve` returned exit 3 instead of 8 when it could not bind.** uvicorn
  catches the bind `OSError` itself and raises
  `SystemExit(uvicorn.config.STARTUP_FAILURE)` (== 3), which bypasses
  `except OSError` because `SystemExit` is a `BaseException`. Exit 3 is
  `_EXIT_DATASOURCE`, so a port clash was indistinguishable from a data-source
  failure to supervisor and CronJob retry logic. Bind and other uvicorn startup
  failures now map to `_EXIT_CONFIG` (8) as documented. The unit test that
  covered this mocked an `OSError` real uvicorn never raises; it is replaced,
  and integration tests now exercise real bind collisions in a subprocess.

- **`examples/csv-local` failed when followed as written.** The recipe asked
  for `cutoff: 20` against bundled data holding only 15 distinct items, so
  irspack raised `ValueError: cutoff must not exeeed the number of items.` and
  training exited 1. The cutoff is now 10, and both the recipe and the README
  state the constraint. (Not a regression: the same reproduction fails on
  irspack 0.4.2, 0.5.0 and 0.5.2 alike.)
- **`examples/plugins/echo-source` could not be used from a recipe.** Its
  `Config` declared no `type` field, so `train` exited 2 with "Recipe source
  has no discriminator 'type' field". `docs/plugin-authoring.md` was the root
  cause: it offered "let `extra="ignore"` discard it" as an option, which
  cannot work, because pydantic refuses to discriminate on anything but a
  `Literal`. The guide now states the requirement,
  `validate_plugin_contract()` enforces it, and an integration test drives the
  full recipe-YAML-to-train path that the previous class-only unit tests never
  exercised.

- **`split.scheme: random` was not random when the recipe declared a time
  column.** The pipeline forwards `schema.time_column` to the splitter for any
  recipe that declares one, and irspack switches to a per-user *recency*
  holdout the moment it receives one -- so `random` silently behaved as
  `time_user`, contradicting the documented "`time_column` is unused". Measured
  on 30 users, the holdout matched each user's most recent interactions 30/30
  times. Since the split defines the validation set the Optuna search scores
  against, the search was optimising for a different task than the recipe
  asked for. `split_interactions` now ignores `time_column` under `random`.
  **Behaviour change:** such a recipe will split differently on its next train
  and its reported metric may move; existing artifacts are unaffected until
  retrained, and `scheme: time_user` restores the old behaviour explicitly.

- **A cold-start feature value must now be a JSON scalar.** The per-value
  length cap was `isinstance(val, str)`-gated and `_FeatureValues`'
  `max_length` counts KEYS, so a JSON array or object was bounded by neither —
  while `_features._tokens` reduces every value with `str(raw)`. One key
  holding a large array therefore materialised a repr as large as
  `RECOTEM_MAX_BODY_BYTES` allows. Such a value never encoded to anything
  regardless (a Python repr does not match a training vocabulary entry), so
  arrays and objects are now rejected with `422` instead of capped. Numbers,
  booleans and `null` are unaffected.
- **The request-body cap no longer swallows `MemoryError` / `RecursionError`.**
  `BodySizeLimitMiddleware` deliberately absorbs whatever the inner app raises
  once the cap is breached, because injecting `http.disconnect` is what made it
  raise — but both of those are `Exception` subclasses, so an out-of-memory
  unwind during an oversized request was reported to the client as a tidy
  `413` instead of surfacing. They are now re-raised ahead of the broad clause,
  matching how every other broad handler in the package treats them.
- **A pruned trial no longer logs a spurious `trial_learn_failed` WARNING
  under `per_trial_timeout_seconds`.** `optuna.TrialPruned` subclasses
  `Exception`, so the generic handler in the threaded learn path caught every
  by-design prune on its way out and logged it exactly like a genuine
  failure — while the non-threaded path, which has no such handler, stayed
  silent for the identical prune. **This is not features-only:** irspack
  raises `TrialPruned` from `recommenders/base_earlystop.py` whenever
  Optuna's pruner fires, so any existing recipe that sets
  `per_trial_timeout_seconds` on an early-stopping algorithm (IALS included)
  was emitting these. Optuna still records the trial as `PRUNED` and the
  search outcome is unchanged; only the log stream is quieter. Alerting built
  on the *count* of `trial_learn_failed` events will see it drop.
- **Feature-aware iALS: every feature-ridge failure is now recognised, not
  just the Cholesky one.** irspack raises this family from three sites in
  `cpp_source/als/IALSTrainer.hpp` and types them inconsistently. On 0.5.0
  there were two, both carrying `Feature ridge Cholesky decomposition
  failed.`, so recotem's substring test covered the family completely.
  **0.5.1 added a third** — `Feature ridge solve failed.`, raised inside the
  `std::async` worker its feature-aware parallelisation introduced, when the
  Cholesky succeeds but the solution is not finite — and 0.5.2 typed only the
  first two as `FeatureRidgeCholeskyError`, leaving the third a bare
  `std::runtime_error` (irspack's own `optuna_trial_failure_exceptions` has
  the same gap). Against irspack 0.5.1+ that third failure did not match, so
  it escaped `study.optimize` (called with no `catch=`) and **aborted the
  entire search instead of pruning one trial**; in the final refit it missed
  the `feature_cholesky_error` mapping and surfaced as an unmapped exit 1
  rather than exit 4 with the `min_frequency` remedy. Recognition is now by
  type **and** by the `Feature ridge` prefix, so all three sites are covered
  on every 0.5.x — while the plain iALS solver's own `Cholesky decomposition
  failed.` / `Cholesky solve failed.` stay unmatched, as before. Applies to
  both search paths and the final refit.
- **Feature-aware iALS: an all-dead-numerical `features:` block is now
  refused.** The whole-block-dead guard keyed on `n_features == 1`, which a
  block whose only column is a zero-variance (or all-null) `numerical` column
  escaped — a numerical column always reserves width 1, so `n_features` stayed
  2 even though it emits nothing. Such a block would sign an artifact
  advertising `features` while serving bias-only (== plain iALS). The guard now
  refuses a block when no column can emit a non-bias feature, matching the
  existing all-categorical-dead and zero-id-overlap refusals.
- **Feature-aware iALS: a finite-but-huge cold-start `numerical` value no
  longer injects `inf`.** The non-finite check tested the raw parsed value, but
  the matrix stores the value standardized and cast to `float32`; a value
  finite in float64 whose standardized magnitude exceeds float32's max became
  `±inf` in the matrix and was not counted. It is now counted as an unknown
  value (`recotem_v1_feature_unknown_value_total`) and contributes nothing,
  like any other unusable value.
- **Cold-start feature request values are now length-capped.** Each string
  value in `user_features` / `item_features` is capped at 8192 characters
  (`422` on violation, like every other request-schema cap). Previously only
  the key count was capped, leaving a single string value unbounded — a
  memory-amplification vector via
  `multi_label` tokenization, reachable with one API key and multiplied by
  batch/related fan-out. The cap covers the batch verbs too.
- **Feature-aware iALS training: an unrepresentable `numerical` column fails
  with a training-domain error, not exit 1.** A `numerical` column carrying a
  Python int too large for float64 (`>= 309` digits) raised an unmapped
  `OverflowError` (exit 1) from the fit's own parser; it now raises a
  `TrainingError` (exit 4) naming the column. A complex-valued column, which
  previously trained silently on its real part, is now rejected explicitly.
- **Recipe load rejects a `features.<side>.id_column` that also names a feature
  column.** The collision is guaranteed to fail at train time (the id column is
  consumed as the index); it is now caught at recipe load with a clear message.
- **Feature-aware iALS: a non-finite value no longer silently kills an
  otherwise-usable `numerical` column.** `pd.to_numeric` maps an overflow token
  like `1e400` to `+inf`, and pandas `mean` / `std` do not skip `±inf`, so a
  single such cell made the column's `std` non-finite and routed the whole
  column to the zero-variance path — silently dropping a column that still held
  usable finite values (like `[1, 2, 3]`) while the artifact continued to
  advertise `features`, and emitting a `feature_zero_variance_column` warning
  that misattributed the cause as "divide by zero." `build_encoder_state` now
  computes mean/std over the finite values only, so a stray overflow cell
  degrades to `unknown` at encode time — exactly as it already did per request —
  instead of killing the column at fit time. A column that parses to no finite
  value at all is still dropped, now with a distinct, accurate warning detail.
  This changes training-time encoding for any feature table with such a column;
  retrain to pick it up.
- **`:recommend-related` cold-start paths now return `404 NO_CANDIDATES`
  consistently.** The pre-existing all-seeds-known path raised `NO_CANDIDATES`
  when the ranker produced no survivors, but the two cold-start branches (the
  `user_features` profile prior, and `item_features` for a seed absent from
  training) returned `200` with an empty `items` list for the identical
  condition. Both branches now raise the same `NO_CANDIDATES`, so every path of
  the verb — single and batch — reports an empty result the same way.
- **The artifact header's `features` descriptor was never reconciled with the
  model the artifact actually contains.** The header block was written whenever
  the recipe declared a `features:` block, and cross-checked against neither the
  winning recommender's capability nor whether that model was trained with
  features at all. A `features:` recipe only requires *one* of its algorithms to
  be feature-aware, so a search over `[IALS, TopPop]` that TopPop wins produces a
  model trained with no features whatsoever — while the header still advertises
  `features` with the full column list. `recotem inspect`, whose whole contract
  is that the header describes the payload, then reports a feature-aware model
  that is really plain TopPop. Request handling was not affected (serve reads the
  encoder state off the payload, not the header, and the only existing consumer
  of the header block checks its `version` integer), so this was a truthfulness
  defect in the artifact's own self-description rather than a serving fault — but
  the header is precisely what operators, audits and `inspect` reason about, and
  it is not independently checkable without deserializing the payload the header
  exists to describe. The `features` block is now reconciled with the winning
  `best_class` and the state actually attached to the payload before signing.
- **Four CLI failures reported the wrong exit code, three of them as an
  unmapped `1`.** Exit codes are the contract supervisors and CronJob retry
  logic read, and `1` (`_EXIT_UNKNOWN`) says nothing about whether to retry, fix
  the recipe, or fix the data. Worse, an unmapped exception is classified
  `internal_error`, so it is the case that logs a full traceback — a recipe or
  data mistake was presented as a Recotem bug. A `schema` column absent from the
  source data escaped as a bare pandas `KeyError` (exit 1) rather than a
  `DataSourceError` (exit **3**); a `training.cutoff` larger than the item
  count surfaced irspack's `ValueError: cutoff must not exeeed the number of
  items.` unmapped (exit 1) rather than a `TrainingError` (exit **4**);
  `recotem inspect` exited 1 with a traceback on a configuration error that
  `serve` already mapped to **8**, because `inspect` called the same
  `ServeConfig.from_env()` outside the `try` that `serve` wraps around it. The
  missing-column case is worth calling out: the CSV source *has* a
  required-column check for exactly this, but it was dead code — it reads the
  column names out of the fetch context's `extra` mapping, which neither
  production call site populates, so it always took its "no schema context —
  skip" early return and the column was first touched much later, by the
  cleansing step's `dropna`. The fourth was the most misleading: a malformed
  `RECOTEM_SIGNING_KEYS` exited **5** (`_EXIT_ARTIFACT`), which reads as "the
  artifact is corrupt" and sends an operator to the artifact store when the
  fault is an environment variable — on `train`, `serve` and `inspect` alike.
  It now exits **8** (`_EXIT_CONFIG`), like every other configuration error.
- **One malformed recipe file made the entire serve deployment permanently
  unready.** A single YAML syntax error produced an orphaned registry entry:
  startup registers a failure stub for the unparseable file, but the watcher
  builds its state map from successfully *parsed* recipes only, so that stub was
  never in the watcher's view. It could therefore never be evicted — not even
  after the operator fixed the YAML — and on the first rescan the watcher, not
  recognising the name, registered a *second* stub under a deduplicated name
  with an empty artifact path. Since `/v1/health` is count-based (`loaded <
  total` → `degraded`, HTTP 503), `total` stayed permanently above `loaded` for
  the life of the process. The valid recipes did load and would answer their
  verbs directly, but `/v1/health` is exactly what the Helm chart's and
  `examples/k8s/`'s readiness probes poll, so the pod never became Ready and was
  kept out of the Service — the healthy recipes stopped receiving traffic
  through it, and no restart cleared the condition. The watcher additionally
  re-parsed and re-failed the broken file on every tick, because its mtime cache
  memoises only successful parses, logging a warning each time at the default
  5-second interval. A malformed recipe file no longer creates a duplicate or
  unevictable entry, and fixing the YAML now restores health without a restart.
- **A repaired recipe YAML left `/v1/health/details` reporting `degraded`
  forever.** The other half of the case above, for a recipe that was *already
  loaded* when its YAML was edited into a syntax error. The rescan records the
  parse failure as `last_load_error` and deliberately keeps the trained model
  serving (M-2), but the watcher's recovery branch only fired for a YAML-failure
  *stub* — an entry with no artifact path. An already-loaded recipe has one, so
  when the file parsed again nothing ran: the error was never retracted, the
  corrected recipe body was never adopted, and `/v1/health/details` answered 503
  for the life of the process. Only a new artifact forcing a full reload, or a
  restart, cleared it. `/v1/health` stayed 200 throughout — it is count-based
  and the model never stopped serving — so this degraded diagnostics rather than
  availability, but it made the operator endpoint useless as an alert source
  once any recipe had been edited badly even once. The rescan-parse path now
  records that it owns the outstanding error, so a successful reparse clears
  exactly that error and reloads the corrected recipe. An artifact load failure
  (HMAC mismatch, missing file, irspack version skew) never sets that marker and
  so is never cleared by a YAML reparse — it stays visible until the artifact
  itself loads.
- **Log redaction blanked non-secret fields and whole event names.** Two
  over-broad rules fired on things that carry no secret. The key-name substring
  patterns `auth` and `key(?!s\b)` matched `auth_enabled` and
  `signing_key_status` on the `security.posture` event — the two fields that
  event exists to report — so the startup security posture was unreadable in the
  very log line that records it. Separately, the high-entropy value scrubber's
  base64url alphabet (`[A-Za-z0-9_-]`, 43+ chars — 43 being `ceil(256/6)`)
  includes the underscore, so an ordinary `snake_case` structlog **event name**
  of 43 characters or more matched end to end and was replaced wholesale with
  `[REDACTED-B64URL43]`. Three real event names are that long and were being
  erased: `sql_statement_timeout_unsupported_on_sqlite`,
  `recipe_yaml_parse_failed_on_rescan_new_file`, and
  `source_registry_unavailable_during_validation`. The first is the one
  `docs/data-sources/sql.md` promises operators can alert on to learn that the
  documented statement-timeout safety control is not in effect on SQLite — an
  alert that could never match, on a control the docs tell you to verify.
  Redaction is now scoped to the fields and value shapes that actually carry
  secrets, and leaves event names and non-secret posture fields intact. **Any
  alerting keyed on the redacted forms needs updating** — these events now log
  their real names and values.
- **The shipped deployment assets did not work as published.** Both
  `examples/k8s/` workloads set `serviceAccountName: recotem`, which no manifest
  in the directory declares and the README does not list as a prerequisite, so
  applying the directory as documented produced a workload whose pods are never
  created. The Helm CronJob pins its shell to `/bin/sh` and then, on the legacy
  `recipeFiles` string form, emitted bash-only syntax into it — a `<<<`
  here-string, `read -a`, array expansion and pattern substitution — none of
  which dash supports, so a chart using that form failed at run time rather than
  at `helm install`. The annotated Compose example in
  `docs/deployment/docker.md` (and its `docker run` counterpart) doubled the
  entrypoint: Compose's `command:` replaces `CMD`, not `ENTRYPOINT`, so
  `command: recotem train …` against `ENTRYPOINT ["recotem"]` runs
  `recotem recotem train …`. The repository's own `compose.yaml` gets this
  right, so the annotated example contradicted the file it claimed to annotate.
  `docs/deployment/k8s.md` described the default NetworkPolicy as the canonical
  deny-all-inbound pattern; in fact the default enables `allowKubeletProbes`,
  which renders an ingress rule with no `from:` selector and therefore admits
  **any** source on the service port. That is the most consequential of these,
  because a reader takes a protection they do not have for granted. And
  first-time install had no documented bootstrap ordering — generate keys, train,
  then serve — which is now stated, along with the fact that `/v1/health` reports
  503 until the first artifact exists.
- **The release pipeline had no gate on the steps most likely to break a
  release.** The publish workflow triggers on the tag glob `v*`, which matches
  `vfoo`, and compared the tag against neither `pyproject.toml` nor
  `version.py`, nor rejected a `.devN`/pre-release version — on a registry where
  a version is burned permanently the moment it is uploaded. The container
  vulnerability scan ran *after* the multi-arch push, so a failing scan could
  only report a vulnerability in an image that was already public. Nothing
  validated the Helm chart, the `examples/k8s/` manifests, or `compose.yaml`,
  which is how the deployment defects above shipped under a green build. And the
  e2e script reported failures misleadingly: every request uses `curl -sf`, which
  exits 22 on any 4xx/5xx, so under `set -euo pipefail` a server that started but
  came up `degraded` (503 on `/v1/health`) was diagnosed as "server did not start
  within 30s", and the script's own status-reporting branch — written for exactly
  that case — was unreachable, because `set -e` killed the script one line
  earlier. The run ended on the cleanup banner with no failure message, which
  reads like an orderly finish. The script also served with `--insecure-no-auth`
  and never set `RECOTEM_API_KEYS`, sent an `X-API-Key`, or asserted a `401`, so
  the authenticated path every production deployment uses went untested by the
  suite whose job is to prove the release works. All of these are now gated.

- **BigQuery: a SQL mistake was reported as a permissions problem.**
  `client.query()` returns before the query executes, so every execution error —
  bad SQL, a missing table, table-level permission denied, quota — surfaced from
  the download call and was caught by the Storage Read API handler. A recipe
  naming a missing table reported `BigQuery Storage Read API failed with
  NotFound`, and under `RECOTEM_BQ_REQUIRE_STORAGE_API=1` a plain SQL typo was
  answered with `Grant bigquery.readSessions.create on the project to fix this`.
  The framing was unconditional: it applied on the pure-REST path too. Execution
  and download are now separate phases with separate messages, and only a genuine
  download failure carries the Storage framing and the `readSessions` advice.
  A side effect is also gone: ordinary SQL errors no longer increment
  `recotem_bigquery_storage_fallback_total{reason="non_iam_error_no_fallback"}`.
- **`RECOTEM_BQ_REQUIRE_STORAGE_API=1` did not enforce when the extra was
  missing.** `google-cloud-bigquery` does not raise `ImportError` from
  `to_dataframe()` when `google-cloud-bigquery-storage` is absent — it warns and
  falls back to REST — so the strict-mode branch that waited for that exception
  was unreachable and the fetch quietly succeeded over REST. The dependency is
  now probed explicitly, and strict mode refuses **before the query is
  submitted**, so no scan is billed. The non-strict path is unchanged.
- **The IAM "REST fallback" was not a REST fallback.** Both
  `QueryJob.to_dataframe()` and `RowIterator.to_dataframe()` default
  `create_bqstorage_client=True`, so the retry after a `PermissionDenied`
  re-created the Storage client and hit the same denial. It now passes
  `create_bqstorage_client=False` and re-obtains a fresh `RowIterator`, because a
  row iterator is single-use. This was invisible to the previous tests, whose
  mocks inspected `create_bqstorage_client` on a call that never passed it.
- **A zero-row result is now a data-source error.** An empty BigQuery or SQL
  result reached the splitter and failed with `irspack split failed: division by
  zero` (exit 4), naming nothing useful; a zero-row fetch now raises
  `DataSourceError` (exit 3) naming the source and the recipe, before the
  schema-column check so an empty result is not misreported as a missing column.
  The CSV source already behaved this way.
- **`recotem_bigquery_storage_fallback_total` is a train-side, log-only signal.**
  It is incremented by the data source, which runs in `recotem train`, while
  `/v1/metrics` is served by `recotem serve`, which never fetches — so the series
  was permanently absent from every scrape. It is removed from the serving metric
  inventory and from the alert table; monitor the log event instead.
- **`docs/data-sources/bigquery.md` was wrong in three places a reader would act
  on**: a missing schema column was documented as exit 2 / `RecipeError` when it
  is exit 3 / `DataSourceError`; the strict-mode section described enforcement
  that did not happen; and the quoted type-mismatch message was not what BigQuery
  returns. The same incorrect exit-2 row was corrected in `sql.md`.
- **The Helm chart silently discarded the objectStore init container's
  `volumeMounts`.** `deployment.yaml` and `cronjob-train.yaml` both emitted
  `name:` and `volumeMounts:` a second time in the same mapping — once from
  `toYaml`-ing `recipes.objectStore.initContainer`, once from the chart's own
  `/recipes` entry. Go's YAML decoder is last-key-wins, so an operator's mounts
  (bucket credentials, a CA bundle) never reached the pod and the sync container
  failed for no visible reason; `helm lint`, `helm template` and
  `kubectl apply --dry-run=server --validate=strict` all accept a duplicate key.
  Both templates now merge into the operator's spec instead of appending a
  sibling key: an operator-supplied `name` wins (`sync-recipes` stays the
  default), and the chart's `recipes` mount is appended last and always wins over
  an operator entry of the same name, so the shared emptyDir cannot be
  redirected. `values.yaml` documents the merge and the fact that a mount must
  name a volume the chart declares — there is no `extraVolumes` hook.
- **`examples/k8s/cronjob.yaml` exited 0 on an empty recipes directory.** Its
  training loop trained nothing and reported success, so a wrong ConfigMap name
  or an unsynced PVC surfaced only later as a 503 from serve. It now counts the
  recipes it trained and exits 1 with
  `no recipe files found under /recipes`, matching both siblings — the chart's
  all-recipes CronJob branch and `examples/k8s/bootstrap-job.yaml`.
- **The manifest gate did not exercise a realistic objectStore spec.** Its only
  `objectStore` permutation supplied neither `name` nor `volumeMounts`, which is
  why the duplicate-key defect above rendered green. A permutation supplying both
  was added, and `tests/unit/test_k8s_manifests.py` now covers the merge
  precedence and the example CronJob's empty-directory guard with a
  duplicate-key-rejecting YAML loader (`yaml.safe_load` takes the last key, so a
  normal parse of a broken render looks healthy).
- **The empty-held-out-set error advised a fix that does not work.** It suggested
  "increasing the dataset size", but the holdout is `floor(n * heldout_ratio)`
  **per user**: 4,000 users with 8 interactions each fails exactly like 400 users
  with 8 interactions each. The message now states the per-user rule, the minimum
  distinct items per user implied by the configured `heldout_ratio`, how many
  users actually clear it, the deepest user observed, and a `heldout_ratio` that
  would work for that data — and distinguishes "no user is deep enough" from
  "deep users exist but `test_user_ratio` drew none of them", which have
  different remedies. `time_global`, which has no per-user floor, gets its own
  message. `docs/recipe-reference.md` documents the rule under **Per-user holdout
  depth** and records that the hash-ordering nondeterminism can flip a marginal
  dataset between exit 0 and a `zero_score` exit 4, not merely perturb
  `best_score`.
- **A read-only filesystem on the lock path exited 1 instead of 8.** The guard
  caught `PermissionError` (EACCES/EPERM) only, but a read-only mount raises
  `OSError(EROFS)`, which is not a `PermissionError` — so `readOnlyRootFilesystem:
  true`, a read-only PVC, or a read-only bind mount produced an unhandled
  traceback and `"code": "internal_error"` instead of the configuration error the
  handler was written for. EROFS now raises `LockPermissionError` (exit 8) from
  the `mkdir`, POSIX `open`, and Windows `open` paths, with a remedy that fits the
  cause: `chmod` advice for EACCES, a writable `RECOTEM_LOCK_DIR` for EROFS. The
  guard stays errno-scoped, so ENOSPC and friends still propagate. Also, the
  contention message raised under `--fail-on-busy` no longer recommends passing
  `--fail-on-busy`, which the reader has by definition already done.

### Security

- **A malformed `RECOTEM_SIGNING_KEYS` printed the signing key in clear text,
  past the redaction processor.** When the variable does not parse, the
  resulting exception quotes the offending value — and for this variable the
  offending value *is* the signing key. The trigger is ordinary: pasting the
  hex half of what `recotem keygen` prints, without the `kid:` prefix. The
  redaction processor really is first in the log chain, and that is why it did
  not help: `structlog.processors.format_exc_info` runs **last**, so the
  `exception` field is rendered after redaction has already passed over the
  event. Two exits leaked. `recotem train` printed the raw key to stderr
  through `cli._exit`, one line below a correctly-redacted `error` field; and
  under `RECOTEM_LOG_FORMAT=json`, `serve` emitted a single
  `signing_key_construction_failed` event whose `error` was redacted and whose
  `exception` traceback was not. JSON logs ship to Datadog, ELK and CloudWatch
  as they are, and `foreign_pre_chain` reuses the same processor list, so
  third-party loggers were affected too.

  A second, idempotent redaction pass now runs at the **end** of the chain, so
  the traceback is scrubbed after it is rendered; the first-position pass is
  unchanged, and the existing ordering guarantee still holds. Tracebacks are
  redacted, not discarded — frames are preserved and only the secret becomes
  `[REDACTED-HEX64]`, because a redaction that destroys the diagnostic is its
  own defect. `log_redaction.redact_text()` is now public so `cli._exit`, which
  is outside the log chain, can use it. The same handling covers
  `RECOTEM_API_KEYS` and credential-bearing DSNs.

  **If you ran any 2.1.0 development build or 2.0.0 with a malformed
  `RECOTEM_SIGNING_KEYS`, treat the affected key as disclosed and rotate it.**
  Log retention is the exposure window and redaction cannot be applied
  retroactively to lines already shipped. `docs/operations.md` has the
  four-step rotation procedure; `KeyRing` supports multiple `kid`s so the
  rotation is zero-downtime.

- **The FQCN allow-list could be walked out of with a dotted name, so a signed
  payload reached arbitrary code.** `pickle.Unpickler.find_class` resolves
  protocol-4 `STACK_GLOBAL` names with `_getattribute`, which walks dots, but
  `_is_allowed` inspected only the *module* half of the pair. A payload naming
  an allow-listed module and putting the gadget in the *name* half — e.g.
  `("numpy._core._methods", "os.system")` — passed the check and then resolved
  to the real `os.system`, giving code execution inside `unpickle_payload` and
  therefore inside `serve`'s artifact load. The HMAC verify was never bypassed,
  so this required a validly signed artifact; the allow-list is the layer
  documented to hold when the signing key does not, which is exactly the case
  it failed to cover. `_is_allowed` now rejects any dotted name. Six of the
  seven algorithms Recotem can build (`IALS`, `CosineKNN`, `TopPop`, `RP3beta`,
  `DenseSLIM`, `TruncatedSVD`) were retrained and reloaded under the fix:
  legitimate artifacts never use a dotted name, so nothing that used to load
  stops loading. `BPRFM` is the seventh and postdates this change, so it was
  not in that set; the rule itself is a property of `find_class` rather than of
  any algorithm.

- **Two security claims did not match the code, both fail-closed.** The
  artifact HMAC covers `kid_bytes || header_json || payload` as one run of
  bytes, so the 4-byte `header_len` that splits the last two is not
  authenticated: moving it passes `verify_hmac` — `recotem inspect` prints
  `HMAC: OK` — and is caught one layer later by the header JSON parse, exit 5.
  It shifts a boundary and cannot inject a byte, so the format is unchanged and
  the documentation now says this precisely. Separately, the prefix allow-list
  carried `numpy.dtypes.`, which matched nothing (a trailing dot matches
  sub-modules, and that module has none) while `docs/security.md` listed it as
  permitted; the dead entry is removed and the document corrected. Nothing
  needs it — numpy reaches dtypes through the hand-enumerated `numpy.dtype`
  plus `_frombuffer`, asserted against real pickles. The test that appeared to
  cover the entry passed `numpy.dtypes.Float64DType` as a *module* string,
  which no pickle ever does, so a dead rule looked exercised for four rounds.

- **A load failure could put an object-store URI, path segments included, into
  an API response.** `_sanitize_error` was called from exactly one place — the
  startup load path — so `/v1/health/details` was only redacted until the first
  watcher rescan. Every other writer (the watcher's `set_load_error`, the
  recipes-directory scan failure, and both YAML-parse stub paths) stored the
  raw exception string. A metadata fetch refused by the SSRF guard surfaced its
  URI verbatim: measured on one recipe and one endpoint, `/v1/health/details`
  reported a sanitized 200-character error at T0 and a raw **327-character**
  one at T+40s. Redaction and the length bound now sit on the write barrier, so
  every path that can set `last_load_error` gets both.

### Migrating to irspack 0.5.0

irspack 0.5.0 changed `IALSModelConfig`'s pickled state from a 7-tuple to a
10-tuple (the three new fields back feature-aware iALS). Its `__setstate__` is
a strict-arity binding, so **IALS artifacts trained with irspack 0.4.x cannot be
loaded under 0.5.x**. This is an upstream format change that irspack's own
changelog does not mention; Recotem cannot migrate such artifacts in place,
because the missing fields are internal C++ state that only a retrain produces
correctly.

- **Action required:** retrain and redeploy every recipe whose `best_class` is
  `IALSRecommender` (the known break). `BPRFMRecommender` is refused too, for a
  different reason: it is **unproven**, not known-broken. Its absence from the
  verified table is what the guard acts on — it refuses the unproven rather
  than risk loading a model that serves subtly wrong scores. Verifying it needs
  a second version axis the header does not record: a BPRFM payload embeds a
  LightFM object, so `(best_class, irspack_mm, running_mm)` does not describe
  the pair completely.

  This is real migration work as of 2.1.0. Earlier releases could not hold a
  BPRFM artifact at all, because irspack gates the class behind an import of
  `lightfm` and upstream `lightfm` has shipped no Python 3.12-compatible
  release. The `bprfm` extra added in this release supplies `lightfm-next`, a
  maintained fork that does build on 3.12, so from 2.1.0 a deployment *can*
  hold one — and a 2.0.0 → 2.1.0 upgrade cannot carry it across.
- The break is **bidirectional**: 0.5.x-trained IALS artifacts also fail to load
  on 0.4.x. Upgrade `train` and `serve` together — the upgrade cannot be staged
  serve-first, and serve cannot be rolled back to 0.4.x once artifacts have been
  retrained on 0.5.x.
- **Verified compatible across 0.4 ↔ 0.5, in both directions:** `CosineKNN`,
  `TopPop`, `RP3beta`, `DenseSLIM`, and `TruncatedSVD`. These artifacts load
  unchanged and need no retrain. "Verified" here means an artifact trained under
  one version was loaded under the other, with irspack as the only variable, and
  the recommendation scores compared bit-exact.
- **Every future irspack minor starts out refused.** The guard consults a table
  of verified pairs, so a later 0.5 → 0.6 upgrade will refuse artifacts for
  *all* algorithms — including the five above — until that transition is
  verified and its rows are added. Patch upgrades within a minor (e.g.
  0.5.0 → 0.5.3) are unaffected: matching major.minor short-circuits before the
  table is consulted.
- Artifacts that skew are refused with an actionable error rather than a raw
  `TypeError` (see the version-skew guard above). Runbook:
  [docs/operations.md](docs/operations.md#irspack-version-skew).
- **Escape hatch.** `RECOTEM_ALLOW_IRSPACK_VERSION_SKEW=1` downgrades the
  refusal to an `irspack_version_skew_allowed` warning and lets the payload
  reach the deserializer. It does not make an incompatible payload loadable — a
  genuinely broken artifact then fails with the bare `TypeError` the guard
  exists to replace. It is for operators who know their artifact is unaffected
  (e.g. an algorithm we simply have not verified yet), not a way to skip a
  needed retrain.

## [2.0.0] - 2026-06-27

Recotem 2.0 is a **complete rewrite**. The 1.x multi-service web application
(Django / DRF / Channels / Vue / Celery, backed by a database and message
broker) has been replaced by a single Python package (`pip install recotem`)
plus one Docker image. There is no in-place upgrade path from 1.x — see
**Migrating from 1.x** below.

### Added

- **Recipe-driven workflow.** A model is defined by a single YAML recipe
  (1 recipe = 1 model = 1 endpoint). See `docs/recipe-reference.md`.
- **Two CLI commands** (Typer): `recotem train <recipe.yaml>` and
  `recotem serve --recipes <dir>`, plus `inspect`, `validate`, `schema`, and
  `keygen`.
- **FastAPI serving** with the `/v1` API namespace, four inference verbs
  (`:recommend`, `:recommend-related`, `:recommend-batch`, recipe discovery),
  recipe-scoped hot-swap driven by artifact file mtime, and a file watcher.
- **Signed artifacts.** Binary container with HMAC signing
  (`magic | version | reserved | kid | hmac | header_json | payload`),
  multi-kid `KeyRing` for zero-downtime key rotation, and a hand-enumerated
  FQCN allow-list enforced before any payload byte is deserialized.
- **Pluggable data sources** discovered via entry points: `csv`, `parquet`,
  `bigquery`, and `sql` (PostgreSQL / MySQL / SQLite), plus a documented
  plugin contract (`docs/plugin-authoring.md`).
- **Optuna-driven hyperparameter search** over irspack algorithms with optional
  per-algorithm trial budgets.
- **Item metadata loader** (CSV / Parquet via fsspec) surfaced in recommend
  responses, with a field deny-list (`RECOTEM_METADATA_FIELD_DENY`).
- **Security hardening:** SSRF-guarded HTTP/HTTPS source fetcher with mandatory
  `sha256` pinning and download-size caps; an explicit path-scheme allow-list;
  env-var expansion restricted to `${RECOTEM_RECIPE_*}` and never applied to
  SQL queries; structlog redaction of API/signing keys and cloud credentials.
- **Deployment assets:** multi-stage Docker image (`appuser:1000`), tutorial
  `compose.yaml`, a serve-only Helm chart with optional training CronJob, and
  `examples/k8s/` manifests.
- **Optional Prometheus `/metrics`** endpoint (`RECOTEM_METRICS_ENABLED`).
- Documentation set under `docs/` (getting started, recipe reference, data
  sources, deployment, operations runbook, security model).

### Changed

- The HTTP API moved to the `/v1/recipes/{name}:<verb>` shape. The 1.x
  `/predict/{name}` style endpoints no longer exist.
- Train and serve communicate **only via signed artifact files** and can run on
  different machines; there is no shared database or message broker.
- Python 3.12+ is now required.

### Removed

- The entire 1.x web-application stack: Django, Django REST Framework,
  Channels, the Vue admin UI, Celery workers, and the database / message-broker
  dependencies.
- The GA4 Data API data source (replaced by the BigQuery source for GA4 export
  datasets).

### Security

- Bumped PyJWT and cryptography to patch HIGH-severity CVEs.
- Bumped Starlette to address CVE-2025-62727 (Range header DoS in
  `FileResponse`); pinned `urllib3` to patch CVE-2026-44431 / CVE-2026-44432.

### Migrating from 1.x

There is no automated migration. Recotem 2.0 shares the name and the
recommendation domain with 1.x but is an entirely new system:

1. **Re-train, don't migrate models.** 1.x model state is incompatible with the
   2.0 signed-artifact format. Define recipes and run `recotem train`.
2. **Drop the database and message broker.** 2.0 is stateless; the only durable
   state is the signed artifact file.
3. **Update API clients** from `/predict/{name}` to
   `POST /v1/recipes/{name}:recommend`.
4. **Generate keys.** Run `recotem keygen --type signing` (and `--type api` for
   serve auth) and set `RECOTEM_SIGNING_KEYS` / `RECOTEM_API_KEYS`.

See `docs/getting-started.md` for the full walkthrough.

## [1.0.0] - 2021

Initial public release: a Django / DRF / Channels / Vue / Celery web
application for training and serving recommenders. Superseded by 2.0.

[2.0.0]: https://github.com/codelibs/recotem/releases/tag/v2.0.0
[1.0.0]: https://github.com/codelibs/recotem/releases/tag/v1.0.0
