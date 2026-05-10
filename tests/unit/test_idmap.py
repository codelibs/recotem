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


# ---------------------------------------------------------------------------
# M-4 (IPython): _ipython_stub.install() idempotency scenarios
# ---------------------------------------------------------------------------


def test_ipython_stub_installs_both_when_neither_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When neither 'IPython' nor 'IPython.display' are in sys.modules,
    install() must add both."""
    import sys

    from recotem._ipython_stub import install

    monkeypatch.delitem(sys.modules, "IPython", raising=False)
    monkeypatch.delitem(sys.modules, "IPython.display", raising=False)

    install()

    assert "IPython" in sys.modules, "install() must add 'IPython' to sys.modules"
    assert "IPython.display" in sys.modules, (
        "install() must add 'IPython.display' to sys.modules"
    )
    assert callable(sys.modules["IPython.display"].display), (
        "IPython.display.display must be callable"
    )


def test_ipython_stub_installs_display_when_ipython_present_but_display_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When 'IPython' is already in sys.modules but 'IPython.display' is not,
    install() must add 'IPython.display' WITHOUT replacing the real 'IPython'."""
    import sys
    import types

    from recotem._ipython_stub import install

    # Simulate partial real-IPython: IPython present but IPython.display absent.
    real_ipython_stub = types.ModuleType("IPython")
    real_ipython_stub.__version__ = "7.0.0"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "IPython", real_ipython_stub)
    monkeypatch.delitem(sys.modules, "IPython.display", raising=False)

    install()

    # IPython must NOT be replaced -- we keep the one already in sys.modules.
    assert sys.modules["IPython"] is real_ipython_stub, (
        "install() must not replace an already-present 'IPython' module"
    )
    # IPython.display must now be present.
    assert "IPython.display" in sys.modules, (
        "install() must add 'IPython.display' when it is absent even if 'IPython' exists"
    )
    assert callable(sys.modules["IPython.display"].display), (
        "The installed IPython.display.display must be callable"
    )


def test_ipython_stub_noop_when_both_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both 'IPython' and 'IPython.display' are already in sys.modules,
    install() must be a no-op -- it must not replace either."""
    import sys
    import types

    from recotem._ipython_stub import install

    existing_ipython = types.ModuleType("IPython")
    existing_display = types.ModuleType("IPython.display")
    monkeypatch.setitem(sys.modules, "IPython", existing_ipython)
    monkeypatch.setitem(sys.modules, "IPython.display", existing_display)

    install()

    assert sys.modules["IPython"] is existing_ipython, (
        "install() must not replace an already-present 'IPython' module"
    )
    assert sys.modules["IPython.display"] is existing_display, (
        "install() must not replace an already-present 'IPython.display' module"
    )
