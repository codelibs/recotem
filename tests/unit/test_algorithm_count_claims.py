"""Prose that counts the supported algorithms must count the ones that exist.

`#211` added `BPRFMRecommender`, taking `SUPPORTED_CLASS_NAMES` from six entries
to seven, and `#220` corrected one release note that still described the world
before it.  Two "all six algorithms" claims survived that pass, in the release
that ships the seventh -- one of them carrying a "no retrain is needed"
conclusion.

The same lesson `#220` drew about the `dim^2.4` sentence applies: a guard that
looks at one file lets the next copy drift.  This one reads every shipped prose
file (``CLAUDE.md``, ``README.md``, ``CHANGELOG.md`` and everything under
``docs/``) and compares against the code.

Only "all N algorithms" is checked -- a total.  "six of the seven supported
algorithms", "of the six algorithms trained under 2.0.0", and any other scoped
or historical count are deliberately not matched: they are how a claim that
covers a subset is written honestly, which is the fix this guard exists to
protect.
"""

from __future__ import annotations

import re
from pathlib import Path

from recotem.training.algorithms import SUPPORTED_CLASS_NAMES

_ROOT = Path(__file__).resolve().parents[2]

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

# "all six algorithms", "all seven supported algorithms",
# "all six algorithms Recotem can build", "all 7 shipped algorithms".
_TOTAL_CLAIM = re.compile(
    r"\ball\s+(?P<count>[a-z]+|\d+)\s+"
    r"(?:supported\s+|shipped\s+|available\s+)?algorithms\b",
    re.IGNORECASE,
)


def _prose_files() -> list[Path]:
    files = [_ROOT / name for name in ("CLAUDE.md", "README.md", "CHANGELOG.md")]
    files.extend(sorted((_ROOT / "docs").rglob("*.md")))
    return [f for f in files if f.is_file()]


def _parse_count(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token.lower())


def test_total_algorithm_counts_match_the_supported_set() -> None:
    expected = len(SUPPORTED_CLASS_NAMES)
    wrong: list[str] = []
    checked = 0
    for path in _prose_files():
        # Scanned whole-file, not line by line: these are wrapped Markdown
        # paragraphs, and "runs for all seven\n  algorithms" is one claim.
        text = path.read_text(encoding="utf-8")
        for match in _TOTAL_CLAIM.finditer(text):
            count = _parse_count(match.group("count"))
            if count is None:
                continue  # e.g. "all these algorithms" -- not a count.
            checked += 1
            if count != expected:
                lineno = text.count("\n", 0, match.start()) + 1
                phrase = " ".join(match.group(0).split())
                wrong.append(
                    f"{path.relative_to(_ROOT)}:{lineno}: "
                    f"{phrase!r} (supported set has {expected})"
                )
    assert checked, (
        "no 'all N algorithms' claim found in any shipped prose file -- the "
        "regex has stopped matching and this guard is watching nothing."
    )
    assert not wrong, (
        "prose claims a different number of algorithms than "
        f"recotem.training.algorithms.SUPPORTED_CLASS_NAMES ({expected}: "
        f"{sorted(SUPPORTED_CLASS_NAMES)}):\n  " + "\n  ".join(wrong) + "\n"
        "A statement that really covers a subset should say so ('six of the "
        "seven supported algorithms') rather than restate the total."
    )
