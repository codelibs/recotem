"""The release notes must carry the Azure URI rule the loader actually applies.

`#267` changed `_check_userinfo` in both directions relative to the released
2.0.0 and recorded neither in `CHANGELOG.md`:

    abfss://cont@acct.dfs.core.windows.net/x   2.0.0: REJECTED   now: accepted
    az://user:secret@acct.blob.core.windows.net/x
                                              2.0.0: ACCEPTED   now: rejected

The second is a breaking change — a recipe that loaded under 2.0.0 exits 2 —
and an operator planning an upgrade reads the notes, not `loader.py`.

The rule is asserted against the loader first and the notes second, on purpose.
A guard that only greps prose goes green the moment the prose is reworded and
stays green if the code drifts underneath it; a guard that only exercises the
loader duplicates `tests/unit/test_recipe_loader.py` and says nothing about
what shipped in the notes. Both halves have to hold together, so reverting
either one fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from recotem.recipe.errors import RecipeError
from recotem.recipe.loader import load_recipe

_ROOT = Path(__file__).resolve().parents[2]
_CHANGELOG = _ROOT / "CHANGELOG.md"

# The `@` here separates the container from the storage account.  Azure's own
# documentation writes ADLS Gen2 paths this way, so it is the form an operator
# copies in.
_ADDRESSING = "abfss://cont@acct.dfs.core.windows.net/data.csv"

# A real userinfo pair on the alias that used to skip the check entirely.
_CREDENTIAL = "az://user:secret@acct.blob.core.windows.net/data.csv"

_RECIPE = """\
name: azure_uri_probe
source:
  type: csv
  path: {path}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""


def _recipe_file(tmp_path: Path, path: str) -> Path:
    f = tmp_path / "recipe.yaml"
    f.write_text(
        _RECIPE.format(path=path, out=tmp_path / "out.recotem"), encoding="utf-8"
    )
    return f


def _unreleased_section() -> str:
    """The CHANGELOG section for the version being prepared (the first one)."""
    text = _CHANGELOG.read_text(encoding="utf-8")
    headings = [m.start() for m in re.finditer(r"^## \[", text, re.MULTILINE)]
    assert len(headings) >= 2, "CHANGELOG has fewer than two version sections"
    return text[headings[0] : headings[1]]


def test_loader_applies_the_split_azure_rule() -> None:
    """Source of truth: addressing accepted, a real user:pass pair refused."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        recipe = load_recipe(_recipe_file(tmp, _ADDRESSING))
        assert recipe.source.path == _ADDRESSING, (
            f"the loader no longer accepts {_ADDRESSING!r}. The CHANGELOG entry "
            "this guard protects tells operators it does; update both together."
        )
        with pytest.raises(RecipeError, match="embedded credentials"):
            load_recipe(_recipe_file(tmp, _CREDENTIAL))


def _azure_entry() -> str:
    """The one CHANGELOG bullet that talks about Azure URIs.

    Scoped to the bullet rather than to the whole release section: the section
    already says "breaking" and "behaviour change" about four other entries, so
    a section-wide search for those words would pass with no Azure entry at all
    -- green over someone else's prose.
    """
    section = _unreleased_section()
    marker = section.find("abfss://")
    assert marker != -1, (
        "no CHANGELOG entry in the release section mentions `abfss://`. #267 "
        "changed how recotem reads every Azure URI, in both directions, and "
        "the notes are the only place an operator planning an upgrade looks."
    )
    starts = [m.start() for m in re.finditer(r"^- \*\*", section, re.MULTILINE)]
    start = max((s for s in starts if s < marker), default=0)
    end = next((s for s in starts if s > marker), len(section))
    return section[start:end]


def test_release_notes_record_both_halves_of_the_azure_uri_change() -> None:
    """Naming the fix is not enough; the breaking half has to be there too."""
    entry = _azure_entry()

    for scheme in ("`az://`", "`abfs://`", "`abfss://`"):
        assert scheme in entry, (
            f"the Azure CHANGELOG entry never mentions {scheme}. All three are "
            "aliases for one adlfs filesystem and #267 changed all three."
        )

    assert "container@account" in entry, (
        "the entry does not name the `container@account` addressing form that "
        "2.0.0 refused and this release accepts."
    )

    # The half that breaks an existing recipe.  `az://user:pass@…` loaded under
    # 2.0.0 (the scheme was simply absent from the reject list) and now exits 2,
    # so the notes have to say so rather than only advertising the fix.
    assert re.search(r"user:(pass|secret)@", entry), (
        "the entry describes the Azure URI fix without saying that a real "
        "`user:pass@` pair is now refused on `az://` as well. That is a "
        "breaking change against 2.0.0: a recipe that loaded then exits 2 now."
    )
    assert re.search(r"behaviour change|behavior change|breaking", entry, re.I), (
        "nothing in the entry marks the `az://user:pass@` tightening as a "
        "change against 2.0.0, so a reader upgrading has no reason to check "
        "their recipes for it."
    )
