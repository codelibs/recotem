"""Train/test split wrapper around irspack.

Implements the three recipe split schemes:
- ``random``      → ``irspack.split_dataframe_partial_user_holdout`` with
                    ``time_column`` forced to ``None``, so the holdout is
                    uniform at random per user even when the recipe declares a
                    time column.
- ``time_user``   → same helper, with ``time_column`` set so each user's most
                    recent interactions are held out.
- ``time_global`` → global timestamp quantile cutoff via
                    ``irspack.split.holdout_specific_interactions``.

Raises ``SplitError`` (a ``TrainingError`` subclass) for any structural
problem with the resulting split.
"""

from __future__ import annotations

import math
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

    *time_column* is honoured only by the two time-based schemes. Under
    ``random`` it is ignored outright, so callers may pass
    ``schema.time_column`` unconditionally without changing the split.

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

    # `random` means "hold out uniformly at random per user"; the time column
    # is no part of that definition.  Normalise it away HERE rather than at the
    # call site, because irspack's splitter switches to a per-user *recency*
    # holdout the moment it is handed a ``time_column`` — so any caller that
    # forwards ``schema.time_column`` unconditionally would silently get a
    # ``time_user`` split while asking for ``random``.  The training pipeline
    # does exactly that, and legitimately so: the column is still parsed and
    # validated regardless of scheme.  Normalising on this side keeps the
    # scheme the single source of truth for every present and future caller.
    if scheme == "random":
        time_column = None

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
            # `random` (time_column normalised to None above) and `time_user`
            # (time_column set) both use partial_user_holdout.
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
            _empty_holdout_message(
                df,
                user_column=user_column,
                item_column=item_column,
                split_config=split_config,
            )
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


def _min_interactions_for_holdout(heldout_ratio: float) -> int:
    """Smallest per-user interaction count that yields one held-out interaction.

    irspack holds out ``floor(n * heldout_ratio)`` interactions per user
    (``ceil_n_heldout`` defaults to False and recotem never overrides it), so
    the answer is the smallest ``n`` with ``floor(n * ratio) >= 1``.  Searched
    upward from ``floor(1 / ratio)`` using the same float arithmetic irspack
    performs, rather than returned as ``ceil(1 / ratio)``, so a ratio that is
    not exactly representable in binary cannot shift the answer by one and
    make the advice wrong at the boundary.
    """
    n = max(1, math.floor(1.0 / heldout_ratio))
    while math.floor(n * heldout_ratio) < 1:
        n += 1
    return n


def _empty_holdout_message(
    df: pd.DataFrame,
    *,
    user_column: str,
    item_column: str,
    split_config: SplitConfig,
) -> str:
    """Diagnose an empty held-out set against the frame that produced it.

    The generic advice this replaced ("increase the dataset size") is wrong
    for the two per-user schemes: the holdout is floored PER USER, so more
    users at the same per-user depth changes nothing.  Only per-user depth,
    ``heldout_ratio`` or ``test_user_ratio`` move the outcome, and which one
    applies is decided here from the actual counts.  Runs only on the failure
    path, so the extra groupby costs nothing in the normal case.
    """
    ratio = split_config.heldout_ratio
    scheme = split_config.scheme
    test_user_ratio = split_config.test_user_ratio

    if scheme == "time_global":
        # No per-user floor here: the cutoff is global, and every interaction
        # at or after it is held out.  An empty result therefore means the
        # post-cutoff interactions all belong to users that were not drawn as
        # validation users.
        return (
            "Split produced an empty held-out test set. The 'time_global' "
            "scheme holds out every interaction at or after the "
            f"{1.0 - ratio:g} quantile of the time column, then evaluates only "
            "the users drawn as validation users out of those that have a "
            f"post-cutoff interaction (test_user_ratio={test_user_ratio:g}); "
            "none of the post-cutoff interactions belong to a drawn user. "
            "Raise training.split.test_user_ratio (1.0 draws every such user) "
            "or raise training.split.heldout_ratio to move the cutoff earlier."
        )

    # irspack drops duplicate (user, item) pairs before splitting, so a user's
    # effective depth is their distinct-item count, not their row count.
    per_user = df.groupby(user_column, observed=True)[item_column].nunique()
    n_users = int(per_user.size)
    if n_users == 0:
        return (
            "Split produced an empty held-out test set: the frame handed to "
            "the splitter contains no users."
        )

    deepest = int(per_user.max())
    min_depth = _min_interactions_for_holdout(ratio)
    n_qualifying = int((per_user >= min_depth).sum())
    # irspack draws int(n_users * test_user_ratio) validation users; only
    # their interactions are candidates for the holdout.
    n_val_users = int(n_users * test_user_ratio)

    rule = (
        f"Split produced an empty held-out test set. The {scheme!r} scheme "
        "holds out floor(n_interactions * heldout_ratio) interactions PER "
        f"USER, so at heldout_ratio={ratio:g} a user needs at least "
        f"{min_depth} distinct items before any of them can be held out"
    )

    if n_qualifying == 0:
        # Ceiling to two decimals keeps the suggestion readable and stays
        # above the exact threshold 1/deepest.
        suggested = math.ceil(100.0 / deepest) / 100.0
        remedy = (
            f"Raise training.split.heldout_ratio to {suggested:g} or more, or "
            "supply deeper per-user histories."
            if suggested < 1.0
            else (
                "No heldout_ratio below 1.0 can hold out an interaction from a "
                f"{deepest}-interaction user; supply deeper per-user histories."
            )
        )
        return (
            f"{rule}; none of the {n_users} users reach that (the deepest has "
            f"{deepest}). Adding more users does not help — only per-user "
            f"depth does. {remedy}"
        )

    if n_val_users == 0:
        return (
            f"{rule}; {n_qualifying} of {n_users} users reach it, but "
            f"test_user_ratio={test_user_ratio:g} draws "
            f"int({n_users} * {test_user_ratio:g}) = 0 validation users, so no "
            "interaction was eligible for the holdout. Raise "
            "training.split.test_user_ratio (1.0 draws every user)."
        )

    return (
        f"{rule}; {n_qualifying} of {n_users} users reach it (the deepest has "
        f"{deepest}), but none of them were among the {n_val_users} validation "
        f"users drawn at test_user_ratio={test_user_ratio:g}. Raise "
        "training.split.test_user_ratio (1.0 draws every user), or raise "
        "training.split.heldout_ratio so more users qualify."
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
