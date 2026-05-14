"""Tests for MAJOR-2: per_algorithm_trials key validation at recipe load time.

Tests:
- typo key (not in algorithms) raises ValidationError
- valid alias key that is in algorithms is accepted
- valid class-name key that is in algorithms is accepted
- key that resolves to an algorithm NOT in algorithms raises ValidationError
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def _make_training_config(**kwargs):
    from recotem.recipe.models import TrainingConfig

    return TrainingConfig(**kwargs)


# ---------------------------------------------------------------------------
# V2-1: typo key raises ValidationError
# ---------------------------------------------------------------------------


def test_per_algorithm_trials_typo_key_raises_validation_error() -> None:
    """A per_algorithm_trials key that is not a known alias nor in algorithms
    must raise a pydantic ValidationError at recipe-load time.
    """
    with pytest.raises(ValidationError) as exc_info:
        _make_training_config(
            algorithms=["CosineKNN"],
            per_algorithm_trials={"Cosine_kNN": 5},  # typo: underscore
        )
    # The error message should mention the offending key.
    assert "Cosine_kNN" in str(exc_info.value)


def test_per_algorithm_trials_unknown_algorithm_raises_validation_error() -> None:
    """A per_algorithm_trials key that resolves to an algorithm not listed in
    algorithms raises a ValidationError.
    """
    with pytest.raises(ValidationError) as exc_info:
        _make_training_config(
            algorithms=["CosineKNN"],
            per_algorithm_trials={"IALS": 5},  # IALS not in algorithms
        )
    assert "IALS" in str(exc_info.value)


# ---------------------------------------------------------------------------
# V2-2: valid alias key accepted
# ---------------------------------------------------------------------------


def test_per_algorithm_trials_valid_alias_accepted() -> None:
    """A per_algorithm_trials key that is a valid alias for an algorithm in
    algorithms must be accepted without error.
    """
    cfg = _make_training_config(
        algorithms=["CosineKNN", "TopPop"],
        per_algorithm_trials={"CosineKNN": 12, "TopPop": 4},
    )
    assert cfg.per_algorithm_trials == {"CosineKNN": 12, "TopPop": 4}


def test_per_algorithm_trials_full_class_name_accepted() -> None:
    """A per_algorithm_trials key that is the full canonical class name for an
    algorithm in algorithms must be accepted.
    """
    cfg = _make_training_config(
        algorithms=["CosineKNN"],
        per_algorithm_trials={"CosineKNNRecommender": 8},
    )
    assert cfg.per_algorithm_trials == {"CosineKNNRecommender": 8}


def test_per_algorithm_trials_none_accepted() -> None:
    """None per_algorithm_trials is always accepted."""
    cfg = _make_training_config(
        algorithms=["IALS"],
        per_algorithm_trials=None,
    )
    assert cfg.per_algorithm_trials is None


def test_per_algorithm_trials_all_algorithms_mix_accepted() -> None:
    """Multiple algorithms with matching per_algorithm_trials keys all accepted."""
    cfg = _make_training_config(
        algorithms=["IALS", "CosineKNN", "TopPop"],
        per_algorithm_trials={"IALS": 24, "CosineKNN": 12, "TopPop": 4},
    )
    assert cfg.per_algorithm_trials is not None
    assert sum(cfg.per_algorithm_trials.values()) == 40
