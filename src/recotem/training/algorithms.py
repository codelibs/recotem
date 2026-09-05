"""Algorithm alias resolution and frozen supported-algorithm list.

Maps short user-facing alias strings (e.g. "IALS") to the canonical irspack
recommender class name (e.g. "IALSRecommender") and exposes a frozen set of
all supported class names for this release.
"""

from __future__ import annotations

from functools import lru_cache

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

# Class names that are in SUPPORTED_CLASS_NAMES but that irspack only exports
# when an OPTIONAL recotem extra is installed -> the extra that supplies them.
#
# Used for the error message only; availability itself is still established by
# asking irspack (see `constructible_class_names`), never by consulting this
# map, so a stale entry can misname a remedy but can never make a working
# algorithm look unavailable or the reverse.
#
# Without it the failure is indistinguishable from a typo: `BPRFM` is a valid,
# documented alias that resolves cleanly through `resolve_algorithm_name`, then
# dies in `get_recommender_cls` with a message blaming irspack. An operator
# reads "irspack does not know recommender class 'BPRFMRecommender'" and goes
# looking through irspack's issue tracker, when one `pip install` fixes it.
_GATED_CLASS_EXTRAS: dict[str, str] = {
    # irspack imports `lightfm` unconditionally in irspack/recommenders/bpr.py
    # and drops BPRFMRecommender from its exports when that import fails; the
    # `bprfm` extra installs `lightfm-next`, which provides that module.
    "BPRFMRecommender": "bprfm",
}

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

    # Suggest only what this interpreter can actually train.  Listing every
    # name in SUPPORTED_CLASS_NAMES handed the user a first suggestion
    # ("BPRFM", alphabetically first) that fails with a *different* error, so
    # correcting a typo by following the advice led straight to a second
    # failure.
    constructible = constructible_class_names()
    raise UnknownAlgorithmError(
        f"Unknown or unsupported algorithm {alias!r}. "
        f"Supported aliases: "
        f"{sorted(a for a, c in _ALIAS_MAP_RAW.items() if c in constructible)} "
        f"or full class names: {sorted(constructible)}."
    )


def get_recommender_cls(class_name: str):  # type: ignore[return]
    """Return the irspack recommender class for *class_name*.

    Two different conditions reach the same irspack failure, and they need
    different remedies: the name is not a recommender at all, or it is one
    recotem supports but that irspack only exports when an optional extra is
    installed.  irspack reports both by simply not having the class, so the
    distinction has to be drawn here -- otherwise a missing dependency reads as
    a typo and sends the operator to irspack's issue tracker instead of to
    ``pip install``.

    Raises
    ------
    UnknownAlgorithmError
        If irspack does not know the class, naming the missing extra when the
        class is one of recotem's own gated algorithms.
    """
    try:
        return get_recommender_class(class_name)
    except (ImportError, AttributeError, ValueError, KeyError) as exc:
        extra = _GATED_CLASS_EXTRAS.get(class_name)
        if extra is not None:
            # Names ONLY the narrow extra, never `recotem[all]`.  `[all]`
            # depends on `[bprfm]`, so on any platform where the narrow extra
            # cannot install, `[all]` cannot either -- it is offered as an
            # alternative to the very thing that just failed and rescues
            # nobody, while asking the operator to pull in eight extras they
            # did not want.  Measured across {3.12, 3.13, 3.14} x
            # {amd64, arm64}: `[bprfm]` and `[all]` install on amd64 3.12 and
            # amd64 3.13 only, because `lightfm-next` publishes no wheel for
            # the other four cells and a source build needs a compiler the
            # slim image does not carry.
            raise UnknownAlgorithmError(
                f"Algorithm {class_name!r} is supported by recotem but is not "
                f"installed: it requires the optional {extra!r} extra. "
                f"Install it with: pip install 'recotem[{extra}]', then retry."
            ) from exc
        raise UnknownAlgorithmError(
            f"irspack does not know recommender class {class_name!r}."
        ) from exc


@lru_cache(maxsize=1)
def constructible_class_names() -> frozenset[str]:
    """Return the ``SUPPORTED_CLASS_NAMES`` irspack exports on this host.

    ``SUPPORTED_CLASS_NAMES`` is recotem's frozen contract, but irspack gates
    some recommenders behind optional dependencies and simply does not export
    the class when they are absent -- ``BPRFMRecommender`` needs ``lightfm``,
    which recotem supplies through its optional ``bprfm`` extra (see
    ``_GATED_CLASS_EXTRAS``).  Availability is therefore established by
    asking irspack rather than by hard-coding the gated names here: which
    recommenders are gated is irspack's decision to change, not ours, and a
    hard-coded copy would go stale silently.

    Cached because the answer cannot change within a process: the result
    depends only on which modules irspack could import at its own import time.
    """
    available: set[str] = set()
    for class_name in SUPPORTED_CLASS_NAMES:
        try:
            get_recommender_cls(class_name)
        except UnknownAlgorithmError:
            continue
        available.add(class_name)
    return frozenset(available)
