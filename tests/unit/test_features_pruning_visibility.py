"""Guards on reporting what ``min_frequency`` pruning actually cost.

``_warn_if_column_dead`` only fires on TOTAL collapse -- the encoded block is
identical for every row. A single surviving token defeats it: rows carrying the
survivor differ from rows carrying nothing, so the block "varies" and nothing is
reported, even though every pruned row has become byte-identical to every other
pruned row on that axis.

That gap sits directly under the remedy recotem itself recommends: the
``RECOTEM_MAX_FEATURE_DIM`` error tells the operator to raise ``min_frequency``
on high-cardinality columns. Doing exactly that can silently delete most of a
column, and before this guard nothing said so.

The artifact header deliberately does NOT carry these counts: ``_FEATURE_SIDE_KEYS``
pins the descriptor's key set exactly and the reconciliation gate fails closed on
any other, so adding one would be an artifact-format break requiring every
feature-aware operator to retrain. See ``state_descriptor``.
"""

from __future__ import annotations

import pandas as pd
import structlog

from recotem._features import build_encoder_state, encode
from recotem.recipe.models import FeatureColumn


def _long_tail_frame(n_rows: int = 50, n_common: int = 10) -> pd.DataFrame:
    """A column with one frequent value and a unique value for every other row."""
    return pd.DataFrame(
        [
            {
                "item_id": f"i{i}",
                "category": "popular" if i < n_common else f"rare_{i}",
                "size": ["S", "M", "L"][i % 3],
            }
            for i in range(n_rows)
        ]
    ).set_index("item_id")


def _cols(min_frequency: int) -> list[FeatureColumn]:
    return [
        FeatureColumn(
            name="category", encoding="categorical", min_frequency=min_frequency
        ),
        FeatureColumn(name="size", encoding="categorical"),
    ]


def _events(cap: list[dict], name: str) -> list[dict]:
    return [e for e in cap if e.get("event") == name]


def test_partial_vocabulary_collapse_is_reported() -> None:
    """min_frequency keeping 1 of 41 values must not pass in silence.

    This is the regression the module docstring describes: the surviving token
    makes the block vary, so the dead-column guard stays quiet while 40 of 50
    rows lose all signal on this axis.
    """
    df = _long_tail_frame()
    with structlog.testing.capture_logs() as cap:
        build_encoder_state(df, _cols(5))

    # The pre-existing dead-column guard is blind here -- that is the point.
    assert not _events(cap, "feature_empty_vocabulary_column")

    pruned = _events(cap, "feature_vocabulary_pruned")
    assert len(pruned) == 1, f"expected one pruning warning, got {cap}"
    evt = pruned[0]
    assert evt["column"] == "category"
    assert evt["log_level"] == "warning"
    assert evt["distinct_values"] == 41
    assert evt["kept_values"] == 1
    assert evt["rows_without_signal"] == 40
    assert evt["n_rows"] == 50
    assert evt["min_frequency"] == 5
    # The operator must be told what to do, not merely that something happened.
    assert "min_frequency" in evt["detail"]


def test_pruned_rows_really_are_indistinguishable() -> None:
    """The warning describes a real loss, not a hypothetical one."""
    df = _long_tail_frame()
    state = build_encoder_state(df, _cols(5))
    order = list(df.index)
    matrix = encode(state, df, order).toarray()

    pruned_rows = matrix[10:]
    distinct = {tuple(row) for row in pruned_rows.tolist()}
    # 40 items, 40 originally distinct categories, now separated only by the
    # unrelated 3-valued `size` column.
    assert len(distinct) == 3


def test_no_warning_when_nothing_is_pruned() -> None:
    df = _long_tail_frame()
    with structlog.testing.capture_logs() as cap:
        build_encoder_state(df, _cols(1))
    assert not _events(cap, "feature_vocabulary_pruned")


def test_no_warning_for_a_genuine_long_tail() -> None:
    """Dropping junk that covers 4% of rows is what min_frequency is FOR.

    Keyed on the share of rows left without signal, not on the share of
    vocabulary dropped, so this stays quiet even though 4 of 5 distinct values
    are pruned. A warning here would be noise operators learn to ignore.
    """
    df = pd.DataFrame(
        [
            {"item_id": f"i{i}", "category": "common" if i < 96 else f"junk_{i}"}
            for i in range(100)
        ]
    ).set_index("item_id")
    with structlog.testing.capture_logs() as cap:
        build_encoder_state(
            df,
            [FeatureColumn(name="category", encoding="categorical", min_frequency=2)],
        )
    assert not _events(cap, "feature_vocabulary_pruned")


def test_multi_label_partial_collapse_is_reported() -> None:
    """The multi_label branch carries the same guard as categorical."""
    df = pd.DataFrame(
        [
            {"item_id": f"i{i}", "tags": "hot|fresh" if i < 8 else f"t{i}"}
            for i in range(40)
        ]
    ).set_index("item_id")
    with structlog.testing.capture_logs() as cap:
        build_encoder_state(
            df,
            [
                FeatureColumn(
                    name="tags",
                    encoding="multi_label",
                    delimiter="|",
                    min_frequency=5,
                )
            ],
        )
    pruned = _events(cap, "feature_vocabulary_pruned")
    assert len(pruned) == 1
    assert pruned[0]["column"] == "tags"
    assert pruned[0]["rows_without_signal"] == 32
