"""Tests for MAJOR-5: parallelism > 1 + per_algorithm_trials warning.

Tests:
- parallelism > 1 and per_algorithm_trials set emits per_algorithm_trials_budget_race warning
- parallelism == 1 does NOT emit the warning
- parallelism > 1 without per_algorithm_trials does NOT emit the warning
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import optuna
import scipy.sparse as sps


def _make_recipe_with_parallelism(tmp_path, *, parallelism: int, per_algo: dict | None):
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        TrainingConfig,
    )

    csv_file = tmp_path / "par_data.csv"
    csv_file.write_text("user_id,item_id\nu1,i1\nu2,i2\n")

    return Recipe(
        name="parallel_recipe",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=["TopPop", "IALS"],
            n_trials=4,
            parallelism=parallelism,
            per_algorithm_trials=per_algo,
        ),
        output=OutputConfig(path=str(tmp_path / "parallel_recipe.recotem")),
    )


def _call_run_search(
    *, parallelism: int, per_algorithm_trials: dict | None
) -> MagicMock:
    """Call run_search with spied logger and return the logger spy."""
    import recotem.training.search as search_mod
    from recotem.training.progress import ProgressReporter
    from recotem.training.search import run_search

    n = 5
    X = sps.csr_matrix(np.ones((n, n)))
    mock_evaluator = MagicMock()

    spy_logger = MagicMock()
    original_logger = search_mod.logger

    # Create a minimal study where best_trial has value != 0.0
    study = optuna.create_study(direction="minimize")

    def _objective(trial: optuna.Trial) -> float:
        trial.set_user_attr("recommender_class_name", "TopPopRecommender")
        return -0.5

    study.optimize(_objective, n_trials=1)

    try:
        search_mod.logger = spy_logger

        reporter = ProgressReporter(
            n_trials=2,
            recipe_name="par_test",
            run_id="r0",
            quiet=True,
            verbose=False,
        )

        with (
            patch("recotem.training.search.optuna.create_study", return_value=study),
            patch("recotem.training.search._make_storage", return_value=None),
            patch(
                "recotem.training.search.resolve_algorithm_name",
                side_effect=lambda x: x,
            ),
        ):
            try:
                with reporter:
                    run_search(
                        algorithms=["TopPopRecommender"],
                        X_tv_train=X,
                        evaluator=mock_evaluator,
                        n_trials=2,
                        per_algorithm_trials=per_algorithm_trials,
                        per_trial_timeout_seconds=None,
                        timeout_seconds=None,
                        parallelism=parallelism,
                        storage_path="",
                        random_seed=42,
                        reporter=reporter,
                        recipe_name="par_test",
                        run_id="r0",
                        metric="ndcg",
                    )
            except Exception:
                pass  # We only care about the warning log
    finally:
        search_mod.logger = original_logger

    return spy_logger


# ---------------------------------------------------------------------------
# T5-1: parallelism > 1 + per_algorithm_trials emits warning
# ---------------------------------------------------------------------------


def test_parallelism_gt1_with_per_algo_emits_warning() -> None:
    """When parallelism > 1 and per_algorithm_trials is set, a
    per_algorithm_trials_budget_race warning must be logged exactly once.
    """
    spy_logger = _call_run_search(
        parallelism=2,
        per_algorithm_trials={"TopPopRecommender": 2},
    )
    warning_calls = [
        call
        for call in spy_logger.warning.call_args_list
        if call.args and call.args[0] == "per_algorithm_trials_budget_race"
    ]
    assert warning_calls, (
        "per_algorithm_trials_budget_race warning must be emitted when "
        f"parallelism > 1 and per_algorithm_trials is set. "
        f"All warning calls: {spy_logger.warning.call_args_list}"
    )


# ---------------------------------------------------------------------------
# T5-2: parallelism == 1 does NOT emit the warning
# ---------------------------------------------------------------------------


def test_parallelism_eq1_no_warning() -> None:
    """When parallelism == 1, no per_algorithm_trials_budget_race warning."""
    spy_logger = _call_run_search(
        parallelism=1,
        per_algorithm_trials={"TopPopRecommender": 2},
    )
    warning_calls = [
        call
        for call in spy_logger.warning.call_args_list
        if call.args and call.args[0] == "per_algorithm_trials_budget_race"
    ]
    assert not warning_calls, (
        f"per_algorithm_trials_budget_race must NOT be emitted when parallelism==1, "
        f"got: {warning_calls}"
    )


# ---------------------------------------------------------------------------
# T5-3: parallelism > 1 without per_algorithm_trials does NOT emit the warning
# ---------------------------------------------------------------------------


def test_parallelism_gt1_no_per_algo_no_warning() -> None:
    """When parallelism > 1 but per_algorithm_trials is None, no warning."""
    spy_logger = _call_run_search(
        parallelism=2,
        per_algorithm_trials=None,
    )
    warning_calls = [
        call
        for call in spy_logger.warning.call_args_list
        if call.args and call.args[0] == "per_algorithm_trials_budget_race"
    ]
    assert not warning_calls, (
        f"per_algorithm_trials_budget_race must NOT be emitted when "
        f"per_algorithm_trials is None, got: {warning_calls}"
    )
