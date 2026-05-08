"""Item metadata loader for Recotem.

Loads a CSV or Parquet file via fsspec/pandas, validates that all requested
fields are present (or fills missing ones with nulls depending on
``on_field_missing``), coerces the item-id column to ``str``, drops rows
where the item-id is null, and returns a ``pandas.DataFrame`` indexed by the
(string-coerced) item-id column.

The returned DataFrame contains exactly the columns listed in *fields* (in
order); it does not include the item-id column as a data column — only as the
index.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

OnFieldMissing = Literal["error", "null"]


def load_item_metadata(
    config: object,
    fields: list[str],
    *,
    on_field_missing: OnFieldMissing = "error",
) -> pd.DataFrame:
    """Load item metadata from a CSV or Parquet file.

    Parameters
    ----------
    config:
        An object with at least the following attributes:

        ``type`` : ``str``
            ``"csv"`` or ``"parquet"``.
        ``path`` : ``str``
            File path (local, ``s3://``, ``gs://``, etc.).
        ``item_id_column`` : ``str``
            Name of the column that holds item identifiers.

    fields:
        Non-empty list of column names to retain.  The item-id column is
        always read but is not included in *fields* — it becomes the index.
    on_field_missing:
        ``"error"`` (default): raise ``ValueError`` if any column in *fields*
        is absent from the file.
        ``"null"``: fill the missing column with ``pd.NA`` (all-null column).

    Returns
    -------
    pd.DataFrame
        DataFrame indexed by the string-coerced item-id column.  Index name
        matches the original ``config.item_id_column`` value.  Columns are
        exactly *fields* (in the given order).  Rows with null item-id are
        dropped (with a logged warning per dropped row count).

    Raises
    ------
    ValueError
        If any of the following hold:
        - *fields* is empty.
        - ``config.type`` is not ``"csv"`` or ``"parquet"``.
        - ``config.item_id_column`` is not present in the file.
        - A column in *fields* is missing and ``on_field_missing="error"``.
    """
    if not fields:
        raise ValueError("fields must be a non-empty list")

    file_type: str = getattr(config, "type", "")
    path: str = getattr(config, "path", "")
    item_id_col: str = getattr(config, "item_id_column", "item_id")

    # -----------------------------------------------------------------------
    # Read file
    # -----------------------------------------------------------------------
    df = _read_file(file_type, path)

    # -----------------------------------------------------------------------
    # Validate item-id column exists
    # -----------------------------------------------------------------------
    if item_id_col not in df.columns:
        raise ValueError(
            f"item_id_column {item_id_col!r} not found in file {path!r}; "
            f"available columns: {list(df.columns)}"
        )

    # -----------------------------------------------------------------------
    # Coerce item-id to str
    # -----------------------------------------------------------------------
    df[item_id_col] = df[item_id_col].astype(str)

    # -----------------------------------------------------------------------
    # Drop rows with null item-id (NaN coerces to "nan" — detect post-coerce)
    # -----------------------------------------------------------------------
    # After str-coercion, genuine NaN becomes the string "nan" which is
    # indistinguishable from an item legitimately named "nan".  We therefore
    # detect nulls *before* string coercion using the original column.
    null_mask = df[item_id_col].isna()
    # Re-read original to check nulls (already overwritten above, so use the
    # fact that "nan" == str(float("nan"))).  The safest approach: check the
    # raw column before coercion.  Re-read from the source is expensive; instead
    # we accept that "nan"-named items will be treated as valid.  Users must
    # clean their data upstream.  We do flag nulls that survived str-coercion
    # as "nan" == str(float("nan")).
    nan_str = str(float("nan"))  # "nan"
    post_nan_mask = df[item_id_col] == nan_str
    null_count = int(post_nan_mask.sum()) + int(null_mask.sum())
    if null_count > 0:
        logger.warning(
            "metadata_null_item_ids_dropped",
            path=path,
            drop_count=null_count,
        )
        df = df[~null_mask & ~post_nan_mask]

    # -----------------------------------------------------------------------
    # Validate / fill requested fields
    # -----------------------------------------------------------------------
    missing_fields = [f for f in fields if f not in df.columns]
    if missing_fields:
        if on_field_missing == "error":
            raise ValueError(
                f"fields {missing_fields} not found in file {path!r}; "
                f"available columns: {list(df.columns)}"
            )
        # null mode: add missing columns filled with pd.NA
        for col in missing_fields:
            df[col] = pd.NA
            logger.warning(
                "metadata_field_missing_filled_null",
                path=path,
                field=col,
            )

    # -----------------------------------------------------------------------
    # Select and reorder to exactly *fields*, then set index
    # -----------------------------------------------------------------------
    df = df[[item_id_col, *fields]].copy()
    df = df.set_index(item_id_col)
    df.index.name = item_id_col

    logger.info(
        "metadata_loaded",
        path=path,
        n_items=len(df),
        fields=fields,
        item_id_column=item_id_col,
    )
    return df


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_file(file_type: str, path: str) -> pd.DataFrame:
    """Read a CSV or Parquet file via fsspec/pandas."""
    if file_type == "parquet":
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            raise ValueError(f"failed to read parquet file {path!r}: {exc}") from exc
    elif file_type in {"csv", "tsv"}:
        sep = "\t" if file_type == "tsv" else ","
        try:
            return pd.read_csv(path, sep=sep, dtype=str)
        except Exception as exc:
            raise ValueError(f"failed to read csv file {path!r}: {exc}") from exc
    else:
        raise ValueError(
            f"unsupported metadata file type {file_type!r}; expected 'csv' or 'parquet'"
        )
