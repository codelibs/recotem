# Plugin Authoring

Recotem discovers DataSource plugins via Python entry points. A plugin is any installed package that registers in the `recotem.datasources` group.

The `examples/plugins/echo-source/` directory in this repository is a minimal, runnable reference implementation.

## Plugin contract

A plugin must provide a class with three class-level attributes and two methods.

```python
from __future__ import annotations

from typing import ClassVar
import pandas as pd
from pydantic import BaseModel
from recotem.datasource.base import DataSource, DataSourceError, FetchContext


class EchoSource:
    """Returns a fixed DataFrame — useful for testing and CI."""

    # 1. type_name: discriminator value in the recipe YAML source.type field.
    #    Must be unique across all installed plugins.
    #    Pattern: ^[a-z][a-z0-9_-]{0,31}$
    type_name: ClassVar[str] = "echo"

    # 2. Config: pydantic BaseModel describing the recipe subfields for this source.
    #    All fields appear under `source:` in the YAML after the `type:` discriminator.
    class Config(BaseModel):
        rows: int = 100
        n_users: int = 10
        n_items: int = 20

    # 3. extras_required: pip extras to suggest if the plugin's imports fail.
    #    Leave empty if the plugin has no optional dependencies.
    extras_required: ClassVar[list[str]] = []

    def __init__(self, config: "EchoSource.Config") -> None:
        self.config = config

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        """Return a DataFrame with columns matching the recipe schema."""
        import random
        rng = random.Random(42)
        return pd.DataFrame({
            "user_id": [f"u{rng.randint(0, self.config.n_users)}" for _ in range(self.config.rows)],
            "item_id": [f"i{rng.randint(0, self.config.n_items)}" for _ in range(self.config.rows)],
        })
```

### Rules

1. **`type_name`** is the discriminator value. It appears as `source.type: echo` in the recipe. Two plugins with the same `type_name` cause both `recotem train` and `recotem serve` to fail at startup with a clear error listing the conflicting packages.

2. **`Config`** is a pydantic `BaseModel`. Fields are validated at recipe load. Use pydantic validators for constraints. Required fields without defaults cause a `RecipeError` when missing from the recipe.

3. **`extras_required`** lists pip extras to suggest when the plugin's imports fail (see [Deferred imports](#deferred-imports)).

4. **`fetch(ctx)`** must return a `pd.DataFrame`. The DataFrame must contain at least the columns referenced in `recipe.schema` (`user_column`, `item_column`, and optionally `time_column`). Column names are validated after `fetch()` returns.

5. **`fetch()` must raise `DataSourceError`** for any external or transient failure (auth errors, network errors, query errors, empty results). Other exceptions surface as exit code 1. Wrap third-party exceptions explicitly:

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

```
recotem-echo-source/
├── pyproject.toml
└── recotem_echo/
    └── __init__.py     # EchoSource class
```

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "recotem-echo-source"
version = "0.1.0"
dependencies = ["recotem>=2.0", "pandas"]

[project.entry-points."recotem.datasources"]
echo = "recotem_echo:EchoSource"
```

The entry-point key (`echo`) is ignored by Recotem — the `type_name` class attribute is used as the discriminator. By convention, keep them the same.

## Install and use

```bash
pip install ./recotem-echo-source
```

Verify discovery:

```bash
recotem schema | python -c "import sys,json; s=json.load(sys.stdin); print(list(s['definitions'].keys()))"
# [..., 'EchoSourceConfig', ...]
```

Recipe:

```yaml
name: echo_test

source:
  type: echo
  rows: 500
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
    recipe_name: str    # the recipe's name field
    run_id: str         # unique ID for this training run (UUID)
```

Most plugins ignore `ctx`. It is useful for logging and for idempotency keys when fetching from write-heavy sources.

## Validation in `recotem validate`

`recotem validate recipes/my_recipe.yaml` calls `DataSource.__init__` (checking extras) but does **not** call `fetch()` unless the source defines a `probe()` method:

```python
def probe(self) -> None:
    """Optional. Called by recotem validate to test connectivity."""
    # Raise DataSourceError if auth or connectivity fails.
    ...
```

If `probe()` is defined, `recotem validate` calls it and reports the result. This lets operators confirm auth is working before scheduling a train run.

## Testing

Test `fetch()` directly without the CLI:

```python
from recotem_echo import EchoSource
from recotem.datasource.base import FetchContext

source = EchoSource(EchoSource.Config(rows=200))
ctx = FetchContext(recipe_name="test", run_id="abc")
df = source.fetch(ctx)
assert set(df.columns) == {"user_id", "item_id"}
assert len(df) == 200
```

Use `recotem.recipe.load_recipe` in integration tests to confirm the full YAML → Recipe → DataSource path:

```python
from recotem.recipe import load_recipe

recipe = load_recipe("tests/fixtures/echo_recipe.yaml")
assert recipe.source.type_name == "echo"
```
