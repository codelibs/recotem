"""Additional tests for MAJOR-1: train_error event schema compliance.

Tests:
- exit_code field is present in every train_error emission
- MinDataViolation lifts n_rows, n_users, n_items, min_rows, min_users, min_items
- DataSourceError exit_code maps to 3
- TrainingError exit_code maps to 4
- Internal (non-domain) error exit_code maps to 1
"""

from __future__ import annotations

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
# T1-1: exit_code present in every train_error emission
# ---------------------------------------------------------------------------


def test_train_error_has_exit_code_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """train_error log event must include exit_code regardless of error type."""
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
    assert train_error_calls, "train_error must be emitted"
    kwargs = train_error_calls[0].kwargs
    assert "exit_code" in kwargs, (
        f"exit_code must be present in train_error event; got keys: {list(kwargs)}"
    )
    # DataSourceError maps to exit 3
    assert kwargs["exit_code"] == 3, (
        f"DataSourceError must map to exit_code=3, got {kwargs['exit_code']}"
    )


def test_train_error_exit_code_for_training_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TrainingError maps to exit_code=4."""
    from recotem.training import pipeline as pipeline_mod
    from recotem.training.errors import TrainingError

    spy_logger = MagicMock()
    monkeypatch.setattr(pipeline_mod, "logger", spy_logger)

    recipe = _make_recipe_good(tmp_path)
    kr = _make_key_ring()

    with patch(
        "recotem.training.pipeline._run_training_locked",
        side_effect=TrainingError("deliberate training failure"),
    ):
        with pytest.raises(TrainingError):
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
    assert train_error_calls[0].kwargs.get("exit_code") == 4


def test_train_error_exit_code_for_internal_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-domain errors map to exit_code=1 (unknown)."""
    from recotem.training import pipeline as pipeline_mod

    spy_logger = MagicMock()
    monkeypatch.setattr(pipeline_mod, "logger", spy_logger)

    recipe = _make_recipe_good(tmp_path)
    kr = _make_key_ring()

    with patch(
        "recotem.training.pipeline._run_training_locked",
        side_effect=KeyError("unexpected"),
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
    assert train_error_calls
    assert train_error_calls[0].kwargs.get("exit_code") == 1


# ---------------------------------------------------------------------------
# T1-2: MinDataViolation lifts extra fields
# ---------------------------------------------------------------------------


def test_train_error_min_data_violation_lifts_extra_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MinDataViolation must lift n_rows, n_users, n_items and min_* fields."""
    from recotem.training import pipeline as pipeline_mod
    from recotem.training.errors import MinDataViolation

    spy_logger = MagicMock()
    monkeypatch.setattr(pipeline_mod, "logger", spy_logger)

    recipe = _make_recipe_good(tmp_path)
    kr = _make_key_ring()

    exc = MinDataViolation(
        "too few rows",
        n_rows=842,
        n_users=50,
        n_items=20,
        min_rows=1000,
        min_users=None,
        min_items=None,
    )

    with patch(
        "recotem.training.pipeline._run_training_locked",
        side_effect=exc,
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
    assert kwargs.get("n_rows") == 842, f"n_rows not lifted: {kwargs}"
    assert kwargs.get("n_users") == 50
    assert kwargs.get("n_items") == 20
    assert kwargs.get("min_rows") == 1000
    # min_users and min_items are None so they should not be in the payload
    assert "min_users" not in kwargs
    assert "min_items" not in kwargs
    assert kwargs.get("code") == "min_data_violation"
    assert kwargs.get("exit_code") == 4
