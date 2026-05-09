"""fsspec-based read and write for .recotem artifact files.

Read-once protocol
------------------
``read_artifact`` reads the complete artifact bytes into memory in a single
``fs.open().read()`` call, then parses the in-memory buffer.  This eliminates
the stat-then-read TOCTOU race (a write could land between a stat and a read)
and the file-still-being-written hazard.

Versioning modes
----------------
``always_overwrite``
    Write to a temp path in the same directory, fsync, then ``os.replace``
    (POSIX-atomic on local FS).

``append_sha``
    Write to ``<base>.<sha8>.recotem``, then write (or overwrite) a small
    pointer file at ``<output_path>`` containing the sha-suffixed name.
    For local FS both writes use atomic rename.  For object stores the
    sha-suffixed object is written first; then the pointer object is
    overwritten (last-write-wins, which is safe because pointer content is
    deterministic for a given payload).

    When ``read_artifact`` opens a path whose content matches the pointer
    regex ``^[A-Za-z0-9_.-]+\\.recotem$`` it resolves through the pointer
    before parsing.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import re
import tempfile
from typing import Any, Literal

import fsspec
import structlog

from recotem.artifact.format import (
    DEFAULT_MAX_PAYLOAD_BYTES,
    ArtifactError,
    ArtifactHeader,
    build_artifact_bytes,
    parse_header_from_bytes,
)
from recotem.artifact.signing import KeyRing, compute_hmac, verify_hmac

logger = structlog.get_logger(__name__)

# Matches the pointer file contents written in append_sha mode.
# A pointer file contains a single line like "news_articles.a1b2c3d4.recotem".
_POINTER_RE = re.compile(r"^[A-Za-z0-9_.-]+\.recotem\s*$")

VersioningMode = Literal["always_overwrite", "append_sha"]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_artifact(
    payload_obj: Any,
    header_dict: dict[str, Any],
    key_ring: KeyRing,
    fs_path: str,
    *,
    versioning: VersioningMode = "append_sha",
) -> str:
    """Serialize *payload_obj*, sign, and write to *fs_path*.

    Parameters
    ----------
    payload_obj:
        The Python object to serialize (via ``pickle.dumps``).
    header_dict:
        Metadata dict; serialized as JSON and embedded in the artifact header.
    key_ring:
        KeyRing to use for signing.  The active key (``key_ring.active_kid``)
        is used.
    fs_path:
        Destination path.  May be a local path, ``s3://``, or ``gs://``.
    versioning:
        ``always_overwrite``: overwrite *fs_path* in-place (atomic on local FS).
        ``append_sha``: write to ``<fs_path>.<sha8>.recotem`` and update a
        pointer file at *fs_path*.

    Returns
    -------
    str
        The final artifact path (may differ from *fs_path* in ``append_sha``
        mode).
    """
    kid = key_ring.active_kid
    key = key_ring.get(kid)
    assert key is not None  # active_kid is always present

    # 1. Serialize payload
    payload: bytes = pickle.dumps(payload_obj, protocol=pickle.HIGHEST_PROTOCOL)

    # 2. Encode header
    header_json: bytes = json.dumps(header_dict, separators=(",", ":")).encode("utf-8")

    # 3. Compute HMAC
    kid_bytes = kid.encode("utf-8")
    digest = compute_hmac(key, kid_bytes, header_json, payload)

    # 4. Assemble artifact bytes
    artifact_bytes = build_artifact_bytes(kid, digest, header_json, payload)

    # 5. Determine filesystem and write strategy
    fs, resolved_path = fsspec.core.url_to_fs(fs_path)
    is_local = _is_local_fs(fs)

    if versioning == "always_overwrite":
        final_path = resolved_path
        _write_atomic(fs, resolved_path, artifact_bytes, is_local)
        logger.info(
            "artifact_written",
            versioning="always_overwrite",
            artifact=resolved_path,
            kid=kid,
        )
        return final_path

    # append_sha mode
    sha8 = hashlib.sha256(artifact_bytes).hexdigest()[:8]
    # Strip trailing ".recotem" if present so we can re-append cleanly
    base = resolved_path
    if base.endswith(".recotem"):
        stem = base[: -len(".recotem")]
    else:
        stem = base
    sha_path = f"{stem}.{sha8}.recotem"

    # Write sha-suffixed artifact first, then the pointer file
    _write_atomic(fs, sha_path, artifact_bytes, is_local)
    sha_basename = os.path.basename(sha_path)
    pointer_bytes = (sha_basename + "\n").encode("utf-8")
    _write_atomic(fs, resolved_path, pointer_bytes, is_local)

    logger.info(
        "artifact_written",
        versioning="append_sha",
        artifact=sha_path,
        pointer=resolved_path,
        kid=kid,
    )
    return sha_path


def _is_local_fs(fs: fsspec.AbstractFileSystem) -> bool:
    """Return True when *fs* is a local filesystem."""
    return type(fs).__name__ in {"LocalFileSystem", "AbstractBufferedFile"}


def _write_atomic(
    fs: fsspec.AbstractFileSystem,
    dest: str,
    data: bytes,
    is_local: bool,
) -> None:
    """Write *data* to *dest* atomically where supported.

    For local FS: write to a sibling temp file, fsync, then ``os.replace``.
    For object stores: write directly (object stores offer strong put semantics;
    last-write-wins on the pointer file is acceptable per spec).
    """
    if is_local:
        dest_dir = os.path.dirname(dest) or "."
        os.makedirs(dest_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dest_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass  # not all FS support fsync; best-effort
            os.replace(tmp_path, dest)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    else:
        # Object store — write directly
        with fs.open(dest, "wb") as fh:
            fh.write(data)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_artifact(
    fs_path: str,
    key_ring: KeyRing,
    *,
    max_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> tuple[ArtifactHeader, bytes]:
    """Read, validate, and HMAC-verify a .recotem artifact.

    Implements the read-once protocol: all bytes are read in a single call
    before any parsing begins.

    Parameters
    ----------
    fs_path:
        Path to the artifact (or pointer file).  Pointer files are resolved
        automatically when their content matches ``_POINTER_RE``.
    key_ring:
        KeyRing used for HMAC verification.
    max_bytes:
        Total file size cap (including header).  Files larger than this are
        rejected *after* reading (the full read is the single-call guarantee).
        Default 2 GiB.

    Returns
    -------
    tuple[ArtifactHeader, bytes]
        The parsed header and the raw payload bytes.  The payload has been
        HMAC-verified but **not** deserialized.  Call
        ``artifact.signing.unpickle_payload(payload_bytes)`` to deserialize.

    Raises
    ------
    ArtifactError
        On any structural, HMAC, or cap violation.
    """
    fs, resolved_path = fsspec.core.url_to_fs(fs_path)

    # Read all bytes once
    try:
        with fs.open(resolved_path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError as exc:
        raise ArtifactError(f"artifact not found: {fs_path}") from exc
    except OSError as exc:
        raise ArtifactError(f"failed to read artifact {fs_path}: {exc}") from exc

    # Resolve pointer if applicable
    resolved_data, resolved_path = resolve_artifact_pointer(
        raw, resolved_path, fs, max_bytes
    )

    # Enforce total size cap
    if len(resolved_data) > max_bytes:
        raise ArtifactError(
            f"artifact size {len(resolved_data)} exceeds cap {max_bytes}; "
            "refusing to load"
        )

    # Parse fixed-layout header
    header = parse_header_from_bytes(resolved_data, max_payload_bytes=max_bytes)

    # Extract components needed for HMAC verification
    kid_bytes = header.kid.encode("utf-8")
    header_json = header.header_data
    payload = resolved_data[header.payload_offset :]

    # HMAC verify — raises ArtifactError on failure
    verify_hmac(
        key_ring=key_ring,
        kid=header.kid,
        kid_bytes=kid_bytes,
        header_json=header_json,
        payload=payload,
        stored_digest=header.hmac_digest,
    )

    logger.info("artifact_loaded", kid=header.kid, path=resolved_path)
    return header, payload


def resolve_artifact_pointer(
    raw: bytes,
    path: str,
    fs: fsspec.AbstractFileSystem,
    max_bytes: int,
) -> tuple[bytes, str]:
    """If *raw* looks like a pointer file, resolve it and return the real bytes.

    A pointer file is a small text file (max 512 B) whose entire content
    matches ``_POINTER_RE`` (e.g. ``news_articles.a1b2c3d4.recotem\\n``).
    Used by both ``read_artifact`` and the serving layer's hot-swap reader
    so that ``versioning: append_sha`` artifacts (the documented default)
    are transparently resolvable from the recipe's ``output.path``.

    Returns ``(raw, path)`` unchanged when *raw* is not a pointer.
    """
    # Pointer files are at most a few hundred bytes; skip resolution for
    # anything that might be a real artifact.
    if len(raw) > 512:
        return raw, path

    try:
        text = raw.decode("ascii")
    except (UnicodeDecodeError, ValueError):
        return raw, path

    if not _POINTER_RE.match(text):
        return raw, path

    # Looks like a pointer — resolve relative to the directory of the pointer
    target_name = text.strip()
    parent = os.path.dirname(path)
    target_path = os.path.join(parent, target_name) if parent else target_name

    logger.debug("artifact_pointer_resolved", pointer=path, target=target_path)

    try:
        with fs.open(target_path, "rb") as fh:
            artifact_raw = fh.read()
    except FileNotFoundError as exc:
        raise ArtifactError(
            f"pointer {path!r} references missing artifact {target_path!r}"
        ) from exc
    except OSError as exc:
        raise ArtifactError(
            f"failed to read artifact via pointer {path!r}: {exc}"
        ) from exc

    if len(artifact_raw) > max_bytes:
        raise ArtifactError(
            f"artifact {target_path!r} size {len(artifact_raw)} "
            f"exceeds cap {max_bytes}; refusing to load"
        )

    return artifact_raw, target_path
