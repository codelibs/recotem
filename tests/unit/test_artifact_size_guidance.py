"""The artifact-sizing guidance must name both terms, not just `n_components`.

`docs/operations.md` told the reader that `n_components` is "the term that
dominates artifact size, since the factor matrices are
`(n_users + n_items) x n_components x 4` bytes".  That is one of two terms.
irspack's recommenders retain ``X_train_all`` -- the user-item CSR -- on the
trained object and define no ``__getstate__``, so every deduplicated
interaction row is pickled into the payload as well, at roughly 12 bytes.

Measured on four runs, the single-term reading spans 5.03 to 10.11 bytes per
``(n_users + n_items) x n_components`` entry -- a factor of two -- and on a
60,000-user / 10,000-item / 1.5M-row model with ``n_components: 44`` the
factor matrices were only 40% of the payload while the interaction matrix was
58%.  An operator sizing a host from ``best_params`` alone can therefore be
out by 2x in the direction that OOMKills a pod.

These tests pin the corrected claim.  Both section lookups assert they located
the heading before asserting anything about the body, so a rename cannot switch
them off silently.  Whitespace is collapsed first because these are wrapped
Markdown paragraphs and the phrases being matched span line breaks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOC = Path(__file__).resolve().parents[2] / "docs" / "operations.md"
_HEADING = "## Sizing `recotem serve` memory"


def _section() -> str:
    """Return the sizing section's body with whitespace collapsed.

    Raises if the heading is gone -- the guard must never assert against an
    empty string it silently failed to find.
    """
    text = _DOC.read_text(encoding="utf-8")
    assert _HEADING in text, (
        f"{_DOC.name} no longer contains {_HEADING!r}; this guard is watching "
        "nothing. Update _HEADING to the section's new name."
    )
    body = text.split(_HEADING, 1)[1]
    # stop at the next same-or-higher-level heading
    nxt = re.search(r"(?m)^## ", body)
    if nxt:
        body = body[: nxt.start()]
    assert len(body) > 500, "sizing section is suspiciously short; did it move?"
    return " ".join(body.split())


def _formula_block() -> str:
    """Return the fenced code block that states the payload estimate.

    Asserted separately from the prose: an earlier draft of these guards was
    satisfied by an unrelated later sentence that happened to say `n_rows`,
    so deleting the second term from the formula itself went undetected.
    """
    text = _DOC.read_text(encoding="utf-8")
    assert _HEADING in text, f"{_DOC.name} no longer contains {_HEADING!r}"
    body = text.split(_HEADING, 1)[1]
    nxt = re.search(r"(?m)^## ", body)
    if nxt:
        body = body[: nxt.start()]
    blocks = re.findall(r"(?ms)^```\n(.*?)^```", body)
    matching = [b for b in blocks if "payload" in b and "n_components" in b]
    assert matching, (
        "no fenced block in the sizing section states a payload estimate in "
        "terms of n_components; this guard is watching nothing"
    )
    return matching[0]


def test_the_payload_formula_states_both_terms() -> None:
    """The formula block itself must carry the interaction-matrix term."""
    block = _formula_block()
    assert "n_users" in block and "n_components" in block, (
        "the payload formula lost its factor-matrix term"
    )
    assert "n_rows" in block, (
        "the payload formula states only the factor-matrix term. The pickled "
        "interaction matrix is the other term and was 58% of the payload on a "
        "measured 60,000-user / 10,000-item / 1.5M-row model."
    )


def test_sizing_section_names_the_interaction_matrix_term() -> None:
    """Not just the factor matrices: `n_rows` is the other term."""
    body = _section()
    assert "n_rows" in body, (
        "the sizing section never mentions n_rows, so it still presents "
        "artifact size as a function of n_components alone"
    )
    assert "X_train_all" in body, (
        "the sizing section does not name X_train_all, the irspack attribute "
        "that makes the interaction matrix part of the payload"
    )


def test_sizing_section_says_a_small_n_components_is_not_a_guarantee() -> None:
    """The actionable half: you cannot size from `best_params` alone."""
    body = _section()
    assert "not** a guarantee" in body or "not a guarantee" in body, (
        "the sizing section does not warn that a small n_components fails to "
        "guarantee a small artifact"
    )


# irspack's package import pulls in LightFM, whose no-OpenMP UserWarning the
# suite-wide `filterwarnings = ["error"]` would otherwise fail the test on.
# `recotem._lightfm_compat` silences it at import time, but pytest reinstates
# its own filters per test, so scope the ignore here.
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_the_named_irspack_attribute_still_exists() -> None:
    """`X_train_all` must be real, or the claim above points at nothing."""
    from irspack.recommenders.base import BaseRecommender

    assert "X_train_all" in BaseRecommender.__annotations__, (
        "irspack's BaseRecommender no longer declares X_train_all; the sizing "
        "section's mechanism claim needs re-verifying against this version"
    )
