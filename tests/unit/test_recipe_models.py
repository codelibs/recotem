"""Unit tests for recotem.recipe.models pydantic strictness.

Tests:
- time_user without time_column raises
- heldout_ratio bounds
- n_trials zero rejected
- fields empty list rejected
- unknown extra fields rejected
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from recotem.recipe.models import (
    CleansingConfig,
    ItemMetadataConfig,
    OutputConfig,
    Recipe,
    SchemaConfig,
    SplitConfig,
    TrainingConfig,
)


def _minimal_recipe_dict(**overrides) -> dict:
    base = {
        "name": "test",
        "source": {"type": "csv", "path": "/tmp/data.csv"},
        "schema": {"user_column": "user_id", "item_column": "item_id"},
        "training": {
            "algorithms": ["TopPop"],
            "n_trials": 1,
        },
        "output": {"path": "/tmp/out.recotem"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# name regex
# ---------------------------------------------------------------------------


def test_recipe_name_with_slash_raises_validation_error() -> None:
    d = _minimal_recipe_dict(name="bad/name")
    with pytest.raises(ValidationError):
        Recipe.model_validate(d)


def test_recipe_name_over_64_chars_raises_validation_error() -> None:
    d = _minimal_recipe_dict(name="a" * 65)
    with pytest.raises(ValidationError):
        Recipe.model_validate(d)


def test_recipe_name_empty_raises_validation_error() -> None:
    d = _minimal_recipe_dict(name="")
    with pytest.raises(ValidationError):
        Recipe.model_validate(d)


def test_recipe_name_valid_alphanum_hyphens() -> None:
    d = _minimal_recipe_dict(name="valid-Name_1")
    r = Recipe.model_validate(d)
    assert r.name == "valid-Name_1"


# ---------------------------------------------------------------------------
# time_column requirement for time-based splits
# ---------------------------------------------------------------------------


def test_time_user_split_without_time_column_rejected() -> None:
    d = _minimal_recipe_dict()
    d["training"]["split"] = {"scheme": "time_user", "heldout_ratio": 0.1}
    with pytest.raises((ValidationError, ValueError)):
        Recipe.model_validate(d)


def test_time_global_split_without_time_column_rejected() -> None:
    d = _minimal_recipe_dict()
    d["training"]["split"] = {"scheme": "time_global", "heldout_ratio": 0.1}
    with pytest.raises((ValidationError, ValueError)):
        Recipe.model_validate(d)


def test_time_user_split_with_time_column_ok() -> None:
    d = _minimal_recipe_dict()
    d["schema"]["time_column"] = "ts"
    d["training"]["split"] = {"scheme": "time_user", "heldout_ratio": 0.1}
    r = Recipe.model_validate(d)
    assert r.training.split.scheme == "time_user"


# ---------------------------------------------------------------------------
# SplitConfig: heldout_ratio bounds
# ---------------------------------------------------------------------------


def test_heldout_ratio_above_one_rejected() -> None:
    with pytest.raises(ValidationError):
        SplitConfig(heldout_ratio=1.1)


def test_heldout_ratio_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        SplitConfig(heldout_ratio=0.0)


def test_heldout_ratio_one_rejected() -> None:
    """heldout_ratio=1.0 is rejected (lt=1.0 constraint)."""
    with pytest.raises(ValidationError):
        SplitConfig(heldout_ratio=1.0)


def test_heldout_ratio_valid() -> None:
    sc = SplitConfig(heldout_ratio=0.2)
    assert sc.heldout_ratio == 0.2


# ---------------------------------------------------------------------------
# TrainingConfig: n_trials
# ---------------------------------------------------------------------------


def test_n_trials_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(algorithms=["TopPop"], n_trials=0)


def test_n_trials_one_accepted() -> None:
    tc = TrainingConfig(algorithms=["TopPop"], n_trials=1)
    assert tc.n_trials == 1


def test_algorithms_empty_list_rejected() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(algorithms=[], n_trials=5)


# ---------------------------------------------------------------------------
# ItemMetadataConfig: fields empty
# ---------------------------------------------------------------------------


def test_item_metadata_fields_empty_list_rejected() -> None:
    with pytest.raises(ValidationError):
        ItemMetadataConfig(type="csv", path="/tmp/meta.csv", fields=[])


def test_item_metadata_fields_nonempty_ok() -> None:
    cfg = ItemMetadataConfig(type="csv", path="/tmp/meta.csv", fields=["title"])
    assert cfg.fields == ["title"]


# ---------------------------------------------------------------------------
# Extra fields rejected (extra="forbid")
# ---------------------------------------------------------------------------


def test_schema_config_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        SchemaConfig(user_column="u", item_column="i", unknown_field="x")


def test_cleansing_config_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        CleansingConfig(not_a_field=True)


# ---------------------------------------------------------------------------
# OutputConfig versioning enum
# ---------------------------------------------------------------------------


def test_output_config_invalid_versioning_rejected() -> None:
    with pytest.raises(ValidationError):
        OutputConfig(path="/tmp/x.recotem", versioning="invalid_mode")


def test_output_config_valid_versioning() -> None:
    oc = OutputConfig(path="/tmp/x.recotem", versioning="append_sha")
    assert oc.versioning == "append_sha"


# ---------------------------------------------------------------------------
# CleansingConfig: dedup field
# ---------------------------------------------------------------------------


def test_cleansing_config_invalid_dedup_rejected() -> None:
    with pytest.raises(ValidationError):
        CleansingConfig(dedup="destroy_all")


def test_cleansing_config_valid_dedup() -> None:
    for val in ("keep_first", "keep_last", "sum_weight", "none"):
        cc = CleansingConfig(dedup=val)
        assert cc.dedup == val
