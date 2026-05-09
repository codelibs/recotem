"""Unit tests for recotem.training.search helpers.

Tests:
- _compute_budgets: n_trials smaller than number of explicit-positive classes
- _compute_budgets: n_trials equal to number of classes
- _compute_budgets: proportional scale-down still works
- _make_storage: rejects URLs embedding userinfo
- _make_storage: accepts URLs without userinfo (connection errors allowed)
- _make_storage: bare file path converted to sqlite URL
- _make_storage: empty / whitespace returns None
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from recotem.training.errors import SearchError
from recotem.training.search import _compute_budgets, _make_storage

# ---------------------------------------------------------------------------
# B1. n_trials smaller than number of explicit-positive classes
# ---------------------------------------------------------------------------


def test_compute_budgets_n_trials_smaller_than_explicit_classes() -> None:
    """When n_trials < number of explicit-positive classes, the first n_trials
    classes each get 1 trial, the rest get 0.  Total must equal n_trials.
    """
    budgets = _compute_budgets(
        class_names=["IALSRecommender", "RP3betaRecommender", "TopPopRecommender"],
        n_trials=2,
        per_algorithm_trials={
            "IALSRecommender": 5,
            "RP3betaRecommender": 5,
            "TopPopRecommender": 5,
        },
    )
    total = sum(budgets.values())
    assert total == 2, f"sum of budgets must equal n_trials=2, got {total}"
    # First 2 classes should have budget 1, last should have 0
    nonzero = [c for c, v in budgets.items() if v > 0]
    zero = [c for c, v in budgets.items() if v == 0]
    assert len(nonzero) == 2, f"exactly 2 classes should have budget >0, got {nonzero}"
    assert len(zero) == 1, f"exactly 1 class should have budget 0, got {zero}"
    for v in nonzero:
        assert budgets[v] == 1


# ---------------------------------------------------------------------------
# B2. n_trials equal to number of classes
# ---------------------------------------------------------------------------


def test_compute_budgets_n_trials_equal_to_classes() -> None:
    """When n_trials equals the number of classes, each class gets exactly 1."""
    budgets = _compute_budgets(
        class_names=["A", "B", "C"],
        n_trials=3,
        per_algorithm_trials={"A": 5, "B": 5, "C": 5},
    )
    assert sum(budgets.values()) == 3
    assert budgets["A"] == 1
    assert budgets["B"] == 1
    assert budgets["C"] == 1


# ---------------------------------------------------------------------------
# B3. Proportional scale-down still works (n_trials >= number of classes)
# ---------------------------------------------------------------------------


def test_compute_budgets_proportional_scaledown() -> None:
    """With n_trials=10 and 4 classes each requesting 5 trials (sum=20 > 10),
    proportional scale-down should yield a total of exactly 10.
    """
    budgets = _compute_budgets(
        class_names=["A", "B", "C", "D"],
        n_trials=10,
        per_algorithm_trials={"A": 5, "B": 5, "C": 5, "D": 5},
    )
    total = sum(budgets.values())
    assert total == 10, f"proportional scale-down must sum to n_trials=10, got {total}"
    # Every class should have at least 1 trial (since n_trials >= num classes)
    for c, v in budgets.items():
        assert v >= 1, f"class {c} should have at least 1 trial after scale-down"


# ---------------------------------------------------------------------------
# B4. _make_storage rejects URLs with embedded userinfo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:pass@db.internal/optuna",
        "postgres://admin:secret@localhost:5432/mydb",
        "mysql://root:password@db.example.com/optuna",
    ],
)
def test_make_storage_rejects_url_with_userinfo(url: str) -> None:
    """URLs embedding user:pass must be rejected with SearchError."""
    with pytest.raises(SearchError, match="must not embed credentials"):
        _make_storage(url)


# ---------------------------------------------------------------------------
# B5. _make_storage accepts URLs without userinfo
# ---------------------------------------------------------------------------


def test_make_storage_accepts_url_without_userinfo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PostgreSQL URL without credentials should not raise SearchError.

    The RDBStorage constructor will try to connect and will fail, but that is
    an acceptable connection error — not a SearchError about credentials.
    """
    stub_storage = MagicMock()
    with patch(
        "recotem.training.search.optuna.storages.RDBStorage",
        return_value=stub_storage,
    ):
        result = _make_storage("postgresql://db.internal/optuna")
    assert result is stub_storage


# ---------------------------------------------------------------------------
# B6. _make_storage converts bare file path to SQLite URL
# ---------------------------------------------------------------------------


def test_make_storage_sqlite_path_to_url() -> None:
    """A bare file path (no scheme) is converted to a sqlite:/// URL."""
    with patch("recotem.training.search.optuna.storages.RDBStorage") as mock_rdb:
        mock_rdb.return_value = MagicMock()
        _make_storage("/tmp/optuna.db")
    # RDBStorage must have been called with a sqlite URL
    assert mock_rdb.called
    call_url = mock_rdb.call_args[0][0]
    assert call_url.startswith("sqlite:///"), (
        f"bare path should become sqlite:/// URL, got {call_url!r}"
    )


# ---------------------------------------------------------------------------
# B7. _make_storage empty / whitespace returns None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["", "  ", "\t"])
def test_make_storage_empty_returns_none(path: str) -> None:
    """Empty or whitespace-only storage_path means in-memory — returns None."""
    assert _make_storage(path) is None
