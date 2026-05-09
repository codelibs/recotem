"""Unit tests for recotem.training.search helpers.

Tests:
- _compute_budgets: n_trials smaller than number of explicit-positive classes
- _compute_budgets: n_trials equal to number of classes
- _compute_budgets: proportional scale-down still works
- _make_storage: rejects URLs embedding userinfo
- _make_storage: accepts URLs without userinfo (connection errors allowed)
- _make_storage: bare file path converted to sqlite URL
- _make_storage: empty / whitespace returns None
- orphaned_count tracked in SearchResult
- excessive orphan trials raises TrainingError
- unknown algorithm in per_algorithm_trials raises TrainingError
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import numpy as np
import optuna
import pytest
import scipy.sparse as sps

from recotem.training.errors import SearchError, TrainingError
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


# ---------------------------------------------------------------------------
# E-3 (new). orphaned_count tracked in SearchResult
# ---------------------------------------------------------------------------


def test_orphan_trial_count_in_search_result() -> None:
    """SearchResult.orphaned_count is incremented for each per-trial-timeout orphan.

    Strategy: use a slow recommender (sleeps > per_trial_timeout_seconds) and
    inject 3 fake completed trials so the excessive-orphan guard (orphaned > n//2)
    does not fire for a single orphan (1 > 3//2 == 1 > 1 == False).

    After run_search returns, we assert orphaned_count >= 1.
    """
    from recotem.training.progress import ProgressReporter
    from recotem.training.search import run_search

    # A recommender that always sleeps past the per-trial timeout.
    class _SlowRecommender:
        learnt_config: dict = {}

        def __init__(self, X, **kwargs):
            pass

        @staticmethod
        def default_suggest_parameter(trial, space):
            return {}

        def learn_with_optimizer(self, evaluator, trial):
            time.sleep(2)  # always exceeds 1s timeout

        def learn(self):
            return self

    def _make_fake_trial(number: int) -> MagicMock:
        t = MagicMock(spec=optuna.trial.FrozenTrial)
        t.state = optuna.trial.TrialState.COMPLETE
        t.value = -0.5  # score = 0.5 after negation
        t.number = number
        t.params = {"recommender_class_name": "TopPopRecommender"}
        t.user_attrs = {"recommender_class_name": "TopPopRecommender"}
        return t

    # 3 completed fake trials → 1 orphan satisfies 1 > 3//2 == 1 > 1 == False
    fake_trials = [_make_fake_trial(i) for i in range(3)]
    best_fake_trial = fake_trials[0]

    with patch(
        "recotem.training.search.get_recommender_cls", return_value=_SlowRecommender
    ):
        with patch("recotem.training.search.optuna.create_study") as mock_study_fn:
            mock_study = MagicMock()

            def _optimize_with_one_orphan(objective, n_trials, **kwargs):
                """Fire objective once (producing one orphan) then expose 3 completed."""
                fake_t = MagicMock(spec=optuna.Trial)
                fake_t.number = 0
                fake_t.suggest_categorical.return_value = "TopPopRecommender"
                fake_t.set_user_attr = MagicMock()
                try:
                    objective(fake_t)
                except optuna.TrialPruned:
                    pass
                except Exception:  # noqa: BLE001
                    pass
                mock_study.trials = fake_trials
                mock_study.best_trial = best_fake_trial

            mock_study.trials = []
            mock_study.optimize = _optimize_with_one_orphan
            mock_study_fn.return_value = mock_study

            X = sps.csr_matrix(np.ones((5, 3)))
            evaluator = MagicMock()

            with ProgressReporter(
                n_trials=4, recipe_name="orphan_test", run_id="r1"
            ) as rep:
                result = run_search(
                    algorithms=["TopPopRecommender"],
                    X_tv_train=X,
                    evaluator=evaluator,
                    n_trials=4,
                    per_algorithm_trials=None,
                    per_trial_timeout_seconds=1,  # 1s timeout; _SlowRecommender sleeps 2s
                    timeout_seconds=None,
                    parallelism=1,
                    storage_path="",
                    random_seed=42,
                    reporter=rep,
                    recipe_name="orphan_test",
                    run_id="r1",
                )

    assert result.orphaned_count >= 1, (
        f"Expected orphaned_count >= 1, got {result.orphaned_count}"
    )


# ---------------------------------------------------------------------------
# E-3 (new). excessive orphan trials raises TrainingError
# ---------------------------------------------------------------------------


def test_excessive_orphan_trials_raises_training_error() -> None:
    """When orphaned_count > completed_count // 2, TrainingError with
    code='excessive_per_trial_timeouts' must be raised.
    """
    from recotem.training.progress import ProgressReporter
    from recotem.training.search import run_search

    class _SlowRecommender:
        learnt_config: dict = {}

        def __init__(self, X, **kwargs):
            pass

        @staticmethod
        def default_suggest_parameter(trial, space):
            return {}

        def learn_with_optimizer(self, evaluator, trial):
            time.sleep(2)  # always exceeds the 0.05 s timeout

        def learn(self):
            return self

    # One completed trial (score != 0) — orphaned_count > 1//2 == 0, so 1 orphan suffices.
    fake_trial = MagicMock(spec=optuna.trial.FrozenTrial)
    fake_trial.state = optuna.trial.TrialState.COMPLETE
    fake_trial.value = -0.5
    fake_trial.number = 99
    fake_trial.params = {"recommender_class_name": "TopPopRecommender"}
    fake_trial.user_attrs = {"recommender_class_name": "TopPopRecommender"}

    with patch(
        "recotem.training.search.get_recommender_cls", return_value=_SlowRecommender
    ):
        with patch("recotem.training.search.optuna.create_study") as mock_study_fn:
            mock_study = MagicMock()

            def _optimize_with_orphan(objective, n_trials, **kwargs):
                fake_t = MagicMock(spec=optuna.Trial)
                fake_t.number = 0
                fake_t.suggest_categorical.return_value = "TopPopRecommender"
                fake_t.set_user_attr = MagicMock()
                try:
                    objective(fake_t)
                except optuna.TrialPruned:
                    pass
                except Exception:  # noqa: BLE001
                    pass
                mock_study.trials = [fake_trial]
                mock_study.best_trial = fake_trial

            mock_study.trials = []
            mock_study.optimize = _optimize_with_orphan
            mock_study_fn.return_value = mock_study

            X = sps.csr_matrix(np.ones((5, 3)))
            evaluator = MagicMock()

            with ProgressReporter(
                n_trials=1, recipe_name="excessive_test", run_id="r3"
            ) as rep:
                with pytest.raises(TrainingError) as exc_info:
                    run_search(
                        algorithms=["TopPopRecommender"],
                        X_tv_train=X,
                        evaluator=evaluator,
                        n_trials=1,
                        per_algorithm_trials=None,
                        per_trial_timeout_seconds=1,
                        timeout_seconds=None,
                        parallelism=1,
                        storage_path="",
                        random_seed=42,
                        reporter=rep,
                        recipe_name="excessive_test",
                        run_id="r3",
                    )

    assert exc_info.value.code == "excessive_per_trial_timeouts", (
        f"Expected code='excessive_per_trial_timeouts', got {exc_info.value.code!r}"
    )


# ---------------------------------------------------------------------------
# E-4 (new). Unknown algorithm alias in per_algorithm_trials raises TrainingError
# ---------------------------------------------------------------------------


def test_unknown_algorithm_in_per_algorithm_trials_raises() -> None:
    """A typo in per_algorithm_trials (e.g. 'IALSS') must raise TrainingError
    with code='unknown_algorithm_in_budget' — not silently drop to zero budget.
    """
    with pytest.raises(TrainingError) as exc_info:
        _compute_budgets(
            class_names=["IALSRecommender", "TopPopRecommender"],
            n_trials=10,
            per_algorithm_trials={"IALSS": 7, "TopPop": 3},  # "IALSS" is a typo
        )
    assert exc_info.value.code == "unknown_algorithm_in_budget", (
        f"Expected code='unknown_algorithm_in_budget', got {exc_info.value.code!r}"
    )
    assert "IALSS" in str(exc_info.value), (
        f"Error message must name the bad alias; got: {exc_info.value!s}"
    )
