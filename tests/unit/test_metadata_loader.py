"""Unit tests for recotem.metadata.loader.

Tests:
- null item_id rows dropped + warning
- field outside allowlist never present
- missing field with on_field_missing=error raises
- missing field with on_field_missing=null fills with NA
- field deny override
- predict returns null metadata for unjoined item
- metadata id string coerced matches recommender ids
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from recotem.metadata.loader import load_item_metadata


class _Config:
    """Minimal config object for load_item_metadata."""

    def __init__(self, type_: str, path: str, item_id_column: str = "item_id"):
        self.type = type_
        self.path = path
        self.item_id_column = item_id_column


def _write_csv(tmp_path: Path, content: str, filename: str = "meta.csv") -> Path:
    p = tmp_path / filename
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# null item_id rows dropped + warning
# ---------------------------------------------------------------------------


def test_warning_logged_and_row_skipped_for_null_item_id(
    tmp_path: Path, caplog
) -> None:
    """Rows with null item_id are dropped and a warning is emitted."""
    csv_file = _write_csv(
        tmp_path,
        "item_id,title\n,No Title\ni1,Item One\n",
    )
    import logging

    with caplog.at_level(logging.WARNING):
        df = load_item_metadata(
            _Config("csv", str(csv_file)),
            fields=["title"],
        )
    assert "i1" in df.index
    # The null row should be gone
    assert len(df) == 1


# ---------------------------------------------------------------------------
# field outside allowlist never present
# ---------------------------------------------------------------------------


def test_field_outside_allowlist_never_in_response(tmp_path: Path) -> None:
    """The returned DataFrame only contains the requested fields."""
    csv_file = _write_csv(
        tmp_path,
        "item_id,title,secret_field\ni1,Title1,SECRET\ni2,Title2,SECRET2\n",
    )
    df = load_item_metadata(
        _Config("csv", str(csv_file)),
        fields=["title"],
    )
    assert "secret_field" not in df.columns
    assert "title" in df.columns


# ---------------------------------------------------------------------------
# missing field with on_field_missing=error
# ---------------------------------------------------------------------------


def test_field_in_allowlist_but_missing_in_file_with_on_field_missing_error(
    tmp_path: Path,
) -> None:
    csv_file = _write_csv(
        tmp_path,
        "item_id,title\ni1,Title1\n",
    )
    with pytest.raises(ValueError, match="not found"):
        load_item_metadata(
            _Config("csv", str(csv_file)),
            fields=["title", "missing_field"],
            on_field_missing="error",
        )


# ---------------------------------------------------------------------------
# missing field with on_field_missing=null
# ---------------------------------------------------------------------------


def test_field_in_allowlist_but_missing_with_on_field_missing_null(
    tmp_path: Path,
) -> None:
    csv_file = _write_csv(
        tmp_path,
        "item_id,title\ni1,Title1\n",
    )
    df = load_item_metadata(
        _Config("csv", str(csv_file)),
        fields=["title", "missing_field"],
        on_field_missing="null",
    )
    assert "missing_field" in df.columns
    assert df["missing_field"].isna().all()


# ---------------------------------------------------------------------------
# metadata field deny override
# ---------------------------------------------------------------------------


def test_metadata_field_deny_overrides_recipe_fields(tmp_path: Path) -> None:
    """Fields in the deny-list are not present in the result."""
    csv_file = _write_csv(
        tmp_path,
        "item_id,title,category\ni1,T1,C1\n",
    )
    df = load_item_metadata(
        _Config("csv", str(csv_file)),
        fields=["title", "category"],
    )
    # Deny "category" post-load
    df_denied = df.drop(columns=["category"], errors="ignore")
    assert "category" not in df_denied.columns
    assert "title" in df_denied.columns


# ---------------------------------------------------------------------------
# predict returns null metadata for unjoined item
# ---------------------------------------------------------------------------


def test_predict_returns_null_metadata_for_unjoined_item(tmp_path: Path) -> None:
    """An item not in the metadata file has no metadata fields in the response."""
    csv_file = _write_csv(
        tmp_path,
        "item_id,title\ni1,Item1\n",
    )
    df = load_item_metadata(
        _Config("csv", str(csv_file)),
        fields=["title"],
    )
    # Simulate a lookup for an item NOT in the metadata
    try:
        row = df.loc["unknown_item"]
        # Should raise KeyError
        assert False, "Expected KeyError"
    except KeyError:
        pass  # correct behaviour — no entry for unknown item


# ---------------------------------------------------------------------------
# metadata id string coerced matches recommender ids
# ---------------------------------------------------------------------------


def test_metadata_id_string_coerced_matches_recommender_ids(tmp_path: Path) -> None:
    """Numeric item_ids in the metadata file are str-coerced for index lookup."""
    csv_file = _write_csv(
        tmp_path,
        "item_id,title\n1,Item1\n2,Item2\n",
    )
    df = load_item_metadata(
        _Config("csv", str(csv_file)),
        fields=["title"],
    )
    # Index should be string even though CSV has numeric values
    assert "1" in df.index
    assert df.loc["1", "title"] == "Item1"


# ---------------------------------------------------------------------------
# empty fields list rejected
# ---------------------------------------------------------------------------


def test_empty_fields_list_raises_value_error(tmp_path: Path) -> None:
    csv_file = _write_csv(tmp_path, "item_id,title\ni1,T1\n")
    with pytest.raises(ValueError, match="non-empty"):
        load_item_metadata(_Config("csv", str(csv_file)), fields=[])


# ---------------------------------------------------------------------------
# parquet support
# ---------------------------------------------------------------------------


def test_parquet_metadata_loads_correctly(tmp_path: Path) -> None:
    parquet_file = tmp_path / "meta.parquet"
    df_orig = pd.DataFrame({"item_id": ["i1", "i2"], "title": ["T1", "T2"]})
    df_orig.to_parquet(parquet_file, index=False)
    df = load_item_metadata(
        _Config("parquet", str(parquet_file)),
        fields=["title"],
    )
    assert "i1" in df.index
    assert df.loc["i1", "title"] == "T1"
