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
    """Every string literal, with implicit concatenation and f-strings folded."""
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
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
    return out


def test_no_emitted_site_anchor_contains_an_underscore() -> None:
    offenders: list[str] = []
    checked = 0
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, value in _string_values(tree):
            for match in _SITE_URL.finditer(value):
                anchor = match.group(1)
                if anchor is None:
                    continue
                checked += 1
                if "_" in anchor:
                    rel = path.relative_to(SRC.parent.parent)
                    offenders.append(f"{rel}:{lineno}: {match.group(0)}")

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


def test_the_scan_sees_a_url_split_across_source_lines() -> None:
    """Anti-vacuity control.

    The defect this file exists for was invisible to every line-based scan
    because the version segment and the anchor sat on different source lines.
    A scan that quietly stopped folding concatenation would pass the test above
    for the wrong reason, so assert that the folding still happens.
    """
    tree = ast.parse(
        'X = (\n    "https://recotem.org/2.1/docs/operations"\n'
        '    "#recotem-train-exits-4-with-feature_axis_error"\n)\n'
    )
    values = [v for _lineno, v in _string_values(tree)]
    assert any("operations#recotem-train" in v for v in values), (
        "adjacent string fragments are no longer folded into one value, so the "
        "scan above cannot see a URL written the way this codebase writes long "
        f"messages: {values}"
    )
