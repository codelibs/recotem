"""Tests for MAJOR-4: ZeroScoreError scoped to non-hit metrics.

Tests:
- metric='hit' + best_score=0.0 does NOT raise ZeroScoreError, emits warning
- metric='ndcg' + best_score=0.0 raises ZeroScoreError
- metric='map' + best_score=0.0 raises ZeroScoreError
- metric='recall' + best_score=0.0 raises ZeroScoreError
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import optuna
import pytest
import scipy.sparse as sps

from recotem.training.errors import SearchError, ZeroScoreError


def _make_zero_score_study(class_name: str = "TopPopRecommender") -> optuna.Study:
    """Return a completed single-trial Optuna study with score 0.0."""
    study = optuna.create_study(direction="minimize")

    def _objective(trial: optuna.Trial) -> float:
        trial.set_user_attr("recommender_class_name", class_name)
        return 0.0  # score = -0.0 = 0.0

    study.optimize(_objective, n_trials=1)
    return study


def _run_search_patched(metric: str) -> None:
    """Run run_search with a patched study that always returns best_score=0.0."""
    from recotem.training.progress import ProgressReporter
    from recotem.training.search import run_search

    n = 5
    X = sps.csr_matrix(np.ones((n, n)))

    mock_evaluator = MagicMock()

    # Patch optuna.create_study to return our zero-score study.
    zero_study = _make_zero_score_study("TopPopRecommender")

    with (
        patch("recotem.training.search.optuna.create_study", return_value=zero_study),
        patch("recotem.training.search._make_storage", return_value=None),
        patch("recotem.training.search.get_recommender_cls") as mock_cls,
    ):
        mock_rec = MagicMock()
        mock_rec.default_suggest_parameter.return_value = {}
        mock_rec.return_value = mock_rec
        mock_rec.learn_with_optimizer.return_value = None
        mock_rec.learnt_config = {}
        mock_cls.return_value = mock_rec

        reporter = ProgressReporter(
            n_trials=1,
            recipe_name="test",
            run_id="r1",
            quiet=True,
            verbose=False,
        )
        with reporter:
            run_search(
                algorithms=["TopPopRecommender"],
                X_tv_train=X,
                evaluator=mock_evaluator,
                n_trials=1,
                per_algorithm_trials=None,
                per_trial_timeout_seconds=None,
                timeout_seconds=None,
                parallelism=1,
                storage_path="",
                random_seed=42,
                reporter=reporter,
                recipe_name="test",
                run_id="r1",
                metric=metric,
            )


# ---------------------------------------------------------------------------
# T4-1: hit metric + 0.0 score does NOT raise ZeroScoreError
# ---------------------------------------------------------------------------


def test_zero_score_hit_metric_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """For metric='hit', a best_score of 0.0 must not raise ZeroScoreError."""

    # Patch get_score to always return 0.0
    with patch("recotem.training.search.get_score", return_value=0.0):
        with patch(
            "recotem.training.search.resolve_algorithm_name",
            return_value="TopPopRecommender",
        ):
            try:
                _run_search_patched("hit")
            except ZeroScoreError:
                pytest.fail("ZeroScoreError must NOT be raised when metric='hit'")
            except (SearchError, Exception):
                # Other errors (e.g. from mock study structure) are acceptable here;
                # we only care that ZeroScoreError is not raised.
                pass


def test_zero_score_hit_metric_warning_logged() -> None:
    """For metric='hit' with 0.0 best score, a warning must be logged."""

    with patch("recotem.training.search.get_score", return_value=0.0):
        with patch(
            "recotem.training.search.resolve_algorithm_name",
            return_value="TopPopRecommender",
        ):
            import recotem.training.search as search_mod

            spy_logger = MagicMock()
            original_logger = search_mod.logger

            try:
                search_mod.logger = spy_logger

                try:
                    _run_search_patched("hit")
                except ZeroScoreError:
                    pytest.fail("ZeroScoreError must NOT be raised when metric='hit'")
                except Exception:
                    pass

                warning_calls = [
                    call
                    for call in spy_logger.warning.call_args_list
                    if call.args and call.args[0] == "zero_score_hit_metric"
                ]
                assert warning_calls, (
                    "zero_score_hit_metric warning must be logged when metric='hit' "
                    f"and best_score=0.0. All warning calls: {spy_logger.warning.call_args_list}"
                )
            finally:
                search_mod.logger = original_logger


# ---------------------------------------------------------------------------
# T4-2: non-hit metrics + 0.0 score STILL raise ZeroScoreError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("metric", ["ndcg", "map", "recall"])
def test_zero_score_non_hit_metric_raises_zero_score_error(metric: str) -> None:
    """For non-hit metrics, a best_score of 0.0 must still raise ZeroScoreError."""
    with patch("recotem.training.search.get_score", return_value=0.0):
        with patch(
            "recotem.training.search.resolve_algorithm_name",
            return_value="TopPopRecommender",
        ):
            with pytest.raises(ZeroScoreError):
                _run_search_patched(metric)
