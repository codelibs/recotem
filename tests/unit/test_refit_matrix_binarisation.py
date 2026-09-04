"""The final refit must train on the matrix the search actually scored.

``split_dataframe_partial_user_holdout`` (search) binarises.  ``df_to_sparse``
(final refit) builds ``csr_matrix((ones, (row, col)))`` and scipy sums duplicate
coordinates, so a frame that still holds repeat ``(user, item)`` pairs -- which
is exactly what ``cleansing.dedup: none`` preserves -- produced confidence
weights in the refit only.  Hyperparameters were then chosen against 0/1 while
the shipped model was built on counts, and ``best_score`` described neither.

``irspack.utils`` is imported inside each test rather than at module scope: the
import chain reaches ``lightfm``, whose no-OpenMP ``UserWarning`` the suite
turns into an error, and ``recotem.training.pipeline`` is what installs the
shim that silences it (``recotem._lightfm_compat``).  Importing it first at
module scope is what makes the function-level imports below safe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from recotem.training.pipeline import _build_final_matrix


def _repeat_heavy_frame() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    rows = [
        (f"u{u}", f"i{int(rng.integers(0, 30))}")
        for u in range(200)
        for _ in range(int(rng.integers(10, 40)))
    ]
    return pd.DataFrame(rows, columns=["user_id", "item_id"])


def test_duplicate_pairs_would_otherwise_become_confidence_weights() -> None:
    """Pins the upstream behaviour the binarisation exists for."""
    from irspack.utils import df_to_sparse

    raw, _, _ = df_to_sparse(_repeat_heavy_frame(), "user_id", "item_id")
    assert raw.data.max() > 1, (
        "df_to_sparse no longer sums duplicate coordinates. If scipy or irspack "
        "changed this, the binarisation in _build_final_matrix may be redundant "
        "-- confirm against the search path before removing it."
    )


def test_refit_matrix_is_binary_when_duplicates_survive_cleansing() -> None:
    X, _, _ = _build_final_matrix(_repeat_heavy_frame(), "user_id", "item_id")
    assert X.data.max() == 1
    assert X.data.min() == 1


def test_binarisation_preserves_the_sparsity_pattern_and_axes() -> None:
    from irspack.utils import df_to_sparse

    df = _repeat_heavy_frame()
    raw, raw_u, raw_i = df_to_sparse(df, "user_id", "item_id")
    X, uids, iids = _build_final_matrix(df, "user_id", "item_id")
    assert X.nnz == raw.nnz
    assert (X.indices == raw.indices).all()
    assert (X.indptr == raw.indptr).all()
    assert list(uids) == list(raw_u)
    assert list(iids) == list(raw_i)


def test_deduplicated_frame_is_untouched() -> None:
    """The default (``keep_last``) path must be bit-identical to before."""
    from irspack.utils import df_to_sparse

    df = _repeat_heavy_frame().drop_duplicates(subset=["user_id", "item_id"])
    raw, _, _ = df_to_sparse(df, "user_id", "item_id")
    X, _, _ = _build_final_matrix(df, "user_id", "item_id")
    assert raw.data.max() == 1
    assert (X.data == raw.data).all()
