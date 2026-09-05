"""`best_score` guidance must say what the number is not, and set a real bar.

Two separate failures this guards, both found by measuring rather than reading:

1. **The validation bar was popularity.** `docs/operations.md` told the
   operator to "score the served model and a most-popular-items list on it,
   and require the model to win". On a personalisable dataset that bar is
   nearly free. Measured across four industries with verified leave-one-out
   holdouts, the shipped model beat popularity by 11x to 56x on ndcg@10 while
   beating a hand-written 30-line item-item cosine kNN by between -9% and
   +56% -- including one catalogue where it beat popularity 11.4x and *lost*
   to the kNN. Popularity would have called that run a success.

2. **The popularity bar can be not merely low but degenerate.** On a
   recency-driven catalogue, measured over four runs of a synthetic news
   dataset split by time, popularity scored ndcg@10 = 0.0000 -- none of the
   ten most-popular training articles survives into the holdout. "Require the
   model to win" is then vacuously true for any model that returns anything,
   while the shipped model ranged from 39% below to 1% above the same 30-line
   kNN. Saying the bar is *low* does not cover the case where there is no bar.

3. **Nothing said what `best_score` measures.** It is the winning trial's
   score on recotem's own internal split, over the same trained item set --
   not an estimate of quality on the operator's task, and not a measurement of
   the cold-start paths at all. It is also the criterion the search maximises,
   so a disagreement with the operator's task is acted on, not merely
   reported.

Both guards read the whole file and assert they located their section before
asserting anything about it, so a rename cannot switch them off silently --
`#229`'s design, and the failure mode `#220` hit when a regex quietly stopped
matching.
"""

from __future__ import annotations

import re
from pathlib import Path

_DOC = Path(__file__).resolve().parents[2] / "docs" / "operations.md"


def _section(heading: str) -> str:
    """Return the body of the ``###`` section titled *heading*.

    Whitespace is collapsed to single spaces: these are wrapped Markdown
    paragraphs, so "recotem's own internal validation\\nsplit" is one phrase
    and a line-by-line or newline-sensitive match would miss it.
    """
    text = _DOC.read_text(encoding="utf-8")
    match = re.search(rf"^### {re.escape(heading)}\s*$", text, re.MULTILINE)
    assert match, (
        f"docs/operations.md has no '### {heading}' section -- this guard is "
        "watching nothing. Rename it here too if the section was renamed."
    )
    rest = text[match.end() :]
    nxt = re.search(r"^#{1,3} ", rest, re.MULTILINE)
    body = rest[: nxt.start()] if nxt else rest
    return " ".join(body.split())


def test_validation_advice_names_a_baseline_stronger_than_popularity() -> None:
    """Telling the reader to beat popularity, and stopping there, is the bug."""
    body = _section("Choosing a model on a small dataset")

    assert "popularity" in body.lower(), (
        "the validation advice no longer mentions popularity at all -- this "
        "guard is watching nothing."
    )
    assert re.search(r"\bk-?nn\b|nearest[- ]neighbour|item-item", body, re.I), (
        "docs/operations.md tells the operator to validate against a baseline "
        "but never names an item-item / kNN baseline. Popularity alone is not "
        "a bar: a model measured at 11.4x popularity still lost to a 30-line "
        "cosine kNN on the same holdout."
    )
    assert re.search(
        r"not the bar|not against popularity|beating popularity", body, re.I
    ), (
        "the advice names a kNN somewhere but no longer says that beating "
        "popularity is insufficient, which is the actual correction."
    )


def test_validation_advice_covers_the_degenerate_popularity_case() -> None:
    """A low bar and an absent one need different advice.

    The sibling guard above only requires the text to say popularity is
    insufficient. That wording still reads as "popularity is a weak but
    informative baseline", which is false on a catalogue that turns over:
    there popularity scores exactly zero and the comparison carries no
    information at all.
    """
    body = _section("Choosing a model on a small dataset")

    assert re.search(r"recency|turns over|perishable", body, re.I), (
        "the validation advice no longer covers recency-driven catalogues, "
        "where popularity scores ndcg@10 = 0.0000 and 'beat popularity' is "
        "vacuously true for any model that returns any items at all."
    )
    # A bare "zero" is deliberately NOT accepted: this section already
    # contains "zero diagonal" in the kNN recipe, and matching that would let
    # the consequence be deleted while the guard still passed.
    assert re.search(r"0\.0000|no bar at all|undefined margin", body, re.I), (
        "the advice mentions recency but no longer states the consequence -- "
        "that popularity's score is 0.0000 there, so a passing comparison "
        "carries no information."
    )


def test_best_score_section_says_what_the_number_is_not() -> None:
    """The section must scope the number, the search's use of it, and cold start."""
    body = _section("What `best_score` is, and is not")

    required = {
        "the internal split it is computed on": r"internal validation split|internal split",
        "that it is not an estimate of the operator's task": r"is not\b[^.]*estimate",
        "that the search maximises it": r"criterion that chooses|search maximises",
        "that it does not measure cold start": r"cold start at all|does not measure cold",
    }
    missing = [what for what, pat in required.items() if not re.search(pat, body, re.I)]
    assert not missing, (
        "the `best_score` section no longer states: "
        + "; ".join(missing)
        + ". Each is a property an operator cannot infer from the number "
        "itself, and each was measured rather than assumed."
    )


def test_cold_start_claim_points_at_the_module_that_backs_it() -> None:
    """The cold-start claim is checkable only if it names the objective."""
    body = _section("What `best_score` is, and is not")
    assert "training/evaluate.py" in body, (
        "the claim that the search never evaluates a cold-start request no "
        "longer names recotem/training/evaluate.py, which is where the "
        "objective is built and the only place a reader can check it."
    )
    assert (
        Path(__file__).resolve().parents[2]
        / "src"
        / "recotem"
        / "training"
        / "evaluate.py"
    ).is_file(), (
        "docs/operations.md points at recotem/training/evaluate.py for the "
        "cold-start claim and that module no longer exists."
    )
