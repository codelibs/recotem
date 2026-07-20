"""Unit tests for recotem._features, the neutral side-feature encoder.

Covers:
- build_encoder_state: versioning, plain-data state, dimension cap, missing
  source columns, min_frequency vocabulary pruning.
- encode: required index_order reindexing, bias column, missing rows,
  standardization, unknown-value handling for each encoding kind.
- encode_one: single-row encoding and unknown-column reporting.
- Parity between encode (DataFrame path) and encode_one (dict path) for
  equivalent inputs, including divergent missing-value and numeric-string
  representations across the two call paths.
- state_descriptor.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from fractions import Fraction
from typing import Any

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from recotem._features import (
    FEATURE_STATE_VERSION,
    FeatureEncodeError,
    _parse_number,
    build_encoder_state,
    encode,
    encode_one,
    state_descriptor,
)
from recotem.recipe.models import FeatureColumn


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "item_id": ["i1", "i2", "i3"],
            "genre": ["action", "drama", "action"],
            "year": [2000.0, 2010.0, 2020.0],
            "tags": ["a|b", "b", None],
        }
    ).set_index("item_id")


@pytest.fixture
def columns() -> list[FeatureColumn]:
    return [
        FeatureColumn(name="genre", encoding="categorical"),
        FeatureColumn(name="year", encoding="numerical"),
        FeatureColumn(name="tags", encoding="multi_label", delimiter="|"),
    ]


def test_state_is_versioned_and_json_shaped(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    state = build_encoder_state(df, columns)
    assert state["version"] == FEATURE_STATE_VERSION
    # 2 genres + 1 year + 2 tags + 1 bias
    assert state["n_features"] == 6


def test_state_contains_no_pandas_objects(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """The state must contain only plain Python containers and numpy arrays.

    The key assertion is ``type(k) is str``, NOT ``isinstance(k, str)``.
    ``numpy.str_`` subclasses ``str``, so an ``isinstance`` check (this
    test's original form) passes for a ``numpy.str_`` key and cannot detect
    the exact leak this test exists to prevent -- see
    ``test_vocabulary_keys_are_exactly_str_not_numpy_str`` for why nothing
    downstream catches it either.
    """
    state = build_encoder_state(df, columns)

    def walk(o: object) -> None:
        assert not isinstance(o, pd.Index | pd.Series | pd.DataFrame), o
        if isinstance(o, dict):
            for k, v in o.items():
                assert type(k) is str, f"{k!r} is {type(k)}, not exactly str"
                walk(v)
        elif isinstance(o, list | tuple):
            for v in o:
                walk(v)

    walk(state)


def test_vocabulary_keys_are_exactly_str_not_numpy_str() -> None:
    """``build_encoder_state``'s ``str()`` coercions are load-bearing on their
    own -- the artifact FQCN allow-list does NOT back them up.

    The module docstring used to imply that a stray non-plain object in the
    state would be refused at load time. That is true for ``pd.Index``
    (``pandas.core.indexes.base._new_Index`` is genuinely not allow-listed)
    but FALSE for ``numpy.str_``, which pickles via
    ``numpy._core.multiarray.scalar`` + ``numpy.dtype`` -- both explicitly
    allow-listed -- and so round-trips through ``SafeUnpickler`` keeping its
    type. Nothing downstream catches it either: ``numpy.str_`` hashes and
    compares equal to ``str``, so vocabulary lookups keep working and the
    leak is invisible at runtime.

    This test is therefore the only enforcement of the "state is plain data"
    invariant for the numpy scalar types, which is exactly why it asserts the
    EXACT type.
    """
    d = pd.DataFrame(
        {
            "item_id": ["i1", "i2"],
            "genre": pd.Series([np.str_("action"), np.str_("drama")], dtype=object),
            "tags": pd.Series([np.str_("a|b"), np.str_("b")], dtype=object),
        }
    ).set_index("item_id")
    # Premise: the cells really are numpy.str_, not plain str. An object-dtype
    # column preserves them (a native numpy str-dtype column would normalize
    # to plain str on .tolist(), and would not exercise the coercion at all).
    assert type(d["genre"].iloc[0]) is np.str_
    assert type(d["tags"].iloc[0]) is np.str_

    state = build_encoder_state(
        d,
        [
            FeatureColumn(name="genre", encoding="categorical"),
            FeatureColumn(name="tags", encoding="multi_label", delimiter="|"),
        ],
    )
    for spec in state["columns"]:
        for key in spec["vocab"]:
            assert type(key) is str, (
                f"vocab key {key!r} of column {spec['name']!r} is "
                f"{type(key)}, not exactly str; the str() coercion in "
                f"build_encoder_state is the ONLY thing keeping the state "
                f"plain -- the artifact allow-list loads numpy.str_ happily"
            )


def test_encode_reindexes_to_requested_order(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    state = build_encoder_state(df, columns)
    forward = encode(state, df, index_order=["i1", "i2", "i3"])
    reverse = encode(state, df, index_order=["i3", "i2", "i1"])
    assert forward.shape == (3, 6)
    np.testing.assert_allclose(forward.toarray()[::-1], reverse.toarray())


def test_encode_appends_bias_column(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    state = build_encoder_state(df, columns)
    m = encode(state, df, index_order=["i1", "i2", "i3"]).toarray()
    np.testing.assert_allclose(m[:, -1], np.ones(3))


def test_missing_row_is_all_zero_except_bias(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    state = build_encoder_state(df, columns)
    m = encode(state, df, index_order=["i1", "absent"]).toarray()
    np.testing.assert_allclose(m[1, :-1], np.zeros(5))
    assert m[1, -1] == 1.0


def test_encode_handles_duplicate_id_in_index_order(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """A repeated id in ``index_order`` must produce the same row each time
    it appears, not raise.

    Regression guard for the row-count optimization in ``encode``: an
    earlier draft narrowed the lookup via ``frame.reindex(index_order)``,
    which raises ``ValueError`` from the subsequent
    ``to_dict(orient="index")`` whenever *index_order* itself contains a
    duplicate (reindexing onto a repeated label yields a non-unique result
    index). No production caller currently repeats an id in ``index_order``,
    but ``encode`` is a general-purpose neutral function and must not crash
    if one ever does.
    """
    state = build_encoder_state(df, columns)
    m = encode(state, df, index_order=["i1", "i1", "i2"]).toarray()
    np.testing.assert_allclose(m[0], m[1])


def test_encode_does_not_upcast_int_dtype_column_when_reindexing() -> None:
    """A non-nullable-int-dtype ``categorical`` column must encode a
    present row identically whether or not *index_order* also asks for an
    id absent from the table.

    Regression guard: narrowing the lookup via ``frame.reindex(index_order)``
    fills every id absent from the table with NaN, which forces pandas to
    upcast an int64 column to float64 for EVERY row (present ones
    included) -- turning ``1`` into ``1.0``. The categorical branch keys the
    vocabulary by ``str(raw)``, so ``"1"`` (built at train time from the
    original int64 column) no longer matches ``"1.0"`` (read back after the
    upcast), silently degrading a known category to unknown.
    """
    d = pd.DataFrame({"item_id": ["i1", "i2"], "genre": [1, 2]}).set_index("item_id")
    assert d["genre"].dtype == np.int64
    state = build_encoder_state(
        d, [FeatureColumn(name="genre", encoding="categorical")]
    )

    # No absent id requested: nothing to regress against even on the buggy
    # reindex-based implementation, but pins the base case.
    m_clean = encode(state, d, index_order=["i1", "i2"]).toarray()

    # An absent id ("ghost") IS requested alongside a present one: this is
    # the case that silently broke under `frame.reindex(index_order)`.
    m_with_absent = encode(state, d, index_order=["i1", "ghost", "i2"]).toarray()

    genre_spec = state["columns"][0]
    lo, hi = genre_spec["offset"], genre_spec["offset"] + genre_spec["width"]
    # "i1" (genre=1) must one-hot to vocab index 0 in both cases.
    np.testing.assert_allclose(m_clean[0, lo:hi], m_with_absent[0, lo:hi])
    assert m_with_absent[0, lo:hi].sum() == 1.0, (
        "known int-valued category must not degrade to unknown (all-zero) "
        "just because index_order also contains an absent id"
    )


def test_multi_label_partial_unknown_keeps_known_tokens(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    state = build_encoder_state(df, columns)
    probe = pd.DataFrame(
        {"item_id": ["x"], "genre": ["action"], "year": [2000.0], "tags": ["a|zzz"]}
    ).set_index("item_id")
    m = encode(state, probe, index_order=["x"]).toarray()
    tag_slice = state["columns"][2]
    lo, hi = tag_slice["offset"], tag_slice["offset"] + tag_slice["width"]
    # 'a' known -> 1; 'zzz' unknown -> dropped, not an all-zero segment.
    assert m[0, lo:hi].sum() == 1.0


def test_categorical_unknown_is_all_zero(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    state = build_encoder_state(df, columns)
    probe = pd.DataFrame(
        {"item_id": ["x"], "genre": ["comedy"], "year": [2000.0], "tags": ["a"]}
    ).set_index("item_id")
    m = encode(state, probe, index_order=["x"]).toarray()
    g = state["columns"][0]
    assert m[0, g["offset"] : g["offset"] + g["width"]].sum() == 0.0


def test_numerical_is_standardized(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    state = build_encoder_state(df, columns)
    m = encode(state, df, index_order=["i1", "i2", "i3"]).toarray()
    y = state["columns"][1]
    col = m[:, y["offset"]]
    assert abs(col.mean()) < 1e-6
    assert abs(col.std() - 1.0) < 1e-6


def test_zero_variance_numerical_emits_zeros() -> None:
    d = pd.DataFrame({"item_id": ["i1", "i2"], "year": [5.0, 5.0]}).set_index("item_id")
    state = build_encoder_state(d, [FeatureColumn(name="year", encoding="numerical")])
    m = encode(state, d, index_order=["i1", "i2"]).toarray()
    np.testing.assert_allclose(m[:, 0], np.zeros(2))


# ---------------------------------------------------------------------------
# Review finding: a column need not be EXACTLY constant (std == 0.0) to
# behave like one. Floating-point rounding noise (values that are "the same
# number" up to a few ULPs) survives an exact std == 0.0 check but still
# divides serve-time standardization by a near-zero denominator, turning an
# ordinary raw request value into an astronomically large standardized one
# -- the mechanism behind a false FEATURE_VALUE_UNUSABLE 400 for a value that
# is not, in any meaningful sense, extreme.
# ---------------------------------------------------------------------------


def test_near_constant_numerical_std_is_floored_to_zero() -> None:
    """A column whose values differ only by ULP-scale floating-point noise
    (std ~1e-15, not exactly 0.0) must be treated as zero-variance, exactly
    like ``test_zero_variance_numerical_emits_zeros`` above -- otherwise
    standardizing an ordinary request value against a near-zero std produces
    an astronomically large standardized value.
    """
    base = 5.0
    values = [
        base,
        np.nextafter(base, np.inf),
        np.nextafter(base, -np.inf),
        np.nextafter(np.nextafter(base, np.inf), np.inf),
    ]
    d = pd.DataFrame({"item_id": ["i1", "i2", "i3", "i4"], "year": values}).set_index(
        "item_id"
    )

    # Test-setup invariant: the raw std must be genuinely nonzero (not
    # exactly 0.0, which the pre-existing `std == 0.0` check already
    # handled) but tiny -- ULP-scale -- or this test would not exercise the
    # floor at all.
    raw_std = float(pd.Series(values).std(ddof=0))
    assert 0.0 < raw_std < 1e-10, (
        f"test setup invariant violated: need a genuinely nonzero, "
        f"ULP-scale std; got {raw_std!r}"
    )

    state = build_encoder_state(d, [FeatureColumn(name="year", encoding="numerical")])
    assert state["columns"][0]["std"] == 0.0, (
        f"a std of {raw_std!r} (relative to a column scale of {base!r}) is "
        f"well below the relative floor and must be treated as zero-variance"
    )

    # An ordinary-looking request value must standardize to 0 (degrade like
    # a missing value), not explode.
    m, unknown = encode_one(state, {"year": 10000.0})
    assert m.toarray()[0, 0] == 0.0
    assert unknown == []


def test_small_but_real_variance_numerical_std_is_not_floored() -> None:
    """A column with genuine (not rounding-noise) small variance, comfortably
    above the relative floor, must still standardize normally -- the floor
    must not swallow real variance just because it is small.
    """
    d = pd.DataFrame(
        {"item_id": ["i1", "i2", "i3"], "year": [4.999, 5.0, 5.001]}
    ).set_index("item_id")
    raw_std = float(pd.Series([4.999, 5.0, 5.001]).std(ddof=0))
    assert raw_std > 1e-4, (
        f"test setup invariant violated: need a std well above the "
        f"relative floor (1e-8 * scale); got {raw_std!r}"
    )
    state = build_encoder_state(d, [FeatureColumn(name="year", encoding="numerical")])
    assert state["columns"][0]["std"] == pytest.approx(raw_std)


# ---------------------------------------------------------------------------
# Review finding M2: a single non-finite/overflow cell poisoned the WHOLE
# column's statistics. `pd.to_numeric(..., errors="coerce")` maps a token like
# "1e400" to +inf (NOT NaN, because `float("1e400") == inf`), and pandas
# `mean()`/`std()` do NOT skip +-inf -- so one such cell made mean=inf (reset
# to 0.0) and std=nan (routed into the zero-variance branch), silently marking
# a column with usable finite values like [1,2,3] DEAD (std=0.0, emits zeros)
# and MISATTRIBUTING the cause as "standardization would divide by zero". The
# fit must instead compute mean/std over the FINITE values only -- consistent
# with `encode()`, which already routes a per-row non-finite standardized
# value to `unknown` (see the `not isfinite`/`abs > FLOAT32_MAX` guard in
# `_row_values`). Only a column with NO finite values, or genuine zero variance
# AMONG the finite values, is dead, and the two causes must warn distinctly.
# ---------------------------------------------------------------------------


def test_numerical_column_with_one_overflow_cell_stays_live() -> None:
    """A numerical column with usable finite values plus one overflow cell
    ("1e400" -> +inf) must stay LIVE, with mean/std computed over the finite
    values only.

    Pre-fix, `pd.to_numeric` mapped "1e400" to +inf, which pandas did NOT skip
    in `mean()`/`std()`: mean came back +inf (reset to 0.0) and std came back
    nan (routed into the zero-variance branch), so `std` was floored to 0.0 and
    the whole column emitted zeros -- as though [1,2,3] carried no signal --
    while the warning blamed "divide by zero". This test pins the fixed
    behavior: the finite rows [1,2,3] set mean=2.0/std=sqrt(2/3), the column is
    live, and no zero-variance warning fires.
    """
    import structlog.testing

    d = pd.DataFrame(
        {"item_id": ["a", "b", "c", "d"], "price": ["1.0", "2.0", "3.0", "1e400"]}
    ).set_index("item_id")
    with structlog.testing.capture_logs() as cap:
        state = build_encoder_state(
            d, [FeatureColumn(name="price", encoding="numerical")]
        )
    spec = state["columns"][0]

    # Statistics are computed over the finite values [1,2,3] alone.
    assert spec["mean"] == pytest.approx(2.0)
    assert spec["std"] == pytest.approx(math.sqrt(2.0 / 3.0))
    assert spec["std"] != 0.0, (
        "one overflow cell must not mark a column with finite values dead; "
        "pre-fix std was floored to 0.0"
    )

    # The column is live -> NO zero-variance warning.
    assert not [e for e in cap if e.get("event") == "feature_zero_variance_column"], (
        "a live column must not warn as zero-variance"
    )

    # End-to-end consistency with encode(): the "1e400" row degrades like a
    # missing value (contributes 0) while a finite row contributes its
    # standardized value.
    m = encode(state, d, index_order=["a", "d"]).toarray()
    assert m[0, spec["offset"]] == pytest.approx((1.0 - 2.0) / math.sqrt(2.0 / 3.0))
    assert m[1, spec["offset"]] == 0.0, "the overflow row must encode to zero"

    # And encode_one routes the same overflow token to `unknown` -- an unknown
    # value must not also be invisible.
    _, unknown = encode_one(state, {"price": "1e400"})
    assert unknown == ["price"]


def test_numerical_column_all_nonfinite_is_dead_with_accurate_message() -> None:
    """A column with NO finite parseable values is dead, and warns with a
    message that names the real cause -- not "divide by zero".

    Every cell is either an overflow token ("1e400" -> +inf) or unparseable
    ("nope" -> NaN), so there is nothing finite to fit. Pre-fix this still
    reported the zero-variance "standardization would divide by zero" detail,
    misattributing the cause; the fix emits a distinct, accurate message.
    """
    import structlog.testing

    d = pd.DataFrame({"item_id": ["a", "b"], "price": ["1e400", "nope"]}).set_index(
        "item_id"
    )
    with structlog.testing.capture_logs() as cap:
        state = build_encoder_state(
            d, [FeatureColumn(name="price", encoding="numerical")]
        )
    spec = state["columns"][0]

    assert spec["std"] == 0.0, "no finite values -> dead column (std == 0.0)"

    events = [e for e in cap if e.get("event") == "feature_zero_variance_column"]
    assert events, "a no-finite-values column must still warn"
    detail = events[0]["detail"]
    assert "divide by zero" not in detail, (
        f"an all-non-finite column must not blame zero variance; got {detail!r}"
    )
    assert "finite" in detail, (
        f"the warning must name the real cause (no finite values); got {detail!r}"
    )


def test_constant_finite_numerical_column_uses_zero_variance_message() -> None:
    """A genuinely constant finite column [5,5,5] still hits the zero-variance
    path and keeps the ORIGINAL "divide by zero" wording -- the fix must not
    relabel real zero-variance-among-finite-values as the no-finite case.
    """
    import structlog.testing

    d = pd.DataFrame({"item_id": ["a", "b", "c"], "price": [5.0, 5.0, 5.0]}).set_index(
        "item_id"
    )
    with structlog.testing.capture_logs() as cap:
        state = build_encoder_state(
            d, [FeatureColumn(name="price", encoding="numerical")]
        )
    assert state["columns"][0]["std"] == 0.0

    events = [e for e in cap if e.get("event") == "feature_zero_variance_column"]
    assert events, "a constant finite column must warn as zero-variance"
    assert "divide by zero" in events[0]["detail"], (
        "genuine zero variance among finite values must keep the original "
        "divide-by-zero wording"
    )


def test_numerical_missing_becomes_mean(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    state = build_encoder_state(df, columns)
    probe = pd.DataFrame(
        {"item_id": ["x"], "genre": ["action"], "year": [np.nan], "tags": ["a"]}
    ).set_index("item_id")
    m = encode(state, probe, index_order=["x"]).toarray()
    y = state["columns"][1]
    assert m[0, y["offset"]] == 0.0  # standardized mean == 0


# ---------------------------------------------------------------------------
# Review finding: a directly-supplied non-finite numerical value (+-inf, or a
# NaN reached via a string) was a silent no-op -- byte-identical to omitting
# the column entirely, with no `unknown` entry and no counter fired. This is
# exactly the failure mode encode_one's own docstring says must not happen:
# "an unknown category degrades the recommendation silently, so it must not
# also be invisible." A *missing* (None/NaN/pd.NA) or *unparseable*
# (non-numeric string) value is a separate, deliberately uncounted gap and
# must stay that way -- these tests pin both sides of that distinction.
# ---------------------------------------------------------------------------


def test_encode_one_reports_unknown_for_infinite_numerical_value(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """+inf and -inf must both fire the unknown-value signal, and must not
    contribute to the standardized column (same zero contribution as
    before -- only the signal is new)."""
    state = build_encoder_state(df, columns)
    y = state["columns"][1]

    m_pos, unknown_pos = encode_one(
        state, {"genre": "action", "year": float("inf"), "tags": "a"}
    )
    assert unknown_pos == ["year"]
    assert m_pos.toarray()[0, y["offset"]] == 0.0

    m_neg, unknown_neg = encode_one(
        state, {"genre": "action", "year": float("-inf"), "tags": "a"}
    )
    assert unknown_neg == ["year"]
    assert m_neg.toarray()[0, y["offset"]] == 0.0


def test_encode_one_infinite_numerical_value_is_identical_to_omitted_column(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """Pre-fix, a supplied +inf was byte-identical to omitting the column
    entirely -- this pins that the encoded MATRIX stays identical (dropping
    an unusable value must still degrade the same way), while the `unknown`
    signal now differs (the whole point of the fix)."""
    state = build_encoder_state(df, columns)

    m_inf, unknown_inf = encode_one(
        state, {"genre": "action", "year": float("inf"), "tags": "a"}
    )
    m_omitted, unknown_omitted = encode_one(state, {"genre": "action", "tags": "a"})
    np.testing.assert_allclose(m_inf.toarray(), m_omitted.toarray())
    assert unknown_inf == ["year"]
    assert unknown_omitted == [], (
        "omitting the column entirely must NOT report it as unknown -- "
        "'missing' and 'non-finite' are different, deliberately different, "
        "signals"
    )


def test_encode_one_missing_numerical_value_does_not_report_unknown(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """A genuinely missing numerical value (None) must stay uncounted --
    this deliberate gap is separate from the non-finite fix above and must
    not be widened by it."""
    state = build_encoder_state(df, columns)
    _, unknown = encode_one(state, {"genre": "action", "year": None, "tags": "a"})
    assert unknown == []


def test_encode_one_unparseable_numerical_string_does_not_report_unknown(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """A numerical value that fails to parse as a number at all (e.g. a
    non-numeric string) must stay uncounted -- also a deliberate,
    unaffected gap, distinct from the non-finite case above where
    ``float()`` succeeds but the result cannot be standardized."""
    state = build_encoder_state(df, columns)
    _, unknown = encode_one(
        state, {"genre": "action", "year": "not-a-number", "tags": "a"}
    )
    assert unknown == []


# ---------------------------------------------------------------------------
# Review finding (MERGE BLOCKER): `float()` raises OverflowError -- an
# ArithmeticError, NOT a ValueError -- for an int above float64's max, so it
# escaped `_row_values`'s `except (TypeError, ValueError)`, escaped
# `encode_one`, escaped `_idmap`'s RuntimeError-only cold-start catch, escaped
# `routes.py`'s ColdStartNumericalError/ValueError catch, and reached the
# generic HTTP 500 handler. Any JSON integer literal of >=309 digits triggers
# it with nothing but a valid API key.
#
# Note the asymmetry that made this subtle: the STRING "1e400" and the FLOAT
# literal 1e309 both yield `inf` and were already handled (counted unknown,
# HTTP 200). Only the INTEGER token raises.
# ---------------------------------------------------------------------------


def test_encode_one_reports_unknown_for_oversized_integer(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """An integer too large for float64 must be counted as unknown, exactly
    like the +-inf it conceptually is -- not raise, and not silently degrade
    to the column mean."""
    state = build_encoder_state(df, columns)
    y = state["columns"][1]
    huge = 10**309

    # Test-setup invariant: this is the token that raises, and it raises
    # OverflowError specifically -- NOT the ValueError the pre-fix except
    # clause was written for. If a future Python makes float() return inf
    # here instead, this test would silently stop covering the 500 path.
    with pytest.raises(OverflowError):
        float(huge)

    m, unknown = encode_one(state, {"genre": "action", "year": huge, "tags": "a"})
    assert unknown == ["year"], (
        "an oversized integer must fire the unknown-value signal, like +-inf"
    )
    assert m.toarray()[0, y["offset"]] == 0.0


def test_encode_one_reports_unknown_for_oversized_negative_integer(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """The negative side of the same token must behave identically -- it is
    conceptually -inf, and -inf is already a counted unknown."""
    state = build_encoder_state(df, columns)
    y = state["columns"][1]
    m, unknown = encode_one(state, {"genre": "action", "year": -(10**309), "tags": "a"})
    assert unknown == ["year"]
    assert m.toarray()[0, y["offset"]] == 0.0


def test_oversized_integer_arrives_as_a_plain_json_integer_literal(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """Pins the REACHABILITY of the 500, not just the arithmetic.

    A >=309-digit integer is a valid JSON number literal; Python's json
    parser yields an arbitrary-precision `int` for it, and pydantic's
    `dict[str, Any]` passes that through uncoerced -- so the value reaching
    `_row_values` really is a Python int, with no float conversion anywhere
    in between to blunt it. This test builds the value the way a client
    actually would rather than writing `10**309` by hand.
    """
    payload = json.loads('{"year": ' + "9" * 400 + "}")
    assert type(payload["year"]) is int, (
        "premise: a long JSON integer literal must parse to a Python int, "
        "not a float -- otherwise this attack surface would not exist"
    )
    state = build_encoder_state(df, columns)
    _, unknown = encode_one(state, {"genre": "action", **payload, "tags": "a"})
    assert unknown == ["year"]


# ---------------------------------------------------------------------------
# Fix B: the non-finite guard tested the RAW parsed value (`num`), but the
# matrix stores `scaled = (num - mean) / std` cast to float32. A value finite
# as float64 whose STANDARDIZED magnitude exceeds float32's max (~3.4e38)
# becomes +/-inf when the matrix is cast to float32 and was NOT appended to
# `unknown` -- violating the branch's own contract ("an unknown value must not
# also be invisible"). The right variable to check is `scaled`, not `num`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (1e39, "float finite in float64 but standardized magnitude > float32 max"),
        (1e300, "large float, standardizes far above float32 max"),
        ("1e39", "same magnitude arriving as a numeric string"),
    ],
)
def test_encode_one_reports_unknown_for_float32_overflow_value(
    raw: object, why: str
) -> None:
    """A value that parses finite in float64 but standardizes to a magnitude
    float32 cannot hold must be counted as unknown, and must NOT inject a
    non-finite value into the matrix -- pre-fix it was a silent `inf` with
    `unknown == []`."""
    # A state fitted to mean ~ 0 / std ~ 1, so a raw value that is finite in
    # float64 (1e39) still standardizes above float32's max.
    d = pd.DataFrame({"item_id": ["i1", "i2", "i3"], "x": [-1.0, 0.0, 1.0]}).set_index(
        "item_id"
    )
    state = build_encoder_state(d, [FeatureColumn(name="x", encoding="numerical")])
    spec = state["columns"][0]
    # Premise: mean ~ 0, std ~ 0.816 -- so 1e39 standardizes to ~1.2e39, which
    # is finite in float64 but overflows float32 (~3.4e38).
    assert abs(spec["mean"]) < 1e-9, f"premise ({why})"
    assert spec["std"] == pytest.approx(0.816, abs=1e-2), f"premise ({why})"

    m, unknown = encode_one(state, {"x": raw})
    assert unknown == ["x"], f"{raw!r} must fire the unknown-value signal ({why})"
    # The matrix must carry NO non-finite value -- only the bias 1.0.
    assert all(np.isfinite(v) for v in m.data), (
        f"{raw!r} injected a non-finite value into the matrix ({list(m.data)}) -- {why}"
    )
    # And the standardized column contributes nothing, like an omitted value.
    assert m.toarray()[0, spec["offset"]] == 0.0


# ---------------------------------------------------------------------------
# Review finding: `build_encoder_state` fits mean/std with
# `pd.to_numeric(..., errors="coerce")` but `_row_values` parsed the raw value
# with `float()`. The two do not accept the same strings, and `float()` is the
# LOOSER of the pair -- so a value pandas silently dropped from a column's own
# statistics could still be encoded against those statistics.
#
# The invariant these tests pin: a value the FITTING parser could not use must
# not be encodable by the ENCODING parser.
# ---------------------------------------------------------------------------


def test_value_excluded_from_statistics_is_not_encodable() -> None:
    """The headline divergence, end to end.

    ``"1_000"`` is accepted by ``float()`` (PEP 515 underscores) but coerced
    to NaN by ``pd.to_numeric``. So the column fits mean=6.0/std=1.0 from
    {5, 7} ALONE -- the "1_000" row is excluded from its own column's
    statistics -- and then pre-fix encoded that same row to (1000-6)/1 ==
    994.0: a 994-sigma value the fitted statistics never saw, with no
    warning and no counter.
    """
    d = pd.DataFrame(
        {"item_id": ["a", "b", "c"], "price": ["1_000", "5", "7"]}
    ).set_index("item_id")
    state = build_encoder_state(d, [FeatureColumn(name="price", encoding="numerical")])
    spec = state["columns"][0]

    # Premise: pandas really did exclude "1_000" from the column's own stats.
    assert spec["mean"] == pytest.approx(6.0), (
        "premise: the fitting parser must have coerced '1_000' to NaN and "
        "fitted from {5, 7} only"
    )
    assert spec["std"] == pytest.approx(1.0)

    m = encode(state, d, index_order=["a"]).toarray()
    assert m[0, spec["offset"]] == 0.0, (
        "a value the fitting parser coerced to NaN must not be encodable; "
        "pre-fix this was 994.0"
    )


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ("1_000", "PEP 515 underscores: float() accepts, to_numeric rejects"),
        ("１２３", "full-width digits (common in Japanese CSV exports)"),
        ("١٢٣", "Arabic-Indic digits"),
    ],
)
def test_encode_one_rejects_values_the_fitting_parser_cannot_use(
    df: pd.DataFrame, columns: list[FeatureColumn], raw: str, why: str
) -> None:
    """Each string form that ``float()`` accepts but ``pd.to_numeric`` coerces
    to NaN must join the documented unparseable path on the request side too:
    encoded identically to omitting the column, and (like every other
    unparseable value) deliberately uncounted."""
    # Premise: this really is a divergence between the two parsers, or the
    # case proves nothing.
    assert isinstance(float(raw), float), why
    assert pd.isna(pd.to_numeric(pd.Series([raw], dtype=object), errors="coerce")[0]), (
        f"premise for {raw!r}: the fitting parser must coerce it to NaN"
    )

    state = build_encoder_state(df, columns)
    m, unknown = encode_one(state, {"genre": "action", "year": raw, "tags": "a"})
    m_omitted, _ = encode_one(state, {"genre": "action", "tags": "a"})
    np.testing.assert_allclose(m.toarray(), m_omitted.toarray())
    assert unknown == []


def test_ascii_numeric_strings_are_still_accepted(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """The tightening must not overshoot: ordinary ASCII numeric strings --
    including signs, exponents and surrounding whitespace, all of which the
    fitting parser accepts -- must still encode normally."""
    state = build_encoder_state(df, columns)
    y = state["columns"][1]
    baseline, _ = encode_one(state, {"genre": "action", "year": 2000.0, "tags": "a"})
    expected = baseline.toarray()[0, y["offset"]]
    assert expected != 0.0, "premise: 2000.0 must standardize to a nonzero value"

    for raw in ["2000", " 2000 ", "+2000", "2e3", "2000.0"]:
        m, unknown = encode_one(state, {"genre": "action", "year": raw, "tags": "a"})
        assert m.toarray()[0, y["offset"]] == pytest.approx(expected), (
            f"{raw!r} is accepted by the fitting parser and must stay encodable"
        )
        assert unknown == []


# ---------------------------------------------------------------------------
# Review finding: the parity gate above was `str`-only, but `_parse_number`'s
# domain is `Any`. `float()` also parses `bytes`, `bytearray`, `memoryview` and
# any object with `__float__` / `__index__`, while `pd.to_numeric` NaNs all of
# those EXCEPT `bytes` (which it parses as text, with the same grammar it
# applies to `str`). So the "1_000" worked example reproduced verbatim one type
# over: a BYTES column (a SQL BLOB / parquet binary column declared
# `numerical`) of [b"1_000", b"5", b"7"] fitted mean=6.0/std=1.0 from {5, 7}
# ALONE and then encoded the b"1_000" row to 994.0.
#
# JSON cannot carry any of these types, so this is a TRAINING-path bug -- which
# is why these tests drive `encode`, not only `encode_one`. That is the level
# at which the bool regression hid.
#
# Both directions are pinned below, because tightening only direction 1 is how
# the bool regression happened:
#   1. a value the FITTING parser could not use must not be encodable, and
#   2. a value the FITTING parser DID use must stay encodable.
# ---------------------------------------------------------------------------


class _HasFloat:
    """An object `float()` accepts and `pd.to_numeric` NaNs."""

    def __float__(self) -> float:
        return 1000.0


class _HasIndex:
    """`float()` accepts `__index__` too; `pd.to_numeric` still NaNs it."""

    def __index__(self) -> int:
        return 1000


@pytest.mark.parametrize(
    ("poison", "why"),
    [
        (b"1_000", "bytes: float() honours PEP 515 underscores, to_numeric does not"),
        (bytearray(b"1000"), "bytearray: float() parses it, to_numeric NaNs it"),
        (memoryview(b"1000"), "memoryview: float() parses it, to_numeric NaNs it"),
        (Fraction(1000, 1), "Fraction: IS a numbers.Number, yet to_numeric NaNs it"),
        (_HasFloat(), "custom __float__: float() calls it, to_numeric NaNs it"),
        (_HasIndex(), "custom __index__: float() uses it, to_numeric NaNs it"),
    ],
)
def test_non_str_value_excluded_from_statistics_is_not_encodable(
    poison: object, why: str
) -> None:
    """The 994-sigma bug, reproduced across the whole non-``str`` domain.

    Exactly the shape of ``test_value_excluded_from_statistics_is_not_encodable``
    above, one type over: each ``poison`` is a value ``pd.to_numeric`` coerces
    to NaN -- so the column fits mean=6.0/std=1.0 from {5, 7} ALONE -- but that
    ``float()`` happily turns into 1000.0, encoding the excluded row to
    (1000-6)/1 == 994.0 against statistics that never saw it.

    Drives ``encode`` (the training path), because none of these types can
    arrive over JSON.
    """
    d = pd.DataFrame(
        {
            "item_id": ["a", "b", "c"],
            "price": pd.Series([poison, "5", "7"], dtype=object),
        }
    ).set_index("item_id")
    state = build_encoder_state(d, [FeatureColumn(name="price", encoding="numerical")])
    spec = state["columns"][0]

    # Premise: the fitting parser really did exclude the poison value from the
    # column's own statistics, and really did fit from {5, 7}. Without this the
    # case proves nothing.
    assert spec["mean"] == pytest.approx(6.0), f"premise for {why}"
    assert spec["std"] == pytest.approx(1.0), f"premise for {why}"
    # Premise: `float()` -- the encoding parser -- does accept it. This is the
    # divergence itself; if a future Python/numpy stops accepting it, this case
    # would silently stop covering anything.
    assert float(poison) == 1000.0, f"premise for {why}"  # type: ignore[arg-type]

    m = encode(state, d, index_order=["a"]).toarray()
    assert m[0, spec["offset"]] == 0.0, (
        f"a value the fitting parser coerced to NaN must not be encodable "
        f"({why}); pre-fix this encoded to 994.0"
    )


@pytest.mark.parametrize(
    ("value", "why"),
    [
        (b"1000", "bytes ARE text to to_numeric: it parses b'5' -> 5 (measured)"),
        (np.bytes_(b"1000"), "np.bytes_ subclasses bytes and parses the same"),
        (
            Decimal("1000"),
            "a SQL NUMERIC/DECIMAL column yields Decimal; the fit uses it",
        ),
        (np.float32(1000.0), "numpy scalars reach encode via an object-dtype column"),
        (np.int64(1000), "same for the integer scalars"),
    ],
)
def test_non_str_value_the_fitting_parser_used_stays_encodable(
    value: object, why: str
) -> None:
    """Direction 2: refusing a value the FIT used is the bool regression again.

    ``_row_values`` is shared by ``encode`` and ``encode_one``, so a rule that
    rejects one of these silently encodes 0.0 for every TRAINING row of the
    column, against perfectly healthy statistics, with no warning able to fire
    (``feature_zero_variance_column`` cannot see a healthy std). This is why the
    fix is an allow-list measured against ``to_numeric`` rather than a
    convenient ``isinstance(raw, numbers.Number)`` -- which would MISS
    ``np.bool_`` and ``bytes`` and land exactly here.
    """
    d = pd.DataFrame(
        {
            "item_id": ["a", "b", "c"],
            "price": pd.Series([value, 5.0, 7.0], dtype=object),
        }
    ).set_index("item_id")
    # Premise: the object dtype really preserved the exotic type (a native
    # dtype would normalize it to a plain float and prove nothing).
    assert type(d["price"].iloc[0]) is type(value)

    state = build_encoder_state(d, [FeatureColumn(name="price", encoding="numerical")])
    spec = state["columns"][0]

    # Premise: the fitting parser USED the value, so the statistics include it.
    assert spec["mean"] == pytest.approx((1000.0 + 5.0 + 7.0) / 3.0), (
        f"premise: to_numeric must have fitted FROM the value ({why})"
    )

    expected = (1000.0 - spec["mean"]) / spec["std"]
    assert expected != 0.0, "premise: the value must standardize to something visible"

    m = encode(state, d, index_order=["a"]).toarray()
    assert m[0, spec["offset"]] == pytest.approx(expected), (
        f"the fit used this value, so encode must too, or the column is "
        f"silently dead at training time ({why})"
    )


def test_numpy_bool_object_column_encodes_real_values_at_training_time() -> None:
    """REGRESSION guard, ``np.bool_`` edition.

    ``test_bool_column_encodes_real_values_at_training_time`` covers a native
    bool-dtype column, whose cells arrive at ``_row_values`` as plain Python
    ``bool`` (measured). An OBJECT-dtype column preserves ``np.bool_`` instead
    -- and ``np.bool_`` is NOT registered with ``numbers.Number`` (measured),
    while ``pd.to_numeric`` uses it exactly like a bool. So the tidy-looking
    ``isinstance(raw, numbers.Number)`` gate would zero this column at training
    time: the bool regression, one type over.
    """
    d = pd.DataFrame(
        {
            "item_id": ["i1", "i2", "i3", "i4"],
            "in_stock": pd.Series(
                [np.bool_(True), np.bool_(False), np.bool_(True), np.bool_(False)],
                dtype=object,
            ),
        }
    ).set_index("item_id")
    # Premise: the cells really are np.bool_, and really are not numbers.Number.
    import numbers

    assert type(d["in_stock"].iloc[0]) is np.bool_
    assert not isinstance(np.bool_(True), numbers.Number)

    state = build_encoder_state(
        d, [FeatureColumn(name="in_stock", encoding="numerical")]
    )
    spec = state["columns"][0]
    assert (spec["mean"], spec["std"]) == (0.5, 0.5), (
        "premise: to_numeric must fit mean/std from the np.bool_ values themselves"
    )

    m = encode(state, d, ["i1", "i2", "i3", "i4"])
    np.testing.assert_allclose(
        m.toarray()[:, spec["offset"]],
        [1.0, -1.0, 1.0, -1.0],
        err_msg="an np.bool_ column declared `numerical` must standardize to +-1.0",
    )


# --- differential fuzz: _parse_number vs. the fitting parser ---------------
#
# The gate's original justification cited "differential fuzzing over 20k
# numeric-flavored inputs" -- but that fuzz was str-only AND was never
# committed, so nothing re-ran it and nothing could have caught the non-str
# hole above. These two properties are the committed replacement.


def _fitting_parser(value: object) -> float | None:
    """What ``build_encoder_state`` would fit from *value*, or None if it can't.

    Mirrors the ``pd.to_numeric(series, errors="coerce")`` call in
    ``build_encoder_state`` exactly -- one object-dtype cell at a time.
    """
    try:
        fitted = pd.to_numeric(pd.Series([value], dtype=object), errors="coerce")[0]
    except (OverflowError, TypeError, ValueError):
        # `errors="coerce"` does NOT suppress OverflowError for an int above
        # float64's max (measured) -- the documented reason _parse_number does
        # not simply delegate to to_numeric. Nothing to compare against here.
        return None
    if pd.isna(fitted):
        return None
    # Normalize to a plain float: `to_numeric` returns numpy scalars
    # (``np.True_`` for a bool, ``np.int64`` for an int), and ``pytest.approx``
    # mishandles ``np.True_`` (``1.0 == approx(np.True_)`` is False). What
    # `build_encoder_state` actually fits from is ``float(numeric.mean())``,
    # so a plain float is the honest comparison target.
    return float(fitted)


# Non-text values only: `str`/`bytes` carry a documented residual divergence
# (pandas tolerates an internal space in an exponent, "6E 66" -> 5.99e66, which
# `float()` rejects -- measured), so they get the weaker property below.
_non_text_values = st.one_of(
    st.floats(allow_nan=True, allow_infinity=True),
    st.integers(),
    st.booleans(),
    st.decimals(allow_nan=True, allow_infinity=True),
    st.fractions(),
    st.binary(max_size=8).map(bytearray),
    st.binary(max_size=8).map(memoryview),
    st.builds(_HasFloat),
    st.builds(_HasIndex),
    st.datetimes(),
    st.just(np.bool_(True)),
    st.just(np.bool_(False)),
    st.floats(allow_nan=False, allow_infinity=False, width=32).map(np.float32),
    st.integers(min_value=-(2**63), max_value=2**63 - 1).map(np.int64),
)


@settings(
    max_examples=2000, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
@given(value=_non_text_values)
def test_parse_number_mirrors_the_fitting_parser_for_non_text(value: Any) -> None:
    """Full parity, both directions, over the non-text domain.

    Direction 1 (a value the fit dropped must not be encodable) is the
    994-sigma invariant. Direction 2 (a value the fit used must stay encodable)
    is the bool/np.bool_/Decimal regression guard. `complex` is excluded from
    the strategy on purpose: `to_numeric` keeps a complex, but
    `build_encoder_state` then raises TypeError at `float(numeric.mean())`, so
    no state is ever built and the invariant is vacuous for it.
    """
    fitted = _fitting_parser(value)
    parsed = _parse_number(value)

    if fitted is None:
        assert parsed is None or not math.isfinite(parsed), (
            f"{value!r} ({type(value).__name__}) was dropped from the column's "
            f"statistics by the fitting parser, so it must not encode to a "
            f"finite number -- got {parsed!r}"
        )
    else:
        assert parsed is not None, (
            f"{value!r} ({type(value).__name__}) WAS used by the fitting "
            f"parser, so refusing it here silently zeroes the column for every "
            f"training row (the bool regression's exact shape)"
        )
        if math.isfinite(fitted):
            assert parsed == pytest.approx(fitted, rel=1e-12, nan_ok=True)


@settings(
    max_examples=2000, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
@given(
    value=st.one_of(
        st.text(max_size=12),
        st.from_regex(
            r"[+-]?[0-9_]{1,6}(\.[0-9]{0,4})?([eE][+-]?[0-9]{1,3})?", fullmatch=True
        ),
        st.binary(max_size=8),
        st.from_regex(r"[+-]?[0-9_]{1,6}", fullmatch=True).map(str.encode),
    )
)
def test_parse_number_never_encodes_text_the_fitting_parser_dropped(value: Any) -> None:
    """Direction 1 over the text domain (`str` AND `bytes`).

    Only direction 1: the pair has a known, documented residual in the other
    direction (pandas tolerates whitespace inside an exponent where `float()`
    does not), which makes `encode` STRICTER than the fit -- the safe side of
    the invariant, and the same "deliberately uncounted unparseable gap" the
    tests above pin.
    """
    fitted = _fitting_parser(value)
    parsed = _parse_number(value)
    if fitted is None:
        assert parsed is None or not math.isfinite(parsed), (
            f"{value!r} ({type(value).__name__}) was NaN'd by the fitting "
            f"parser, so it must not encode to a finite number -- got {parsed!r}"
        )


# ---------------------------------------------------------------------------
# `bool` is a *numerical* value, deliberately. It is an `int` subclass, so
# `float(True)` is 1.0 -- and that is the right answer, because the fitting
# parser (`pd.to_numeric`) uses bools too: a bool-dtype column fits its
# mean/std FROM them. Excluding bool from `_parse_number` was tried once, on
# the theory that a JSON `true` should not be indistinguishable from the
# number 1.0. It was a regression: `_row_values` is shared by `encode` and
# `encode_one`, so the exclusion also fired on every TRAINING row and silently
# zeroed out any bool column declared `numerical`, against healthy statistics
# and with no warning. These tests pin the accepted behaviour on both paths so
# the exclusion cannot come back unnoticed.
#
# The contrast is `check_artifact_feature_version`, which DOES exclude bool
# from its `isinstance(version, int)` check (see test_features_compat.py) -- a
# state version is an identity token with no numeric reading, not a
# measurement.
# ---------------------------------------------------------------------------


def test_encode_one_boolean_is_a_number(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """A JSON boolean must encode as the number it is, not join the
    unparseable path.

    Asserts against the encoding of the equivalent *number* rather than a
    hardcoded cell value: the encoded number for 1.0 is not 1.0, it is the
    state-dependent standardized `(1.0 - mean) / std`.
    """
    state = build_encoder_state(df, columns)
    y = state["columns"][1]
    m_omitted, _ = encode_one(state, {"genre": "action", "tags": "a"})

    for raw, equivalent in [(True, 1.0), (False, 0.0)]:
        m, unknown = encode_one(state, {"genre": "action", "year": raw, "tags": "a"})
        m_num, _ = encode_one(
            state, {"genre": "action", "year": equivalent, "tags": "a"}
        )
        cell = m.toarray()[0, y["offset"]]

        # Premise: the equivalent number must standardize to something
        # visible, or "encodes like the number" would be trivially true of the
        # all-zero unparseable path and prove nothing.
        assert m_num.toarray()[0, y["offset"]] != 0.0, (
            f"premise: {equivalent} must standardize to a nonzero value "
            f"against this fixture's statistics"
        )
        np.testing.assert_allclose(
            m.toarray(),
            m_num.toarray(),
            err_msg=f"{raw!r} must encode exactly like the number {equivalent}",
        )
        assert cell != m_omitted.toarray()[0, y["offset"]], (
            f"{raw!r} must not degrade to the omitted-column path"
        )
        assert unknown == []


def test_encode_one_true_matches_the_number_one(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """`true` encoding like the number 1.0 is correct, not a collision.

    For a column the recipe declares `numerical`, `true` IS 1.0 -- which is
    also what the fitting parser makes of it. This is the assertion that was
    previously inverted; it is pinned explicitly because the inverted form
    reads plausible in isolation.
    """
    state = build_encoder_state(df, columns)
    y = state["columns"][1]
    m_true, _ = encode_one(state, {"genre": "action", "year": True, "tags": "a"})
    m_one, _ = encode_one(state, {"genre": "action", "year": 1.0, "tags": "a"})

    cell_one = m_one.toarray()[0, y["offset"]]
    # Premise: 1.0 must itself standardize to something visible, or "matches
    # 1.0" would be satisfied by two all-zero rows and prove nothing.
    assert cell_one != 0.0, (
        "premise: the number 1.0 must standardize to a nonzero value against "
        "this fixture's statistics"
    )
    assert m_true.toarray()[0, y["offset"]] == cell_one, (
        "JSON `true` must encode as the number 1.0 for a `numerical` column"
    )


def test_bool_column_encodes_real_values_at_training_time() -> None:
    """REGRESSION: a bool-dtype column declared `numerical` must encode to
    real standardized values on the TRAINING path, not zeros.

    This is the coverage that was missing when `_parse_number` excluded bool.
    The whole failure was invisible from the serve-side tests: the fit was
    healthy (std=0.5, so `feature_zero_variance_column` never fired) and
    `feature_encoder_state_built` still listed the column as active, so a
    retrain of an existing recipe silently degraded the model.

    Asserts the exact encoded values, not merely "not zero": `(x - 0.5) / 0.5`
    is +1.0 for True and -1.0 for False, and the sign carries the meaning.
    """
    d = pd.DataFrame(
        {
            "item_id": ["i1", "i2", "i3", "i4"],
            "in_stock": [True, False, True, False],
        }
    ).set_index("item_id")
    assert d["in_stock"].dtype == bool, "premise: pandas must infer a bool dtype"

    cols = [FeatureColumn(name="in_stock", encoding="numerical")]
    state = build_encoder_state(d, cols)
    spec = state["columns"][0]

    # Premise: the fitting parser used the bools, so the statistics are
    # healthy and the column is not zero-variance.
    assert (spec["mean"], spec["std"]) == (0.5, 0.5), (
        "premise: pd.to_numeric must fit mean/std from the bools themselves"
    )

    m = encode(state, d, ["i1", "i2", "i3", "i4"])
    np.testing.assert_allclose(
        m.toarray()[:, spec["offset"]],
        [1.0, -1.0, 1.0, -1.0],
        err_msg="a bool column declared `numerical` must standardize to +-1.0",
    )


def test_encode_and_encode_one_agree_for_a_bool() -> None:
    """PARITY: the shared `_row_values` must give `encode` (training) and
    `encode_one` (serve) the same answer for a bool.

    The bool exclusion broke exactly this: it was reasoned about as a
    serve-side concern only, but `_row_values` is shared, so it silently
    changed training too. A serve-side-only bool rule would break this test by
    construction, which is the point.
    """
    d = pd.DataFrame(
        {
            "item_id": ["i1", "i2", "i3", "i4"],
            "in_stock": [True, False, True, False],
        }
    ).set_index("item_id")
    state = build_encoder_state(
        d, [FeatureColumn(name="in_stock", encoding="numerical")]
    )

    for entity_id, raw in [("i1", True), ("i2", False)]:
        from_training = encode(state, d, [entity_id]).toarray()
        from_serving, unknown = encode_one(state, {"in_stock": raw})
        np.testing.assert_allclose(
            from_training,
            from_serving.toarray(),
            err_msg=f"encode and encode_one must agree for {raw!r}",
        )
        assert unknown == []


# ---------------------------------------------------------------------------
# Review finding: `min_frequency` is `Field(default=1, ge=1)` with no upper
# bound, so `min_frequency: 50` against a 3-row catalog validates happily and
# prunes EVERY token -- the column comes back width=0 and the `features:`
# block becomes a complete no-op, with `feature_encoder_state_built` at INFO
# listing the column as though it were active. The numerical branch already
# warns loudly on the identical "this column contributes nothing" condition
# (`feature_zero_variance_column`); the vocabulary branches did not.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("encoding", ["categorical", "multi_label"])
def test_min_frequency_that_empties_a_vocabulary_warns(encoding: str) -> None:
    """An emptied vocabulary must warn, mirroring feature_zero_variance_column."""
    import structlog.testing

    # Distinctive sentinel values: a single letter like "a" would collide with
    # ordinary English prose in the event's own `detail` text, making the PII
    # assertion below fail for a reason that has nothing to do with the values.
    values = ["zqxsentinel1", "zqxsentinel2", "zqxsentinel3"]
    d = pd.DataFrame({"item_id": ["i1", "i2", "i3"], "g": values}).set_index("item_id")
    with structlog.testing.capture_logs() as cap:
        state = build_encoder_state(
            d, [FeatureColumn(name="g", encoding=encoding, min_frequency=50)]
        )

    # Premise: the column really did collapse to nothing.
    assert state["columns"][0]["width"] == 0
    assert state["n_features"] == 1  # bias only

    events = [e for e in cap if e.get("event") == "feature_empty_vocabulary_column"]
    assert events, (
        f"an emptied vocabulary must warn; got events: {[e.get('event') for e in cap]}"
    )
    ev = events[0]
    assert ev["log_level"] == "warning"
    assert ev["column"] == "g"
    assert ev["min_frequency"] == 50
    assert ev["distinct_values"] == 3
    # PII rule: column names and counts only -- never the values themselves.
    rendered = str(ev)
    for value in values:
        assert value not in rendered, (
            f"vocabulary values must never be logged; found {value!r} in {ev}"
        )


def test_empty_vocabulary_warning_names_every_emptied_column() -> None:
    """Each emptied column must be named individually -- the operator has to
    know WHICH column is a no-op, not merely that one of them is.

    Updated for the dead-column broadening (see the ``feature_dead_column``
    tests below): ``keep`` must genuinely VARY across rows, or it too would be
    a (correctly) warned dead column. A prior single-row form left ``keep``
    constant, which the broadened check now flags -- so this table has two
    rows and a varying ``keep`` to isolate the min_frequency-emptied columns.
    """
    import structlog.testing

    d = pd.DataFrame(
        {
            "item_id": ["i1", "i2"],
            "genre": ["a", "b"],
            "tags": ["x|y", "z"],
            "keep": ["k", "m"],
        }
    ).set_index("item_id")
    with structlog.testing.capture_logs() as cap:
        build_encoder_state(
            d,
            [
                FeatureColumn(name="genre", encoding="categorical", min_frequency=9),
                FeatureColumn(name="tags", encoding="multi_label", min_frequency=9),
                FeatureColumn(name="keep", encoding="categorical"),
            ],
        )
    warned = {
        e["column"] for e in cap if e.get("event") == "feature_empty_vocabulary_column"
    }
    assert warned == {"genre", "tags"}, (
        f"expected exactly the emptied columns to warn; got {warned}"
    )


# ---------------------------------------------------------------------------
# Review finding: the empty-vocabulary warning keyed on "vocab has no entries",
# but the real property is "the encoded one-hot block is byte-identical to the
# bias column for every row" -- i.e. the column contributes nothing. A CONSTANT
# categorical column (``["rock","rock","rock"]``) has a NON-empty vocab
# (``{"rock"}``) yet its one-hot is all-1 for every row, collinear with the
# bias column -- equally dead, but the ``if vocab: return`` early-out skipped
# it. The numerical branch already catches its constant case
# (``feature_zero_variance_column``); this brings categorical / multi_label to
# parity.
#
# The trap: the null-bearing variant ``["rock", None, "rock"]`` -> [1,0,1] IS
# genuinely informative (it distinguishes has-rock from missing) and must stay
# silent. So the property is whether the encoded column VARIES across rows, not
# whether the vocabulary has exactly one entry.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("encoding", "values"),
    [
        # Distinctive tokens: a bare "a"/"b" would collide with ordinary prose
        # in the warning's own `detail` text ("vocabulary", "collinear"),
        # making the PII assertion below fail for an unrelated reason -- the
        # same trap test_min_frequency_that_empties_a_vocabulary_warns avoids.
        ("categorical", ["zqxrock", "zqxrock", "zqxrock"]),
        # multi_label: every row carries the identical token set, so every
        # row's multi-hot block is identical -- collinear with bias.
        ("multi_label", ["zqxalpha|zqxbeta", "zqxalpha|zqxbeta", "zqxalpha|zqxbeta"]),
    ],
)
def test_constant_vocabulary_column_warns_as_dead(
    encoding: str, values: list[str]
) -> None:
    """A constant (non-empty-vocab) column encodes identically for every row,
    so it is as dead as an emptied one and must warn -- the false negative the
    ``if vocab: return`` early-out used to let through."""
    import structlog.testing

    d = pd.DataFrame(
        {"item_id": [f"i{i}" for i in range(len(values))], "g": values}
    ).set_index("item_id")
    col = (
        FeatureColumn(name="g", encoding=encoding, delimiter="|")
        if (encoding == "multi_label")
        else FeatureColumn(name="g", encoding=encoding)
    )
    with structlog.testing.capture_logs() as cap:
        state = build_encoder_state(d, [col])

    # Premise: the vocab really is NON-empty (so the old early-out would have
    # skipped it) yet the block is constant across rows.
    assert state["columns"][0]["vocab"], "premise: this case has a non-empty vocab"
    m = encode(state, d, index_order=list(d.index)).toarray()
    spec = state["columns"][0]
    block = m[:, spec["offset"] : spec["offset"] + spec["width"]]
    assert len({tuple(r) for r in block.tolist()}) == 1, (
        "premise: every row's encoded block must be identical (dead column)"
    )

    events = [e for e in cap if e.get("event") == "feature_empty_vocabulary_column"]
    assert events, (
        f"a constant (non-empty-vocab) column must warn as dead; "
        f"got events {[e.get('event') for e in cap]}"
    )
    assert events[0]["column"] == "g"
    # PII rule: never the token values themselves.
    rendered = str(events[0])
    for v in set(values):
        for tok in v.split("|"):
            assert tok not in rendered, f"dead-column warning must not log {tok!r}"


@pytest.mark.parametrize(
    ("encoding", "values"),
    [
        # [1,0,1]: has-rock vs missing is real signal, must stay silent.
        ("categorical", ["rock", None, "rock"]),
        # one row {a,b}, one row {b}: the blocks differ -> informative.
        ("multi_label", ["a|b", "b", "a|b"]),
    ],
)
def test_null_bearing_or_varying_vocabulary_column_does_not_warn(
    encoding: str, values: list[str | None]
) -> None:
    """The trap: a null-bearing / partially-covering column has a one-entry (or
    small) vocab but its encoded block VARIES across rows, so it is genuinely
    informative and must NOT be warned as dead -- exactly the property the
    numerical branch expresses with ``std``."""
    import structlog.testing

    d = pd.DataFrame(
        {"item_id": [f"i{i}" for i in range(len(values))], "g": values}
    ).set_index("item_id")
    col = (
        FeatureColumn(name="g", encoding=encoding, delimiter="|")
        if (encoding == "multi_label")
        else FeatureColumn(name="g", encoding=encoding)
    )
    with structlog.testing.capture_logs() as cap:
        state = build_encoder_state(d, [col])
    # Premise: the block really does vary across rows.
    m = encode(state, d, index_order=list(d.index)).toarray()
    spec = state["columns"][0]
    block = m[:, spec["offset"] : spec["offset"] + spec["width"]]
    assert len({tuple(r) for r in block.tolist()}) > 1, (
        "premise: an informative column's block must differ across rows"
    )
    assert not [
        e for e in cap if e.get("event") == "feature_empty_vocabulary_column"
    ], "an informative (varying) column must not be warned as dead"


def test_healthy_vocabulary_does_not_warn() -> None:
    """The warning must not cry wolf on a normal column."""
    import structlog.testing

    d = pd.DataFrame({"item_id": ["i1", "i2"], "g": ["a", "b"]}).set_index("item_id")
    with structlog.testing.capture_logs() as cap:
        build_encoder_state(d, [FeatureColumn(name="g", encoding="categorical")])
    assert not [e for e in cap if e.get("event") == "feature_empty_vocabulary_column"]


def test_all_null_column_warns_as_empty_vocabulary() -> None:
    """A column with no usable values at all is the same "contributes
    nothing" condition, reached by a different route than min_frequency, and
    must warn too."""
    import structlog.testing

    d = pd.DataFrame({"item_id": ["i1", "i2"], "g": [None, None]}).set_index("item_id")
    with structlog.testing.capture_logs() as cap:
        build_encoder_state(d, [FeatureColumn(name="g", encoding="categorical")])
    events = [e for e in cap if e.get("event") == "feature_empty_vocabulary_column"]
    assert events, "an all-null vocabulary column must warn"
    assert events[0]["distinct_values"] == 0


def test_min_frequency_prunes_vocabulary() -> None:
    d = pd.DataFrame(
        {"item_id": ["i1", "i2", "i3"], "g": ["a", "a", "rare"]}
    ).set_index("item_id")
    state = build_encoder_state(
        d, [FeatureColumn(name="g", encoding="categorical", min_frequency=2)]
    )
    assert state["columns"][0]["vocab"] == {"a": 0}
    assert state["n_features"] == 2  # 'a' + bias


def test_min_frequency_counts_multi_label_occurrences_not_rows() -> None:
    """``min_frequency`` is a row count for ``categorical`` but an
    *occurrence* count for ``multi_label``: a single row's repeated tokens
    all count. ``tags="a|a|a"`` in ONE row must satisfy ``min_frequency=2``
    and keep ``a`` -- pins docs/recipe-reference.md's documented semantics
    and guards against reverting to a row-count model for this encoding.
    """
    d = pd.DataFrame({"item_id": ["i1"], "tags": ["a|a|a"]}).set_index("item_id")
    state = build_encoder_state(
        d,
        [FeatureColumn(name="tags", encoding="multi_label", min_frequency=2)],
    )
    assert state["columns"][0]["vocab"] == {"a": 0}
    assert state["n_features"] == 2  # 'a' + bias


def test_dimension_cap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_FEATURE_DIM", "16")
    d = pd.DataFrame(
        {"item_id": [f"i{i}" for i in range(40)], "g": [f"c{i}" for i in range(40)]}
    ).set_index("item_id")
    with pytest.raises(FeatureEncodeError, match="exceeds"):
        build_encoder_state(d, [FeatureColumn(name="g", encoding="categorical")])


def test_missing_source_column_raises(df: pd.DataFrame) -> None:
    with pytest.raises(FeatureEncodeError, match="not present"):
        build_encoder_state(df, [FeatureColumn(name="nope", encoding="categorical")])


# ---------------------------------------------------------------------------
# Fix D: build_encoder_state's numerical branch had two escape hatches.
# (1) `pd.to_numeric(..., errors="coerce")` does NOT suppress OverflowError for
#     an object-dtype Python int above float64's max (>=309 digits) -- it
#     escaped `_fetch_side`'s FeatureEncodeError-only catch and surfaced as
#     exit 1 (unmapped) instead of the documented exit 4.
# (2) A complex column: `to_numeric` keeps complex dtype and (under numpy 2.x)
#     `float(np.complex128)` silently discards the imaginary part
#     (ComplexWarning, not TypeError), so a complex feature column trained on
#     its real part with no error. Both must raise FeatureEncodeError naming
#     the column.
# ---------------------------------------------------------------------------


def test_build_encoder_state_oversized_int_column_raises_feature_encode_error() -> None:
    """An object-dtype numerical column carrying a Python int too large for
    float64 must raise FeatureEncodeError (which _fetch_side maps to exit 4),
    not the raw OverflowError that escaped to exit 1."""
    big = int("1" + "0" * 309)
    # Premise: this really is the token that makes the FIT's own parser raise
    # OverflowError despite errors="coerce" -- an ArithmeticError, so it
    # escaped every ValueError/TypeError-shaped catch on the path.
    with pytest.raises(OverflowError):
        pd.to_numeric(pd.Series([big], dtype=object), errors="coerce")

    d = pd.DataFrame(
        {"item_id": ["a", "b", "c"], "amount": pd.Series([big, 5, 7], dtype=object)}
    ).set_index("item_id")
    with pytest.raises(FeatureEncodeError, match="amount"):
        build_encoder_state(d, [FeatureColumn(name="amount", encoding="numerical")])


def test_build_encoder_state_complex_column_raises_feature_encode_error() -> None:
    """A complex-dtype numerical column must be rejected explicitly. Pre-fix it
    built a state silently, training on the real part alone -- `float(complex)`
    discards the imaginary part under numpy 2.x with only a ComplexWarning."""
    d = pd.DataFrame(
        {
            "item_id": ["a", "b", "c"],
            "amount": pd.Series([1 + 2j, 3 + 4j, 5 + 6j], dtype=object),
        }
    ).set_index("item_id")
    # Premise: to_numeric keeps it complex (so float() would silently discard
    # the imaginary part rather than raise a clean TypeError).
    assert pd.api.types.is_complex_dtype(pd.to_numeric(d["amount"], errors="coerce"))
    with pytest.raises(FeatureEncodeError, match="complex"):
        build_encoder_state(d, [FeatureColumn(name="amount", encoding="numerical")])


def test_encode_one_reports_unknown_columns(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    state = build_encoder_state(df, columns)
    m, unknown = encode_one(state, {"genre": "comedy", "year": 2000.0, "tags": "a"})
    assert m.shape == (1, 6)
    assert unknown == ["genre"]


def test_state_descriptor_shape(df: pd.DataFrame, columns: list[FeatureColumn]) -> None:
    state = build_encoder_state(df, columns)
    d = state_descriptor(state)
    assert d == {"n_features": 6, "columns": ["genre", "year", "tags"]}
    assert state_descriptor(None) is None


# ---------------------------------------------------------------------------
# encode / encode_one parity
#
# encode() goes through a DataFrame row (values may be numpy scalars, NaN,
# pandas NA); encode_one() goes through a plain request dict (values may be
# Python str/int/float/None). A cold-start request must be encoded exactly
# the way training saw the same entity, so these two paths must agree.
# ---------------------------------------------------------------------------


def test_encode_and_encode_one_agree_on_dataframe_row(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """The row encode() produces for a known id must equal encode_one() fed
    the same values pulled out of the DataFrame by hand."""
    state = build_encoder_state(df, columns)
    from_df = encode(state, df, index_order=["i1"]).toarray()
    row = df.loc["i1"]
    from_dict, unknown = encode_one(
        state, {"genre": row["genre"], "year": row["year"], "tags": row["tags"]}
    )
    np.testing.assert_allclose(from_df, from_dict.toarray())
    assert unknown == []


def test_encode_and_encode_one_agree_on_missing_values(
    columns: list[FeatureColumn],
) -> None:
    """A DataFrame row with a real NaN (encode's missing convention) must
    produce the same vector as a dict with None (encode_one's missing
    convention), for the categorical and multi_label columns too -- not just
    numerical.

    The probe frame mixes a string row in with the missing row so pandas
    promotes the missing cell to a real float ``NaN``. A single-row,
    all-``None`` object column is *not* promoted -- pandas leaves it as
    literal Python ``None`` -- so a naive one-row probe would silently
    compare None-vs-None instead of the NaN-vs-None case this test exists to
    cover (confirmed: ``pd.DataFrame({"g": [None]})["g"][0]`` is ``None``,
    but ``pd.DataFrame({"g": [None, "x"]})["g"][0]`` is ``nan``). The
    assertions right after building ``probe_df`` guard against that trap
    recurring silently if pandas' promotion rules ever change.
    """
    train_df = pd.DataFrame(
        {
            "item_id": ["i1", "i2", "i3"],
            "genre": ["action", "drama", "action"],
            "year": [2000.0, 2010.0, 2020.0],
            "tags": ["a|b", "b", None],
        }
    ).set_index("item_id")
    state = build_encoder_state(train_df, columns)

    probe_df = pd.DataFrame(
        {
            "item_id": ["x", "y"],
            "genre": [None, "action"],
            "year": [np.nan, 2000.0],
            "tags": [None, "a|b"],
        }
    ).set_index("item_id")
    # Guard the premise: row "x"'s missing cells must be real float NaN, not
    # Python None, or this test would not exercise what it claims to.
    assert isinstance(probe_df.loc["x", "genre"], float)
    assert np.isnan(probe_df.loc["x", "genre"])
    assert isinstance(probe_df.loc["x", "tags"], float)
    assert np.isnan(probe_df.loc["x", "tags"])

    from_df = encode(state, probe_df, index_order=["x"]).toarray()

    from_dict, unknown_dict = encode_one(
        state, {"genre": None, "year": None, "tags": None}
    )
    np.testing.assert_allclose(from_df, from_dict.toarray())
    # A genuinely missing value must not be reported as an unknown category.
    assert unknown_dict == []


def test_encode_one_agrees_on_nan_vs_none_categorical(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """encode_one must treat a float NaN identically to None for a
    categorical column -- both mean "missing", not "unknown category".

    This talks to encode_one directly (no DataFrame involved) so there is no
    dtype-promotion subtlety to get wrong: it is the most direct possible
    proof that the categorical branch of ``_row_values`` uses ``_is_missing``
    rather than the narrower ``raw is None or str(raw) == ""`` check.
    """
    state = build_encoder_state(df, columns)
    from_nan, unknown_nan = encode_one(
        state, {"genre": float("nan"), "year": 2000.0, "tags": "a"}
    )
    from_none, unknown_none = encode_one(
        state, {"genre": None, "year": 2000.0, "tags": "a"}
    )
    np.testing.assert_allclose(from_nan.toarray(), from_none.toarray())
    assert unknown_nan == unknown_none == []


def test_encode_one_agrees_on_nan_vs_none_multi_label(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """Same guarantee as the categorical case above, for multi_label.

    Direct encode_one-vs-encode_one comparison, fed straight to _tokens via
    _row_values, so it needs no pandas dtype promotion to expose a broken
    ``_is_missing`` check.
    """
    state = build_encoder_state(df, columns)
    from_nan, unknown_nan = encode_one(
        state, {"genre": "action", "year": 2000.0, "tags": float("nan")}
    )
    from_none, unknown_none = encode_one(
        state, {"genre": "action", "year": 2000.0, "tags": None}
    )
    np.testing.assert_allclose(from_nan.toarray(), from_none.toarray())
    assert unknown_nan == unknown_none == []


def test_encode_one_reports_unknown_multi_label_columns(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """Mirrors test_encode_one_reports_unknown_columns (categorical) for the
    multi_label path, which had no dedicated coverage before this test."""
    state = build_encoder_state(df, columns)
    _, unknown_empty = encode_one(
        state, {"genre": "action", "year": 2000.0, "tags": ""}
    )
    assert unknown_empty == []

    _, unknown_all = encode_one(
        state, {"genre": "action", "year": 2000.0, "tags": "zzz|qqq"}
    )
    assert unknown_all == ["tags"]


def test_encode_one_numeric_string_matches_numeric_value(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """A numeric feature passed as the string "2000" must standardize
    identically to the same value passed as a float."""
    state = build_encoder_state(df, columns)
    m_numeric, _ = encode_one(state, {"genre": "action", "year": 2000.0, "tags": "a"})
    m_string, _ = encode_one(state, {"genre": "action", "year": "2000", "tags": "a"})
    np.testing.assert_allclose(m_numeric.toarray(), m_string.toarray())


# ---------------------------------------------------------------------------
# Review finding: multi_label must emit multi-HOT, not counts (a repeated
# token must not double its dimension's weight), and the unknown-value
# counter must fire on ANY supplied token miss, not only a total miss.
# ---------------------------------------------------------------------------


def test_multi_label_duplicate_tokens_encode_as_binary_not_count(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """``tags="a|b|a"`` must put 1.0 on 'a', not 2.0.

    docs/recipe-reference.md documents ``multi_label`` as "multi-hot"
    (binary), but scipy's COO->CSR conversion SUMS duplicate (row, col)
    entries -- appending one 1.0 per raw token occurrence (the pre-fix
    behavior) would silently turn a doubled tag into a weight of 2.0. This
    talks to ``encode`` (the DataFrame/training path); the paired
    ``encode_one`` test below covers the cold-start request path.
    """
    state = build_encoder_state(df, columns)
    probe = pd.DataFrame(
        {"item_id": ["x"], "genre": ["action"], "year": [2000.0], "tags": ["a|b|a"]}
    ).set_index("item_id")
    m = encode(state, probe, index_order=["x"]).toarray()
    tag_spec = state["columns"][2]
    a_idx = tag_spec["vocab"]["a"]
    assert m[0, tag_spec["offset"] + a_idx] == 1.0


def test_encode_one_multi_label_duplicate_tokens_encode_as_binary_not_count(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """Same guarantee as the ``encode`` test above, for the cold-start
    request path (``encode_one``) -- a duplicated token in
    ``item_features`` / ``user_features`` must not double-weight its
    dimension either."""
    state = build_encoder_state(df, columns)
    m, unknown = encode_one(state, {"genre": "action", "year": 2000.0, "tags": "a|b|a"})
    tag_spec = state["columns"][2]
    a_idx = tag_spec["vocab"]["a"]
    assert m.toarray()[0, tag_spec["offset"] + a_idx] == 1.0
    assert unknown == []


def test_encode_one_reports_unknown_for_partial_multi_label_miss(
    df: pd.DataFrame, columns: list[FeatureColumn]
) -> None:
    """A MIXED multi_label value (one known token, one unknown token) must
    still report the column as unknown.

    Before this fix, ``_row_values`` only appended to ``unknown`` when
    EVERY supplied token missed the vocabulary (``toks and not hit``), so
    ``"a|zzz"`` (with 'a' known) silently escaped the counter -- exactly
    the partial-typo shape (``"Action|Thrller"``) the spec's Observability
    section commits to catching: "Fires ... when a multi_label token is
    dropped" (any token), not "when every token is dropped".
    """
    state = build_encoder_state(df, columns)
    _, unknown = encode_one(state, {"genre": "action", "year": 2000.0, "tags": "a|zzz"})
    assert unknown == ["tags"]


# ---------------------------------------------------------------------------
# Characterization: `_tokens` strips each split piece and drops the empty ones,
# so trailing / leading / doubled delimiters and surrounding whitespace all
# tokenize to the same set. Each variant below must encode identically to the
# canonical "a|b". (No production code -- documents existing behavior.)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["a|b|", "|a|b", "a||b", " a | b "])
def test_multi_label_tokenization_ignores_empty_and_whitespace_tokens(
    df: pd.DataFrame, columns: list[FeatureColumn], raw: str
) -> None:
    """Each delimiter/whitespace variant tokenizes to {a, b} and so encodes
    identically to the canonical "a|b"."""
    state = build_encoder_state(df, columns)
    m_canonical, _ = encode_one(
        state, {"genre": "action", "year": 2000.0, "tags": "a|b"}
    )
    m, unknown = encode_one(state, {"genre": "action", "year": 2000.0, "tags": raw})
    np.testing.assert_allclose(m.toarray(), m_canonical.toarray())
    assert unknown == []
