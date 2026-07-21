"""Unit tests for recotem.training.features.

Tests:
- load_feature_tables(None, ...) returns an empty FeatureTables.
- load_feature_tables builds an encoder state from a fetched CSV feature
  table (item side); n_features accounts for the bias column.
- numeric feature columns keep their dtype through the fetch -> state path
  (only the id_column is string-coerced).
- encode_for_axis reindexes onto the supplied item_order and omits absent
  sides from its result dict.
- a missing id_column raises with the offending name in the message.
- rows with a null/empty id are dropped before the vocabulary is built.
- a dimension-cap breach (recotem._features.FeatureEncodeError) is wrapped
  into TrainingError so it maps to exit 4, not exit 1.
- an unregistered source type raises DataSourceError (exit 3) unwrapped,
  same as the main interaction source.
- encode_for_axis raises TrainingError if a configured side's order is
  omitted (internal-misuse guard, not reachable through the public pipeline).
- encode_for_axis refuses a feature table with ZERO id overlap against the
  interaction axis (the silent all-bias bug), logs coverage at INFO, and
  still permits a legitimately partial-covering table.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# _compat applies the IPython stub required by irspack's transitive import chain.
import recotem.training._compat  # noqa: F401
from recotem.datasource.base import DataSourceError
from recotem.recipe.models import FeatureColumn, FeaturesConfig, FeatureSideConfig
from recotem.training.errors import TrainingError
from recotem.training.features import encode_for_axis, load_feature_tables


@pytest.fixture
def items_csv(tmp_path: Path) -> str:
    p = tmp_path / "items.csv"
    pd.DataFrame(
        {
            "item_id": ["i_a", "i_b", "i_c"],
            "genre": ["action", "drama", "action"],
            "year": [2000, 2010, 2020],
        }
    ).to_csv(p, index=False)
    return str(p)


def _features(items_csv: str) -> FeaturesConfig:
    return FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": items_csv},
            id_column="item_id",
            columns=[
                FeatureColumn(name="genre", encoding="categorical"),
                FeatureColumn(name="year", encoding="numerical"),
            ],
        )
    )


def test_load_feature_tables_none_returns_empty() -> None:
    t = load_feature_tables(None, recipe_name="r", run_id="run")
    assert t.item_state is None and t.user_state is None
    assert t.enabled is False


def test_load_feature_tables_builds_state(items_csv: str) -> None:
    t = load_feature_tables(_features(items_csv), recipe_name="r", run_id="run")
    # 2 genre one-hots + 1 standardized year + 1 bias
    assert t.item_state["n_features"] == 4
    assert t.user_state is None
    assert t.enabled is True


def test_numeric_column_is_not_stringified(items_csv: str) -> None:
    """The CSV datasource must preserve dtypes; metadata/loader forces str."""
    t = load_feature_tables(_features(items_csv), recipe_name="r", run_id="run")
    spec = next(s for s in t.item_state["columns"] if s["name"] == "year")
    assert spec["encoding"] == "numerical"
    assert spec["std"] > 0


def test_encode_for_axis_respects_order(items_csv: str) -> None:
    t = load_feature_tables(_features(items_csv), recipe_name="r", run_id="run")
    fwd = encode_for_axis(t, item_order=["i_a", "i_b", "i_c"], user_order=None)
    rev = encode_for_axis(t, item_order=["i_c", "i_b", "i_a"], user_order=None)
    np.testing.assert_allclose(
        fwd["item_features"].toarray()[::-1], rev["item_features"].toarray()
    )
    assert "user_features" not in fwd


def test_id_column_missing_raises(items_csv: str) -> None:
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": items_csv},
            id_column="nope",
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )
    with pytest.raises(Exception, match="nope"):
        load_feature_tables(cfg, recipe_name="r", run_id="run")


def test_null_and_empty_ids_are_dropped(tmp_path: Path) -> None:
    """A blank id must not become a spurious feature-table row.

    Detecting null BEFORE str-coercion (mirroring metadata/loader.py) also
    means an entity literally named the string "nan" is preserved rather
    than mistaken for a missing id.
    """
    p = tmp_path / "items_with_nulls.csv"
    # The two SURVIVING rows (i_a, i_c) carry DISTINCT genres so the block stays
    # live -- were they identical, the (correct) whole-block-dead guard would
    # refuse a bias-only block, which is a different test's concern
    # (test_constant_categorical_whole_block_raises), not this one's.
    pd.DataFrame(
        {
            "item_id": ["i_a", None, "i_c", ""],
            "genre": ["action", "drama", "sci-fi", "comedy"],
            "year": [2000, 2010, 2020, 2030],
        }
    ).to_csv(p, index=False)
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )
    t = load_feature_tables(cfg, recipe_name="r", run_id="run")
    encoded = encode_for_axis(t, item_order=["i_a", "i_c"], user_order=None)
    # Only the two valid ids should have been encoded; "comedy" (the dropped
    # empty-id row) must not appear in the vocabulary.
    spec = next(s for s in t.item_state["columns"] if s["name"] == "genre")
    assert "comedy" not in spec["vocab"]
    assert encoded["item_features"].shape[0] == 2


def test_duplicate_ids_are_dropped_and_logged(tmp_path: Path) -> None:
    """A repeated id must keep only its first row (``keep="first"``) AND
    emit a ``feature_table_duplicate_ids_dropped`` warning naming the drop
    count -- mirroring the adjacent null-id path's
    ``feature_table_null_ids_dropped``. Before this fix, the null-id path
    logged its drop count but the duplicate-id path (``drop_duplicates``)
    dropped silently: a 28-row table with 14 unique ids logged ``n_rows: 14``
    and nothing else, giving an operator no signal that half the table was
    discarded. The log must carry only the count -- never the ids/values,
    which are user PII.
    """
    import structlog.testing

    p = tmp_path / "items_with_dupes.csv"
    pd.DataFrame(
        {
            "item_id": ["i_a", "i_b", "i_a", "i_c", "i_b", "i_b"],
            "genre": ["action", "drama", "comedy", "action", "horror", "sci-fi"],
        }
    ).to_csv(p, index=False)
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )
    with structlog.testing.capture_logs() as cap:
        t = load_feature_tables(cfg, recipe_name="r", run_id="run")

    # 6 rows, 3 unique ids -> 3 duplicates dropped, first occurrence kept.
    encoded = encode_for_axis(t, item_order=["i_a", "i_b", "i_c"], user_order=None)
    assert encoded["item_features"].shape[0] == 3
    spec = next(s for s in t.item_state["columns"] if s["name"] == "genre")
    # "i_a"'s FIRST row ("action") must have won, not "comedy" (its second,
    # dropped row) -- pins keep="first", not just "some row survived".
    assert "comedy" not in spec["vocab"]
    assert "action" in spec["vocab"]

    dupe_events = [
        e for e in cap if e.get("event") == "feature_table_duplicate_ids_dropped"
    ]
    assert dupe_events, (
        "Expected 'feature_table_duplicate_ids_dropped' warning; "
        f"got events: {[e.get('event') for e in cap]}"
    )
    ev = dupe_events[0]
    assert ev["drop_count"] == 3, f"Expected drop_count=3; got {ev['drop_count']!r}"
    assert ev["side"] == "item"
    # Never log the raw ids/values (user PII) -- only the count and side.
    for e in cap:
        for value in e.values():
            assert "i_a" not in str(value)
            assert "comedy" not in str(value)


def test_dimension_cap_becomes_training_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FeatureEncodeError is not a TrainingError subclass; load_feature_tables
    must wrap it so a dimension-cap breach maps to exit 4 like every other
    training-domain error, not exit 1 (unmapped exception).
    """
    monkeypatch.setenv("RECOTEM_MAX_FEATURE_DIM", "16")
    p = tmp_path / "wide_items.csv"
    pd.DataFrame(
        {
            "item_id": [f"i{i}" for i in range(40)],
            "genre": [f"g{i}" for i in range(40)],
        }
    ).to_csv(p, index=False)
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )
    with pytest.raises(TrainingError, match="exceeds") as exc_info:
        load_feature_tables(cfg, recipe_name="r", run_id="run")
    assert exc_info.value.code == "feature_table_error"


def test_unregistered_source_type_raises_datasource_error(items_csv: str) -> None:
    """An unknown source type is a datasource problem (exit 3), not a
    training-domain one (exit 4) -- same treatment as the main interaction
    source in pipeline.py's _fetch_data.
    """
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "not_a_real_source", "path": items_csv},
            id_column="item_id",
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )
    with pytest.raises(DataSourceError):
        load_feature_tables(cfg, recipe_name="r", run_id="run")


def test_encode_for_axis_missing_item_order_raises(items_csv: str) -> None:
    t = load_feature_tables(_features(items_csv), recipe_name="r", run_id="run")
    with pytest.raises(TrainingError, match="item_order"):
        encode_for_axis(t, item_order=None, user_order=None)


@pytest.fixture
def blank_id_cell_csv(tmp_path: Path) -> str:
    """A feature table whose id column contains ONE blank cell.

    That single blank is enough for pandas to infer ``float64`` for the whole
    column, so the surviving integer ids read back as ``1.0`` / ``2.0`` while
    the interaction axis carries ``"1"`` / ``"2"``.
    """
    p = tmp_path / "items_blank_id.csv"
    p.write_text("item_id,genre\n1,action\n2,drama\n,comedy\n")
    return str(p)


def _int_id_features(path: str, **source_extra: object) -> FeaturesConfig:
    return FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": path, **source_extra},
            id_column="item_id",
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )


def test_zero_overlap_from_blank_cell_dtype_coercion_raises(
    blank_id_cell_csv: str,
) -> None:
    """A blank id cell must not silently train an all-bias (feature-less) model.

    One blank cell makes pandas infer float64 for the id column, so ids become
    "1.0" while the interaction axis has "1". Overlap is empty, every item
    encodes to the bias column only, and training used to COMPLETE and sign an
    artifact whose header advertises `features` for what is really plain iALS.
    The null-id handling in _fetch_side does fire for the blank row, but the
    dtype damage to the SURVIVING rows is already done before it runs.
    """
    t = load_feature_tables(
        _int_id_features(blank_id_cell_csv), recipe_name="r", run_id="run"
    )
    # Precondition: the table itself loaded fine -- the damage is invisible
    # until the ids meet the interaction axis.
    assert list(t.item_df.index) == ["1.0", "2.0"]

    with pytest.raises(TrainingError) as exc_info:
        encode_for_axis(t, item_order=["1", "2"], user_order=None)

    msg = str(exc_info.value)
    # Name the side and the id_column ...
    assert "item" in msg
    assert "item_id" in msg
    # ... and show both samples, so the 1.0-vs-1 mismatch is self-evident.
    assert "1.0" in msg
    assert "'1'" in msg
    # TrainingError -> exit 4 (see CLAUDE.md's exit-code table). Only the
    # "signing_key_missing" code diverts to exit 8, so any other code is 4.
    assert exc_info.value.code != "signing_key_missing"


def test_zero_overlap_from_wrong_id_column_raises(tmp_path: Path) -> None:
    """A wrong-but-EXISTING id_column passes _fetch_side's presence check.

    `sku` exists, so the missing-column guard never fires; the ids simply have
    nothing to do with the interaction axis. Same silent all-bias outcome as
    the dtype case, and the same overlap check catches it.
    """
    p = tmp_path / "items_sku.csv"
    pd.DataFrame(
        {
            "sku": ["SKU-1", "SKU-2"],
            "product_id": ["p1", "p2"],
            "genre": ["action", "drama"],
        }
    ).to_csv(p, index=False)
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="sku",  # should have been product_id
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )
    t = load_feature_tables(cfg, recipe_name="r", run_id="run")
    with pytest.raises(TrainingError) as exc_info:
        encode_for_axis(t, item_order=["p1", "p2"], user_order=None)

    msg = str(exc_info.value)
    assert "sku" in msg
    assert "SKU-1" in msg


def test_zero_overlap_names_the_user_side(tmp_path: Path) -> None:
    """The message must name the offending SIDE -- a user-side mismatch must
    not report itself as an item-side one."""
    p = tmp_path / "users.csv"
    p.write_text("user_id,country\n1,jp\n2,us\n,fr\n")
    cfg = FeaturesConfig(
        user=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="user_id",
            columns=[FeatureColumn(name="country", encoding="categorical")],
        )
    )
    t = load_feature_tables(cfg, recipe_name="r", run_id="run")
    with pytest.raises(TrainingError) as exc_info:
        encode_for_axis(t, item_order=None, user_order=["1", "2"])

    msg = str(exc_info.value)
    assert "user" in msg
    assert "user_id" in msg
    assert "item" not in msg


def test_dtype_override_restores_overlap(blank_id_cell_csv: str) -> None:
    """The remedy the error message recommends must actually work.

    Pinning the id column to `str` on the feature source defeats the float64
    inference, so the ids line up with the interaction axis again and the genre
    one-hot is really encoded (not just the bias column).
    """
    t = load_feature_tables(
        _int_id_features(blank_id_cell_csv, dtype={"item_id": "str"}),
        recipe_name="r",
        run_id="run",
    )
    assert list(t.item_df.index) == ["1", "2"]

    encoded = encode_for_axis(t, item_order=["1", "2"], user_order=None)
    dense = encoded["item_features"].toarray()
    # Each row must carry a real genre one-hot ALONGSIDE the bias column;
    # an all-bias row would sum to exactly 1.0.
    assert dense.sum(axis=1).tolist() == [2.0, 2.0]


def test_axis_coverage_is_logged(items_csv: str) -> None:
    """Coverage is the observability that was missing: a healthy run must say
    how much of the axis the feature table actually covers. Ids are NOT logged
    -- they are user PII (see test_duplicate_ids_are_dropped_and_logged); the
    bounded id sample belongs only to the fatal zero-overlap message.
    """
    import structlog.testing

    t = load_feature_tables(_features(items_csv), recipe_name="r", run_id="run")
    with structlog.testing.capture_logs() as cap:
        encode_for_axis(t, item_order=["i_a", "i_b", "i_zzz"], user_order=None)

    events = [e for e in cap if e.get("event") == "feature_axis_coverage"]
    assert events, f"Expected coverage log; got {[e.get('event') for e in cap]}"
    ev = events[0]
    assert ev["side"] == "item"
    assert ev["matched"] == 2
    assert ev["total"] == 3
    for e in cap:
        for value in e.values():
            assert "i_a" not in str(value)


def test_partial_coverage_is_allowed(items_csv: str) -> None:
    """A partially-covering feature table is legitimate, not an error.

    Cold-start entities are representable by design (see build_encoder_state's
    docstring): an id absent from the table encodes to bias-only and degrades
    to plain iALS for that entity alone. Only ZERO overlap is refused.
    """
    t = load_feature_tables(_features(items_csv), recipe_name="r", run_id="run")
    encoded = encode_for_axis(
        t, item_order=["i_a", "cold_1", "cold_2", "cold_3"], user_order=None
    )
    dense = encoded["item_features"].toarray()
    assert dense.shape[0] == 4
    # Count NONZEROS, not the row sum: `year` is standardized, so a covered
    # row can legitimately sum below 1.0 via a negative z-score.
    nonzero = (dense != 0).sum(axis=1).tolist()
    # i_a keeps genre + year + bias; the three cold ids are bias-only.
    assert nonzero == [3, 1, 1, 1]


def test_empty_axis_does_not_raise(items_csv: str) -> None:
    """An empty axis has nothing to cover, so 0 matched is not a mismatch --
    and must not trip a 0/0 ratio. An itemless interaction table is a
    different problem, already caught by the min_items precondition."""
    t = load_feature_tables(_features(items_csv), recipe_name="r", run_id="run")
    encoded = encode_for_axis(t, item_order=[], user_order=None)
    assert encoded["item_features"].shape[0] == 0


# ---------------------------------------------------------------------------
# Review finding (Gap 2): `_check_axis_coverage` RAISES on 0% id overlap
# because "training completes and the artifact advertises `features` for what
# is really plain iALS". A whole `features:` block that prunes to bias-only
# (`n_features == 1` -- every categorical/multi_label vocab emptied, no
# numerical column) reaches the SAME end state by a third route, and
# `_features.py`'s `build_encoder_state` only WARNS there (it is a neutral
# module and must not raise a training error). The training-side refusal
# therefore lives here, matching the coverage check's posture: a signed
# artifact must not advertise features it does not actually carry.
# ---------------------------------------------------------------------------


def test_whole_block_pruned_to_bias_only_raises(tmp_path: Path) -> None:
    """A `features:` block whose every column is emptied (here by an
    unsatisfiable `min_frequency`) collapses to `n_features == 1` (bias only).
    Training must refuse it, not sign an artifact advertising a no-op block."""
    p = tmp_path / "items.csv"
    pd.DataFrame(
        {"item_id": ["i_a", "i_b", "i_c"], "genre": ["action", "drama", "comedy"]}
    ).to_csv(p, index=False)
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            # min_frequency=50 against a 3-row catalog prunes every token.
            columns=[
                FeatureColumn(name="genre", encoding="categorical", min_frequency=50)
            ],
        )
    )
    with pytest.raises(TrainingError) as exc_info:
        load_feature_tables(cfg, recipe_name="r", run_id="run")
    # Exit 4 (any TrainingError code except signing_key_missing -> exit 4).
    assert exc_info.value.code != "signing_key_missing"
    msg = str(exc_info.value)
    assert "item" in msg
    assert "bias" in msg


def test_all_null_feature_column_raises_as_bias_only(tmp_path: Path) -> None:
    """The all-null route to the same bias-only end state must also refuse --
    a single categorical column with no usable values leaves `n_features == 1`.
    """
    p = tmp_path / "items_null_feat.csv"
    p.write_text("item_id,genre\ni_a,\ni_b,\ni_c,\n")
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )
    with pytest.raises(TrainingError, match="bias"):
        load_feature_tables(cfg, recipe_name="r", run_id="run")


# ---------------------------------------------------------------------------
# Fix A: the whole-block-dead guard keyed on `n_features == 1`, which a dead
# NUMERICAL block escapes: a numerical spec always reserves width 1 (so
# n_features stays 2) even when its std was floored to 0.0 (dead) and it emits
# nothing at encode time. So an all-dead-numerical block signs an artifact
# advertising `features` that serves bias-only == plain iALS -- the exact
# silent downgrade this guard exists to refuse. The guard must key on whether
# ANY spec can emit a non-bias feature, not on n_features.
# ---------------------------------------------------------------------------


def test_all_dead_numerical_block_raises_as_bias_only(tmp_path: Path) -> None:
    """A block whose ONLY column is a constant numerical column is dead: its
    std floors to 0.0 and it emits nothing, so every entity encodes to bias
    alone -- yet n_features stays 2 (width-1 numerical + bias), so the old
    `n_features == 1` guard never fired. It must be refused like the
    all-categorical-dead block above."""
    p = tmp_path / "items_constant_num.csv"
    # ids overlap the interaction axis so the overlap check passes; the only
    # feature column is a constant numerical one (std -> 0.0, dead).
    pd.DataFrame({"item_id": ["i_a", "i_b", "i_c"], "score": [5, 5, 5]}).to_csv(
        p, index=False
    )
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[FeatureColumn(name="score", encoding="numerical")],
        )
    )
    with pytest.raises(TrainingError) as exc_info:
        load_feature_tables(cfg, recipe_name="r", run_id="run")
    assert exc_info.value.code != "signing_key_missing"  # -> exit 4
    msg = str(exc_info.value)
    assert "item" in msg
    assert "bias" in msg


def test_all_null_numerical_column_raises_as_bias_only(tmp_path: Path) -> None:
    """The all-null numerical route to the same bias-only end state must also
    refuse: no usable values leaves std == 0.0 (dead), width still 1, so the
    old `n_features == 1` guard again missed it."""
    p = tmp_path / "items_null_num.csv"
    p.write_text("item_id,score\ni_a,\ni_b,\ni_c,\n")
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[FeatureColumn(name="score", encoding="numerical")],
        )
    )
    with pytest.raises(TrainingError, match="bias"):
        load_feature_tables(cfg, recipe_name="r", run_id="run")


def test_one_dead_column_among_several_does_not_raise(tmp_path: Path) -> None:
    """The whole-block refusal must fire ONLY when the ENTIRE block is dead.
    Pruning one column of several via `min_frequency` is the operator's
    legitimate choice: it warns per-column (see test_features.py) but must not
    abort, because the surviving column keeps `n_features > 1`."""
    p = tmp_path / "items_mixed.csv"
    pd.DataFrame(
        {
            "item_id": ["i_a", "i_b", "i_c"],
            "genre": ["action", "drama", "comedy"],
            "brand": ["x", "y", "z"],
        }
    ).to_csv(p, index=False)
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[
                # genre pruned to nothing, brand survives.
                FeatureColumn(name="genre", encoding="categorical", min_frequency=50),
                FeatureColumn(name="brand", encoding="categorical"),
            ],
        )
    )
    t = load_feature_tables(cfg, recipe_name="r", run_id="run")
    # brand's 3 one-hots + bias == 4; the block is NOT dead.
    assert t.item_state["n_features"] == 4


# ---------------------------------------------------------------------------
# Constant-categorical refusal: the whole-block-dead guard was WIDTH-based, so
# a constant-but-present categorical/multi_label column (non-empty vocab,
# `width > 0`, yet every row emits the SAME one-hot) counted as live and slipped
# past the refusal -- even though it is collinear with the bias and byte-
# identical to plain iALS, exactly what the per-column warning already flags and
# what the numerical branch's zero-variance refusal already refuses. The guard
# now keys on whether the encoded block VARIES across rows, so it agrees with
# both. A varying column, and a mixed block with one live column, must still
# load: the refusal fires only when EVERY spec is dead.
# ---------------------------------------------------------------------------


def test_constant_categorical_whole_block_raises(tmp_path: Path) -> None:
    """A block whose ONLY column is a CONSTANT categorical (every item the same
    value; full id overlap so this is not the coverage check) keeps a non-empty
    vocab and `width == 1`, so the old width-based guard passed it -- yet every
    row emits the same one-hot, collinear with the bias, so the model is plain
    iALS. It must be refused like the all-dead-numerical block."""
    p = tmp_path / "items_constant_cat.csv"
    pd.DataFrame(
        {"item_id": ["i_a", "i_b", "i_c"], "genre": ["book", "book", "book"]}
    ).to_csv(p, index=False)
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )
    with pytest.raises(TrainingError) as exc_info:
        load_feature_tables(cfg, recipe_name="r", run_id="run")
    assert exc_info.value.code == "feature_table_error"  # -> exit 4
    msg = str(exc_info.value)
    assert "item" in msg
    assert "bias" in msg


def test_constant_multi_label_whole_block_raises(tmp_path: Path) -> None:
    """The multi_label analogue: every row carries the same single token, so its
    multi-hot block is identical across rows (constant), collinear with the
    bias. Non-empty vocab and `width == 1` again fooled the width-based guard;
    the varies-based guard refuses it."""
    p = tmp_path / "items_constant_ml.csv"
    pd.DataFrame(
        {"item_id": ["i_a", "i_b", "i_c"], "tags": ["rock", "rock", "rock"]}
    ).to_csv(p, index=False)
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[FeatureColumn(name="tags", encoding="multi_label")],
        )
    )
    with pytest.raises(TrainingError) as exc_info:
        load_feature_tables(cfg, recipe_name="r", run_id="run")
    assert exc_info.value.code == "feature_table_error"  # -> exit 4
    msg = str(exc_info.value)
    assert "item" in msg
    assert "bias" in msg


def test_varying_categorical_whole_block_loads(tmp_path: Path) -> None:
    """Vacuity control: a VARYING categorical column carries real signal and
    must load, so the refusal cannot be trivially satisfied (it must not fire
    on every categorical-only block)."""
    p = tmp_path / "items_varying_cat.csv"
    pd.DataFrame(
        {"item_id": ["i_a", "i_b", "i_c"], "genre": ["action", "drama", "comedy"]}
    ).to_csv(p, index=False)
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )
    t = load_feature_tables(cfg, recipe_name="r", run_id="run")
    # 3 one-hots + bias == 4; the block is live.
    assert t.item_state["n_features"] == 4


def test_mixed_constant_and_varying_categorical_does_not_raise(
    tmp_path: Path,
) -> None:
    """A constant categorical column ALONGSIDE a varying one: the varying column
    is a live spec, so the whole block is not dead and must load -- the refusal
    fires only when EVERY spec is dead. This is the constant-column route to
    "one dead column among several", distinct from the min_frequency-pruning
    route in test_one_dead_column_among_several_does_not_raise (the dead column
    here keeps a non-empty vocab)."""
    p = tmp_path / "items_mixed_constant.csv"
    pd.DataFrame(
        {
            "item_id": ["i_a", "i_b", "i_c"],
            "genre": ["book", "book", "book"],  # constant -> dead
            "brand": ["x", "y", "z"],  # varying -> live
        }
    ).to_csv(p, index=False)
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[
                FeatureColumn(name="genre", encoding="categorical"),
                FeatureColumn(name="brand", encoding="categorical"),
            ],
        )
    )
    t = load_feature_tables(cfg, recipe_name="r", run_id="run")
    # constant genre one-hot (1) + varying brand's 3 one-hots + bias == 5.
    assert t.item_state["n_features"] == 5


def test_constant_feature_column_warns_at_training_time(tmp_path: Path) -> None:
    """Gap 3 at the TRAINING level: a constant categorical column (every item
    the same genre) is dead (collinear with bias) and must warn when the
    feature table is loaded, not only when `build_encoder_state` is called by
    hand. Here the constant `genre` sits ALONGSIDE a varying `brand`, so the
    block as a whole stays live (the varying column keeps it off the
    whole-block-dead refusal) and the load succeeds -- letting us observe the
    per-column warning in isolation from the whole-block guard. A block whose
    ONLY column is constant is refused instead; see
    test_constant_categorical_whole_block_raises."""
    import structlog.testing

    p = tmp_path / "items_constant.csv"
    pd.DataFrame(
        {
            "item_id": ["i_a", "i_b", "i_c"],
            "genre": ["action", "action", "action"],
            "brand": ["x", "y", "z"],
        }
    ).to_csv(p, index=False)
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[
                FeatureColumn(name="genre", encoding="categorical"),
                FeatureColumn(name="brand", encoding="categorical"),
            ],
        )
    )
    with structlog.testing.capture_logs() as cap:
        t = load_feature_tables(cfg, recipe_name="r", run_id="run")
    # constant genre one-hot (1) + varying brand's 3 one-hots + bias == 5.
    assert t.item_state["n_features"] == 5  # not a raise: brand keeps it live
    events = [e for e in cap if e.get("event") == "feature_empty_vocabulary_column"]
    assert events, (
        f"a constant column must warn as dead at load time; "
        f"got {[e.get('event') for e in cap]}"
    )
    # The warning must name the constant column, not the varying one.
    assert any(e.get("column") == "genre" for e in events)
    # PII: the genre value must not be logged.
    for e in cap:
        for value in e.values():
            assert "action" not in str(value)


# ---------------------------------------------------------------------------
# Review finding (Gap 4): the zero-overlap message samples real ids -- which
# are PII. `_id_sample` bounded the COUNT (3) but not the per-id BYTES, so two
# multi-MB ids produced a multi-MB exception message that then travels into the
# `train_error` event (`error=str(exc)` in pipeline.py). Bound each sampled
# id's length too.
# ---------------------------------------------------------------------------


def test_zero_overlap_message_bounds_each_sampled_id_length(tmp_path: Path) -> None:
    """A pathologically long id must be truncated in the sample, so the fatal
    message (and the `train_error` event carrying it) stays bounded."""
    huge = "z" * 1_000_000
    p = tmp_path / "items_huge_id.csv"
    pd.DataFrame({"item_id": [huge, "other"], "genre": ["a", "b"]}).to_csv(
        p, index=False
    )
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="item_id",
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )
    t = load_feature_tables(cfg, recipe_name="r", run_id="run")
    with pytest.raises(TrainingError) as exc_info:
        encode_for_axis(t, item_order=["nope1", "nope2"], user_order=None)

    msg = str(exc_info.value)
    # The full 1 MB id must NOT appear verbatim, and the message must stay
    # small even though a sampled id was a megabyte.
    assert huge not in msg
    assert len(msg) < 2000, f"message length {len(msg)} not bounded"


# ---------------------------------------------------------------------------
# Review finding (Gap 5): the zero-overlap message hardcoded
# `dtype: {item_id: str}` as the remedy -- but `dtype` exists only on
# `CSVConfig`. A bigquery/sql/parquet operator hitting this abort was told to
# set a key their source does not have. The message must not name a
# source-specific key; it points at the docs' per-source remedy matrix instead.
# ---------------------------------------------------------------------------


def test_zero_overlap_message_does_not_hardcode_csv_only_dtype_key(
    tmp_path: Path,
) -> None:
    """The remedy must not name `dtype: {...}`, which only a `csv` source has;
    a non-csv operator would be misdirected. It must instead point at the docs
    that carry the per-source-type matrix."""
    p = tmp_path / "items_sku.csv"
    pd.DataFrame({"sku": ["SKU-1", "SKU-2"], "genre": ["a", "b"]}).to_csv(
        p, index=False
    )
    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "csv", "path": str(p)},
            id_column="sku",
            columns=[FeatureColumn(name="genre", encoding="categorical")],
        )
    )
    t = load_feature_tables(cfg, recipe_name="r", run_id="run")
    with pytest.raises(TrainingError) as exc_info:
        encode_for_axis(t, item_order=["p1", "p2"], user_order=None)
    msg = str(exc_info.value)
    assert "dtype:" not in msg, (
        "must not hardcode the csv-only `dtype:` remedy; a bigquery/sql/parquet "
        "operator would be told to set a key their source does not have"
    )
    # Still diagnosable: names the side, the id_column, and points at the docs.
    assert "sku" in msg
    assert "operations.md" in msg


# ---------------------------------------------------------------------------
# Zero-row feature table: a header-only table (columns declared, no data rows)
# flowing into the training feature path. The built-in csv/parquet sources
# reject a header-only file at FETCH time with DataSourceError (exit 3), so
# they never hand a 0-row frame to the encoder. To pin what the training
# feature path ITSELF does with an empty frame -- the state a 0-row sql query
# or a custom plugin can still reach -- a stub source that returns an empty
# (but correctly-columned) frame is injected via the registry lookup
# ``load_feature_tables`` uses. The encoder then finds every declared column
# dead (empty categorical vocab, zero-variance numerical), so the whole-block-
# dead guard refuses it exactly as the constant/all-null blocks above do.
# ---------------------------------------------------------------------------


def test_zero_row_feature_table_refused_as_bias_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 0-row (header-only) feature table reaching the encoder collapses to the
    bias column alone and is refused with ``feature_table_error`` (exit 4) --
    NOT signed as a features-advertising plain-iALS artifact."""
    import recotem.training.features as tf

    class _EmptyFrameSource:
        class Config:
            @classmethod
            def model_validate(cls, raw: object) -> _EmptyFrameSource.Config:
                return cls()

        def __init__(self, config: object) -> None:
            pass

        def fetch(self, ctx: object) -> pd.DataFrame:
            # Columns declared (header present), zero data rows.
            return pd.DataFrame({"item_id": [], "genre": [], "year": []})

    monkeypatch.setattr(tf, "get_source_class", lambda _name: _EmptyFrameSource)

    cfg = FeaturesConfig(
        item=FeatureSideConfig(
            source={"type": "stub_empty"},
            id_column="item_id",
            columns=[
                FeatureColumn(name="genre", encoding="categorical"),
                FeatureColumn(name="year", encoding="numerical"),
            ],
        )
    )
    with pytest.raises(TrainingError) as exc_info:
        load_feature_tables(cfg, recipe_name="r", run_id="run")
    # Whole-block-dead guard fires in _fetch_side, before any axis is known --
    # so this is feature_table_error, not the encode-time feature_axis_error.
    assert exc_info.value.code == "feature_table_error"  # -> exit 4
    assert exc_info.value.code != "signing_key_missing"
    msg = str(exc_info.value)
    assert "item" in msg
    assert "bias" in msg
