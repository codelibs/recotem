"""Unit tests for the train_error log event in recotem.training.pipeline.

Tests:
- run_training emits train_error on data-source failure
- run_training does NOT emit train_error on success
- CLI train does not double-emit train_error (exactly once in stderr output)

The first two tests intercept structlog by directly replacing the module-level
logger inside ``recotem.training.pipeline``.  Going via
``structlog.testing.capture_logs()`` is brittle when other tests in the suite
have already triggered ``recotem.logging.configure_logging`` (which uses
``cache_logger_on_first_use=True``), because the cached BoundLogger holds its
own processor chain that ``capture_logs`` cannot see into.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ACTIVE_KEY_HEX = "aa" * 32


def _make_key_ring():
    from recotem.artifact.signing import KeyRing

    return KeyRing(f"active:{ACTIVE_KEY_HEX}")


def _make_recipe_bad_csv(tmp_path: Path):
    """Recipe pointing at a non-existent CSV (will fail at fetch stage)."""
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        TrainingConfig,
    )

    return Recipe(
        name="bad_recipe",
        source=CSVConfig(type="csv", path=str(tmp_path / "does_not_exist.csv")),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(algorithms=["TopPop"], n_trials=1),
        output=OutputConfig(path=str(tmp_path / "bad_recipe.recotem")),
    )


def _make_recipe_good(tmp_path: Path):
    """Recipe pointing at a valid tiny CSV (will succeed via mocked _run_training_locked)."""
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        TrainingConfig,
    )

    csv_file = tmp_path / "data.csv"
    if not csv_file.exists():
        csv_file.write_text("user_id,item_id\nu1,i1\nu2,i2\n")

    return Recipe(
        name="good_recipe",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(algorithms=["TopPop"], n_trials=1),
        output=OutputConfig(path=str(tmp_path / "good_recipe.recotem")),
    )


# ---------------------------------------------------------------------------
# D1. run_training emits train_error on data-source failure
# ---------------------------------------------------------------------------


def test_run_training_emits_train_error_event_on_data_source_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the data source fails (missing CSV), run_training must emit a
    train_error log event with recipe, run_id, error, code, trained_at fields.
    """
    from recotem.datasource.base import DataSourceError
    from recotem.training import pipeline as pipeline_mod

    spy_logger = MagicMock()
    monkeypatch.setattr(pipeline_mod, "logger", spy_logger)

    recipe = _make_recipe_bad_csv(tmp_path)
    kr = _make_key_ring()

    with pytest.raises(DataSourceError):
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
    assert train_error_calls, (
        "train_error log event must be emitted when training fails. "
        f"All error() calls: {spy_logger.error.call_args_list}"
    )
    kwargs = train_error_calls[0].kwargs
    # CLI-2: train_error must use 'name' (not 'recipe') to match train_done schema.
    assert kwargs.get("name") == "bad_recipe", (
        f"train_error must use key 'name', not 'recipe'; got keys: {list(kwargs)}"
    )
    assert "recipe" not in kwargs, (
        f"train_error must NOT use key 'recipe' (schema drift); got keys: {list(kwargs)}"
    )
    assert "run_id" in kwargs
    assert "error" in kwargs
    assert "code" in kwargs
    assert "trained_at" in kwargs


# ---------------------------------------------------------------------------
# D2. run_training does NOT emit train_error on success
# ---------------------------------------------------------------------------


def test_run_training_does_not_emit_train_error_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When training succeeds, no train_error event must be emitted."""
    from recotem.training import pipeline as pipeline_mod

    spy_logger = MagicMock()
    monkeypatch.setattr(pipeline_mod, "logger", spy_logger)

    recipe = _make_recipe_good(tmp_path)
    kr = _make_key_ring()

    fake_result = MagicMock(spec=pipeline_mod.TrainResult)

    with patch(
        "recotem.training.pipeline._run_training_locked",
        return_value=fake_result,
    ):
        result = pipeline_mod.run_training(
            recipe,
            key_ring=kr,
            signing_key="active",
            no_lock=True,
            quiet=True,
        )

    assert result is fake_result
    train_error_calls = [
        call
        for call in spy_logger.error.call_args_list
        if call.args and call.args[0] == "train_error"
    ]
    assert not train_error_calls, (
        f"train_error must NOT be emitted on success; got {train_error_calls}"
    )


# ---------------------------------------------------------------------------
# D4. train_done event carries all canonical fields (T-5)
# ---------------------------------------------------------------------------


def test_run_training_emits_train_done_event_with_canonical_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On successful training, a train_done log event must be emitted exactly
    once with all canonical fields: name, run_id, best_class, best_score,
    artifact (path), trained_at, kid, trials, exit_code.

    We call _run_training_locked directly and mock out the heavyweight steps
    (data fetch, split, search, final training, artifact write) so the test
    is fast and deterministic.  The goal is to verify that the log emit
    statement inside _run_training_locked carries the correct keys.
    """
    import numpy as np
    import scipy.sparse as sps

    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.training import pipeline as pipeline_mod
    from recotem.training.pipeline import _run_training_locked
    from recotem.training.search import SearchResult

    spy_logger = MagicMock()
    monkeypatch.setattr(pipeline_mod, "logger", spy_logger)

    csv_file = tmp_path / "train_done_data.csv"
    csv_file.write_text("user_id,item_id\nu1,i1\nu2,i2\n")

    recipe = Recipe(
        name="train_done_recipe",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=["TopPop"],
            n_trials=1,
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=42),
        ),
        output=OutputConfig(path=str(tmp_path / "train_done_recipe.recotem")),
    )

    kr = _make_key_ring()
    artifact_path = str(tmp_path / "train_done_recipe.recotem")

    fake_search_result = SearchResult(
        best_class_name="TopPopRecommender",
        best_params={},
        best_score=0.42,
        best_trial_number=0,
        tried_algorithms=["TopPopRecommender"],
        n_trials=1,
        n_completed=1,
        orphaned_count=0,
        search_seed=42,
    )

    fake_recommender = MagicMock()

    def _mock_write(payload_obj, header_dict, key_ring, fs_path, *, versioning):
        return artifact_path

    # Mock data fetch → tiny DataFrame.
    import pandas as pd

    mock_df = pd.DataFrame({"user_id": ["u1", "u2"], "item_id": ["i1", "i2"]})

    X_sparse = sps.csr_matrix(np.ones((2, 2)))

    with (
        patch("recotem.training.pipeline._fetch_data", return_value=mock_df),
        patch(
            "recotem.training.pipeline.split_interactions",
            return_value=(X_sparse, X_sparse, 1),
        ),
        patch("recotem.training.pipeline.build_evaluator", return_value=MagicMock()),
        patch("recotem.training.pipeline.run_search", return_value=fake_search_result),
        patch("recotem.training.pipeline._train_final", return_value=fake_recommender),
    ):
        _run_training_locked(
            recipe=recipe,
            key_ring=kr,
            signing_key="active",
            write_artifact_fn=_mock_write,
            quiet=True,
            verbose=False,
            run_id="canonical-run-id",
        )

    # Inspect the train_done calls via the spy logger.
    train_done_calls = [
        call
        for call in spy_logger.info.call_args_list
        if call.args and call.args[0] == "train_done"
    ]
    assert len(train_done_calls) == 1, (
        f"train_done must be emitted exactly once; got {len(train_done_calls)} calls. "
        f"All info() calls: {spy_logger.info.call_args_list}"
    )

    kwargs = train_done_calls[0].kwargs
    required_fields = {
        "name",
        "run_id",
        "best_class",
        "best_score",
        "artifact",
        "trained_at",
        "kid",
        "trials",
        "exit_code",
    }
    missing = required_fields - set(kwargs)
    assert not missing, (
        f"train_done event is missing canonical fields: {missing}. "
        f"Present fields: {set(kwargs)}"
    )
    assert kwargs["exit_code"] == 0
    assert kwargs["name"] == "train_done_recipe"
    assert kwargs["run_id"] == "canonical-run-id"
    assert kwargs["kid"] == "active"
    assert kwargs["best_class"] == "TopPopRecommender"
    assert kwargs["best_score"] == 0.42
    assert kwargs["artifact"] == artifact_path


# ---------------------------------------------------------------------------
# D5. Non-domain errors emit code='internal_error' (m-6)
# ---------------------------------------------------------------------------


def test_train_error_internal_error_code_for_keyerror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an unexpected non-domain exception (e.g. KeyError) propagates through
    run_training, the train_error log event must carry code='internal_error'
    rather than the meaningless exception class name 'KeyError'.
    """
    from recotem.training import pipeline as pipeline_mod

    spy_logger = MagicMock()
    monkeypatch.setattr(pipeline_mod, "logger", spy_logger)

    recipe = _make_recipe_good(tmp_path)
    kr = _make_key_ring()

    # Inject a raw KeyError from _run_training_locked.
    with patch(
        "recotem.training.pipeline._run_training_locked",
        side_effect=KeyError("unexpected_key"),
    ):
        with pytest.raises(KeyError):
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
    assert train_error_calls, "train_error must be emitted on KeyError"
    code = train_error_calls[0].kwargs.get("code")
    assert code == "internal_error", (
        f"Expected code='internal_error' for non-domain KeyError, got {code!r}"
    )


# ---------------------------------------------------------------------------
# D3. CLI train does not double-emit train_error (exactly once)
# ---------------------------------------------------------------------------


def test_cli_train_does_not_double_emit_train_error(
    tmp_path: Path,
) -> None:
    """When 'recotem train' is run via Typer CliRunner with a bad recipe,
    exactly one train_error event must appear in the structured log output.

    The CLI must not re-emit the error after run_training already emitted it.
    """
    import os

    from typer.testing import CliRunner

    from recotem.cli import app

    recipe_yaml = tmp_path / "bad.yaml"
    recipe_yaml.write_text(
        f"""\
name: cli_bad_recipe
source:
  type: csv
  path: {tmp_path / "nonexistent.csv"}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {tmp_path / "cli_bad_recipe.recotem"}
"""
    )

    runner = CliRunner()
    env = {
        **os.environ,
        "RECOTEM_SIGNING_KEYS": f"active:{ACTIVE_KEY_HEX}",
        "RECOTEM_LOG_FORMAT": "json",
    }

    result = runner.invoke(
        app,
        ["train", str(recipe_yaml), "--no-lock"],
        env=env,
        catch_exceptions=True,
    )

    # Parse JSON log lines from the captured stderr/stdout.  Each well-formed
    # JSON object with ``event == "train_error"`` counts as one emission.
    train_error_count = 0
    for line in result.output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "train_error":
            train_error_count += 1

    assert train_error_count == 1, (
        f"train_error must appear exactly once (not double-emitted); "
        f"got {train_error_count}. Full CLI output:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# CLI-2: train_error event uses 'name' (not 'recipe') + includes 'kid' when known
# ---------------------------------------------------------------------------


def test_run_training_emits_train_error_event_with_canonical_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """train_error event must use 'name' key (not 'recipe') and include 'kid'
    when the signing key was resolved before the failure.

    This is the regression test for CLI-2: the old code used 'recipe=recipe.name'
    which caused SIEM join failures when correlating with 'train_done' events
    that used 'name=recipe.name'.
    """
    from recotem.training import pipeline as pipeline_mod

    spy_logger = MagicMock()
    monkeypatch.setattr(pipeline_mod, "logger", spy_logger)

    recipe = _make_recipe_good(tmp_path)
    kr = _make_key_ring()

    # Inject a failure AFTER the KeyRing is resolved (inside _run_training_locked)
    # so 'kid' should be available in the train_error event.
    from recotem.datasource.base import DataSourceError

    with patch(
        "recotem.training.pipeline._run_training_locked",
        side_effect=DataSourceError("simulated data failure"),
    ):
        with pytest.raises(DataSourceError):
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
    assert train_error_calls, "train_error must be emitted on DataSourceError"

    kwargs = train_error_calls[0].kwargs

    # CLI-2 regression: key must be 'name', NOT 'recipe'
    assert "name" in kwargs, (
        f"train_error event must use key 'name' (not 'recipe'); "
        f"present keys: {list(kwargs)}"
    )
    assert "recipe" not in kwargs, (
        f"train_error event must NOT contain key 'recipe' (schema drift fix); "
        f"present keys: {list(kwargs)}"
    )
    assert kwargs["name"] == "good_recipe"

    # CLI-2: 'kid' must be present when the signing key was resolved
    assert "kid" in kwargs, (
        f"train_error must include 'kid' when key_ring was resolved; "
        f"present keys: {list(kwargs)}"
    )
    assert kwargs["kid"] == "active", (
        f"kid must match the resolved signing_key; got {kwargs['kid']!r}"
    )

    # Other canonical fields must also be present
    for field in ("run_id", "error", "code", "exit_code", "trained_at"):
        assert field in kwargs, (
            f"train_error must include '{field}'; present keys: {list(kwargs)}"
        )


def test_run_training_train_error_omits_kid_when_key_resolution_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When signing key resolution fails (signing_key_missing), 'kid' must be
    omitted from the train_error event — it was never resolved.
    """
    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)

    from recotem.training import pipeline as pipeline_mod
    from recotem.training.errors import TrainingError

    spy_logger = MagicMock()
    monkeypatch.setattr(pipeline_mod, "logger", spy_logger)

    recipe = _make_recipe_good(tmp_path)

    with pytest.raises(TrainingError) as exc_info:
        pipeline_mod.run_training(
            recipe,
            key_ring=None,  # forces auto-build from env
            no_lock=True,
            quiet=True,
        )

    assert exc_info.value.code == "signing_key_missing"

    train_error_calls = [
        call
        for call in spy_logger.error.call_args_list
        if call.args and call.args[0] == "train_error"
    ]
    assert train_error_calls, "train_error must be emitted on signing_key_missing"
    kwargs = train_error_calls[0].kwargs

    # 'name' must be present
    assert "name" in kwargs
    # 'kid' must be absent (key was never resolved)
    assert "kid" not in kwargs, (
        f"kid must be omitted when key resolution failed; present keys: {list(kwargs)}"
    )
