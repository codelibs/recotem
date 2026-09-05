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

import re
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
from recotem.config import ServeConfig
from recotem.recipe.loader import load_recipe
from recotem.serving.app import _try_load_artifact
from recotem.serving.watcher import _classify_artifact_error
from tests.conftest import ACTIVE_KEY_HEX


def _keyring() -> KeyRing:
    return KeyRing(f"active:{ACTIVE_KEY_HEX}")


def _recipe_for(tmp_path: Path, artifact_path: str):
    """A minimal loaded recipe whose ``output.path`` is *artifact_path*."""
    path = tmp_path / "r.yaml"
    path.write_text(
        f"""\
name: sizecap
source:
  type: csv
  path: {tmp_path / "data.csv"}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms:
    - TopPop
output:
  path: {artifact_path}
"""
    )
    return load_recipe(str(path))


def _startup_reason(tmp_path: Path, artifact_path: str, **caps: int) -> str:
    """Run the real startup load path and return the reason label it assigns."""
    cfg = ServeConfig()
    cfg.max_artifact_bytes = caps.get("max_artifact_bytes", 1 << 30)
    cfg.max_payload_bytes = caps.get("max_payload_bytes", 1 << 30)
    _entry, reason = _try_load_artifact(
        _recipe_for(tmp_path, artifact_path), _keyring(), cfg
    )
    return reason


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


# ---------------------------------------------------------------------------
# The startup load path must agree with the watcher.
#
# Every test above drives `_classify_artifact_error`, which only the WATCHER
# calls.  `serving/app.py`'s `_try_load_artifact` -- the path that runs on a
# fresh `recotem serve`, which is where an over-cap model is actually met --
# assigned its reason from a hard-coded string per except-block and never
# consulted the classifier at all.  Measured on one 8,559,920-byte artifact in
# one process: present at startup it reported `parse`, hot-swapped in
# afterwards it reported `size_cap`.
# ---------------------------------------------------------------------------


def test_startup_payload_cap_refusal_is_labelled_size_cap(tmp_path: Path) -> None:
    """Over ``RECOTEM_MAX_PAYLOAD_BYTES`` at startup -> ``size_cap``, not ``parse``."""
    path = _artifact_on_disk(tmp_path)
    reason = _startup_reason(tmp_path, path, max_payload_bytes=16)
    assert reason == "size_cap", (
        f"startup labelled a payload-cap refusal {reason!r}; the watcher calls "
        "the same refusal 'size_cap'. 'parse' reads as a corrupt artifact and "
        "sends the operator to re-train instead of to the cap."
    )


def test_startup_artifact_cap_refusal_is_labelled_size_cap(tmp_path: Path) -> None:
    """Over ``RECOTEM_MAX_ARTIFACT_BYTES`` at startup -> ``size_cap``, not ``read``."""
    path = _artifact_on_disk(tmp_path)
    reason = _startup_reason(
        tmp_path, path, max_artifact_bytes=64, max_payload_bytes=64
    )
    assert reason == "size_cap", (
        f"startup labelled an artifact-cap refusal {reason!r}; the watcher "
        "calls the same refusal 'size_cap'. 'read' reads as a missing or "
        "unreadable file."
    )


def test_startup_missing_artifact_still_labelled_read(tmp_path: Path) -> None:
    """The size-cap branch must not swallow the ordinary read failure."""
    reason = _startup_reason(tmp_path, str(tmp_path / "absent.recotem"))
    assert reason == "read", reason


def test_startup_corrupt_artifact_still_labelled_parse(tmp_path: Path) -> None:
    """Nor the ordinary structural failure."""
    bad = tmp_path / "bad.recotem"
    bad.write_bytes(b"NOTRECOTEM" + b"\x00" * 128)
    reason = _startup_reason(tmp_path, str(bad))
    assert reason == "parse", reason


# The one reason the startup path may own alone.  The watcher classifies an
# ``ArtifactError`` message; "the file is not there yet" never reaches it,
# because a recipe whose artifact is absent simply has nothing to swap in.
_STARTUP_ONLY_REASONS = frozenset({"read"})


def test_startup_reason_vocabulary_matches_the_watchers() -> None:
    """A reason the startup path emits must be one the watcher can emit too.

    Both paths feed one operator-facing series -- ``reason`` is a Prometheus
    label on ``recotem_artifact_load_failures_total`` -- so a label only one of
    them produces splits that series silently.  ``size_cap`` was exactly such a
    label: the watcher could emit it, the startup path could not.

    Read from the source of both rather than from a hand-kept list, and fail
    when either scan matches nothing, so a refactor cannot switch this off.
    """
    root = Path(__file__).resolve().parents[2] / "src" / "recotem" / "serving"
    app_src = (root / "app.py").read_text(encoding="utf-8")
    watcher_src = (root / "watcher.py").read_text(encoding="utf-8")

    startup = set(re.findall(r'return _failed_entry\([^)]*\), "([a-z_]+)"', app_src))
    startup |= set(re.findall(r'_size_cap_or\([^)]*"([a-z_]+)"\)', app_src))
    startup |= set(re.findall(r'\breturn "([a-z_]+)"', app_src))
    watcher = set(re.findall(r'\breturn "([a-z_]+)"', watcher_src))

    assert startup, (
        "no reason label found in serving/app.py -- the regex has stopped "
        "matching and this guard is watching nothing."
    )
    assert watcher, (
        "no reason label found in serving/watcher.py -- the regex has stopped "
        "matching and this guard is watching nothing."
    )
    assert "size_cap" in startup, (
        "the startup load path can no longer report 'size_cap'; an over-cap "
        "model on a fresh deploy is back to being labelled as a damaged file."
    )
    orphans = startup - watcher - _STARTUP_ONLY_REASONS
    assert not orphans, (
        "these startup reason labels have no counterpart in the watcher's "
        f"classifier: {sorted(orphans)}. The same failure must carry the same "
        "label on both load paths; add the branch to "
        "_classify_artifact_error, or list the label in "
        "_STARTUP_ONLY_REASONS with the reason it cannot occur on hot-swap."
    )


def test_documented_reason_enum_covers_every_label_the_code_emits() -> None:
    """``docs/operations.md``'s ``reason`` enum must list what the code emits.

    The enum is the operator's reference for alerting on
    ``recotem_artifact_load_failures_total``. It went stale the moment
    ``size_cap`` was added to the classifier and nothing noticed, because no
    test read the two together. Scanning the whole row and failing when the
    row itself cannot be found keeps that from recurring quietly.
    """
    root = Path(__file__).resolve().parents[2]
    serving = root / "src" / "recotem" / "serving"
    emitted = set(
        re.findall(r'\breturn "([a-z_]+)"', (serving / "watcher.py").read_text("utf-8"))
    )
    emitted |= set(
        re.findall(
            r'return _failed_entry\([^)]*\), "([a-z_]+)"',
            (serving / "app.py").read_text("utf-8"),
        )
    )
    emitted |= set(
        re.findall(
            r'_size_cap_or\([^)]*"([a-z_]+)"\)',
            (serving / "app.py").read_text("utf-8"),
        )
    )
    emitted.add("size_cap")
    emitted.discard("ok")

    doc = (root / "docs" / "operations.md").read_text(encoding="utf-8")
    row = re.search(
        r"recotem_artifact_load_failures_total.*?`reason` ∈ \{(?P<enum>[^}]*)\}",
        doc,
        re.DOTALL,
    )
    assert row, (
        "no `recotem_artifact_load_failures_total` reason enum found in "
        "docs/operations.md -- this guard is watching nothing."
    )
    documented = set(re.findall(r"`([a-z_]+)`", row.group("enum")))
    assert documented, "the reason enum parsed as empty; the regex has drifted."

    missing = sorted(emitted - documented)
    assert not missing, (
        "these reason labels are emitted by the code but absent from the "
        f"documented enum in docs/operations.md: {missing}. An operator "
        "alerting per-reason has no entry for them."
    )
