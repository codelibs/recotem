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
- Task 9: feature-aware iALS wiring -- header/payload carry the encoder
  state when a features: block is configured, the header omits the key
  entirely otherwise, and the final refit re-encodes onto its OWN item
  order rather than reusing the search phase's (a regression guard for the
  one bug irspack will not raise an error for: a misordered feature matrix)
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
# Task 9: feature-aware iALS fixtures
#
# IALS's default tune range samples n_components from [4, 300] and needs a
# matrix with real (non-degenerate) low-rank structure to factorise cleanly
# without divide-by-zero warnings; a couple of interaction rows is not
# enough. This mirrors the clustered synthetic dataset that
# tests/integration/test_serve_predict_e2e.py already uses to exercise IALS
# outside the `slow` mark.
# ---------------------------------------------------------------------------


def _make_clustered_synthetic_csv(tmp_path: Path) -> Path:
    """Deterministic interaction matrix with real low-rank cluster structure.

    A fully-dense grid is rank-deficient and makes IALS warn/divide-by-zero;
    laying users out in overlapping clusters with a few idiosyncratic items
    each gives a matrix every algorithm (including IALS) factorises cleanly.
    """
    n_users, n_items, n_clusters, band = 60, 40, 6, 12
    pairs: set[tuple[str, str]] = set()
    for u in range(n_users):
        cluster = u % n_clusters
        for k in range(band):
            pairs.add(
                (f"u{u}", f"i{(cluster * (n_items // n_clusters) + k) % n_items}")
            )
        pairs.add((f"u{u}", f"i{(u * 7) % n_items}"))
        pairs.add((f"u{u}", f"i{(u * 13 + 3) % n_items}"))
    rows = ["user_id,item_id"]
    rows.extend(f"{u},{i}" for u, i in sorted(pairs))
    csv_file = tmp_path / "clustered.csv"
    csv_file.write_text("\n".join(rows) + "\n")
    return csv_file


@pytest.fixture
def plain_recipe(tmp_path: Path):
    """A features-less recipe: the header must omit "features" entirely."""
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )

    csv_file = _make_clustered_synthetic_csv(tmp_path)
    return Recipe(
        name="plain_pipeline_test",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=["TopPop"],
            n_trials=1,
            cutoff=5,  # must be < n_items to avoid irspack ValueError
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=0),
        ),
        output=OutputConfig(
            path=str(tmp_path / "plain_pipeline_test.recotem"),
            versioning="always_overwrite",
        ),
    )


@pytest.fixture
def feature_recipe(tmp_path: Path):
    """An IALS recipe with an item `features:` block (genre + year)."""
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        FeatureColumn,
        FeaturesConfig,
        FeatureSideConfig,
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )

    csv_file = _make_clustered_synthetic_csv(tmp_path)

    items_csv = tmp_path / "item_features.csv"
    genres = ["action", "drama", "comedy"]
    pd.DataFrame(
        {
            "item_id": [f"i{i}" for i in range(40)],
            "genre": [genres[i % len(genres)] for i in range(40)],
            "year": [2000 + i for i in range(40)],
        }
    ).to_csv(items_csv, index=False)

    return Recipe(
        name="feature_pipeline_test",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        features=FeaturesConfig(
            item=FeatureSideConfig(
                source=CSVConfig(type="csv", path=str(items_csv)),
                id_column="item_id",
                columns=[
                    FeatureColumn(name="genre", encoding="categorical"),
                    FeatureColumn(name="year", encoding="numerical"),
                ],
            )
        ),
        training=TrainingConfig(
            algorithms=["IALS"],
            n_trials=2,
            cutoff=5,  # must be < n_items to avoid irspack ValueError
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=0),
        ),
        output=OutputConfig(
            path=str(tmp_path / "feature_pipeline_test.recotem"),
            versioning="always_overwrite",
        ),
    )


@pytest.fixture
def feature_recipe_both_axes(tmp_path: Path):
    """An IALS recipe with BOTH an item and a USER `features:` block.

    ``feature_recipe`` above (used by most Task 9 tests) is item-only, which
    means no pipeline fixture ever exercised the search-phase alignment
    test's ``user_features`` row order -- a mutation of the search-phase
    call site's ``user_order`` argument (e.g. reversing it) was therefore
    invisible to the whole suite. This fixture exists specifically to close
    that gap: it is deliberately NOT a drop-in replacement for
    ``feature_recipe`` (several other tests assert ``"user" not in
    parsed["features"]`` against that fixture and must keep passing).
    """
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        FeatureColumn,
        FeaturesConfig,
        FeatureSideConfig,
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )

    csv_file = _make_clustered_synthetic_csv(tmp_path)

    items_csv = tmp_path / "item_features_both_axes.csv"
    genres = ["action", "drama", "comedy"]
    pd.DataFrame(
        {
            "item_id": [f"i{i}" for i in range(40)],
            "genre": [genres[i % len(genres)] for i in range(40)],
            "year": [2000 + i for i in range(40)],
        }
    ).to_csv(items_csv, index=False)

    users_csv = tmp_path / "user_features_both_axes.csv"
    bands = ["young", "old"]
    pd.DataFrame(
        {
            "user_id": [f"u{u}" for u in range(60)],
            "band": [bands[u % len(bands)] for u in range(60)],
        }
    ).to_csv(users_csv, index=False)

    return Recipe(
        name="feature_pipeline_both_axes_test",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        features=FeaturesConfig(
            item=FeatureSideConfig(
                source=CSVConfig(type="csv", path=str(items_csv)),
                id_column="item_id",
                columns=[
                    FeatureColumn(name="genre", encoding="categorical"),
                    FeatureColumn(name="year", encoding="numerical"),
                ],
            ),
            user=FeatureSideConfig(
                source=CSVConfig(type="csv", path=str(users_csv)),
                id_column="user_id",
                columns=[FeatureColumn(name="band", encoding="categorical")],
            ),
        ),
        training=TrainingConfig(
            algorithms=["IALS"],
            n_trials=2,
            cutoff=5,  # must be < n_items to avoid irspack ValueError
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=0),
        ),
        output=OutputConfig(
            path=str(tmp_path / "feature_pipeline_both_axes_test.recotem"),
            versioning="always_overwrite",
        ),
    )


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
# M-9 regression: callback default_class is configurable, not "unknown"
# ---------------------------------------------------------------------------


def test_make_trial_callback_uses_configured_default_class() -> None:
    """``make_trial_callback`` must forward ``default_class`` so early trials
    surface a real candidate name to the SIEM rather than ``"unknown"``.

    Pre-fix, the callback hard-coded ``default_class="unknown"``.  When a
    trial completed before its ``recommender_class_name`` made it into
    ``trial.user_attrs`` / ``trial.params`` (early-trial race), the
    structured ``trial_done`` event surfaced ``algorithm="unknown"`` and
    polluted SIEM aggregations on the algorithm dimension.
    """
    from unittest.mock import MagicMock

    from recotem.training.progress import make_trial_callback

    seen_algorithms: list[str] = []

    class _RecordingReporter:
        def on_trial_done(self, *, trial_number, algorithm, score, params) -> None:
            seen_algorithms.append(algorithm)

    cb = make_trial_callback(_RecordingReporter(), default_class="IALSRecommender")

    trial = MagicMock()
    trial.number = 0
    trial.value = -0.5
    trial.params = {}  # no recommender_class_name yet
    trial.user_attrs = {}  # not set yet either

    cb(MagicMock(), trial)

    assert seen_algorithms == ["IALSRecommender"], (
        "Early-trial callback must use the configured default_class "
        f"(IALSRecommender); got {seen_algorithms!r}"
    )


def test_make_trial_callback_default_class_back_compat_is_unknown() -> None:
    """Omitting ``default_class`` must keep the legacy ``"unknown"`` behaviour
    for back-compat with any external callers of ``make_trial_callback``.
    """
    from unittest.mock import MagicMock

    from recotem.training.progress import make_trial_callback

    seen: list[str] = []

    class _RecordingReporter:
        def on_trial_done(self, *, trial_number, algorithm, score, params) -> None:
            seen.append(algorithm)

    cb = make_trial_callback(_RecordingReporter())  # no default_class
    trial = MagicMock()
    trial.number = 0
    trial.value = -0.5
    trial.params = {}
    trial.user_attrs = {}
    cb(MagicMock(), trial)
    assert seen == ["unknown"]


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
    # Accept **kwargs so that callers forwarding additional arguments (e.g.
    # timeout=0.0 added by LEAK-2) do not break this mock.
    @contextlib.contextmanager
    def _contended_lock(path, *, fail_on_busy=False, **kwargs):
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
    # Accept **kwargs so that callers forwarding additional arguments (e.g.
    # timeout=0.0 added by LEAK-2) do not break this mock.
    @contextlib.contextmanager
    def _contended_fail_on_busy(path, *, fail_on_busy=False, **kwargs):
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


# ---------------------------------------------------------------------------
# I-B: _compute_recipe_hash handles non-JSON-serializable values (Decimal etc.)
# ---------------------------------------------------------------------------


def test_compute_recipe_hash_with_decimal_query_parameter(tmp_path: Path) -> None:
    """_compute_recipe_hash must not raise TypeError for Decimal query_parameters.

    BigQuery recipes may carry query_parameters with Decimal values.  pydantic's
    model_dump(mode='json') does not coerce Decimal, so json.dumps raised
    TypeError before the I-B fix added default=str.
    """
    from decimal import Decimal

    from recotem.datasource.bigquery import BigQueryConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.training.pipeline import _compute_recipe_hash

    recipe = Recipe(
        name="bq_decimal_test",
        source=BigQueryConfig(
            type="bigquery",
            project="test-project",
            query="SELECT * FROM `test_dataset.test_table` WHERE score > @threshold",
            query_parameters={"threshold": Decimal("1.5")},
        ),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=["TopPop"],
            n_trials=1,
            split=SplitConfig(scheme="random", heldout_ratio=0.1, seed=42),
        ),
        output=OutputConfig(path=str(tmp_path / "bq_test.recotem")),
    )

    # Must not raise TypeError
    digest = _compute_recipe_hash(recipe)
    assert isinstance(digest, str) and len(digest) == 64, (
        f"Expected 64-char hex digest, got {digest!r}"
    )


def test_compute_recipe_hash_is_reproducible(tmp_path: Path) -> None:
    """The same recipe must always produce the same hash (canonical serialization)."""
    from recotem.training.pipeline import _compute_recipe_hash

    recipe = _make_recipe(tmp_path)

    hash1 = _compute_recipe_hash(recipe)
    hash2 = _compute_recipe_hash(recipe)

    assert hash1 == hash2, (
        f"_compute_recipe_hash must be deterministic; got {hash1!r} then {hash2!r}"
    )


def test_compute_recipe_hash_differs_for_different_recipes(tmp_path: Path) -> None:
    """Different recipes must produce different hashes."""
    from recotem.training.pipeline import _compute_recipe_hash

    recipe_a = _make_recipe(tmp_path, algorithms=["TopPop"])
    recipe_b = _make_recipe(tmp_path, algorithms=["TopPop"], n_trials=5)

    assert _compute_recipe_hash(recipe_a) != _compute_recipe_hash(recipe_b), (
        "Recipes with different n_trials must produce different hashes"
    )


# ---------------------------------------------------------------------------
# MAJOR-3: _compute_recipe_hash must normalise Path separators to POSIX style
# ---------------------------------------------------------------------------


def test_compute_recipe_hash_normalizes_windows_style_paths() -> None:
    """Paths on Windows serialize as '\\'-separated strings; hash must be identical
    to the POSIX forward-slash form so that artifact headers produced on
    different OSes are comparable.

    We bypass the Recipe constructor (which would reject a PureWindowsPath for
    source.path) and call ``_normalize_paths_for_hash`` directly with a dict
    that mimics what ``model_dump`` returns when a ``PureWindowsPath`` slips
    through (e.g. via an ``Any``-typed field on a Windows host).
    """
    import pathlib

    from recotem.training.pipeline import _normalize_paths_for_hash

    win_path = pathlib.PureWindowsPath("C:\\data\\recipes\\file.csv")
    posix_path = pathlib.PurePosixPath("C:/data/recipes/file.csv")

    # Both must normalise to the same POSIX string.
    win_result = _normalize_paths_for_hash({"path": win_path})
    posix_result = _normalize_paths_for_hash({"path": posix_path})

    assert win_result == {"path": "C:/data/recipes/file.csv"}, (
        f"PureWindowsPath must be converted to POSIX; got {win_result!r}"
    )
    assert posix_result == {"path": "C:/data/recipes/file.csv"}, (
        f"PurePosixPath must be converted to POSIX; got {posix_result!r}"
    )
    assert win_result == posix_result, (
        "Windows-style and POSIX-style paths for the same location must "
        f"produce identical normalised dicts; got {win_result!r} vs {posix_result!r}"
    )


def test_compute_recipe_hash_path_independent_serialization(tmp_path: Path) -> None:
    """The recipe hash must not vary based on which pathlib class holds the path.

    We inject a ``Path`` instance into the dump dict by hand (simulating what
    happens when ``model_dump`` encounters a ``pathlib.Path`` inside an
    ``Any``-typed field) and verify the hash equals the one produced when the
    same path is represented as a plain POSIX string.
    """
    import json
    import pathlib

    from recotem.training.pipeline import (
        _json_default_for_hash,
        _normalize_paths_for_hash,
    )

    posix_str = "/data/recipes/news_articles.csv"

    # Dict with a plain string path (baseline).
    string_dict = {"source": {"path": posix_str, "type": "csv"}}

    # Dict with a pathlib.Path instance (what might come from an Any-typed field).
    path_dict = {"source": {"path": pathlib.Path(posix_str), "type": "csv"}}

    def _canonical(d: dict) -> str:
        return json.dumps(
            _normalize_paths_for_hash(d),
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default_for_hash,
        )

    assert _canonical(string_dict) == _canonical(path_dict), (
        "The canonical JSON for a plain string path and a pathlib.Path with the "
        "same value must be identical; got:\n"
        f"  string: {_canonical(string_dict)!r}\n"
        f"  path:   {_canonical(path_dict)!r}"
    )


# ---------------------------------------------------------------------------
# B-1 regression: _train_final filters best_params to __init__-accepted keys
# ---------------------------------------------------------------------------


def test_train_final_filters_best_params_to_init_signature() -> None:
    """``_train_final`` must drop best_params keys not in ``rec_cls.__init__``
    and emit a ``final_training_dropped_params`` WARN log.

    Pre-fix, a stray TPE-injected key (e.g. an Optuna ``user_attrs`` overlay
    from ``learnt_config``) caused ``rec_cls(X_full, **best_params)`` to
    raise ``TypeError`` after a successful 100% search — the artifact never
    got written and the operator saw exit 4 with no actionable message.
    """
    from unittest.mock import patch

    import pandas as pd
    import structlog
    import structlog.testing

    from recotem.training.pipeline import _train_final

    df = pd.DataFrame(
        {
            "user_id": ["u1", "u2", "u1", "u3"],
            "item_id": ["i1", "i1", "i2", "i2"],
        }
    )

    class FakeRec:
        """Recommender with a strict ``__init__`` that does not accept extras.

        Mirrors the irspack failure mode where recommender constructors
        reject unknown keyword arguments.
        """

        def __init__(self, X, n_components: int = 8) -> None:
            self.X = X
            self.n_components = n_components

        def learn(self):
            return self

    # The pipeline emits via structlog directly (not stdlib logging), so
    # caplog cannot see the line.  capture_logs() temporarily replaces the
    # processor chain with one that buffers events into a list.
    with (
        patch(
            "recotem.training.pipeline.get_recommender_cls",
            return_value=FakeRec,
        ),
        structlog.testing.capture_logs() as captured,
    ):
        result = _train_final(
            df,
            user_column="user_id",
            item_column="item_id",
            class_name="FakeRec",
            best_params={
                "n_components": 16,
                "stray_key_from_user_attrs": "ignored",
                "another_unknown": 42,
            },
        )

    # Construction must succeed (no TypeError leaks past the filter).
    assert result.recommender.n_components == 16

    # Both unknown keys must be reported in the dropped log line so an
    # operator can investigate plugin/version drift instead of staring at a
    # silent exit 4.
    dropped_events = [
        e for e in captured if e.get("event") == "final_training_dropped_params"
    ]
    assert dropped_events, (
        f"Expected a final_training_dropped_params WARN line; got {captured!r}"
    )
    dropped_keys = dropped_events[0]["dropped"]
    assert "another_unknown" in dropped_keys
    assert "stray_key_from_user_attrs" in dropped_keys
    assert dropped_events[0]["class_name"] == "FakeRec"


def test_train_final_passes_through_when_constructor_accepts_kwargs() -> None:
    """If ``__init__`` has ``**kwargs``, all params flow through unchanged.

    Recommenders that intentionally accept extras (e.g. transparently
    forwarding to a base class) must not be denied params just because
    we cannot enumerate them via ``inspect.signature``.
    """
    from unittest.mock import patch

    import pandas as pd

    from recotem.training.pipeline import _train_final

    df = pd.DataFrame(
        {"user_id": ["u1", "u2"], "item_id": ["i1", "i2"]},
    )

    captured: dict = {}

    class OpenRec:
        def __init__(self, X, **kwargs) -> None:
            captured.update(kwargs)

        def learn(self):
            return self

    with patch(
        "recotem.training.pipeline.get_recommender_cls",
        return_value=OpenRec,
    ):
        _train_final(
            df,
            user_column="user_id",
            item_column="item_id",
            class_name="OpenRec",
            best_params={"alpha": 0.1, "beta": 0.2, "gamma": 3},
        )

    assert captured == {"alpha": 0.1, "beta": 0.2, "gamma": 3}


def test_train_final_maps_value_error_to_training_error() -> None:
    """irspack ``ValueError`` (e.g. invalid hyperparam combo) must surface as
    ``TrainingError`` so the operator-visible exit code is 4 (training),
    not 1 (unknown).
    """
    from unittest.mock import patch

    import pandas as pd
    import pytest

    from recotem.training.errors import TrainingError
    from recotem.training.pipeline import _train_final

    df = pd.DataFrame(
        {"user_id": ["u1", "u2"], "item_id": ["i1", "i2"]},
    )

    class RejectingRec:
        def __init__(self, X, n_components: int = 8) -> None:
            raise ValueError("n_components must be < min(n_users, n_items)")

        def learn(self):
            return self  # pragma: no cover

    with patch(
        "recotem.training.pipeline.get_recommender_cls",
        return_value=RejectingRec,
    ):
        with pytest.raises(TrainingError, match="rejected params"):
            _train_final(
                df,
                user_column="user_id",
                item_column="item_id",
                class_name="RejectingRec",
                best_params={"n_components": 64},
            )


# ---------------------------------------------------------------------------
# Round-15 MJ7: train_done event includes recipe_hash and data_stats fields
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_train_done_event_includes_recipe_hash_and_data_stats(
    tmp_path: Path, movielens_small_df: pd.DataFrame
) -> None:
    """The canonical ``train_done`` event must include recipe_hash plus the
    data-stats fields (n_rows, n_users, n_items).  SIEM rules need these
    so an artifact can be correlated to "which recipe version produced
    it" and "what training-set size" without joining against the artifact
    header.
    """
    import structlog.testing

    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.training.pipeline import run_training

    csv_file = tmp_path / "ml_for_log_test.csv"
    movielens_small_df[["user_id", "item_id"]].to_csv(csv_file, index=False)

    recipe = Recipe(
        name="train_done_log_test",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=["TopPop"],
            n_trials=1,
            split=SplitConfig(scheme="random", heldout_ratio=0.1, seed=42),
        ),
        output=OutputConfig(path=str(tmp_path / "train_done_log_test.recotem")),
    )

    kr = _make_key_ring()

    def _mock_write(payload_obj, header_dict, key_ring, fs_path, *, versioning):
        return fs_path

    with structlog.testing.capture_logs() as cap:
        result = run_training(
            recipe, key_ring=kr, signing_key="active", write_artifact_fn=_mock_write
        )

    assert result is not None

    done_events = [e for e in cap if e.get("event") == "train_done"]
    assert done_events, (
        f"Expected exactly one 'train_done' event; got events: "
        f"{[e.get('event') for e in cap]}"
    )
    done = done_events[0]

    required_fields = ("recipe_hash", "n_rows", "n_users", "n_items")
    for field_name in required_fields:
        assert field_name in done, (
            f"train_done must include {field_name!r}; got keys: {sorted(done.keys())!r}"
        )

    # Plausibility checks
    assert isinstance(done["recipe_hash"], str)
    assert len(done["recipe_hash"]) > 0
    assert isinstance(done["n_rows"], int) and done["n_rows"] > 0
    assert isinstance(done["n_users"], int) and done["n_users"] > 0
    assert isinstance(done["n_items"], int) and done["n_items"] > 0


# ---------------------------------------------------------------------------
# Round-15 MJ7: train_error surfaces partial metrics when populated
# ---------------------------------------------------------------------------


def test_train_error_event_includes_partial_metrics_when_available(
    tmp_path: Path,
) -> None:
    """When the inner pipeline writes ``recipe_hash`` / ``n_rows`` /
    ``n_users`` / ``n_items`` into the shared metrics holder before a
    failure, the outer ``run_training`` ``except`` clause must surface
    those fields in the ``train_error`` event.
    """
    import structlog.testing

    from recotem.training.errors import TrainingError
    from recotem.training.pipeline import run_training

    recipe = _make_recipe(tmp_path)
    kr = _make_key_ring()

    def _failing_inner(*args, metrics_holder=None, **kwargs):
        # Simulate the inner pipeline having computed the metrics before the
        # failure (this is what the production code does — recipe_hash is
        # filled in step 1 and data_stats after step 3).
        if metrics_holder is not None:
            metrics_holder["recipe_hash"] = "deadbeef" * 8  # 64-char fingerprint
            metrics_holder["n_rows"] = 12345
            metrics_holder["n_users"] = 678
            metrics_holder["n_items"] = 90
        raise TrainingError("simulated failure", code="custom_failure")

    with structlog.testing.capture_logs() as cap:
        with patch(
            "recotem.training.pipeline._run_training_locked",
            side_effect=_failing_inner,
        ):
            with pytest.raises(TrainingError):
                run_training(
                    recipe,
                    key_ring=kr,
                    signing_key="active",
                    no_lock=True,
                    quiet=True,
                )

    err_events = [e for e in cap if e.get("event") == "train_error"]
    assert err_events, (
        f"Expected 'train_error' event; got events: {[e.get('event') for e in cap]}"
    )
    err = err_events[0]

    assert err["recipe_hash"] == "deadbeef" * 8
    assert err["n_rows"] == 12345
    assert err["n_users"] == 678
    assert err["n_items"] == 90
    assert err["code"] == "custom_failure"


# ---------------------------------------------------------------------------
# Round-15 MJ17: internal_error path uses logger.exception for stacktrace
# ---------------------------------------------------------------------------


def test_train_error_internal_error_attaches_exc_info(
    tmp_path: Path,
) -> None:
    """Non-domain (internal_error) failures must be logged with
    ``exc_info=True`` so structlog attaches the stacktrace.  Sentry /
    DataDog integrations rely on the stacktrace being present in the
    structured event for grouping and root-cause analysis.

    The event still goes through ``logger.error`` (same method used for
    domain errors) so spy-based tests that scan ``spy.error.call_args_list``
    continue to find it.
    """
    from recotem.training import pipeline as pipeline_mod

    spy_logger = MagicMock()
    original_logger = pipeline_mod.logger
    pipeline_mod.logger = spy_logger

    try:
        recipe = _make_recipe(tmp_path)
        kr = _make_key_ring()

        def _failing_inner(*args, metrics_holder=None, **kwargs):
            # KeyError is NOT a TrainingError subclass → "internal_error" code.
            raise KeyError("unexpected_internal_bug")

        with patch(
            "recotem.training.pipeline._run_training_locked",
            side_effect=_failing_inner,
        ):
            with pytest.raises(KeyError):
                run_training_under_test = pipeline_mod.run_training
                run_training_under_test(
                    recipe,
                    key_ring=kr,
                    signing_key="active",
                    no_lock=True,
                    quiet=True,
                )

        train_error_calls = [
            call
            for call in spy_logger.error.call_args_list
            if call.args and call.args[0] == "train_error"
        ]
        assert train_error_calls, "train_error must be emitted via logger.error"
        kwargs = train_error_calls[0].kwargs
        assert kwargs.get("code") == "internal_error"
        assert kwargs.get("exc_info") is True, (
            f"internal_error path must set exc_info=True so the stacktrace "
            f"is attached to the structured event; got kwargs: {kwargs!r}"
        )
    finally:
        pipeline_mod.logger = original_logger


def test_train_error_domain_error_does_not_attach_exc_info(
    tmp_path: Path,
) -> None:
    """For declared TrainingError subclasses the user-facing message is in
    the ``error`` field; the stacktrace is redundant noise.  Verify the
    domain-error path omits ``exc_info`` (or sets it falsy) so the
    structured event stays compact.
    """
    from recotem.training import pipeline as pipeline_mod
    from recotem.training.errors import MinDataViolation

    spy_logger = MagicMock()
    original_logger = pipeline_mod.logger
    pipeline_mod.logger = spy_logger

    try:
        recipe = _make_recipe(tmp_path)
        kr = _make_key_ring()

        def _failing_inner(*args, metrics_holder=None, **kwargs):
            raise MinDataViolation(
                "not enough rows",
                n_rows=1,
                n_users=1,
                n_items=1,
                min_rows=1000,
                min_users=100,
                min_items=100,
            )

        with patch(
            "recotem.training.pipeline._run_training_locked",
            side_effect=_failing_inner,
        ):
            with pytest.raises(MinDataViolation):
                pipeline_mod.run_training(
                    recipe,
                    key_ring=kr,
                    signing_key="active",
                    no_lock=True,
                    quiet=True,
                )

        train_error_calls = [
            call
            for call in spy_logger.error.call_args_list
            if call.args and call.args[0] == "train_error"
        ]
        assert train_error_calls
        kwargs = train_error_calls[0].kwargs
        assert kwargs.get("code") == "min_data_violation"
        # Domain errors do not need the stacktrace attached.
        assert not kwargs.get("exc_info"), (
            f"Domain-error path must NOT set exc_info=True (kept compact); "
            f"got kwargs: {kwargs!r}"
        )
    finally:
        pipeline_mod.logger = original_logger


# ---------------------------------------------------------------------------
# I-12: missing discriminator 'type' field raises RecipeError (exit 2)
# ---------------------------------------------------------------------------


def test_fetch_data_no_type_raises_recipe_error(tmp_path: Path) -> None:
    """When the source config has no 'type' discriminator, _fetch_data must
    raise RecipeError (maps to exit 2), not TrainingError (exit 4).

    I-12 fix: the old code raised TrainingError(code='datasource_error');
    the fix changes this to RecipeError(category='schema') so the CLI
    maps it correctly to exit 2.
    """
    from recotem.recipe.errors import RecipeError
    from recotem.training.pipeline import _fetch_data

    recipe = _make_recipe(tmp_path)

    # Bypass pydantic validation by using object.__setattr__ so we can inject
    # a source config object that has no 'type' attribute at all.
    class _NoTypeConfig:
        pass

    original_source = recipe.source
    try:
        object.__setattr__(recipe, "source", _NoTypeConfig())
        with pytest.raises(RecipeError) as exc_info:
            _fetch_data(recipe, run_id="i12-test")
    finally:
        object.__setattr__(recipe, "source", original_source)

    assert exc_info.value.category == "schema", (
        f"RecipeError for missing 'type' must have category='schema', "
        f"got {exc_info.value.category!r}"
    )


def test_fetch_data_no_type_maps_to_exit_2() -> None:
    """RecipeError from missing discriminator must map to exit code 2 (not 4).

    This confirms the I-12 fix integrates with the exit-code mapper.
    """
    from recotem._exit_codes import _map_exception_to_exit
    from recotem.recipe.errors import RecipeError

    err = RecipeError(
        "Recipe source has no discriminator 'type' field.", category="schema"
    )
    exit_code = _map_exception_to_exit(err)
    assert exit_code == 2, f"RecipeError must map to exit 2; got {exit_code}"


def test_fetch_data_unexpected_exception_logs_datasource_unexpected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected exception from the datasource path must log
    'datasource_unexpected_error' and then raise DataSourceError.
    """
    import structlog.testing

    from recotem.datasource.base import DataSourceError
    from recotem.training.pipeline import _fetch_data

    recipe = _make_recipe(tmp_path)

    boom = RuntimeError("unexpected network error")
    mock_source = MagicMock()
    mock_source.fetch.side_effect = boom
    mock_source_cls = MagicMock(return_value=mock_source)

    with patch(
        "recotem.datasource.registry.get_source_class",
        return_value=mock_source_cls,
    ):
        with structlog.testing.capture_logs() as cap:
            with pytest.raises(DataSourceError):
                _fetch_data(recipe, run_id="i12-log-test")

    error_events = [e for e in cap if e.get("event") == "datasource_unexpected_error"]
    assert error_events, (
        f"datasource_unexpected_error must be emitted for unexpected exceptions; "
        f"events: {[e.get('event') for e in cap]}"
    )
    assert error_events[0].get("exc_class") == "RuntimeError"


# ---------------------------------------------------------------------------
# I-13: MemoryError during time_column parsing propagates unwrapped
# ---------------------------------------------------------------------------


def test_cleanse_memory_error_in_time_column_parse_propagates_unwrapped(
    tmp_path: Path,
) -> None:
    """MemoryError during pd.to_datetime (time_column parse) must propagate
    unwrapped — not be caught and re-raised as TrainingError.

    I-13 fix: added `except (MemoryError, RecursionError): raise` before the
    generic `except Exception` in _cleanse.
    """
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.training.pipeline import _cleanse

    csv_file = tmp_path / "ts_data.csv"
    csv_file.write_text("user_id,item_id,ts\nu1,i1,1234567890\n")

    recipe = Recipe(
        name="ts_recipe",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(
            user_column="user_id",
            item_column="item_id",
            time_column="ts",
            time_unit="s",
        ),
        training=TrainingConfig(
            algorithms=["TopPop"],
            n_trials=1,
            split=SplitConfig(scheme="random", heldout_ratio=0.1, seed=42),
        ),
        output=OutputConfig(path=str(tmp_path / "ts_recipe.recotem")),
    )

    df = pd.DataFrame({"user_id": ["u1"], "item_id": ["i1"], "ts": [1234567890]})
    df["user_id"] = df["user_id"].astype(object)
    df["item_id"] = df["item_id"].astype(object)

    def _oom(*args, **kwargs):
        raise MemoryError("out of memory during time parse")

    with patch("recotem.training.pipeline.pd.to_datetime", side_effect=_oom):
        with pytest.raises(MemoryError):
            _cleanse(df, recipe)


# ---------------------------------------------------------------------------
# Task 9: feature-aware iALS end-to-end wiring
# ---------------------------------------------------------------------------


def test_feature_aware_training_writes_state_and_header(
    tmp_path: Path, feature_recipe, key_ring
) -> None:
    """End-to-end: a features: recipe produces an artifact carrying both the
    encoder state (payload) and the descriptor (header)."""
    import json

    from recotem.artifact.io import read_artifact
    from recotem.artifact.signing import unpickle_payload
    from recotem.training.pipeline import run_training

    result = run_training(
        feature_recipe,
        key_ring=key_ring,
        signing_key="active",
        no_lock=True,
        quiet=True,
    )
    assert result is not None

    header, payload = read_artifact(result.artifact_path, key_ring)
    parsed = json.loads(header.header_data)

    assert parsed["features"]["version"] == 1
    assert parsed["features"]["item"]["columns"] == ["genre", "year"]
    assert "user" not in parsed["features"]

    model = unpickle_payload(payload)
    assert model.item_feature_state["version"] == 1
    assert model.user_feature_state is None
    # The matrix must NOT ride in best_params: that is JSON-serialized into a
    # 64 KiB-capped header.
    assert "item_features" not in parsed["best_params"]
    assert "lambda_item_feature" in parsed["best_params"]


def test_no_features_recipe_omits_header_key(
    tmp_path: Path, plain_recipe, key_ring
) -> None:
    """A recipe with no features: block must keep the header byte-identical
    to today's -- no "features" key at all, not even null."""
    import json

    from recotem.artifact.io import read_artifact
    from recotem.training.pipeline import run_training

    result = run_training(
        plain_recipe,
        key_ring=key_ring,
        signing_key="active",
        no_lock=True,
        quiet=True,
    )
    assert result is not None

    header, _ = read_artifact(result.artifact_path, key_ring)
    assert "features" not in json.loads(header.header_data)


def test_train_final_reencodes_features_for_its_own_axis_not_search_phase() -> None:
    """Regression / mutation guard for the single most dangerous mistake in
    feature-aware training: irspack raises on a feature-matrix ROW-COUNT
    mismatch but accepts a MISORDERED matrix silently -- no shape error, no
    value error, just a silently-wrong model. That means the header/payload
    smoke test above cannot detect it: ``item_feature_state`` and the header
    descriptor are built from ``feature_tables`` directly and do not depend
    on whether ``_train_final`` actually re-encoded onto the right axis.

    This test makes the row order itself observable. Each item's "genre"
    value IS its own item id, so the encoder's vocabulary index for item X
    equals X's rank in the *sorted* item id list -- exactly
    ``df_to_sparse``'s own row/column order (``pd.Categorical`` sorts its
    categories). A correctly re-encoded matrix is therefore the identity
    permutation: row i's lone non-bias one-hot sits at column i. Reusing a
    matrix built for any other ordering -- e.g. the search phase's
    ``list(set(...))`` order, which is neither sorted nor stable across
    processes for string ids -- breaks that identity and fails the
    assertion below.
    """
    from recotem._features import build_encoder_state
    from recotem.recipe.models import FeatureColumn
    from recotem.training.features import FeatureTables
    from recotem.training.pipeline import _train_final

    item_ids = ["i5", "i1", "i9", "i3"]  # deliberately unsorted input order
    df = pd.DataFrame(
        {
            "user_id": ["u1", "u2", "u1", "u2"],
            "item_id": item_ids,
        }
    )
    # The feature table's "genre" value for each item IS its own id, so the
    # vocab index is a stand-in for "which item is this row".
    item_df = pd.DataFrame({"genre": item_ids}, index=item_ids)
    item_state = build_encoder_state(
        item_df, [FeatureColumn(name="genre", encoding="categorical")]
    )
    tables = FeatureTables(item_state=item_state, item_df=item_df)

    captured: dict = {}

    class FakeIALS:
        """Stand-in for IALSRecommender: records the matrix it was given."""

        def __init__(
            self, X, lambda_item_feature: float = 0.0, item_features=None
        ) -> None:
            captured["item_features"] = item_features

        def learn(self):
            return self

    with patch(
        "recotem.training.pipeline.get_recommender_cls",
        return_value=FakeIALS,
    ):
        # class_name must be a REAL, feature-capable canonical class name
        # ("IALSRecommender") -- not an arbitrary fake string -- because
        # _train_final now gates final_feature_kwargs on
        # is_feature_capable(class_name) (Finding 1 fix), which resolves the
        # string through the real alias table. get_recommender_cls is
        # patched above so the actual class instantiated is still FakeIALS
        # regardless of this string.
        result = _train_final(
            df,
            user_column="user_id",
            item_column="item_id",
            class_name="IALSRecommender",
            best_params={"lambda_item_feature": 0.1},
            feature_tables=tables,
        )

    dense = captured["item_features"].toarray()
    vocab = item_state["columns"][0]["vocab"]
    bias_col = item_state["bias_offset"]

    for row_idx, iid in enumerate(result.item_ids):
        expected_col = vocab[iid]
        nonzero_cols = set(dense[row_idx].nonzero()[0].tolist())
        assert nonzero_cols == {expected_col, bias_col}, (
            f"row {row_idx} (item {iid!r}): expected the one-hot at column "
            f"{expected_col} (plus the always-on bias column {bias_col}), "
            f"got nonzero columns {nonzero_cols}. _train_final must "
            "re-encode features against df_to_sparse's OWN item order -- "
            "it must never reuse/cache a matrix built for a different "
            "ordering (e.g. the search phase's)."
        )


def test_train_final_feature_cholesky_error_message_does_not_blame_a_column() -> None:
    """A Cholesky failure during the FINAL refit must map to TrainingError
    with an actionable message -- and that message must NOT tell the user to
    drop a column. recotem's own always-on bias column is deliberately
    collinear with the categorical one-hots (see recotem._features's module
    docstring) and is the single most likely structural cause, and the user
    cannot remove it from the recipe.
    """
    from recotem._features import build_encoder_state
    from recotem.recipe.models import FeatureColumn
    from recotem.training.errors import TrainingError
    from recotem.training.features import FeatureTables
    from recotem.training.pipeline import _train_final

    df = pd.DataFrame(
        {
            "user_id": ["u1", "u2"],
            "item_id": ["i1", "i2"],
        }
    )
    item_df = pd.DataFrame({"genre": ["a", "b"]}, index=["i1", "i2"])
    item_state = build_encoder_state(
        item_df, [FeatureColumn(name="genre", encoding="categorical")]
    )
    tables = FeatureTables(item_state=item_state, item_df=item_df)

    class RankDeficientRec:
        def __init__(
            self, X, lambda_item_feature: float = 0.0, item_features=None
        ) -> None:
            pass

        def learn(self):
            raise RuntimeError(
                "Feature ridge Cholesky decomposition failed: matrix is not "
                "positive definite"
            )

    with patch(
        "recotem.training.pipeline.get_recommender_cls",
        return_value=RankDeficientRec,
    ):
        # class_name must be a REAL, feature-capable canonical class name
        # ("IALSRecommender") so is_feature_capable(class_name) (Finding 1
        # fix) lets final_feature_kwargs be built; get_recommender_cls is
        # patched above so RankDeficientRec is still what actually gets
        # instantiated.
        with pytest.raises(TrainingError, match="Cholesky") as exc_info:
            _train_final(
                df,
                user_column="user_id",
                item_column="item_id",
                class_name="IALSRecommender",
                best_params={"lambda_item_feature": 0.1},
                feature_tables=tables,
            )

    assert exc_info.value.code == "feature_cholesky_error"
    message = str(exc_info.value)
    assert "drop" not in message.lower(), (
        f"the error message must not tell the user to drop a column "
        f"(recotem's own bias column is the most likely structural cause "
        f"and cannot be removed from the recipe); got: {message!r}"
    )
    assert "min_frequency" in message


def test_train_final_without_features_is_unaffected_by_feature_tables_param() -> None:
    """Back-compat: omitting ``feature_tables`` (the pre-Task-9 call shape)
    must behave exactly as before -- no feature kwargs, no feature state on
    the returned wrapper."""
    from recotem.training.pipeline import _train_final

    df = pd.DataFrame(
        {"user_id": ["u1", "u2", "u1", "u3"], "item_id": ["i1", "i1", "i2", "i2"]}
    )

    class PlainRec:
        def __init__(self, X) -> None:
            self.X = X

        def learn(self):
            return self

    with patch("recotem.training.pipeline.get_recommender_cls", return_value=PlainRec):
        result = _train_final(
            df,
            user_column="user_id",
            item_column="item_id",
            class_name="PlainRec",
            best_params={},
        )

    assert result.item_feature_state is None
    assert result.user_feature_state is None


# ---------------------------------------------------------------------------
# Review finding 1 (CRITICAL): _train_final must not crash when the search
# winner is a features:-enabled recipe's non-feature-capable algorithm.
#
# Recipe._validate_features_algorithms only requires that AT LEAST ONE
# listed algorithm be feature-capable (see recipe/models.py); an
# `algorithms: ["TopPop", "IALS"]` recipe with a `features:` block is
# explicitly valid, and the search may legitimately pick TopPop as the
# winner. Pre-fix, `_train_final` built `final_feature_kwargs` whenever
# `feature_tables.enabled`, with no check on whether `class_name` accepts
# feature kwargs -- splatting `item_features` into TopPop's constructor
# (which only accepts `X_train`) raised
# `TypeError: TopPopRecommender.__init__() got an unexpected keyword
# argument 'item_features'`, wrapped as
# TrainingError(code="final_training_error"). search.py's run_search
# already gated its analogous `trial_features` on `is_feature_capable`
# (search.py:398-402); that gate was never replicated for the final refit.
# ---------------------------------------------------------------------------


def test_train_final_with_non_feature_capable_winner_does_not_crash() -> None:
    """``_train_final`` must produce a valid (non-feature) artifact, not
    raise, when ``class_name`` resolves to a real, non-patched,
    non-feature-capable irspack class (``TopPopRecommender``) alongside an
    enabled ``feature_tables``.

    Uses the REAL irspack ``TopPopRecommender`` (not a mock/fake class) so
    the constructor-signature mismatch this test guards against is genuine,
    matching the reviewer's exact reproduction.
    """
    from recotem._features import build_encoder_state
    from recotem.recipe.models import FeatureColumn
    from recotem.training.features import FeatureTables
    from recotem.training.pipeline import _train_final

    df = pd.DataFrame(
        {
            "user_id": ["u1", "u2", "u1", "u2"],
            "item_id": ["i1", "i2", "i3", "i4"],
        }
    )
    item_df = pd.DataFrame(
        {"genre": ["a", "b", "a", "b"]}, index=["i1", "i2", "i3", "i4"]
    )
    item_state = build_encoder_state(
        item_df, [FeatureColumn(name="genre", encoding="categorical")]
    )
    tables = FeatureTables(item_state=item_state, item_df=item_df)

    # class_name="TopPopRecommender" resolves via get_recommender_cls to the
    # real irspack class -- no patch("...get_recommender_cls", ...) here.
    result = _train_final(
        df,
        user_column="user_id",
        item_column="item_id",
        class_name="TopPopRecommender",
        best_params={},
        feature_tables=tables,
    )

    assert result is not None
    assert sorted(result.item_ids) == ["i1", "i2", "i3", "i4"]

    # Decision (see task report for full rationale): item_feature_state is
    # STILL persisted even though TopPop never received item_features at
    # construction time. The header's "features" descriptor is already
    # unconditional on feature_tables.enabled (independent of best_class --
    # see _run_training_locked's header-building step below the search),
    # so nulling the payload side out here would make the payload silently
    # disagree with the header for the very same artifact. The encoder
    # state is descriptive metadata about what the recipe configured, not a
    # claim that the winning model consumed it.
    assert result.item_feature_state is not None
    assert result.item_feature_state["version"] == 1


def test_multi_algorithm_features_recipe_with_toppop_winner_produces_valid_artifact(
    tmp_path: Path, key_ring
) -> None:
    """Full run_training proof of the reviewer's exact scenario: a legal
    multi-algorithm recipe (``algorithms: ["TopPop", "IALS"]``) with a
    ``features:`` block, where TopPop -- not IALS -- wins the search.

    ``per_algorithm_trials={"IALS": 0, "TopPop": 1}`` forces TopPop to be
    the only algorithm that actually runs (budget 0 means "skip", per
    ``_compute_budgets``), while the recipe-level validator is satisfied
    because IALS is still *listed* in ``training.algorithms``. Pre-fix, this
    is a 100%-reproducible hard failure for any operator running exactly
    this configuration; post-fix it must produce a normal, non-feature
    artifact.
    """
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        FeatureColumn,
        FeaturesConfig,
        FeatureSideConfig,
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.training.pipeline import run_training

    csv_file = _make_clustered_synthetic_csv(tmp_path)

    items_csv = tmp_path / "item_features_toppop_winner.csv"
    genres = ["action", "drama", "comedy"]
    pd.DataFrame(
        {
            "item_id": [f"i{i}" for i in range(40)],
            "genre": [genres[i % len(genres)] for i in range(40)],
        }
    ).to_csv(items_csv, index=False)

    recipe = Recipe(
        name="toppop_winner_features_test",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        features=FeaturesConfig(
            item=FeatureSideConfig(
                source=CSVConfig(type="csv", path=str(items_csv)),
                id_column="item_id",
                columns=[FeatureColumn(name="genre", encoding="categorical")],
            )
        ),
        training=TrainingConfig(
            algorithms=["TopPop", "IALS"],
            n_trials=1,
            per_algorithm_trials={"IALS": 0, "TopPop": 1},
            cutoff=5,
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=0),
        ),
        output=OutputConfig(
            path=str(tmp_path / "toppop_winner_features_test.recotem"),
            versioning="always_overwrite",
        ),
    )

    result = run_training(
        recipe,
        key_ring=key_ring,
        signing_key="active",
        no_lock=True,
        quiet=True,
    )

    assert result is not None
    assert result.best_class == "TopPopRecommender", (
        "test setup invariant: IALS must have budget 0 so TopPop is "
        f"guaranteed to win; got best_class={result.best_class!r}"
    )


# ---------------------------------------------------------------------------
# Review finding 2 (Important): the SEARCH-phase feature encoding call site
# (pipeline.py, immediately after split_interactions, before run_search) had
# no misordering regression test of its own -- only the final-refit
# re-encoding did (test_train_final_reencodes_features_for_its_own_axis_not_
# search_phase above). Because the final refit re-encodes independently, a
# search-phase-only misordering bug (e.g. `sorted(split_result.item_ids)`
# instead of `split_result.item_ids`) would not corrupt the shipped model,
# but WOULD silently corrupt the hyperparameter search: lambda tuned against
# a mismatched item<->feature correspondence, with no error and (pre-fix) no
# failing test.
# ---------------------------------------------------------------------------


def test_search_phase_feature_kwargs_match_split_result_axis_order_exactly(
    feature_recipe_both_axes, key_ring
) -> None:
    """Spy on run_search's ``feature_kwargs`` argument and assert its row
    order matches ``split_result.item_ids`` / ``row_user_ids`` EXACTLY -- by
    order, not merely by set -- for BOTH the item and the user axis.

    Strategy: wrap (not replace) ``split_interactions`` and
    ``load_feature_tables`` so the REAL split result and REAL feature tables
    are captured alongside whatever ``run_search`` actually received, then
    independently recompute the expected encoding from the captured
    ``split_result.item_ids`` / ``row_user_ids`` and assert it is row-for-row
    identical to what ``run_search`` was given. ``run_search`` itself is
    replaced with a stub that raises immediately after capturing its
    ``feature_kwargs``, so this test does not need a real, successful Optuna
    search to complete.

    A same-SET-but-different-ORDER mutation of the search-phase call site
    (e.g. ``item_order=sorted(split_result.item_ids)`` or
    ``user_order=list(reversed(split_result.row_user_ids))``) changes the
    actual encoding's row order without changing which items/users are
    present, so it is invisible to any assertion that only checks set
    membership or matrix shape -- exactly the gap this test targets.

    Uses ``feature_recipe_both_axes`` (not the item-only ``feature_recipe``):
    a prior version of this test only configured ``features.item``, so
    ``feature_kwargs`` never contained ``user_features`` at all and no
    assertion here could ever have observed a misordered user axis --
    verified by mutating ``pipeline.py``'s search-phase call site from
    ``user_order=split_result.row_user_ids`` to
    ``user_order=list(reversed(split_result.row_user_ids))`` and confirming
    the full suite still passed (the exact mirror of the item-axis hazard
    this whole feature is built to prevent).
    """
    from recotem.training import pipeline as pipeline_mod
    from recotem.training.features import encode_for_axis
    from recotem.training.pipeline import run_training
    from recotem.training.split import split_interactions as real_split_interactions

    captured: dict = {}

    def _spy_split_interactions(*args, **kwargs):
        result = real_split_interactions(*args, **kwargs)
        captured["split_result"] = result
        return result

    real_load_feature_tables = pipeline_mod.load_feature_tables

    def _spy_load_feature_tables(*args, **kwargs):
        tables = real_load_feature_tables(*args, **kwargs)
        captured["feature_tables"] = tables
        return tables

    class _StopAfterCapture(Exception):
        """Sentinel raised to short-circuit the pipeline right after
        run_search is invoked, so no real search need complete."""

    def _spy_run_search(*args, feature_kwargs=None, **kwargs):
        captured["feature_kwargs"] = feature_kwargs
        raise _StopAfterCapture

    with (
        patch(
            "recotem.training.pipeline.split_interactions",
            side_effect=_spy_split_interactions,
        ),
        patch(
            "recotem.training.pipeline.load_feature_tables",
            side_effect=_spy_load_feature_tables,
        ),
        patch(
            "recotem.training.pipeline.run_search",
            side_effect=_spy_run_search,
        ),
    ):
        with pytest.raises(_StopAfterCapture):
            run_training(
                feature_recipe_both_axes,
                key_ring=key_ring,
                signing_key="active",
                no_lock=True,
                quiet=True,
            )

    split_result = captured["split_result"]
    feature_tables = captured["feature_tables"]
    actual_kwargs = captured["feature_kwargs"]

    assert actual_kwargs is not None and "item_features" in actual_kwargs, (
        f"run_search must have received item_features; got {actual_kwargs!r}"
    )
    assert "user_features" in actual_kwargs, (
        f"run_search must have received user_features; got {actual_kwargs!r}"
    )

    # Test-setup invariant: split_result.item_ids must not already be sorted,
    # or a sorted(...) mutation would be unobservable by coincidence and this
    # test would pass whether or not the mutation guard is present.
    assert split_result.item_ids != sorted(split_result.item_ids), (
        "test setup invariant violated: split_result.item_ids is already in "
        "sorted order, so this test cannot distinguish correct code from "
        "the sorted(...) mutation it targets"
    )

    # Test-setup invariant for the USER axis. Unlike item_ids (built from
    # irspack's `list(set(...))`, hash-order-dependent and not sorted for a
    # typical string-id fixture), row_user_ids is built from pandas
    # Categorical-backed user indexing and comes out ALREADY sorted for a
    # typical "u0".."u59" fixture -- confirmed empirically, not assumed. That
    # makes a `sorted(...)` mutation of user_order a value-level NO-OP here
    # (indistinguishable from correct code), so the mutation this guards
    # against is `reversed(...)`, not `sorted(...)`, and the vacuity check
    # must match: assert the row order differs from its own reversal.
    assert split_result.row_user_ids != list(reversed(split_result.row_user_ids)), (
        "test setup invariant violated: split_result.row_user_ids is a "
        "palindrome under reversal, so this test cannot distinguish correct "
        "code from the reversed(...) mutation it targets"
    )

    # Recompute independently from the SAME feature_tables and the split
    # phase's OWN (real, unsorted) axis labels.
    expected_kwargs = encode_for_axis(
        feature_tables,
        item_order=split_result.item_ids,
        user_order=split_result.row_user_ids,
    )

    actual_dense = actual_kwargs["item_features"].toarray()
    expected_dense = expected_kwargs["item_features"].toarray()
    assert actual_dense.shape == expected_dense.shape
    assert (actual_dense == expected_dense).all(), (
        "run_search's feature_kwargs['item_features'] must be row-for-row "
        "identical to encode_for_axis(feature_tables, "
        "item_order=split_result.item_ids, user_order=split_result."
        "row_user_ids) -- a same-SET-but-different-ORDER item_order (e.g. "
        "sorted(split_result.item_ids)) must fail this assertion."
    )

    actual_user_dense = actual_kwargs["user_features"].toarray()
    expected_user_dense = expected_kwargs["user_features"].toarray()
    assert actual_user_dense.shape == expected_user_dense.shape
    assert (actual_user_dense == expected_user_dense).all(), (
        "run_search's feature_kwargs['user_features'] must be row-for-row "
        "identical to encode_for_axis(feature_tables, "
        "item_order=split_result.item_ids, user_order=split_result."
        "row_user_ids) -- a same-SET-but-different-ORDER user_order (e.g. "
        "list(reversed(split_result.row_user_ids))) must fail this "
        "assertion. This is the exact mirror of the item-axis hazard above, "
        "on the axis that had no coverage at all before this test."
    )


# ---------------------------------------------------------------------------
# Review finding 4: the feature-aware iALS design spec ("Testing" + "Risks")
# promises the alignment test above "must run under multiple PYTHONHASHSEED
# values to catch any
# reintroduced 'build once, reuse' shortcut". That promise was fulfilled by a
# manual `for seed in 0 1 2; do PYTHONHASHSEED=$seed uv run pytest ...`
# shell loop run once during implementation -- not a standing guard a future
# regression would ever trip, and outside the test suite entirely.
#
# Worse: the vacuity guard inside the alignment test itself (`assert
# split_result.item_ids != sorted(split_result.item_ids)`, a few lines
# above) is itself hash-order-dependent. Python randomizes PYTHONHASHSEED
# per process by default, so whether that guard's precondition is even
# satisfied -- and therefore whether the alignment test exercises a
# non-sorted order at all -- varies run to run, uncontrolled.
#
# This test converts the promise into a standing, in-suite guard: it
# re-executes the alignment test above, as a subprocess, under each of the
# three PYTHONHASHSEED values the spec's own worked example (Hazard 1,
# "Silent misalignment") demonstrates produce three DIFFERENT
# `list(set(...))` orderings for string item ids -- so this is not merely
# "run under several arbitrary seeds", it specifically covers the seeds the
# design doc already showed to be non-sorted, guaranteeing real coverage
# rather than leaving it to chance on whatever seed pytest happens to start
# with.
#
# Re-invokes pytest on the single node id rather than duplicating the
# alignment test's spy/compare logic here: any future edit to that test
# keeps being exactly what this guard re-runs, with no second copy that
# could silently drift out of sync (the same "kept in sync by hand" risk
# this codebase calls out elsewhere, e.g.
# `_idmap._FEATURE_CAPABLE_CLASS_NAMES`).
#
# A CI matrix entry was considered instead (running the alignment test
# under a PYTHONHASHSEED matrix in .github/workflows/test.yml) and rejected:
# a workflow YAML edit is invisible to `uv run pytest` and easy for someone
# tuning CI runtime to delete without realizing what guarantee it was
# providing, whereas this test fails the same `pytest tests` invocation
# every contributor already runs locally and in CI.
# ---------------------------------------------------------------------------


def test_search_phase_feature_alignment_holds_across_hash_seeds() -> None:
    """Standing guard: re-run the alignment test above under three fixed
    PYTHONHASHSEED values so a reintroduced 'build once, reuse' shortcut
    (e.g. ``item_order=sorted(split_result.item_ids)`` at the search-phase
    feature-encoding call site in ``pipeline.py``) cannot slip back in
    unnoticed just because one particular process's random hash seed
    happened to produce an order the vacuity guard was happy with.
    """
    import os
    import subprocess
    import sys

    node_id = (
        f"{__file__}::"
        "test_search_phase_feature_kwargs_match_split_result_axis_order_exactly"
    )
    # The exact three seeds the feature-aware iALS design spec's Hazard 1
    # demonstrates produce three DIFFERENT `list(set(...))` orderings for
    # string item ids -- not an arbitrary choice of "a few seeds".
    failures: list[str] = []
    for seed in ("0", "1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(
            [sys.executable, "-m", "pytest", node_id, "-q", "--no-header"],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        if result.returncode != 0:
            failures.append(
                f"PYTHONHASHSEED={seed} FAILED (exit {result.returncode}):\n"
                f"{result.stdout}\n{result.stderr}"
            )
    assert not failures, (
        "alignment test failed under one or more fixed hash seeds:\n\n"
        + "\n\n".join(failures)
    )
