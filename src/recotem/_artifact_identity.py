"""Preflight check binding an artifact to the recipe that is about to serve it.

The trainer writes ``recipe_name`` into every artifact header, but nothing on
the serve side compared it with the recipe whose ``output.path`` the file was
read from. A correctly-signed artifact therefore loaded under *any* recipe
pointing at it, and did so with no signal anywhere: the hot-swap logged at
INFO, ``/v1/health`` stayed ``ok``, and ``/v1/recipes/{name}`` reported the
other recipe's ``best_algorithm``, ``best_params`` and ``recipe_hash``.

The realistic trigger is not an attack. Copy a recipe, forget to change
``output.path``, and two training runs overwrite one file; whichever ran last
is then served under both names, permanently and silently. HMAC does not catch
it — both artifacts are signed by the same key ring, which is the whole point
of a key ring. The binding is only enforceable by reading the field the
trainer already writes, which is why this check costs nothing: the header dict
is already decoded, and no payload byte has to be interpreted.

Policy, matching the sibling gates:

- ``recipe_name`` absent -> pass. Pre-2.0 artifacts predate the field, and an
  absent field is not evidence of a mismatch. ``_irspack_compat`` fails open
  on an absent version for the same reason.
- present and equal -> pass.
- present and anything else — a different name, or a non-string -> refuse.
  Present-but-wrong IS evidence. Same asymmetry
  ``check_artifact_irspack_version`` applies to ``best_class``.

Unlike the irspack and feature gates there is no escape-hatch env var. Those
exist because their refusals are *conservative* — they can refuse a
combination that would in fact have loaded fine. This one refuses only on a
positive, unambiguous contradiction stated by the artifact itself, so an
override would have nothing to rescue; the remedy is to fix ``output.path``.

This module lives at the top level rather than under ``serving/`` or
``training/`` so neither sub-package depends on the other (see CLAUDE.md),
matching ``_irspack_compat.py`` and ``_features.py``.
"""

from __future__ import annotations

from typing import Any

import structlog

from recotem.artifact.format import ArtifactError

logger = structlog.get_logger(__name__)

# Stable message prefix — `_classify_artifact_error` keys the Prometheus
# `reason` label off it, the same contract `_irspack_compat.SKEW_MSG_PREFIX`
# and `_features.FEATURE_*_MSG_PREFIX` carry. Keep the two in sync.
RECIPE_NAME_MSG_PREFIX = "recipe name mismatch:"

# Bound on the header's name where the refusal quotes it back. serve truncates
# last_load_error to 200 chars for /v1/health/details, so both names have to
# fit inside that budget; the recipe schema caps a legitimate name at 64
# chars (``^[A-Za-z0-9_-]{1,64}$``), so anything longer is already anomalous
# and truncating it loses nothing an operator needs. Same reasoning as the
# 40-char `best_class` bound in `_irspack_compat`.
_NAME_MAX_CHARS = 64


def _header_name_label(value: Any) -> str:
    """Render the header's ``recipe_name`` for the refusal message.

    A non-string is reported by type rather than by value: the value could be
    an arbitrarily large nested structure, and its *shape* is the actionable
    fact — a header carrying a non-string here was not written by this
    trainer.
    """
    if not isinstance(value, str):
        return f"<non-string {type(value).__name__}>"
    if len(value) > _NAME_MAX_CHARS:
        return f"{value[: _NAME_MAX_CHARS - 1]}…"
    return value


def check_artifact_recipe_name(header_dict: dict[str, Any], *, name: str) -> None:
    """Raise ``ArtifactError`` if *header_dict* was trained for another recipe.

    *name* is the recipe the artifact is being loaded for — the one whose
    ``output.path`` produced these bytes.
    """
    header_name = header_dict.get("recipe_name")
    if header_name is None:
        # Pre-2.0 artifacts predate the header field. Nothing to compare, and
        # a retrain would add it — so warn rather than refuse.
        logger.warning("artifact_recipe_name_absent_from_header", name=name)
        return

    if isinstance(header_name, str) and header_name == name:
        return

    label = _header_name_label(header_name)

    # WARNING, not ERROR: two recipes sharing one output.path is an operator
    # or CI misconfiguration, not a security event — the HMAC still verified,
    # so these bytes came from a trusted trainer. serving/app.py reserves
    # ERROR for security signals so SIEM rules filtering on level >= ERROR
    # stay meaningful, and `_irspack_compat` honours the same split. The load
    # still fails and still increments
    # recotem_artifact_load_failures_total{reason="recipe_name"}, which is the
    # surface operators alert on.
    logger.warning(
        "artifact_recipe_name_mismatch",
        name=name,
        artifact_recipe_name=label,
    )

    # Front-loaded on purpose: both names must land inside the 200-char
    # /health/details budget even at the 64-char schema maximum. The rest
    # still reaches the log line above.
    raise ArtifactError(
        f"{RECIPE_NAME_MSG_PREFIX} {name!r} loads an artifact trained for "
        f"{label!r} — give each recipe its own output.path and retrain. Two "
        "recipes writing one artifact file overwrite each other's model, so "
        "this endpoint would serve the other recipe's recommendations."
    )
