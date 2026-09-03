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

This affects more deployments than it might appear to. The shipped tutorial
recipe searches `algorithms: [IALS, TopPop]` and normally settles on IALS, so a
deployment that started from the tutorial holds an IALS artifact without anyone
having chosen IALS explicitly. The winning algorithm is a search outcome, not a
recipe setting — check each artifact with `recotem inspect` rather than reading
it off the recipe.

**What you will see.** `serve` starts normally — it does not crash. The IALS
recipe is registered with `"loaded": false` and an error naming the recipe, both
irspack versions, and the remedy. Requests to that recipe return `503`
(`RECIPE_UNAVAILABLE`); every other recipe keeps serving.
`recotem_artifact_load_failures_total{reason="version_skew"}` increments, and
`/v1/health/details` reports `"status": "degraded"`.

**On Kubernetes the blast radius is the whole pod, not one recipe.**
`/v1/health` is count-based: it returns `degraded` with HTTP **503** whenever
`loaded < total` — that is, whenever *any* recipe failed to load. The shipped
Helm chart points all three probes (startup, readiness, liveness) at
`/v1/health`, and `examples/k8s/` points readiness and liveness there too. So a
single refused IALS artifact fails the startupProbe, the pod never becomes
ready, it is kept out of the Service and restarted — and the recipes that would
have served fine never receive traffic. "Other recipes keep serving" is true of
the process, not of the deployment. Retrain before rolling serve.

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
layout — `src/recotem/artifact/` is byte-identical between the two releases);
and every existing recipe, which stays valid as written. Every recipe's
`recipe_hash` does change, but nothing gates on it — see **Changed** below.

### Added

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
  recipe-level lever. Cost is cubic in this number and multiplies with
  `training.parallelism`. See `docs/operations.md#feature-aware-ials-sizing`.
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
  change the serialised model: for all six algorithms Recotem can build, an
  identically-trained recommender pickles to a byte-identical payload under
  0.5.0 and 0.5.2 (SHA-256 compared), `IALSModelConfig.__setstate__` keeps its
  10-element arity, and artifacts interchange in both directions with
  bit-exact recommendation scores. That comparison was run on
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
- **`recotem validate` labels each probed data source.** Because a recipe may
  now declare feature-side sources (`features.item.source` /
  `features.user.source`) alongside the top-level `source:`, the probe output
  tags which one it is (`DataSource: probe OK (csv) [source]`, `DataSource probe
  failed [features.item.source]: ...`) and the missing-discriminator message
  reads `source is missing the 'type' discriminator.` rather than `Recipe
  source is missing the 'type' discriminator.`. Exit codes are unchanged;
  tooling that greps the exact `validate` output lines should update.

### Fixed

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
  written directly to stderr by irspack's `fastprogress` dependency is
  unaffected and still interleaves with a JSON train log.)
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
  different reason: irspack gates it behind the separately installed `lightfm`
  package, which has no Python 3.12-compatible release, so irspack never
  exports the class and we could not verify it either way. In practice no
  recotem 2.x deployment can hold a BPRFM artifact — recotem requires Python
  3.12+ — so this line is a completeness note rather than real migration work.
  Its absence from the verified table means **unproven**, not
  known-broken — but the guard refuses the unproven rather than risk loading a
  model that serves subtly wrong scores.
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
