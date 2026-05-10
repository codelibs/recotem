"""Integration test: real ArtifactWatcher + real file I/O hot-swap (CRITICAL-4).

Starts with artifact-A in the registry.  Writes artifact-B (different payload)
to disk.  Polls until the watcher picks it up and updates the registry.

The existing test_hot_swap_concurrency.py uses only MagicMock.  This test
exercises the real ArtifactWatcher -> _read_artifact_bytes -> parse_header_from_bytes
-> verify_hmac -> unpickle_payload -> ModelRegistry.replace chain.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from recotem.artifact.signing import KeyRing
from recotem.config import ServeConfig
from recotem.serving.registry import ModelRegistry
from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState
from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact

WATCH_INTERVAL = 0.05


def _make_serve_config() -> ServeConfig:
    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.watch_interval = WATCH_INTERVAL
    cfg.max_artifact_bytes = 50 * 1024 * 1024
    return cfg


def _write_artifact(path: Path, payload_tag: str) -> None:
    """Write a signed artifact with a dict payload containing a tag."""
    import pickle  # noqa: S403  # test fixture: payload built locally

    payload = pickle.dumps({"tag": payload_tag}, protocol=4)  # noqa: S301
    data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={
            "recipe_name": "swap_test",
            "best_class": "TopPop",
            "trained_at": "2026-01-01T00:00:00Z",
        },
        payload_bytes=payload,
    )
    path.write_bytes(data)


def _write_recipe_yaml(recipes_dir: Path, name: str, artifact_path: Path) -> Path:
    content = f"""\
name: {name}
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
  path: {artifact_path}
"""
    yaml_path = recipes_dir / f"{name}.yaml"
    yaml_path.write_text(content)
    return yaml_path


def test_real_watcher_hot_swap_updates_registry(tmp_path: Path) -> None:
    """Real ArtifactWatcher picks up an artifact change via actual file I/O.

    Sequence:
    1. Write artifact-A to disk.
    2. Start the watcher with forced-reload state (sha256="").
    3. Wait for watcher to load artifact-A.
    4. Write artifact-B to disk (different content -> different sha256).
    5. Poll until watcher loads artifact-B.
    6. Verify the registry entry is still loaded with no errors.
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"

    # Write artifact-A
    _write_artifact(artifact_path, "version_a")
    yaml_path = _write_recipe_yaml(recipes_dir, "swap_test", artifact_path)

    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    registry = ModelRegistry()
    cfg = _make_serve_config()

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)

    # Start with empty sha256 so watcher loads on first tick
    initial_states = {
        "swap_test": _RecipeWatchState(
            recipe=recipe,
            artifact_path=str(artifact_path),
            last_sha256="",
            last_marker=None,
        ),
    }

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )
    watcher.start()

    # Wait for artifact-A to load
    deadline = time.monotonic() + 3.0
    loaded_a = False
    while time.monotonic() < deadline:
        entry = registry.get("swap_test")
        if entry is not None and entry.loaded and entry.last_load_error is None:
            loaded_a = True
            break
        time.sleep(0.05)

    assert loaded_a, "Watcher must load artifact-A from real file within 3s"

    # Capture sha256 of artifact-A
    sha_a = initial_states["swap_test"].last_sha256

    # Now write artifact-B with different payload tag
    _write_artifact(artifact_path, "version_b")

    # Poll until the watcher's internal sha256 changes (= artifact-B loaded)
    deadline = time.monotonic() + 3.0
    loaded_b = False
    while time.monotonic() < deadline:
        current_sha = initial_states["swap_test"].last_sha256
        if current_sha != sha_a and current_sha != "":
            # sha changed -> watcher re-loaded the new artifact
            entry = registry.get("swap_test")
            if entry is not None and entry.loaded:
                loaded_b = True
                break
        time.sleep(0.05)

    watcher.stop()
    watcher.join(timeout=3.0)

    assert loaded_b, (
        "Watcher must hot-swap to artifact-B when the file changes; "
        f"sha_a={sha_a!r}, final_sha={initial_states['swap_test'].last_sha256!r}"
    )


def test_real_watcher_hot_swap_concurrent_reads_safe(tmp_path: Path) -> None:
    """In-flight reads remain safe across an artifact swap.

    5 reader threads read from the registry while the watcher triggers a swap.
    All accesses must succeed without exceptions.
    """
    import threading

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"

    _write_artifact(artifact_path, "concurrent_test")
    yaml_path = _write_recipe_yaml(recipes_dir, "concurrent_recipe", artifact_path)

    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    registry = ModelRegistry()
    cfg = _make_serve_config()

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)

    initial_states = {
        "concurrent_recipe": _RecipeWatchState(
            recipe=recipe,
            artifact_path=str(artifact_path),
            last_sha256="",
            last_marker=None,
        ),
    }

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )
    watcher.start()

    errors: list[str] = []
    reads_ok: list[int] = [0]
    stop_flag = threading.Event()

    def _reader() -> None:
        while not stop_flag.is_set():
            try:
                entry = registry.get("concurrent_recipe")
                if entry is not None:
                    reads_ok[0] += 1
            except Exception as exc:
                errors.append(str(exc))
            time.sleep(0.002)

    readers = [threading.Thread(target=_reader) for _ in range(5)]
    for t in readers:
        t.start()

    # Let watcher + readers run, then write a new artifact
    time.sleep(0.3)
    _write_artifact(artifact_path, "concurrent_test_v2")
    time.sleep(0.3)

    stop_flag.set()
    for t in readers:
        t.join(timeout=2.0)
    watcher.stop()
    watcher.join(timeout=3.0)

    assert not errors, f"Concurrent read errors during hot-swap: {errors}"
    assert reads_ok[0] > 0, "Readers must have successfully accessed registry entries"


def test_watcher_does_not_reload_when_sha256_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Watcher skips _build_entry when file content (sha256) is unchanged.

    Sequence:
    1. Write an artifact and start the watcher.
    2. Wait for initial load.
    3. Touch the file's mtime WITHOUT changing bytes.
    4. Wait one poll cycle.
    5. Assert unpickle_payload is NOT called a second time
       (the sha256 short-circuit skips build_entry).
    """
    import time

    import pytest

    import recotem.artifact.signing as signing_module

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "no_reload.recotem"

    _write_artifact(artifact_path, "stable_version")
    yaml_path = _write_recipe_yaml(recipes_dir, "no_reload_recipe", artifact_path)

    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    registry = ModelRegistry()
    cfg = _make_serve_config()

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)

    initial_states = {
        "no_reload_recipe": _RecipeWatchState(
            recipe=recipe,
            artifact_path=str(artifact_path),
            last_sha256="",
            last_marker=None,
        ),
    }

    unpickle_call_count: list[int] = [0]
    original_unpickle = signing_module.unpickle_payload

    def _counting_unpickle(payload_bytes: bytes):
        unpickle_call_count[0] += 1
        return original_unpickle(payload_bytes)

    monkeypatch.setattr(signing_module, "unpickle_payload", _counting_unpickle)

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )
    watcher.start()

    # Wait for initial load (unpickle_call_count must reach at least 1).
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        entry = registry.get("no_reload_recipe")
        if entry is not None and entry.loaded:
            break
        time.sleep(0.05)
    else:
        watcher.stop()
        watcher.join(timeout=3.0)
        pytest.fail("Watcher did not load initial artifact within 3s")

    count_after_initial_load = unpickle_call_count[0]
    assert count_after_initial_load >= 1, "Must have unpickled at least once"

    # Touch the mtime without changing bytes.
    current_bytes = artifact_path.read_bytes()
    artifact_path.write_bytes(current_bytes)

    # Wait two full poll cycles for the watcher to process the mtime change.
    time.sleep(WATCH_INTERVAL * 4)

    watcher.stop()
    watcher.join(timeout=3.0)

    count_after_touch = unpickle_call_count[0]
    assert count_after_touch == count_after_initial_load, (
        f"Watcher must NOT reload when sha256 is unchanged (touch only). "
        f"unpickle_payload called {count_after_touch - count_after_initial_load} "
        f"extra times after mtime touch."
    )
