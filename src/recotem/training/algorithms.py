"""Algorithm alias resolution and frozen supported-algorithm list.

Maps short user-facing alias strings (e.g. "IALS") to the canonical irspack
recommender class name (e.g. "IALSRecommender") and exposes a frozen set of
all supported class names for this release.
"""

from __future__ import annotations

# _compat must be imported first: it applies the IPython stub that allows
# irspack (which depends on fastprogress) to be imported without IPython.
import recotem.training._compat  # noqa: F401

from irspack.recommenders.base import get_recommender_class

from recotem.training.errors import UnknownAlgorithmError

# ---------------------------------------------------------------------------
# Canonical aliases
# ---------------------------------------------------------------------------

_ALIAS_MAP: dict[str, str] = {
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


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def resolve_algorithm_name(alias: str) -> str:
    """Resolve a user-facing algorithm alias to a canonical class name.

    Tries (in order):
    1. Direct lookup in ``_ALIAS_MAP``.
    2. The alias itself if it ends with "Recommender" and is importable.
    3. Appending "Recommender" suffix.
    4. Attempting ``get_recommender_class`` from irspack (future-proofing).

    Raises
    ------
    UnknownAlgorithmError
        If no mapping can be found.
    """
    if alias in _ALIAS_MAP:
        return _ALIAS_MAP[alias]

    # Already a full class name?
    if alias.endswith("Recommender"):
        try:
            get_recommender_class(alias)
            return alias
        except (ImportError, AttributeError, ValueError, KeyError):
            pass

    # Try appending "Recommender"
    candidate = f"{alias}Recommender"
    try:
        get_recommender_class(candidate)
        return candidate
    except (ImportError, AttributeError, ValueError, KeyError):
        pass

    raise UnknownAlgorithmError(
        f"Unknown algorithm {alias!r}. "
        f"Supported aliases: {sorted(_ALIAS_MAP)} "
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
