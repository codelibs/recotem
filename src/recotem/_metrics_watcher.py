"""Prometheus counter for recipes-dir scan failure events.

This module is intentionally neutral — it lives at the top-level ``recotem``
package so that ``serving/watcher`` can import it without coupling the metric
definition to the serving sub-package.  The same pattern is used by
``recotem._metrics_bigquery``.

The counter is a no-op when ``prometheus_client`` is not installed, following
the same convention as ``recotem.serving.metrics``.

Counter inventory:

| Name                                              | Type    | Labels      |
|---------------------------------------------------|---------|-------------|
| ``recotem_recipes_dir_scan_failures_total``       | Counter | error_class |
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via env without extra
    _PROMETHEUS_AVAILABLE = False

# Module-level counter instance.  Initialised lazily on the first call to
# ``inc_recipes_dir_scan_failure`` so that importing this module does not
# register metrics in environments where prometheus_client is absent.
_RECIPES_DIR_SCAN_FAILURES: Any = None


def _ensure_initialized() -> None:
    """Idempotently create the counter object."""
    global _RECIPES_DIR_SCAN_FAILURES

    if not _PROMETHEUS_AVAILABLE or _RECIPES_DIR_SCAN_FAILURES is not None:
        return

    _RECIPES_DIR_SCAN_FAILURES = Counter(
        "recotem_recipes_dir_scan_failures_total",
        "Total per-recipe load failures during watcher directory rescan "
        "(includes both new-recipe discovery failures and errors that could "
        "not be matched to an existing registered recipe). "
        "A sustained rate indicates a broken recipe YAML or artifact path.",
        ["error_class"],
    )


def inc_recipes_dir_scan_failure(error_class: str) -> None:
    """Increment the recipes-dir scan failure counter.

    Parameters
    ----------
    error_class:
        The ``type(exc).__name__`` of the exception caught during the per-recipe
        load inside ``_scan_recipes_dir``.  Common values:

        - ``"RecipeError"``   — YAML schema violation or env-var expansion failure
        - ``"OSError"``       — filesystem permission denied, path not found
        - ``"ValueError"``    — unexpected value during recipe loading
        - ``"sidecar_stale"`` — synthetic class used when a sidecar pointer
                                changed but the full artifact read failed

        Using the exception class name as the label keeps cardinality bounded
        and makes PromQL queries predictable (e.g.
        ``recotem_recipes_dir_scan_failures_total{error_class="RecipeError"}``).
    """
    _ensure_initialized()
    if _RECIPES_DIR_SCAN_FAILURES is None:
        return
    _RECIPES_DIR_SCAN_FAILURES.labels(error_class=error_class).inc()
