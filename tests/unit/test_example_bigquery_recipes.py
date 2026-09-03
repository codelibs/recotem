"""Guards for the shipped BigQuery example recipes.

`examples/ga4-bigquery/recipe.yaml` is explicitly offered for reuse ("replace
`analytics_123` with your GA4 export dataset name"), so a costing mistake in it
is charged to whoever copies it.  BigQuery decides wildcard-table pruning per
statement: one `events_*` reference without its own `_TABLE_SUFFIX` predicate
drops pruning for the entire query, the outer `BETWEEN` degrades to a plain row
filter, and every run scans the whole export regardless of `lookback_days`.

Measured against `bigquery-public-data.ga4_obfuscated_sample_ecommerce` (92 days,
3.34 GiB) with a dry run: the unpruned form scanned a constant 1,029,558,211
bytes for a 1-day, 7-day and 31-day window alike, while the pruned form scanned
6,010,287 / 62,536,522 / 277,526,167 bytes respectively.

These tests need no credentials: they are a static check on the shipped YAML.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
BIGQUERY_EXAMPLE_RECIPES = sorted(
    p
    for p in (REPO_ROOT / "examples").rglob("recipe.yaml")
    if (yaml.safe_load(p.read_text()) or {}).get("source", {}).get("type") == "bigquery"
)


def _scope_of_each_char(query: str) -> list[int]:
    """Map every character to the id of its innermost enclosing paren block.

    Block 0 is the statement itself.  Each `(` opens a new block; the matching
    `)` returns to the enclosing one.  Characters inside a string literal or a
    line comment are attributed to the block they sit in, which is all this
    needs -- neither can contain an unbalanced paren in these recipes.
    """
    scopes: list[int] = []
    stack = [0]
    next_id = 1
    for ch in query:
        if ch == "(":
            scopes.append(stack[-1])
            stack.append(next_id)
            next_id += 1
        elif ch == ")":
            if len(stack) > 1:
                stack.pop()
            scopes.append(stack[-1])
        else:
            scopes.append(stack[-1])
    return scopes


def _blocks(query: str) -> dict[int, str]:
    """Text belonging directly to each paren block, keyed by block id."""
    out: dict[int, list[str]] = {}
    for ch, scope in zip(query, _scope_of_each_char(query), strict=True):
        out.setdefault(scope, []).append(ch)
    return {k: "".join(v) for k, v in out.items()}


def _wildcard_table_scopes(query: str) -> set[int]:
    """Block ids that contain a backtick-quoted wildcard table reference."""
    scopes = _scope_of_each_char(query)
    found: set[int] = set()
    in_ref = False
    start = 0
    for i, ch in enumerate(query):
        if ch == "`":
            if in_ref:
                if "*" in query[start:i]:
                    found.add(scopes[start])
                in_ref = False
            else:
                in_ref = True
                start = i + 1
    return found


@pytest.mark.parametrize(
    "recipe_path", BIGQUERY_EXAMPLE_RECIPES, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_every_wildcard_table_reference_is_pruned(recipe_path: Path) -> None:
    """Each `events_*` reference carries `_TABLE_SUFFIX` in its own scope.

    Regression guard: before this was fixed, the per-user activity subquery in
    the GA4 example referenced `analytics_123.events_*` with no `_TABLE_SUFFIX`
    predicate, which silently defeated the outer window's pruning.
    """
    query = yaml.safe_load(recipe_path.read_text())["source"]["query"]
    blocks = _blocks(query)
    unpruned = [
        scope
        for scope in _wildcard_table_scopes(query)
        if "_TABLE_SUFFIX" not in blocks.get(scope, "")
    ]
    assert not unpruned, (
        f"{recipe_path.relative_to(REPO_ROOT)}: a wildcard table reference has no "
        f"_TABLE_SUFFIX predicate in its own scope. BigQuery prunes per statement, "
        f"so this makes the whole query scan every table in the export and makes "
        f"any lookback parameter a no-op on bytes scanned. Offending scope(s): "
        f"{[blocks[s].strip()[:200] for s in unpruned]}"
    )


def test_scope_checker_catches_the_original_defect() -> None:
    """The guard above fails on the shape that shipped, and passes once fixed."""
    unpruned_inner = """
    SELECT user_pseudo_id AS user_id
    FROM `analytics_123.events_*`
    WHERE _TABLE_SUFFIX BETWEEN '20210101' AND '20210131'
      AND user_pseudo_id IN (
        SELECT user_pseudo_id FROM `analytics_123.events_*` GROUP BY 1
      )
    """
    blocks = _blocks(unpruned_inner)
    offenders = [
        s
        for s in _wildcard_table_scopes(unpruned_inner)
        if "_TABLE_SUFFIX" not in blocks.get(s, "")
    ]
    assert len(offenders) == 1

    pruned_inner = unpruned_inner.replace(
        "SELECT user_pseudo_id FROM `analytics_123.events_*` GROUP BY 1",
        "SELECT user_pseudo_id FROM `analytics_123.events_*` "
        "WHERE _TABLE_SUFFIX BETWEEN '20210101' AND '20210131' GROUP BY 1",
    )
    blocks = _blocks(pruned_inner)
    assert not [
        s
        for s in _wildcard_table_scopes(pruned_inner)
        if "_TABLE_SUFFIX" not in blocks.get(s, "")
    ]


def _block_spans(query: str) -> dict[int, tuple[int, int]]:
    """The `[start, end)` index range of each paren block, keyed by block id."""
    spans: dict[int, list[int]] = {0: [0, len(query)]}
    stack = [0]
    next_id = 1
    for i, ch in enumerate(query):
        if ch == "(":
            spans[next_id] = [i + 1, len(query)]
            stack.append(next_id)
            next_id += 1
        elif ch == ")" and len(stack) > 1:
            spans[stack.pop()][1] = i
    return {k: (v[0], v[1]) for k, v in spans.items()}


def _table_suffix_predicates(query: str) -> dict[int, list[str]]:
    """The text of each `_TABLE_SUFFIX ...` predicate, keyed by its scope.

    A predicate runs from the `_TABLE_SUFFIX` token to the next `AND` at the
    same paren depth, or to the end of its block.  Nested calls such as
    `FORMAT_DATE(..., DATE_SUB(..., INTERVAL @lookback_days DAY))` sit deeper
    than the predicate itself, so the span has to follow the raw query rather
    than the text belonging directly to the scope.
    """
    scopes = _scope_of_each_char(query)
    spans = _block_spans(query)
    out: dict[int, list[str]] = {}
    token = "_TABLE_SUFFIX"
    i = query.find(token)
    while i != -1:
        scope = scopes[i]
        stop = spans[scope][1]
        j = i + len(token)
        while j < stop:
            if scopes[j] == scope and query.startswith("AND", j):
                break
            j += 1
        out.setdefault(scope, []).append(query[i:j])
        i = query.find(token, j)
    return out


@pytest.mark.parametrize(
    "recipe_path", BIGQUERY_EXAMPLE_RECIPES, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_lookback_parameter_bounds_every_reference(recipe_path: Path) -> None:
    """A recipe with a lookback parameter applies it to every wildcard scope.

    Bounding only the outer scope is the shape that made `lookback_days` a
    no-op on cost while still looking like a rolling window: every wildcard
    scope was pruned *syntactically*, but only one of them moved with the
    parameter.
    """
    source = yaml.safe_load(recipe_path.read_text())["source"]
    params = source.get("query_parameters") or {}
    lookback = [k for k in params if "lookback" in k.lower()]
    if not lookback:
        pytest.skip("recipe declares no lookback parameter")
    query = source["query"]
    predicates = _table_suffix_predicates(query)
    for scope in _wildcard_table_scopes(query):
        spans = predicates.get(scope, [])
        assert any(f"@{name}" in span for span in spans for name in lookback), (
            f"{recipe_path.relative_to(REPO_ROOT)}: the _TABLE_SUFFIX predicate "
            f"guarding a wildcard table reference does not use {lookback}, so the "
            f"lookback window does not bound that reference. Predicate(s): {spans}"
        )
