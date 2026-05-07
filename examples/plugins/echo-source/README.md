# Echo source plugin

Minimal third-party `DataSource` plugin for Recotem 2.0. `EchoSource`
returns a deterministic, statically-generated DataFrame keyed by
`(n_users, n_items, n_rows)`. It is suitable for tests, documentation, and
as a starting scaffold for real plugins — never for production training.

## What it demonstrates

- **Class-level contract:** `type_name`, `Config` (a pydantic model),
  `extras_required` — the three attributes Recotem inspects to register a
  plugin.
- **Instance methods:** `__init__(config)` that performs deferred imports
  of optional dependencies, `fetch(ctx) -> pandas.DataFrame`, and an
  optional `probe()` for `recotem validate` connectivity checks.
- **Entry-point declaration** in `pyproject.toml`:

  ```toml
  [project.entry-points."recotem.datasources"]
  echo = "recotem_echo:EchoSource"
  ```

  The key on the left of `=` is the discriminator value used in recipe
  YAML (`source.type: echo`). The value is `<module>:<class>`.

## Install

From the repository root:

```bash
uv pip install -e examples/plugins/echo-source/
```

Once installed in the same environment as `recotem`, the entry point is
discovered automatically.

## Use in a recipe

```yaml
name: echo_example

source:
  type: echo
  n_users: 20
  n_items: 50
  n_rows: 200

schema:
  user_column: user_id
  item_column: item_id

training:
  algorithms: [TopPop]
  n_trials: 1
  split:
    scheme: random
    heldout_ratio: 0.2

output:
  path: ./artifacts/echo_example.recotem
  versioning: always_overwrite
```

Then:

```bash
mkdir -p artifacts
uv run recotem train recipe.yaml
```

## Files

- `pyproject.toml` — package metadata and the `recotem.datasources` entry
  point that makes Recotem find `EchoSource`.
- `src/recotem_echo/__init__.py` — re-exports `EchoSource` at the package
  root so the entry-point string `recotem_echo:EchoSource` resolves.
- `src/recotem_echo/source.py` — the actual `DataSource` implementation
  with annotated explanations of each contract method.

## Authoring your own plugin

Copy this directory, rename `recotem-echo-source` /
`recotem_echo:EchoSource` / `type_name = "echo"` to fit your data source,
and replace `fetch()` with your real data fetch. The
[plugin-authoring docs](../../../docs/plugin-authoring.md) walk through the
full contract.
