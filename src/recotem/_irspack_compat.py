"""Preflight check for irspack version skew between train and serve.

irspack does not guarantee a stable pickle format across minor releases, and
does not always document when it changes.  irspack 0.5.0 grew
``IALSModelConfig``'s pickled state from a 7-tuple to a 10-tuple (feature-aware
iALS added three fields).  Its ``__setstate__`` is a nanobind binding with a
strict arity, so the mismatch surfaces as::

    TypeError: __setstate__(): incompatible function arguments.
      The following argument types are supported:
        1. __setstate__(self, arg: tuple[int, float, ..., int], /) -> None

That names neither the recipe, nor the versions, nor the remedy, and it is
raised from inside the deserializer where the header is no longer in scope.
This module turns it into an ``ArtifactError`` raised *before* deserialization,
naming both versions and the fix (retrain).

The rule is an ALLOW-LIST, not a deny-list: a (best_class, version-transition)
pair is accepted only if ``_VERIFIED_COMPATIBLE`` below records that we
empirically verified it. Anything absent is refused.

The distinction matters. A deny-list ("refuse IALS across 0.4/0.5") would
silently green-light BPRFM, for which we have no evidence at all, and every
future untested transition — the failure mode would be an artifact that loads
and serves subtly wrong scores. Refusing the unproven keeps the safety default
of the older blanket rule while letting the five verified-compatible classes
through, so an irspack 0.4 -> 0.5 upgrade now forces a retrain only for IALS
and BPRFM rather than for every algorithm.

Scope is major.minor: irspack 0.4.x was verified internally stable (0.4.0 ->
0.4.2 interchange, IALS included), so requiring an exact match would strand
artifacts for no benefit. Matching major.minor short-circuits before the table
is consulted.

``RECOTEM_ALLOW_IRSPACK_VERSION_SKEW`` remains the escape hatch for operators
who know their artifact is unaffected.

This module lives at the top level rather than under ``serving/`` or
``training/`` so neither sub-package depends on the other (see CLAUDE.md).
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from recotem.artifact.format import ArtifactError
from recotem.config import is_truthy_env

logger = structlog.get_logger(__name__)

SKEW_ENV = "RECOTEM_ALLOW_IRSPACK_VERSION_SKEW"

# Stable message prefix — `_classify_artifact_error` keys the Prometheus
# `reason` label off it. Keep the two in sync.
SKEW_MSG_PREFIX = "irspack version skew:"

# ---------------------------------------------------------------------------
# Verified-compatible table
# ---------------------------------------------------------------------------
#
# Each row states: artifacts recording this ``best_class`` interchange between
# these two irspack major.minor versions, in BOTH directions.
#
# "Verified" has one meaning here, and adding a row REQUIRES the same evidence:
# an artifact trained under one version was loaded under the other, with
# irspack as the only variable, and the recommendation scores compared
# bit-exact.  Reasoning about whether a class "looks like" it pickles cleanly
# is NOT evidence — IALSRecommender looks fine right up until nanobind rejects
# the state tuple.
#
# Deliberately NOT listed:
#   IALSRecommender  — FAILS across 0.4/0.5 in both directions.
#                      ``IALSModelConfig.__setstate__`` arity went 7 -> 10 at
#                      0.5.0 (feature-aware iALS added three fields).
#   BPRFMRecommender — UNVERIFIABLE. irspack gates it behind the separately
#                      installed ``lightfm`` package (not an irspack extra --
#                      irspack declares none), and lightfm has no
#                      py3.12-compatible release, so irspack does not export
#                      the class here. No evidence either way, so the
#                      allow-list refuses it. Absence from this table means
#                      "unproven", not "known broken".
#
# Rows are keyed at major.minor because irspack 0.4.x was verified internally
# stable (0.4.0 -> 0.4.2 interchange, IALS included), so patch drift within a
# minor is tolerated without consulting this table at all.
#
# SEPARATE VERSION AXIS — NOT COVERED HERE: scikit-learn. TruncatedSVD embeds
# an sklearn estimator, and sklearn emits InconsistentVersionWarning across its
# own minors. The verification behind its row below varied irspack while
# holding sklearn constant, so the row says nothing about an sklearn upgrade
# underneath. recotem range-pins scikit-learn in pyproject.toml to bound that
# axis. This guard does not check it.
_VERIFIED_COMPATIBLE_BIDIRECTIONAL: tuple[
    tuple[str, tuple[int, int], tuple[int, int]], ...
] = (
    ("CosineKNNRecommender", (0, 4), (0, 5)),
    ("TopPopRecommender", (0, 4), (0, 5)),
    ("RP3betaRecommender", (0, 4), (0, 5)),
    ("DenseSLIMRecommender", (0, 4), (0, 5)),
    ("TruncatedSVDRecommender", (0, 4), (0, 5)),
)

# Expanded to directed (best_class, header_mm, running_mm) lookup keys. The
# declaration above stays bidirectional so a row cannot be half-added.
_VERIFIED_COMPATIBLE: frozenset[tuple[str, tuple[int, int], tuple[int, int]]] = (
    frozenset(
        directed
        for best_class, one, other in _VERIFIED_COMPATIBLE_BIDIRECTIONAL
        for directed in (
            (best_class, one, other),
            (best_class, other, one),
        )
    )
)


def _major_minor(version: str) -> tuple[int, int] | None:
    """Return ``(major, minor)`` for *version*, or ``None`` if unparseable.

    Tolerant by design: dev/local suffixes (``0.5.0.dev1``, ``0.5.0+local``)
    parse fine because only the first two dot-separated fields are read.
    """
    parts = version.strip().split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _running_irspack_version() -> str | None:
    try:
        import irspack  # noqa: PLC0415 — deferred: keeps this module import-cheap

        version = irspack.__version__
    except Exception:  # pragma: no cover — irspack is a hard dependency
        return None
    return version if isinstance(version, str) else None


def check_artifact_irspack_version(
    header_dict: dict[str, Any],
    *,
    name: str,
    running: str | None = None,
) -> None:
    """Raise ``ArtifactError`` if *header_dict* was trained on a skewed irspack.

    *name* is the recipe name, used for logging and the error. *running*
    overrides the installed irspack version (tests).

    Returns without raising when:

    - the versions agree at major.minor (patch drift; the table is not
      consulted);
    - the versions differ but ``(best_class, header_mm, running_mm)`` is in
      ``_VERIFIED_COMPATIBLE``;
    - either version is absent or unparseable — fail OPEN, because an
      unverifiable version is not evidence of incompatibility and the
      deserializer remains the backstop;
    - ``RECOTEM_ALLOW_IRSPACK_VERSION_SKEW`` is set.

    Note the asymmetry: an unusable *version* fails open (nothing to compare),
    but an unusable or unknown *best_class* on a real skew fails CLOSED — it
    misses the allow-list and is refused. Unproven is not the same as safe.
    """
    header_version = header_dict.get("irspack_version")
    if not isinstance(header_version, str) or not header_version:
        # Pre-2.0 artifacts predate the header field. Nothing to compare.
        logger.warning("irspack_version_absent_from_header", name=name)
        return

    if running is None:
        running = _running_irspack_version()
    if running is None:
        logger.warning("irspack_version_unavailable", name=name)
        return

    header_mm = _major_minor(header_version)
    running_mm = _major_minor(running)
    if header_mm is None or running_mm is None:
        logger.warning(
            "irspack_version_unparseable",
            name=name,
            artifact_irspack=header_version,
            running_irspack=running,
        )
        return

    if header_mm == running_mm:
        # Patch drift within a minor. Verified stable; the table is not
        # consulted, so a new patch release never needs a row.
        return

    best_class = header_dict.get("best_class")
    if (
        isinstance(best_class, str)
        and (best_class, header_mm, running_mm) in _VERIFIED_COMPATIBLE
    ):
        return

    if is_truthy_env(os.environ.get(SKEW_ENV)):
        logger.warning(
            "irspack_version_skew_allowed",
            name=name,
            artifact_irspack=header_version,
            running_irspack=running,
            reason=f"{SKEW_ENV} is set",
        )
        return

    # WARNING, not ERROR: skew is a fleet-consistency signal whose remedy is a
    # retrain, not a security event. serving/app.py reserves ERROR for security
    # signals (HMAC) so that SIEM rules filtering on level >= ERROR stay
    # meaningful; this module honours the same split. The load still fails and
    # still increments recotem_artifact_load_failures_total{reason=
    # "version_skew"}, which is the surface operators alert on.
    logger.warning(
        "irspack_version_skew",
        name=name,
        best_class=best_class,
        artifact_irspack=header_version,
        running_irspack=running,
    )
    # Bound the label so a long best_class cannot evict the versions from the
    # 200-char budget asserted below. 40 comfortably clears the longest real
    # name (TruncatedSVDRecommender, 23).
    label = best_class if isinstance(best_class, str) and best_class else "unknown"
    if len(label) > 40:
        label = f"{label[:39]}…"

    # Front-loaded on purpose: serve truncates last_load_error to 200 chars for
    # /health/details, so the remedy, the recipe name, best_class and BOTH
    # versions must land inside that budget even at a 64-char recipe name.
    # tests/unit/test_irspack_compat.py measures this against _sanitize_error
    # rather than trusting it to eye. The rest still reaches the logs above.
    raise ArtifactError(
        f"{SKEW_MSG_PREFIX} retrain recipe '{name}' with irspack {running} "
        f"— {label} {header_version}→{running} is not verified compatible. "
        "Recotem allows only (algorithm, irspack transition) pairs it has "
        "empirically verified load correctly; unverified is not proof of "
        "breakage — the one known break is IALSRecommender at irspack 0.5.0, "
        "whose pickled model state changed shape. Retrain and redeploy, or if "
        f"you know this artifact is unaffected set {SKEW_ENV}=1 to downgrade "
        "this to a warning."
    )
