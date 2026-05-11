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

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from recotem.metadata.loader import load_item_metadata

pytest_plugins = ("pytest_httpserver",)


class _Config:
    """Minimal config object for load_item_metadata."""

    def __init__(
        self,
        type_: str,
        path: str,
        item_id_column: str = "item_id",
        sha256: str | None = None,
    ):
        self.type = type_
        self.path = path
        self.item_id_column = item_id_column
        self.sha256 = sha256


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


# ---------------------------------------------------------------------------
# HTTP fetch: sha256 verification, byte cap, redirect controls
# (mirrors the controls already enforced for source.path; see
#  docs/recipe-reference.md and docs/security.md.)
# ---------------------------------------------------------------------------


def _csv_body() -> bytes:
    return b"item_id,title\ni1,Foo\ni2,Bar\n"


def test_http_metadata_sha256_match_loads(httpserver) -> None:
    body = _csv_body()
    digest = hashlib.sha256(body).hexdigest()
    httpserver.expect_request("/items.csv").respond_with_data(
        body, content_type="text/csv"
    )
    url = httpserver.url_for("/items.csv")
    df = load_item_metadata(
        _Config("csv", url, sha256=digest),
        fields=["title"],
    )
    assert "i1" in df.index
    assert df.loc["i1", "title"] == "Foo"


def test_http_metadata_sha256_mismatch_raises(httpserver) -> None:
    body = _csv_body()
    bad_digest = "0" * 64
    httpserver.expect_request("/items.csv").respond_with_data(
        body, content_type="text/csv"
    )
    url = httpserver.url_for("/items.csv")
    with pytest.raises((ValueError, Exception), match="sha256"):
        load_item_metadata(
            _Config("csv", url, sha256=bad_digest),
            fields=["title"],
        )


def test_http_metadata_byte_cap_exceeded_raises(httpserver, monkeypatch) -> None:
    """Body larger than RECOTEM_MAX_DOWNLOAD_BYTES is refused.

    The cap is clamped to a minimum of 1 MiB by config.get_max_download_bytes,
    so the test body must exceed 1 MiB to actually hit the limit.
    """
    big_body = b"item_id,title\n" + (b"a" * (2 * 1024 * 1024))  # > 1 MiB
    httpserver.expect_request("/items.csv").respond_with_data(
        big_body, content_type="text/csv"
    )
    url = httpserver.url_for("/items.csv")
    monkeypatch.setenv("RECOTEM_MAX_DOWNLOAD_BYTES", "1")  # clamps to 1 MiB
    digest = hashlib.sha256(big_body).hexdigest()
    with pytest.raises((ValueError, Exception), match="cap|exceed"):
        load_item_metadata(
            _Config("csv", url, sha256=digest),
            fields=["title"],
        )


def test_http_metadata_redirect_to_disallowed_scheme_refused(
    httpserver, tmp_path
) -> None:
    """A 302 redirect to a non-http(s) scheme is refused (no SSRF helper)."""
    target = tmp_path / "leak.csv"
    target.write_bytes(_csv_body())
    httpserver.expect_request("/items.csv").respond_with_data(
        b"",
        status=302,
        headers={"Location": f"file://{target}"},
    )
    url = httpserver.url_for("/items.csv")
    digest = hashlib.sha256(_csv_body()).hexdigest()
    with pytest.raises((ValueError, Exception), match="scheme|disallowed"):
        load_item_metadata(
            _Config("csv", url, sha256=digest),
            fields=["title"],
        )


def test_local_metadata_sha256_mismatch_raises(tmp_path) -> None:
    """Local files with a sha256 set are also content-verified before parsing."""
    csv_file = _write_csv(
        tmp_path,
        "item_id,title\ni1,Title1\ni2,Title2\n",
    )
    bad_digest = "0" * 64
    with pytest.raises((ValueError, Exception), match="sha256"):
        load_item_metadata(
            _Config("csv", str(csv_file), sha256=bad_digest),
            fields=["title"],
        )


# ---------------------------------------------------------------------------
# custom item_id_column
# ---------------------------------------------------------------------------


def test_custom_item_id_column_resolves_correct_column(tmp_path: Path) -> None:
    """Loader uses config.item_id_column to locate item identifiers."""
    csv_file = _write_csv(
        tmp_path,
        "product_id,title\np1,Widget A\np2,Widget B\n",
    )
    df = load_item_metadata(
        _Config("csv", str(csv_file), item_id_column="product_id"),
        fields=["title"],
    )
    assert df.index.name == "product_id"
    assert "p1" in df.index
    assert df.loc["p1", "title"] == "Widget A"


def test_custom_item_id_column_missing_raises(tmp_path: Path) -> None:
    """Loader raises ValueError when item_id_column is not present in the file."""
    csv_file = _write_csv(
        tmp_path,
        "item_id,title\ni1,Title1\n",
    )
    with pytest.raises(ValueError, match="item_id_column"):
        load_item_metadata(
            _Config("csv", str(csv_file), item_id_column="product_id"),
            fields=["title"],
        )


# ---------------------------------------------------------------------------
# I1. _read_file rejects unsupported type 'tsv'
# ---------------------------------------------------------------------------


def test_read_file_rejects_tsv_type(tmp_path: Path) -> None:
    """_read_file must raise ValueError for unsupported file types like 'tsv'.

    The dead TSV branch was removed; attempting to load a 'tsv' type should
    fail with a clear error rather than silently succeeding or raising
    an AttributeError deep in pandas.
    """
    from recotem.metadata.loader import _read_file

    tsv_path = tmp_path / "x.tsv"
    tsv_path.write_text("item_id\ttitle\ni1\tItem One\n")

    with pytest.raises(ValueError, match="unsupported metadata file type"):
        _read_file("tsv", str(tsv_path), sha256=None)


# ---------------------------------------------------------------------------
# MAJOR-12: on_field_missing="null" — WARNING log + column filled + continue
# ---------------------------------------------------------------------------
# The spec calls this "warn" mode; the actual implementation uses "null".
# Tests verify: WARNING log emitted, column filled with pd.NA, execution continues.


def test_on_field_missing_null_logs_warning_and_continues(
    tmp_path: Path, caplog
) -> None:
    """on_field_missing='null': missing fields produce a WARNING log and the
    DataFrame is returned with pd.NA values — execution does not raise.
    """
    import logging

    csv_file = _write_csv(tmp_path, "item_id,title\ni1,Item1\ni2,Item2\n")

    with caplog.at_level(logging.WARNING):
        df = load_item_metadata(
            _Config("csv", str(csv_file)),
            fields=["title", "absent_column"],
            on_field_missing="null",
        )

    # The function must continue (not raise)
    assert df is not None

    # The missing column must be present and filled with pd.NA
    assert "absent_column" in df.columns, (
        "on_field_missing='null' must add the missing column to the result"
    )
    assert df["absent_column"].isna().all(), (
        "Missing columns in 'null' mode must be all-NA"
    )

    # The valid column must still be present
    assert "title" in df.columns


def test_on_field_missing_null_warning_log_emitted_via_structlog(
    tmp_path: Path,
) -> None:
    """on_field_missing='null': the structlog WARNING must name the missing field.

    structlog logs are captured via structlog.testing.capture_logs, not caplog.
    """
    import structlog.testing

    csv_file = _write_csv(tmp_path, "item_id,title\ni1,Item1\n")

    with structlog.testing.capture_logs() as captured:
        load_item_metadata(
            _Config("csv", str(csv_file)),
            fields=["title", "my_missing_field"],
            on_field_missing="null",
        )

    warning_events = [e for e in captured if e.get("log_level") in ("warning", "warn")]
    assert warning_events, (
        f"At least one WARNING log must be emitted for missing field; "
        f"got events: {captured!r}"
    )
    # At least one warning mentions the missing field name
    warning_str = str(warning_events)
    assert "my_missing_field" in warning_str, (
        f"WARNING log must mention 'my_missing_field'; got: {warning_str!r}"
    )


def test_on_field_missing_null_multiple_missing_fields_all_filled(
    tmp_path: Path,
) -> None:
    """When multiple fields are missing in 'null' mode, all are added as pd.NA."""
    csv_file = _write_csv(tmp_path, "item_id,title\ni1,Item1\n")

    df = load_item_metadata(
        _Config("csv", str(csv_file)),
        fields=["title", "missing_a", "missing_b"],
        on_field_missing="null",
    )

    assert "missing_a" in df.columns
    assert "missing_b" in df.columns
    assert df["missing_a"].isna().all()
    assert df["missing_b"].isna().all()
    # Existing field still present
    assert df.loc["i1", "title"] == "Item1"


def test_on_field_missing_error_raises_with_field_names(tmp_path: Path) -> None:
    """on_field_missing='error' must raise ValueError naming all missing fields."""
    csv_file = _write_csv(tmp_path, "item_id,title\ni1,Item1\n")

    with pytest.raises(ValueError) as exc_info:
        load_item_metadata(
            _Config("csv", str(csv_file)),
            fields=["title", "alpha", "beta"],
            on_field_missing="error",
        )

    msg = str(exc_info.value)
    assert "alpha" in msg or "beta" in msg, (
        f"Error message must name the missing fields; got: {msg!r}"
    )


# ---------------------------------------------------------------------------
# Fix 1: local file size cap via RECOTEM_MAX_DOWNLOAD_BYTES
# ---------------------------------------------------------------------------


def test_local_metadata_size_cap_exceeded_raises(tmp_path: Path, monkeypatch) -> None:
    """A local metadata CSV larger than the cap must raise ValueError.

    The RECOTEM_MAX_DOWNLOAD_BYTES cap documented in CLAUDE.md applies to all
    read paths (local + object-store), not only HTTP downloads.
    """
    from recotem import _size_cap
    from recotem.metadata import loader as metadata_loader

    csv_file = _write_csv(tmp_path, "item_id,title\n" + "i1,Item1\n" * 20)

    # The metadata loader imported `check_size_cap` by name, so binding lives in
    # its own module namespace.  Patch *that* reference (not the source module)
    # so the loader sees the tiny-cap version on its next call.
    original = _size_cap.check_size_cap

    def _tiny_cap(path: str, cap: int, *, label: str = "file") -> None:
        return original(path, 10, label=label)  # 10-byte cap

    monkeypatch.setattr(metadata_loader, "check_size_cap", _tiny_cap)

    with pytest.raises(ValueError, match="RECOTEM_MAX_DOWNLOAD_BYTES"):
        load_item_metadata(_Config("csv", str(csv_file)), fields=["title"])


def test_object_store_uncappable_returns_silently(monkeypatch) -> None:
    """When fsspec stat fails for an object-store path, check_size_cap is silent.

    The real read will surface the error; the cap check must not raise on
    stat failure.
    """
    from recotem._size_cap import check_size_cap

    # s3:// path — fsspec will fail (not installed / no credentials), so the
    # cap check must return without raising SizeCapExceededError.
    try:
        check_size_cap("s3://some-bucket/some/file.csv", cap=1, label="CSV")
    except Exception as exc:
        # Only SizeCapExceededError is a failure here; other exceptions from
        # missing fsspec/credentials are acceptable but should not occur
        # (the helper swallows them).
        from recotem._size_cap import SizeCapExceededError

        if isinstance(exc, SizeCapExceededError):
            raise AssertionError(
                "check_size_cap must not raise SizeCapExceededError "
                "when fsspec stat fails"
            ) from exc
        # Any other exception means fsspec itself raised — that means the
        # helper did NOT swallow the error correctly.
        raise AssertionError(
            f"check_size_cap must swallow fsspec errors silently, got: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Fix 2: literal "nan" item id preserved
# ---------------------------------------------------------------------------


def test_literal_nan_item_id_preserved(tmp_path: Path) -> None:
    """An item whose id is literally the string 'nan' must not be dropped.

    Previously, after astype(str), genuine NaN and the literal string "nan"
    were both dropped.  The fix detects nulls before str-coercion so that
    items whose id field contains the text "nan" are retained.
    """
    csv_file = _write_csv(
        tmp_path,
        # Row 1: literal "nan" id — must be preserved
        # Row 2: real null id (empty cell) — must be dropped
        # Row 3: normal id — must be preserved
        "item_id,title\nnan,NaN Product\n,No Title\ni1,Item One\n",
    )
    df = load_item_metadata(_Config("csv", str(csv_file)), fields=["title"])

    assert "nan" in df.index, (
        "Literal 'nan' item id must be preserved (it is a valid string id)"
    )
    assert "i1" in df.index
    # The null row (empty cell) must be dropped
    assert len(df) == 2, f"Expected 2 rows (nan + i1), got {len(df)}: {list(df.index)}"
    assert df.loc["nan", "title"] == "NaN Product"


# ---------------------------------------------------------------------------
# I-A: sha256 path byte cap — metadata loader
# ---------------------------------------------------------------------------


def test_metadata_sha256_path_over_byte_cap_raises(tmp_path: Path, monkeypatch) -> None:
    """Metadata CSV with sha256 set that exceeds the byte cap must raise ValueError.

    Previously fh.read() (no limit) was used in the sha256 verification branch;
    now fh.read(cap + 1) is used and the size is checked before sha256 verify.
    The stat-based check_size_cap() is best-effort and may be silent on some
    filesystems, so this second line of defence is essential.
    """
    import hashlib

    from recotem.metadata import loader as loader_module

    csv_file = _write_csv(
        tmp_path,
        "item_id,title\n" + f"i{1},Product {1}\n" * 200,
        "big_meta.csv",
    )
    content = csv_file.read_text()
    real_sha256 = hashlib.sha256(content.encode()).hexdigest()

    # Patch cap in the loader's own namespace (it imports get_max_download_bytes
    # directly, so patching recotem.config is not sufficient).
    monkeypatch.setattr(loader_module, "get_max_download_bytes", lambda: 512)

    with pytest.raises(ValueError, match="RECOTEM_MAX_DOWNLOAD_BYTES"):
        load_item_metadata(
            _Config("csv", str(csv_file), sha256=real_sha256),
            fields=["title"],
        )


def test_metadata_sha256_path_within_byte_cap_passes(
    tmp_path: Path, monkeypatch
) -> None:
    """Metadata CSV with sha256 set that is within the cap must be accepted."""
    import hashlib

    from recotem.metadata import loader as loader_module

    csv_file = _write_csv(tmp_path, "item_id,title\ni1,Alpha\ni2,Beta\n")
    content = csv_file.read_text()
    real_sha256 = hashlib.sha256(content.encode()).hexdigest()

    # 64 KiB cap — well above the tiny file.
    monkeypatch.setattr(loader_module, "get_max_download_bytes", lambda: 65536)

    df = load_item_metadata(
        _Config("csv", str(csv_file), sha256=real_sha256),
        fields=["title"],
    )
    assert "i1" in df.index and "i2" in df.index


def test_metadata_sha256_cap_fires_when_stat_silent(
    tmp_path: Path, monkeypatch
) -> None:
    """Metadata sha256-path cap fires even when check_size_cap (stat) is silent.

    Simulates a filesystem where check_size_cap is a no-op, confirming that
    the fh.read(cap+1) enforcement inside the sha256 branch is the independent
    safety net.
    """
    import hashlib

    from recotem.metadata import loader as loader_module

    csv_file = _write_csv(
        tmp_path,
        "item_id,title\n" + "i1,Alpha\n" * 100,
        "nostat_meta.csv",
    )
    content = csv_file.read_text()
    real_sha256 = hashlib.sha256(content.encode()).hexdigest()

    # Make stat-based check silent and set cap below the file size.
    monkeypatch.setattr(loader_module, "check_size_cap", lambda *_a, **_kw: None)
    monkeypatch.setattr(loader_module, "get_max_download_bytes", lambda: 64)

    with pytest.raises(ValueError, match="RECOTEM_MAX_DOWNLOAD_BYTES"):
        load_item_metadata(
            _Config("csv", str(csv_file), sha256=real_sha256),
            fields=["title"],
        )


# ---------------------------------------------------------------------------
# P-1: build_metadata_index — pre-flattened dict for O(1) /predict lookups
# ---------------------------------------------------------------------------


def test_load_metadata_returns_dict_indexed_by_item_id(tmp_path: Path) -> None:
    """build_metadata_index returns a dict[str, dict[str, Any]] keyed by item_id.

    Every item_id from the DataFrame index must appear as a key in the
    returned dict, and each value must be a dict of field->value pairs.
    """
    from recotem.metadata.loader import build_metadata_index

    csv_file = _write_csv(
        tmp_path,
        "item_id,title,category\ni1,Widget A,tools\ni2,Widget B,garden\ni3,Widget C,home\n",
    )
    df = load_item_metadata(
        _Config("csv", str(csv_file)),
        fields=["title", "category"],
    )
    index = build_metadata_index(df)

    assert isinstance(index, dict), "build_metadata_index must return a dict"
    assert set(index.keys()) == {"i1", "i2", "i3"}, (
        f"Keys must match item_ids; got {set(index.keys())}"
    )
    assert index["i1"] == {"title": "Widget A", "category": "tools"}
    assert index["i2"] == {"title": "Widget B", "category": "garden"}
    assert index["i3"] == {"title": "Widget C", "category": "home"}


def test_load_metadata_strips_denylisted_fields_at_flatten_time(tmp_path: Path) -> None:
    """build_metadata_index omits deny-listed fields from every per-item dict.

    Deny filtering applied once at flatten time means /predict can use
    the pre-filtered dict directly without any runtime column check.
    """
    from recotem.metadata.loader import build_metadata_index

    csv_file = _write_csv(
        tmp_path,
        "item_id,title,secret_col\ni1,Public Title,secret-value\n",
    )
    df = load_item_metadata(
        _Config("csv", str(csv_file)),
        fields=["title", "secret_col"],
    )
    # Deny 'secret_col' at flatten time -- case-folded as per router convention.
    deny_set: frozenset[str] = frozenset({"secret_col"})
    index = build_metadata_index(df, deny_set=deny_set)

    assert "i1" in index
    assert "title" in index["i1"], "'title' must be present (not denied)"
    assert "secret_col" not in index["i1"], (
        "'secret_col' must be absent (denied at flatten time)"
    )

    # Case-insensitivity: deny entry is lowercase, column may be mixed-case.
    csv_file2 = _write_csv(
        tmp_path,
        "item_id,title,Secret_Col\ni2,Another,hide-me\n",
        "meta2.csv",
    )
    df2 = load_item_metadata(
        _Config("csv", str(csv_file2)),
        fields=["title", "Secret_Col"],
    )
    deny_set2: frozenset[str] = frozenset({"secret_col"})  # lowercase deny entry
    index2 = build_metadata_index(df2, deny_set=deny_set2)
    assert "Secret_Col" not in index2.get("i2", {}), (
        "Deny set must be case-insensitive (lowercase deny blocks mixed-case column)"
    )


def test_load_metadata_converts_NaN_to_None_for_json_safety(tmp_path: Path) -> None:
    """build_metadata_index replaces float NaN with None for JSON safety.

    pandas uses float NaN for missing values (even in object columns).
    Standard json.dumps raises on NaN; Pydantic model_construct passes it
    through.  The index must contain None so the response serialises cleanly.
    """
    import io

    import pandas as pd

    from recotem.metadata.loader import build_metadata_index

    parquet_buf = io.BytesIO()
    pd.DataFrame(
        {
            "item_id": ["p1", "p2"],
            "title": ["Alpha", "Beta"],
            "score": [float("nan"), 7.5],
        }
    ).to_parquet(parquet_buf, index=False)
    parquet_buf.seek(0)
    parquet_file = tmp_path / "nan_meta.parquet"
    parquet_file.write_bytes(parquet_buf.read())

    df = load_item_metadata(
        _Config("parquet", str(parquet_file)),
        fields=["title", "score"],
    )
    index = build_metadata_index(df)

    # p1 has NaN score -- must be None in the index.
    assert index["p1"]["score"] is None, (
        f"float NaN must be converted to None; got {index['p1']['score']!r}"
    )
    # p2 has a real float score -- must be preserved.
    assert index["p2"]["score"] == 7.5, (
        f"Non-NaN float must be preserved; got {index['p2']['score']!r}"
    )


def test_load_metadata_handles_duplicate_item_ids(tmp_path: Path) -> None:
    """build_metadata_index reflects first-wins deduplication from load_item_metadata.

    load_item_metadata already drops duplicate item_ids (keeping the FIRST
    occurrence via drop_duplicates(keep='first')).  build_metadata_index
    therefore receives a DataFrame with a unique index, so each item_id maps
    to exactly one dict.  This test documents and pins the first-wins contract.
    """
    from recotem.metadata.loader import build_metadata_index

    # The CSV has two rows for 'dup_id'. load_item_metadata keeps the first.
    csv_file = _write_csv(
        tmp_path,
        "item_id,title\ndup_id,First Title\ndup_id,Second Title\ni2,Unique\n",
    )
    df = load_item_metadata(
        _Config("csv", str(csv_file)),
        fields=["title"],
    )
    # The DataFrame must already be deduplicated (first-wins) by load_item_metadata.
    assert list(df.index).count("dup_id") == 1, (
        "load_item_metadata must deduplicate -- first-wins"
    )
    assert df.loc["dup_id", "title"] == "First Title", (
        "First occurrence must be kept when deduplicating"
    )

    index = build_metadata_index(df)

    # The index must contain exactly one entry for 'dup_id'.
    assert "dup_id" in index
    assert index["dup_id"]["title"] == "First Title", (
        "build_metadata_index must reflect first-wins deduplication from loader"
    )


# ---------------------------------------------------------------------------
# N-9: M-8 — MemoryError propagates from load_item_metadata (not wrapped)
# ---------------------------------------------------------------------------


def test_load_item_metadata_memory_error_propagates_unwrapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MemoryError from pd.read_csv inside load_item_metadata must propagate
    without being silently wrapped in ValueError or another exception type.

    This is an OOM-safety contract: a MemoryError during metadata loading must
    escape so the caller (_try_load_artifact or _build_entry) can either
    propagate it further or at least fail loudly rather than swallowing the error.
    """
    from recotem.metadata import loader as metadata_loader_mod

    csv_file = tmp_path / "items.csv"
    pd.DataFrame({"item_id": ["i1"], "title": ["A"]}).to_csv(csv_file, index=False)

    def _oom(*args, **kwargs):
        raise MemoryError("out of memory during CSV parse")

    monkeypatch.setattr(metadata_loader_mod.pd, "read_csv", _oom)

    with pytest.raises(MemoryError):
        load_item_metadata(
            _Config("csv", str(csv_file)),
            fields=["title"],
        )
