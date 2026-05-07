"""Unit tests for recotem.datasource.csv (sha256 + byte cap)."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import pytest

from recotem.datasource.base import DataSourceError, FetchContext
from recotem.datasource.csv import CSVConfig, CSVSource


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
