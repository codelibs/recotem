# Plugin Authoring

Recotem discovers DataSource plugins via Python entry points. A plugin is any installed package that registers in the `recotem.datasources` group.

The `examples/plugins/echo-source/` directory in this repository is a minimal, runnable reference implementation.

## Plugin contract

A plugin must provide a class with three class-level attributes and one
required method (`fetch`); `__init__` and the optional `probe` are described
below.

```python
from __future__ import annotations

from typing import ClassVar
import pandas as pd
from pydantic import BaseModel, Field
from recotem.datasource.base import DataSourceError, FetchContext


class EchoSource:
    """Returns a fixed DataFrame — useful for testing and CI."""

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
        n_rows: int = Field(default=100, ge=1)
        n_users: int = Field(default=10, ge=1)
        n_items: int = Field(default=20, ge=1)

    # 3. extras_required: pip extras to suggest when optional dependencies
    #    are missing.  Leave empty if the plugin has no optional deps.
    extras_required: ClassVar[list[str]] = []

    def __init__(self, config: "EchoSource.Config") -> None:
        self._config = config

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        """Return a DataFrame whose columns include those named in
        the recipe `schema` block (user_column, item_column, optional
        time_column)."""
        import random
        rng = random.Random(42)
        return pd.DataFrame({
            "user_id": [f"u{rng.randint(0, self._config.n_users)}" for _ in range(self._config.n_rows)],
            "item_id": [f"i{rng.randint(0, self._config.n_items)}" for _ in range(self._config.n_rows)],
        })
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
pip install ./recotem-echo-source
```

Verify discovery by running `recotem validate` against a recipe that uses the
plugin — the loader resolves `source.type` through the entry-point registry
and will report `Unknown DataSource type 'echo'` if the plugin is not
installed in the same environment as `recotem`.

> Note: `recotem schema` emits the JSON Schema for the top-level `Recipe`
> model only. `Recipe.source` is typed as `Any` because the discriminated
> union is built dynamically from entry points, so plugin `Config` schemas
> do not appear in that output.

Recipe:

```yaml
name: echo_test

source:
  type: echo
  n_rows: 500
  n_users: 50
  n_items: 100

schema:
  user_column: user_id
  item_column: item_id

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
def probe(self) -> None:
    """Optional. Called by recotem validate to test connectivity.

    Should be cheap (LIMIT 1, dry-run, fs.exists, ...) — never load full data.
    Raise DataSourceError on failure.
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

source = EchoSource(EchoSource.Config(n_rows=200))
ctx = FetchContext(recipe_name="test", run_id="abc")
df = source.fetch(ctx)
assert {"user_id", "item_id"}.issubset(df.columns)
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
