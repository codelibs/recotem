"""Guards that a partly-unparseable numerical feature column is reported.

``feature_zero_variance_column`` fires only when a column has NO finite value.
A column that is one fifth unparseable text is fit silently from the rest, and
those rows then standardize to 0.0 -- identical to a row at the column mean and
identical to a legitimately missing cell. After the fit nothing distinguishes
"no price" from "price we could not read", so the count has to be taken at fit
time or it is never available at all.

Serve-time has the matching gap and it is documented (operations.md's
``recotem_v1_feature_unknown_value_total`` row says an unparseable numerical
value degrades silently and is not counted). The train-time side was not
documented anywhere, and it is the side that can still be fixed by correcting
the export.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import pytest
import structlog

from recotem._features import build_encoder_state
from recotem.recipe.models import FeatureColumn

_COL = [FeatureColumn(name="price", encoding="numerical")]


def _build(make_value: Callable[[int], object], n_rows: int = 20) -> list[dict]:
    df = pd.DataFrame(
        [{"item_id": f"i{i}", "price": make_value(i)} for i in range(n_rows)]
    ).set_index("item_id")
    with structlog.testing.capture_logs() as cap:
        build_encoder_state(df, _COL)
    return [e for e in cap if e["event"] == "feature_numerical_unparseable_column"]


def test_a_fifth_of_the_column_unparseable_is_reported() -> None:
    """A thousands separator in an export is the canonical way to reach this."""
    events = _build(lambda i: "1,234" if i % 5 == 0 else float(i))

    assert len(events) == 1, events
    evt = events[0]
    assert evt["log_level"] == "warning"
    assert evt["column"] == "price"
    assert evt["n_unparseable"] == 4
    assert evt["n_rows"] == 20
    # The operator needs the likely cause, not just the count.
    assert "thousands separators" in evt["detail"]


def test_a_stray_bad_cell_is_not_reported() -> None:
    """One bad cell in twenty is noise the mean absorbs; warning would be spam."""
    assert _build(lambda i: "N/A" if i == 0 else float(i)) == []


def test_missing_values_are_not_counted_as_unparseable() -> None:
    """Encoding a missing cell to the mean is documented behaviour, not a bug.

    Counting it here would make the warning fire on every catalog with an
    optional attribute, which is exactly the noise that gets a warning ignored.
    """
    assert _build(lambda i: None if i % 5 == 0 else float(i)) == []


def test_a_clean_column_is_silent() -> None:
    assert _build(float) == []


def test_a_wholly_unparseable_column_is_reported_too() -> None:
    """Still worth naming, even though zero-variance also fires here."""
    events = _build(lambda i: "junk")
    assert len(events) == 1
    assert events[0]["n_unparseable"] == 20


@pytest.mark.parametrize("bad", ["1,234", "$99", "N/A", "", "  "])
def test_common_export_artifacts_all_count_as_unparseable(bad: str) -> None:
    """The shapes an operator actually hits, not just a synthetic token.

    Empty and whitespace-only strings are included deliberately: pandas coerces
    them to NaN like any other unparseable text, and `notna()` is True for
    them, so they are supplied-but-unreadable rather than missing.
    """
    events = _build(lambda i: bad if i % 5 == 0 else float(i))
    assert len(events) == 1, f"{bad!r} was not counted"
    assert events[0]["n_unparseable"] == 4
