# Changelog

All notable changes to Recotem are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] - Unreleased

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
  Optuna over a new recotem-owned range (`5e-2`–`1e6`, log-scale) rather than
  irspack's own `default_suggest_parameter`, because irspack ships no default
  range for them and their `0.0` constructor default is a hard error whenever
  the matching feature matrix is non-empty. See
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
  contributes nothing rather than failing the request) and
  `recotem_v1_cold_start_requests_total` (cold-start traffic by case).
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
  cap. The default clears the largest well-formed request `serve` already
  accepts (~72 MiB) with headroom while blocking GB-scale bodies. A new
  `PAYLOAD_TOO_LARGE` error code is added to the v1 API's `ErrorCode` union.
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
  (`recotem inspect`), the `train_done` log event, and the
  `GET /v1/recipes/{name}` response purely for operators' own SIEM/audit
  rules. The inference verbs do not echo it: `:recommend` returns
  `request_id` / `recipe` / `model_version` / `items` only. If you pin or
  diff `recipe_hash` in external tooling, expect every recipe to show a
  changed hash on this upgrade even though nothing about the recipe's
  behavior changed.

- **irspack upgraded from 0.4.2 to 0.5.0.** irspack 0.5.0 adds feature-aware
  iALS, cache/Eigen performance work, and a reworked tuning API. Recotem drives
  Optuna itself and does not call `BaseRecommender.tune`, so none of irspack's
  documented breaking changes (`tune_with_study` removal, `fixed_params` →
  keyword arguments, `random_seed` → `tuning_random_seed`) affect Recotem.
  **IALS and BPRFM models trained on 0.4.x must be retrained** — see below.
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
