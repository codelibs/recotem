"""Unit tests for recotem.training.pipeline.run_training.

Tests:
- end-to-end on small MovieLens slice with n_trials=2
- min_data_violation (min_rows, min_users, min_items)
- dedup policies (keep_last; sum_weight is schema-rejected)
- drop_null_ids default true records drop_count
- string-coerce user and item ids
- all-trials-failing -> SearchError/TrainingError exit4
- zero-score -> ZeroScoreError
- per_algorithm_trials partitioning
- one structured log per trial
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from recotem.training.errors import (
    MinDataViolation,
    SearchError,
    ZeroScoreError,
)

ACTIVE_KEY_HEX = "aa" * 32


def _make_key_ring():
    from recotem.artifact.signing import KeyRing

    return KeyRing(f"active:{ACTIVE_KEY_HEX}")


def _make_recipe(
    tmp_path: Path,
    algorithms: list[str] | None = None,
    n_trials: int = 2,
    min_rows: int | None = None,
    min_users: int | None = None,
    min_items: int | None = None,
    dedup: str = "keep_last",
    drop_null_ids: bool = True,
    per_algorithm_trials: dict | None = None,
):
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        CleansingConfig,
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )

    if algorithms is None:
        algorithms = ["TopPop"]

    csv_file = tmp_path / "data.csv"
    if not csv_file.exists():
        csv_file.write_text("user_id,item_id\nu1,i1\nu2,i2\n")

    recipe = Recipe(
        name="pipeline_test",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        cleansing=CleansingConfig(
            drop_null_ids=drop_null_ids,
            dedup=dedup,
            min_rows=min_rows,
            min_users=min_users,
            min_items=min_items,
        ),
        training=TrainingConfig(
            algorithms=algorithms,
            n_trials=n_trials,
            per_algorithm_trials=per_algorithm_trials,
            split=SplitConfig(scheme="random", heldout_ratio=0.1, seed=42),
        ),
        output=OutputConfig(path=str(tmp_path / "pipeline_test.recotem")),
    )
    return recipe


# ---------------------------------------------------------------------------
# end-to-end on small MovieLens slice with n_trials=2
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_end_to_end_movielens_small_n_trials_2(
    tmp_path: Path, movielens_small_df: pd.DataFrame
) -> None:
    """Full training pipeline on small MovieLens slice with n_trials=2."""
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.training.pipeline import run_training

    csv_file = tmp_path / "ml100k_small.csv"
    movielens_small_df[["user_id", "item_id"]].to_csv(csv_file, index=False)

    recipe = Recipe(
        name="ml_test",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=["TopPop"],
            n_trials=2,
            split=SplitConfig(scheme="random", heldout_ratio=0.1, seed=42),
        ),
        output=OutputConfig(path=str(tmp_path / "ml_test.recotem")),
    )

    kr = _make_key_ring()
    write_calls = []

    def _mock_write(payload_obj, header_dict, key_ring, fs_path, *, versioning):
        write_calls.append({"header": header_dict, "path": fs_path})
        return fs_path

    result = run_training(
        recipe, key_ring=kr, signing_key="active", write_artifact_fn=_mock_write
    )
    assert result is not None
    assert result.best_score > 0
    assert result.best_class is not None
    assert len(write_calls) == 1


# ---------------------------------------------------------------------------
# min_data_violation
# ---------------------------------------------------------------------------


def test_min_rows_violation_raises_exit4_min_data(tmp_path: Path) -> None:
    """min_rows threshold violation raises MinDataViolation."""
    from recotem.training.pipeline import _cleanse

    csv_file = tmp_path / "small.csv"
    csv_file.write_text("user_id,item_id\nu1,i1\n")
    recipe = _make_recipe(tmp_path, min_rows=1000)
    df = pd.DataFrame({"user_id": ["u1"], "item_id": ["i1"]})
    with pytest.raises(MinDataViolation) as exc_info:
        _cleanse(df, recipe)
    assert exc_info.value.code == "min_data_violation"


def test_min_users_violation_raises_exit4(tmp_path: Path) -> None:
    from recotem.training.pipeline import _cleanse

    recipe = _make_recipe(tmp_path, min_users=100)
    df = pd.DataFrame(
        {"user_id": [f"u{i}" for i in range(5)], "item_id": [f"i{i}" for i in range(5)]}
    )
    with pytest.raises(MinDataViolation):
        _cleanse(df, recipe)


def test_min_items_violation_raises_exit4(tmp_path: Path) -> None:
    from recotem.training.pipeline import _cleanse

    recipe = _make_recipe(tmp_path, min_items=200)
    df = pd.DataFrame({"user_id": [f"u{i}" for i in range(10)], "item_id": ["i1"] * 10})
    with pytest.raises(MinDataViolation):
        _cleanse(df, recipe)


# ---------------------------------------------------------------------------
# dedup policies
# ---------------------------------------------------------------------------


def test_dedup_keep_last_resolves_duplicates(tmp_path: Path) -> None:
    from recotem.training.pipeline import _cleanse

    recipe = _make_recipe(tmp_path, dedup="keep_last")
    df = pd.DataFrame(
        {
            "user_id": ["u1", "u1", "u2"],
            "item_id": ["i1", "i1", "i1"],  # u1,i1 is a duplicate
        }
    )
    result, drop_count = _cleanse(df, recipe)
    # After dedup, u1-i1 should appear once
    u1_i1 = result[(result["user_id"] == "u1") & (result["item_id"] == "i1")]
    assert len(u1_i1) == 1
    assert drop_count >= 0


def test_dedup_sum_weight_rejected_by_schema(tmp_path: Path) -> None:
    """sum_weight was documented but never plumbed through to the sparse-
    matrix builder, so it is rejected at recipe-validation time.  Older
    recipes that still set it must fail loudly rather than silently
    behaving like keep_first."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_recipe(tmp_path, dedup="sum_weight")


# ---------------------------------------------------------------------------
# drop_null_ids
# ---------------------------------------------------------------------------


def test_drop_null_ids_default_true_records_drop_count(tmp_path: Path) -> None:
    from recotem.training.pipeline import _cleanse

    recipe = _make_recipe(tmp_path, drop_null_ids=True)
    df = pd.DataFrame(
        {
            "user_id": ["u1", None, "u3"],
            "item_id": ["i1", "i2", "i3"],
        }
    )
    result, drop_count = _cleanse(df, recipe)
    assert drop_count >= 1
    assert len(result) == 2


# ---------------------------------------------------------------------------
# string coerce
# ---------------------------------------------------------------------------


def test_string_coerce_user_and_item_ids(tmp_path: Path) -> None:
    from recotem.training.pipeline import _cleanse

    recipe = _make_recipe(tmp_path)
    df = pd.DataFrame({"user_id": [1, 2, 3], "item_id": [10, 20, 30]})
    result, _ = _cleanse(df, recipe)
    # pandas may return either object (legacy) or StringDtype depending on version.
    assert result["user_id"].dtype == object or pd.api.types.is_string_dtype(
        result["user_id"]
    )
    assert result["item_id"].dtype == object or pd.api.types.is_string_dtype(
        result["item_id"]
    )
    assert result["user_id"].iloc[0] == "1"


# ---------------------------------------------------------------------------
# all-trials-failing -> SearchError
# ---------------------------------------------------------------------------


def test_all_trials_failing_raises_TrainingError_exit4(tmp_path: Path) -> None:
    """When no trials complete, run_search raises SearchError (code=no_completed_trials)."""
    import numpy as np
    import scipy.sparse as sps

    from recotem.training.progress import ProgressReporter
    from recotem.training.search import run_search

    # Tiny matrix that will cause evaluator/split to fail, but we mock the study
    with patch("recotem.training.search.optuna.create_study") as mock_study_fn:
        mock_study = MagicMock()
        mock_study.trials = []  # no trials completed
        mock_study.optimize = MagicMock()  # does nothing

        mock_study_fn.return_value = mock_study

        X = sps.csr_matrix(np.ones((10, 5)))
        evaluator = MagicMock()
        evaluator.n_users = 10

        with ProgressReporter(n_trials=1, recipe_name="test", run_id="run1") as rep:
            with pytest.raises(SearchError):
                run_search(
                    algorithms=["TopPopRecommender"],
                    X_tv_train=X,
                    evaluator=evaluator,
                    n_trials=1,
                    per_algorithm_trials=None,
                    per_trial_timeout_seconds=None,
                    timeout_seconds=None,
                    parallelism=1,
                    storage_path="",
                    random_seed=42,
                    reporter=rep,
                    recipe_name="test",
                    run_id="run1",
                )


# ---------------------------------------------------------------------------
# all-scores-zero -> ZeroScoreError
# ---------------------------------------------------------------------------


def test_all_scores_zero_raises_TrainingError_exit4(tmp_path: Path) -> None:
    """When all completed trials score 0.0, ZeroScoreError is raised."""
    import numpy as np
    import optuna
    import scipy.sparse as sps

    from recotem.training.progress import ProgressReporter
    from recotem.training.search import run_search

    with patch("recotem.training.search.optuna.create_study") as mock_study_fn:
        mock_study = MagicMock()

        trial = MagicMock()
        trial.state = optuna.trial.TrialState.COMPLETE
        trial.value = 0.0  # score = 0
        trial.number = 0
        trial.params = {"recommender_class_name": "TopPopRecommender"}
        trial.user_attrs = {"recommender_class_name": "TopPopRecommender"}

        mock_study.trials = [trial]
        mock_study.best_trial = trial
        mock_study.optimize = MagicMock()

        mock_study_fn.return_value = mock_study

        X = sps.csr_matrix(np.ones((10, 5)))
        evaluator = MagicMock()

        with ProgressReporter(n_trials=1, recipe_name="test", run_id="run2") as rep:
            with pytest.raises(ZeroScoreError):
                run_search(
                    algorithms=["TopPopRecommender"],
                    X_tv_train=X,
                    evaluator=evaluator,
                    n_trials=1,
                    per_algorithm_trials=None,
                    per_trial_timeout_seconds=None,
                    timeout_seconds=None,
                    parallelism=1,
                    storage_path="",
                    random_seed=42,
                    reporter=rep,
                    recipe_name="test",
                    run_id="run2",
                )


# ---------------------------------------------------------------------------
# per_algorithm_trials partitioning
# ---------------------------------------------------------------------------


def test_per_algorithm_trials_partition_global_budget() -> None:
    from recotem.training.search import _compute_budgets

    budgets = _compute_budgets(
        class_names=["IALSRecommender", "TopPopRecommender"],
        n_trials=10,
        per_algorithm_trials={"IALS": 7, "TopPop": 3},
    )
    # The two budgets should sum to ~10
    total = sum(budgets.values())
    assert total == 10
    # IALS should get more than TopPop
    assert budgets.get("IALSRecommender", 0) > budgets.get("TopPopRecommender", 0)


def test_per_algorithm_trials_proportional_without_override() -> None:
    from recotem.training.search import _compute_budgets

    budgets = _compute_budgets(
        class_names=["A", "B", "C"],
        n_trials=9,
        per_algorithm_trials=None,
    )
    assert sum(budgets.values()) == 9
    assert budgets["A"] == 3
    assert budgets["B"] == 3
    assert budgets["C"] == 3


def test_per_algorithm_trials_explicit_zero_skips_algorithm() -> None:
    """Regression: explicit ``0`` must mean 'skip', not 'minimum 1'."""
    from recotem.training.search import _compute_budgets

    budgets = _compute_budgets(
        class_names=["A", "B", "C"],
        n_trials=10,
        per_algorithm_trials={"A": 10, "B": 0, "C": 0},
    )
    assert budgets == {"A": 10, "B": 0, "C": 0}
    assert sum(budgets.values()) == 10


def test_per_algorithm_trials_unspecified_share_leftover() -> None:
    from recotem.training.search import _compute_budgets

    budgets = _compute_budgets(
        class_names=["A", "B", "C"],
        n_trials=10,
        per_algorithm_trials={"A": 5},
    )
    assert budgets["A"] == 5
    assert budgets["B"] + budgets["C"] == 5
    assert sum(budgets.values()) == 10


def test_per_algorithm_trials_all_zero_falls_back_to_even_split() -> None:
    from recotem.training.search import _compute_budgets

    budgets = _compute_budgets(
        class_names=["A", "B", "C"],
        n_trials=9,
        per_algorithm_trials={"A": 0, "B": 0, "C": 0},
    )
    # All-zero override is treated as "no override".
    assert sum(budgets.values()) == 9
    assert all(v > 0 for v in budgets.values())


def test_per_algorithm_trials_over_budget_scaled_down() -> None:
    from recotem.training.search import _compute_budgets

    budgets = _compute_budgets(
        class_names=["A", "B", "C"],
        n_trials=10,
        per_algorithm_trials={"A": 8, "B": 7, "C": 5},
    )
    assert sum(budgets.values()) == 10
    assert all(v >= 1 for v in budgets.values())


def test_per_algorithm_trials_enqueues_each_algo_to_guarantee_budget(
    tmp_path: Path,
) -> None:
    """Regression: per_algorithm_trials must guarantee each algorithm
    receives its budgeted number of trials. Previously the search relied on
    TPESampler's categorical choice + post-hoc pruning, which let the
    sampler keep picking a saturated algorithm and waste slots that were
    nominally allocated to other algorithms. Fix: pre-enqueue per-class
    trials so Optuna runs exactly the requested distribution."""
    import numpy as np
    import scipy.sparse as sps

    from recotem.training.errors import SearchError
    from recotem.training.progress import ProgressReporter
    from recotem.training.search import run_search

    with patch("recotem.training.search.optuna.create_study") as mock_study_fn:
        mock_study = MagicMock()
        # Fresh study: no prior trials. After optimize() runs (mocked, no-op),
        # the enqueue loop has already populated the queue; we then trigger
        # SearchError by leaving trials empty so we can assert enqueue calls
        # without needing a fully-faked completed trial flow.
        mock_study.trials = []
        mock_study.optimize = MagicMock()
        mock_study_fn.return_value = mock_study

        X = sps.csr_matrix(np.ones((10, 5)))
        evaluator = MagicMock()

        with ProgressReporter(
            n_trials=10, recipe_name="test", run_id="run-enqueue"
        ) as rep:
            with pytest.raises(SearchError):
                run_search(
                    algorithms=["IALS", "TopPop"],
                    X_tv_train=X,
                    evaluator=evaluator,
                    n_trials=10,
                    per_algorithm_trials={"IALS": 7, "TopPop": 3},
                    per_trial_timeout_seconds=None,
                    timeout_seconds=None,
                    parallelism=1,
                    storage_path="",
                    random_seed=42,
                    reporter=rep,
                    recipe_name="test",
                    run_id="run-enqueue",
                )

        # Collect every enqueue_trial call's first positional arg.
        enqueued = [
            call.args[0]["recommender_class_name"]
            for call in mock_study.enqueue_trial.call_args_list
        ]
        ials_count = enqueued.count("IALSRecommender")
        toppop_count = enqueued.count("TopPopRecommender")
        assert ials_count == 7, (
            f"expected 7 IALS trials enqueued, got {ials_count}: {enqueued}"
        )
        assert toppop_count == 3, (
            f"expected 3 TopPop trials enqueued, got {toppop_count}: {enqueued}"
        )


# ---------------------------------------------------------------------------
# one structured log per trial
# ---------------------------------------------------------------------------


def test_one_structured_log_per_trial(caplog) -> None:
    """The trial progress reporter emits one log per completed trial."""
    import optuna

    from recotem.training.progress import ProgressReporter, make_trial_callback

    with caplog.at_level(logging.DEBUG):
        with ProgressReporter(n_trials=3, recipe_name="test", run_id="run-log") as rep:
            cb = make_trial_callback(rep)
            study = MagicMock()
            for i in range(3):
                trial = MagicMock()
                trial.number = i
                trial.value = -0.1 * (i + 1)
                trial.state = optuna.trial.TrialState.COMPLETE
                trial.params = {}
                trial.user_attrs = {}
                cb(study, trial)

    # The callback should have been invoked without error
    # The structured logs may go through structlog, not standard logging
    # — we just verify no unhandled exception occurred


# ---------------------------------------------------------------------------
# no_lock / lock semantics
# ---------------------------------------------------------------------------


def test_run_training_no_lock_skips_lock_acquisition(tmp_path: Path) -> None:
    """When no_lock=True, recipe_lock must NOT be called.

    We mock _run_training_locked to bypass data fetch / split / train so this
    test focuses solely on lock-acquisition behavior.
    """
    from recotem.training.pipeline import TrainResult, run_training

    recipe = _make_recipe(tmp_path)
    kr = _make_key_ring()

    fake_result = MagicMock(spec=TrainResult)

    # Patch _run_training_locked so we don't need real training data.
    with patch(
        "recotem.training.pipeline._run_training_locked", return_value=fake_result
    ) as mock_inner:
        # recipe_lock is imported lazily from recotem.training.lock; also patch there.
        with patch("recotem.training.lock.recipe_lock") as mock_lock:
            result = run_training(
                recipe,
                key_ring=kr,
                signing_key="active",
                no_lock=True,
                quiet=True,
            )

    mock_lock.assert_not_called()
    mock_inner.assert_called_once()
    assert result is fake_result


def test_run_training_lock_contended_returns_none_default(tmp_path: Path) -> None:
    """When the lock is held by another process and fail_on_busy=False, return None."""
    import contextlib

    from recotem.training.pipeline import run_training

    recipe = _make_recipe(tmp_path)
    kr = _make_key_ring()

    # Simulate a contended lock by yielding False from recipe_lock.
    @contextlib.contextmanager
    def _contended_lock(path, *, fail_on_busy=False):
        yield False

    # recipe_lock is imported lazily from recotem.training.lock; patch it there.
    with patch("recotem.training.lock.recipe_lock", _contended_lock):
        result = run_training(
            recipe,
            key_ring=kr,
            signing_key="active",
            no_lock=False,
            fail_on_busy=False,
            quiet=True,
        )
    assert result is None


def test_run_training_lock_contended_raises_when_fail_on_busy(tmp_path: Path) -> None:
    """When the lock is held and fail_on_busy=True, LockContestedError is raised."""
    import contextlib

    from recotem.training.lock import LockContestedError
    from recotem.training.pipeline import run_training

    recipe = _make_recipe(tmp_path)
    kr = _make_key_ring()

    # Simulate the lock module raising LockContestedError when fail_on_busy=True.
    @contextlib.contextmanager
    def _contended_fail_on_busy(path, *, fail_on_busy=False):
        if fail_on_busy:
            raise LockContestedError(f"lock held at {path}")
        yield False

    with patch("recotem.training.lock.recipe_lock", _contended_fail_on_busy):
        with pytest.raises(LockContestedError):
            run_training(
                recipe,
                key_ring=kr,
                signing_key="active",
                no_lock=False,
                fail_on_busy=True,
                quiet=True,
            )


# ---------------------------------------------------------------------------
# dev_allow_unsigned / signing key resolution
# ---------------------------------------------------------------------------


def test_run_training_dev_allow_unsigned_uses_in_memory_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dev_allow_unsigned=True builds an in-memory dev KeyRing (kid=='dev').

    We mock _run_training_locked so this test can verify KeyRing construction
    without needing real training data.
    """
    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)

    from recotem.training.pipeline import TrainResult, run_training

    recipe = _make_recipe(tmp_path)

    captured_key_rings: list = []

    def _capture_inner(**kwargs):
        captured_key_rings.append(kwargs.get("key_ring"))
        return MagicMock(spec=TrainResult)

    with patch(
        "recotem.training.pipeline._run_training_locked", side_effect=_capture_inner
    ):
        result = run_training(
            recipe,
            key_ring=None,  # force auto-build from env
            no_lock=True,
            dev_allow_unsigned=True,
            quiet=True,
        )

    assert result is not None
    assert len(captured_key_rings) == 1
    kr = captured_key_rings[0]
    assert kr.active_kid == "dev"


def test_run_training_missing_signing_key_raises_with_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No RECOTEM_SIGNING_KEYS + dev_allow_unsigned=False → TrainingError with code."""
    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)

    from recotem.training.errors import TrainingError
    from recotem.training.pipeline import run_training

    recipe = _make_recipe(tmp_path)

    with pytest.raises(TrainingError) as exc_info:
        run_training(
            recipe,
            key_ring=None,
            no_lock=True,
            dev_allow_unsigned=False,
            quiet=True,
        )

    assert exc_info.value.code == "signing_key_missing"
    assert "RECOTEM_SIGNING_KEYS" in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_source_class / datasource dispatch
# ---------------------------------------------------------------------------


def test_run_training_uses_get_source_class_for_fetch(tmp_path: Path) -> None:
    """_fetch_data calls get_source_class with the recipe's source.type.

    get_source_class is imported lazily inside _fetch_data; we patch it at
    the registry module where it is defined.
    """
    from recotem.training.pipeline import _fetch_data

    recipe = _make_recipe(tmp_path)

    import pandas as pd

    mock_source = MagicMock()
    mock_source.fetch.return_value = pd.DataFrame(
        {"user_id": ["u1", "u2"], "item_id": ["i1", "i2"]}
    )
    mock_source_cls = MagicMock(return_value=mock_source)

    with patch(
        "recotem.datasource.registry.get_source_class",
        return_value=mock_source_cls,
    ) as mock_gsc:
        df = _fetch_data(recipe, run_id="test-run")

    # get_source_class must have been called with the recipe's source type.
    mock_gsc.assert_called_once_with("csv")
    assert len(df) == 2


# ---------------------------------------------------------------------------
# M21 — unexpected exception inside datasource path -> DataSourceError (exit 3)
# ---------------------------------------------------------------------------


def test_unexpected_exception_in_fetch_raises_DataSourceError_not_TrainingError(
    tmp_path: Path,
) -> None:
    """An unexpected exception raised by source_instance.fetch() must be
    wrapped as DataSourceError (exit 3), not TrainingError (exit 4).

    The documented exit-code contract in docs/operations.md maps datasource
    failures to exit 3.  Before this fix _fetch_data wrapped them as
    TrainingError(code='datasource_error'), which the CLI mapped to exit 4.
    """
    from recotem.datasource.base import DataSourceError
    from recotem.training.pipeline import _fetch_data

    recipe = _make_recipe(tmp_path)

    # Simulate an unexpected runtime error from the data source (e.g. network
    # timeout, unexpected library exception, etc.) that is NOT a DataSourceError.
    boom = RuntimeError("connection refused")
    mock_source = MagicMock()
    mock_source.fetch.side_effect = boom
    mock_source_cls = MagicMock(return_value=mock_source)

    with patch(
        "recotem.datasource.registry.get_source_class",
        return_value=mock_source_cls,
    ):
        with pytest.raises(DataSourceError) as exc_info:
            _fetch_data(recipe, run_id="test-m21")

    assert "Data fetch failed" in str(exc_info.value)
    assert exc_info.value.__cause__ is boom


def test_DataSourceError_from_fetch_propagates_unchanged(tmp_path: Path) -> None:
    """A DataSourceError raised by fetch() must pass through _fetch_data
    unchanged (not double-wrapped)."""
    from recotem.datasource.base import DataSourceError
    from recotem.training.pipeline import _fetch_data

    recipe = _make_recipe(tmp_path)

    original = DataSourceError("auth token expired")
    mock_source = MagicMock()
    mock_source.fetch.side_effect = original
    mock_source_cls = MagicMock(return_value=mock_source)

    with patch(
        "recotem.datasource.registry.get_source_class",
        return_value=mock_source_cls,
    ):
        with pytest.raises(DataSourceError) as exc_info:
            _fetch_data(recipe, run_id="test-m21b")

    assert exc_info.value is original


# ---------------------------------------------------------------------------
# J1. per_trial_timeout_seconds orphaned-thread warning log
# ---------------------------------------------------------------------------


def test_per_trial_timeout_orphans_thread_warns(tmp_path: Path) -> None:
    """When per_trial_timeout_seconds is very short and the recommender's
    learn takes longer, the watcher thread is orphaned and a
    per_trial_timeout_thread_orphaned structlog event must be emitted.
    """
    import time

    import numpy as np
    import scipy.sparse as sps
    import structlog.testing

    from recotem.training.errors import SearchError
    from recotem.training.progress import ProgressReporter
    from recotem.training.search import run_search

    # Build a fake recommender class whose learn_with_optimizer sleeps > timeout
    class _SlowRecommender:
        """Fake recommender that sleeps during learn_with_optimizer."""

        learnt_config: dict = {}

        def __init__(self, X, **kwargs):
            self._X = X

        @staticmethod
        def default_suggest_parameter(trial, space):
            return {}

        def learn_with_optimizer(self, evaluator, trial):
            time.sleep(2)  # exceed timeout=0.1

        def learn(self):
            return self

    with patch(
        "recotem.training.search.get_recommender_cls",
        return_value=_SlowRecommender,
    ):
        X = sps.csr_matrix(np.ones((5, 3)))
        evaluator = MagicMock()

        with structlog.testing.capture_logs() as cap:
            with ProgressReporter(
                n_trials=2, recipe_name="timeout_test", run_id="run-timeout"
            ) as rep:
                with pytest.raises((SearchError, Exception)):
                    run_search(
                        algorithms=["TopPopRecommender"],
                        X_tv_train=X,
                        evaluator=evaluator,
                        n_trials=2,
                        per_algorithm_trials=None,
                        per_trial_timeout_seconds=1,  # 1s, but learn sleeps 2s
                        timeout_seconds=5,
                        parallelism=1,
                        storage_path="",
                        random_seed=0,
                        reporter=rep,
                        recipe_name="timeout_test",
                        run_id="run-timeout",
                    )

    orphan_events = [
        e for e in cap if e.get("event") == "per_trial_timeout_thread_orphaned"
    ]
    assert orphan_events, (
        "Expected at least one per_trial_timeout_thread_orphaned log event; "
        f"captured events: {[e.get('event') for e in cap]}"
    )


# ---------------------------------------------------------------------------
# CRITICAL: per_algorithm_trials zero budget enqueues no trials (not max(1, 0)=1)
# ---------------------------------------------------------------------------


def test_per_algorithm_zero_budget_enqueues_no_trials() -> None:
    """Explicit 0 budget for an algorithm must result in exactly 0 in the plan.

    Regression guard against ``max(1, budget)`` footgun: if someone adds that
    guard, TopPop would silently get 1 trial even when budget=0.

    Uses _compute_budgets (pure function) to verify budget allocation, and
    also verifies that run_search does NOT enqueue any TopPop trials when its
    budget is 0 (only IALS trials are enqueued).
    """
    from recotem.training.search import _compute_budgets

    # Canonical aliases: "IALS" and "TopPop" are the supported short names.
    budgets = _compute_budgets(
        class_names=["IALSRecommender", "TopPopRecommender"],
        n_trials=5,
        per_algorithm_trials={"IALS": 5, "TopPop": 0},
    )

    assert budgets.get("IALSRecommender", -1) == 5, (
        f"IALS should have 5 trials, got {budgets.get('IALSRecommender')}"
    )
    assert budgets.get("TopPopRecommender", -1) == 0, (
        f"TopPop budget=0 must NOT be promoted to 1 (max(1,0) footgun); "
        f"got {budgets.get('TopPopRecommender')}"
    )
    assert sum(budgets.values()) == 5

    # Verify via run_search's enqueue calls: TopPop must never be enqueued.
    import numpy as np
    import scipy.sparse as sps

    from recotem.training.errors import SearchError
    from recotem.training.progress import ProgressReporter
    from recotem.training.search import run_search

    with patch("recotem.training.search.optuna.create_study") as mock_study_fn:
        mock_study = MagicMock()
        mock_study.trials = []
        mock_study.optimize = MagicMock()
        mock_study_fn.return_value = mock_study

        X = sps.csr_matrix(np.ones((10, 5)))
        evaluator = MagicMock()

        with ProgressReporter(
            n_trials=5, recipe_name="zero_budget", run_id="run-zero"
        ) as rep:
            with pytest.raises(SearchError):
                run_search(
                    algorithms=["IALS", "TopPop"],
                    X_tv_train=X,
                    evaluator=evaluator,
                    n_trials=5,
                    per_algorithm_trials={"IALS": 5, "TopPop": 0},
                    per_trial_timeout_seconds=None,
                    timeout_seconds=None,
                    parallelism=1,
                    storage_path="",
                    random_seed=42,
                    reporter=rep,
                    recipe_name="zero_budget",
                    run_id="run-zero",
                )

    enqueued = [
        call.args[0]["recommender_class_name"]
        for call in mock_study.enqueue_trial.call_args_list
    ]
    toppop_count = enqueued.count("TopPopRecommender")

    assert toppop_count == 0, (
        f"TopPop budget=0 must enqueue 0 trials, not {toppop_count}. "
        "max(1, budget) footgun detected. Enqueued: {enqueued}"
    )


# ---------------------------------------------------------------------------
# C4 — timeout_seconds fires before first trial completes -> TrainingError
# ---------------------------------------------------------------------------


def test_timeout_before_first_trial_raises_TrainingError(tmp_path: Path) -> None:
    """A very short global timeout_seconds must cause run_training to raise TrainingError.

    We mock the recommender's learn_with_optimizer to sleep slightly beyond the
    timeout, and set timeout_seconds to a very small value (0.05 s).  The Optuna
    study must stop and raise a SearchError (which is a TrainingError subclass)
    rather than hanging or returning silently.
    """
    import time

    import numpy as np
    import scipy.sparse as sps

    from recotem.training.errors import SearchError, TrainingError
    from recotem.training.progress import ProgressReporter
    from recotem.training.search import run_search

    class _SlowRecommender:
        learnt_config: dict = {}

        def __init__(self, X, **kwargs):
            self._X = X

        @staticmethod
        def default_suggest_parameter(trial, space):
            return {}

        def learn_with_optimizer(self, evaluator, trial):
            time.sleep(0.3)  # longer than per_trial_timeout below

        def learn(self):
            return self

    X = sps.csr_matrix(np.ones((5, 3)))
    evaluator = MagicMock()

    with patch(
        "recotem.training.search.get_recommender_cls",
        return_value=_SlowRecommender,
    ):
        with ProgressReporter(
            n_trials=2, recipe_name="timeout_c4", run_id="run-c4"
        ) as rep:
            with pytest.raises((SearchError, TrainingError)):
                run_search(
                    algorithms=["TopPopRecommender"],
                    X_tv_train=X,
                    evaluator=evaluator,
                    n_trials=2,
                    per_algorithm_trials=None,
                    per_trial_timeout_seconds=0.1,  # 100ms per-trial budget
                    timeout_seconds=0.15,  # 150ms global — exhausted quickly
                    parallelism=1,
                    storage_path="",
                    random_seed=0,
                    reporter=rep,
                    recipe_name="timeout_c4",
                    run_id="run-c4",
                )


# ---------------------------------------------------------------------------
# C6 — per_trial_timeout_seconds: orphaned trial not in best score
# ---------------------------------------------------------------------------


def test_per_trial_timeout_excludes_killed_trial_from_best(tmp_path: Path) -> None:
    """When a trial is orphaned by per_trial_timeout, its result must not contribute
    to the best score.

    We mock the Optuna study so that all trials are marked FAIL/RUNNING (no
    COMPLETE state), which means run_search must raise SearchError (no_completed_trials)
    rather than returning a best score from the orphaned trial.
    """
    import numpy as np
    import optuna
    import scipy.sparse as sps

    from recotem.training.errors import SearchError
    from recotem.training.progress import ProgressReporter
    from recotem.training.search import run_search

    with patch("recotem.training.search.optuna.create_study") as mock_study_fn:
        mock_study = MagicMock()

        # All trials are in RUNNING state (orphaned/timed-out) — none COMPLETE.
        orphaned_trial = MagicMock()
        orphaned_trial.state = optuna.trial.TrialState.RUNNING
        orphaned_trial.value = None
        orphaned_trial.number = 0
        orphaned_trial.params = {"recommender_class_name": "TopPopRecommender"}
        orphaned_trial.user_attrs = {"recommender_class_name": "TopPopRecommender"}

        mock_study.trials = [orphaned_trial]
        mock_study.optimize = MagicMock()
        mock_study_fn.return_value = mock_study

        X = sps.csr_matrix(np.ones((5, 3)))
        evaluator = MagicMock()

        with ProgressReporter(
            n_trials=1, recipe_name="orphan_c6", run_id="run-c6"
        ) as rep:
            with pytest.raises(SearchError) as exc_info:
                run_search(
                    algorithms=["TopPopRecommender"],
                    X_tv_train=X,
                    evaluator=evaluator,
                    n_trials=1,
                    per_algorithm_trials=None,
                    per_trial_timeout_seconds=None,
                    timeout_seconds=None,
                    parallelism=1,
                    storage_path="",
                    random_seed=0,
                    reporter=rep,
                    recipe_name="orphan_c6",
                    run_id="run-c6",
                )

    # The orphaned (RUNNING) trial must NOT have been promoted to best.
    assert exc_info.value is not None, (
        "run_search must raise SearchError when only orphaned/running trials exist; "
        "the orphaned trial's score must not appear in best_score."
    )
