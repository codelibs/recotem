# ADR 0002 — training/ and serving/ Module Boundary

**Status:** Accepted

**Date:** 2026-05-13

## Context

`recotem` is distributed as a single Python package used for both training (batch) and serving (long-lived HTTP process). A naive implementation would let these two sub-packages import each other freely. That creates several problems:

1. **Dependency bloat.** `recotem train` should not import FastAPI, uvicorn, or Starlette. `recotem serve` should not import Optuna or irspack's training utilities.
2. **Circular imports.** `training/` and `serving/` share types (e.g. `IDMappedRecommender`). A direct cross-import creates circular dependency chains.
3. **Import-time side effects.** irspack installs an IPython stub at import time. If `serving/` imported `training/`, every serve process would pay that cost.

## Decision

`training/` and `serving/` are declared mutually non-importing sub-packages. Neither may contain a top-level import of the other.

### Shared types live in neutral top-level modules

Types needed by both sides are placed at the top level of the `recotem` package, where neither sub-package "owns" them:

| Module | Contents | Reason |
|--------|----------|--------|
| `recotem._idmap` | `IDMappedRecommender` | irspack's recommender type; needed by serving to deserialize artifacts and by training to produce them |
| `recotem.config` | `ServeConfig`, `TrainConfig`, `ConfigError` | Config types imported at module level by CLI, serving, and training without cross-sub-package dependency |
| `recotem._http_fetch` | SSRF-guarded HTTP fetcher | Used by training's CSV datasource; isolated here so serving does not pull in the fetcher |
| `recotem._size_cap` | Download-size cap helper | Shared between the CSV datasource and the metadata loader |
| `recotem._metrics_bigquery` | Prometheus counter for BQ fallbacks | Neutral location avoids a serving -> training import |
| `recotem._metrics_watcher` | Prometheus counter for watcher scan failures | Neutral location avoids a serving -> training import |
| `recotem.log_redaction` | structlog redaction processor | Needed at top-level so train-only invocations do not pull in serving |

### `cli.py` exception

`recotem/cli.py` imports from both `training/` and `serving/` — it is the orchestration layer that wires the two together. To prevent import-time cost when only one side is invoked, all sub-package imports in `cli.py` are **function-local deferred imports** (inside the body of each CLI command function), not top-level imports. This means:

- `recotem train` never imports anything from `serving/` at import time.
- `recotem serve` never imports anything from `training/` at import time.
- The `recotem` package itself can be imported cheaply without triggering either sub-package's heavy dependencies.

### IPython stub

irspack's transitive dependency `fastprogress -> IPython.display` requires an IPython stub when IPython is not installed. The stub is installed idempotently by:

- `recotem.training._compat` — for training-package callers.
- `recotem._idmap` — for direct importers of `IDMappedRecommender` (e.g. the serving path that deserializes artifacts without going through `training/`).

This ensures the stub is installed regardless of which entry point a user invokes.

## Enforcement

The CI `ruff` configuration catches cross-sub-package imports at lint time via import order rules. The integration test suite imports `recotem.serving` in isolation and asserts that `recotem.training` is not present in `sys.modules` after the import (and vice versa).

## Consequences

- New shared types must be placed in a neutral top-level module rather than inside either sub-package.
- Any cross-sub-package dependency that slips in will be caught by CI.
- The architecture supports future splitting of `training/` and `serving/` into separate distribution packages (e.g. `recotem-train` and `recotem-serve`) without import-graph surgery.

## References

- `src/recotem/_idmap.py`
- `src/recotem/cli.py` — deferred function-local imports for `train` and `serve` commands
- `src/recotem/training/_compat.py` — IPython stub installer
- [CLAUDE.md](../../CLAUDE.md) — "Modules `training/` and `serving/` never import each other"
