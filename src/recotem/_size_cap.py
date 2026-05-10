"""Shared size-cap helper for local and object-store reads.

Enforces ``RECOTEM_MAX_DOWNLOAD_BYTES`` before any read begins, preventing
OOM from unexpectedly large local, object-store, or sha256-pinned files.
HTTP/HTTPS paths are already capped by the streaming fetch in
``recotem._http_fetch`` and are skipped here.

The check is best-effort: stat failures (missing file, lack of permissions,
object-store connectivity) are swallowed — the real read will surface them.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import structlog

from recotem._http_fetch import NETWORK_SCHEMES

logger = structlog.get_logger(__name__)


class SizeCapExceededError(Exception):
    """Raised when a file's reported size exceeds the configured byte cap.

    The message always names ``RECOTEM_MAX_DOWNLOAD_BYTES`` so operators know
    which env var to adjust.
    """


def _file_uri_to_local_path(path: str) -> Path:
    """Convert a ``file://`` URI to a :class:`~pathlib.Path`.

    Handles all common forms:
    - ``file:///abs/path``  (canonical)
    - ``file://localhost/abs/path``  (RFC 8089 Section 2)
    - ``file://hostname/abs/path``  (treated as absolute path; hostname dropped)

    On Windows, drive letters are preserved by ``urllib.request.url2pathname``.
    """
    parsed = urlparse(path)
    # urllib.request.url2pathname converts the percent-encoded path component
    # to a native OS path string, handling both POSIX and Windows correctly.
    return Path(urllib.request.url2pathname(parsed.path))


def check_size_cap(path: str, cap: int, *, label: str = "file") -> None:
    """Enforce a maximum byte cap on *path* before reading it.

    Parameters
    ----------
    path:
        File path.  May be a bare local path, a ``file://`` URI, or an
        object-store URI (``s3://``, ``gs://``, ``az://``, …).
    cap:
        Maximum allowed size in bytes.
    label:
        Human-readable file kind for the error message (e.g. ``"CSV"``).

    Raises
    ------
    SizeCapExceededError
        If the file's reported size exceeds *cap*.
    """
    scheme = urlparse(path).scheme.lower()

    # HTTP/HTTPS paths are capped during the streaming fetch — skip here.
    if scheme in NETWORK_SCHEMES:
        return

    size: int | None = None

    if scheme in ("", "file"):
        # Local path — cheap stat, no I/O.
        if scheme == "file":
            local = _file_uri_to_local_path(path)
        else:
            local = Path(path)
        try:
            size = local.stat().st_size
        except OSError:
            # File may not exist yet (caught later by the actual read).
            return
    else:
        # Object-store path (s3://, gs://, az://, …) — ask fsspec for size.
        try:
            import fsspec

            fs, fspath = fsspec.core.url_to_fs(path)
            info = fs.info(fspath)
            size = info.get("size")
        except Exception:
            # Size query failed (permissions, connectivity, etc.) — skip cap
            # check here; the real read will surface the error.
            return

    if size is not None and size > cap:
        raise SizeCapExceededError(
            f"{label} file '{path}' is {size:,} bytes which exceeds the "
            f"{cap:,}-byte cap set by RECOTEM_MAX_DOWNLOAD_BYTES. "
            "Raise the limit or downsample the source data."
        )
