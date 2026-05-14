"""Tests for MAJOR-7: case-insensitive algorithm alias resolution.

Tests:
- lowercase variant resolves correctly
- UPPERCASE variant resolves correctly
- Mixed-case variant resolves correctly
- Case-folded full class name resolves correctly
- Truly unknown alias still raises UnknownAlgorithmError
"""

from __future__ import annotations

import pytest

from recotem.training.algorithms import resolve_algorithm_name
from recotem.training.errors import UnknownAlgorithmError


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        # Standard aliases — should still work unchanged
        ("IALS", "IALSRecommender"),
        ("CosineKNN", "CosineKNNRecommender"),
        ("TopPop", "TopPopRecommender"),
        # Lowercase variants
        ("ials", "IALSRecommender"),
        ("cosineknn", "CosineKNNRecommender"),
        ("toppop", "TopPopRecommender"),
        ("rp3beta", "RP3betaRecommender"),
        ("denseslim", "DenseSLIMRecommender"),
        ("truncatedsvd", "TruncatedSVDRecommender"),
        ("bprfm", "BPRFMRecommender"),
        # ALLCAPS variants
        ("COSINEKNN", "CosineKNNRecommender"),
        ("TOPPOP", "TopPopRecommender"),
        ("RP3BETA", "RP3betaRecommender"),
        ("BPRFM", "BPRFMRecommender"),
        # Mixed case (common user typos)
        ("Cosine_KNN".replace("_", ""), "CosineKNNRecommender"),  # CosineKNN
        ("cosine_knn".replace("_", ""), "CosineKNNRecommender"),  # cosineknn
        ("Ials", "IALSRecommender"),
        ("iAlS", "IALSRecommender"),
        # Full class names with wrong case
        ("ialsrecommender", "IALSRecommender"),
        ("IALSRECOMMENDER", "IALSRecommender"),
        ("cosineknnrecommender", "CosineKNNRecommender"),
        ("toppOPrecommender", "TopPopRecommender"),
    ],
)
def test_resolve_case_insensitive(alias: str, expected: str) -> None:
    """Various case variants of known aliases must resolve to the canonical name."""
    result = resolve_algorithm_name(alias)
    assert result == expected, (
        f"resolve_algorithm_name({alias!r}) expected {expected!r}, got {result!r}"
    )


def test_truly_unknown_alias_still_raises() -> None:
    """Completely unknown aliases (not any variant of a supported name) must
    still raise UnknownAlgorithmError.
    """
    with pytest.raises(UnknownAlgorithmError):
        resolve_algorithm_name("not-an-algorithm")


def test_p3alpha_still_rejected_case_insensitive() -> None:
    """Algorithms irspack knows but recotem does not support are rejected even
    with case-insensitive resolution.
    """
    with pytest.raises(UnknownAlgorithmError):
        resolve_algorithm_name("p3alpha")

    with pytest.raises(UnknownAlgorithmError):
        resolve_algorithm_name("P3Alpha")

    with pytest.raises(UnknownAlgorithmError):
        resolve_algorithm_name("P3ALPHARECOMMENDER")
