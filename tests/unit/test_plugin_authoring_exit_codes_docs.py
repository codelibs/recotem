"""``docs/plugin-authoring.md`` must state exit codes the product can produce.

The document told plugin authors three things that were not true:

* a `Config` contract violation exits **3** (it exits 2 -- plugin discovery runs
  inside recipe loading, so the registry's ``DataSourceError`` is re-raised as a
  ``RecipeError``);
* a duplicate ``type_name`` makes ``recotem serve`` "exit 3 at startup" (serve
  does not exit at all -- it logs ``recipe_load_error_skipped`` and keeps
  running with the recipe unloaded);
* an exception other than ``DataSourceError`` escaping ``fetch()`` or
  ``__init__`` "surfaces as exit code 1".

The last one is the sharpest: measured across eight deliberately broken
plugins, **the only way a plugin reaches exit 1 is a non-``DataSourceError``
escaping ``__init__`` under ``recotem validate``**. Under ``recotem train``
every plugin-side failure is exit 3, because the pipeline wraps an unrecognised
exception as ``Data fetch failed: ...``. So rule 7's stated motivation for
deferred imports -- avoiding "an ``ImportError`` with exit code 1" -- described
a consequence that, for ``train``, cannot happen.

That matters more than an ordinary typo. This is the contract a would-be
*contributor* reads, and an author who tests the claim finds the document
unreliable.

The assertions come in two layers so the file cannot drift back:

1. the three mapping facts that *explain* every row of the table, asserted
   against ``_map_exception_to_exit`` rather than restated;
2. the document text, pinned to the corrected claims and to the absence of the
   withdrawn ones.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from recotem._exit_codes import (
    _EXIT_CONFIG,
    _EXIT_DATASOURCE,
    _EXIT_RECIPE,
    _EXIT_UNKNOWN,
    _map_exception_to_exit,
)

_ROOT = Path(__file__).resolve().parents[2]
_DOC = _ROOT / "docs" / "plugin-authoring.md"


def _doc() -> str:
    return _DOC.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Layer 1 -- the mapping facts the table is derived from.
# ---------------------------------------------------------------------------


def test_datasource_error_maps_to_3() -> None:
    """Why a plugin that raises DataSourceError reports 3 from both commands."""
    from recotem.datasource.base import DataSourceError

    assert _map_exception_to_exit(DataSourceError("boom")) == _EXIT_DATASOURCE


def test_recipe_error_maps_to_2() -> None:
    """Why every *contract* violation reports 2 rather than 3.

    The registry raises ``DataSourceError``, but discovery happens inside
    recipe loading, so what reaches the CLI is a ``RecipeError``.
    """
    from recotem.recipe.errors import RecipeError

    assert _map_exception_to_exit(RecipeError("boom")) == _EXIT_RECIPE


def test_bare_importerror_maps_to_1() -> None:
    """Why an unwrapped ``__init__`` failure reports 1 under ``validate``.

    ``_probe_source`` maps the raw exception, and a bare ``ImportError`` has no
    mapping -- which is the entire reason the doc tells authors to raise
    ``DataSourceError`` from ``__init__``.
    """
    assert _map_exception_to_exit(ImportError("no module named x")) == _EXIT_UNKNOWN
    assert _map_exception_to_exit(RuntimeError("boom")) == _EXIT_UNKNOWN


# ---------------------------------------------------------------------------
# Layer 2 -- the document text.
# ---------------------------------------------------------------------------


def test_withdrawn_claims_are_gone() -> None:
    """The three statements the product does not support must not reappear."""
    doc = _doc()
    withdrawn = [
        # contract violations were said to be exit 3
        "plugin-discovery time (exit code 3)",
        # every non-DataSourceError was said to be exit 1
        "Any other exception surfaces as exit code 1",
        # deferred imports were motivated by an exit code train never emits
        "rather than an `ImportError` with exit code 1",
        # serve was said to exit 3 on a duplicate type_name
        "`recotem serve` exit 3 at startup",
    ]
    for claim in withdrawn:
        assert claim not in doc, (
            f"docs/plugin-authoring.md has regained a withdrawn claim: {claim!r}"
        )


def _paragraph_containing(needle: str) -> str:
    """Return the single line/paragraph that mentions *needle*.

    Assertions are scoped to the paragraph that makes the claim, never to the
    whole document.  A document-wide ``in`` check is satisfied by any *other*
    occurrence of the same phrase: an earlier draft of this file asserted
    ``"exit **2**" in doc`` and a mutation that reverted rule 1 back to "exit
    code 3" still passed, because the corrected sentence further down the file
    kept the phrase alive.
    """
    matches = [ln for ln in _doc().splitlines() if needle in ln]
    assert matches, f"no paragraph mentions {needle!r}"
    assert len(matches) == 1, (
        f"{needle!r} appears {len(matches)} times; the anchor is no longer "
        "unique, so a scoped assertion cannot be trusted"
    )
    return matches[0]


def test_duplicate_type_name_paragraph_says_exit_2_not_3() -> None:
    """Rule 1, scoped to its own paragraph."""
    para = _paragraph_containing("duplicate `type_name` values are reported")
    assert "exit **2**" in para, f"rule 1 must state exit 2; got: {para}"
    assert "exit code 3" not in para
    assert "does **not** exit" in para, (
        "rule 1 must say serve does not exit, which is the operational risk"
    )


def test_contract_violations_are_documented_as_exit_2() -> None:
    """Rule 2, scoped to its own paragraph."""
    para = _paragraph_containing("`validate_plugin_contract` raises")
    assert "exits **2**" in para, f"rule 2 must state exit 2; got: {para}"
    assert "exit code 3" not in para
    assert "wrapped into a `RecipeError`" in para, (
        "the doc must explain WHY it is 2 -- the DataSourceError is re-raised "
        "as a RecipeError during recipe loading -- or the number reads as "
        "arbitrary and will be 'corrected' back to 3"
    )


def test_no_paragraph_claims_exit_3_for_a_contract_violation() -> None:
    """Belt to the scoped braces above: catch the claim wherever it reappears.

    Any sentence that talks about plugin *discovery* or a duplicate
    ``type_name`` and also says "exit 3" is the withdrawn claim, no matter how
    it is worded or where in the file it lands.
    """
    offenders = [
        ln
        for ln in _doc().splitlines()
        if re.search(r"discovery|duplicate `?type_name|Duplicate DataSource", ln)
        and re.search(r"exit(?:s)?(?: code)? \*{0,2}3\*{0,2}\b", ln)
    ]
    assert not offenders, (
        "a contract/discovery failure is exit 2, not 3; offending line(s):\n"
        + "\n".join(offenders)
    )


def _serve_passage() -> str:
    """Return the passage describing what ``recotem serve`` does on a collision.

    Scoped for the same reason as ``_paragraph_containing``, and fixed after a
    probe showed the file-wide version of this test was passing on luck: of its
    three needles, ``recipe_load_error_skipped`` appears **twice** in this file
    (rule 1 mentions it too), so that needle proves nothing on its own. The
    assertion held only because a *companion* needle happened to be unique — and
    a later edit that reworded the passage while mentioning
    ``recipes_directory_loaded_lenient`` anywhere else would have silenced it
    without anyone noticing.

    Scoping removes the dependency on needle uniqueness entirely: only the
    anchor has to be unique, and that is asserted.
    """
    text = _doc()
    marker = "## Exit codes a plugin can actually produce"
    assert text.count(marker) == 1, (
        f"expected exactly one exit-code section heading, found "
        f"{text.count(marker)}; the anchor is no longer unique"
    )
    start = text.index("If two installed plugins both declare")
    return text[start : text.index(marker)]


def test_serve_is_documented_as_not_exiting_on_a_duplicate_type_name() -> None:
    """Serve keeps running with the recipe unloaded; that is the operational risk."""
    passage = _serve_passage()
    assert "does not\nexit at all" in passage or "does not exit at all" in passage, (
        "the serve passage must say recotem serve does NOT exit on a "
        "duplicate type_name"
    )
    assert "recipe_load_error_skipped" in passage
    assert "recipes_directory_loaded_lenient" in passage


def test_exit_code_table_is_present_and_matches_measurement() -> None:
    """Pin the measured rows, so a revert to the old numbers fails here.

    Each tuple is (row label fragment, train code, validate code) exactly as
    measured by installing the corresponding broken plugin and running the real
    CLI.
    """
    doc = _doc()
    rows = [
        ("omits the `type` discriminator", "2", "2"),
        ("`type` is `str` rather than `Literal`", "2", "2"),
        ("disagrees with `type_name`", "2", "2"),
        ("`no_expand_fields` missing", "2", "2"),
        ("collides with another plugin", "2", "2"),
        ("`fetch()` raises `DataSourceError`", "3", "0 †"),
        ("`fetch()` raises any other exception", "3", "0 †"),
        ("`__init__` raises `DataSourceError`", "3", "3"),
        ("`__init__` raises any other exception", "3", "**1**"),
    ]
    for label, train_code, validate_code in rows:
        line = next(
            (ln for ln in doc.splitlines() if label in ln and ln.startswith("|")),
            None,
        )
        assert line is not None, f"exit-code table row missing: {label!r}"
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert cells[1] == train_code, (
            f"row {label!r}: doc says train={cells[1]!r}, measured {train_code!r}"
        )
        assert cells[2] == validate_code, (
            f"row {label!r}: doc says validate={cells[2]!r}, measured {validate_code!r}"
        )


def test_no_exit_code_claim_names_a_code_that_does_not_exist() -> None:
    """Every `exit N` in the file must be a real code from the CLI table."""
    valid = {
        0,
        _EXIT_UNKNOWN,
        _EXIT_RECIPE,
        _EXIT_DATASOURCE,
        4,
        5,
        6,
        7,
        _EXIT_CONFIG,
    }
    found = {
        int(m) for m in re.findall(r"exit(?:s)?(?: code)? \*{0,2}(\d+)\*{0,2}", _doc())
    }
    assert found, "no exit-code claims found -- has the file been restructured?"
    assert found <= valid, f"doc names non-existent exit codes: {sorted(found - valid)}"


@pytest.mark.parametrize(
    "anchor",
    ["## Exit codes a plugin can actually produce"],
)
def test_exit_code_section_exists(anchor: str) -> None:
    assert anchor in _doc()
