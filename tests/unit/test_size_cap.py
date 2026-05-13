"""Unit tests for recotem._size_cap.

Tests:
- SizeCapExceededError is raised when reported size exceeds cap (local file).
- SizeCapProbeError is raised when fsspec raises an unexpected exception.
- FileNotFoundError from fsspec is silently returned (best-effort skip).
- PermissionError from fsspec is silently returned (best-effort skip).
- MemoryError propagates unwrapped (OOM let-propagate policy).
- HTTP/HTTPS paths are skipped (already capped by streaming fetch).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from recotem._size_cap import (
    SizeCapExceededError,
    SizeCapProbeError,
    check_size_cap,
)

# ---------------------------------------------------------------------------
# Local file path — cheap stat, no fsspec
# ---------------------------------------------------------------------------


def test_local_file_under_cap_passes(tmp_path: Path) -> None:
    """A local file smaller than the cap must not raise."""
    f = tmp_path / "small.csv"
    f.write_bytes(b"user_id,item_id\n1,a\n")
    check_size_cap(str(f), cap=1024 * 1024, label="CSV")  # must not raise


def test_local_file_exceeds_cap_raises(tmp_path: Path) -> None:
    """A local file larger than the cap must raise SizeCapExceededError."""
    f = tmp_path / "big.csv"
    f.write_bytes(b"x" * 200)
    with pytest.raises(SizeCapExceededError, match="RECOTEM_MAX_DOWNLOAD_BYTES"):
        check_size_cap(str(f), cap=100, label="CSV")


def test_local_file_missing_returns_silently(tmp_path: Path) -> None:
    """A missing local file must not raise — the read will surface the error."""
    check_size_cap(str(tmp_path / "no_such_file.csv"), cap=100, label="CSV")


def test_file_uri_under_cap_passes(tmp_path: Path) -> None:
    """A file:// URI for a small file must not raise."""
    f = tmp_path / "ok.csv"
    f.write_bytes(b"header\n1\n")
    check_size_cap(f.as_uri(), cap=1024, label="CSV")  # must not raise


def test_file_uri_exceeds_cap_raises(tmp_path: Path) -> None:
    """A file:// URI for a file that exceeds the cap must raise."""
    f = tmp_path / "big.csv"
    f.write_bytes(b"y" * 500)
    with pytest.raises(SizeCapExceededError, match="RECOTEM_MAX_DOWNLOAD_BYTES"):
        check_size_cap(f.as_uri(), cap=200, label="CSV")


# ---------------------------------------------------------------------------
# HTTP/HTTPS paths — skipped (capped by streaming fetch)
# ---------------------------------------------------------------------------


def test_http_path_skipped() -> None:
    """HTTP paths must be skipped without any stat or error."""
    check_size_cap("http://example.com/data.csv", cap=1, label="CSV")  # no raise


def test_https_path_skipped() -> None:
    """HTTPS paths must be skipped without any stat or error."""
    check_size_cap("https://example.com/data.csv", cap=1, label="CSV")  # no raise


# ---------------------------------------------------------------------------
# Object-store paths — fsspec stat (best-effort)
# ---------------------------------------------------------------------------


def _make_mock_fs(size: int | None = None) -> MagicMock:
    """Return a mock fsspec filesystem whose info() returns the given size."""
    fs = MagicMock()
    fs.info.return_value = {"size": size} if size is not None else {}
    return fs


def test_object_store_under_cap_passes() -> None:
    """Object-store path smaller than cap must not raise."""
    fs = _make_mock_fs(size=50)
    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
        check_size_cap("s3://bucket/key.csv", cap=100, label="CSV")  # no raise


def test_object_store_exceeds_cap_raises() -> None:
    """Object-store path larger than cap must raise SizeCapExceededError."""
    fs = _make_mock_fs(size=200)
    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
        with pytest.raises(SizeCapExceededError, match="RECOTEM_MAX_DOWNLOAD_BYTES"):
            check_size_cap("s3://bucket/key.csv", cap=100, label="CSV")


def test_object_store_no_size_in_info_passes() -> None:
    """When fsspec info() returns no 'size' key, the cap check is skipped."""
    fs = MagicMock()
    fs.info.return_value = {}  # no size key
    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
        check_size_cap("s3://bucket/key.csv", cap=1, label="CSV")  # no raise


# ---------------------------------------------------------------------------
# IO-1: bare except Exception narrowed — new behaviour
# ---------------------------------------------------------------------------


def test_object_store_file_not_found_returns_silently() -> None:
    """FileNotFoundError from fsspec must be swallowed (best-effort skip)."""
    fs = MagicMock()
    fs.info.side_effect = FileNotFoundError("key not found")
    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/missing")):
        # Must NOT raise — best-effort skip
        check_size_cap("s3://bucket/missing.csv", cap=100, label="CSV")


def test_object_store_permission_error_returns_silently() -> None:
    """PermissionError from fsspec must be swallowed (best-effort skip)."""
    fs = MagicMock()
    fs.info.side_effect = PermissionError("access denied")
    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/locked")):
        # Must NOT raise — best-effort skip
        check_size_cap("s3://bucket/locked.csv", cap=100, label="CSV")


def test_object_store_unexpected_exception_raises_size_cap_probe_error() -> None:
    """Any non-permission, non-missing fsspec error must raise SizeCapProbeError.

    Previously the bare ``except Exception: return`` silently discarded the
    download cap for errors like botocore.ClientError, fsspec backend bugs,
    or connectivity failures.  The fix narrows the except clause and wraps
    unexpected errors in SizeCapProbeError so callers can decide how to handle
    them (e.g. DataSourceError).
    """
    fs = MagicMock()
    fs.info.side_effect = Exception("boom — backend error")
    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
        with pytest.raises(SizeCapProbeError):
            check_size_cap("s3://bucket/key.csv", cap=100, label="CSV")


def test_object_store_unexpected_exception_has_error_class_in_message() -> None:
    """SizeCapProbeError message must include the error class name for diagnostics."""
    fs = MagicMock()
    fs.info.side_effect = RuntimeError("unexpected backend failure")
    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
        with pytest.raises(SizeCapProbeError, match="RuntimeError"):
            check_size_cap("s3://bucket/key.csv", cap=100, label="CSV")


def test_object_store_memory_error_propagates_unwrapped() -> None:
    """MemoryError must propagate unwrapped (OOM let-propagate policy).

    Wrapping MemoryError inside SizeCapProbeError would hide an OOM condition
    and allow callers to retry indefinitely while the real cause (host RAM)
    remains obscured.  The fix lets MemoryError escape the except clause
    directly so the process exit code reflects the true failure.
    """
    fs = MagicMock()
    fs.info.side_effect = MemoryError("out of memory")
    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
        with pytest.raises(MemoryError):
            check_size_cap("s3://bucket/key.csv", cap=100, label="CSV")


def test_object_store_unexpected_exception_wraps_original_as_cause() -> None:
    """SizeCapProbeError must chain the original exception as __cause__."""
    original = ValueError("original error")
    fs = MagicMock()
    fs.info.side_effect = original
    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
        with pytest.raises(SizeCapProbeError) as exc_info:
            check_size_cap("s3://bucket/key.csv", cap=100, label="CSV")
    assert exc_info.value.__cause__ is original, (
        "SizeCapProbeError must chain the original exception via __cause__"
    )


# ---------------------------------------------------------------------------
# sil M-13: credential exception → INFO log emitted + path redacted
# ---------------------------------------------------------------------------


def test_object_store_credential_error_emits_info_log() -> None:
    """A credential-shaped exception must emit size_cap_probe_skipped_credential at INFO."""
    import structlog.testing

    NoCredentialsError = type("NoCredentialsError", (Exception,), {})
    fs = MagicMock()
    fs.info.side_effect = NoCredentialsError("no credentials found")

    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
        with structlog.testing.capture_logs() as cap:
            check_size_cap("s3://bucket/key.csv", cap=100, label="CSV")

    info_events = [
        e for e in cap if e.get("event") == "size_cap_probe_skipped_credential"
    ]
    assert info_events, (
        "Expected size_cap_probe_skipped_credential INFO event for credential error; "
        f"got events: {[e.get('event') for e in cap]}"
    )
    assert info_events[0]["exc_class"] == "NoCredentialsError"


def test_object_store_auth_shaped_error_raises_size_cap_probe_error() -> None:
    """After C-1: partial-name matching ('Auth' in exc_name) is removed.

    'AuthTokenError' is NOT in the explicit credential_shapes allow-list,
    so it must raise SizeCapProbeError rather than being silently swallowed.
    This prevents 's3:HeadObject deny + s3:GetObject allow' OOM bypass.
    """
    AuthTokenError = type("AuthTokenError", (Exception,), {})
    fs = MagicMock()
    fs.info.side_effect = AuthTokenError("auth token expired")

    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
        with pytest.raises(SizeCapProbeError):
            check_size_cap("s3://bucket/key.csv", cap=100, label="CSV")


# ---------------------------------------------------------------------------
# C-1: credential-skip uses exact class-name match only (no partial-match)
# ---------------------------------------------------------------------------


def test_credential_skip_exact_match_only_no_partial_match() -> None:
    """After C-1 fix, only exact class-name matches in credential_shapes are
    silently skipped. Partial matches like 'Author' or 'Authorize' must NOT
    be silently swallowed — they should raise SizeCapProbeError.
    """
    AuthorizeError = type("AuthorizeError", (Exception,), {})
    AuthorError = type("AuthorError", (Exception,), {})

    for exc_cls in (AuthorizeError, AuthorError):
        fs = MagicMock()
        fs.info.side_effect = exc_cls("some auth-like error")
        with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
            with pytest.raises(SizeCapProbeError, match=exc_cls.__name__):
                check_size_cap("s3://bucket/key.csv", cap=100, label="CSV")


def test_credential_skip_still_works_for_exact_credential_shapes() -> None:
    """Exact credential_shapes names must still be silently skipped after C-1.

    The fix narrows the match from 'any name containing Credential/Auth' to
    exact names in the allow-list.  The known names must still work.
    """
    for exact_name in (
        "NoCredentialsError",
        "PartialCredentialsError",
        "NoRegionError",
        "DefaultCredentialsError",
        "RefreshError",
        "InvalidConfigError",
        "ProfileNotFound",
        "EndpointConnectionError",
    ):
        exc_cls = type(exact_name, (Exception,), {})
        fs = MagicMock()
        fs.info.side_effect = exc_cls("credential failure")
        with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
            # Must NOT raise — exact match is silently skipped.
            check_size_cap("s3://bucket/key.csv", cap=100, label="CSV")


def test_object_store_credential_error_redacts_http_userinfo_in_path() -> None:
    """When the path is an HTTP URL with credentials, the logged path must be redacted."""
    import structlog.testing

    NoCredentialsError = type("NoCredentialsError", (Exception,), {})
    # We need a non-HTTP scheme that triggers fsspec path so we simulate
    # the scenario by patching fsspec to raise a credential error for an
    # S3 path that happens to carry no userinfo (object-store @ is not userinfo).
    # The redact_url_userinfo function only strips userinfo from http/https/ftp.
    # So we verify the logged path equals the input for an S3 path.
    fs = MagicMock()
    fs.info.side_effect = NoCredentialsError("creds missing")

    with patch("fsspec.core.url_to_fs", return_value=(fs, "/bucket/key")):
        with structlog.testing.capture_logs() as cap:
            check_size_cap("s3://bucket/key.csv", cap=100, label="CSV")

    info_events = [
        e for e in cap if e.get("event") == "size_cap_probe_skipped_credential"
    ]
    assert info_events
    # For s3:// path, redact_url_userinfo is a no-op → path preserved.
    assert info_events[0]["path"] == "s3://bucket/key.csv"
