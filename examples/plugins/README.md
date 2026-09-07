# DataSource plugin examples

Recotem discovers third-party `DataSource` implementations through the
`recotem.datasources` Python entry-point group. This directory hosts
runnable example plugins that demonstrate the contract.

## Subdirectories

- [`echo-source/`](echo-source/) — minimal plugin that returns a static
  DataFrame. Useful for understanding the entry-point declaration, the
  `DataSource` Protocol, and how recipes pick a plugin up via
  `source.type`.

## Authoring your own plugin

The full plugin contract is documented in
[Plugin authoring](https://recotem.org/2.1/docs/plugin-authoring). At a glance, a
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

From the repository root. `uv sync` installs the CLI into `.venv` and does not
put it on `PATH`, so the commands below use `uv run` to reach it (as
[`echo-source/README.md`](echo-source/README.md) does). If you installed with
`pip install recotem` into an active virtualenv instead, drop the `uv run`
prefix and use plain `pip install -e` on the second line.

```bash
uv pip install -e examples/plugins/echo-source

# `recotem train` refuses to write an unsigned artifact: without a signing key
# it exits 8 with `RECOTEM_SIGNING_KEYS is not set`.
export $(uv run recotem keygen --type signing | grep '^env_entry=' | sed 's/^env_entry=//')

uv run recotem train your-recipe-using-echo.yaml
```

The `uv run` on the `export` line is the one that matters most. If `recotem` is
not on `PATH`, the command substitution produces nothing and `export` is left
with no arguments — which is not an error but a request to **print the whole
environment**, so the step exits 0, floods the terminal with every exported
variable you have, and leaves `RECOTEM_SIGNING_KEYS` unset. The failure then
arrives one command later as an unrelated-looking exit 8.

When the plugin's wheel is installed in the same environment as Recotem,
its entry point is discovered automatically — no Recotem code changes
required.
