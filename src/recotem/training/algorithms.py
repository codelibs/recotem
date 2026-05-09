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

    Resolution is restricted to the frozen ``SUPPORTED_CLASS_NAMES`` set so
    that recipes cannot reference irspack recommenders the artifact loader
    would refuse at serve time.

    Tries (in order):
    1. Direct lookup in ``_ALIAS_MAP``.
    2. The alias itself if it is in ``SUPPORTED_CLASS_NAMES``.
    3. Appending "Recommender" suffix and checking ``SUPPORTED_CLASS_NAMES``.

    Raises
    ------
    UnknownAlgorithmError
        If no mapping can be found within the supported set.
    """
    if alias in _ALIAS_MAP:
        return _ALIAS_MAP[alias]

    if alias in SUPPORTED_CLASS_NAMES:
        return alias

    candidate = f"{alias}Recommender"
    if candidate in SUPPORTED_CLASS_NAMES:
        return candidate

    raise UnknownAlgorithmError(
        f"Unknown or unsupported algorithm {alias!r}. "
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
