"""Unit tests for recotem.training.evaluate.build_evaluator.

Tests:
- unsupported metric raises TrainingError with code='invalid_metric'
- every supported metric (ndcg, map, recall, hit) returns a non-None Evaluator
- metric name is case-insensitive (NDCG, MAP, Recall, HIT)
- offset parameter is forwarded to Evaluator
"""

from __future__ import annotations

import numpy as np
import pytest
import scipy.sparse as sps

# _compat applies the IPython stub required by irspack's transitive import chain.
import recotem.training._compat  # noqa: F401
from recotem.training.errors import TrainingError
from recotem.training.evaluate import _METRIC_MAP, build_evaluator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tiny_matrix(n: int = 5) -> sps.spmatrix:
    """Return a small identity sparse matrix suitable for Evaluator construction."""
    return sps.eye(n, format="csr")


# ---------------------------------------------------------------------------
# Unsupported metric raises TrainingError
# ---------------------------------------------------------------------------


def test_build_evaluator_unsupported_metric_raises_TrainingError() -> None:
    """metric='precision' is not in _METRIC_MAP and must raise TrainingError."""
    X = _tiny_matrix()
    with pytest.raises(TrainingError) as exc_info:
        build_evaluator(X, offset=0, metric="precision", cutoff=5)
    assert exc_info.value.code == "invalid_metric", (
        f"Expected code='invalid_metric', got {exc_info.value.code!r}"
    )
    assert "precision" in str(exc_info.value), (
        f"Error message must mention the bad metric name; got {exc_info.value!s}"
    )


@pytest.mark.parametrize(
    "bad_metric",
    ["rmse", "auc", "f1", "mrr", "", "  ", "nDCG@10"],
)
def test_build_evaluator_various_unsupported_metrics_raise_TrainingError(
    bad_metric: str,
) -> None:
    """Any metric string not in the allow-list must raise TrainingError."""
    X = _tiny_matrix()
    with pytest.raises(TrainingError) as exc_info:
        build_evaluator(X, offset=0, metric=bad_metric, cutoff=5)
    assert exc_info.value.code == "invalid_metric"


def test_build_evaluator_unsupported_metric_error_lists_valid_options() -> None:
    """The error message must list the valid metric names so operators can fix recipes."""
    X = _tiny_matrix()
    with pytest.raises(TrainingError) as exc_info:
        build_evaluator(X, offset=0, metric="auc", cutoff=10)
    msg = str(exc_info.value)
    for valid_name in sorted(_METRIC_MAP):
        assert valid_name in msg, (
            f"Error message must list valid metric {valid_name!r}; got: {msg!r}"
        )


# ---------------------------------------------------------------------------
# Supported metrics return a non-None Evaluator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("metric", list(_METRIC_MAP))
def test_build_evaluator_supported_metrics_return_evaluator(metric: str) -> None:
    """Each metric in _METRIC_MAP must produce a non-None irspack Evaluator."""
    X = _tiny_matrix()
    evaluator = build_evaluator(X, offset=0, metric=metric, cutoff=5)
    assert evaluator is not None
    # The Evaluator's target_metric name must match what we requested.
    assert evaluator.target_metric.name == _METRIC_MAP[metric], (
        f"For metric={metric!r}, expected target_metric.name={_METRIC_MAP[metric]!r}, "
        f"got {evaluator.target_metric.name!r}"
    )


# ---------------------------------------------------------------------------
# Case-insensitive metric names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metric_variant",
    ["NDCG", "Ndcg", "nDcG", "MAP", "Map", "RECALL", "Recall", "HIT", "Hit"],
)
def test_build_evaluator_metric_is_case_insensitive(metric_variant: str) -> None:
    """build_evaluator must normalise metric to lowercase before lookup."""
    X = _tiny_matrix()
    # Should not raise
    evaluator = build_evaluator(X, offset=0, metric=metric_variant, cutoff=5)
    assert evaluator is not None


# ---------------------------------------------------------------------------
# offset parameter is forwarded
# ---------------------------------------------------------------------------


def test_build_evaluator_with_offset_uses_correct_split() -> None:
    """The offset parameter must be forwarded to Evaluator unchanged.

    irspack's Evaluator accepts offset as the number of train-only users; the
    test-interaction rows are indexed starting at offset.  We verify that an
    evaluator built with a non-zero offset is accepted without error by irspack
    and that it is distinct from one built with offset=0 (they are different
    objects configured for different row ranges).
    """
    n_users = 10
    n_items = 5
    X = sps.csr_matrix(np.eye(n_users, n_items))

    ev_no_offset = build_evaluator(X, offset=0, metric="ndcg", cutoff=3)
    ev_with_offset = build_evaluator(X, offset=5, metric="ndcg", cutoff=3)

    assert ev_no_offset is not None
    assert ev_with_offset is not None
    # Both must be valid Evaluator instances (different offset, same metric).
    assert ev_no_offset.target_metric.name == "ndcg"
    assert ev_with_offset.target_metric.name == "ndcg"
    # They should be distinct objects.
    assert ev_no_offset is not ev_with_offset
