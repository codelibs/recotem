"""Algorithm alias resolution and frozen supported-algorithm list.

Maps short user-facing alias strings (e.g. "IALS") to the canonical irspack
recommender class name (e.g. "IALSRecommender") and exposes a frozen set of
all supported class names for this release.
"""

from __future__ import annotations

from irspack.recommenders.base import get_recommender_class

# _compat must be imported first: it applies the IPython stub that allows
# irspack (which depends on fastprogress) to be imported without IPython.
import recotem.training._compat  # noqa: F401
from recotem.training.errors import UnknownAlgorithmError

# ---------------------------------------------------------------------------
# Canonical aliases
# ---------------------------------------------------------------------------

_ALIAS_MAP_RAW: dict[str, str] = {
    # Short/mnemonic -> canonical irspack class name
    "IALS": "IALSRecommender",
    "CosinekNN": "CosineKNNRecommender",
    "CosineKNN": "CosineKNNRecommender",
    "TopPop": "TopPopRecommender",
    "RP3beta": "RP3betaRecommender",
    "DenseSLIM": "DenseSLIMRecommender",
    "TruncatedSVD": "TruncatedSVDRecommender",
    "BPRFM": "BPRFMRecommender",
}

# Case-folded lookup map: casefold(alias) -> canonical class name.
# Built once at import time so resolution is O(1) per call.
_ALIAS_MAP: dict[str, str] = {k.casefold(): v for k, v in _ALIAS_MAP_RAW.items()}

# Also expose the case-folded canonical class names for direct-match lookup.
_CLASS_NAME_CASEFOLD: dict[str, str] = {
    n.casefold(): n for n in _ALIAS_MAP_RAW.values()
}

# Frozen set of class names supported in this release.
# The set is explicit (not derived at runtime) to provide a stable contract
# across irspack patch releases.
SUPPORTED_CLASS_NAMES: frozenset[str] = frozenset(
    {
        "IALSRecommender",
        "CosineKNNRecommender",
        "TopPopRecommender",
        "RP3betaRecommender",
        "DenseSLIMRecommender",
        "TruncatedSVDRecommender",
        "BPRFMRecommender",
    }
)

# Algorithms that accept `user_features` / `item_features` constructor kwargs.
# As of irspack 0.5.0 feature-aware iALS is not a distinct class -- it is
# IALSRecommender with extra kwargs -- so this set holds exactly one entry.
# Kept explicit (not derived) for the same reason as SUPPORTED_CLASS_NAMES:
# a stable contract across irspack patch releases.
FEATURE_CAPABLE_CLASS_NAMES: frozenset[str] = frozenset({"IALSRecommender"})


def is_feature_capable(alias: str) -> bool:
    """Return True when *alias* resolves to a feature-aware-capable class.

    Unknown aliases return False rather than raising: ``training.algorithms``
    has no load-time validation (recipe/models.py deliberately swallows
    ``UnknownAlgorithmError``), and this helper must not change that.
    """
    try:
        class_name = resolve_algorithm_name(alias)
    except UnknownAlgorithmError:
        return False
    return class_name in FEATURE_CAPABLE_CLASS_NAMES


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def resolve_algorithm_name(alias: str) -> str:
    """Resolve a user-facing algorithm alias to a canonical class name.

    Resolution is case-insensitive and restricted to the frozen
    ``SUPPORTED_CLASS_NAMES`` set so that recipes cannot reference irspack
    recommenders the artifact loader would refuse at serve time.

    Tries (in order):
    1. Case-folded lookup in ``_ALIAS_MAP``.
    2. Case-folded lookup of the alias itself against ``SUPPORTED_CLASS_NAMES``.
    3. Case-folded lookup of ``alias + "Recommender"`` against
       ``SUPPORTED_CLASS_NAMES``.

    Raises
    ------
    UnknownAlgorithmError
        If no mapping can be found within the supported set.
    """
    folded = alias.casefold()

    if folded in _ALIAS_MAP:
        return _ALIAS_MAP[folded]

    if folded in _CLASS_NAME_CASEFOLD:
        return _CLASS_NAME_CASEFOLD[folded]

    candidate_folded = f"{folded}recommender"
    if candidate_folded in _CLASS_NAME_CASEFOLD:
        return _CLASS_NAME_CASEFOLD[candidate_folded]

    raise UnknownAlgorithmError(
        f"Unknown or unsupported algorithm {alias!r}. "
        f"Supported aliases: {sorted(_ALIAS_MAP_RAW)} "
        f"or full class names: {sorted(SUPPORTED_CLASS_NAMES)}."
    )


def get_recommender_cls(class_name: str):  # type: ignore[return]
    """Return the irspack recommender class for *class_name*.

    Raises
    ------
    UnknownAlgorithmError
        If irspack does not know the class.
    """
    try:
        return get_recommender_class(class_name)
    except (ImportError, AttributeError, ValueError, KeyError) as exc:
        raise UnknownAlgorithmError(
            f"irspack does not know recommender class {class_name!r}."
        ) from exc
