"""Helper utilities preserved from the legacy serving routes.

This module previously hosted ``make_router`` (alpha v0 API).  After the
v1 overhaul that lives in ``v1_router.py``.  The metadata-join helper
``_lookup_metadata`` remains here because both modules use it.
"""

import math
from typing import Any

import structlog

from recotem.serving import metrics as _metrics

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Metadata join helper
# ---------------------------------------------------------------------------


def _lookup_metadata(
    meta_df: Any,
    item_id: str,
    deny_set: frozenset[str],
    recipe_name: str = "",
) -> dict[str, Any]:
    """Return a flat dict of metadata fields for *item_id*.

    Returns an empty dict if the item is not found or any error occurs.
    The documented error set that returns empty dict:

    - ``KeyError``      — item not in metadata index (normal, not an error).
    - ``AttributeError`` — non-unique index returned a DataFrame instead of a
                           Series so ``.to_dict()`` behaves unexpectedly.
    - ``TypeError``     — a non-string column name caused ``.lower()`` to fail.
    - ``ValueError``    — malformed row data that cannot be iterated.

    All unexpected errors are logged at WARNING level and increment
    ``recotem_metadata_lookup_errors_total`` so operators can detect
    metadata misconfiguration without silencing it completely.
    """
    if item_id not in meta_df.index:
        return {}
    try:
        row = meta_df.loc[item_id]
    except KeyError:
        # Reaching here means item_id passed the index check above but
        # loc[] still raised — possible with a non-unique index returning a
        # DataFrame instead of a Series, or a corrupt index state.
        # Log at WARNING so operators can detect metadata misconfiguration;
        # also increment the metric so this class of error is observable in
        # dashboards alongside other metadata lookup failures.
        logger.warning(
            "metadata_lookup_unexpected_keyerror",
            recipe=recipe_name,
            item_id=str(item_id),
        )
        _metrics.inc_metadata_lookup_error(recipe_name)
        return {}
    try:
        out: dict[str, Any] = {}
        for k, v in row.to_dict().items():
            # Guard: skip non-string column names (M-13 — .lower() would raise
            # AttributeError on an int column name).
            if not isinstance(k, str):
                continue
            if k.lower() in deny_set:
                continue
            # Preserve existing NaN → None normalisation.
            out[k] = None if isinstance(v, float) and math.isnan(v) else v
        return out
    except (AttributeError, TypeError, ValueError) as exc:
        logger.warning(
            "metadata_lookup_failed",
            recipe=recipe_name,
            item_id=str(item_id),
            error_class=type(exc).__name__,
        )
        _metrics.inc_metadata_lookup_error(recipe_name)
        return {}
