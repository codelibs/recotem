"""Train/test split wrapper around irspack.

Implements the three recipe split schemes:
- ``random``      → ``irspack.split_dataframe_partial_user_holdout`` (no time_column).
- ``time_user``   → same helper, with ``time_column`` set so each user's most
                    recent interactions are held out.
- ``time_global`` → global timestamp quantile cutoff via
                    ``irspack.split.holdout_specific_interactions``.

Raises ``SplitError`` (a ``TrainingError`` subclass) for any structural
problem with the resulting split.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import scipy.sparse as sps
from irspack import split_dataframe_partial_user_holdout
from irspack.split import holdout_specific_interactions

# _compat applies IPython stub before irspack imports (see _compat.py).
import recotem.training._compat  # noqa: F401
from recotem.recipe.models import SplitConfig
from recotem.training.errors import SplitError


@dataclass(frozen=True)
class SplitResult:
    """Split matrices plus the axis labels needed to align a feature matrix.

    ``item_ids`` labels ``X_train_full``'s columns; ``row_user_ids`` labels its
    rows.  Both are load-bearing for feature-aware training: irspack accepts a
    misordered feature matrix silently, and the search-phase item order is
    ``list(set(...))`` -- neither sorted nor stable across processes for string
    ids -- so a feature matrix must be rebuilt in-process from these labels
    rather than cached or reused from the final-refit ordering.
    """

    X_train_full: sps.spmatrix
    X_val_test: sps.spmatrix
    val_offset: int
    item_ids: list[str]
    row_user_ids: list[str]


def split_interactions(
    df: pd.DataFrame,
    *,
    user_column: str,
    item_column: str,
    time_column: str | None,
    split_config: SplitConfig,
) -> SplitResult:
    """Split *df* into train and validation sparse matrices.

    Returns
    -------
    SplitResult
        ``X_train_full``: full combined train+val train-side matrix (used for
        final training).
        ``X_val_test``: held-out test interactions for validation users (used
        for evaluation).
        ``val_offset``: row offset into the full user index pointing to the
        first validation user (``train.n_users`` in irspack terminology).
        ``item_ids``: item vocabulary labelling ``X_train_full``'s columns.
        ``row_user_ids``: user ids labelling ``X_train_full``'s rows, in
        train-then-val order.

    Raises
    ------
    SplitError
        If the split produces an empty test set, or if a time-based scheme is
        requested but the time column is absent / unparseable.
    """
    scheme = split_config.scheme

    if scheme in ("time_user", "time_global") and time_column is None:
        raise SplitError(
            f"Split scheme {scheme!r} requires a time_column but none is "
            "configured in schema.time_column."
        )

    try:
        if scheme == "time_global":
            assert time_column is not None  # narrowed by the check above
            dataset, item_all = _split_time_global(
                df,
                user_column=user_column,
                item_column=item_column,
                time_column=time_column,
                split_config=split_config,
            )
        else:
            # `random` (time_column is None) and `time_user` (time_column set)
            # both use partial_user_holdout.
            dataset, item_all = split_dataframe_partial_user_holdout(
                df,
                user_column=user_column,
                item_column=item_column,
                time_column=time_column,
                val_user_ratio=split_config.test_user_ratio,
                test_user_ratio=0.0,
                heldout_ratio_val=split_config.heldout_ratio,
                random_state=split_config.seed,
            )
    except SplitError:
        raise
    except (MemoryError, RecursionError):
        raise
    except Exception as exc:
        raise SplitError(f"irspack split failed: {exc}") from exc

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

    # Row order is train-then-val and is NOT a free choice: irspack's Evaluator
    # pins rows [val_offset:] to the validation ground truth (evaluator.py:46).
    # Do not sort.
    row_user_ids = [str(u) for u in train.user_ids] + [str(u) for u in val.user_ids]
    item_ids = [str(i) for i in item_all]

    if len(item_ids) != X_train_full.shape[1]:
        raise SplitError(
            f"internal: item vocabulary size {len(item_ids)} does not match "
            f"matrix column count {X_train_full.shape[1]}"
        )
    if len(row_user_ids) != X_train_full.shape[0]:
        raise SplitError(
            f"internal: user row labels {len(row_user_ids)} do not match "
            f"matrix row count {X_train_full.shape[0]}"
        )

    return SplitResult(
        X_train_full=X_train_full,
        X_val_test=X_val_test,
        val_offset=val_offset,
        item_ids=item_ids,
        row_user_ids=row_user_ids,
    )


def _split_time_global(
    df: pd.DataFrame,
    *,
    user_column: str,
    item_column: str,
    time_column: str,
    split_config: SplitConfig,
) -> tuple[dict, list]:
    """Hold out every interaction at or after the global timestamp quantile.

    The cutoff is the ``1 - heldout_ratio`` quantile of ``df[time_column]``.
    Users whose interactions all fall before the cutoff become train-only;
    users with at least one post-cutoff interaction become validation users
    (a fraction controlled by ``test_user_ratio``).
    """
    if df.empty:
        raise SplitError(
            "time_global split requires at least one interaction; got an "
            "empty DataFrame."
        )

    cutoff = df[time_column].quantile(1.0 - split_config.heldout_ratio)
    indicator = (df[time_column] >= cutoff).to_numpy()
    if not indicator.any():
        raise SplitError(
            f"time_global split produced no held-out interactions at "
            f"cutoff={cutoff!r}; check heldout_ratio and time_column values."
        )

    item_all, dataset = holdout_specific_interactions(
        df,
        user_column=user_column,
        item_column=item_column,
        interaction_indicator=indicator,
        validatable_user_ratio_val=split_config.test_user_ratio,
        validatable_user_ratio_test=0.0,
        random_state=split_config.seed,
    )
    # holdout_specific_interactions returns (item_ids, dataset). This first
    # value is the ONLY source of the item vocabulary for this scheme: both
    # returned datasets have item_ids is None (specified.py:118,138-140).
    return dataset, list(item_all)
