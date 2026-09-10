"""Integration test: hot-swap when the pointer file's mtime does not advance.

Under ``versioning: append_sha`` (the default) the path the watcher polls is
not the artifact — it is a pointer file whose whole content is
``<stem>.<sha8>.recotem``.  Because ``sha8`` is always 8 hex characters, that
file's *size is a constant*: every pointer for a given stem is byte-for-byte
the same length no matter which artifact it names.

The watcher's change marker for a local path is ``(mtime, size)``, so for
``append_sha`` the size half can never discriminate and change detection
collapses onto mtime alone.  If a pointer rewrite lands without advancing the
mtime, the swap is missed permanently and silently: the fast path in
``_process_stat_result`` returns without reading a byte, ``:recommend`` keeps
answering from the superseded model, and ``/v1/health`` still reports ``ok``.

These tests pin the three arms that matter together:

* the frozen-mtime rewrite must still swap (the defect),
* an ordinary rewrite whose mtime advances must still swap (the control — a
  watcher that reloaded unconditionally would also pass the first arm), and
* an untouched pointer must never trigger a reload (the noise guard — a
  change signal that fires on unchanged bytes would be a worse defect than
  the one being fixed).
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest

from recotem.artifact.signing import KeyRing
from recotem.config import ServeConfig
from recotem.serving.registry import ModelRegistry
from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState
from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact

WATCH_INTERVAL = 0.05
RECIPE_NAME = "pointer_swap"


def _make_serve_config() -> ServeConfig:
    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.watch_interval = WATCH_INTERVAL
    cfg.max_artifact_bytes = 50 * 1024 * 1024
    return cfg


def _write_artifact(path: Path, payload_tag: str) -> str:
    """Write a signed artifact tagged *payload_tag*; return its sha256."""
    import pickle  # noqa: S403  # test fixture: payload built locally

    payload = pickle.dumps({"tag": payload_tag}, protocol=4)  # noqa: S301
    data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={
            "recipe_name": RECIPE_NAME,
            "best_class": "TopPop",
            "trained_at": "2026-01-01T00:00:00Z",
        },
        payload_bytes=payload,
    )
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _write_recipe_yaml(recipes_dir: Path, pointer_path: Path) -> Path:
    content = f"""\
name: {RECIPE_NAME}
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {pointer_path}
  versioning: append_sha
"""
    yaml_path = recipes_dir / f"{RECIPE_NAME}.yaml"
    yaml_path.write_text(content)
    return yaml_path


def _write_pointer_frozen(pointer: Path, contents: str) -> None:
    """Replace *pointer* with *contents* without letting its mtime advance.

    The new contents are staged in a sibling temp file, stamped with the
    *target's existing* mtime, and only then renamed over the pointer.  Because
    the stamp is applied before the rename, the pointer never exists on disk
    with a newer mtime — atomically indistinguishable, to a poller, from a
    write on a filesystem whose mtime granularity swallowed the change.
    """
    before = pointer.stat()
    tmp = pointer.with_name(pointer.name + ".staging")
    tmp.write_text(contents)
    os.utime(tmp, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(tmp, pointer)


def _start_watcher(
    tmp_path: Path,
) -> tuple[ArtifactWatcher, ModelRegistry, _RecipeWatchState, Path, str]:
    """Write artifact A + a pointer to it, start the watcher, wait for the load.

    Returns ``(watcher, registry, state, pointer_path, sha_a)``.  The caller is
    responsible for stopping the watcher.
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()

    sha_a = _write_artifact(artifacts_dir / "model.aaaaaaaa.recotem", "version_a")
    pointer_path = artifacts_dir / "model.recotem"
    pointer_path.write_text("model.aaaaaaaa.recotem\n")

    yaml_path = _write_recipe_yaml(recipes_dir, pointer_path)

    from recotem.recipe.loader import load_recipe

    state = _RecipeWatchState(
        recipe=load_recipe(yaml_path),
        artifact_path=str(pointer_path),
        last_sha256="",
        last_marker=None,
    )
    registry = ModelRegistry()
    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=_make_serve_config(),
        key_ring=KeyRing(f"active:{ACTIVE_KEY_HEX}"),
        initial_states={RECIPE_NAME: state},
    )
    watcher.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        entry = registry.get(RECIPE_NAME)
        if entry is not None and entry.loaded and state.last_sha256 == sha_a:
            break
        time.sleep(0.02)
    else:
        watcher.stop()
        watcher.join(timeout=3.0)
        pytest.fail(
            "fixture failed: the watcher did not resolve the pointer and load "
            f"artifact A within 5s (last_sha256={state.last_sha256!r})"
        )

    return watcher, registry, state, pointer_path, sha_a


def _await_sha(state: _RecipeWatchState, target: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if state.last_sha256 == target:
            return True
        time.sleep(0.02)
    return False


def test_pointer_rewrite_with_frozen_mtime_still_hot_swaps(tmp_path: Path) -> None:
    """A pointer rewritten to a different artifact must swap even if mtime is frozen.

    The pointer's size is identical before and after (both names carry an
    8-hex-character sha), so ``(mtime, size)`` is byte-identical across the
    rewrite.  Only the pointer's *contents* changed.
    """
    watcher, registry, state, pointer_path, sha_a = _start_watcher(tmp_path)
    try:
        sha_b = _write_artifact(
            pointer_path.parent / "model.bbbbbbbb.recotem", "version_b"
        )
        assert sha_b != sha_a, "fixture failed: artifacts A and B must differ"

        before = pointer_path.stat()
        _write_pointer_frozen(pointer_path, "model.bbbbbbbb.recotem\n")
        after = pointer_path.stat()

        # Prove the trigger was actually reproduced.  Without this the test
        # could silently degrade into the control arm below and pass on a
        # watcher that never fixed anything.
        assert (after.st_mtime_ns, after.st_size) == (
            before.st_mtime_ns,
            before.st_size,
        ), (
            "harness failed: the frozen write did not freeze the marker "
            f"({before.st_mtime_ns}, {before.st_size}) -> "
            f"({after.st_mtime_ns}, {after.st_size})"
        )
        assert pointer_path.read_text() == "model.bbbbbbbb.recotem\n"

        swapped = _await_sha(state, sha_b, timeout=3.0)
    finally:
        watcher.stop()
        watcher.join(timeout=3.0)

    assert swapped, (
        "the pointer now names artifact B but the watcher is still serving "
        "artifact A. The change marker is (mtime, size) and an append_sha "
        "pointer's size is a constant, so with the mtime frozen the marker "
        "compares equal and the fast path returns without reading the pointer. "
        f"expected last_sha256={sha_b!r}, got {state.last_sha256!r}"
    )
    entry = registry.get(RECIPE_NAME)
    assert entry is not None and entry.loaded
    assert entry.last_load_error is None


def test_pointer_rewrite_with_advancing_mtime_still_hot_swaps(tmp_path: Path) -> None:
    """Control: the ordinary rewrite path must keep working.

    This is the arm a watcher that reloads unconditionally would also pass, so
    it cannot stand alone — but without it, a change that broke ordinary
    hot-swap would look like a fix.
    """
    watcher, registry, state, pointer_path, sha_a = _start_watcher(tmp_path)
    try:
        sha_b = _write_artifact(
            pointer_path.parent / "model.bbbbbbbb.recotem", "version_b"
        )
        before = pointer_path.stat()
        # Let the clock move so the rewrite genuinely advances the mtime.
        time.sleep(0.02)
        pointer_path.write_text("model.bbbbbbbb.recotem\n")
        after = pointer_path.stat()
        assert after.st_mtime_ns > before.st_mtime_ns, (
            "harness failed: this arm is supposed to advance the mtime, but it "
            f"did not ({before.st_mtime_ns} -> {after.st_mtime_ns})"
        )

        swapped = _await_sha(state, sha_b, timeout=3.0)
    finally:
        watcher.stop()
        watcher.join(timeout=3.0)

    assert swapped, (
        "an ordinary pointer rewrite whose mtime advanced was not picked up; "
        f"expected last_sha256={sha_b!r}, got {state.last_sha256!r}"
    )
    entry = registry.get(RECIPE_NAME)
    assert entry is not None and entry.loaded


def test_untouched_pointer_never_triggers_a_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Noise guard: an unchanged pointer must not cause repeated artifact reads.

    Counted at ``_read_artifact_bytes`` rather than at ``unpickle_payload``,
    because the sha256 short-circuit inside ``_load_recipe`` would hide a
    per-tick re-read behind an unchanged model.
    """
    import recotem.serving.watcher as watcher_module

    reads: list[str] = []
    real_read = watcher_module._read_artifact_bytes

    def _counting_read(path: str, max_bytes: int) -> bytes:
        reads.append(path)
        return real_read(path, max_bytes)

    monkeypatch.setattr(watcher_module, "_read_artifact_bytes", _counting_read)

    watcher, _registry, state, pointer_path, sha_a = _start_watcher(tmp_path)
    try:
        reads_after_load = len(reads)
        # Ten poll intervals with nothing touching the pointer at all.
        time.sleep(WATCH_INTERVAL * 10)
        extra = len(reads) - reads_after_load
    finally:
        watcher.stop()
        watcher.join(timeout=3.0)

    assert state.last_sha256 == sha_a
    assert extra == 0, (
        "the watcher re-read the artifact while the pointer was untouched: "
        f"{extra} extra read(s) over ~10 poll intervals. A change signal that "
        "fires on unchanged bytes is worse than the missed swap it fixes."
    )
