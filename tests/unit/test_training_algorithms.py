"""Tests for ``recotem.training.algorithms.resolve_algorithm_name``."""

from __future__ import annotations

import pytest

from recotem.training.algorithms import (
    _ALIAS_MAP_RAW,
    FEATURE_CAPABLE_CLASS_NAMES,
    SUPPORTED_CLASS_NAMES,
    constructible_class_names,
    get_recommender_cls,
    is_feature_capable,
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


# ---------------------------------------------------------------------------
# Task 2: feature-capable algorithm registry
# ---------------------------------------------------------------------------


def test_only_ials_is_feature_capable() -> None:
    assert frozenset({"IALSRecommender"}) == FEATURE_CAPABLE_CLASS_NAMES


def test_is_feature_capable_accepts_alias() -> None:
    assert is_feature_capable("IALS") is True
    assert is_feature_capable("ials") is True
    assert is_feature_capable("TopPop") is False


def test_is_feature_capable_unknown_name_is_false_not_raise() -> None:
    # Unknown names must NOT raise here: training.algorithms has no load-time
    # validation and models.py:136-141 deliberately tolerates them.
    assert is_feature_capable("NoSuchThing") is False


# ---------------------------------------------------------------------------
# The suggestion list must only offer algorithms that can actually be trained
# ---------------------------------------------------------------------------


def test_bprfm_constructibility_tracks_the_lightfm_extra() -> None:
    """``BPRFMRecommender`` is available exactly when lightfm is installed.

    BPRFM is the one algorithm gated behind an optional dependency: irspack
    imports ``lightfm`` from ``recommenders/bpr.py`` and drops the class from
    its exports when that import fails.  recotem ships the dependency as the
    ``bprfm`` extra (``lightfm-next``, since upstream lightfm does not build on
    Python 3.12), so whether BPRFM can be trained is an install-time property,
    not a code-level one.

    It stays in the frozen ``SUPPORTED_CLASS_NAMES`` either way, so an artifact
    trained on a host that has the extra still passes the FQCN allow-list on
    one that does not -- the load then fails for a reason the operator can act
    on rather than as an unknown class.
    """
    import importlib.util

    # find_spec rather than import: importing lightfm is what emits its
    # no-OpenMP UserWarning, and this suite runs warnings-as-error.
    lightfm_installed = importlib.util.find_spec("lightfm") is not None

    assert "BPRFMRecommender" in SUPPORTED_CLASS_NAMES
    assert ("BPRFMRecommender" in constructible_class_names()) is lightfm_installed


def test_unknown_algorithm_error_suggests_only_constructible_names() -> None:
    """A wrong name must not be answered with a suggestion that also fails.

    ``BPRFM`` sorts first, so it led the suggestion list: a user correcting a
    typo by taking the first offer hit a second, different failure
    (``irspack does not know recommender class 'BPRFMRecommender'``).
    """
    with pytest.raises(UnknownAlgorithmError) as excinfo:
        resolve_algorithm_name("not-an-algorithm")
    message = str(excinfo.value)

    constructible = constructible_class_names()
    for class_name in SUPPORTED_CLASS_NAMES - constructible:
        assert class_name not in message, message
        for alias, canonical in _ALIAS_MAP_RAW.items():
            if canonical == class_name:
                assert alias not in message, message

    # ...and everything that does work is still offered.
    for class_name in constructible:
        assert class_name in message, message
    for alias, canonical in _ALIAS_MAP_RAW.items():
        if canonical in constructible:
            assert repr(alias) in message, message


def test_constructible_class_names_asks_irspack() -> None:
    """Availability is irspack's answer, not a hard-coded list in recotem.

    Every name reported constructible must actually resolve to a class, so the
    helper cannot drift from irspack the way a copied deny-list would.
    """
    constructible = constructible_class_names()
    assert constructible <= SUPPORTED_CLASS_NAMES
    for class_name in constructible:
        assert get_recommender_cls(class_name).__name__ == class_name
