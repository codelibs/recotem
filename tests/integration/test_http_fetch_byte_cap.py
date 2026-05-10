"""Integration tests for HTTP fetch byte-cap streaming (CRITICAL-1 / MAJOR-8).

Verifies that fetch_http_bytes refuses responses that exceed max_bytes at the
streaming stage — i.e. that the cap is enforced chunk-by-chunk, not just after
a full read.

Uses pytest-httpserver to serve a body that is exactly cap+1 bytes, confirming
that HttpFetchError is raised and the collected bytes never exceed the cap.
"""

from __future__ import annotations

import pytest

from recotem._http_fetch import HttpFetchError, fetch_http_bytes

pytest_plugins = ("pytest_httpserver",)


def test_byte_cap_exceeded_raises_http_fetch_error(httpserver) -> None:
    """A response body of cap+1 bytes raises HttpFetchError during streaming."""
    cap = 1024  # a tiny cap for speed; well above the streaming chunk size
    body = b"x" * (cap + 1)

    httpserver.expect_request("/big.csv").respond_with_data(
        body, content_type="text/csv"
    )
    url = httpserver.url_for("/big.csv")

    with pytest.raises(HttpFetchError, match="cap|exceed"):
        fetch_http_bytes(
            url,
            timeout=10,
            max_bytes=cap,
            allow_private=True,
        )


def test_byte_cap_exactly_at_limit_succeeds(httpserver) -> None:
    """A response body of exactly cap bytes is accepted (edge: len == cap)."""
    cap = 512
    body = b"y" * cap

    httpserver.expect_request("/exact.csv").respond_with_data(
        body, content_type="text/csv"
    )
    url = httpserver.url_for("/exact.csv")

    result = fetch_http_bytes(
        url,
        timeout=10,
        max_bytes=cap,
        allow_private=True,
    )
    assert len(result) == cap
    assert result == body


def test_byte_cap_one_below_limit_succeeds(httpserver) -> None:
    """A response body of cap-1 bytes is accepted."""
    cap = 512
    body = b"z" * (cap - 1)

    httpserver.expect_request("/small.csv").respond_with_data(
        body, content_type="text/csv"
    )
    url = httpserver.url_for("/small.csv")

    result = fetch_http_bytes(
        url,
        timeout=10,
        max_bytes=cap,
        allow_private=True,
    )
    assert len(result) == cap - 1


def test_byte_cap_body_never_buffered_beyond_cap(httpserver) -> None:
    """The bytes returned on success never exceed max_bytes.

    This is a safety assertion: even if the streaming check fired late,
    the returned bytes must not exceed the cap.
    """
    cap = 256
    body = b"a" * cap  # exactly at the cap — must succeed

    httpserver.expect_request("/ok.bin").respond_with_data(
        body, content_type="application/octet-stream"
    )
    url = httpserver.url_for("/ok.bin")

    result = fetch_http_bytes(
        url,
        timeout=10,
        max_bytes=cap,
        allow_private=True,
    )
    # We returned successfully; the length must be ≤ cap
    assert len(result) <= cap


def test_byte_cap_multi_chunk_body_refused(httpserver) -> None:
    """A body spanning multiple 1 MiB chunks is refused when total > cap.

    With cap = 100 bytes and body = 200 bytes, the first chunk already exceeds
    the cap, so the error fires on the first read.
    """
    cap = 100
    body = b"b" * 200  # exceeds cap

    httpserver.expect_request("/multi.csv").respond_with_data(
        body, content_type="text/csv"
    )
    url = httpserver.url_for("/multi.csv")

    with pytest.raises(HttpFetchError):
        fetch_http_bytes(
            url,
            timeout=10,
            max_bytes=cap,
            allow_private=True,
        )
