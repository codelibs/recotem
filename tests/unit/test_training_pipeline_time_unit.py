"""Tests for MAJOR-3: time_unit field and numeric time column handling.

Tests:
- time_unit not set + numeric time column raises TrainingError(code=time_unit_required)
- time_unit='s' + numeric time column is correctly parsed
- time_unit='ms' + numeric time column is correctly parsed
- string time column works without time_unit
- datetime time column works without time_unit
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from recotem.training.errors import TrainingError


def _make_recipe(tmp_path: Path, *, time_unit: str | None, csv_content: str):
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        TrainingConfig,
    )

    csv_file = tmp_path / "data_tu.csv"
    csv_file.write_text(csv_content)

    return Recipe(
        name="time_unit_recipe",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(
            user_column="user_id",
            item_column="item_id",
            time_column="ts",
            time_unit=time_unit,
        ),
        training=TrainingConfig(algorithms=["TopPop"], n_trials=1),
        output=OutputConfig(path=str(tmp_path / "time_unit_recipe.recotem")),
    )


# ---------------------------------------------------------------------------
# T3-1: numeric time column without time_unit raises TrainingError
# ---------------------------------------------------------------------------


def test_numeric_time_column_without_time_unit_raises_training_error(
    tmp_path: Path,
) -> None:
    """A numeric ts column without schema.time_unit must raise TrainingError
    with code='time_unit_required' rather than silently using ns interpretation.
    """
    from recotem.training.pipeline import _cleanse

    recipe = _make_recipe(
        tmp_path,
        time_unit=None,
        csv_content="user_id,item_id,ts\nu1,i1,1700000000\nu2,i2,1700000100\n",
    )
    df = pd.read_csv(str(tmp_path / "data_tu.csv"))

    with pytest.raises(TrainingError) as exc_info:
        _cleanse(df, recipe)

    assert exc_info.value.code == "time_unit_required", (
        f"Expected code='time_unit_required', got {exc_info.value.code!r}"
    )
    assert "time_unit" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# T3-2: time_unit='s' correctly parses Unix epoch seconds
# ---------------------------------------------------------------------------


def test_numeric_time_column_with_time_unit_s_parsed_correctly(
    tmp_path: Path,
) -> None:
    """With time_unit='s', a numeric ts column must be parsed as seconds since
    Unix epoch (not nanoseconds).
    """
    from recotem.training.pipeline import _cleanse

    # 2023-11-14 22:13:20 UTC
    unix_ts = 1700000000

    recipe = _make_recipe(
        tmp_path,
        time_unit="s",
        csv_content=f"user_id,item_id,ts\nu1,i1,{unix_ts}\nu2,i2,{unix_ts + 100}\n",
    )
    df = pd.read_csv(str(tmp_path / "data_tu.csv"))

    result_df, _ = _cleanse(df, recipe)

    expected_ts = pd.Timestamp(unix_ts, unit="s", tz="UTC")
    parsed_ts = result_df["ts"].iloc[0]
    assert parsed_ts == expected_ts, (
        f"Expected {expected_ts}, got {parsed_ts}. "
        "time_unit='s' must interpret values as Unix epoch seconds."
    )
    # Verify the year is correct (not 1970 from ns misinterpretation)
    assert parsed_ts.year == 2023, (
        f"year should be 2023, got {parsed_ts.year}. "
        "ns misinterpretation would put this near 1970."
    )


# ---------------------------------------------------------------------------
# T3-3: time_unit='ms' correctly parses milliseconds
# ---------------------------------------------------------------------------


def test_numeric_time_column_with_time_unit_ms_parsed_correctly(
    tmp_path: Path,
) -> None:
    """With time_unit='ms', a numeric ts column must be parsed as ms since epoch."""
    from recotem.training.pipeline import _cleanse

    unix_ms = 1700000000000  # same moment in milliseconds

    recipe = _make_recipe(
        tmp_path,
        time_unit="ms",
        csv_content=f"user_id,item_id,ts\nu1,i1,{unix_ms}\nu2,i2,{unix_ms + 1000}\n",
    )
    df = pd.read_csv(str(tmp_path / "data_tu.csv"))

    result_df, _ = _cleanse(df, recipe)

    expected_ts = pd.Timestamp(unix_ms, unit="ms", tz="UTC")
    parsed_ts = result_df["ts"].iloc[0]
    assert parsed_ts == expected_ts
    assert parsed_ts.year == 2023


# ---------------------------------------------------------------------------
# T3-4: string time column works without time_unit
# ---------------------------------------------------------------------------


def test_string_time_column_without_time_unit_works(
    tmp_path: Path,
) -> None:
    """String/ISO datetime columns must work unchanged when time_unit is None."""
    from recotem.training.pipeline import _cleanse

    recipe = _make_recipe(
        tmp_path,
        time_unit=None,
        csv_content="user_id,item_id,ts\nu1,i1,2023-11-14T22:13:20Z\nu2,i2,2023-11-14T22:15:00Z\n",
    )
    df = pd.read_csv(str(tmp_path / "data_tu.csv"))

    # Must not raise
    result_df, _ = _cleanse(df, recipe)
    assert pd.api.types.is_datetime64_any_dtype(result_df["ts"])
    assert result_df["ts"].iloc[0].year == 2023


# ---------------------------------------------------------------------------
# T3-5: time_unit field in recipe model validates allowed values
# ---------------------------------------------------------------------------


def test_time_unit_field_rejects_invalid_value() -> None:
    """schema.time_unit must only accept 's', 'ms', 'us', 'ns'."""
    from pydantic import ValidationError

    from recotem.recipe.models import SchemaConfig

    with pytest.raises(ValidationError):
        SchemaConfig(
            user_column="user_id",
            item_column="item_id",
            time_column="ts",
            time_unit="minutes",  # invalid
        )


def test_time_unit_field_accepts_valid_values() -> None:
    """schema.time_unit accepts 's', 'ms', 'us', 'ns'."""
    from recotem.recipe.models import SchemaConfig

    for unit in ("s", "ms", "us", "ns"):
        cfg = SchemaConfig(
            user_column="user_id",
            item_column="item_id",
            time_column="ts",
            time_unit=unit,
        )
        assert cfg.time_unit == unit
