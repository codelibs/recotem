# Contributing to Recotem

Thank you for your interest. This guide gets you from zero to a passing PR.

## Architecture in 60 seconds

Recotem is a single Python package with two execution modes:

- `recotem train recipe.yaml` — fetch → train → write a signed artifact.
- `recotem serve --recipes <dir>` — FastAPI server that watches the dir and
  serves `/v1/recipes/{name}:*` for every loaded recipe.

The two modes communicate only via the signed artifact file format, so the
trainer and the server can run on completely separate hosts.

See `CLAUDE.md` for the full layout, environment variables, and recipe model.

## Development setup

You need:

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) for dependency management.
- Docker (only if you want to validate the image build locally).

```bash
# Install all extras (bigquery + s3 + gcs + metrics)
uv sync --all-extras

# Run the test suite (skips slow MovieLens E2E by default)
uv run pytest tests

# Lint + format
uv run ruff check src tests
uv run ruff format src tests

# Generate a signing key for local play
uv run recotem keygen --type signing
```

## Making a change

1. **Find or write a spec** under `docs/` locally. That directory is
   gitignored — the published documentation lives at https://recotem.org
   (repo: [codelibs/recotem-docs](https://github.com/codelibs/recotem-docs)) —
   so a spec stays a working document and is never committed here. Substantive
   changes start as a design doc reviewed before any code lands.
2. **Branch off `main`**: `git checkout -b feat/your-thing`.
3. **Implement + test**. Every public-facing behavior gets a unit test, every
   cross-module flow gets an integration test, and any new code path that
   touches HMAC/auth/serialization gets a hypothesis fuzz test.
4. **Lint + format**. CI runs `ruff check` and `ruff format --check`.
5. **Run the e2e script** if your change affects the train→serve path:
   ```bash
   uv run bash tests/e2e/run.sh
   ```
   The `uv run` is load-bearing: the script calls the `recotem` console
   script, which `uv sync` installs into `.venv` and not onto `PATH`. Under
   bare `bash` it exits 127 with `recotem: command not found` immediately
   after `[e2e] Generating API key...`, which reads like a keygen failure
   rather than a `PATH` problem. Drop the prefix only inside an activated
   virtualenv.
6. **Open a PR**. The PR template asks for: what changed, why, how it was
   tested, and any spec doc that was updated.

## Test taxonomy

| Tier | Location | What goes there |
|---|---|---|
| Unit | `tests/unit/test_<module>.py` | Functions and classes from a single module. Fast, no network. |
| Integration | `tests/integration/` | Cross-module flows (e.g. train then serve). Uses FastAPI `TestClient`. |
| Fuzz | `tests/fuzz/` | Hypothesis property tests on parsers / loaders. Must never raise unhandled. |
| E2E | `tests/e2e/run.sh` | Bash script: install, generate CSV, train, serve, curl. |

Slow tests (anything > 5 s) carry `@pytest.mark.slow` and are deselected by
default. Run them with `uv run pytest tests -m slow`.

## Code style

- Ruff is authoritative. Line length 88. Configured rules live in
  `pyproject.toml`.
- Prefer plain `dataclass` over `pydantic` for purely internal data structures.
- `from __future__ import annotations` is used everywhere, including the
  serving router. What that costs is one rule in `serving/routes.py`: write a
  FastAPI dependency argument as `kid: str = Depends(_require_auth)`, never as
  `Annotated[str, Depends(_require_auth)]`, so `Depends` is resolved as a
  runtime default rather than as a stringified annotation.
- `recotem.training` and `recotem.serving` must not import each other.
- Public symbols are exported from each module's `__init__.py` and stay stable.
- structlog logger per module: `logger = structlog.get_logger(__name__)`.
- Never log API keys, signing keys, or cloud creds. The redaction processor
  in `recotem.log_redaction` is the safety net, not an excuse. It lives at the
  top level, not under `recotem.serving`, so a `train`-only invocation does not
  pull in the serving package.

## Adding a DataSource plugin

A third-party DataSource plugin is a small package that declares an entry
point and provides a class with `type_name`, `Config`, `extras_required`, and
a `fetch(self, ctx) -> pd.DataFrame` method. See
`https://recotem.org/2.1/docs/plugin-authoring.html` for the walkthrough and
`examples/plugins/echo-source/` for a runnable template.

## Security

If you find a security vulnerability, do **not** open a public issue. Use the
private security advisory mechanism described in `SECURITY.md`.

When changing anything that touches HMAC signing, the FQCN allow-list, the
recipe loader's path scheme rules, or env-var expansion, please flag the PR
description for an extra security review.

## License

By contributing you agree your code is licensed under the same terms as the
project (`LICENSE`).
