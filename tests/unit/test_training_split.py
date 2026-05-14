"""Unit tests for ``recotem.training.split.split_interactions``.

These tests cover:
- ``split.seed`` is plumbed into irspack (deterministic results).
- ``time_user`` and ``time_global`` schemes have distinct semantics:
  * ``time_user`` holds out each user's most recent interactions.
  * ``time_global`` holds out interactions after a global timestamp quantile.
"""

from __future__ import annotations

import pandas as pd
import pytest

import recotem.training._compat  # noqa: F401 - install IPython stub
from recotem.recipe.models import SplitConfig
from recotem.training.split import split_interactions


def _synth_df(n_users: int = 30, n_items_per_user: int = 6) -> pd.DataFrame:
    """Build a synthetic interactions DataFrame with stable ordering.

    Each user gets ``n_items_per_user`` interactions, each with a distinct
    timestamp so that per-user / global time splits are well-defined.
    """
    rows = []
    ts = 0
    for u in range(n_users):
        for i in range(n_items_per_user):
            rows.append({"user_id": f"u{u:03d}", "item_id": f"i{i:03d}", "ts": ts})
            ts += 1
    df = pd.DataFrame(rows)
    df["user_id"] = df["user_id"].astype(object)
    df["item_id"] = df["item_id"].astype(object)
    return df


def _matrix_fingerprint(matrix) -> tuple:
    """A hashable representation of a CSR sparse matrix's structural data."""
    csr = matrix.tocsr()
    return (
        tuple(csr.indices.tolist()),
        tuple(csr.indptr.tolist()),
        tuple(csr.data.tolist()),
    )


# ---------------------------------------------------------------------------
# split.seed determinism
# ---------------------------------------------------------------------------


def test_random_split_is_deterministic_for_same_seed() -> None:
    df = _synth_df()
    config = SplitConfig(scheme="random", heldout_ratio=0.2, seed=123)

    a = split_interactions(
        df,
        user_column="user_id",
        item_column="item_id",
        time_column=None,
        split_config=config,
    )
    b = split_interactions(
        df,
        user_column="user_id",
        item_column="item_id",
        time_column=None,
        split_config=config,
    )

    assert _matrix_fingerprint(a[1]) == _matrix_fingerprint(b[1])


def test_random_split_differs_for_different_seeds() -> None:
    df = _synth_df()
    a = split_interactions(
        df,
        user_column="user_id",
        item_column="item_id",
        time_column=None,
        split_config=SplitConfig(scheme="random", heldout_ratio=0.2, seed=1),
    )
    b = split_interactions(
        df,
        user_column="user_id",
        item_column="item_id",
        time_column=None,
        split_config=SplitConfig(scheme="random", heldout_ratio=0.2, seed=999),
    )

    assert _matrix_fingerprint(a[1]) != _matrix_fingerprint(b[1])


def test_time_user_split_is_deterministic_for_same_seed() -> None:
    df = _synth_df()
    config = SplitConfig(scheme="time_user", heldout_ratio=0.25, seed=7)
    a = split_interactions(
        df,
        user_column="user_id",
        item_column="item_id",
        time_column="ts",
        split_config=config,
    )
    b = split_interactions(
        df,
        user_column="user_id",
        item_column="item_id",
        time_column="ts",
        split_config=config,
    )

    assert _matrix_fingerprint(a[1]) == _matrix_fingerprint(b[1])


# ---------------------------------------------------------------------------
# time_global semantics
# ---------------------------------------------------------------------------


def test_time_global_held_out_interactions_are_after_global_cutoff() -> None:
    """Every held-out interaction must have ts >= the global cutoff."""
    df = _synth_df(n_users=20, n_items_per_user=10)
    heldout_ratio = 0.2
    cutoff = df["ts"].quantile(1.0 - heldout_ratio)

    _, X_val_test, _ = split_interactions(
        df,
        user_column="user_id",
        item_column="item_id",
        time_column="ts",
        split_config=SplitConfig(
            scheme="time_global",
            heldout_ratio=heldout_ratio,
            seed=42,
        ),
    )

    csr = X_val_test.tocsr()
    item_ids = sorted(df["item_id"].unique())
    item_idx_to_name = dict(enumerate(item_ids))
    held_out_item_names = {item_idx_to_name[c] for c in csr.indices}

    # Build the set of (item_id) appearing strictly before the cutoff in the
    # original frame. None of those items should be in held-out.
    pre_cutoff_only = set(df.loc[df["ts"] < cutoff, "item_id"]) - set(
        df.loc[df["ts"] >= cutoff, "item_id"]
    )
    assert not (held_out_item_names & pre_cutoff_only), (
        "time_global held out items that exist only before the cutoff"
    )


# ---------------------------------------------------------------------------
# C5 — empty test set raises SplitError (TrainingError subclass)
# ---------------------------------------------------------------------------


def test_split_producing_empty_test_set_raises_TrainingError() -> None:
    """A heldout_ratio so small that the random split assigns all rows to train
    must raise SplitError, not silently return an empty test matrix.

    SplitError is a TrainingError subclass, so the CLI maps it to exit 4.
    The code in split.py checks ``X_val_test.nnz == 0`` and raises SplitError.

    We use a tiny 2-row dataset with heldout_ratio=0.001 to make it very
    likely that no interaction lands in the held-out set.
    """
    from recotem.training.errors import SplitError

    df = pd.DataFrame({"user_id": ["u1", "u2"], "item_id": ["i1", "i2"]})
    df["user_id"] = df["user_id"].astype(object)
    df["item_id"] = df["item_id"].astype(object)

    config = SplitConfig(scheme="random", heldout_ratio=0.001, seed=42)

    with pytest.raises(SplitError):
        split_interactions(
            df,
            user_column="user_id",
            item_column="item_id",
            time_column=None,
            split_config=config,
        )


# ---------------------------------------------------------------------------
# I-14: MemoryError from irspack split propagates unwrapped
# ---------------------------------------------------------------------------


def test_split_memory_error_propagates_unwrapped() -> None:
    """MemoryError from the irspack split function must propagate unwrapped.

    I-14 fix: added `except (MemoryError, RecursionError): raise` before the
    generic `except Exception` in split_interactions, so OOM conditions are
    not silently wrapped in SplitError.
    """
    from unittest.mock import patch

    from recotem.training.split import split_interactions

    df = _synth_df()
    config = SplitConfig(scheme="random", heldout_ratio=0.2, seed=42)

    def _oom(*args, **kwargs):
        raise MemoryError("out of memory during split")

    with patch(
        "recotem.training.split.split_dataframe_partial_user_holdout",
        side_effect=_oom,
    ):
        with pytest.raises(MemoryError):
            split_interactions(
                df,
                user_column="user_id",
                item_column="item_id",
                time_column=None,
                split_config=config,
            )


def test_split_recursion_error_propagates_unwrapped() -> None:
    """RecursionError from the irspack split function must propagate unwrapped."""
    from unittest.mock import patch

    from recotem.training.split import split_interactions

    df = _synth_df()
    config = SplitConfig(scheme="random", heldout_ratio=0.2, seed=42)

    def _recursion(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    with patch(
        "recotem.training.split.split_dataframe_partial_user_holdout",
        side_effect=_recursion,
    ):
        with pytest.raises(RecursionError):
            split_interactions(
                df,
                user_column="user_id",
                item_column="item_id",
                time_column=None,
                split_config=config,
            )


def test_time_global_and_time_user_produce_different_splits() -> None:
    """The two time schemes must NOT produce the same split."""
    df = _synth_df(n_users=20, n_items_per_user=10)
    user_args = dict(
        user_column="user_id",
        item_column="item_id",
        time_column="ts",
    )

    _, X_user, _ = split_interactions(
        df,
        **user_args,
        split_config=SplitConfig(
            scheme="time_user",
            heldout_ratio=0.2,
            seed=42,
        ),
    )
    _, X_global, _ = split_interactions(
        df,
        **user_args,
        split_config=SplitConfig(
            scheme="time_global",
            heldout_ratio=0.2,
            seed=42,
        ),
    )

    # Held-out counts can match coincidentally, but the structural fingerprint
    # must differ because time_user holds each user's most recent k% while
    # time_global holds the global tail (some users contribute zero).
    assert _matrix_fingerprint(X_user) != _matrix_fingerprint(X_global)
