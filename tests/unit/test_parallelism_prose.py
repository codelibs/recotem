"""The parallelism figures must not be published as algorithm constants.

`docs/recipe-reference.md` gives per-algorithm `parallelism: 1` vs `8` timings
and peak-RSS figures measured on one 100k-row fixture.  Two of them move a long
way with the item count, because a `DenseSLIM` trial's working set is an
`n_items x n_items` dense matrix and every concurrent Optuna thread builds its
own.  Re-measured, `n_trials: 20`:

    1,000 items:   5.2 s ->   3.7 s (1.39x),   272 MB ->   427 MB (1.57x)
    5,000 items: 770.2 s -> 134.3 s (5.74x), 1,058 MB -> 3,719 MB (3.51x)

An operator sizing a training host off the 1.95x in the original text gets a
figure that is 1.8x low on a 5,000-item catalogue and worse above it.  The page
must therefore carry the catalogue caveat next to the numbers, not just the
numbers.  Nothing executes shipped Markdown, so this is a prose guard in the
same spirit as `test_no_shipped_prose_still_calls_the_feature_cost_cubic`.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "docs" / "recipe-reference.md"


def test_parallelism_numbers_carry_the_catalogue_caveat() -> None:
    """The fixture-dependence section must exist and be reachable from the row."""
    text = REF.read_text(encoding="utf-8")
    assert "#### How the parallelism numbers move with the catalogue" in text, (
        "the per-algorithm parallelism speedups and peak-RSS figures are "
        "fixture-dependent (DenseSLIM: 1.39x/1.57x at 1,000 items, "
        "5.74x/3.51x at 5,000) and need a section saying so"
    )
    row = next(
        line for line in text.splitlines() if line.startswith("| `parallelism` |")
    )
    assert "how-the-parallelism-numbers-move-with-the-catalogue" in row, (
        "the parallelism row publishes the fixture-specific numbers without "
        "linking to the section that qualifies them"
    )


def test_bprfm_parallelism_is_documented_as_platform_dependent() -> None:
    """BPRFM ships in 2.1.0 and appeared in no parallelism guidance at all.

    Its measurement does not transfer between platforms: on macOS the lightfm
    extension is built without OpenMP, so BPRFM trains single-threaded and
    Optuna's threads have the machine to themselves; on Linux the extension is
    OpenMP-parallel and stacks the same way IALS does.  A bare number would be
    wrong on one of the two.
    """
    text = REF.read_text(encoding="utf-8")
    section = text.split("#### How the parallelism numbers move with the catalogue")[1]
    section = section.split("#### ")[0]
    assert "BPRFM" in section, "BPRFM has no parallelism guidance anywhere"
    assert "OpenMP" in section, (
        "BPRFM's parallelism figure is platform-dependent; the reason "
        "(macOS builds lightfm without OpenMP) has to be stated with it"
    )
