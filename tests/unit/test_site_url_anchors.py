"""Documentation-site anchors this package emits must be spelled as slugs.

The site generates a heading's anchor by lowercasing it and folding runs of
whitespace and underscores to single hyphens.  A heading like
``recotem train exits 4 with feature_axis_error`` therefore anchors at
``#recotem-train-exits-4-with-feature-axis-error``: an anchor written with the
underscore the *code* uses does not exist, and a browser silently lands at the
top of the page instead of at the section the message was pointing to.

Two existing scans cannot see this.  ``check-release-tag.sh`` section 4b greps
line by line and only compares the version segment; and a long message in this
codebase is built from adjacent string fragments, so the version segment and
the anchor are on different source lines and no line-based grep sees the whole
URL at all.  Parsing is what finds it: ``ast`` folds implicit concatenation and
f-string parts into the single value the operator receives.

What can be checked without the documentation repository is the *shape* of the
anchor, and shape is the whole failure: an underscore in a site anchor is
always wrong, because no generated slug contains one.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

# Matches the URL as assembled, not as written across source lines.
_SITE_URL = re.compile(
    r"recotem\.org/\d+\.\d+/[A-Za-z0-9_/-]*(?:\.[A-Za-z0-9_/-]+)*"
    r"(#[A-Za-z0-9_-]+)?"
)


def _string_values(tree: ast.AST) -> list[tuple[int, str]]:
    """Every string literal, with implicit concatenation and f-strings folded.

    ``ast.walk`` yields a ``JoinedStr`` *and*, separately, each ``Constant``
    inside it, so a value reached both ways would be returned twice -- which
    reports one offending URL as two and inflates the "how many did we look at"
    count that guards against a vacuous pass.  The literal parts of an f-string
    are therefore skipped in their own right; they are already covered by the
    folded value.
    """
    folded_parts = {
        part
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for part in node.values
    }
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            out.append(
                (
                    node.lineno,
                    "".join(
                        v.value
                        for v in node.values
                        if isinstance(v, ast.Constant) and isinstance(v.value, str)
                    ),
                )
            )
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node not in folded_parts
        ):
            out.append((node.lineno, node.value))
    return out


def _underscore_anchor_offenders(source: str, label: str) -> tuple[list[str], int]:
    """Report site anchors containing ``_`` in every URL ``source`` assembles.

    The whole detector lives here -- string extraction, URL match, anchor
    shape -- so that the control below exercises the same code path the real
    scan does.  Returns the offenders and how many anchored URLs were examined,
    because "found nothing" and "looked at nothing" are different results.
    """
    offenders: list[str] = []
    checked = 0
    for lineno, value in _string_values(ast.parse(source)):
        for match in _SITE_URL.finditer(value):
            anchor = match.group(1)
            if anchor is None:
                continue
            checked += 1
            if "_" in anchor:
                offenders.append(f"{label}:{lineno}: {match.group(0)}")
    return offenders, checked


def test_no_emitted_site_anchor_contains_an_underscore() -> None:
    offenders: list[str] = []
    checked = 0
    for path in sorted(SRC.rglob("*.py")):
        found, seen = _underscore_anchor_offenders(
            path.read_text(encoding="utf-8"), str(path.relative_to(SRC.parent.parent))
        )
        offenders.extend(found)
        checked += seen

    assert checked, (
        "no anchored recotem.org URL found anywhere under src/, so this test is "
        "vouching for nothing -- the URLs moved, or the pattern stopped matching."
    )
    assert not offenders, (
        "these anchors cannot exist: the site folds underscores to hyphens when "
        "it generates a heading's slug, so the link lands at the top of the page "
        "instead of at the section. Spell the anchor as the slug, not as the "
        "identifier in the heading:\n  " + "\n  ".join(offenders)
    )


# Both fixtures below spell the URL with '.html', and must keep it.  tests/ is
# one of check-release-tag.sh's SITE_ROOTS, and section 4c matches the URL
# wherever it appears -- it cannot tell sample source inside a string literal
# from a real link, so an extensionless one here refuses the release tag.  The
# script elides its own counter-examples for the same reason.  Nothing in these
# tests depends on the suffix.
_PLANTED_BAD = (
    "raise E(\n"
    '    f"... the remedy for {name} is documented at "\n'
    '    f"https://recotem.org/2.1/docs/operations.html"\n'
    '    f"#recotem-train-exits-4-with-feature_axis_error"\n'
    ")\n"
)

_PLANTED_GOOD = _PLANTED_BAD.replace("feature_axis_error", "feature-axis-error")


def test_the_scan_catches_a_planted_offender_split_across_source_lines() -> None:
    """Positive control for the scan above.

    The defect this file exists for was invisible to every line-based scan: the
    version segment and the anchor sat on different source lines, so no `grep`
    ever saw the whole URL.  The fixture reproduces that shape -- and adds a
    placeholder, because that is how the message in ``training/features.py`` is
    really written.

    This asserts on the detector's *output*, not on an intermediate value.  An
    earlier version of this control only checked that two fragments had been
    folded into one string, which is something CPython's parser does on its own:
    measured, that assertion still passed with the folding branch of
    :func:`_string_values` deleted, so it could not fail for any reason the scan
    cares about.
    """
    offenders, checked = _underscore_anchor_offenders(_PLANTED_BAD, "<planted>")

    assert checked, (
        "the planted URL was not recognised as an anchored recotem.org link at "
        "all, so the scan above is looking at nothing -- _SITE_URL or the string "
        "extraction stopped matching."
    )
    assert len(offenders) == 1, (
        "the scan no longer reports an underscored anchor assembled from "
        f"fragments on separate source lines: {offenders}"
    )
    assert "feature_axis_error" in offenders[0], offenders


def test_the_scan_passes_the_same_url_spelled_as_a_slug() -> None:
    """Negative control: the scan must not flag a correctly spelled anchor.

    Without this, a detector that reported *every* anchor would satisfy the
    positive control while making the real scan unusable.
    """
    offenders, checked = _underscore_anchor_offenders(_PLANTED_GOOD, "<planted>")

    assert checked, "the slug-spelled URL was not recognised as an anchored link"
    assert not offenders, (
        "a hyphenated anchor is what the site actually generates and must not be "
        f"reported: {offenders}"
    )
