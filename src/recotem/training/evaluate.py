"""Evaluator setup for irspack.

Wraps ``irspack.Evaluator`` construction given a recipe's metric and cutoff.
"""

from __future__ import annotations

import scipy.sparse as sps

# _compat applies IPython stub before irspack imports (see _compat.py).
import recotem.training._compat  # noqa: F401
from irspack import Evaluator

from recotem.training.errors import TrainingError

# Metric names accepted by the recipe schema -> irspack target_metric strings
_METRIC_MAP: dict[str, str] = {
    "ndcg": "ndcg",
    "map": "map",
    "recall": "recall",
    "hit": "hit",
}


def build_evaluator(
    X_test: sps.spmatrix,
    offset: int,
    metric: str,
    cutoff: int,
) -> Evaluator:
    """Construct an irspack ``Evaluator`` for the given metric and cutoff.

    Parameters
    ----------
    X_test:
        Sparse test interaction matrix (held-out rows).
    offset:
        Row offset into the full user index (i.e. number of train-only users).
    metric:
        Recipe metric string: one of ``ndcg``, ``map``, ``recall``, ``hit``.
    cutoff:
        Recommendation list length.

    Returns
    -------
    Evaluator

    Raises
    ------
    TrainingError
        If *metric* is not in the recognised set.
    """
    irspack_metric = _METRIC_MAP.get(metric.lower())
    if irspack_metric is None:
        raise TrainingError(
            f"Unsupported metric {metric!r}. "
            f"Must be one of: {sorted(_METRIC_MAP)}.",
            code="invalid_metric",
        )
    return Evaluator(
        X_test,
        offset=offset,
        target_metric=irspack_metric,
        cutoff=cutoff,
    )


def get_score(evaluator: Evaluator, recommender: object) -> float:
    """Return the scalar target-metric score for *recommender* via *evaluator*.

    Negates sign conventions are handled here: irspack ``Evaluator.get_score``
    returns a dict; this helper extracts the target metric value as a float.
    """
    score_dict: dict[str, float] = evaluator.get_score(recommender)
    return float(score_dict[evaluator.target_metric.name])
