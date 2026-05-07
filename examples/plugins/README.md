# DataSource plugin examples

Recotem 2.0 discovers third-party `DataSource` implementations through the
`recotem.datasources` Python entry-point group. This directory hosts
runnable example plugins that demonstrate the contract.

## Subdirectories

- [`echo-source/`](echo-source/) — minimal plugin that returns a static
  DataFrame. Useful for understanding the entry-point declaration, the
  `DataSource` Protocol, and how recipes pick a plugin up via
  `source.type`.

## Authoring your own plugin

The full plugin contract is documented in
[docs/plugin-authoring.md](../../docs/plugin-authoring.md). At a glance, a
plugin must:

1. Provide a class with the class-level attributes `type_name`, `Config`,
   and `extras_required`.
2. Implement `__init__(self, config)` and `fetch(self, ctx) -> pandas.DataFrame`.
   Optionally implement `probe(self) -> None` for `recotem validate`
   connectivity checks.
3. Declare itself under the `recotem.datasources` entry point in
   `pyproject.toml`.

The `echo-source` example is a working scaffold you can copy and adapt.

## Installing a plugin alongside Recotem

```bash
pip install recotem
pip install -e examples/plugins/echo-source
recotem train your-recipe-using-echo.yaml
```

When the plugin's wheel is installed in the same environment as Recotem,
its entry point is discovered automatically — no Recotem code changes
required.
