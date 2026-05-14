"""Tests for ``recotem.training.algorithms.resolve_algorithm_name``."""

from __future__ import annotations

import pytest

from recotem.training.algorithms import (
    SUPPORTED_CLASS_NAMES,
    resolve_algorithm_name,
)
from recotem.training.errors import UnknownAlgorithmError


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("IALS", "IALSRecommender"),
        ("CosineKNN", "CosineKNNRecommender"),
        ("CosinekNN", "CosineKNNRecommender"),
        ("TopPop", "TopPopRecommender"),
        ("RP3beta", "RP3betaRecommender"),
        ("DenseSLIM", "DenseSLIMRecommender"),
        ("TruncatedSVD", "TruncatedSVDRecommender"),
        ("BPRFM", "BPRFMRecommender"),
    ],
)
def test_resolve_known_alias(alias: str, expected: str) -> None:
    assert resolve_algorithm_name(alias) == expected
    assert expected in SUPPORTED_CLASS_NAMES


def test_resolve_full_class_name() -> None:
    assert resolve_algorithm_name("IALSRecommender") == "IALSRecommender"


@pytest.mark.parametrize(
    "alias",
    [
        "P3alpha",
        "P3alphaRecommender",
        "MultVAERecommender",
        "SLIMElastic",
    ],
)
def test_unsupported_irspack_recommender_rejected(alias: str) -> None:
    """Names irspack knows but recotem does not support must fail at resolve.

    Regression for the case where artifacts trained with such recommenders
    cannot be loaded by the FQCN allow-list at serve time.
    """
    with pytest.raises(UnknownAlgorithmError):
        resolve_algorithm_name(alias)


def test_garbage_alias_rejected() -> None:
    with pytest.raises(UnknownAlgorithmError):
        resolve_algorithm_name("not-an-algorithm")
