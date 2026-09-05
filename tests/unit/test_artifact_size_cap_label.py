"""The size-cap refusals must classify as ``size_cap``, not ``unexpected``.

A 10M-interaction / 500k-user / 50k-item IALS model trains to a 644 MiB
artifact, over the 512 MiB ``RECOTEM_MAX_PAYLOAD_BYTES`` default.  ``recotem
serve`` refused it correctly, but ``_classify_artifact_error`` had no branch
for the wording, so the refusal fell through to the ``unexpected`` bucket and
emitted ``artifact_error_unclassified`` -- a "this build does not recognise
this failure" WARNING for a documented, configured cap.  The reason label is a
Prometheus label on ``recotem_artifact_load_failures_total``, so an operator
alerting per-reason could not tell an over-cap model from a genuinely unknown
failure.

Each test drives a real raise site rather than a hand-written string, so
rewording a message breaks the test instead of silently un-labelling it.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
import structlog.testing

from recotem.artifact.format import (
    SIZE_CAP_MSG_MARKER,
    ArtifactError,
    parse_header_from_bytes,
)
from recotem.artifact.io import read_artifact, write_artifact
from recotem.artifact.signing import KeyRing
from recotem.serving.watcher import _classify_artifact_error
from tests.conftest import ACTIVE_KEY_HEX


def _keyring() -> KeyRing:
    return KeyRing(f"active:{ACTIVE_KEY_HEX}")


def _artifact_on_disk(tmp_path: Path) -> str:
    return write_artifact(
        payload_obj={"blob": "x" * 8192},
        header_dict={"recipe_name": "sizecap"},
        key_ring=_keyring(),
        fs_path=str(tmp_path / "m.recotem"),
        versioning="always_overwrite",
    )


def test_payload_cap_refusal_is_labelled_size_cap(tmp_path: Path) -> None:
    """``parse_header_from_bytes`` over its payload cap -> ``size_cap``."""
    data = Path(_artifact_on_disk(tmp_path)).read_bytes()
    with pytest.raises(ArtifactError) as excinfo:
        parse_header_from_bytes(data, max_payload_bytes=16)

    assert _classify_artifact_error(str(excinfo.value)) == "size_cap"


def test_artifact_cap_refusal_is_labelled_size_cap(tmp_path: Path) -> None:
    """``read_artifact`` over its total-size cap -> ``size_cap``."""
    path = _artifact_on_disk(tmp_path)
    with pytest.raises(ArtifactError) as excinfo:
        read_artifact(path, _keyring(), max_bytes=64)

    assert _classify_artifact_error(str(excinfo.value)) == "size_cap"


def test_pointer_path_artifact_cap_refusal_is_labelled_size_cap(
    tmp_path: Path,
) -> None:
    """The ``append_sha`` pointer path has its own message; it counts too.

    Its wording is ``artifact '<path>' size N exceeds cap M`` -- it does not
    start with "artifact size", so a prefix match on the direct path's wording
    would miss exactly the mode recotem writes by default.
    """
    write_artifact(
        payload_obj={"blob": "x" * 8192},
        header_dict={"recipe_name": "sizecap"},
        key_ring=_keyring(),
        fs_path=str(tmp_path / "m.recotem"),
        versioning="append_sha",
    )
    with pytest.raises(ArtifactError) as excinfo:
        read_artifact(str(tmp_path / "m.recotem"), _keyring(), max_bytes=64)

    msg = str(excinfo.value)
    assert not msg.lower().startswith("artifact size"), msg
    assert _classify_artifact_error(msg) == "size_cap"


def test_size_cap_refusal_emits_no_unclassified_warning(tmp_path: Path) -> None:
    """No ``artifact_error_unclassified`` for a cap the operator configured."""
    data = Path(_artifact_on_disk(tmp_path)).read_bytes()
    with pytest.raises(ArtifactError) as excinfo:
        parse_header_from_bytes(data, max_payload_bytes=16)

    with structlog.testing.capture_logs() as cap:
        _classify_artifact_error(str(excinfo.value))
    assert not [e for e in cap if e["event"] == "artifact_error_unclassified"], cap


def test_header_len_overflow_still_classifies_as_parse() -> None:
    """ "exceeds maximum" is a corrupt-file signal and must stay ``parse``.

    The two messages are one word apart; collapsing them would hide a corrupt
    artifact behind a sizing label an operator would treat as a config knob.
    """
    from recotem.artifact.format import (
        FORMAT_VERSION,
        MAGIC,
        MAX_HEADER_LEN,
    )

    kid = b"active"
    blob = (
        MAGIC
        + struct.pack("<HH", FORMAT_VERSION, 0)
        + bytes([len(kid)])
        + kid
        + b"\x00" * 32
        + struct.pack("<I", MAX_HEADER_LEN + 1)
    )
    with pytest.raises(ArtifactError) as excinfo:
        parse_header_from_bytes(blob, max_payload_bytes=1 << 30)

    msg = str(excinfo.value)
    assert "exceeds maximum" in msg
    assert SIZE_CAP_MSG_MARKER not in msg
    assert _classify_artifact_error(msg) == "parse"
