"""Unit tests for recotem.datasource.csv (sha256 + byte cap)."""

from __future__ import annotations

import gzip
import hashlib
import http.server
import socketserver
import threading
from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def _local_http_server(payload: bytes, status: int = 200) -> Iterator[str]:
    """Yield a base URL serving *payload* once."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs) -> None:  # noqa: D401
            return

        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_csv_fetch_with_matching_sha256_loads() -> None:
    body = b"user_id,item_id\n1,a\n2,b\n"
    digest = hashlib.sha256(body).hexdigest()
    with _local_http_server(body) as base:
        cfg = CSVConfig(type="csv", path=f"{base}/data.csv", sha256=digest)
        df = CSVSource(cfg).fetch(_ctx())
    assert len(df) == 2


def test_http_csv_fetch_sha256_mismatch_raises() -> None:
    body = b"user_id,item_id\n1,a\n"
    bogus = "0" * 64
    with _local_http_server(body) as base:
        cfg = CSVConfig(type="csv", path=f"{base}/data.csv", sha256=bogus)
        with pytest.raises(DataSourceError, match="sha256"):
            CSVSource(cfg).fetch(_ctx())


def test_http_csv_fetch_byte_cap_exceeded_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"user_id,item_id\n" + (b"0,a\n" * 1000)  # > 1 KiB
    digest = hashlib.sha256(body).hexdigest()
    # Patch the cap below body size to make the test deterministic:
    from recotem.datasource import csv as csvmod

    monkeypatch.setattr(csvmod, "_get_max_download_bytes", lambda: 100)
    with _local_http_server(body) as base:
        cfg = CSVConfig(type="csv", path=f"{base}/data.csv", sha256=digest)
        with pytest.raises(DataSourceError, match="exceeded"):
            CSVSource(cfg).fetch(_ctx())


def test_http_csv_fetch_404_raises() -> None:
    with _local_http_server(b"", status=404) as base:
        cfg = CSVConfig(
            type="csv",
            path=f"{base}/missing.csv",
            sha256="0" * 64,
        )
        with pytest.raises(DataSourceError, match=r"HTTP 404"):
            CSVSource(cfg).fetch(_ctx())


def test_http_csv_fetch_follows_one_redirect() -> None:
    """3xx → 200 should resolve and load."""
    body = b"user_id,item_id\n1,a\n2,b\n"
    digest = hashlib.sha256(body).hexdigest()

    class RedirectHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs) -> None:
            return

        def do_GET(self) -> None:
            if self.path == "/start.csv":
                self.send_response(302)
                base = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}"
                self.send_header("Location", f"{base}/final.csv")
                self.end_headers()
            elif self.path == "/final.csv":
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    server = socketserver.TCPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        cfg = CSVConfig(
            type="csv",
            path=f"http://{host}:{port}/start.csv",
            sha256=digest,
        )
        df = CSVSource(cfg).fetch(_ctx())
        assert len(df) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_csv_fetch_redirect_loop_detected() -> None:
    """A redirect cycle must trip the visited-set guard."""

    class LoopHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs) -> None:
            return

        def do_GET(self) -> None:
            self.send_response(302)
            base = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}"
            other = "/b" if self.path == "/a" else "/a"
            self.send_header("Location", f"{base}{other}")
            self.end_headers()

    server = socketserver.TCPServer(("127.0.0.1", 0), LoopHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        cfg = CSVConfig(
            type="csv",
            path=f"http://{host}:{port}/a",
            sha256="0" * 64,
        )
        with pytest.raises(DataSourceError, match="loop|redirects"):
            CSVSource(cfg).fetch(_ctx())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
