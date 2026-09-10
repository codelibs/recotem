"""Control characters must not reach the operator's terminal from recipe or header data.

``recotem._log_safe.escape_control_chars`` existed but was wired only to the
serving-side request path.  Three other values reach a terminal unescaped, and
the mutation matrix that found them could not have: deleting each call site left
the whole suite green because nothing asserted on the bytes.

The recipe-path case needs no key of any kind -- authoring a recipe is enough --
which is why it is pinned here rather than left to the serving tests.
"""

from __future__ import annotations

from recotem._log_safe import escape_control_chars
from recotem.datasource.csv import _log_safe_path

# ESC + a colour SGR, an OSC window-title sequence, and a BEL terminator: the
# shapes that repaint or retitle a terminal rather than merely look odd.
_HOSTILE = "./ev\x1b[31mIL\x1b[0m\x07\x1b]0;TITLE\x07.csv"


def test_recipe_path_reaches_logs_and_errors_without_control_characters() -> None:
    out = _log_safe_path(_HOSTILE)
    assert "\x1b" not in out, (
        f"raw ESC survived into an operator-facing string: {out!r}"
    )
    assert "\x07" not in out, (
        f"raw BEL survived into an operator-facing string: {out!r}"
    )
    # Escaped, not dropped: the operator still gets to see what was in the recipe.
    assert "\\x1b" in out and "\\x07" in out


def test_a_benign_path_is_returned_unchanged() -> None:
    """Negative control, so the test above cannot pass by mangling everything."""
    assert _log_safe_path("./data/interactions.csv") == "./data/interactions.csv"
    assert _log_safe_path("s3://bucket/key.csv") == "s3://bucket/key.csv"


def test_credential_removal_still_happens_alongside_the_escaping() -> None:
    """The composition must not have dropped the redaction half."""
    out = _log_safe_path("https://u:p@host/a.csv")
    assert "u:p@" not in out
    assert out == "https://host/a.csv"


def test_escape_control_chars_leaves_ordinary_text_alone() -> None:
    """Pins the escape as targeted at control characters, not at 'unusual' text."""
    assert escape_control_chars("ふつうの日本語 text") == "ふつうの日本語 text"
    assert escape_control_chars("") == ""
