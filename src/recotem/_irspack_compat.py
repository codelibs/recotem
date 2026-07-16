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

Scope of the check is deliberately major.minor:

- Patch releases have not changed the pickle format, so requiring an exact
  match would strand artifacts for no benefit.
- The check cannot be per-algorithm.  Only IALS is known to break across
  0.4/0.5, but the header records ``best_class`` *before* the payload is
  deserialized, and encoding "which algorithm broke in which release" here
  would rot on every irspack release.  Refusing the whole major.minor skew is
  the honest, durable rule; ``RECOTEM_ALLOW_IRSPACK_VERSION_SKEW`` is the
  escape hatch for operators who know their artifact is unaffected.

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

    *name* is the recipe name, used only for logging. *running* overrides the
    installed irspack version (tests).

    Returns without raising when the versions agree at major.minor, when either
    version is absent or unparseable (an unverifiable version is not evidence
    of incompatibility — the deserializer remains the backstop), or when
    ``RECOTEM_ALLOW_IRSPACK_VERSION_SKEW`` is set.
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

    logger.error(
        "irspack_version_skew",
        name=name,
        artifact_irspack=header_version,
        running_irspack=running,
    )
    # Front-loaded on purpose: serve truncates last_load_error to 200 chars for
    # /health, so the remedy and both versions must land inside that budget.
    # The full text still reaches the logs via the irspack_version_skew event.
    raise ArtifactError(
        f"{SKEW_MSG_PREFIX} retrain recipe '{name}' with irspack {running} "
        f"and redeploy — the artifact was trained with irspack "
        f"{header_version} but this process runs {running}. irspack's pickle "
        "format is not stable across minor releases (0.5.0 changed the IALS "
        "model state), so the payload may fail to deserialize or restore "
        "incorrectly. If this artifact uses an algorithm you know to be "
        f"unaffected, set {SKEW_ENV}=1 to downgrade this to a warning."
    )
