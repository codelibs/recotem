# Tutorial-grade HTTPS source + getting-started guide — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an end-to-end Recotem 2.0 tutorial that runs from `git clone` to a working `/predict` call by fetching a small public CSV over HTTPS, plus the data-source / loader changes that make it viable.

**Architecture:** Replace the path-scheme allow-list with a direction-aware policy (input paths permissive, output paths reject non-writeable schemes); add a mandatory `sha256` integrity pin and `RECOTEM_MAX_DOWNLOAD_BYTES` cap for HTTP/HTTPS source paths; fetch via stdlib `urllib.request` so no new runtime deps are added; ship a `examples/tutorial-purchase-log/` recipe + `docs/getting-started.md` consolidated entry point.

**Tech Stack:** Python 3.12+, pydantic v2, pandas, fsspec, stdlib `urllib.request`, structlog, pytest 8 + hypothesis 6, pytest-httpserver (new dev dep), Typer.

**Spec:** `docs/superpowers/specs/2026-05-07-tutorial-and-https-source-design.md`

---

## File structure preview

| File | Action | Responsibility |
|------|--------|----------------|
| `src/recotem/config.py` | Modify | Read `RECOTEM_MAX_DOWNLOAD_BYTES`, `RECOTEM_HTTP_TIMEOUT_SECONDS` |
| `src/recotem/recipe/models.py` | Modify | Add `sha256` field on `ItemMetadataConfig` |
| `src/recotem/datasource/csv.py` | Modify | Add `sha256` field on `CSVConfig`/`ParquetConfig`, urllib HTTP fetch path, sha256 verify, byte cap, URL userinfo redaction |
| `src/recotem/recipe/loader.py` | Modify | Drop allow-list; split into input/output path validation; sha256-required-for-network post-validator |
| `tests/unit/test_recipe_loader.py` | Modify | Update scheme tests for new policy |
| `tests/unit/test_csv_source.py` | Modify | Add sha256 / byte-cap / userinfo-redaction tests |
| `tests/unit/test_recipe_models.py` | Modify | Add `item_metadata.sha256` test |
| `tests/integration/test_https_csv_source.py` | Create | pytest-httpserver-based end-to-end integration test |
| `pyproject.toml` | Modify | Add `pytest-httpserver` to `[dependency-groups].dev` |
| `examples/tutorial-purchase-log/recipe.yaml` | Create | Tutorial recipe (HTTPS URL + sha256) |
| `examples/tutorial-purchase-log/README.md` | Create | Tutorial example orientation |
| `docker-compose.example.yaml` | Modify | Bind-mount tutorial recipe; volume mount path → `/workspace/artifacts`; rewritten header |
| `docs/getting-started.md` | Create | Single canonical entry point (Docker + pip paths) |
| `docs/quickstart.md` | Delete | Content folded into `getting-started.md` |
| `docs/README.md` | Modify | Replace Quickstart link with Getting Started |
| `docs/data-sources/csv.md` | Modify | Document network schemes, `sha256`, `RECOTEM_MAX_DOWNLOAD_BYTES` |
| `docs/security.md` | Modify | Drop allow-list claim; add network-fetch threat rows |
| `docs/recipe-reference.md` | Modify | Document `sha256` and rewrite "Rejected schemes" paragraph |
| `docs/deployment/docker.md` | Modify | Update reference to compose example to match volume-path change |
| `README.md` | Modify | Re-link to `getting-started.md`; add cross-reference from "Hello world" |
| `CLAUDE.md` | Modify | Rewrite path-scheme bullet; refresh dir listing & references |
| `tests/e2e/` | Modify | Add `--tutorial` mode |

---

## Task 1: Add `RECOTEM_MAX_DOWNLOAD_BYTES` and `RECOTEM_HTTP_TIMEOUT_SECONDS` to config

**Files:**
- Modify: `src/recotem/config.py` (extend with two new env-var readers)
- Create: `tests/unit/test_config_download.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_config_download.py`:

```python
"""Tests for RECOTEM_MAX_DOWNLOAD_BYTES and RECOTEM_HTTP_TIMEOUT_SECONDS."""

from __future__ import annotations

import pytest

from recotem.config import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_MAX_DOWNLOAD_BYTES,
    get_http_timeout_seconds,
    get_max_download_bytes,
)


def test_max_download_bytes_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECOTEM_MAX_DOWNLOAD_BYTES", raising=False)
    assert get_max_download_bytes() == DEFAULT_MAX_DOWNLOAD_BYTES
    assert DEFAULT_MAX_DOWNLOAD_BYTES == 256 * 1024 * 1024


def test_max_download_bytes_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_DOWNLOAD_BYTES", str(1024 * 1024))  # 1 MiB
    assert get_max_download_bytes() == 1024 * 1024


def test_max_download_bytes_below_min_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_DOWNLOAD_BYTES", "0")
    # Clamp to 1 MiB minimum
    assert get_max_download_bytes() == 1024 * 1024


def test_max_download_bytes_above_max_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_DOWNLOAD_BYTES", str(64 * 1024 * 1024 * 1024))
    # Clamp to 16 GiB maximum
    assert get_max_download_bytes() == 16 * 1024 * 1024 * 1024


def test_max_download_bytes_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_DOWNLOAD_BYTES", "not-a-number")
    assert get_max_download_bytes() == DEFAULT_MAX_DOWNLOAD_BYTES


def test_http_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECOTEM_HTTP_TIMEOUT_SECONDS", raising=False)
    assert get_http_timeout_seconds() == DEFAULT_HTTP_TIMEOUT_SECONDS
    assert DEFAULT_HTTP_TIMEOUT_SECONDS == 30


def test_http_timeout_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_HTTP_TIMEOUT_SECONDS", "60")
    assert get_http_timeout_seconds() == 60


def test_http_timeout_below_min_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_HTTP_TIMEOUT_SECONDS", "0")
    assert get_http_timeout_seconds() == 1


def test_http_timeout_above_max_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_HTTP_TIMEOUT_SECONDS", "9999")
    assert get_http_timeout_seconds() == 600


def test_http_timeout_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_HTTP_TIMEOUT_SECONDS", "abc")
    assert get_http_timeout_seconds() == DEFAULT_HTTP_TIMEOUT_SECONDS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config_download.py -v`
Expected: ImportError or AttributeError (`get_max_download_bytes`, etc., not defined).

- [ ] **Step 3: Implement the readers in `src/recotem/config.py`**

Append to `src/recotem/config.py` (just after the existing constants block; keep ordering consistent with the rest of the module):

```python
# ---------------------------------------------------------------------------
# Network-fetch caps (used by datasource/csv.py for HTTP/HTTPS sources)
# ---------------------------------------------------------------------------

DEFAULT_MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024  # 256 MiB
_MIN_DOWNLOAD_BYTES = 1 * 1024 * 1024            # 1 MiB
_MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024 * 1024    # 16 GiB

DEFAULT_HTTP_TIMEOUT_SECONDS = 30
_MIN_HTTP_TIMEOUT_SECONDS = 1
_MAX_HTTP_TIMEOUT_SECONDS = 600


def get_max_download_bytes() -> int:
    """Return RECOTEM_MAX_DOWNLOAD_BYTES, clamped to [1 MiB, 16 GiB]."""
    raw = os.environ.get("RECOTEM_MAX_DOWNLOAD_BYTES", "")
    if not raw:
        return DEFAULT_MAX_DOWNLOAD_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_DOWNLOAD_BYTES
    if value < _MIN_DOWNLOAD_BYTES:
        return _MIN_DOWNLOAD_BYTES
    if value > _MAX_DOWNLOAD_BYTES:
        return _MAX_DOWNLOAD_BYTES
    return value


def get_http_timeout_seconds() -> int:
    """Return RECOTEM_HTTP_TIMEOUT_SECONDS, clamped to [1, 600]."""
    raw = os.environ.get("RECOTEM_HTTP_TIMEOUT_SECONDS", "")
    if not raw:
        return DEFAULT_HTTP_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_HTTP_TIMEOUT_SECONDS
    if value < _MIN_HTTP_TIMEOUT_SECONDS:
        return _MIN_HTTP_TIMEOUT_SECONDS
    if value > _MAX_HTTP_TIMEOUT_SECONDS:
        return _MAX_HTTP_TIMEOUT_SECONDS
    return value
```

Update the module docstring's "Environment variables" section to add the two new entries.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config_download.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/recotem/config.py tests/unit/test_config_download.py
git commit -m "feat(config): add RECOTEM_MAX_DOWNLOAD_BYTES and RECOTEM_HTTP_TIMEOUT_SECONDS readers"
```

---

## Task 2: Add `sha256` field to `CSVConfig`, `ParquetConfig`, `ItemMetadataConfig`

**Files:**
- Modify: `src/recotem/datasource/csv.py:18-30` (CSVConfig), `src/recotem/datasource/csv.py:106-110` (ParquetConfig)
- Modify: `src/recotem/recipe/models.py` (ItemMetadataConfig)
- Modify: `tests/unit/test_recipe_models.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_recipe_models.py`:

```python
def test_csvconfig_sha256_valid_lowercase_hex_accepted() -> None:
    from recotem.datasource.csv import CSVConfig

    cfg = CSVConfig(
        type="csv",
        path="/tmp/x.csv",
        sha256="945fc769205a5976d38c5783500ae473afbb04608043b703951a699993c8f8be",
    )
    assert cfg.sha256 == (
        "945fc769205a5976d38c5783500ae473afbb04608043b703951a699993c8f8be"
    )


def test_csvconfig_sha256_uppercase_rejected() -> None:
    import pydantic

    from recotem.datasource.csv import CSVConfig

    with pytest.raises(pydantic.ValidationError):
        CSVConfig(
            type="csv",
            path="/tmp/x.csv",
            sha256="945FC769205A5976D38C5783500AE473AFBB04608043B703951A699993C8F8BE",
        )


def test_csvconfig_sha256_wrong_length_rejected() -> None:
    import pydantic

    from recotem.datasource.csv import CSVConfig

    with pytest.raises(pydantic.ValidationError):
        CSVConfig(type="csv", path="/tmp/x.csv", sha256="abcd1234")


def test_csvconfig_sha256_optional_when_unset() -> None:
    from recotem.datasource.csv import CSVConfig

    cfg = CSVConfig(type="csv", path="/tmp/x.csv")
    assert cfg.sha256 is None


def test_parquetconfig_sha256_accepted() -> None:
    from recotem.datasource.csv import ParquetConfig

    cfg = ParquetConfig(
        type="parquet",
        path="/tmp/x.parquet",
        sha256="0" * 64,
    )
    assert cfg.sha256 == "0" * 64


def test_itemmetadata_sha256_accepted() -> None:
    from recotem.recipe.models import ItemMetadataConfig

    cfg = ItemMetadataConfig(
        type="csv",
        path="/tmp/items.csv",
        sha256="a" * 64,
        fields=["title"],
    )
    assert cfg.sha256 == "a" * 64


def test_itemmetadata_sha256_invalid_rejected() -> None:
    import pydantic

    from recotem.recipe.models import ItemMetadataConfig

    with pytest.raises(pydantic.ValidationError):
        ItemMetadataConfig(
            type="csv", path="/tmp/x.csv", sha256="not-hex", fields=["title"]
        )
```

If `tests/unit/test_recipe_models.py` does not exist yet, create it with `import pytest` at top.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_recipe_models.py -v -k sha256`
Expected: FAIL with `pydantic.ValidationError: extra fields not permitted`.

- [ ] **Step 3: Add `sha256` to the three config models**

In `src/recotem/datasource/csv.py`, modify `CSVConfig`:

```python
class CSVConfig(BaseModel, extra="forbid"):
    """Configuration schema for CSV sources."""

    type: str = Field(default="csv", pattern=r"^csv$")
    path: str
    delimiter: str = ","
    encoding: str = "utf-8"
    header: int = 0
    dtype: dict[str, str] | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
```

And `ParquetConfig`:

```python
class ParquetConfig(BaseModel, extra="forbid"):
    """Configuration schema for Parquet sources."""

    type: str = Field(default="parquet", pattern=r"^parquet$")
    path: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
```

In `src/recotem/recipe/models.py`, modify `ItemMetadataConfig`:

```python
class ItemMetadataConfig(BaseModel, extra="forbid"):
    """Optional item metadata join configuration."""

    type: str = Field(pattern=r"^(csv|parquet)$")
    path: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    fields: list[str] = Field(min_length=1)
    on_field_missing: str = Field(
        default="error",
        pattern=r"^(error|null)$",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_recipe_models.py -v -k sha256`
Expected: 7 passed.

Run also: `uv run pytest tests/unit -v` to ensure nothing else broke.
Expected: all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/recotem/datasource/csv.py src/recotem/recipe/models.py tests/unit/test_recipe_models.py
git commit -m "feat(recipe): add sha256 integrity field on CSV/Parquet/ItemMetadata configs"
```

---

## Task 3: Loader — split path validation into input/output, drop allow-list

**Files:**
- Modify: `src/recotem/recipe/loader.py:22-58` (replace allow-list block + `_validate_path`)
- Modify: `src/recotem/recipe/loader.py:_validate_path_fields` (call site rewiring)
- Modify: `tests/unit/test_recipe_loader.py:170-205` (rewrite the four `test_path_field_*` tests for the new policy)

- [ ] **Step 1: Update the failing tests**

In `tests/unit/test_recipe_loader.py`, replace the "Path scheme allow-list" section's tests with:

```python
# ---------------------------------------------------------------------------
# Path scheme — direction-aware policy
# ---------------------------------------------------------------------------


def test_input_source_with_https_scheme_accepted_when_sha256_set(
    tmp_path: Path,
) -> None:
    """HTTPS source paths load when sha256 is provided. (Network rule covered in Task 4.)"""
    content = """\
name: https_input_ok
source:
  type: csv
  path: https://example.com/data.csv
  sha256: 0000000000000000000000000000000000000000000000000000000000000000
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: %s
""" % str(tmp_path / "https_input_ok.recotem")
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.source.path == "https://example.com/data.csv"


def test_input_source_with_file_scheme_accepted(tmp_path: Path) -> None:
    """file:// is accepted on input paths (equivalent to bare local)."""
    content = """\
name: file_input_ok
source:
  type: csv
  path: file:///tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: %s
""" % str(tmp_path / "file_input_ok.recotem")
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.source.path == "file:///tmp/data.csv"


def test_output_path_with_http_scheme_rejected(tmp_path: Path) -> None:
    """http:// is not writeable by fsspec; reject at load time."""
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="http_output",
        output_path="http://example.com/out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="does not support"):
        load_recipe(p)


def test_output_path_with_https_scheme_rejected(tmp_path: Path) -> None:
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="https_output",
        output_path="https://example.com/out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="does not support"):
        load_recipe(p)


def test_output_path_with_memory_scheme_rejected(tmp_path: Path) -> None:
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="memory_output",
        output_path="memory://out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="does not support"):
        load_recipe(p)


def test_output_path_with_file_scheme_accepted(tmp_path: Path) -> None:
    """file:// is treated equivalent to bare local path on output."""
    out = tmp_path / "out.recotem"
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="file_output_ok",
        output_path=f"file://{out}",
    )
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.output.path == f"file://{out}"


def test_output_path_with_s3_scheme_accepted(tmp_path: Path) -> None:
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="s3_output_ok",
        output_path="s3://my-bucket/out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.output.path == "s3://my-bucket/out.recotem"


def test_input_source_with_embedded_credentials_rejected(tmp_path: Path) -> None:
    content = """\
name: cred_input
source:
  type: csv
  path: https://user:pass@example.com/data.csv
  sha256: 0000000000000000000000000000000000000000000000000000000000000000
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: %s
""" % str(tmp_path / "cred.recotem")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="credentials"):
        load_recipe(p)


def test_output_path_with_embedded_credentials_rejected(tmp_path: Path) -> None:
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="cred_output",
        output_path="s3://AKIA123:secret@bucket/out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="credentials"):
        load_recipe(p)
```

Delete the old `test_path_field_with_file_scheme_rejected`,
`test_path_field_with_http_scheme_rejected`,
`test_path_field_with_embedded_credentials_rejected`,
`test_s3_path_without_credentials_accepted` tests (replaced by the above).

- [ ] **Step 2: Run tests to verify they fail or behave inconsistently**

Run: `uv run pytest tests/unit/test_recipe_loader.py -v -k "scheme or credentials"`
Expected: several tests FAIL (the loader still rejects HTTPS / file:// inputs).

- [ ] **Step 3: Rewrite the loader path-scheme section**

In `src/recotem/recipe/loader.py`, replace the block from line 22 (just after the
imports) through the end of `_validate_path` with:

```python
# ---------------------------------------------------------------------------
# Path-scheme policy
# ---------------------------------------------------------------------------

# Schemes for which `output.path` is rejected because writing is not
# supported by fsspec / urllib. (See spec §5.3.)
_OUTPUT_REJECTED_SCHEMES: frozenset[str] = frozenset(
    {"http", "https", "ftp", "ftps", "memory"}
)

# Schemes that involve an unauthenticated network fetch. Used by the
# sha256-required post-validator.
_NETWORK_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def _network_scheme(path: str) -> bool:
    """True iff *path* uses a scheme in `_NETWORK_SCHEMES`."""
    return urlparse(path).scheme.lower() in _NETWORK_SCHEMES


def _check_userinfo(path: str, field_name: str) -> None:
    parsed = urlparse(path)
    if parsed.username or parsed.password:
        raise RecipeError(
            f"'{field_name}' contains embedded credentials in the URI. "
            "Use environment-based authentication instead."
        )


def _validate_input_path(path: str, field_name: str) -> None:
    """Validate an input-side path (source.path, item_metadata.path)."""
    _check_userinfo(path, field_name)


def _validate_output_path(path: str, field_name: str) -> None:
    """Validate an output-side path (output.path)."""
    _check_userinfo(path, field_name)
    parsed = urlparse(path)
    scheme = (parsed.scheme or "").lower()
    if scheme in _OUTPUT_REJECTED_SCHEMES:
        raise RecipeError(
            f"'{field_name}' uses scheme '{scheme}://' which does not support "
            "writes. Use a bare local path, file://, s3://, gs://, or az://."
        )
```

Update `_validate_path_fields` (lower in the file) to call the new helpers:

```python
def _validate_path_fields(data: dict[str, Any]) -> None:
    """Validate scheme + credentials for all path fields in the raw dict."""
    output = data.get("output")
    if isinstance(output, dict):
        output_path = output.get("path")
        if isinstance(output_path, str):
            _validate_output_path(output_path, "output.path")

    source = data.get("source")
    if isinstance(source, dict):
        source_path = source.get("path")
        if isinstance(source_path, str):
            _validate_input_path(source_path, "source.path")

    item_metadata = data.get("item_metadata")
    if isinstance(item_metadata, dict):
        meta_path = item_metadata.get("path")
        if isinstance(meta_path, str):
            _validate_input_path(meta_path, "item_metadata.path")
```

- [ ] **Step 4: Run all loader tests**

Run: `uv run pytest tests/unit/test_recipe_loader.py -v`
Expected: all pass (sha256-required-for-network rule comes in Task 4).

- [ ] **Step 5: Commit**

```bash
git add src/recotem/recipe/loader.py tests/unit/test_recipe_loader.py
git commit -m "refactor(recipe): split path validation into input/output; drop allow-list"
```

---

## Task 4: Loader — sha256-required post-validator for network schemes

**Files:**
- Modify: `src/recotem/recipe/loader.py` (add post-validator and call site after `Recipe.model_validate`)
- Modify: `tests/unit/test_recipe_loader.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_recipe_loader.py`:

```python
# ---------------------------------------------------------------------------
# sha256 required for network-scheme input paths
# ---------------------------------------------------------------------------


def test_https_source_without_sha256_rejected(tmp_path: Path) -> None:
    content = """\
name: https_no_sha
source:
  type: csv
  path: https://example.com/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: %s
""" % str(tmp_path / "out.recotem")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="sha256"):
        load_recipe(p)


def test_http_source_without_sha256_rejected(tmp_path: Path) -> None:
    content = """\
name: http_no_sha
source:
  type: csv
  path: http://example.com/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: %s
""" % str(tmp_path / "out.recotem")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="sha256"):
        load_recipe(p)


def test_https_item_metadata_without_sha256_rejected(tmp_path: Path) -> None:
    content = """\
name: https_meta_no_sha
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
item_metadata:
  type: csv
  path: https://example.com/items.csv
  fields: [title]
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: %s
""" % str(tmp_path / "out.recotem")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="sha256"):
        load_recipe(p)


def test_local_source_without_sha256_accepted(tmp_path: Path) -> None:
    """Bare local paths don't require sha256."""
    p = _minimal(tmp_path, name="local_no_sha")
    recipe = load_recipe(p)
    assert recipe.source.sha256 is None


def test_s3_source_without_sha256_accepted(tmp_path: Path) -> None:
    """s3:// is not a network-fetch scheme — sha256 stays optional."""
    content = """\
name: s3_no_sha
source:
  type: csv
  path: s3://my-bucket/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: %s
""" % str(tmp_path / "out.recotem")
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.source.sha256 is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_recipe_loader.py -v -k sha256`
Expected: the three "without_sha256_rejected" tests FAIL — the loader currently
accepts those recipes.

- [ ] **Step 3: Add the post-validator and wire it in**

Append to `src/recotem/recipe/loader.py` after the `_validate_path_fields`
function (or just before it — keep existing organization):

```python
def _enforce_sha256_for_network_paths(recipe: Recipe) -> None:
    """For source / item_metadata paths using a network scheme, require sha256.

    Raises
    ------
    RecipeError
        If a network-scheme path is missing the integrity pin.
    """
    src = recipe.source
    src_path = getattr(src, "path", None)
    if isinstance(src_path, str) and _network_scheme(src_path):
        if not getattr(src, "sha256", None):
            raise RecipeError(
                f"'source.path' uses a network scheme "
                f"({urlparse(src_path).scheme}://) and requires a 'sha256' "
                "integrity pin. Compute it with `shasum -a 256 <file>` and "
                "set `source.sha256: <hex>`."
            )

    meta = recipe.item_metadata
    if meta is not None:
        meta_path = getattr(meta, "path", None)
        if isinstance(meta_path, str) and _network_scheme(meta_path):
            if not getattr(meta, "sha256", None):
                raise RecipeError(
                    f"'item_metadata.path' uses a network scheme "
                    f"({urlparse(meta_path).scheme}://) and requires a "
                    "'sha256' integrity pin."
                )
```

In `load_recipe`, just after the source-config promotion step and before the
local-output containment check, add:

```python
    # Enforce sha256 integrity pin for network-scheme paths.
    _enforce_sha256_for_network_paths(recipe)
```

- [ ] **Step 4: Run all loader tests**

Run: `uv run pytest tests/unit/test_recipe_loader.py -v`
Expected: all pass (including the new sha256-required tests).

- [ ] **Step 5: Commit**

```bash
git add src/recotem/recipe/loader.py tests/unit/test_recipe_loader.py
git commit -m "feat(recipe): require sha256 on network-scheme source paths"
```

---

## Task 5: CSV/Parquet — sha256 verification for non-network schemes

**Files:**
- Modify: `src/recotem/datasource/csv.py` (CSVSource.fetch and ParquetSource.fetch)
- Modify: `tests/unit/test_csv_source.py` (or create if absent)

This task adds the **non-network** sha256 verify path. Network/HTTP fetch
comes in Task 6 to keep diffs small.

- [ ] **Step 1: Write the failing tests**

If `tests/unit/test_csv_source.py` does not exist, create it:

```python
"""Unit tests for recotem.datasource.csv (sha256 + byte cap)."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import pytest

from recotem.datasource.base import DataSourceError, FetchContext
from recotem.datasource.csv import CSVConfig, CSVSource


def _ctx() -> FetchContext:
    return FetchContext(recipe_name="t", run_id="r")


def _write_csv(path: Path, body: str) -> str:
    path.write_text(body)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_csv_local_sha256_match_loads(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    digest = _write_csv(csv_path, "user_id,item_id\n1,a\n2,b\n")
    cfg = CSVConfig(type="csv", path=str(csv_path), sha256=digest)
    df = CSVSource(cfg).fetch(_ctx())
    assert len(df) == 2
    assert list(df.columns) == ["user_id", "item_id"]


def test_csv_local_sha256_mismatch_raises(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, "user_id,item_id\n1,a\n")
    bogus_digest = "0" * 64
    cfg = CSVConfig(type="csv", path=str(csv_path), sha256=bogus_digest)
    with pytest.raises(DataSourceError, match="sha256"):
        CSVSource(cfg).fetch(_ctx())


def test_csv_local_no_sha256_loads(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, "user_id,item_id\n1,a\n2,b\n")
    cfg = CSVConfig(type="csv", path=str(csv_path))
    df = CSVSource(cfg).fetch(_ctx())
    assert len(df) == 2


def test_csv_local_gzip_sha256_match(tmp_path: Path) -> None:
    """sha256 is computed over the raw on-disk bytes (post-gzip)."""
    csv_path = tmp_path / "data.csv.gz"
    body = b"user_id,item_id\n1,a\n2,b\n"
    csv_path.write_bytes(gzip.compress(body))
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    cfg = CSVConfig(type="csv", path=str(csv_path), sha256=digest)
    df = CSVSource(cfg).fetch(_ctx())
    assert len(df) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_csv_source.py -v`
Expected: FAIL — the existing fetch ignores `sha256`.

- [ ] **Step 3: Restructure `CSVSource.fetch` and `ParquetSource.fetch`**

In `src/recotem/datasource/csv.py`, replace the `CSVSource.fetch` method body
and add a new helper. Keep imports tidy:

```python
import hashlib
import hmac
from io import BytesIO
from urllib.parse import urlparse, urlunparse

# Existing imports above...
```

Add these helpers near the top (after the `logger = ...` line):

```python
_NETWORK_SCHEMES: frozenset[str] = frozenset({"http", "https"})

_COMPRESSION_MAP: dict[str, str] = {
    ".gz": "gzip",
    ".bz2": "bz2",
    ".zip": "zip",
    ".xz": "xz",
}


def _redact_url_userinfo(path: str) -> str:
    """Strip any userinfo from URL-shaped *path* before logging."""
    parsed = urlparse(path)
    if not parsed.username and not parsed.password:
        return path
    netloc = parsed.hostname or ""
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _infer_compression(path: str) -> str | None:
    """Pandas-style compression hint from path extension. Returns None if plain."""
    lower_path = urlparse(path).path.lower() if "://" in path else path.lower()
    for ext, codec in _COMPRESSION_MAP.items():
        if lower_path.endswith(ext):
            return codec
    return None


def _verify_sha256(actual: bytes, expected_hex: str) -> None:
    """hmac.compare_digest the sha256 of *actual* vs *expected_hex*."""
    digest = hashlib.sha256(actual).hexdigest()
    if not hmac.compare_digest(digest, expected_hex):
        # Show only first 8 chars on each side to avoid leaking ground truth.
        raise DataSourceError(
            f"sha256 mismatch: got {digest[:8]}…, expected {expected_hex[:8]}…"
        )
```

Replace `CSVSource.fetch`:

```python
    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        import fsspec
        import pandas as pd

        cfg = self._config
        scheme = urlparse(cfg.path).scheme.lower()
        is_network = scheme in _NETWORK_SCHEMES
        safe_path = _redact_url_userinfo(cfg.path)

        logger.info(
            "csv_source_fetch_start",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            path=safe_path,
            scheme=scheme or "local",
        )

        if is_network:
            # Implemented in Task 6; raise here so callers get a clear error
            # if they try to use the partial implementation.
            raise DataSourceError(
                f"Network-scheme CSV fetch is not yet wired for '{cfg.path}'."
            )

        # Non-network path: read via fsspec, then verify sha256 if set.
        try:
            with fsspec.open(cfg.path, "rb") as f:
                raw_bytes = f.read()
        except FileNotFoundError as exc:
            raise DataSourceError(f"CSV file not found: {cfg.path}") from exc
        except PermissionError as exc:
            raise DataSourceError(
                f"Permission denied reading CSV file: {cfg.path}"
            ) from exc
        except Exception as exc:
            raise DataSourceError(
                f"Failed to read CSV from '{cfg.path}': {exc}"
            ) from exc

        if cfg.sha256 is not None:
            _verify_sha256(raw_bytes, cfg.sha256)
            sha256_verified = True
        else:
            sha256_verified = False

        compression = _infer_compression(cfg.path)

        read_kwargs: dict = {
            "sep": cfg.delimiter,
            "encoding": cfg.encoding,
            "header": cfg.header,
            "compression": compression,
        }
        if cfg.dtype:
            read_kwargs["dtype"] = cfg.dtype

        try:
            df: pd.DataFrame = pd.read_csv(BytesIO(raw_bytes), **read_kwargs)
        except Exception as exc:
            raise DataSourceError(
                f"Failed to parse CSV from '{safe_path}': {exc}"
            ) from exc

        if df.empty:
            raise DataSourceError(
                f"CSV file '{safe_path}' is empty (no data rows after header)."
            )

        logger.info(
            "csv_source_fetch_done",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            path=safe_path,
            rows=len(df),
            bytes=len(raw_bytes),
            sha256_verified=sha256_verified,
            columns=list(df.columns),
        )
        return df
```

Apply the analogous restructure to `ParquetSource.fetch` (no compression
inference; pandas reads parquet directly from BytesIO):

```python
    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        import fsspec
        import pandas as pd

        cfg = self._config
        scheme = urlparse(cfg.path).scheme.lower()
        is_network = scheme in _NETWORK_SCHEMES
        safe_path = _redact_url_userinfo(cfg.path)

        logger.info(
            "parquet_source_fetch_start",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            path=safe_path,
            scheme=scheme or "local",
        )

        if is_network:
            raise DataSourceError(
                f"Network-scheme Parquet fetch is not yet wired for '{cfg.path}'."
            )

        try:
            with fsspec.open(cfg.path, "rb") as f:
                raw_bytes = f.read()
        except FileNotFoundError as exc:
            raise DataSourceError(f"Parquet file not found: {cfg.path}") from exc
        except PermissionError as exc:
            raise DataSourceError(
                f"Permission denied reading Parquet file: {cfg.path}"
            ) from exc
        except Exception as exc:
            raise DataSourceError(
                f"Failed to read Parquet from '{cfg.path}': {exc}"
            ) from exc

        if cfg.sha256 is not None:
            _verify_sha256(raw_bytes, cfg.sha256)
            sha256_verified = True
        else:
            sha256_verified = False

        try:
            df: pd.DataFrame = pd.read_parquet(BytesIO(raw_bytes))
        except Exception as exc:
            raise DataSourceError(
                f"Failed to parse Parquet from '{safe_path}': {exc}"
            ) from exc

        logger.info(
            "parquet_source_fetch_done",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            path=safe_path,
            rows=len(df),
            bytes=len(raw_bytes),
            sha256_verified=sha256_verified,
            columns=list(df.columns),
        )
        return df
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_csv_source.py tests/unit -v`
Expected: new tests pass; existing CSV/Parquet tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/recotem/datasource/csv.py tests/unit/test_csv_source.py
git commit -m "feat(datasource): verify sha256 on local/object-store CSV and Parquet reads"
```

---

## Task 6: CSV — HTTP/HTTPS fetch via stdlib urllib

**Files:**
- Modify: `src/recotem/datasource/csv.py` (replace the `raise DataSourceError("Network-scheme... not yet wired...")` placeholders with a real urllib fetch)
- Modify: `tests/unit/test_csv_source.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_csv_source.py`:

```python
import http.server
import socketserver
import threading
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def _local_http_server(payload: bytes, status: int = 200) -> Iterator[str]:
    """Yield a base URL serving *payload* once."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs) -> None:  # noqa: D401
            return

        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_csv_fetch_with_matching_sha256_loads() -> None:
    body = b"user_id,item_id\n1,a\n2,b\n"
    digest = hashlib.sha256(body).hexdigest()
    with _local_http_server(body) as base:
        cfg = CSVConfig(type="csv", path=f"{base}/data.csv", sha256=digest)
        df = CSVSource(cfg).fetch(_ctx())
    assert len(df) == 2


def test_http_csv_fetch_sha256_mismatch_raises() -> None:
    body = b"user_id,item_id\n1,a\n"
    bogus = "0" * 64
    with _local_http_server(body) as base:
        cfg = CSVConfig(type="csv", path=f"{base}/data.csv", sha256=bogus)
        with pytest.raises(DataSourceError, match="sha256"):
            CSVSource(cfg).fetch(_ctx())


def test_http_csv_fetch_byte_cap_exceeded_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"user_id,item_id\n" + (b"0,a\n" * 1000)  # > 1 KiB
    digest = hashlib.sha256(body).hexdigest()
    monkeypatch.setenv("RECOTEM_MAX_DOWNLOAD_BYTES", str(1024 * 1024))  # clamp floor
    # Patch the cap below 1 MiB by direct call to make the test deterministic:
    from recotem.datasource import csv as csvmod

    monkeypatch.setattr(csvmod, "_get_max_download_bytes", lambda: 100)
    with _local_http_server(body) as base:
        cfg = CSVConfig(type="csv", path=f"{base}/data.csv", sha256=digest)
        with pytest.raises(DataSourceError, match="exceeded"):
            CSVSource(cfg).fetch(_ctx())


def test_http_csv_fetch_404_raises() -> None:
    with _local_http_server(b"", status=404) as base:
        cfg = CSVConfig(
            type="csv",
            path=f"{base}/missing.csv",
            sha256="0" * 64,
        )
        with pytest.raises(DataSourceError, match="HTTP|fetch"):
            CSVSource(cfg).fetch(_ctx())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_csv_source.py -v -k http`
Expected: tests FAIL — `_get_max_download_bytes` doesn't exist; the `is_network`
branch still raises "not yet wired".

- [ ] **Step 3: Implement the urllib fetch path**

In `src/recotem/datasource/csv.py`, replace the `raise DataSourceError("Network-scheme...")`
in both fetch methods with a call to a new shared helper. Add helpers near
the top of the file:

```python
import urllib.error
import urllib.request

from recotem.config import (
    get_http_timeout_seconds,
    get_max_download_bytes,
)

_USER_AGENT = "recotem/2"
_CHUNK_SIZE = 1 * 1024 * 1024  # 1 MiB
_MAX_REDIRECTS = 5


def _get_max_download_bytes() -> int:
    """Indirection so tests can monkeypatch a smaller cap."""
    return get_max_download_bytes()


def _fetch_http_bytes(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    recipe_name: str,
    run_id: str,
) -> bytes:
    """GET *url* via stdlib urllib. Streams into memory with a byte cap.

    Follows up to ``_MAX_REDIRECTS`` redirects (urllib default is 30; we cap
    lower to keep the path predictable). Raises :class:`DataSourceError` on
    any HTTP, network, or cap-exceeded failure.
    """
    safe_url = _redact_url_userinfo(url)
    redirects = 0
    current_url = url
    visited: set[str] = set()
    while True:
        if redirects > _MAX_REDIRECTS:
            raise DataSourceError(
                f"Too many redirects (>{_MAX_REDIRECTS}) fetching {safe_url}"
            )
        if current_url in visited:
            raise DataSourceError(
                f"Redirect loop detected fetching {safe_url}"
            )
        visited.add(current_url)

        req = urllib.request.Request(
            current_url,
            headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                status = getattr(resp, "status", 200)
                # Manual redirect handling (we capped lower than urllib's default)
                if status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        raise DataSourceError(
                            f"HTTP {status} from {safe_url} without Location header"
                        )
                    redirects += 1
                    current_url = urllib.request.urljoin(current_url, location)
                    logger.info(
                        "csv_source_redirect",
                        recipe=recipe_name,
                        run_id=run_id,
                        from_=safe_url,
                        to=_redact_url_userinfo(current_url),
                        status=status,
                    )
                    continue
                if status >= 400:
                    raise DataSourceError(
                        f"HTTP {status} fetching {safe_url}"
                    )
                buf = bytearray()
                while True:
                    chunk = resp.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    if len(buf) + len(chunk) > max_bytes:
                        logger.warning(
                            "csv_source_size_exceeded",
                            recipe=recipe_name,
                            run_id=run_id,
                            path=safe_url,
                            bytes_read=len(buf) + len(chunk),
                            cap=max_bytes,
                        )
                        raise DataSourceError(
                            f"Download size cap exceeded fetching {safe_url}: "
                            f"> {max_bytes} bytes (RECOTEM_MAX_DOWNLOAD_BYTES)."
                        )
                    buf.extend(chunk)
                return bytes(buf)
        except urllib.error.HTTPError as exc:
            # urllib.request follows redirects up to its own limit; HTTPError
            # is raised for terminal 4xx/5xx.
            raise DataSourceError(
                f"HTTP {exc.code} fetching {safe_url}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise DataSourceError(
                f"URL error fetching {safe_url}: {exc.reason}"
            ) from exc
        except DataSourceError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise DataSourceError(
                f"Unexpected error fetching {safe_url}: {exc}"
            ) from exc
```

In `CSVSource.fetch`, replace the `if is_network: raise ...` block with:

```python
        if is_network:
            raw_bytes = _fetch_http_bytes(
                cfg.path,
                timeout=get_http_timeout_seconds(),
                max_bytes=_get_max_download_bytes(),
                recipe_name=ctx.recipe_name,
                run_id=ctx.run_id,
            )
            # sha256 is guaranteed present by the recipe loader's
            # _enforce_sha256_for_network_paths post-validator. Verify here.
            assert cfg.sha256 is not None  # noqa: S101 — loader invariant
            _verify_sha256(raw_bytes, cfg.sha256)
            sha256_verified = True

            compression = _infer_compression(cfg.path)
            read_kwargs: dict = {
                "sep": cfg.delimiter,
                "encoding": cfg.encoding,
                "header": cfg.header,
                "compression": compression,
            }
            if cfg.dtype:
                read_kwargs["dtype"] = cfg.dtype
            try:
                df = pd.read_csv(BytesIO(raw_bytes), **read_kwargs)
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to parse CSV from '{safe_path}': {exc}"
                ) from exc
            if df.empty:
                raise DataSourceError(
                    f"CSV file '{safe_path}' is empty (no data rows after header)."
                )
            logger.info(
                "csv_source_fetch_done",
                recipe=ctx.recipe_name,
                run_id=ctx.run_id,
                path=safe_path,
                rows=len(df),
                bytes=len(raw_bytes),
                sha256_verified=sha256_verified,
                columns=list(df.columns),
            )
            return df
```

Apply the analogous block to `ParquetSource.fetch` (without `compression` /
`dtype` kwargs).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_csv_source.py -v`
Expected: all pass.

Run also: `uv run ruff check src tests` — fix any new lint findings.

- [ ] **Step 5: Commit**

```bash
git add src/recotem/datasource/csv.py tests/unit/test_csv_source.py
git commit -m "feat(datasource): fetch HTTP/HTTPS CSV via stdlib urllib with byte cap and sha256"
```

---

## Task 7: Integration test — pytest-httpserver end-to-end

**Files:**
- Modify: `pyproject.toml` (add `pytest-httpserver` to `[dependency-groups].dev`)
- Create: `tests/integration/test_https_csv_source.py`

- [ ] **Step 1: Add pytest-httpserver to dev deps**

Edit `pyproject.toml`, in the `[dependency-groups]` block:

```toml
[dependency-groups]
dev = [
    "pytest>=8,<9",
    "pytest-asyncio>=0.24,<2",
    "pytest-cov>=6,<7",
    "pytest-httpserver>=1.0,<2",
    "hypothesis>=6.100,<8",
    "httpx>=0.27,<1",
    "freezegun>=1.5,<2",
    "ruff>=0.6,<1",
]
```

Run: `uv sync --all-extras`
Expected: pytest-httpserver downloaded and installed.

- [ ] **Step 2: Write the integration test**

Create `tests/integration/test_https_csv_source.py`:

```python
"""Integration tests: HTTP-fetched CSV ⇒ recotem train end-to-end.

Uses pytest-httpserver to stand up an HTTP server in-process. The same
urllib code path serves HTTP and HTTPS, so HTTP coverage is sufficient at
the unit/integration layer. TLS handshake is covered (optionally) by the
e2e tutorial mode.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest_plugins = ("pytest_httpserver",)


def _csv_body() -> bytes:
    rows = ["user_id,item_id"]
    # 200 users × 5 items each = 1000 interactions (above min_rows default).
    for u in range(1, 201):
        for i in range(1, 6):
            rows.append(f"{u},item_{i}")
    return ("\n".join(rows) + "\n").encode("utf-8")


@pytest.fixture
def signing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate a signing key and export it via env."""
    kid = "it"
    plaintext = "ab" * 32
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"{kid}:{plaintext}")
    return kid


def test_http_csv_train_end_to_end(
    httpserver, tmp_path: Path, signing_env: str  # noqa: ARG001
) -> None:
    body = _csv_body()
    digest = hashlib.sha256(body).hexdigest()
    httpserver.expect_request("/data.csv").respond_with_data(
        body, content_type="text/csv"
    )
    url = httpserver.url_for("/data.csv")

    out_path = tmp_path / "artifacts" / "it.recotem"
    out_path.parent.mkdir()
    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(
        f"""\
name: it_recipe

source:
  type: csv
  path: {url}
  sha256: {digest}
  dtype:
    user_id: str
    item_id: str

schema:
  user_column: user_id
  item_column: item_id

cleansing:
  drop_null_ids: true
  min_rows: 100

training:
  algorithms: [TopPop]
  metric: ndcg
  cutoff: 5
  n_trials: 1
  parallelism: 1
  split:
    scheme: random
    heldout_ratio: 0.2
    seed: 42

output:
  path: {out_path}
  versioning: always_overwrite
"""
    )

    proc = subprocess.run(
        [sys.executable, "-m", "recotem", "train", str(recipe)],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    assert proc.returncode == 0, f"train failed: {proc.stderr}"
    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_http_csv_sha256_mismatch_train_exits_3(
    httpserver, tmp_path: Path, signing_env: str  # noqa: ARG001
) -> None:
    body = _csv_body()
    httpserver.expect_request("/bad.csv").respond_with_data(
        body, content_type="text/csv"
    )
    url = httpserver.url_for("/bad.csv")

    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(
        f"""\
name: it_bad_sha
source:
  type: csv
  path: {url}
  sha256: {'0' * 64}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {tmp_path / 'out.recotem'}
  versioning: always_overwrite
"""
    )

    proc = subprocess.run(
        [sys.executable, "-m", "recotem", "train", str(recipe)],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    assert proc.returncode == 3, f"expected exit 3 (DataSourceError), got {proc.returncode}: {proc.stderr}"
```

If `recotem` is not exposed as `python -m recotem`, replace the subprocess
command with `["recotem", "train", str(recipe)]` (the entry point installed
by `uv sync`). Verify by running: `uv run recotem --help`.

- [ ] **Step 3: Run the integration test**

Run: `uv run pytest tests/integration/test_https_csv_source.py -v`
Expected: 2 passed.

- [ ] **Step 4: Run the full test suite to check for regressions**

Run: `uv run pytest tests -v`
Expected: all green (excluding `-m slow`).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/integration/test_https_csv_source.py
git commit -m "test(integration): cover HTTP CSV source end-to-end via pytest-httpserver"
```

---

## Task 8: Tutorial recipe + example README

**Files:**
- Create: `examples/tutorial-purchase-log/recipe.yaml`
- Create: `examples/tutorial-purchase-log/README.md`

- [ ] **Step 1: Create the tutorial recipe**

Create `examples/tutorial-purchase-log/recipe.yaml`:

```yaml
# Tutorial recipe — fetches a small public CSV over HTTPS and trains.
# Walkthrough: docs/getting-started.md
#
# The CSV is the v1.0.0-tagged purchase log used by the original Recotem
# project's e2e tests. Two columns: user_id, item_id.

name: purchase_log

source:
  type: csv
  path: https://raw.githubusercontent.com/codelibs/recotem/refs/tags/v1.0.0/frontend/e2e/test_data/purchase_log.csv
  # sha256 is mandatory for network-scheme paths. Computed once at
  # tutorial-authoring time. If upstream rotates the file, regenerate with:
  #   curl -sL <url> | shasum -a 256
  # and update both this value and docs/getting-started.md.
  sha256: 945fc769205a5976d38c5783500ae473afbb04608043b703951a699993c8f8be
  dtype:
    user_id: str
    item_id: str

schema:
  user_column: user_id
  item_column: item_id
  # No timestamp column on this dataset → split.scheme must be `random`.

cleansing:
  drop_null_ids: true
  dedup: keep_last
  min_rows: 100
  min_users: 10
  min_items: 10

training:
  algorithms: [IALS, TopPop]
  metric: ndcg
  cutoff: 10
  n_trials: 10
  parallelism: 1
  split:
    scheme: random
    heldout_ratio: 0.2
    seed: 42

output:
  path: ./artifacts/purchase_log.recotem
  versioning: append_sha
```

- [ ] **Step 2: Create the orientation README**

Create `examples/tutorial-purchase-log/README.md`:

```markdown
# Tutorial example: purchase_log

Self-contained Recotem 2.0 tutorial recipe. Fetches a small public CSV
(≈37 KiB, ≈4 988 interactions) over HTTPS and trains an IALS + TopPop
recommender against it.

- Walkthrough: [docs/getting-started.md](../../docs/getting-started.md)
- Source data: `https://raw.githubusercontent.com/codelibs/recotem/refs/tags/v1.0.0/frontend/e2e/test_data/purchase_log.csv`
- sha256: `945fc769205a5976d38c5783500ae473afbb04608043b703951a699993c8f8be`

Run from the repository root:

```bash
mkdir -p artifacts
uv run recotem train examples/tutorial-purchase-log/recipe.yaml
```

The artifact is written to `./artifacts/purchase_log-<sha>.recotem` (the
`-<sha>` suffix is added by `versioning: append_sha`).
```

- [ ] **Step 3: Sanity-check the recipe with `recotem validate`**

Run: `uv run recotem validate examples/tutorial-purchase-log/recipe.yaml`
Expected: exit 0; "recipe valid" output. (`validate` issues an HTTP HEAD
to GitHub raw — requires network. If offline, skip this step.)

- [ ] **Step 4: Sanity-check the recipe with `recotem train`**

Run: `mkdir -p artifacts && uv run recotem train examples/tutorial-purchase-log/recipe.yaml`
Expected: train completes in under a minute; artifact appears under `artifacts/`.
This is a manual smoke test — the integration test (Task 7) covers the
deterministic in-process variant.

- [ ] **Step 5: Commit**

```bash
git add examples/tutorial-purchase-log/recipe.yaml examples/tutorial-purchase-log/README.md
git commit -m "feat(examples): add tutorial-purchase-log recipe with HTTPS source + sha256 pin"
```

---

## Task 9: Update `docker-compose.example.yaml` for the tutorial

**Files:**
- Modify: `docker-compose.example.yaml`

- [ ] **Step 1: Rewrite the compose file**

Replace `docker-compose.example.yaml` content with:

```yaml
# docker-compose.example.yaml — Recotem 2.0 tutorial compose file
#
# This file is a runnable example for the getting-started tutorial. It is
# NOT for production use.
#
# Usage (3 commands from the repo root):
#
#   1. Generate a signing key + an API key
#      $ docker run --rm ghcr.io/codelibs/recotem:latest keygen --type signing --kid dev
#      # → copy the env_entry plaintext into RECOTEM_SIGNING_KEYS_SECRET below
#      $ docker run --rm ghcr.io/codelibs/recotem:latest keygen --type api --kid dev
#      # → copy the env_entry hash into RECOTEM_API_KEYS_SECRET below
#      # → keep the api plaintext for later (used as X-API-Key header)
#
#   2. Train (one-shot)
#      $ RECOTEM_SIGNING_KEYS_SECRET="dev:..." \
#        RECOTEM_API_KEYS_SECRET="dev:sha256:..." \
#        docker compose -f docker-compose.example.yaml run --rm train
#
#   3. Serve + curl
#      $ RECOTEM_SIGNING_KEYS_SECRET="..." RECOTEM_API_KEYS_SECRET="..." \
#        docker compose -f docker-compose.example.yaml up -d serve
#      $ curl -sX POST http://localhost:8080/predict/purchase_log \
#          -H "X-API-Key: <api plaintext>" \
#          -H "Content-Type: application/json" \
#          -d '{"user_id": "1", "cutoff": 5}'

x-recotem-image: &recotem-image
  image: ghcr.io/codelibs/recotem:latest
  # To build locally:
  # build:
  #   context: .
  #   dockerfile: Dockerfile

services:

  # ── train ──────────────────────────────────────────────────────────────────
  # One-shot training job. Run via `docker compose run --rm train`.
  # Exit codes: 0=success, 2=recipe error, 3=datasource error,
  #             4=training error, 5=artifact error.
  train:
    <<: *recotem-image
    command: ["train", "/recipes/recipe.yaml"]
    working_dir: /workspace
    volumes:
      - ./examples/tutorial-purchase-log:/recipes:ro
      - artifacts:/workspace/artifacts
    environment:
      RECOTEM_SIGNING_KEYS: "${RECOTEM_SIGNING_KEYS_SECRET}"
      RECOTEM_LOG_FORMAT: "json"
    restart: "no"

  # ── serve ──────────────────────────────────────────────────────────────────
  # Long-running prediction server. Hot-swaps when train rewrites the artifact.
  serve:
    <<: *recotem-image
    command: ["serve", "--recipes", "/recipes"]
    working_dir: /workspace
    ports:
      - "8080:8080"
    volumes:
      - ./examples/tutorial-purchase-log:/recipes:ro
      - artifacts:/workspace/artifacts:ro
    environment:
      RECOTEM_SIGNING_KEYS: "${RECOTEM_SIGNING_KEYS_SECRET}"
      RECOTEM_API_KEYS:     "${RECOTEM_API_KEYS_SECRET}"
      RECOTEM_HOST: "0.0.0.0"
      RECOTEM_PORT: "8080"
      RECOTEM_WATCH_INTERVAL: "10"
      RECOTEM_LOG_FORMAT: "json"
      RECOTEM_ENV: "production"
    restart: unless-stopped
    healthcheck:
      test:
        - "CMD-SHELL"
        - "python -c \"import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5).status == 200 else 1)\""
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

# ── volumes ────────────────────────────────────────────────────────────────────
# Shared between train (writes) and serve (reads). The recipe writes to
# ./artifacts/purchase_log.recotem (CWD-relative); the working_dir above plus
# this mount resolve that to /workspace/artifacts.
volumes:
  artifacts:
```

- [ ] **Step 2: Verify YAML parses and compose renders**

Run: `docker compose -f docker-compose.example.yaml config > /dev/null`
Expected: no errors. (Skip if Docker daemon is not running; the YAML lint
in step 4 is a sufficient gate.)

- [ ] **Step 3: Lint the YAML**

Run: `python -c "import yaml; yaml.safe_load(open('docker-compose.example.yaml'))"`
Expected: no exception.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.example.yaml
git commit -m "feat(compose): bind-mount tutorial recipe and align artifact volume path"
```

---

## Task 10: Replace `docs/quickstart.md` with `docs/getting-started.md`

**Files:**
- Create: `docs/getting-started.md`
- Delete: `docs/quickstart.md`

- [ ] **Step 1: Write the new doc**

Create `docs/getting-started.md`:

```markdown
# Getting Started

Train a recommender from a small public CSV and serve it as a REST API in
under 10 minutes. Two paths: Docker Compose (no Python install needed) and
pip (everything in your venv).

## Prerequisites

- Either Docker (with the Compose plugin) **or** Python 3.12+
- ~50 MB of disk
- Network access to fetch a small CSV from `raw.githubusercontent.com`

## Path A — Docker Compose (recommended)

The repo ships a `docker-compose.example.yaml` and an `examples/tutorial-purchase-log/`
recipe. From the repo root:

### 1. Generate keys

```bash
docker run --rm ghcr.io/codelibs/recotem:latest keygen --type signing --kid dev
# kid=dev
# plaintext=<64-char hex>
# hash=sha256:<64-char hex>
# env_entry=RECOTEM_SIGNING_KEYS=dev:<plaintext>

docker run --rm ghcr.io/codelibs/recotem:latest keygen --type api --kid dev
# kid=dev
# plaintext=<43-char base64url>          ← keep this; clients pass it as X-API-Key
# hash=sha256:<64-char hex>
# env_entry=RECOTEM_API_KEYS=dev:sha256:<hex>
```

Export both into your shell:

```bash
export RECOTEM_SIGNING_KEYS_SECRET="dev:<plaintext-hex-from-signing>"
export RECOTEM_API_KEYS_SECRET="dev:sha256:<hash-hex-from-api>"
export RECOTEM_API_PLAINTEXT="<plaintext-from-api>"      # used in step 4
```

### 2. Train

```bash
docker compose -f docker-compose.example.yaml run --rm train
```

What happens: the train container fetches `purchase_log.csv` over HTTPS,
verifies its sha256, runs Optuna with IALS + TopPop, and writes a signed
artifact to the `artifacts` volume.

Expected last log line (JSON):

```json
{"event":"train_done","name":"purchase_log","exit_code":0,
 "artifact":"./artifacts/purchase_log-...recotem","best_class":"IALSRecommender",...}
```

### 3. Serve

```bash
docker compose -f docker-compose.example.yaml up -d serve
docker compose -f docker-compose.example.yaml logs --no-color -n 20 serve
```

Health check:

```bash
curl http://localhost:8080/health
# {"status":"ok","recipes":{"purchase_log":{"loaded":true,...}}}
```

### 4. Predict

```bash
curl -sX POST http://localhost:8080/predict/purchase_log \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "1", "cutoff": 5}' | jq .
```

Expected (the exact items / scores depend on training):

```json
{
  "items": [
    {"item_id": "...", "score": 0.91},
    ...
  ],
  "model": {"recipe": "purchase_log", "best_class": "IALSRecommender", "kid": "dev"},
  "request_id": "..."
}
```

### 5. Tear down

```bash
docker compose -f docker-compose.example.yaml down -v
```

## Path B — pip install

```bash
pip install recotem
```

### 1. Generate keys

```bash
recotem keygen --type signing --kid dev
recotem keygen --type api     --kid dev
```

Export into your shell (mirrors Path A):

```bash
export RECOTEM_SIGNING_KEYS="dev:<plaintext-hex-from-signing>"
export RECOTEM_API_KEYS="dev:sha256:<hash-hex-from-api>"
export RECOTEM_API_PLAINTEXT="<plaintext-from-api>"
```

### 2. Train

The tutorial recipe writes to `./artifacts/...` (CWD-relative). Run from
the repo root:

```bash
mkdir -p artifacts
recotem train examples/tutorial-purchase-log/recipe.yaml
```

### 3. Serve

```bash
recotem serve --recipes examples/tutorial-purchase-log/
```

### 4. Predict

```bash
curl -sX POST http://127.0.0.1:8080/predict/purchase_log \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "1", "cutoff": 5}' | jq .
```

## What just happened

- `recotem train` parsed the recipe, fetched the CSV over HTTPS, compared its
  sha256 against the recipe pin, ran an Optuna hyperparameter search with
  IALS and TopPop, and wrote a binary artifact signed with your signing key.
- `recotem serve` watched the artifact directory, picked up the new file,
  HMAC-verified it against the same signing key, and registered the
  `/predict/purchase_log` endpoint.
- The `/predict` request was authenticated by the API key allow-list and
  scored using the trained model.

## Common issues

| Symptom | Cause | Fix |
|---|---|---|
| `RecipeError: 'source.path' uses a network scheme … requires a 'sha256' integrity pin` | Recipe edited; sha256 removed | Re-add the `sha256:` line in the recipe |
| `DataSourceError: sha256 mismatch` | Upstream rotated the file | Re-compute with `curl -sL <url> \| shasum -a 256` and update the recipe |
| `DataSourceError: HTTP 404 fetching …` | URL changed | Verify the URL in a browser; restore the v1.0.0 tag |
| `ArtifactError: RECOTEM_SIGNING_KEYS not set` | Step 1 not exported | Re-run the export and try again |
| `401 Unauthorized` on /predict | Wrong API key plaintext | Use the `plaintext` line from `keygen --type api`, not the `hash` |

## Next steps

- [docs/recipe-reference.md](recipe-reference.md) — every recipe field
- [docs/data-sources/csv.md](data-sources/csv.md) — full CSV/Parquet documentation including schemes
- [docs/deployment/docker.md](deployment/docker.md) — production Docker patterns
- [docs/deployment/k8s.md](deployment/k8s.md) — Helm chart and CronJob
- [docs/security.md](security.md) — threat model and operator responsibilities
- [docs/operations.md](operations.md) — key rotation, recovery, sizing, troubleshooting
```

- [ ] **Step 2: Delete `docs/quickstart.md`**

Run: `git rm docs/quickstart.md`
Expected: file removed.

- [ ] **Step 3: Verify intra-doc links resolve**

Run: `grep -E "\]\([^)]*quickstart" docs/ -r`
Expected: no matches (or only the lines we'll fix in subsequent tasks).

- [ ] **Step 4: Commit**

```bash
git add docs/getting-started.md docs/quickstart.md
git commit -m "docs: replace quickstart.md with getting-started.md"
```

---

## Task 11: Update `docs/data-sources/csv.md` for new policy

**Files:**
- Modify: `docs/data-sources/csv.md`

- [ ] **Step 1: Replace the "Rejected schemes" / "fsspec paths" section**

Find the block in `docs/data-sources/csv.md` that begins "Rejected schemes:"
(around line 71) and replace from "## fsspec paths" through the end of that
section with:

```markdown
## Path schemes

Any fsspec-supported scheme is accepted on `source.path` and
`item_metadata.path`:

```yaml
# Local (relative or absolute)
path: ./data/interactions.csv
path: /mnt/data/interactions.csv

# Object storage (uses cloud SDK auth — instance profile / ADC / env vars)
path: s3://my-bucket/data/interactions.csv.gz
path: gs://my-bucket/data/interactions.parquet
path: az://my-container/interactions.parquet

# HTTP / HTTPS — `sha256` integrity pin is REQUIRED
path: https://files.example.com/2025-01/interactions.csv
sha256: 945fc769205a5976d38c5783500ae473afbb04608043b703951a699993c8f8be

# file:// is treated as a bare local path
path: file:///mnt/data/interactions.csv
```

Embedded credentials in URIs (e.g. `s3://AKIA...:secret@bucket/`) are
rejected at recipe load. Credentials must come from the environment
(instance profile, ADC, `AWS_*` env vars, etc.).

`output.path` is more restrictive — `http://`, `https://`, `ftp://`,
`ftps://`, and `memory://` are rejected because writes are not supported
on those schemes. Use a bare local path, `file://`, or a writeable
object-store scheme.

## Network-scheme integrity (HTTP / HTTPS)

When `source.path` (or `item_metadata.path`) uses `http://` or `https://`:

- `sha256` is **mandatory** on the same config block. Recipe load fails
  with `RecipeError` if it is missing.
- The fetch is performed via stdlib `urllib.request` — no extra runtime
  deps required. Up to 5 redirects are followed, with TLS verification
  always on for `https://`.
- The downloaded payload is capped at `RECOTEM_MAX_DOWNLOAD_BYTES` (default
  256 MiB; clamped to [1 MiB, 16 GiB]).
- The connect/read timeout is `RECOTEM_HTTP_TIMEOUT_SECONDS` (default 30,
  clamped to [1, 600]).
- `recotem validate` issues a HEAD-like check (`fs.exists()` for non-network
  schemes; we currently skip the HEAD on HTTP/HTTPS during validate, since
  the integrity check is only meaningful at fetch time).

Compute the sha256 once when authoring the recipe:

```bash
curl -sL <url> | shasum -a 256
```

If the upstream file rotates, regenerate the value and update the recipe.
The mismatch is the alert.

## sha256 on non-network paths

`sha256` is also valid (but optional) on local, `file://`, and object-store
paths. When set, the bytes are hashed and compared post-read. Useful for
internal reproducibility audits even when the network is not involved.
```

(Adjust line numbers as needed — preserve any sections we did not touch.)

- [ ] **Step 2: Verify the docs render**

Run: `grep -n "Rejected schemes" docs/data-sources/csv.md`
Expected: no matches.

Run: `grep -n "sha256" docs/data-sources/csv.md`
Expected: matches in the new sections.

- [ ] **Step 3: Commit**

```bash
git add docs/data-sources/csv.md
git commit -m "docs(csv): document network-scheme support, sha256 pin, byte cap"
```

---

## Task 12: Update `docs/security.md` for new policy

**Files:**
- Modify: `docs/security.md`

- [ ] **Step 1: Update the threat-model table and add a new section**

In `docs/security.md`, change the "Path traversal via recipe" row to drop
the implicit allow-list claim (keep the `name` regex / artifact-root part)
and add new rows after it:

```markdown
| Path traversal via recipe | `name` validated with `^[A-Za-z0-9_-]{1,64}$` at load and before every filesystem use; artifact root confinement via `RECOTEM_ARTIFACT_ROOT` |
| Tampered or rotated network-fetched data | `sha256` integrity pin is **mandatory** on `source.path` / `item_metadata.path` when the scheme is `http://` or `https://`; mismatch raises `DataSourceError` (exit 3) before the bytes reach the parser |
| Resource exhaustion via giant network fetch | `RECOTEM_MAX_DOWNLOAD_BYTES` (default 256 MiB) caps the in-memory body during HTTP/HTTPS fetch; cap exceeded → `DataSourceError` mid-stream |
| Plaintext HTTP source on the public internet | Operator policy. `http://` is allowed (legitimate inside trusted networks) but operators MUST avoid plaintext on the public internet; sha256 mitigates content tampering for any reachable response |
```

After the threat table, append a new subsection (before "Artifact payload and the FQCN allow-list"):

```markdown
## Operator responsibilities for network sources

Recipes are operator-authored and live inside the Recotem trust boundary.
That means choices about which URLs to point at — and whether `http://`
URLs are safe to use — are operator decisions, not Recotem decisions.

Specific operator responsibilities:

- **Choose `https://` over `http://` on the public internet.** TLS prevents
  a network attacker from swapping bytes; `sha256` detects the swap, but
  TLS prevents it from happening in the first place.
- **Avoid pointing recipes at metadata services.** URLs like
  `http://169.254.169.254/...` (AWS IMDSv1) or
  `http://metadata.google.internal/...` will be fetched by `recotem train`
  and could leak instance-role credentials into your dataset. This is
  operator misuse; Recotem does not block such URLs (since it cannot
  distinguish "metadata service" from "internal artifact server" in
  general).
- **Compute and pin sha256 once, then alert on changes.** A mismatch is
  the signal. Don't bypass it by silently regenerating during CI.
```

- [ ] **Step 2: Verify the changes**

Run: `grep -nE "sha256|metadata service|RECOTEM_MAX_DOWNLOAD_BYTES" docs/security.md`
Expected: matches in the new content.

- [ ] **Step 3: Commit**

```bash
git add docs/security.md
git commit -m "docs(security): document network-fetch threats and operator responsibilities"
```

---

## Task 13: Update `docs/recipe-reference.md`

**Files:**
- Modify: `docs/recipe-reference.md` (rewrite "Rejected schemes" paragraph at lines 213–216 and document `sha256`)

- [ ] **Step 1: Locate and rewrite the path section**

Find lines 213–218 of `docs/recipe-reference.md` (the "Rejected schemes" /
"Embedded credentials" / "Local paths are resolved" block) and replace with:

```markdown
Path schemes for `source.path` and `item_metadata.path`: any fsspec-supported
scheme is accepted. Schemes `http://` and `https://` additionally require an
`sha256` integrity pin on the same config block.

`output.path` rejects schemes that fsspec does not implement for writes:
`http://`, `https://`, `ftp://`, `ftps://`, `memory://`. Acceptable output
schemes: bare local, `file://`, `s3://`, `gs://`, `az://`.

Embedded credentials (`s3://AKIA...:secret@bucket/`) are rejected at recipe
load on every path field.

Local paths are resolved to absolute. If `RECOTEM_ARTIFACT_ROOT` is set,
`output.path` must resolve to a path under it after `realpath` resolution
(symlink escapes are rejected).
```

- [ ] **Step 2: Add `sha256` to the source / item_metadata field tables**

Locate the CSV / Parquet field documentation block in this file and add a
row to its table (or its prose, depending on existing style):

```markdown
| `sha256` | string | optional (required when `path` is `http://` or `https://`) | 64-char lowercase hex; verified against the fetched bytes; mismatch raises `DataSourceError` |
```

Locate the `item_metadata` field docs and add the same row.

- [ ] **Step 3: Verify**

Run: `grep -n "Rejected schemes" docs/recipe-reference.md`
Expected: no matches.

Run: `grep -n "sha256" docs/recipe-reference.md`
Expected: matches in the new sections.

- [ ] **Step 4: Commit**

```bash
git add docs/recipe-reference.md
git commit -m "docs(recipe): document sha256 field and network-scheme requirement"
```

---

## Task 14: Update `docs/deployment/docker.md` for the volume-mount path change

**Files:**
- Modify: `docs/deployment/docker.md`

- [ ] **Step 1: Find references to the example compose file**

Run: `grep -nE "docker-compose\.example|/artifacts|recipes:/recipes" docs/deployment/docker.md`
Note any references to the old `/artifacts` mount path.

- [ ] **Step 2: Update the doc**

For each match:

- If the doc references `/artifacts:` (the old mount path) as part of a
  walkthrough of the example compose, update to `/workspace/artifacts` to
  match Task 9.
- If the doc references the named volume `recipes` (the old approach),
  update the description to "bind mount of `examples/tutorial-purchase-log/`".
- Keep production-deployment recommendations (e.g., "use a PVC, S3-mounted
  volume, or object-store paths") unchanged — they're not tied to the
  example.

If `docs/deployment/docker.md` does not reference these specifics directly
(it may just embed a snippet), no change is needed beyond verifying.

- [ ] **Step 3: Commit**

```bash
git add docs/deployment/docker.md
git commit -m "docs(deployment): align docker.md with updated compose example mounts"
```

(If no changes were needed, skip the commit.)

---

## Task 15: Update `README.md` and `docs/README.md`

**Files:**
- Modify: `README.md`
- Modify: `docs/README.md`

- [ ] **Step 1: Update top-level `README.md`**

In `README.md`, find:

```markdown
- [docs/quickstart.md](docs/quickstart.md) — 5-minute walkthrough
```

Replace with:

```markdown
- [docs/getting-started.md](docs/getting-started.md) — Docker Compose / pip walkthrough end-to-end
```

In the "Hello world (CSV)" section, after the current intro line, add one
sentence:

```markdown
> For a runnable end-to-end tutorial (Docker Compose, no manual data prep),
> see [docs/getting-started.md](docs/getting-started.md).
```

- [ ] **Step 2: Update `docs/README.md`**

In `docs/README.md`, replace:

```markdown
- [Quickstart](quickstart.md) — install, write a recipe, train, curl `/predict` in 5 minutes
```

with:

```markdown
- [Getting started](getting-started.md) — install (Docker or pip), train from a public CSV, curl `/predict`
```

- [ ] **Step 3: Verify links**

Run: `grep -rn "quickstart\.md" README.md docs/`
Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/README.md
git commit -m "docs: redirect README links from quickstart.md to getting-started.md"
```

---

## Task 16: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the directory layout listing (line 54)**

Find the `docs/` listing in `CLAUDE.md` and change:

```markdown
├── quickstart.md       5-minute install → recipe → train → /predict
```

to:

```markdown
├── getting-started.md  Docker / pip walkthrough → train → /predict
```

In the same listing, update the `examples/` line (around line 64) from:

```markdown
examples/               csv-local, ga4-bigquery, k8s/, plugins/echo-source/
```

to:

```markdown
examples/               tutorial-purchase-log, csv-local, ga4-bigquery, k8s/, plugins/echo-source/
```

- [ ] **Step 2: Update the example train command (line 83)**

Find:

```markdown
uv run recotem train examples/csv-local/recipe.yaml
```

and replace with:

```markdown
uv run recotem train examples/tutorial-purchase-log/recipe.yaml
```

- [ ] **Step 3: Rewrite the path-scheme bullet (lines 102-104)**

Find:

```markdown
- Path scheme allow-list: bare local | `s3://` | `gs://` | `az://`. No
  `file://`, `http(s)://`, `ftp(s)://`, `memory://`. Embedded URI credentials
  are rejected.
```

Replace with:

```markdown
- Path scheme: any fsspec-supported scheme on `source.path` and
  `item_metadata.path`. `output.path` rejects `http(s)://`, `ftp(s)://`,
  `memory://` (write not supported). For network-scheme inputs (`http://`,
  `https://`), `sha256` is mandatory and `RECOTEM_MAX_DOWNLOAD_BYTES`
  (default 256 MiB) caps the body. Embedded URI credentials are rejected.
```

- [ ] **Step 4: Add new env vars to the table (around line 163)**

Find the env-var table and add two new rows (alphabetic order or grouped
with other source-related vars — match the existing style):

```markdown
| `RECOTEM_MAX_DOWNLOAD_BYTES` | 256 MiB | Cap on HTTP/HTTPS source-path body. Clamped [1 MiB, 16 GiB]. |
| `RECOTEM_HTTP_TIMEOUT_SECONDS` | 30 | Connect/read timeout for HTTP/HTTPS source fetch. Clamped [1, 600]. |
```

- [ ] **Step 5: Update the reference docs section (lines 181-182)**

Find:

```markdown
- Spec: `docs/superpowers/specs/2026-05-07-recotem-2-design.md`
- Quickstart: `docs/quickstart.md`
```

Replace with:

```markdown
- Spec: `docs/superpowers/specs/2026-05-07-tutorial-and-https-source-design.md`
- Getting started: `docs/getting-started.md`
```

- [ ] **Step 6: Verify**

Run: `grep -nE "quickstart\.md|recotem-2-design|csv-local/recipe\.yaml" CLAUDE.md`
Expected: no matches.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): refresh path-scheme policy, env vars, and tutorial links"
```

---

## Task 17: Add `--tutorial` mode to e2e

**Files:**
- Modify: `tests/e2e/<existing-script>.sh` (or create one if absent)

- [ ] **Step 1: Inspect the e2e directory**

Run: `ls tests/e2e/` and `cat tests/e2e/*.sh 2>/dev/null | head -100`
Note the existing script's structure (entry-point, env handling).

- [ ] **Step 2: Add a tutorial mode flag**

If a `run-e2e.sh` (or similar) exists, add a branch:

```bash
if [[ "${1:-}" == "--tutorial" ]]; then
    if [[ -z "${RECOTEM_E2E_NETWORK:-}" ]]; then
        echo "Skipping --tutorial: RECOTEM_E2E_NETWORK not set"
        exit 0
    fi
    RECIPE="examples/tutorial-purchase-log/recipe.yaml"
else
    RECIPE="${RECIPE:-tests/e2e/recipe.yaml}"
fi

# ... rest of the script uses $RECIPE
```

If no e2e script exists, this task is reduced to documenting the same in
`tests/e2e/README.md`. Defer the actual implementation if there is no
existing scaffold to extend.

- [ ] **Step 3: Verify the script runs in default (non-tutorial) mode**

Run: `bash tests/e2e/run-e2e.sh` (substitute the actual filename)
Expected: existing behavior preserved.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/
git commit -m "test(e2e): add --tutorial mode gated on RECOTEM_E2E_NETWORK"
```

---

## Final verification

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests -v`
Expected: all green except `-m slow` tests (run those if desired).

- [ ] **Step 2: Run the linters**

Run: `uv run ruff check src tests` and `uv run ruff format --check src tests`
Expected: no findings.

- [ ] **Step 3: Manual smoke test of the tutorial**

Run the Docker Compose path A from `docs/getting-started.md` end-to-end,
or the pip path B. Confirm `/predict/purchase_log` returns items.

- [ ] **Step 4: Update spec status**

Edit `docs/superpowers/specs/2026-05-07-tutorial-and-https-source-design.md`,
change the `Status: draft` line to `Status: implemented`.

- [ ] **Step 5: Final commit**

```bash
git add docs/superpowers/specs/
git commit -m "docs(spec): mark tutorial-and-https-source design as implemented"
```

---

## Subagent allocation suggestion

If using `subagent-driven-development`:

| Tasks | Subagent |
|-------|----------|
| 1, 2, 3, 4 (loader / models / config core) | `marevol:backend-engineer` |
| 5, 6 (fetch refactor) | `marevol:backend-engineer` |
| 7 (integration test) | `marevol:test-engineer` |
| 8, 9 (tutorial + compose) | `marevol:devops-engineer` |
| 10–16 (docs) | `marevol:tech-writer` |
| 17 (e2e) | `marevol:test-engineer` |
| Final verification | `marevol:code-reviewer` |
| Optional second pass | `codex-review` |

---

## Self-review notes

This plan has been self-reviewed against the spec:

- §5 (path-scheme policy) → Tasks 3, 4, 11, 12, 13, 16
- §6.1 (loader) → Tasks 3, 4
- §6.2 (csv.py) → Tasks 5, 6
- §6.3 (models.py) → Task 2
- §6.4 (config.py) → Task 1
- §6.5 (logging) → Tasks 5, 6 (URL userinfo redaction; size-exceeded log)
- §6.6 (probe) → covered by spec note; no code change required
- §6.7 (`recotem schema`) → covered by Task 2 (pydantic schema picks up `sha256` automatically)
- §7.1, §7.2 (tutorial assets) → Task 8
- §7.3 (compose) → Task 9
- §8 (docs) → Tasks 10, 11, 12, 13, 14, 15, 16
- §9 (tests) → Tasks 1, 2, 3, 4, 5, 6, 7, 17
- §10 (CI) → no workflow changes; integration test covered in Task 7
- §11 (file-by-file) → cross-checked against Task file lists above

No placeholders. Type / function names consistent across tasks
(`_validate_input_path`, `_validate_output_path`, `_NETWORK_SCHEMES`,
`_OUTPUT_REJECTED_SCHEMES`, `_enforce_sha256_for_network_paths`,
`_redact_url_userinfo`, `_fetch_http_bytes`, `_verify_sha256`,
`_infer_compression`, `get_max_download_bytes`,
`get_http_timeout_seconds`).
