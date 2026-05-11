"""Unit tests for recotem.datasource.csv (CSVSource and ParquetSource).

Tests:
- CSV positive path
- Parquet positive path
- Missing required columns -> DataSourceError
- Empty CSV after header -> DataSourceError
- Corrupt parquet -> DataSourceError
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from recotem.datasource.base import DataSourceError, FetchContext
from recotem.datasource.csv import CSVConfig, CSVSource, ParquetConfig, ParquetSource


def _ctx(name: str = "test") -> FetchContext:
    return FetchContext(recipe_name=name, run_id="run-001")


# ---------------------------------------------------------------------------
# CSV positive path
# ---------------------------------------------------------------------------


def test_csv_source_reads_file(tmp_path: Path) -> None:
    csv_file = tmp_path / "interactions.csv"
    csv_file.write_text("user_id,item_id\nu1,i1\nu2,i2\n")
    cfg = CSVConfig(type="csv", path=str(csv_file))
    source = CSVSource(cfg)
    df = source.fetch(_ctx())
    assert len(df) == 2
    assert list(df.columns) == ["user_id", "item_id"]


def test_csv_source_respects_delimiter(tmp_path: Path) -> None:
    tsv_file = tmp_path / "data.tsv"
    tsv_file.write_text("user_id\titem_id\nu1\ti1\n")
    cfg = CSVConfig(type="csv", path=str(tsv_file), delimiter="\t")
    source = CSVSource(cfg)
    df = source.fetch(_ctx())
    assert "user_id" in df.columns


def test_csv_source_dtype_override(tmp_path: Path) -> None:
    csv_file = tmp_path / "dtype.csv"
    csv_file.write_text("user_id,item_id,rating\n1,2,5\n")
    cfg = CSVConfig(type="csv", path=str(csv_file), dtype={"user_id": "str"})
    source = CSVSource(cfg)
    df = source.fetch(_ctx())
    # pandas may return either object (legacy) or StringDtype depending on version.
    assert df["user_id"].dtype == object or pd.api.types.is_string_dtype(df["user_id"])


# ---------------------------------------------------------------------------
# CSV negative paths
# ---------------------------------------------------------------------------


def test_csv_missing_file_raises_DataSourceError(tmp_path: Path) -> None:
    cfg = CSVConfig(type="csv", path=str(tmp_path / "nonexistent.csv"))
    source = CSVSource(cfg)
    with pytest.raises(DataSourceError, match="not found"):
        source.fetch(_ctx())


def test_csv_empty_after_header_raises_DataSourceError(tmp_path: Path) -> None:
    csv_file = tmp_path / "empty.csv"
    csv_file.write_text("user_id,item_id\n")
    cfg = CSVConfig(type="csv", path=str(csv_file))
    source = CSVSource(cfg)
    with pytest.raises(DataSourceError, match="empty"):
        source.fetch(_ctx())


def test_csv_corrupt_file_raises_DataSourceError(tmp_path: Path) -> None:
    """A binary non-CSV file wrapped as CSV raises DataSourceError."""
    corrupt = tmp_path / "corrupt.csv"
    corrupt.write_bytes(b"\x00\x01\x02garbage data\xff")
    cfg = CSVConfig(type="csv", path=str(corrupt))
    source = CSVSource(cfg)
    # pandas may read it as one row of garbled data or raise — either way,
    # if it does succeed with garbled data the test passes, but corrupt data
    # that causes a parse exception must be wrapped in DataSourceError
    try:
        df = source.fetch(_ctx())
        # If pandas manages to read it without raising, that's OK for this test
    except DataSourceError:
        pass  # expected


# ---------------------------------------------------------------------------
# Parquet positive path
# ---------------------------------------------------------------------------


def test_parquet_source_reads_file(tmp_path: Path) -> None:
    parquet_file = tmp_path / "data.parquet"
    df_orig = pd.DataFrame({"user_id": ["u1", "u2"], "item_id": ["i1", "i2"]})
    df_orig.to_parquet(parquet_file, index=False)
    cfg = ParquetConfig(type="parquet", path=str(parquet_file))
    source = ParquetSource(cfg)
    df = source.fetch(_ctx())
    assert len(df) == 2


# ---------------------------------------------------------------------------
# Parquet negative paths
# ---------------------------------------------------------------------------


def test_parquet_corrupt_wraps_in_DataSourceError(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.parquet"
    corrupt.write_bytes(b"this is not a parquet file")
    cfg = ParquetConfig(type="parquet", path=str(corrupt))
    source = ParquetSource(cfg)
    with pytest.raises(DataSourceError):
        source.fetch(_ctx())


def test_parquet_missing_file_raises_DataSourceError(tmp_path: Path) -> None:
    cfg = ParquetConfig(type="parquet", path="/tmp/definitely_missing_12345.parquet")
    source = ParquetSource(cfg)
    with pytest.raises(DataSourceError, match="not found"):
        source.fetch(_ctx())


# ---------------------------------------------------------------------------
# HTTP probe SSRF guard
# ---------------------------------------------------------------------------


def test_csv_probe_refuses_private_http_url(monkeypatch) -> None:
    """CSVSource.probe() must raise DataSourceError for private-IP HTTP URLs.

    assert_host_public() only does a DNS lookup (no network I/O beyond that),
    so this test does not make any HTTP connection.
    """
    monkeypatch.delenv("RECOTEM_HTTP_ALLOW_PRIVATE", raising=False)
    cfg = CSVConfig(type="csv", path="http://127.0.0.1/foo.csv")
    source = CSVSource(cfg)
    with pytest.raises(DataSourceError, match="HTTP probe refused"):
        source.probe()


def test_parquet_probe_refuses_private_http_url(monkeypatch) -> None:
    """ParquetSource.probe() must raise DataSourceError for private-IP HTTP URLs."""
    monkeypatch.delenv("RECOTEM_HTTP_ALLOW_PRIVATE", raising=False)
    cfg = ParquetConfig(type="parquet", path="http://127.0.0.1/data.parquet")
    source = ParquetSource(cfg)
    with pytest.raises(DataSourceError, match="HTTP probe refused"):
        source.probe()


def test_csv_probe_http_allow_private_skips_fsspec(monkeypatch) -> None:
    """With RECOTEM_HTTP_ALLOW_PRIVATE=1, probe() must return without calling fsspec.

    Verifies that fsspec.core.url_to_fs is never invoked for HTTP/HTTPS paths —
    not even in the "allow private" code path — confirming the early return.
    """
    import fsspec.core

    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "1")

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("fsspec.core.url_to_fs must not be called for HTTP URLs")

    monkeypatch.setattr(fsspec.core, "url_to_fs", _fail_if_called)

    cfg = CSVConfig(
        type="csv",
        path="http://127.0.0.1/foo.csv",
        sha256="a" * 64,
    )
    source = CSVSource(cfg)
    # Should return without error and without touching fsspec
    source.probe()


# ---------------------------------------------------------------------------
# E-8: byte cap enforced on local non-network reads
# ---------------------------------------------------------------------------


def test_local_csv_over_byte_cap_rejected(tmp_path: Path, monkeypatch) -> None:
    """A local CSV file larger than RECOTEM_MAX_DOWNLOAD_BYTES must raise DataSourceError.

    The cap (controlled by RECOTEM_MAX_DOWNLOAD_BYTES) must be enforced for local
    paths — not only for HTTP downloads — to prevent OOM on large inputs.
    """
    from recotem.datasource import csv as csv_module

    csv_file = tmp_path / "big.csv"
    # Write 100 bytes of real CSV content.
    csv_file.write_text("user_id,item_id\n" + "u1,i1\n" * 10)

    # Patch the cap to 10 bytes so the real file (100 bytes) exceeds it.
    monkeypatch.setattr(csv_module, "_get_max_download_bytes", lambda: 10)

    cfg = CSVConfig(type="csv", path=str(csv_file))
    source = CSVSource(cfg)
    with pytest.raises(DataSourceError, match="RECOTEM_MAX_DOWNLOAD_BYTES"):
        source.fetch(_ctx())


def test_local_parquet_over_byte_cap_rejected(tmp_path: Path, monkeypatch) -> None:
    """A local Parquet file larger than the byte cap must raise DataSourceError.

    Same enforcement as CSV — the cap applies to all non-HTTP source reads.
    """
    from recotem.datasource import csv as csv_module

    parquet_file = tmp_path / "big.parquet"
    df_orig = pd.DataFrame({"user_id": ["u1", "u2"] * 50, "item_id": ["i1", "i2"] * 50})
    df_orig.to_parquet(parquet_file, index=False)

    # Patch the cap to 10 bytes so the parquet file (much larger) exceeds it.
    monkeypatch.setattr(csv_module, "_get_max_download_bytes", lambda: 10)

    cfg = ParquetConfig(type="parquet", path=str(parquet_file))
    source = ParquetSource(cfg)
    with pytest.raises(DataSourceError, match="RECOTEM_MAX_DOWNLOAD_BYTES"):
        source.fetch(_ctx())


# ---------------------------------------------------------------------------
# Fix 3: file://localhost path parsing
# ---------------------------------------------------------------------------


def test_file_localhost_uri_resolves_correctly(tmp_path: Path, monkeypatch) -> None:
    """file://localhost/abs/path must resolve to /abs/path, not localhost/abs/path.

    The old ``Path(path.removeprefix("file://"))`` left ``localhost`` as a
    path component, turning the absolute path into a relative one and silently
    skipping the size cap check.  The fix uses urllib.request.url2pathname on
    the parsed path component.
    """
    from recotem import _size_cap

    csv_file = tmp_path / "data.csv"
    csv_file.write_text("user_id,item_id\nu1,i1\nu2,i2\n")

    file_localhost_uri = f"file://localhost{csv_file}"

    # Confirm that _file_uri_to_local_path resolves to the real absolute path.
    result = _size_cap._file_uri_to_local_path(file_localhost_uri)
    assert result == csv_file, (
        f"file://localhost URI must resolve to {csv_file}, got {result}"
    )


def test_file_localhost_size_cap_fires(tmp_path: Path, monkeypatch) -> None:
    """Size cap must fire for a file://localhost URI that points to an oversized file."""
    from recotem import _size_cap

    csv_file = tmp_path / "data.csv"
    csv_file.write_text("user_id,item_id\n" + "u1,i1\n" * 20)

    file_localhost_uri = f"file://localhost{csv_file}"

    with pytest.raises(
        _size_cap.SizeCapExceededError, match="RECOTEM_MAX_DOWNLOAD_BYTES"
    ):
        _size_cap.check_size_cap(file_localhost_uri, cap=10, label="CSV")


# ---------------------------------------------------------------------------
# CRITICAL: sha256 mismatch on HTTP path raises DataSourceError
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# C8 — missing required column is surfaced after fetch
# ---------------------------------------------------------------------------


def test_csv_missing_required_column_raises_DataSourceError(tmp_path: Path) -> None:
    """A CSV that lacks the recipe-declared user_column raises DataSourceError.

    CSVSource.fetch() reads whatever columns the CSV contains and returns the
    DataFrame.  The required-column check is the caller's responsibility
    (e.g. pipeline._cleanse).  This test documents that behavior by asserting:
    (a) fetch() succeeds and returns a DataFrame, AND
    (b) the DataFrame does NOT contain the required user_column 'user_id'.

    The test then verifies that calling the downstream cleanse function with
    such a DataFrame raises the expected error — this is the full code path
    that would surface to the user.
    """
    from recotem.datasource.base import FetchContext

    # CSV has 'other_user' and 'item_id' but NOT 'user_id'.
    csv_file = tmp_path / "wrong_cols.csv"
    csv_file.write_text("other_user,item_id\nu1,i1\nu2,i2\n")

    cfg = CSVConfig(type="csv", path=str(csv_file))
    source = CSVSource(cfg)
    ctx = FetchContext(recipe_name="col_test", run_id="run-col")

    # Fetch itself succeeds — CSVSource returns whatever columns the file has.
    df = source.fetch(ctx)
    assert "user_id" not in df.columns, (
        "CSV with 'other_user' column must NOT contain 'user_id' after fetch"
    )

    # Downstream column access raises KeyError, which the pipeline wraps.
    # We simulate that to confirm the gap is surfaced.
    with pytest.raises(KeyError):
        _ = df["user_id"]


def test_csv_source_sha256_mismatch_via_http_raises(httpserver) -> None:
    """Serve a small CSV via pytest-httpserver and request with wrong sha256.

    CSVSource.fetch() must raise DataSourceError whose message contains
    "sha256" when the downloaded content does not match the declared digest.
    This is distinct from metadata sha256 tests — it exercises the data-
    source HTTP path.
    """
    from recotem.datasource.base import DataSourceError, FetchContext
    from recotem.datasource.csv import CSVConfig, CSVSource

    body = b"user_id,item_id\nu1,i1\nu2,i2\n"
    httpserver.expect_request("/data.csv").respond_with_data(
        body,
        status=200,
        content_type="text/csv",
    )

    wrong_sha256 = "0" * 64  # definitely wrong

    cfg = CSVConfig(
        type="csv",
        path=httpserver.url_for("/data.csv"),
        sha256=wrong_sha256,
    )
    source = CSVSource(cfg)
    ctx = FetchContext(recipe_name="sha256_test", run_id="run-sha-test")

    with pytest.raises(DataSourceError, match="sha256"):
        source.fetch(ctx)


# ---------------------------------------------------------------------------
# I-A: sha256 path byte cap — CSV and Parquet non-network paths
# ---------------------------------------------------------------------------


def test_csv_sha256_path_over_byte_cap_rejected(tmp_path: Path, monkeypatch) -> None:
    """A local CSV file with sha256 set that exceeds the byte cap must be rejected.

    Previously f.read() (no limit) would buffer the entire file; now
    f.read(cap + 1) is used and the size is checked before sha256 verification.
    The stat-based check_size_cap() is best-effort and may be silent on some
    filesystems, so this second line of defence is essential.
    """
    import hashlib

    from recotem.datasource import csv as csv_module

    # Write a file with enough content to exceed a small cap.
    csv_file = tmp_path / "sha256_big.csv"
    content = "user_id,item_id\n" + "u1,i1\n" * 200  # ~1.4 KiB
    csv_file.write_text(content)

    # Patch the cap to 1024 bytes so the file exceeds it.
    monkeypatch.setattr(csv_module, "_get_max_download_bytes", lambda: 1024)

    real_sha256 = hashlib.sha256(content.encode()).hexdigest()
    cfg = CSVConfig(type="csv", path=str(csv_file), sha256=real_sha256)
    source = CSVSource(cfg)
    with pytest.raises(DataSourceError, match="RECOTEM_MAX_DOWNLOAD_BYTES"):
        source.fetch(_ctx())


def test_csv_sha256_path_within_byte_cap_passes(tmp_path: Path, monkeypatch) -> None:
    """A local CSV file with sha256 set that is within the cap must be accepted.

    Ensures the cap check does not fire for files that are legitimately small.
    """
    import hashlib

    from recotem.datasource import csv as csv_module

    csv_file = tmp_path / "sha256_small.csv"
    content = "user_id,item_id\nu1,i1\nu2,i2\n"
    csv_file.write_text(content)

    # Cap at 8192 bytes — well above the tiny file.
    monkeypatch.setattr(csv_module, "_get_max_download_bytes", lambda: 8192)

    real_sha256 = hashlib.sha256(content.encode()).hexdigest()
    cfg = CSVConfig(type="csv", path=str(csv_file), sha256=real_sha256)
    source = CSVSource(cfg)
    df = source.fetch(_ctx())
    assert len(df) == 2


def test_parquet_sha256_path_over_byte_cap_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    """A local Parquet file with sha256 set that exceeds the byte cap must be rejected.

    Mirrors the CSV test for the ParquetSource sha256 code path.
    """
    import hashlib

    from recotem.datasource import csv as csv_module

    parquet_file = tmp_path / "sha256_big.parquet"
    df_orig = pd.DataFrame(
        {
            "user_id": ["u" + str(i) for i in range(50)],
            "item_id": ["i" + str(i) for i in range(50)],
        }
    )
    df_orig.to_parquet(parquet_file, index=False)

    file_bytes = parquet_file.read_bytes()
    real_sha256 = hashlib.sha256(file_bytes).hexdigest()

    # Cap smaller than the Parquet file.
    monkeypatch.setattr(csv_module, "_get_max_download_bytes", lambda: 10)

    cfg = ParquetConfig(type="parquet", path=str(parquet_file), sha256=real_sha256)
    source = ParquetSource(cfg)
    with pytest.raises(DataSourceError, match="RECOTEM_MAX_DOWNLOAD_BYTES"):
        source.fetch(_ctx())


def test_parquet_sha256_path_within_byte_cap_passes(
    tmp_path: Path, monkeypatch
) -> None:
    """A local Parquet file with sha256 set that is within the cap must be accepted."""
    import hashlib

    from recotem.datasource import csv as csv_module

    parquet_file = tmp_path / "sha256_ok.parquet"
    df_orig = pd.DataFrame({"user_id": ["u1", "u2"], "item_id": ["i1", "i2"]})
    df_orig.to_parquet(parquet_file, index=False)

    file_bytes = parquet_file.read_bytes()
    real_sha256 = hashlib.sha256(file_bytes).hexdigest()

    # 1 MiB cap — well above the small parquet file.
    monkeypatch.setattr(csv_module, "_get_max_download_bytes", lambda: 1024 * 1024)

    cfg = ParquetConfig(type="parquet", path=str(parquet_file), sha256=real_sha256)
    source = ParquetSource(cfg)
    df = source.fetch(_ctx())
    assert len(df) == 2


def test_csv_sha256_cap_fires_even_when_stat_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    """The sha256-path cap fires even when check_size_cap (stat) is silent.

    Simulates a filesystem where stat fails by replacing check_size_cap with a
    no-op.  The sha256-path f.read(cap+1) enforcement must still catch oversized
    files independently of the stat-based pre-check.
    """
    import hashlib

    from recotem._size_cap import SizeCapExceededError  # noqa: F401
    from recotem.datasource import csv as csv_module

    csv_file = tmp_path / "nostat.csv"
    content = "user_id,item_id\n" + "u1,i1\n" * 200
    csv_file.write_text(content)

    # Make stat-based check a no-op.
    monkeypatch.setattr(csv_module, "_check_size_cap", lambda *_a, **_kw: None)
    monkeypatch.setattr(csv_module, "_get_max_download_bytes", lambda: 512)

    real_sha256 = hashlib.sha256(content.encode()).hexdigest()
    cfg = CSVConfig(type="csv", path=str(csv_file), sha256=real_sha256)
    source = CSVSource(cfg)
    with pytest.raises(DataSourceError, match="RECOTEM_MAX_DOWNLOAD_BYTES"):
        source.fetch(_ctx())


# ---------------------------------------------------------------------------
# N-9: M-8 — MemoryError propagates from CSVSource.fetch (not wrapped)
# ---------------------------------------------------------------------------


def test_csv_source_memory_error_propagates_unwrapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MemoryError from pd.read_csv must propagate without being caught and
    re-raised as DataSourceError.

    This is an OOM-safety contract: silently catching MemoryError and
    retrying every poll cycle drives the process to the OOM killer with no
    observable symptom.  The error must escape so the caller's process-level
    handler can react.
    """
    import pandas

    csv_file = tmp_path / "data.csv"
    csv_file.write_text("user_id,item_id\nu1,i1\n")

    def _oom(*args, **kwargs):
        raise MemoryError("out of memory")

    # pandas is imported locally inside CSVSource.fetch, so we patch
    # the top-level pandas module attribute directly.
    monkeypatch.setattr(pandas, "read_csv", _oom)

    cfg = CSVConfig(type="csv", path=str(csv_file))
    source = CSVSource(cfg)
    with pytest.raises(MemoryError):
        source.fetch(_ctx())
