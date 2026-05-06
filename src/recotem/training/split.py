"""Train/test split wrapper around irspack.

Wraps ``irspack.split_dataframe_partial_user_holdout`` and honours the
recipe ``SplitConfig`` scheme.  Raises ``SplitError`` (a ``TrainingError``
subclass) for any structural problem with the resulting split.
"""

from __future__ import annotations

import pandas as pd
import scipy.sparse as sps

# _compat applies IPython stub before irspack imports (see _compat.py).
import recotem.training._compat  # noqa: F401
from irspack import split_dataframe_partial_user_holdout

from recotem.recipe.models import SplitConfig
from recotem.training.errors import SplitError


def split_interactions(
    df: pd.DataFrame,
    *,
    user_column: str,
    item_column: str,
    time_column: str | None,
    split_config: SplitConfig,
) -> tuple[sps.spmatrix, sps.spmatrix, int]:
    """Split *df* into train and validation sparse matrices.

    Returns
    -------
    X_train_full:
        Full combined train+val train-side matrix (used for final training).
    X_val_test:
        Held-out test interactions for validation users (used for evaluation).
    val_offset:
        Row offset into the full user index pointing to the first validation
        user (``train.n_users`` in irspack terminology).

    Raises
    ------
    SplitError
        If the split produces an empty test set, or if a time-based scheme is
        requested but the time column is absent / unparseable.
    """
    scheme = split_config.scheme

    # Validate time column availability for time-based schemes.
    if scheme in ("time_user", "time_global") and time_column is None:
        raise SplitError(
            f"Split scheme {scheme!r} requires a time_column but none is "
            "configured in schema.time_column."
        )

    try:
        dataset, _ = split_dataframe_partial_user_holdout(
            df,
            user_column=user_column,
            item_column=item_column,
            time_column=time_column,
            val_user_ratio=split_config.test_user_ratio,
            test_user_ratio=0.0,
            heldout_ratio_val=split_config.heldout_ratio,
        )
    except Exception as exc:
        raise SplitError(
            f"irspack split failed: {exc}"
        ) from exc

    train = dataset["train"]
    val = dataset["val"]

    X_val_test: sps.spmatrix = val.X_test
    if X_val_test.nnz == 0:
        raise SplitError(
            "Split produced an empty held-out test set. "
            "Try reducing heldout_ratio or increasing the dataset size."
        )

    X_train_full: sps.spmatrix = sps.vstack([train.X_train, val.X_train])
    val_offset: int = train.n_users

    return X_train_full, X_val_test, val_offset
