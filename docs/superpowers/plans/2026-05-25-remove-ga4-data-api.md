# Remove GA4 Data API Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the GA4 Data API data source (`source.type: ga4`) entirely from recotem, because the GA4 Data API cannot return a stable user identifier (`userId`) suitable for collaborative-filtering training.

**Architecture:** recotem discovers data sources via setuptools entry points (`recotem.datasources`) with a hard-coded `_FALLBACK_BUILTINS` map for the not-installed case. The `ga4` source is one builtin among `csv`/`parquet`/`bigquery`/`sql`. Removal means: delete the implementation + its dedicated Prometheus metrics module + its config helper, drop it from the entry-point map / fallback map / optional-dependency extra, regenerate the lockfile, delete all GA4-Data-API-specific tests, and strip GA4-Data-API references from docs/examples/CI. The unrelated `examples/ga4-bigquery/` (which uses `type: bigquery`, NOT the `ga4` source) is **preserved**.

**Tech Stack:** Python 3.12, `uv`, pydantic v2, FastAPI, pytest 8 + hypothesis, ruff, GitHub Actions.

**Decisions locked in (confirmed with user 2026-05-25):**
1. Remove the `ga4` Data API source entirely; **keep** `examples/ga4-bigquery/` (it uses the generic `bigquery` source).
2. GA4 Data API references in README / docs / CLAUDE.md are **simply removed** — no replacement "use BigQuery instead" prose is added.
3. Deliver as a feature branch + Pull Request.

---

## Change Map

### Files to DELETE entirely
| Path | Why |
|------|-----|
| `src/recotem/datasource/ga4.py` | GA4 Data API source implementation (`GA4Config`, `GA4Source`). |
| `src/recotem/_metrics_ga4.py` | Prometheus counters/gauges used only by the GA4 source. |
| `tests/unit/test_datasource_ga4.py` | Unit tests for the GA4 source. |
| `tests/unit/test_metrics_ga4.py` | Unit tests for the GA4 metrics module. |
| `tests/fuzz/test_ga4_recipe_fuzz.py` | Hypothesis fuzz tests for GA4 recipe loading. |
| `docs/data-sources/ga4.md` | GA4 Data API source reference doc. |
| `examples/ga4-data-api/` (dir: `README.md`, `recipe.yaml`) | GA4 Data API example. |

### Files to EDIT
| Path | Edit |
|------|------|
| `pyproject.toml` | Remove `ga4` from the `all` extra (l.42); remove the `ga4 = [...]` extra (l.41); remove the `ga4 = "recotem.datasource.ga4:GA4Source"` entry point (l.52). |
| `uv.lock` | Regenerate via `uv lock` so `google-analytics-data` is dropped. |
| `src/recotem/datasource/registry.py` | Remove `"ga4"` from `_FALLBACK_BUILTINS` (l.34) and `_BUILTIN_INSTALL_HINTS` (l.42); update the `(sql, ga4, bigquery)` comment (l.136). |
| `src/recotem/config.py` | Remove the `RECOTEM_GA4_MAX_PAGES` docstring lines (l.38-39); remove the GA4 page-cap block + `get_ga4_max_pages()` (l.580-596). |
| `tests/unit/test_config.py` | Remove all `get_ga4_max_pages` / `RECOTEM_GA4_MAX_PAGES` tests + the parametrize entry. |
| `tests/unit/test_datasource_registry.py` | Remove the two ga4-specific tests; fix the `(sql, ga4, bigquery)` comments. |
| `.github/workflows/test.yml` | Remove `--extra ga4` (l.63 and l.98). |
| `docs/recipe-reference.md` | Remove the `### source.type = ga4` section (l.101-133) and `ga4` from the discriminator list (l.10). |
| `README.md` | Remove GA4 from the data-sources bullet (l.38). |
| `CLAUDE.md` | Remove `ga4` from datasource list (l.39), `ga4.md` from docs list (l.62), `ga4-data-api/` from examples list (l.69), `ga4` from discriminator (l.105), and the `RECOTEM_GA4_MAX_PAGES` env-var row (l.220). |

### Files to PRESERVE (do NOT touch — verify intact)
- `examples/ga4-bigquery/` — uses `type: bigquery`, not the `ga4` source.
- `docs/data-sources/bigquery.md` — its "GA4 events_* pattern" section is about the BigQuery export, keep it.
- `docs/README.md` l.14 "GA4 query patterns" — refers to BigQuery query patterns, keep it.
- `src/recotem/datasource/bigquery.py`, all other data sources.

---

## Task 1: Remove GA4 source code, config, registry wiring, packaging, and dead tests

This must be a **single commit** because deleting `ga4.py` / `_metrics_ga4.py` / `get_ga4_max_pages` immediately breaks the test files that import them — the suite is only green again once both sides change together.

**Files:**
- Delete: `src/recotem/datasource/ga4.py`
- Delete: `src/recotem/_metrics_ga4.py`
- Delete: `tests/unit/test_datasource_ga4.py`
- Delete: `tests/unit/test_metrics_ga4.py`
- Delete: `tests/fuzz/test_ga4_recipe_fuzz.py`
- Modify: `src/recotem/datasource/registry.py`
- Modify: `src/recotem/config.py`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_datasource_registry.py`
- Modify: `pyproject.toml`
- Regenerate: `uv.lock`

- [ ] **Step 1: Delete the five GA4 source/metrics/test files**

```bash
git rm src/recotem/datasource/ga4.py \
       src/recotem/_metrics_ga4.py \
       tests/unit/test_datasource_ga4.py \
       tests/unit/test_metrics_ga4.py \
       tests/fuzz/test_ga4_recipe_fuzz.py
```

- [ ] **Step 2: Remove `ga4` from `src/recotem/datasource/registry.py`**

Delete this line from `_FALLBACK_BUILTINS` (currently l.34):
```python
    "ga4": "recotem.datasource.ga4:GA4Source",
```
Delete this line from `_BUILTIN_INSTALL_HINTS` (currently l.42):
```python
    "ga4": "install recotem[ga4]",
```
Update the comment at ~l.136 (drop `ga4`):
```python
                # Optional extras (sql, ga4, bigquery) may not be installed.
```
→
```python
                # Optional extras (sql, bigquery) may not be installed.
```

- [ ] **Step 3: Remove the GA4 page-cap helper from `src/recotem/config.py`**

Delete the module-docstring lines (currently l.38-39):
```
  RECOTEM_GA4_MAX_PAGES        Hard ceiling on GA4 Data API pagination loops
                                 (default 500; clamped [1, 10_000])
```
Delete the entire block (currently l.580-596):
```python
# ---------------------------------------------------------------------------
# GA4 page cap (used by datasource/ga4.py)
# ---------------------------------------------------------------------------

_GA4_MAX_PAGES_MIN = 1
_GA4_MAX_PAGES_MAX = 10_000
_GA4_MAX_PAGES_DEFAULT = 500


def get_ga4_max_pages() -> int:
    """Return RECOTEM_GA4_MAX_PAGES, clamped to [1, 10 000]."""
    return _clamped_int_env(
        "RECOTEM_GA4_MAX_PAGES",
        _GA4_MAX_PAGES_DEFAULT,
        _GA4_MAX_PAGES_MIN,
        _GA4_MAX_PAGES_MAX,
    )
```

- [ ] **Step 4: Remove GA4 tests from `tests/unit/test_config.py`**

Remove these test functions in full: `test_ga4_max_pages_default`, `test_ga4_max_pages_clamp_low`, `test_ga4_max_pages_clamp_high`, `test_ga4_max_pages_non_integer_logs_env_var_unparseable` (currently around l.657-679 and l.744-765, plus the `# Task 3.2: RECOTEM_GA4_MAX_PAGES` section header comment at l.657). Also remove the parametrize tuple entry referencing GA4 (currently l.691):
```python
        ("RECOTEM_GA4_MAX_PAGES", "xyz", "get_ga4_max_pages", 500),
```
Read the surrounding parametrize decorator first; remove only that one tuple, leaving the other entries and the decorator intact.

- [ ] **Step 5: Remove GA4 tests from `tests/unit/test_datasource_registry.py`**

Remove these test functions in full: `test_ga4_import_error_hint_names_ga4_extra` (currently l.350-372) and `test_sql_and_ga4_resolve_via_fallback_with_no_entry_points` (currently l.565-591).

For the second one, `sql` must still be proven to resolve via the fallback. After reading the file, replace the combined sql+ga4 fallback test with a sql-only version (drop every `ga4` line — the `ep`/registration setup, the `assert "ga4" in types`, and `assert types["ga4"].__name__ == "GA4Source"`), keeping the `sql` assertions. Rename the function to `test_sql_resolves_via_fallback_with_no_entry_points` and update its docstring to drop `ga4`. Also update the two comments mentioning `(sql, ga4, bigquery)` (l.146 and l.305) to `(sql, bigquery)`.

- [ ] **Step 6: Remove `ga4` from `pyproject.toml`**

Remove `ga4` from the `all` extra (currently l.42):
```toml
all = ["recotem[bigquery,postgres,mysql,sqlite,ga4,s3,gcs,azure,metrics]"]
```
→
```toml
all = ["recotem[bigquery,postgres,mysql,sqlite,s3,gcs,azure,metrics]"]
```
Remove the entire `ga4 = [...]` extra definition (currently l.41):
```toml
ga4 = ["google-analytics-data>=0.18,<1", "google-auth>=2,<3"]
```
Remove the entry point (currently l.52):
```toml
ga4 = "recotem.datasource.ga4:GA4Source"
```
Read each surrounding block before editing to match exact whitespace/quoting.

- [ ] **Step 7: Regenerate the lockfile**

Run: `uv lock`
Expected: `uv.lock` updated; `google-analytics-data` no longer present.
Verify: `grep -c "google-analytics-data" uv.lock` → expected `0`.

- [ ] **Step 8: Reinstall and run ruff**

Run:
```bash
uv sync --all-extras
uv run ruff check src tests
uv run ruff format --check src tests
```
Expected: no errors, no remaining import of `recotem.datasource.ga4`, `recotem._metrics_ga4`, or `get_ga4_max_pages`.

- [ ] **Step 9: Run the full test suite**

Run: `uv run pytest tests`
Expected: PASS (no collection errors, no references to deleted modules).

Sanity-check that the `ga4` type is gone from discovery:
```bash
uv run recotem schema | grep -c '"ga4"'    # expected 0 (ga4 not a source type)
```
(The string `ga4` may still appear inside `examples/ga4-bigquery`-style free text elsewhere, but `recotem schema` must not list it as a source `type`.)

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor: remove GA4 Data API data source

The GA4 Data API cannot return a stable userId suitable for
collaborative-filtering training, so the ga4 source is removed.
GA4 via the BigQuery export (examples/ga4-bigquery, type: bigquery)
is unaffected."
```

---

## Task 2: Remove GA4 Data API from docs, examples, and CI

These changes don't affect the Python test suite, so they form a second commit.

**Files:**
- Delete: `docs/data-sources/ga4.md`
- Delete: `examples/ga4-data-api/` (directory)
- Modify: `.github/workflows/test.yml`
- Modify: `docs/recipe-reference.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Delete the GA4 doc and example**

```bash
git rm docs/data-sources/ga4.md
git rm -r examples/ga4-data-api
```

- [ ] **Step 2: Remove `--extra ga4` from `.github/workflows/test.yml`**

Two occurrences (currently l.63 and l.98), both of the form:
```
        run: uv sync --frozen --dev --extra bigquery --extra s3 --extra gcs --extra metrics --extra postgres --extra mysql --extra sqlite --extra ga4
```
Remove the trailing ` --extra ga4` from both lines (use a replace-all on the exact ` --extra ga4` token if it is unique to these lines; otherwise edit each line).

- [ ] **Step 3: Remove the GA4 section from `docs/recipe-reference.md`**

Delete the whole `### source.type = ga4` section including its trailing `---` separator (currently l.101 through l.133 — the section ends just before the next `###`/section). Read the file around l.99-135 first to confirm exact boundaries (the section is bounded by the preceding `See [docs/data-sources/sql.md]...` block above and the next section heading below).

Also edit the discriminator description (currently l.10) to drop `ga4`:
```
... the discriminator (`csv`, `parquet`, `bigquery`, `sql`, `ga4`, or any plugin). ...
```
→ drop `, `ga4``:
```
... the discriminator (`csv`, `parquet`, `bigquery`, `sql`, or any plugin). ...
```
After editing, grep the file: `grep -ni "ga4" docs/recipe-reference.md` → expected `0` matches.

- [ ] **Step 4: Remove GA4 from `README.md`**

Currently l.38:
```
- Pluggable data sources (built-in: CSV / Parquet / BigQuery / SQL / GA4; extend via Python entry points)
```
→
```
- Pluggable data sources (built-in: CSV / Parquet / BigQuery / SQL; extend via Python entry points)
```
Then grep: `grep -ni "ga4" README.md` → expected `0` matches.

- [ ] **Step 5: Update `CLAUDE.md`**

Make these five edits (read each line first to match exactly):

1. l.39 datasource list — drop ` / ga4`:
   `├── datasource/         DataSource Protocol + entry_points discovery (csv / parquet / bigquery / sql / ga4)`
   → `... (csv / parquet / bigquery / sql)`
2. l.62 docs list — drop `ga4.md`:
   `├── data-sources/       bigquery.md, csv.md, ga4.md, sql.md`
   → `├── data-sources/       bigquery.md, csv.md, sql.md`
3. l.69 examples list — drop `ga4-data-api/` (keep `ga4-bigquery/`):
   `examples/               quickstart/, csv-local/, sql-sqlite/, ga4-bigquery/, ga4-data-api/, k8s/, plugins/echo-source/, tutorial-purchase-log/`
   → remove `ga4-data-api/, ` so it reads `..., ga4-bigquery/, k8s/, ...`
4. l.105 discriminator — drop ` | ga4`:
   ``- `source.type` is a discriminator (`csv` | `parquet` | `bigquery` | `sql` | `ga4` | plugins).``
   → ``... | `sql` | plugins).``
5. l.220 env-var table — delete the entire `RECOTEM_GA4_MAX_PAGES` row.

After editing, grep: `grep -ni "ga4" CLAUDE.md` → expected `0` matches (the `ga4-bigquery/` example reference is intentionally kept, so this should actually return the `ga4-bigquery/` line; confirm ONLY that line remains).

- [ ] **Step 6: Final repo-wide verification**

```bash
# No GA4 Data API references remain outside the preserved ga4-bigquery example
grep -rin "ga4" --include="*.py" --include="*.toml" --include="*.md" --include="*.yaml" --include="*.yml" . \
  | grep -v "/.git/" | grep -v "examples/ga4-bigquery/" \
  | grep -viE "google.analytics export|GA4 events_\*|GA4 export|GA4 query patterns|GA4 BigQuery"
```
Expected: only intentional BigQuery-export mentions remain (`docs/data-sources/bigquery.md` GA4 events_* pattern, `docs/README.md` GA4 query patterns, `CLAUDE.md` `ga4-bigquery/` example line). NO references to `type: ga4`, `recotem[ga4]`, `RECOTEM_GA4_MAX_PAGES`, `_metrics_ga4`, `datasource/ga4`, or `Data API`.

```bash
uv run ruff format --check src tests   # docs/yaml unaffected, just re-confirm clean
uv run pytest tests                    # still green
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "docs: remove GA4 Data API references from docs, examples, and CI

Drops docs/data-sources/ga4.md, examples/ga4-data-api/, the --extra ga4
CI step, and GA4 mentions in README/CLAUDE.md/recipe-reference.
Keeps examples/ga4-bigquery (type: bigquery) and the BigQuery-export
GA4 query-pattern docs."
```

---

## Task 3: Open the Pull Request

- [ ] **Step 1: Push the branch**

```bash
git push -u origin <branch-name>
```

- [ ] **Step 2: Create the PR**

```bash
gh pr create --title "Remove GA4 Data API data source" --body "$(cat <<'EOF'
## Summary
Removes the GA4 Data API data source (`source.type: ga4`). The GA4 Data API
cannot return a stable user identifier (`userId`) suitable for
collaborative-filtering training, so the source has no viable use.

## What was removed
- `src/recotem/datasource/ga4.py`, `src/recotem/_metrics_ga4.py`,
  `config.get_ga4_max_pages` + `RECOTEM_GA4_MAX_PAGES`
- `ga4` entry point + `ga4` optional-dependency extra (`google-analytics-data`)
- All GA4-Data-API tests (unit, metrics, fuzz)
- `docs/data-sources/ga4.md`, `examples/ga4-data-api/`, the `--extra ga4` CI step,
  and GA4 mentions in README / CLAUDE.md / recipe-reference

## What was preserved
- `examples/ga4-bigquery/` (uses `type: bigquery`, not the `ga4` source) — the
  recommended way to use GA4 data, since the BigQuery export carries `userId`
- `docs/data-sources/bigquery.md` GA4 events_* query patterns

## Verification
- `uv run ruff check src tests` / `uv run ruff format --check src tests`
- `uv run pytest tests`
- `uv lock` regenerated (no `google-analytics-data`)
EOF
)"
```

---

## Self-Review (completed during planning)

**Spec coverage:** Every GA4-Data-API touchpoint found in the repo-wide `grep -in ga4` sweep maps to a task step — implementation (`ga4.py`), metrics (`_metrics_ga4.py`), config (`get_ga4_max_pages` + docstring), registry (fallback map + hints + comment), packaging (`pyproject.toml` extra + entry point + `all`), lockfile (`uv lock`), tests (3 deleted files + `test_config.py` + `test_datasource_registry.py` edits), CI (`test.yml` ×2), docs (`ga4.md`, `recipe-reference.md` ×2, `README.md`, `CLAUDE.md` ×5), example (`examples/ga4-data-api/`).

**Preservation guard:** `examples/ga4-bigquery/` confirmed to use `type: bigquery` (not `ga4`) and to not depend on `google-analytics-data`; explicitly excluded from every deletion/grep filter.

**Ordering/dependency:** Source + metrics + config deletions are paired with their test deletions in Task 1's single commit so the suite never enters a broken-import state. Lockfile regeneration follows the `pyproject.toml` edit. Docs/CI (no test impact) are isolated in Task 2.

**Placeholder scan:** No TBD/"add error handling"/"similar to" placeholders; every code edit shows exact before/after text or an exact `git rm`.
