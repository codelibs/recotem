"""Unit tests for recotem.datasource.csv (sha256 + byte cap)."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import pytest

from recotem.datasource.base import DataSourceError, FetchContext
from recotem.datasource.csv import (
    CSVConfig,
    CSVSource,
    _infer_compression,
    _redact_url_userinfo,
    _verify_sha256,
)


def _ctx() -> FetchContext:
    return FetchContext(recipe_name="t", run_id="r")


def _write_csv(path: Path, body: str) -> str:
    path.write_text(body)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_csv_local_sha256_match_loads(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    digest = _write_csv(csv_path, "user_id,item_id\n1,a\n2,b\n")
    cfg = CSVConfig(type="csv", path=str(csv_path), sha256=digest)
    df = CSVSource(cfg).fetch(_ctx())
    assert len(df) == 2
    assert list(df.columns) == ["user_id", "item_id"]


def test_csv_local_sha256_mismatch_raises(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, "user_id,item_id\n1,a\n")
    bogus_digest = "0" * 64
    cfg = CSVConfig(type="csv", path=str(csv_path), sha256=bogus_digest)
    with pytest.raises(DataSourceError, match="sha256"):
        CSVSource(cfg).fetch(_ctx())


def test_csv_local_no_sha256_loads(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, "user_id,item_id\n1,a\n2,b\n")
    cfg = CSVConfig(type="csv", path=str(csv_path))
    df = CSVSource(cfg).fetch(_ctx())
    assert len(df) == 2


def test_csv_local_gzip_sha256_match(tmp_path: Path) -> None:
    """sha256 is computed over the raw on-disk bytes (post-gzip)."""
    csv_path = tmp_path / "data.csv.gz"
    body = b"user_id,item_id\n1,a\n2,b\n"
    csv_path.write_bytes(gzip.compress(body))
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    cfg = CSVConfig(type="csv", path=str(csv_path), sha256=digest)
    df = CSVSource(cfg).fetch(_ctx())
    assert len(df) == 2


@pytest.mark.parametrize(
    "path,expected",
    [
        ("data.csv", "data.csv"),
        ("/abs/path/data.csv", "/abs/path/data.csv"),
        ("s3://bucket/key.csv", "s3://bucket/key.csv"),
        # gcsfs idiomatic — bucket@project should NOT be redacted
        (
            "gs://bucket@project.iam.gserviceaccount.com/key.csv",
            "gs://bucket@project.iam.gserviceaccount.com/key.csv",
        ),
        # https with credentials — userinfo stripped
        (
            "https://user:pass@example.com/data.csv?t=1",
            "https://example.com/data.csv?t=1",
        ),
        # https with port preserved
        (
            "https://example.com:8443/data.csv",
            "https://example.com:8443/data.csv",
        ),
    ],
)
def test_redact_url_userinfo_table(path: str, expected: str) -> None:
    assert _redact_url_userinfo(path) == expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("data.csv", None),
        ("data.csv.gz", "gzip"),
        ("data.csv.bz2", "bz2"),
        ("data.csv.zip", "zip"),
        ("data.csv.xz", "xz"),
        ("https://example.com/x.csv.gz?ver=1", "gzip"),
        ("/abs/path/data.parquet", None),
    ],
)
def test_infer_compression_table(path: str, expected: str | None) -> None:
    assert _infer_compression(path) == expected


def test_verify_sha256_match() -> None:
    body = b"hello"
    digest = hashlib.sha256(body).hexdigest()
    _verify_sha256(body, digest)  # must not raise


def test_verify_sha256_mismatch_raises() -> None:
    with pytest.raises(DataSourceError, match="sha256"):
        _verify_sha256(b"hello", "0" * 64)
