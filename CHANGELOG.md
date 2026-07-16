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

### Changed

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
