# Plugin Authoring

Recotem discovers DataSource plugins via Python entry points. A plugin is any installed package that registers in the `recotem.datasources` group.

The `examples/plugins/echo-source/` directory in this repository is a minimal, runnable reference implementation.

## Plugin contract

A plugin must provide a class with three class-level attributes and one
required method (`fetch`); `__init__` and the optional `probe` are described
below.

```python
from __future__ import annotations

import random
from typing import ClassVar

import pandas as pd
from pydantic import BaseModel, Field
from recotem.datasource.base import DataSourceError, FetchContext


class EchoSource:
    """Returns a synthetic DataFrame — useful for testing and CI."""

    # 1. type_name: discriminator value matched against the recipe YAML
    #    `source.type` field.  Must be a non-empty string and unique across
    #    all installed plugins.  By convention use a short lower-case slug.
    type_name: ClassVar[str] = "echo"

    # 2. Config: pydantic BaseModel describing the recipe sub-fields for this
    #    source.  All fields appear under `source:` in the YAML alongside the
    #    `type:` discriminator.  The loader passes the entire `source:` mapping
    #    (including `type`) to `Config.model_validate(...)`, so either declare
    #    `type` as a field on Config (the builtin convention — see below) or
    #    rely on pydantic's default `extra="ignore"` to drop it.  Combining
    #    `extra="forbid"` with no `type` field will fail recipe load with an
    #    "unexpected key" error.
    class Config(BaseModel):
        n_users: int = Field(default=10, ge=1)
        n_items: int = Field(default=20, ge=1)
        n_rows: int = Field(default=100, ge=1)
        seed: int = Field(default=42)

    # 3. extras_required: pip extras to suggest when optional dependencies
    #    are missing.  Leave empty if the plugin has no optional deps.
    extras_required: ClassVar[list[str]] = []

    def __init__(self, config: "EchoSource.Config") -> None:
        self._config = config

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        """Return a DataFrame whose columns include those named in
        the recipe `schema` block (user_column, item_column, optional
        time_column).

        Returns a DataFrame with columns: user_id (str), item_id (str),
        timestamp (int epoch seconds).
        """
        cfg = self._config
        max_possible = cfg.n_users * cfg.n_items
        if cfg.n_rows > max_possible:
            raise DataSourceError(
                f"EchoSource: n_rows ({cfg.n_rows}) exceeds n_users * n_items "
                f"({max_possible}).  Reduce n_rows or increase n_users/n_items."
            )
        rng = random.Random(cfg.seed)
        users = [f"user_{i}" for i in range(cfg.n_users)]
        items = [f"item_{j}" for j in range(cfg.n_items)]
        all_pairs = [(u, v) for u in users for v in items]
        sampled = rng.sample(all_pairs, cfg.n_rows)
        base_ts = 1_700_000_000
        rows = [
            {"user_id": u, "item_id": v, "timestamp": base_ts + idx}
            for idx, (u, v) in enumerate(sampled)
        ]
        return pd.DataFrame(rows, columns=["user_id", "item_id", "timestamp"])

    def probe(self) -> None:
        """Optional. Called by recotem validate to test connectivity.

        Should be cheap — never load full data.
        Raise DataSourceError on failure.
        Return value is ignored by recotem (Protocol declares -> None).
        """
        cfg = self._config
        max_possible = cfg.n_users * cfg.n_items
        if cfg.n_rows > max_possible:
            raise DataSourceError(
                f"EchoSource: n_rows ({cfg.n_rows}) exceeds n_users * n_items "
                f"({max_possible})."
            )
        # discarded by recotem validate — kept here for illustration only
        return {"status": "ok", "rows_to_emit": cfg.n_rows, "items": cfg.n_items}  # type: ignore[return-value]
```

### Rules

1. **`type_name`** is the discriminator value. It appears as `source.type: echo` in the recipe. The registry validates that it is a non-empty string and unique across all loaded plugins; duplicate `type_name` values cause both `recotem train` and `recotem serve` to fail at startup with a `DataSourceError` (exit code 3) listing the conflicting fully-qualified class names.

2. **`Config`** is a pydantic `BaseModel`. Fields are validated at recipe load. Use pydantic validators for constraints. Required fields without defaults cause a `RecipeError` when missing from the recipe.

3. **`extras_required`** is **purely documentation**. The registry only validates that it is a `list[str]`; recotem never auto-installs or auto-checks these extras. Surface a helpful message yourself in `__init__` (see [Deferred imports](#deferred-imports)) — the value of the attribute is what you cite there.

4. **`fetch(ctx)`** must return a `pandas.DataFrame`. The DataFrame must contain at least the columns referenced in `recipe.schema` (`user_column`, `item_column`, and optionally `time_column`). The training pipeline accesses those columns by name immediately after fetch — a missing column surfaces as a `KeyError` and exits the train run.

5. **`fetch()` must raise `DataSourceError`** for any external or transient failure (auth errors, network errors, query errors, empty results). `DataSourceError` is mapped to exit code 3. Any other exception surfaces as exit code 1. Wrap third-party exceptions explicitly:

   ```python
   def fetch(self, ctx: FetchContext) -> pd.DataFrame:
       try:
           return self._do_fetch()
       except SomeLibraryError as exc:
           raise DataSourceError(str(exc)) from exc
   ```

6. **Deferred imports.** Do not import optional dependencies at module top-level. Defer to `__init__` or `fetch()`:

   ```python
   def __init__(self, config: "MySource.Config") -> None:
       try:
           import my_optional_dep  # noqa: F401
       except ImportError as exc:
           raise DataSourceError(
               "MySource requires 'recotem[myextra]'. "
               "Install with: pip install 'recotem[myextra]'"
           ) from exc
       self.config = config
   ```

   This ensures missing extras produce a clear `DataSourceError` mentioning the required extra by name, rather than an `ImportError` with exit code 1.

## Package structure

The reference plugin under `examples/plugins/echo-source/` uses this layout:

```
recotem-echo-source/
├── pyproject.toml
└── src/
    └── recotem_echo/
        ├── __init__.py     # re-exports EchoSource so "recotem_echo:EchoSource" resolves
        └── source.py       # EchoSource class definition
```

A flatter `recotem_echo/__init__.py` containing the class directly also works
— what matters is that the entry-point string `<module>:<class>` resolves.

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "recotem-echo-source"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["recotem>=2.0,<3", "pandas>=2.2,<4"]

[project.entry-points."recotem.datasources"]
echo = "recotem_echo:EchoSource"

[tool.hatch.build.targets.wheel]
packages = ["src/recotem_echo"]
```

The entry-point key (`echo`) is the name reported in registry log/error
messages but is **not** used as the discriminator — Recotem uses the loaded
class's `type_name` attribute. By convention, keep them the same.

## Install and use

```bash
uv pip install -e examples/plugins/echo-source/
```

Verify discovery by running `recotem validate` against a recipe that uses the
plugin — the loader resolves `source.type` through the entry-point registry
and will report `Unknown DataSource type 'echo'` if the plugin is not
installed in the same environment as `recotem`.

> Note: `recotem schema` builds the JSON Schema at runtime by constructing
> a discriminated union of every registered DataSource `Config` class
> (including plugin-provided ones) and substituting it into the `Recipe`
> model. Plugin `Config` schemas **do** appear in the output — this is
> what makes IDE autocompletion work for `source.*` fields. The union is
> assembled via `build_source_config_union()` at invocation time, so the
> plugin must be installed in the same Python environment as `recotem`.

Recipe:

```yaml
name: echo_test

source:
  type: echo
  n_users: 50
  n_items: 100
  n_rows: 500
  seed: 42        # optional; omit to use the default seed

schema:
  user_column: user_id
  item_column: item_id
  time_column: timestamp   # EchoSource emits integer epoch-second timestamps

training:
  algorithms: [TopPop]
  metric: ndcg
  cutoff: 10
  n_trials: 1

output:
  path: ./artifacts/echo_test.recotem
```

Train:

```bash
recotem train recipe.yaml
```

## FetchContext

`FetchContext` carries metadata that `fetch()` can optionally use:

```python
@dataclass
class FetchContext:
    recipe_name: str                            # the recipe's name field
    run_id: str                                 # unique ID for this training run (UUID)
    extra: dict[str, Any] = field(default_factory=dict)  # reserved for future use
```

Most plugins ignore `ctx`. It is useful for logging and for idempotency keys when fetching from write-heavy sources.

## Constraints on `fetch()`

- **Synchronous**, returning a single `pandas.DataFrame`. Generators,
  `Iterator[DataFrame]`, and `async def` are not supported — the training
  pipeline calls `fetch(ctx)` directly and reads `.columns` immediately.
- **Whole-DataFrame in memory.** Recotem trains on the full result set
  (irspack constructs a sparse matrix from it). For larger-than-memory
  sources, do the chunking and aggregation inside `fetch()` and return a
  pre-aggregated DataFrame (e.g. counts of `(user, item)` pairs).
- **Credentials never come via `FetchContext.extra`** (it is reserved).
  Read them from environment variables (preferred — works with K8s
  Secrets, systemd `EnvironmentFile`, Docker `--env-file`) or from
  recipe-declared `Config` fields (but never accept secrets in YAML —
  reference an env var via `${RECOTEM_RECIPE_*}` instead).

## Compatibility

The plugin contract is part of the recotem 2.x public surface. Pin
`recotem>=2.0,<3` in your plugin's `pyproject.toml` — the `type_name` /
`Config` / `fetch(ctx)` shape is stable within a major version. The
`probe()` hook may gain optional parameters in a future minor release;
use `**kwargs: Any` if you want to be future-proof.

The entry-point key in `[project.entry-points."recotem.datasources"]` is
informational only (used in error messages); the discriminator is the
class's `type_name`. If two installed plugins both declare
`type_name = "csv"`, both `recotem train` and `recotem serve` exit 3 at
startup with both fully-qualified class names — uninstall one or rename
its `type_name`.

## Validation in `recotem validate`

`recotem validate recipes/my_recipe.yaml` instantiates the source class
(which exercises the `__init__` deferred-import / extras check) but does
**not** call `fetch()`. If the source defines an optional `probe()` method,
`recotem validate` calls it for a lightweight connectivity / auth check:

```python
def probe(self) -> dict:
    """Optional. Called by recotem validate to test connectivity.

    Should be cheap (LIMIT 1, dry-run, fs.exists, ...) — never load full data.
    Raise DataSourceError on failure.  Return a small status dict that
    recotem validate logs (e.g. {"status": "ok", "rows_to_emit": n_rows}).
    """
    ...
```

When `probe()` is defined, `recotem validate` reports `DataSource: probe OK
(<type_name>)`; when it is not, it reports `DataSource: extras OK
(<type_name>, no probe defined)`. The builtin `CSVSource` / `ParquetSource`
use `fsspec` `exists()`, and `BigQuerySource` uses a dry-run query job.

## Testing

Test `fetch()` directly without the CLI:

```python
from recotem_echo import EchoSource
from recotem.datasource.base import FetchContext

source = EchoSource(EchoSource.Config(n_users=20, n_items=50, n_rows=200))
ctx = FetchContext(recipe_name="test", run_id="abc")
df = source.fetch(ctx)
assert {"user_id", "item_id", "timestamp"}.issubset(df.columns)
assert len(df) == 200
```

Use `recotem.recipe.load_recipe` in integration tests to confirm the full
YAML → Recipe → DataSource path. `recipe.source` is an instance of the
plugin's `Config` model:

```python
from recotem.recipe import load_recipe
from recotem_echo import EchoSource

recipe = load_recipe("tests/fixtures/echo_recipe.yaml")
assert isinstance(recipe.source, EchoSource.Config)
```
