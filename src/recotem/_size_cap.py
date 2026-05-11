"""Shared size-cap helper for local and object-store reads.

Enforces ``RECOTEM_MAX_DOWNLOAD_BYTES`` before any read begins, preventing
OOM from unexpectedly large local, object-store, or sha256-pinned files.
HTTP/HTTPS paths are already capped by the streaming fetch in
``recotem._http_fetch`` and are skipped here.

The object-store stat check is best-effort: when the stat cannot be performed
because the file does not yet exist or the caller lacks permission, the cap
check is skipped and the real read will surface the underlying error.  All
other unexpected errors during the stat are wrapped in :class:`SizeCapProbeError`
and re-raised so callers can decide how to handle them (typically mapping to a
domain-specific error such as ``DataSourceError``).
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


class SizeCapProbeError(Exception):
    """Raised when the object-store size probe fails for an unexpected reason.

    This wraps the original exception so callers (e.g. ``DataSourceError``)
    can map it to a domain-specific error.  The probe is best-effort for
    ``FileNotFoundError`` and ``PermissionError`` (which are skipped), but
    any other error indicates a backend problem that the caller should surface.
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
        except (FileNotFoundError, PermissionError):
            # The file may not exist yet or the caller lacks permissions —
            # skip the cap check here; the real read will surface the error.
            return
        except (MemoryError, RecursionError):
            # OOM / stack-exhaustion must propagate unwrapped so the process
            # exit code reflects the true cause (OOM let-propagate policy).
            raise
        except Exception as exc:
            # Any other backend error (connectivity, config, fsspec bugs)
            # indicates a problem the caller must handle.  Log a structured
            # event so operators can diagnose the failure without reading
            # raw tracebacks, then re-raise as SizeCapProbeError.
            safe_path = path.split("@")[0] if "@" in path else path
            logger.warning(
                "size_cap_probe_failed",
                path=safe_path,
                error_class=type(exc).__name__,
            )
            raise SizeCapProbeError(
                f"Object-store size probe for {safe_path!r} failed "
                f"({type(exc).__name__}): {exc}"
            ) from exc

    if size is not None and size > cap:
        raise SizeCapExceededError(
            f"{label} file '{path}' is {size:,} bytes which exceeds the "
            f"{cap:,}-byte cap set by RECOTEM_MAX_DOWNLOAD_BYTES. "
            "Raise the limit or downsample the source data."
        )
