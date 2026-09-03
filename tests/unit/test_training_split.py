"""Unit tests for ``recotem.training.split.split_interactions``.

These tests cover:
- ``split.seed`` is plumbed into irspack (deterministic results).
- ``time_user`` and ``time_global`` schemes have distinct semantics:
  * ``time_user`` holds out each user's most recent interactions.
  * ``time_global`` holds out interactions after a global timestamp quantile.
"""

from __future__ import annotations

import numpy as np
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

    assert _matrix_fingerprint(a.X_val_test) == _matrix_fingerprint(b.X_val_test)


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

    assert _matrix_fingerprint(a.X_val_test) != _matrix_fingerprint(b.X_val_test)


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

    assert _matrix_fingerprint(a.X_val_test) == _matrix_fingerprint(b.X_val_test)


# ---------------------------------------------------------------------------
# `random` ignores time_column
# ---------------------------------------------------------------------------
#
# Recipes routinely declare `schema.time_column` regardless of split scheme
# (the pipeline still parses and validates it), and the pipeline forwards it to
# `split_interactions` unconditionally.  irspack's splitter switches to a
# per-user *recency* holdout the moment it receives a time column, so without
# the normalisation in `split_interactions` a `scheme: random` recipe would
# silently be split as `time_user`.


def test_random_scheme_ignores_time_column() -> None:
    """`random` must produce the same split with or without a time column."""
    df = _synth_df(n_users=30, n_items_per_user=10)
    config = SplitConfig(scheme="random", heldout_ratio=0.2, seed=42)
    common = dict(user_column="user_id", item_column="item_id")

    with_time = split_interactions(
        df, **common, time_column="ts", split_config=config
    ).X_val_test
    without_time = split_interactions(
        df, **common, time_column=None, split_config=config
    ).X_val_test

    assert _matrix_fingerprint(with_time) == _matrix_fingerprint(without_time), (
        "time_column changed the `random` split — it leaked into the scheme"
    )


def test_random_scheme_with_time_column_is_not_a_recency_holdout() -> None:
    """`random` + a time column must not hold out each user's latest N.

    ``time_user`` is exactly "hold out each user's most recent
    ``heldout_ratio`` interactions", so comparing against it row by row is a
    direct assertion that the ``random`` holdout is *not* time-ordered.
    """
    df = _synth_df(n_users=30, n_items_per_user=10)
    common = dict(user_column="user_id", item_column="item_id", time_column="ts")

    random_split = split_interactions(
        df,
        **common,
        split_config=SplitConfig(scheme="random", heldout_ratio=0.2, seed=42),
    ).X_val_test
    recency_split = split_interactions(
        df,
        **common,
        split_config=SplitConfig(scheme="time_user", heldout_ratio=0.2, seed=42),
    ).X_val_test

    # Both calls shuffle users with the same seed and derive the item ordering
    # from the same frame in the same process, so row i is the same user in
    # both matrices and column indices are directly comparable.
    random_csr = random_split.tocsr()
    recency_csr = recency_split.tocsr()
    assert random_csr.shape == recency_csr.shape

    n_users = random_csr.shape[0]
    matches = sum(
        set(random_csr[row].indices) == set(recency_csr[row].indices)
        for row in range(n_users)
    )
    # Each user has 10 interactions with 2 held out, so a genuinely random
    # holdout coincides with the recency one for 1 user in 45; matching for
    # even half the users means the split is time-ordered.
    assert matches < n_users // 2, (
        f"`random` held out each user's most recent interactions for "
        f"{matches}/{n_users} users — time_column leaked into the scheme"
    )


# ---------------------------------------------------------------------------
# time_global semantics
# ---------------------------------------------------------------------------


def test_time_global_held_out_interactions_are_after_global_cutoff() -> None:
    """Every held-out interaction must have ts >= the global cutoff."""
    df = _synth_df(n_users=20, n_items_per_user=10)
    heldout_ratio = 0.2
    cutoff = df["ts"].quantile(1.0 - heldout_ratio)

    res = split_interactions(
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

    csr = res.X_val_test.tocsr()
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
# Empty-holdout diagnosis: the message must name the per-user flooring rule
# ---------------------------------------------------------------------------
#
# irspack holds out floor(n_interactions * heldout_ratio) interactions PER
# USER, so a user below 1/heldout_ratio interactions contributes nothing to
# the held-out set no matter how many such users there are.  The message these
# tests pin replaced advice to "increase the dataset size", which does not
# work: only per-user depth, heldout_ratio, or test_user_ratio move the
# outcome.


def _shallow_df(n_users: int, depth: int) -> pd.DataFrame:
    """``n_users`` users with ``depth`` distinct items each, stable ordering."""
    rows = [
        {
            "user_id": f"u{u:04d}",
            "item_id": f"i{(u * 7 + k) % 60:03d}",
            "ts": u * 100 + k,
        }
        for u in range(n_users)
        for k in range(depth)
    ]
    df = pd.DataFrame(rows)
    df["user_id"] = df["user_id"].astype(object)
    df["item_id"] = df["item_id"].astype(object)
    return df


def _empty_holdout_message(df: pd.DataFrame, config: SplitConfig) -> str:
    from recotem.training.errors import SplitError

    with pytest.raises(SplitError) as exc_info:
        split_interactions(
            df,
            user_column="user_id",
            item_column="item_id",
            time_column="ts",
            split_config=config,
        )
    return str(exc_info.value)


@pytest.mark.parametrize("scheme", ["random", "time_user"])
def test_empty_holdout_message_names_the_per_user_floor(scheme: str) -> None:
    """The diagnosis must give the real lever and the observed numbers."""
    config = SplitConfig(scheme=scheme, heldout_ratio=0.1, test_user_ratio=1.0, seed=42)
    message = _empty_holdout_message(_shallow_df(40, 9), config)

    # The rule, the implied minimum depth, and the observed counts.
    assert "floor(n_interactions * heldout_ratio)" in message
    assert "PER USER" in message
    assert "at least 10 distinct items" in message
    assert "none of the 40 users reach that (the deepest has 9)" in message
    # The lever that does NOT work must be ruled out explicitly.
    assert "Adding more users does not help" in message
    assert "increasing the dataset size" not in message
    # A ratio that would actually hold one interaction out of a 9-item user.
    assert "0.12" in message


def test_empty_holdout_more_users_at_same_depth_still_fails() -> None:
    """The claim the message makes must hold: user count is not the lever.

    Ten times the users at the same per-user depth produces the same abort,
    which is why the previous "increase the dataset size" advice was wrong.
    """
    config = SplitConfig(
        scheme="random", heldout_ratio=0.1, test_user_ratio=1.0, seed=42
    )
    small = _empty_holdout_message(_shallow_df(40, 9), config)
    large = _empty_holdout_message(_shallow_df(400, 9), config)

    assert "none of the 40 users reach that" in small
    assert "none of the 400 users reach that" in large


def test_deeper_users_at_the_threshold_produce_a_holdout() -> None:
    """One more interaction per user — the lever the message names — works."""
    config = SplitConfig(
        scheme="random", heldout_ratio=0.1, test_user_ratio=1.0, seed=42
    )
    result = split_interactions(
        _shallow_df(40, 10),
        user_column="user_id",
        item_column="item_id",
        time_column=None,
        split_config=config,
    )
    assert result.X_val_test.nnz > 0


def test_empty_holdout_message_blames_test_user_ratio_when_deep_users_exist() -> None:
    """When qualifying users exist, the message must point at the draw instead.

    ``test_user_ratio`` selects ``int(n_users * ratio)`` validation users; a
    value that rounds down to zero holds nothing out however deep the users
    are, so the per-user-depth advice would be misleading here.
    """
    df = pd.concat(
        [
            _shallow_df(400, 9),
            _shallow_df(2, 20).assign(user_id=["d0"] * 20 + ["d1"] * 20),
        ]
    )
    config = SplitConfig(
        scheme="random", heldout_ratio=0.1, test_user_ratio=0.002, seed=42
    )
    message = _empty_holdout_message(df, config)

    assert "2 of 402 users reach it" in message
    assert "= 0 validation users" in message
    assert "training.split.test_user_ratio" in message
    assert "Adding more users does not help" not in message


def test_empty_holdout_message_reports_qualifying_users_not_drawn() -> None:
    """Deep users that exist but were not drawn get their own diagnosis.

    Here the draw is non-empty, so the message must say the qualifying users
    missed it rather than repeat the ``= 0 validation users`` case.
    """
    df = pd.concat(
        [
            _shallow_df(400, 9),
            _shallow_df(2, 20).assign(user_id=["d0"] * 20 + ["d1"] * 20),
        ]
    )
    config = SplitConfig(
        scheme="random", heldout_ratio=0.1, test_user_ratio=0.05, seed=42
    )
    message = _empty_holdout_message(df, config)

    assert "2 of 402 users reach it (the deepest has 20)" in message
    assert "none of them were among the 20 validation users" in message
    assert "training.split.test_user_ratio" in message
    assert "Adding more users does not help" not in message


def test_empty_holdout_message_for_time_global_is_scheme_specific() -> None:
    """``time_global`` has no per-user floor, so it must not claim one."""
    config = SplitConfig(
        scheme="time_global", heldout_ratio=0.1, test_user_ratio=0.01, seed=42
    )
    message = _empty_holdout_message(_shallow_df(400, 9), config)

    assert "'time_global'" in message
    assert "PER USER" not in message
    assert "quantile of the time column" in message
    assert "training.split.test_user_ratio" in message


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(0.1, 10), (0.2, 5), (0.5, 2), (0.15, 7), (0.01, 100), (0.99, 2)],
)
def test_min_interactions_for_holdout(ratio: float, expected: int) -> None:
    """The advertised minimum must match irspack's own floor arithmetic."""
    import math

    from recotem.training.split import _min_interactions_for_holdout

    assert _min_interactions_for_holdout(ratio) == expected
    assert math.floor(expected * ratio) >= 1
    assert math.floor((expected - 1) * ratio) < 1


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

    res_user = split_interactions(
        df,
        **user_args,
        split_config=SplitConfig(
            scheme="time_user",
            heldout_ratio=0.2,
            seed=42,
        ),
    )
    res_global = split_interactions(
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
    assert _matrix_fingerprint(res_user.X_val_test) != _matrix_fingerprint(
        res_global.X_val_test
    )


# ---------------------------------------------------------------------------
# SplitResult axis labels — item_ids / row_user_ids must align to the
# returned matrices' columns/rows. Feature-aware training (Task 6) relies on
# these labels to build a correctly ordered feature matrix; irspack accepts a
# misordered feature matrix silently, so these axes must be verified rather
# than assumed.
# ---------------------------------------------------------------------------


def _df() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    # STRING ids on purpose: integer ids would pass by accident because
    # hash(int) == int makes list(set(...)) come out sorted.
    for u in range(12):
        for i in rng.choice(8, size=4, replace=False):
            rows.append(
                {
                    "user_id": f"u{u:02d}",
                    "item_id": f"i_{'abcdefgh'[i]}",
                    "ts": 1000 + u * 10 + int(i),
                }
            )
    df = pd.DataFrame(rows)
    # Match _synth_df: force plain-object string dtype. Pandas' default
    # (Arrow-backed) string dtype produces an ArrowStringArray, which
    # irspack's `_split_list` cannot shuffle (not a Sequence subclass).
    df["user_id"] = df["user_id"].astype(object)
    df["item_id"] = df["item_id"].astype(object)
    return df


@pytest.mark.parametrize(
    "scheme,time_col",
    [("random", None), ("time_user", "ts"), ("time_global", "ts")],
)
def test_split_returns_axes(scheme: str, time_col: str | None) -> None:
    # heldout_ratio=0.25: with 4 items/user (as in _synth_df's time_user
    # test), the default 0.1 rounds down to 0 held-out interactions per user
    # for the random/time_user schemes and raises SplitError.
    # test_user_ratio=0.5: the repo default of 1.0 sends every user into the
    # validation split, leaving train.n_users == 0. That degenerates
    # row_user_ids (== train.user_ids + val.user_ids) into literally
    # val.user_ids, so a swapped concatenation order would go undetected.
    # 0.5 guarantees a genuine non-empty train block for every scheme.
    res = split_interactions(
        _df(),
        user_column="user_id",
        item_column="item_id",
        time_column=time_col,
        split_config=SplitConfig(
            scheme=scheme, heldout_ratio=0.25, test_user_ratio=0.5
        ),
    )
    assert len(res.item_ids) == res.X_train_full.shape[1]
    assert len(res.row_user_ids) == res.X_train_full.shape[0]
    assert all(isinstance(i, str) for i in res.item_ids)
    assert all(isinstance(u, str) for u in res.row_user_ids)


@pytest.mark.parametrize(
    "scheme,time_col",
    [("random", None), ("time_user", "ts"), ("time_global", "ts")],
)
def test_item_ids_label_the_columns(scheme: str, time_col: str | None) -> None:
    """Reconstructing interactions from the returned axes must match the input."""
    df = _df()
    # test_user_ratio=0.5: see test_split_returns_axes for why the default
    # 1.0 would make this test blind to a swapped row_user_ids concatenation
    # order (train.n_users == 0 collapses train+val into just val).
    res = split_interactions(
        df,
        user_column="user_id",
        item_column="item_id",
        time_column=time_col,
        split_config=SplitConfig(
            scheme=scheme, heldout_ratio=0.25, test_user_ratio=0.5
        ),
    )
    X = res.X_train_full.tocoo()
    recon = {
        (res.row_user_ids[r], res.item_ids[c])
        for r, c in zip(X.row, X.col, strict=True)
    }
    truth = set(zip(df["user_id"], df["item_id"], strict=True))
    assert recon <= truth, "reconstructed pairs must all be real interactions"
    assert recon, "reconstruction must not be empty"


def test_val_offset_still_points_at_validation_users() -> None:
    # test_user_ratio=0.5: see test_split_returns_axes for why the default
    # 1.0 would make this test blind to a swapped row_user_ids concatenation
    # order (train.n_users == 0 collapses train+val into just val).
    res = split_interactions(
        _df(),
        user_column="user_id",
        item_column="item_id",
        time_column=None,
        split_config=SplitConfig(
            scheme="random", heldout_ratio=0.25, test_user_ratio=0.5
        ),
    )
    assert res.X_val_test.shape[0] == res.X_train_full.shape[0] - res.val_offset
