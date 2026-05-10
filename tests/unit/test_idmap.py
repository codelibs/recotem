"""Unit tests for recotem._idmap.IDMappedRecommender.

Covers:
- Fix 4: unknown user_id raises KeyError without calling underlying recommender.
- Fix 4: known user_id that causes RuntimeError in the underlying recommender
  propagates as RuntimeError (not masked to KeyError).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_idmapped(user_ids: list[str], item_ids: list[str]) -> object:
    """Build an IDMappedRecommender with a real IDMapper but a mock recommender."""
    from recotem._idmap import IDMappedRecommender

    mock_rec = MagicMock()
    return IDMappedRecommender(mock_rec, user_ids, item_ids)


# ---------------------------------------------------------------------------
# Unknown user raises KeyError — does NOT call underlying recommender
# ---------------------------------------------------------------------------


def test_unknown_user_raises_key_error() -> None:
    """get_recommendation_for_known_user_id must raise KeyError for an
    unknown user_id without invoking the underlying recommender."""
    idmapped = _make_idmapped(["u1", "u2"], ["i1", "i2"])

    with pytest.raises(KeyError) as exc_info:
        idmapped.get_recommendation_for_known_user_id("unknown_user", cutoff=5)

    assert str(exc_info.value) == "'unknown_user'", (
        f"KeyError must contain the user_id; got {exc_info.value!r}"
    )
    # Confirm recommender was never called
    idmapped.recommender.assert_not_called()  # type: ignore[attr-defined]


def test_unknown_user_key_error_not_called_for_any_unknown_variant() -> None:
    """Confirm the pre-check fires for various unknown user strings."""
    idmapped = _make_idmapped(["alice", "bob"], ["item1"])

    for uid in ("", "charlie", "Alice", " alice", "bob ", "ALICE"):
        with pytest.raises(KeyError):
            idmapped.get_recommendation_for_known_user_id(uid, cutoff=1)


# ---------------------------------------------------------------------------
# Known user with internal RuntimeError propagates (NOT masked to KeyError)
# ---------------------------------------------------------------------------


def test_known_user_internal_runtime_error_propagates() -> None:
    """When the underlying recommender raises RuntimeError for a KNOWN user_id,
    the error must propagate as RuntimeError — not be swallowed into KeyError.

    This ensures that genuine internal failures (e.g. numpy/scipy errors) are
    surfaced as 500 errors rather than silently becoming 404 responses.
    """
    from unittest.mock import patch

    from recotem._idmap import IDMappedRecommender

    mock_rec = MagicMock()
    idmapped = IDMappedRecommender(mock_rec, ["u1"], ["i1"])

    # Patch the mapper's recommend_for_known_user_id to raise RuntimeError
    with patch.object(
        idmapped._mapper,
        "recommend_for_known_user_id",
        side_effect=RuntimeError("internal scipy error"),
    ):
        with pytest.raises(RuntimeError, match="internal scipy error"):
            idmapped.get_recommendation_for_known_user_id("u1", cutoff=5)


def test_known_user_internal_runtime_error_is_not_key_error() -> None:
    """Double-check that the RuntimeError is not wrapped in a KeyError."""
    from unittest.mock import patch

    from recotem._idmap import IDMappedRecommender

    mock_rec = MagicMock()
    idmapped = IDMappedRecommender(mock_rec, ["u1"], ["i1"])

    with patch.object(
        idmapped._mapper,
        "recommend_for_known_user_id",
        side_effect=RuntimeError("matrix dimension mismatch"),
    ):
        try:
            idmapped.get_recommendation_for_known_user_id("u1")
            pytest.fail("Expected RuntimeError was not raised")
        except KeyError:
            pytest.fail("RuntimeError must not be caught and re-raised as KeyError")
        except RuntimeError:
            pass  # correct: propagates unchanged
