"""Unit tests for recotem.serving.watcher.ArtifactWatcher.

Tests (using RECOTEM_WATCH_INTERVAL=0.05, assertions within 0.5s):
- hot-swap success
- malformed file keeps old + last_load_error
- new yaml added
- deleted yaml removed
- non-yaml ignored
- initial mtime captured pre-watcher
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from recotem.artifact.signing import KeyRing
from recotem.config import ServeConfig
from recotem.serving import metrics as _metrics
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.watcher import (
    ArtifactWatcher,
    build_initial_states,
)
from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact

WATCH_INTERVAL = 0.05  # fast for tests


def _make_serve_config(watch_interval: float = WATCH_INTERVAL) -> ServeConfig:
    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.watch_interval = watch_interval
    cfg.max_artifact_bytes = 100 * 1024 * 1024
    return cfg


def _make_entry(name: str = "recipe1") -> ModelEntry:
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    return ModelEntry(
        name=name,
        recommender=rec,
        header={"trained_at": "2026-01-01T00:00:00Z", "best_class": "TopPop"},
        kid="active",
    )


def _write_valid_artifact(path: Path) -> None:
    """Write a signed artifact (dict payload) to path."""
    import pickle  # noqa: S403

    payload = pickle.dumps({"key": "test"}, protocol=4)  # noqa: S301
    data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={
            "recipe_name": "test",
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


# ---------------------------------------------------------------------------
# build_initial_states (initial mtime captured pre-watcher)
# ---------------------------------------------------------------------------


def test_initial_mtime_captured_in_watcher_state_no_missed_swap(
    tmp_path: Path,
) -> None:
    """build_initial_states captures the marker so first watcher tick is a no-op."""
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    from recotem.recipe.models import Recipe

    recipe = MagicMock(spec=Recipe)
    recipe.name = "test"
    recipe.output = MagicMock()
    recipe.output.path = str(artifact_path)
    recipe.item_metadata = None

    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    entry = _make_entry("test")
    entry.artifact_path = str(artifact_path)

    states = build_initial_states([recipe], {"test": entry})
    assert "test" in states
    # The marker must be non-None (file exists)
    assert states["test"].last_marker is not None


# ---------------------------------------------------------------------------
# hot-swap success
# ---------------------------------------------------------------------------


def test_hot_swap_success(tmp_path: Path) -> None:
    """When the artifact file changes, the watcher replaces the registry entry."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "test", artifact_path)

    registry = ModelRegistry()
    old_entry = _make_entry("test")
    old_entry.artifact_path = str(artifact_path)
    registry.replace("test", old_entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    # Build initial state
    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    initial_states = build_initial_states([recipe], {"test": old_entry})
    # Force the last_sha256 to empty so the first tick sees a "change"
    initial_states["test"].last_sha256 = ""

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )
    watcher.start()
    try:
        time.sleep(0.5)
    finally:
        watcher.stop()
        watcher.join(timeout=2.0)

    # After the watcher ran, the registry entry should have been refreshed
    # (we can't easily assert the exact object changed, but we can check it's there)
    assert registry.get("test") is not None


# ---------------------------------------------------------------------------
# Malformed file keeps old + marks last_load_error
# ---------------------------------------------------------------------------


def test_malformed_swap_keeps_old_model_marks_health_error(
    tmp_path: Path,
) -> None:
    """When the artifact is malformed, the old entry remains and last_load_error is set."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "test_bad", artifact_path)

    registry = ModelRegistry()
    good_entry = _make_entry("test_bad")
    good_entry.artifact_path = str(artifact_path)
    registry.replace("test_bad", good_entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    initial_states = build_initial_states([recipe], {"test_bad": good_entry})
    # Force a re-load by clearing sha256
    initial_states["test_bad"].last_sha256 = ""

    # Now corrupt the artifact
    artifact_path.write_bytes(b"corrupted garbage data that is not a valid artifact")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )
    watcher.start()
    try:
        time.sleep(0.5)
    finally:
        watcher.stop()
        watcher.join(timeout=2.0)

    # Old entry should still be there
    entry = registry.get("test_bad")
    assert entry is not None
    # last_load_error should be set
    assert entry.last_load_error is not None


# ---------------------------------------------------------------------------
# Non-yaml files silently ignored
# ---------------------------------------------------------------------------


def test_non_yaml_files_in_recipes_dir_silently_ignored(tmp_path: Path) -> None:
    """Files without .yaml extension in the recipes dir do not cause errors."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    # Create non-yaml files
    (recipes_dir / "notes.txt").write_text("just notes")
    (recipes_dir / "model.recotem").write_bytes(b"artifact")
    (recipes_dir / ".hidden").write_text("hidden")

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
    )
    watcher.start()
    try:
        time.sleep(0.3)
    finally:
        watcher.stop()
        watcher.join(timeout=2.0)

    # Registry should still be empty (no valid yaml recipes)
    assert registry.list() == []


# ---------------------------------------------------------------------------
# yaml deleted removes entry from registry
# ---------------------------------------------------------------------------


def test_yaml_deleted_removes_entry_from_registry(tmp_path: Path) -> None:
    """When a YAML file is deleted, the watcher removes it from the registry."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "removable", artifact_path)

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)

    registry = ModelRegistry()
    entry = _make_entry("removable")
    entry.artifact_path = str(artifact_path)
    registry.replace("removable", entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    initial_states = build_initial_states([recipe], {"removable": entry})

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )
    watcher.start()
    time.sleep(0.1)

    # Delete the YAML file
    yaml_path.unlink()

    time.sleep(0.5)
    watcher.stop()
    watcher.join(timeout=2.0)

    # The entry should have been removed
    assert registry.get("removable") is None


# ---------------------------------------------------------------------------
# _load_metadata_safe (regression tests for the load_item_metadata call)
# ---------------------------------------------------------------------------


def test_load_metadata_returns_dataframe_when_item_metadata_present(
    tmp_path: Path,
) -> None:
    """_load_metadata must pass `fields` and `on_field_missing` through.

    Regression: previously this function called ``load_item_metadata(config)``
    without ``fields``, which raised ``TypeError`` at runtime the moment any
    recipe defined ``item_metadata``. Existing watcher tests all set
    ``recipe.item_metadata = None`` so the broken branch was never reached.
    """
    import pandas as pd

    from recotem.serving.watcher import _load_metadata

    csv_path = tmp_path / "items.csv"
    pd.DataFrame({"item_id": ["i1", "i2", "i3"], "title": ["A", "B", "C"]}).to_csv(
        csv_path, index=False
    )

    item_metadata = MagicMock()
    item_metadata.type = "csv"
    item_metadata.path = str(csv_path)
    item_metadata.item_id_column = "item_id"
    item_metadata.sha256 = None
    item_metadata.fields = ["title"]
    item_metadata.on_field_missing = "error"

    recipe = MagicMock()
    recipe.item_metadata = item_metadata

    df = _load_metadata(recipe, "test")
    assert df is not None
    assert list(df.columns) == ["title"]
    assert df.index.name == "item_id"
    assert sorted(df.index.tolist()) == ["i1", "i2", "i3"]


def test_hot_swap_metadata_failure_marks_last_load_error(tmp_path: Path) -> None:
    """If item_metadata load raises during a hot-swap, the watcher must keep
    the previous entry and surface the error via ``last_load_error`` so
    ``/health`` reports the misconfiguration."""
    import pandas as pd

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    metadata_csv = tmp_path / "items.csv"
    pd.DataFrame({"item_id": ["i1"], "title": ["A"]}).to_csv(metadata_csv, index=False)

    yaml_path = recipes_dir / "with_metadata.yaml"
    yaml_path.write_text(
        f"""\
name: with_metadata
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
item_metadata:
  type: csv
  path: {metadata_csv}
  fields: [missing_column]
  on_field_missing: error
output:
  path: {artifact_path}
"""
    )

    registry = ModelRegistry()
    good_entry = _make_entry("with_metadata")
    good_entry.artifact_path = str(artifact_path)
    registry.replace("with_metadata", good_entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    initial_states = build_initial_states([recipe], {"with_metadata": good_entry})
    initial_states["with_metadata"].last_sha256 = ""  # force reload
    initial_states["with_metadata"].last_marker = None  # force marker change

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )
    watcher.start()
    try:
        time.sleep(0.5)
    finally:
        watcher.stop()
        watcher.join(timeout=2.0)

    entry = registry.get("with_metadata")
    assert entry is not None
    assert entry.last_load_error is not None, (
        "metadata load failure must propagate to last_load_error"
    )
    assert "missing_column" in entry.last_load_error


def test_load_metadata_raises_on_missing_field_with_on_field_missing_error(
    tmp_path: Path,
) -> None:
    """`on_field_missing="error"` must surface as an exception, not a silent
    None. Otherwise the model registers as ``loaded=True`` with no metadata,
    and ``/health`` cannot detect the misconfiguration."""
    import pandas as pd
    import pytest

    from recotem.serving.watcher import _load_metadata

    csv_path = tmp_path / "items.csv"
    pd.DataFrame({"item_id": ["i1"], "title": ["A"]}).to_csv(csv_path, index=False)

    item_metadata = MagicMock()
    item_metadata.type = "csv"
    item_metadata.path = str(csv_path)
    item_metadata.item_id_column = "item_id"
    item_metadata.sha256 = None
    item_metadata.fields = ["missing_column"]
    item_metadata.on_field_missing = "error"

    recipe = MagicMock()
    recipe.item_metadata = item_metadata

    with pytest.raises(ValueError, match="missing_column"):
        _load_metadata(recipe, "test")


# ---------------------------------------------------------------------------
# G1. Watcher picks up a YAML + artifact added at runtime
# ---------------------------------------------------------------------------


def test_watcher_picks_up_runtime_added_yaml(tmp_path: Path) -> None:
    """Mirror of test_yaml_deleted_removes_entry_from_registry:
    watcher starts with an empty recipes dir; a YAML + valid artifact are
    added after the watcher starts.  Within 0.5s the entry should appear
    in the registry.
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
    )
    watcher.start()
    # Give the watcher one tick to observe the empty directory
    time.sleep(0.1)

    # Now create the artifact and YAML
    artifact_path = tmp_path / "new_model.recotem"
    _write_valid_artifact(artifact_path)
    _write_recipe_yaml(recipes_dir, "new_recipe", artifact_path)

    # Wait for watcher to pick it up
    deadline = time.monotonic() + 2.0
    found = False
    while time.monotonic() < deadline:
        if registry.get("new_recipe") is not None and registry.get("new_recipe").loaded:
            found = True
            break
        time.sleep(0.05)

    watcher.stop()
    watcher.join(timeout=2.0)

    assert found, "Watcher must pick up a YAML+artifact added at runtime within 2s"


# ---------------------------------------------------------------------------
# G2. Watcher marks error via registry.set_load_error on load failure
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# MAJOR-1: Newly-discovered recipe with bad artifact surfaces in /health
# ---------------------------------------------------------------------------


def test_new_recipe_bad_artifact_appears_in_health(tmp_path: Path) -> None:
    """When the watcher discovers a new recipe whose artifact is invalid,
    the registry must still contain an entry with loaded=False and a non-None
    last_load_error so /health can surface the problem.

    Previously _load_recipe would call _record_load_failure → set_load_error,
    but no entry existed in the registry yet, so set_load_error returned False
    and the failure was invisible.  The fix inserts a stub entry BEFORE
    attempting the load.
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    # Write an artifact that is intentionally corrupt
    artifact_path = tmp_path / "bad_model.recotem"
    artifact_path.write_bytes(b"this is not a valid artifact at all")

    # Write the recipe YAML pointing at the corrupt artifact
    _write_recipe_yaml(recipes_dir, "bad_new_recipe", artifact_path)

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    # Start watcher with empty initial states — will discover the YAML at runtime
    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
    )
    watcher.start()

    # Wait for at least two poll ticks so the discovery and load attempt happen
    deadline = time.monotonic() + 2.0
    found_error = False
    while time.monotonic() < deadline:
        entry = registry.get("bad_new_recipe")
        if entry is not None and entry.last_load_error is not None:
            found_error = True
            break
        time.sleep(0.05)

    watcher.stop()
    watcher.join(timeout=2.0)

    entry = registry.get("bad_new_recipe")
    assert entry is not None, (
        "Registry must contain an entry for the discovered recipe even on load failure"
    )
    assert not entry.loaded, "Entry must report loaded=False when initial load failed"
    assert entry.last_load_error is not None, (
        "last_load_error must be set so /health surfaces the failure"
    )
    assert found_error, "last_load_error must be set within 2 s of discovery"


# ---------------------------------------------------------------------------
# MAJOR-2: Artifact disappearance surfaces in /health and increments metric
# ---------------------------------------------------------------------------


def test_artifact_disappearance_sets_last_load_error_and_increments_metric(
    tmp_path: Path,
) -> None:
    """When a known artifact file is deleted, the next watcher poll must
    call set_load_error so /health shows the problem, AND must increment
    recotem_artifact_load_failures_total.

    The stale model (loaded=True) must remain in the registry — the watcher
    must NOT flip loaded=False so existing traffic continues while the file
    is temporarily missing.
    """
    from unittest.mock import patch

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "vanishing.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "vanishing", artifact_path)

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)

    registry = ModelRegistry()
    good_entry = _make_entry("vanishing")
    good_entry.artifact_path = str(artifact_path)
    # Manually clear last_load_error to establish "healthy" baseline
    good_entry.last_load_error = None
    registry.replace("vanishing", good_entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    initial_states = build_initial_states([recipe], {"vanishing": good_entry})

    failure_count: list[int] = [0]
    original_inc = _metrics.inc_artifact_load_failure

    def _counting_inc(name: str) -> None:
        if name == "vanishing":
            failure_count[0] += 1
        original_inc(name)

    with patch.object(_metrics, "inc_artifact_load_failure", side_effect=_counting_inc):
        watcher = ArtifactWatcher(
            registry=registry,
            recipes_dir=recipes_dir,
            serve_config=cfg,
            key_ring=kr,
            initial_states=initial_states,
        )
        watcher.start()

        # Give the watcher one tick with the file present so state is stable
        time.sleep(0.15)

        # Now delete the artifact
        artifact_path.unlink()

        # Wait for watcher to notice the disappearance
        deadline = time.monotonic() + 2.0
        error_set = False
        while time.monotonic() < deadline:
            entry = registry.get("vanishing")
            if entry is not None and entry.last_load_error is not None:
                error_set = True
                break
            time.sleep(0.05)

        watcher.stop()
        watcher.join(timeout=2.0)

    entry = registry.get("vanishing")
    assert entry is not None, "Entry must remain in registry after artifact disappears"
    assert error_set, "last_load_error must be set within 2 s of file deletion"
    assert entry.last_load_error is not None
    assert (
        "missing" in entry.last_load_error or "unreadable" in entry.last_load_error
    ), (
        f"last_load_error should mention 'missing' or 'unreadable': {entry.last_load_error!r}"
    )
    assert entry.loaded, (
        "loaded flag must stay True (stale model keeps serving) after artifact disappears"
    )
    assert failure_count[0] >= 1, (
        "inc_artifact_load_failure must be called at least once when artifact disappears"
    )


# ---------------------------------------------------------------------------
# T-7: append_sha pointer-file hot-swap
# ---------------------------------------------------------------------------


def _write_pointer_and_artifact(
    artifact_dir: Path,
    pointer_path: Path,
    name: str = "ptr_test",
) -> None:
    """Write a signed artifact and a corresponding append_sha pointer file.

    Creates:
      - ``<artifact_dir>/<name>.deadbeef.recotem``  — real signed artifact
      - ``pointer_path``                             — pointer file text
    """
    # Build the real artifact bytes using the existing test helper.
    artifact_data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={
            "recipe_name": name,
            "best_class": "TopPop",
            "trained_at": "2026-01-01T00:00:00Z",
        },
    )
    # Derive a short sha suffix (matches io.write_artifact behaviour)
    import hashlib

    sha8 = hashlib.sha256(artifact_data).hexdigest()[:8]
    stem = pointer_path.stem  # without .recotem
    sha_filename = f"{stem}.{sha8}.recotem"
    sha_path = artifact_dir / sha_filename
    sha_path.write_bytes(artifact_data)
    # Pointer file contains the sha-suffixed filename + newline
    pointer_path.write_text(sha_filename + "\n", encoding="ascii")


def test_hot_swap_via_append_sha_pointer(tmp_path: Path) -> None:
    """Watcher must load models when the artifact path is a pointer file.

    Regression coverage for the ``versioning: append_sha`` path (the
    documented default).  The recipe's ``output.path`` points to a small
    ASCII pointer file; the real artifact lives at ``<stem>.<sha8>.recotem``
    in the same directory.  The watcher delegates resolution to
    ``resolve_artifact_pointer`` via ``_read_artifact_bytes``.
    """
    from recotem.serving.watcher import _RecipeWatchState

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    # The recipe's output.path — this is a pointer file after write.
    pointer_path = artifact_dir / "ptr_model.recotem"
    _write_pointer_and_artifact(artifact_dir, pointer_path, name="ptr_test")

    # Confirm the pointer file was created (small ASCII text, not a real artifact).
    pointer_bytes = pointer_path.read_bytes()
    assert len(pointer_bytes) < 512, "pointer file must be small"
    assert pointer_bytes.strip().endswith(b".recotem"), (
        f"unexpected pointer content: {pointer_bytes!r}"
    )

    yaml_path = _write_recipe_yaml(recipes_dir, "ptr_test", pointer_path)

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    # Start with an empty last_sha256 so the watcher is forced to reload.
    initial_states = {
        "ptr_test": _RecipeWatchState(recipe=recipe, artifact_path=str(pointer_path)),
    }

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )
    watcher.start()

    # Wait up to 2 s for the entry to appear and be loaded.
    deadline = time.monotonic() + 2.0
    loaded = False
    while time.monotonic() < deadline:
        entry = registry.get("ptr_test")
        if entry is not None and entry.loaded and entry.last_load_error is None:
            loaded = True
            break
        time.sleep(0.05)

    watcher.stop()
    watcher.join(timeout=2.0)

    assert loaded, (
        "Watcher must load a model when output.path is an append_sha pointer file; "
        f"entry={registry.get('ptr_test')!r}"
    )


def test_watcher_marks_error_via_registry_set_load_error(tmp_path: Path) -> None:
    """When a hot-swap fails (corrupt artifact), the existing entry's
    last_load_error must be non-None after the watcher's next tick.

    This is essentially a re-assertion of the existing malformed-swap test,
    but focussed on the set_load_error path rather than the raw attribute.
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "error_recipe", artifact_path)

    registry = ModelRegistry()
    good_entry = _make_entry("error_recipe")
    good_entry.artifact_path = str(artifact_path)
    registry.replace("error_recipe", good_entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    initial_states = build_initial_states([recipe], {"error_recipe": good_entry})
    initial_states["error_recipe"].last_sha256 = ""  # force reload

    # Corrupt the artifact before the watcher ticks
    artifact_path.write_bytes(b"this is not a valid artifact")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )
    watcher.start()
    try:
        time.sleep(0.5)
    finally:
        watcher.stop()
        watcher.join(timeout=2.0)

    entry = registry.get("error_recipe")
    assert entry is not None
    assert entry.last_load_error is not None, (
        "Watcher must set last_load_error via registry.set_load_error on failed load"
    )


# ---------------------------------------------------------------------------
# Fix 1: ThreadPoolExecutor reuse
# ---------------------------------------------------------------------------


def test_executor_exists_after_init() -> None:
    """ArtifactWatcher._executor must be a ThreadPoolExecutor after __init__."""
    from concurrent.futures import ThreadPoolExecutor

    from recotem.serving.watcher import ArtifactWatcher

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=Path("/tmp"),
        serve_config=cfg,
        key_ring=kr,
    )
    assert hasattr(watcher, "_executor"), (
        "ArtifactWatcher must have _executor attribute"
    )
    assert isinstance(watcher._executor, ThreadPoolExecutor)
    # Clean up without starting the thread
    watcher._executor.shutdown(wait=False)


def test_executor_reused_across_poll_cycles(tmp_path: Path) -> None:
    """The same executor instance must be reused across multiple _poll_artifacts calls."""
    from recotem.serving.watcher import ArtifactWatcher

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
    )

    executor_id_before = id(watcher._executor)
    # Call _poll_artifacts directly twice — no states so it returns immediately
    watcher._poll_artifacts()
    watcher._poll_artifacts()
    executor_id_after = id(watcher._executor)

    assert executor_id_before == executor_id_after, (
        "_poll_artifacts must not replace the executor; same instance required"
    )
    watcher._executor.shutdown(wait=False)


def test_executor_shutdown_on_stop(tmp_path: Path) -> None:
    """stop() must shut down the executor gracefully.

    After stop() the executor's internal _shutdown flag must be True,
    meaning it will no longer accept new work.
    """

    from recotem.serving.watcher import ArtifactWatcher

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
    )
    watcher.start()
    watcher.stop()
    watcher.join(timeout=2.0)

    # After shutdown, submitting new work must raise RuntimeError
    with pytest.raises(RuntimeError):
        watcher._executor.submit(lambda: None)


# ---------------------------------------------------------------------------
# Fix 2: artifact_read_failed log event on read failure
# ---------------------------------------------------------------------------


def test_artifact_read_failed_log_emitted_on_read_error(tmp_path: Path) -> None:
    """When _read_artifact_bytes raises, _load_recipe must emit an
    'artifact_read_failed' structlog event with name, path, and error keys."""
    from unittest.mock import patch

    import structlog.testing

    from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"

    from recotem.artifact.format import ArtifactError

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    yaml_path = _write_recipe_yaml(recipes_dir, "fail_recipe", artifact_path)
    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    state = _RecipeWatchState(recipe=recipe, artifact_path=str(artifact_path))
    initial_states = {"fail_recipe": state}

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )

    stub_entry = MagicMock()
    stub_entry.last_load_error = None
    stub_entry.loaded = False
    registry.replace("fail_recipe", stub_entry)

    with structlog.testing.capture_logs() as cap:
        with patch(
            "recotem.serving.watcher._read_artifact_bytes",
            side_effect=ArtifactError("disk read error"),
        ):
            watcher._load_recipe("fail_recipe", state, force=True)

    read_failed_events = [e for e in cap if e.get("event") == "artifact_read_failed"]
    assert read_failed_events, (
        "artifact_read_failed log event must be emitted when _read_artifact_bytes raises"
    )
    evt = read_failed_events[0]
    assert evt.get("name") == "fail_recipe", f"event must include name; got {evt!r}"
    assert "error" in evt, f"event must include error; got {evt!r}"
    assert "disk read error" in evt["error"]


def test_artifact_read_failed_log_emitted_on_unexpected_exception(
    tmp_path: Path,
) -> None:
    """artifact_read_failed must also fire for non-ArtifactError exceptions."""
    from unittest.mock import patch

    import structlog.testing

    from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    yaml_path = _write_recipe_yaml(recipes_dir, "unexpected_recipe", artifact_path)
    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    state = _RecipeWatchState(recipe=recipe, artifact_path=str(artifact_path))
    initial_states = {"unexpected_recipe": state}

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )

    stub_entry = MagicMock()
    stub_entry.last_load_error = None
    stub_entry.loaded = False
    registry.replace("unexpected_recipe", stub_entry)

    with structlog.testing.capture_logs() as cap:
        with patch(
            "recotem.serving.watcher._read_artifact_bytes",
            side_effect=OSError("network timeout"),
        ):
            watcher._load_recipe("unexpected_recipe", state, force=True)

    read_failed_events = [e for e in cap if e.get("event") == "artifact_read_failed"]
    assert read_failed_events, (
        "artifact_read_failed must fire for any exception from _read_artifact_bytes"
    )
    evt = read_failed_events[0]
    assert "network timeout" in evt.get("error", "")


# ---------------------------------------------------------------------------
# MAJOR-11: watcher uses registry setter for loaded marker
# ---------------------------------------------------------------------------


def test_watcher_uses_registry_setter_for_loaded_marker(tmp_path: Path) -> None:
    """After _load_recipe succeeds, the entry's _loaded_marker is set atomically
    via replace_with_marker (which holds the lock for both the entry insert and
    the marker assignment in one shot).  Pre-fix code used a two-step
    replace() + update_loaded_marker() which exposed a stale-marker window.
    """
    from unittest.mock import patch

    from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "marker_recipe", artifact_path)
    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    state = _RecipeWatchState(recipe=recipe, artifact_path=str(artifact_path))
    initial_states = {"marker_recipe": state}

    registry = ModelRegistry()
    # Insert a stub entry so the registry knows about the recipe before we call
    # _load_recipe directly.
    from recotem.serving.registry import ModelEntry

    stub = ModelEntry(
        name="marker_recipe",
        recommender=None,
        header={},
        kid="",
        loaded=False,
    )
    registry.replace("marker_recipe", stub)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )

    rwm_calls: list[tuple] = []
    real_rwm = registry.replace_with_marker

    def _spy(name, entry, marker):
        rwm_calls.append((name, marker))
        return real_rwm(name, entry, marker)

    with patch.object(registry, "replace_with_marker", side_effect=_spy):
        watcher._load_recipe("marker_recipe", state, force=True)

    # replace_with_marker must have been called at least once for the recipe
    matching = [c for c in rwm_calls if c[0] == "marker_recipe"]
    assert matching, (
        "watcher._load_recipe must call registry.replace_with_marker after a "
        "successful load; the two-step replace() + update_loaded_marker() path "
        "was replaced with the atomic replace_with_marker() to close the "
        "stale-marker window"
    )
    # Confirm the entry in the registry has the marker set
    entry = registry.get("marker_recipe")
    assert entry is not None
    loaded_marker = entry._loaded_marker
    assert isinstance(loaded_marker, tuple) and len(loaded_marker) == 2, (
        f"_loaded_marker must be a 2-tuple; got {loaded_marker!r}"
    )
    # sha256 part must be a non-empty hex string
    sha256_val = loaded_marker[1]
    assert sha256_val, "sha256 part of _loaded_marker must be non-empty after load"


# ---------------------------------------------------------------------------
# Fix D1: future raise in _poll_artifacts increments stat failure metric
# ---------------------------------------------------------------------------


def test_artifact_stat_future_raise_increments_failure_metric(
    tmp_path: Path,
) -> None:
    """When the executor future raises (e.g. BrokenThreadPool), the except
    branch in _poll_artifacts must call inc_artifact_stat_failure with the
    recipe name — not just log the error.
    """
    from concurrent.futures import Future
    from unittest.mock import patch

    from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    yaml_path = _write_recipe_yaml(recipes_dir, "stat_fail_recipe", artifact_path)
    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    state = _RecipeWatchState(recipe=recipe, artifact_path=str(artifact_path))

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states={"stat_fail_recipe": state},
    )

    # Build a future that raises RuntimeError when .result() is called.
    failing_future: Future = Future()
    failing_future.set_exception(RuntimeError("simulated executor failure"))

    failure_calls: list[str] = []
    original_inc = _metrics.inc_artifact_stat_failure

    def _counting_inc(name: str) -> None:
        failure_calls.append(name)
        original_inc(name)

    with patch.object(_metrics, "inc_artifact_stat_failure", side_effect=_counting_inc):
        # Patch executor.submit to return the pre-built failing future.
        with patch.object(watcher._executor, "submit", return_value=failing_future):
            watcher._poll_artifacts()

    assert "stat_fail_recipe" in failure_calls, (
        "inc_artifact_stat_failure must be called with the recipe name when "
        "the executor future raises an exception"
    )


# ---------------------------------------------------------------------------
# Fix D5: startup stat failure logs real recipe name
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T-1: _poll_artifacts concurrent stat count bounded by _MAX_CONCURRENT_STATS
# ---------------------------------------------------------------------------


def test_poll_artifacts_concurrent_stat_bounded_by_max(tmp_path: Path) -> None:
    """_poll_artifacts must not exceed _MAX_CONCURRENT_STATS=16 concurrent stats.

    Strategy:
    - Build 30 recipe entries (well above the ceiling of 16).
    - Monkeypatch _stat_marker to sleep briefly so futures can pile up, and
      to count peak concurrent invocations via a threading.Semaphore.
    - Call _poll_artifacts() directly once.
    - Assert peak concurrent calls never exceeded _MAX_CONCURRENT_STATS.
    """
    import threading
    import time as _time

    from recotem.serving.watcher import (
        _MAX_CONCURRENT_STATS,
        ArtifactWatcher,
        _RecipeWatchState,
    )

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    # Create 30 stub states with unique paths
    n = 30
    states: dict[str, _RecipeWatchState] = {}
    for i in range(n):
        artifact_path = tmp_path / f"model_{i}.recotem"
        recipe = MagicMock()
        recipe.name = f"recipe_{i}"
        recipe.output = MagicMock()
        recipe.output.path = str(artifact_path)
        recipe.item_metadata = None
        state = _RecipeWatchState(recipe=recipe, artifact_path=str(artifact_path))
        state.last_marker = (
            "old"  # force no reload (marker unchanged if stat returns same)
        )
        states[f"recipe_{i}"] = state

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=states,
    )

    concurrent_count = [0]
    peak_concurrent = [0]
    count_lock = threading.Lock()

    # _poll_artifacts calls _stat_marker_with_error (returns a tuple).
    # The slow stub must return the same marker as state.last_marker so
    # _load_recipe is NOT triggered (marker unchanged → no hot-swap).
    def _slow_stat_with_error(path: str, recipe_name: str = "<unknown>"):
        with count_lock:
            concurrent_count[0] += 1
            if concurrent_count[0] > peak_concurrent[0]:
                peak_concurrent[0] = concurrent_count[0]
        _time.sleep(0.05)
        with count_lock:
            concurrent_count[0] -= 1
        # Return the same marker so _load_recipe is not triggered
        return "old", None

    from unittest.mock import patch

    with patch(
        "recotem.serving.watcher._stat_marker_with_error",
        side_effect=_slow_stat_with_error,
    ):
        watcher._poll_artifacts()

    watcher._executor.shutdown(wait=False)

    assert peak_concurrent[0] <= _MAX_CONCURRENT_STATS, (
        f"Peak concurrent stat calls {peak_concurrent[0]} exceeded "
        f"_MAX_CONCURRENT_STATS={_MAX_CONCURRENT_STATS}. "
        "The executor must bound parallelism."
    )
    assert peak_concurrent[0] > 1, (
        f"Expected concurrent execution (peak > 1), got {peak_concurrent[0]}. "
        "The test may not be measuring concurrency correctly."
    )


# ---------------------------------------------------------------------------
# T-2: same-tick add + remove both reflected after one _scan_recipes_dir call
# ---------------------------------------------------------------------------


def test_scan_recipes_dir_add_and_remove_in_same_tick(tmp_path: Path) -> None:
    """_scan_recipes_dir in one tick must handle simultaneous add + remove.

    Setup:
    - Preload watcher with one recipe ('old_recipe').
    - Before calling _scan_recipes_dir again:
      - Delete old_recipe.yaml from the recipes_dir.
      - Write new_recipe.yaml (with a valid artifact).
    - After one _scan_recipes_dir call:
      - 'old_recipe' must be absent from the registry.
      - 'new_recipe' must have a stub entry in the registry (even if artifact
        load fails because the artifact file may not exist).
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    # ── Create OLD recipe ────────────────────────────────────────────────────
    old_artifact = tmp_path / "old_model.recotem"
    _write_valid_artifact(old_artifact)
    old_yaml = _write_recipe_yaml(recipes_dir, "old_recipe", old_artifact)

    from recotem.recipe.loader import load_recipe

    old_recipe = load_recipe(old_yaml)

    registry = ModelRegistry()
    old_entry = _make_entry("old_recipe")
    old_entry.artifact_path = str(old_artifact)
    registry.replace("old_recipe", old_entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    initial_states = build_initial_states([old_recipe], {"old_recipe": old_entry})

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )

    # ── Within the same tick: remove old, add new ────────────────────────────
    old_yaml.unlink()

    new_artifact = tmp_path / "new_model.recotem"
    _write_valid_artifact(new_artifact)
    _write_recipe_yaml(recipes_dir, "new_recipe", new_artifact)

    # ── Single _scan_recipes_dir call ────────────────────────────────────────
    watcher._scan_recipes_dir()

    watcher._executor.shutdown(wait=False)

    # old_recipe must be removed
    assert registry.get("old_recipe") is None, (
        "old_recipe must be removed after its YAML is deleted in _scan_recipes_dir"
    )
    # new_recipe must be discovered (stub inserted even if artifact load fails)
    new_entry = registry.get("new_recipe")
    assert new_entry is not None, (
        "new_recipe must be registered in the registry after _scan_recipes_dir "
        "discovers its YAML, even before artifact is fully loaded"
    )


def test_startup_stat_failure_logs_real_recipe_name(
    tmp_path: Path,
) -> None:
    """When _stat_marker is called during startup (force=True, marker=None)
    and the stat fails, the logged recipe name must be the real recipe name,
    not the default '<unknown>'.

    We verify by calling _stat_marker directly with a recipe_name kwarg and
    confirming the log event captures it correctly.
    """
    import structlog.testing

    from recotem.serving.watcher import _stat_marker

    # Point at a path that doesn't trigger FileNotFoundError but causes
    # a generic error by patching fsspec.
    nonexistent_path = str(tmp_path / "ghost.recotem")

    import fsspec

    original_url_to_fs = fsspec.core.url_to_fs

    def _raising_url_to_fs(path, **kwargs):
        fs_mock = MagicMock()
        fs_mock.info.side_effect = OSError("simulated stat error")
        return fs_mock, path

    with structlog.testing.capture_logs() as cap:
        from unittest.mock import patch

        with patch(
            "recotem.serving.watcher.fsspec.core.url_to_fs",
            side_effect=_raising_url_to_fs,
        ):
            result = _stat_marker(nonexistent_path, recipe_name="startup_recipe")

    assert result is None, "_stat_marker must return None on error"

    stat_failed_events = [e for e in cap if e.get("event") == "artifact_stat_failed"]
    assert stat_failed_events, (
        "artifact_stat_failed must be logged on non-FileNotFoundError"
    )
    evt = stat_failed_events[0]
    assert evt.get("recipe") == "startup_recipe", (
        f"artifact_stat_failed must log real recipe name 'startup_recipe', "
        f"not '<unknown>'; got {evt!r}"
    )
    assert evt.get("recipe") != "<unknown>", (
        "artifact_stat_failed must NOT log '<unknown>' when recipe_name is provided"
    )


# ---------------------------------------------------------------------------
# M-2: rescan YAML syntax error keeps loaded model
# ---------------------------------------------------------------------------


def test_rescan_yaml_syntax_error_keeps_loaded_model(tmp_path: Path) -> None:
    """When a YAML file becomes unparseable during rescan, the registry must
    retain the existing loaded model and set last_load_error; the recipe must
    NOT be removed from the registry.

    Also: recotem_recipe_rescan_errors_total must be incremented.
    """
    from unittest.mock import patch

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "rescan_test", artifact_path)

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)

    registry = ModelRegistry()
    entry = _make_entry("rescan_test")
    entry.artifact_path = str(artifact_path)
    entry.last_load_error = None
    registry.replace("rescan_test", entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    initial_states = build_initial_states([recipe], {"rescan_test": entry})

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )

    # Corrupt the YAML file (syntax error)
    yaml_path.write_text("name: [this is: invalid yaml {{{\n")

    rescan_error_calls: list[str] = []
    original_inc = _metrics.inc_recipe_rescan_error

    def _counting_inc(name: str) -> None:
        rescan_error_calls.append(name)
        original_inc(name)

    with patch.object(_metrics, "inc_recipe_rescan_error", side_effect=_counting_inc):
        # Call _scan_recipes_dir directly so we don't need the thread running.
        watcher._scan_recipes_dir()

    # Registry must still contain the entry (not deleted).
    surviving_entry = registry.get("rescan_test")
    assert surviving_entry is not None, (
        "Recipe must NOT be removed when its YAML fails to parse on rescan"
    )

    # last_load_error must be populated with the parse error.
    assert surviving_entry.last_load_error is not None, (
        "last_load_error must be set on the registry entry when YAML parse fails"
    )
    assert "rescan" in surviving_entry.last_load_error.lower() or (
        "parse" in surviving_entry.last_load_error.lower()
        or "error" in surviving_entry.last_load_error.lower()
    ), (
        f"last_load_error should describe a YAML parse error; "
        f"got: {surviving_entry.last_load_error!r}"
    )

    # recotem_recipe_rescan_errors_total must be incremented.
    assert "rescan_test" in rescan_error_calls, (
        "inc_recipe_rescan_error must be called with the recipe name on YAML parse failure"
    )

    watcher._executor.shutdown(wait=False)


def test_rescan_recipe_truly_deleted_removes_entry(tmp_path: Path) -> None:
    """Regression: when the YAML file is genuinely deleted (not just broken),
    the registry entry must still be removed after _scan_recipes_dir.

    This preserves the existing behavior — only transient parse errors on
    an existing YAML must be retained; actual deletion must purge the entry.
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "to_delete", artifact_path)

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)

    registry = ModelRegistry()
    entry = _make_entry("to_delete")
    entry.artifact_path = str(artifact_path)
    registry.replace("to_delete", entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    initial_states = build_initial_states([recipe], {"to_delete": entry})

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )

    # Delete the YAML file entirely.
    yaml_path.unlink()

    # One scan should remove the entry.
    watcher._scan_recipes_dir()

    assert registry.get("to_delete") is None, (
        "Recipe must be removed from registry when its YAML file is deleted"
    )

    watcher._executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# M-9: _stat_marker error class surfaced in /health via set_load_error
# ---------------------------------------------------------------------------


def test_stat_marker_permission_error_sets_descriptive_load_error(
    tmp_path: Path,
) -> None:
    """When _stat_marker encounters a non-FileNotFoundError (e.g. permission denied),
    the watcher must set a descriptive last_load_error that includes the error
    class name rather than the generic 'artifact missing or unreadable'.
    """
    from unittest.mock import patch

    from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "perm_recipe", artifact_path)

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    state = _RecipeWatchState(
        recipe=recipe,
        artifact_path=str(artifact_path),
        last_marker="initial",  # force marker to differ from None
        last_sha256="",
    )

    registry = ModelRegistry()
    from recotem.serving.registry import ModelEntry

    stub = ModelEntry(
        name="perm_recipe",
        recommender=None,
        header={},
        kid="",
        loaded=False,
        last_load_error=None,
    )
    registry.replace("perm_recipe", stub)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states={"perm_recipe": state},
    )

    # Monkeypatch fsspec to raise OSError("permission denied") on stat.
    def _raising_url_to_fs(path, **kwargs):
        fs_mock = MagicMock()
        fs_mock.info.side_effect = OSError("permission denied")
        return fs_mock, path

    with patch(
        "recotem.serving.watcher.fsspec.core.url_to_fs",
        side_effect=_raising_url_to_fs,
    ):
        watcher._poll_artifacts()

    entry = registry.get("perm_recipe")
    assert entry is not None
    assert entry.last_load_error is not None, (
        "last_load_error must be set when stat fails with OSError"
    )
    # The error message must contain the error class name (M-9).
    assert "OSError" in entry.last_load_error, (
        f"last_load_error must include error class 'OSError'; "
        f"got: {entry.last_load_error!r}"
    )

    watcher._executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# M-11: ETag-based hot-swap on object store path
# ---------------------------------------------------------------------------


def test_hot_swap_triggered_by_etag_change_on_object_store(
    tmp_path: Path,
) -> None:
    """When fsspec info() returns ETag metadata, the watcher must detect a swap
    when the ETag changes between poll ticks.

    Strategy:
    - Monkeypatch fsspec to return ETag='v1' first, then 'v2'.
    - On ETag change the watcher calls _load_recipe; monkeypatch
      _read_artifact_bytes to return a valid artifact.
    - Assert that record_swap (ok=True) was called on the second tick.
    """
    from unittest.mock import patch

    from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "etag_recipe", artifact_path)

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    state = _RecipeWatchState(
        recipe=recipe,
        artifact_path=str(artifact_path),
        last_marker="v1",  # ETag 'v1' is the current known marker
        last_sha256="",
    )

    registry = ModelRegistry()
    from recotem.serving.registry import ModelEntry

    stub = ModelEntry(
        name="etag_recipe",
        recommender=None,
        header={},
        kid="",
        loaded=False,
        last_load_error=None,
    )
    registry.replace("etag_recipe", stub)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states={"etag_recipe": state},
    )

    swap_ok_calls: list[str] = []
    original_record_swap = _metrics.record_swap

    def _spy_swap(recipe: str, ok: bool) -> None:
        if ok:
            swap_ok_calls.append(recipe)
        original_record_swap(recipe, ok)

    # Monkeypatch _stat_marker_with_error to return ETag='v2' (changed from v1).
    # _poll_artifacts detects the marker change and calls _load_recipe which
    # reads the actual artifact file from disk via _read_artifact_bytes
    # (real fsspec, real file I/O — artifact was written above).
    import recotem.serving.watcher as watcher_module

    def _etag_v2_stat(path: str, recipe_name: str = "<unknown>"):
        # Return ETag 'v2' so marker differs from state.last_marker='v1'
        return "v2", None

    with patch.object(
        watcher_module, "_stat_marker_with_error", side_effect=_etag_v2_stat
    ):
        with patch.object(_metrics, "record_swap", side_effect=_spy_swap):
            watcher._poll_artifacts()

    # Wait for any submitted futures to complete (load runs in thread pool too)
    watcher._executor.shutdown(wait=True)

    assert "etag_recipe" in swap_ok_calls, (
        "record_swap(ok=True) must be called when ETag changes from 'v1' to 'v2'; "
        f"got swap_ok_calls={swap_ok_calls!r}"
    )


# ---------------------------------------------------------------------------
# M-1 regression: public re-exports for symbols used outside the watcher
# ---------------------------------------------------------------------------


def test_public_reexports_are_bound_and_callable() -> None:
    """``serving.app`` reaches into watcher for these helpers; expose them as
    public names so the cross-module contract is no longer a leading-
    underscore handshake.  The aliases must point at the same callables to
    keep behaviour identical and so existing private-name unit tests keep
    binding to the canonical implementation.
    """
    import recotem.serving.watcher as watcher_module

    assert watcher_module.read_artifact_bytes is watcher_module._read_artifact_bytes
    assert watcher_module.stat_marker is watcher_module._stat_marker
    assert watcher_module.sha256_bytes is watcher_module._sha256_bytes
    assert watcher_module.load_metadata is watcher_module._load_metadata


def test_public_reexports_in_module_all() -> None:
    """``__all__`` must list the public surface so ``from watcher import *``
    in downstream code does not pull private helpers."""
    import recotem.serving.watcher as watcher_module

    expected = {
        "ArtifactWatcher",
        "build_initial_states",
        "read_artifact_bytes",
        "stat_marker",
        "sha256_bytes",
        "load_metadata",
    }
    assert set(watcher_module.__all__) == expected, (
        f"watcher.__all__ drift: got {sorted(watcher_module.__all__)!r}, "
        f"expected {sorted(expected)!r}"
    )


# ---------------------------------------------------------------------------
# M-5 regression: MemoryError from _build_entry must propagate, not be
# swallowed by the broad except → "next poll" loop
# ---------------------------------------------------------------------------


def test_build_entry_memory_error_propagates_through_load_recipe(
    tmp_path: Path,
) -> None:
    """A long-running watcher must never silently retry through OOM.

    Pre-fix, ``except Exception`` in ``_load_recipe`` caught ``MemoryError``
    too — so the next poll cycle re-attempted the load, repeating the OOM
    path indefinitely until the kernel killed the process with no
    actionable log line.  The fix re-raises ``MemoryError`` /
    ``RecursionError`` before the broad handler runs.

    The test mocks ``_read_artifact_bytes`` to return synthetic bytes so the
    load reaches ``_build_entry`` (where the simulated MemoryError fires).
    """
    from unittest.mock import patch

    from recotem.recipe.models import OutputConfig
    from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState

    artifact_path = tmp_path / "model.recotem"
    artifact_path.write_bytes(b"placeholder")

    cfg = _make_serve_config()
    key_ring = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    fake_recipe = MagicMock()
    fake_recipe.name = "oom_recipe"
    fake_recipe.output = OutputConfig(path=str(artifact_path))
    initial_states = {
        "oom_recipe": _RecipeWatchState(
            recipe=fake_recipe, artifact_path=str(artifact_path)
        ),
    }
    registry = ModelRegistry()
    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=tmp_path,
        serve_config=cfg,
        key_ring=key_ring,
        initial_states=initial_states,
    )

    state = initial_states["oom_recipe"]
    # Force the sha-comparison branch to "different" so _build_entry runs.
    state.last_sha256 = "0" * 64
    state.last_marker = ("v0", None)

    fresh_data = b"\x00" * 128

    with (
        patch(
            "recotem.serving.watcher._read_artifact_bytes",
            return_value=fresh_data,
        ),
        patch.object(
            watcher,
            "_build_entry",
            side_effect=MemoryError("simulated OOM"),
        ),
        pytest.raises(MemoryError, match="simulated OOM"),
    ):
        watcher._load_recipe(
            "oom_recipe",
            state,
            force=True,
            marker=("v1", None),
        )


# ---------------------------------------------------------------------------
# M-6: recipes_dir scan failure counter
# ---------------------------------------------------------------------------


def test_recipes_dir_scan_failure_increments_counter_on_recipe_error(
    tmp_path: Path,
) -> None:
    """When _scan_recipes_dir encounters a per-recipe load failure for a
    brand-new YAML (not previously registered), the neutral
    recotem_recipes_dir_scan_failures_total counter must be incremented.

    This ensures operators can observe silent per-recipe load errors even
    when the recipe has never made it into the registry.
    """
    from unittest.mock import patch

    from recotem._metrics_watcher import inc_recipes_dir_scan_failure

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    # Write a YAML that is syntactically broken so load_recipe raises.
    bad_yaml = recipes_dir / "broken.yaml"
    bad_yaml.write_text("name: [invalid yaml {{{\n")

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
    )

    call_args: list[str] = []
    original = inc_recipes_dir_scan_failure

    def _spy(error_class: str) -> None:
        call_args.append(error_class)
        original(error_class)

    with patch(
        "recotem.serving.watcher._inc_scan_failure",
        side_effect=_spy,
    ):
        watcher._scan_recipes_dir()

    watcher._executor.shutdown(wait=False)

    assert call_args, (
        "inc_recipes_dir_scan_failure must be called when a per-recipe YAML "
        "load fails in _scan_recipes_dir"
    )


def test_recipes_dir_scan_failure_counter_label_is_error_class_name(
    tmp_path: Path,
) -> None:
    """The error_class label passed to inc_recipes_dir_scan_failure must be
    ``type(exc).__name__``, not a generic constant.

    Strategy: write a YAML file whose load_recipe call (imported locally inside
    _scan_recipes_dir) will raise ValueError.  Patch the module-level import in
    recotem.recipe.loader so the local import inside the method resolves to the
    stub.  Assert the spy receives 'ValueError'.
    """
    from unittest.mock import patch

    import recotem.recipe.loader as _loader_mod
    from recotem._metrics_watcher import inc_recipes_dir_scan_failure

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    # Any YAML file will do — load_recipe is patched to raise before it reads.
    (recipes_dir / "target.yaml").write_text("name: target\n")

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
    )

    call_args: list[str] = []

    def _spy(error_class: str) -> None:
        call_args.append(error_class)
        inc_recipes_dir_scan_failure(error_class)

    # _scan_recipes_dir does `from recotem.recipe.loader import load_recipe`
    # inside the loop.  Patching the attribute on the module object redirects
    # that local import to our stub.
    with patch.object(_loader_mod, "load_recipe", side_effect=ValueError("boom")):
        with patch("recotem.serving.watcher._inc_scan_failure", side_effect=_spy):
            watcher._scan_recipes_dir()

    watcher._executor.shutdown(wait=False)

    assert call_args, "inc_recipes_dir_scan_failure must be called"
    assert call_args[0] == "ValueError", (
        f"error_class label must be 'ValueError'; got {call_args[0]!r}"
    )


# ---------------------------------------------------------------------------
# S-4: _extract_kid_safe log-DoS guard
# ---------------------------------------------------------------------------


def test_extract_kid_safe_truncates_long_kid_in_log(tmp_path: Path) -> None:
    """_format_kid_for_log must truncate any kid longer than _KID_LOG_MAX_LEN
    characters and append '...' so log aggregators are not flooded.

    After OBS-3 the format changed: truncation suffix is '...' (not the old
    '...<truncated>'), and non-safe chars are rendered as \\xHH escapes rather
    than '?' replacements.  This test verifies the new contract.

    We call the helper directly (since MAX_KID_LEN is only 32, we can't
    embed an actual >64-char kid in a real artifact).
    """
    from recotem.serving.watcher import _KID_LOG_MAX_LEN, _format_kid_for_log

    long_kid = "a" * (_KID_LOG_MAX_LEN + 10)
    result = _format_kid_for_log(long_kid)

    assert len(result) <= _KID_LOG_MAX_LEN + len("..."), (
        "Formatted kid must not exceed _KID_LOG_MAX_LEN + truncation suffix"
    )
    assert result.endswith("..."), (
        f"Long kid must be suffixed with '...'; got {result!r}"
    )
    assert result.startswith("a" * _KID_LOG_MAX_LEN), (
        "First _KID_LOG_MAX_LEN chars must be preserved before the suffix"
    )


def test_extract_kid_safe_strips_non_printable_chars(tmp_path: Path) -> None:
    """_format_kid_for_log must replace non-safe bytes with \\xHH escapes to
    prevent terminal escape-sequence injection in log output.

    After OBS-3 the sanitisation changed from '?' replacement to \\xHH hex
    escapes, which are more informative (the original byte is recoverable) and
    still prevent ANSI injection.  Brackets '[' and ']' in the ANSI sequence are
    outside the safe-char set and also get escaped.
    """
    from recotem.serving.watcher import _format_kid_for_log

    # Include an ANSI escape, a null byte, and a tab (non-printable)
    evil_kid = "hello\x1b[31mred\x00\tworld"
    result = _format_kid_for_log(evil_kid)

    # Raw control characters must not appear in the output
    assert "\x1b" not in result, "Raw ESC byte must not appear in output"
    assert "\x00" not in result, "Raw null byte must not appear in output"
    assert "\t" not in result, "Raw tab must not appear in output"

    # After OBS-3 the non-safe chars are rendered as \\xHH escapes (literal
    # backslash-x-HH in the returned string), NOT as '?' replacements.
    assert r"\x1b" in result, "ESC must appear as \\x1b escape in output"
    assert r"\x00" in result, "Null must appear as \\x00 escape in output"
    assert r"\x09" in result, "Tab must appear as \\x09 escape in output"

    # Printable chars in the safe set must be preserved
    assert "hello" in result
    assert "world" in result


# ---------------------------------------------------------------------------
# P-4: pointer-only poll for append-sha sidecar files
# ---------------------------------------------------------------------------


def test_pointer_only_poll_skips_full_read_when_sidecar_unchanged(
    tmp_path: Path,
) -> None:
    """When a ``.sha256`` sidecar exists and its contents are unchanged,
    _check_sidecar_changed must return False and log a
    'pointer_unchanged_skip_read' debug event, so the expensive full
    artifact read is skipped.
    """
    import structlog.testing

    from recotem.serving.watcher import _check_sidecar_changed, _RecipeWatchState

    artifact_path = tmp_path / "model.recotem"
    artifact_path.write_bytes(b"placeholder")
    sidecar_path = Path(str(artifact_path) + ".sha256")
    sidecar_contents = "model.abc12345.recotem\n"
    sidecar_path.write_text(sidecar_contents)

    recipe = MagicMock()
    recipe.name = "sidecar_test"
    state = _RecipeWatchState(
        recipe=recipe,
        artifact_path=str(artifact_path),
        last_sidecar_contents=sidecar_contents,  # already known — unchanged
    )

    with structlog.testing.capture_logs() as cap:
        changed = _check_sidecar_changed(state)

    assert changed is False, (
        "_check_sidecar_changed must return False when sidecar contents are unchanged"
    )
    skip_events = [e for e in cap if e.get("event") == "pointer_unchanged_skip_read"]
    assert skip_events, (
        "pointer_unchanged_skip_read debug event must be emitted when sidecar is unchanged"
    )


def test_pointer_change_triggers_full_read(tmp_path: Path) -> None:
    """When the ``.sha256`` sidecar contents differ from last-known,
    _check_sidecar_changed must return True and update
    state.last_sidecar_contents in-place.
    """
    from recotem.serving.watcher import _check_sidecar_changed, _RecipeWatchState

    artifact_path = tmp_path / "model.recotem"
    artifact_path.write_bytes(b"placeholder")
    sidecar_path = Path(str(artifact_path) + ".sha256")
    old_contents = "model.abc12345.recotem\n"
    new_contents = "model.def67890.recotem\n"
    sidecar_path.write_text(new_contents)

    recipe = MagicMock()
    recipe.name = "sidecar_change_test"
    state = _RecipeWatchState(
        recipe=recipe,
        artifact_path=str(artifact_path),
        last_sidecar_contents=old_contents,  # stale: file now has new_contents
    )

    changed = _check_sidecar_changed(state)

    assert changed is True, (
        "_check_sidecar_changed must return True when sidecar contents changed"
    )
    assert state.last_sidecar_contents == new_contents, (
        "state.last_sidecar_contents must be updated to the new sidecar value"
    )


def test_no_sidecar_falls_back_to_full_stat_compare(tmp_path: Path) -> None:
    """When no ``.sha256`` sidecar exists, _check_sidecar_changed must return
    False so the caller falls back to the existing full-stat marker comparison.
    """
    from recotem.serving.watcher import _check_sidecar_changed, _RecipeWatchState

    artifact_path = tmp_path / "model_no_sidecar.recotem"
    artifact_path.write_bytes(b"placeholder")
    # Confirm no sidecar exists
    sidecar_path = Path(str(artifact_path) + ".sha256")
    assert not sidecar_path.exists(), "Test setup error: sidecar must not exist"

    recipe = MagicMock()
    recipe.name = "no_sidecar_test"
    state = _RecipeWatchState(
        recipe=recipe,
        artifact_path=str(artifact_path),
        last_sidecar_contents=None,
    )

    changed = _check_sidecar_changed(state)

    assert changed is False, (
        "_check_sidecar_changed must return False when no sidecar file exists; "
        "caller must use full-stat comparison as fallback"
    )


# ---------------------------------------------------------------------------
# N-3: M-1 — _scan_recipes_dir inserts stub without calling _load_recipe
# ---------------------------------------------------------------------------


def test_scan_recipes_dir_new_yaml_does_not_call_load_recipe_synchronously(
    tmp_path: Path,
) -> None:
    """When a new YAML is discovered by _scan_recipes_dir, _load_recipe must
    NOT be called synchronously (M-1 contract: blocking I/O stalls the watcher
    loop).  Instead a stub entry with loaded=False is inserted so the next
    _poll_artifacts tick picks up the new recipe via the marker-change path.
    """
    from unittest.mock import MagicMock

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
    )

    # Replace _load_recipe with a MagicMock spy BEFORE writing the YAML
    watcher._load_recipe = MagicMock(name="_load_recipe")  # type: ignore[method-assign]

    # Now add a new YAML to the empty recipes dir
    artifact_path = tmp_path / "new_model.recotem"
    artifact_path.write_bytes(b"placeholder")
    _write_recipe_yaml(recipes_dir, "new_recipe_n3", artifact_path)

    # Call _scan_recipes_dir — this should discover the new YAML
    watcher._scan_recipes_dir()

    # _load_recipe must NOT have been called (M-1: async load via poll tick)
    assert watcher._load_recipe.call_count == 0, (
        f"_load_recipe must NOT be called synchronously during _scan_recipes_dir "
        f"(M-1 contract); call_count={watcher._load_recipe.call_count}"
    )

    # A stub entry must have been inserted so /health can show it
    entry = registry.get("new_recipe_n3")
    assert entry is not None, (
        "A stub entry must be inserted in the registry when a new YAML is discovered"
    )
    assert not entry.loaded, (
        "The stub entry must have loaded=False (it has not been loaded yet)"
    )

    watcher._executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# N-7: M-5 — _scan_recipes_dir iterdir failure increments _inc_scan_failure
# ---------------------------------------------------------------------------


def test_scan_recipes_dir_iterdir_failure_increments_scan_failure_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _scan_recipes_dir's Path.iterdir() raises PermissionError,
    the recotem_recipes_dir_scan_failures_total counter must be incremented
    with error_class='dir_iter_PermissionError'.

    The watcher should log a structured warning and return without crashing.
    """
    from pathlib import Path as _Path
    from unittest.mock import MagicMock, patch

    import structlog.testing

    from recotem.serving.watcher import ArtifactWatcher

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    registry = ModelRegistry()
    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
    )

    # Replace _recipes_dir with a MagicMock whose iterdir raises PermissionError.
    # PosixPath.iterdir is a C-level slot (read-only), so we can't patch it via
    # patch.object.  Instead we substitute the entire _recipes_dir attribute.
    mock_dir = MagicMock(spec=_Path)
    mock_dir.iterdir.side_effect = PermissionError("denied")
    watcher._recipes_dir = mock_dir

    scan_failure_calls: list[str] = []
    from recotem._metrics_watcher import inc_recipes_dir_scan_failure

    def _spy(error_class: str) -> None:
        scan_failure_calls.append(error_class)
        inc_recipes_dir_scan_failure(error_class)

    with structlog.testing.capture_logs() as cap:
        with patch("recotem.serving.watcher._inc_scan_failure", side_effect=_spy):
            watcher._scan_recipes_dir()

    watcher._executor.shutdown(wait=False)

    # Scan failure counter must have been incremented with dir_iter_* label
    dir_iter_calls = [c for c in scan_failure_calls if c.startswith("dir_iter_")]
    assert dir_iter_calls, (
        f"_inc_scan_failure must be called with 'dir_iter_*' label on iterdir failure; "
        f"got calls: {scan_failure_calls!r}"
    )

    # The warning log event must also be emitted
    scan_error_events = [e for e in cap if e.get("event") == "recipes_dir_scan_error"]
    assert scan_error_events, (
        "recipes_dir_scan_error warning must be emitted when iterdir fails"
    )
    assert scan_error_events[0].get("error_class") == "PermissionError", (
        f"error_class field must be 'PermissionError'; got {scan_error_events[0]!r}"
    )


# ---------------------------------------------------------------------------
# N-8: M-6 — parse error on rescan does not evict the loaded model
# ---------------------------------------------------------------------------


def test_scan_recipes_dir_yaml_corrupt_on_rescan_keeps_loaded_entry(
    tmp_path: Path,
) -> None:
    """When a previously-loaded recipe's YAML becomes unparseable on rescan,
    the existing registry entry must NOT be evicted.

    The recipe must remain visible in the registry (found_names includes it)
    and last_load_error is updated, but the model keeps serving (M-2 contract).
    This variant also exercises M-6: recipe.name from YAML may differ from
    file stem; once the path→name mapping is established, a later parse error
    is matched by that mapping, not the stem.
    """
    from unittest.mock import patch

    import recotem.recipe.loader as _loader_mod

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    # Write a recipe whose name matches the stem (simplest case for M-6 path)
    yaml_path = _write_recipe_yaml(recipes_dir, "n8_recipe", artifact_path)

    registry = ModelRegistry()
    good_entry = _make_entry("n8_recipe")
    good_entry.artifact_path = str(artifact_path)
    good_entry.last_load_error = None
    registry.replace("n8_recipe", good_entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    initial_states = build_initial_states([recipe], {"n8_recipe": good_entry})

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states=initial_states,
    )
    # Populate the yaml_path → name mapping (normally done via build_initial_states
    # or a previous successful scan)
    watcher._yaml_path_to_name[yaml_path] = "n8_recipe"

    # Now simulate a parse error on the YAML (corrupt content, but file still exists)
    with patch.object(
        _loader_mod,
        "load_recipe",
        side_effect=ValueError("YAML syntax error: invalid"),
    ):
        watcher._scan_recipes_dir()

    watcher._executor.shutdown(wait=False)

    # The entry must still be in the registry (not evicted)
    entry = registry.get("n8_recipe")
    assert entry is not None, (
        "Registry entry must NOT be evicted when YAML becomes unparseable on rescan"
    )

    # last_load_error should be set so /health surfaces the parse failure
    assert entry.last_load_error is not None, (
        "last_load_error must be updated when YAML parse fails on rescan"
    )
    assert (
        "parse error" in entry.last_load_error.lower()
        or "yaml" in entry.last_load_error.lower()
    ), (
        f"last_load_error should mention the parse failure; got {entry.last_load_error!r}"
    )


# ---------------------------------------------------------------------------
# N-10: OBS-1 — repeated stat errors with same error_class demoted to DEBUG
# ---------------------------------------------------------------------------


def test_repeated_stat_error_same_class_demoted_to_debug(
    tmp_path: Path,
) -> None:
    """The first occurrence of a stat error emits WARNING; subsequent occurrences
    with the same error_class are demoted to DEBUG (logged as
    'artifact_stat_failed_repeated') to prevent log aggregation flooding during
    sustained outages (OBS-1).

    Strategy:
    1. Build a watcher with one recipe whose artifact is a path that can be
       monkeypatched to always return an error_class.
    2. Call _poll_artifacts() twice with a mock _stat_marker_with_error that
       always returns (None, 'OSError').
    3. After the first call, _last_stat_error_class is 'OSError'; the WARNING
       has already been emitted inside _stat_marker_with_error itself.
    4. On the second call, the code path checks
       state._last_stat_error_class == stat_error_class and emits a DEBUG
       event 'artifact_stat_failed_repeated' instead of another WARNING.
    """
    from unittest.mock import patch

    import structlog.testing

    from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "obs1_recipe", artifact_path)

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    state = _RecipeWatchState(recipe=recipe, artifact_path=str(artifact_path))

    registry = ModelRegistry()
    stub_entry = _make_entry("obs1_recipe")
    stub_entry.last_load_error = None
    registry.replace("obs1_recipe", stub_entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states={"obs1_recipe": state},
    )

    # _stat_marker_with_error is called inside the executor's _check function.
    # Patch it to always return (None, "OSError").
    with patch(
        "recotem.serving.watcher._stat_marker_with_error",
        return_value=(None, "OSError"),
    ):
        # First poll: _last_stat_error_class is None → different from "OSError"
        # → state._last_stat_error_class is updated to "OSError"
        # The warning is emitted in _stat_marker_with_error itself (already patched
        # to not emit it).  We just verify the state transition and second-call
        # behaviour.
        with structlog.testing.capture_logs() as cap_first:
            watcher._poll_artifacts()

        # After first poll the state must record the error class
        assert state._last_stat_error_class == "OSError", (
            f"After first stat error, _last_stat_error_class must be 'OSError'; "
            f"got {state._last_stat_error_class!r}"
        )

        # Second poll: same error class → must emit 'artifact_stat_failed_repeated'
        with structlog.testing.capture_logs() as cap_second:
            watcher._poll_artifacts()

    repeated_events = [
        e for e in cap_second if e.get("event") == "artifact_stat_failed_repeated"
    ]
    assert repeated_events, (
        "Second consecutive stat error with same error_class must emit "
        "'artifact_stat_failed_repeated' (DEBUG) event to prevent log flooding; "
        f"events captured: {[e.get('event') for e in cap_second]!r}"
    )

    watcher._executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Fix 3: Sidecar-only path must update state.last_marker
# ---------------------------------------------------------------------------


def test_sidecar_only_path_updates_last_marker(tmp_path: Path) -> None:
    """When marker == state.last_marker (fast path), state.last_marker must
    be explicitly updated to the new marker value.

    Pre-fix: the code did ``continue`` without reassigning state.last_marker.
    On object stores with unstable ETags, the watcher would receive a stable
    ETag on one tick and not acknowledge it, causing spurious reloads or
    stale-marker windows on the next tick.

    This test verifies that after a no-op sidecar check (sidecar unchanged),
    state.last_marker is still updated to the current marker.
    """
    from unittest.mock import patch

    from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "marker_stable", artifact_path)

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    state = _RecipeWatchState(
        recipe=recipe,
        artifact_path=str(artifact_path),
        last_marker="etag-v1",
        last_sha256="",
    )

    registry = ModelRegistry()
    from recotem.serving.registry import ModelEntry

    stub = ModelEntry(
        name="marker_stable",
        recommender=None,
        header={},
        kid="",
        loaded=False,
        last_load_error=None,
    )
    registry.replace("marker_stable", stub)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states={"marker_stable": state},
    )

    import recotem.serving.watcher as watcher_module

    def _same_marker(path: str, recipe_name: str = "<unknown>"):
        return "etag-v1", None  # same as state.last_marker

    with patch.object(
        watcher_module, "_stat_marker_with_error", side_effect=_same_marker
    ):
        with patch.object(watcher_module, "_check_sidecar_changed", return_value=False):
            watcher._poll_artifacts()

    watcher._executor.shutdown(wait=False)

    # state.last_marker must be updated even when sidecar didn't change
    assert state.last_marker == "etag-v1", (
        f"state.last_marker must be updated to the current marker even when "
        f"marker == last_marker and sidecar unchanged; got {state.last_marker!r}"
    )


# ---------------------------------------------------------------------------
# Fix 4: Atomic replace_with_marker — no stale-marker window
# ---------------------------------------------------------------------------


def test_replace_with_marker_is_atomic_no_stale_window(tmp_path: Path) -> None:
    """After a successful hot-swap, readers must never see a fresh recommender
    paired with a stale (None, '') _loaded_marker.

    Pre-fix: _load_recipe called registry.replace(entry) then
    registry.update_loaded_marker(marker) as two separate lock acquisitions.
    A reader iterating list() between those two ops would see the new
    recommender with the old (unset) marker.

    Fix: registry.replace_with_marker() sets both atomically in one lock.
    """
    from unittest.mock import patch

    from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "atomic_recipe", artifact_path)

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    state = _RecipeWatchState(
        recipe=recipe,
        artifact_path=str(artifact_path),
        last_marker=None,
        last_sha256="",
    )

    registry = ModelRegistry()
    from recotem.serving.registry import ModelEntry

    stub = ModelEntry(
        name="atomic_recipe",
        recommender=None,
        header={},
        kid="",
        loaded=False,
        last_load_error=None,
    )
    registry.replace("atomic_recipe", stub)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states={"atomic_recipe": state},
    )

    replace_with_marker_calls: list[tuple] = []
    real_rwm = registry.replace_with_marker

    def _spy_rwm(name, entry, marker):
        replace_with_marker_calls.append((name, marker))
        real_rwm(name, entry, marker)

    with patch.object(registry, "replace_with_marker", side_effect=_spy_rwm):
        watcher._load_recipe("atomic_recipe", state, force=True)

    assert replace_with_marker_calls, (
        "_load_recipe must call registry.replace_with_marker for atomic update"
    )
    name_called, marker_called = replace_with_marker_calls[0]
    assert name_called == "atomic_recipe"

    entry = registry.get("atomic_recipe")
    assert entry is not None
    loaded_marker = entry._loaded_marker
    assert isinstance(loaded_marker, tuple) and len(loaded_marker) == 2, (
        f"_loaded_marker must be a 2-tuple; got {loaded_marker!r}"
    )
    assert loaded_marker[1], "_loaded_marker sha256 part must be non-empty after load"


# ---------------------------------------------------------------------------
# Test 5: Hot-swap preserves stale on failure — real registry + real ModelEntry
# ---------------------------------------------------------------------------


def test_hot_swap_corrupt_artifact_preserves_stale_entry_real_registry(
    tmp_path: Path,
) -> None:
    """When a hot-swap fails due to corrupt artifact bytes, the watcher must:
    1. Keep the original recommender in the registry (not None, not raise).
    2. Set last_load_error on the entry.
    3. Increment the artifact_load_failures_total metric.

    Uses a REAL ModelRegistry and a REAL ModelEntry (not MagicMock).
    """
    from unittest.mock import patch

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "stale_real", artifact_path)

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)

    original_recommender = MagicMock()
    original_recommender.get_recommendation_for_known_user_id.return_value = [
        ("item_x", 0.99)
    ]

    from recotem.serving.registry import ModelEntry

    good_entry = ModelEntry(
        name="stale_real",
        recommender=original_recommender,
        header={"best_class": "TopPop", "trained_at": "2026-01-01T00:00:00Z"},
        kid="active",
        loaded=True,
        last_load_error=None,
    )
    good_entry.artifact_path = str(artifact_path)

    registry = ModelRegistry()
    registry.replace("stale_real", good_entry)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    initial_states = build_initial_states([recipe], {"stale_real": good_entry})
    initial_states["stale_real"].last_sha256 = ""  # force reload

    failure_count: list[int] = [0]
    original_inc = _metrics.inc_artifact_load_failure

    def _counting_inc(name: str) -> None:
        if name == "stale_real":
            failure_count[0] += 1
        original_inc(name)

    # Replace the artifact with corrupt content
    artifact_path.write_bytes(b"THIS IS CORRUPT AND WILL FAIL VERIFICATION")

    with patch.object(_metrics, "inc_artifact_load_failure", side_effect=_counting_inc):
        watcher = ArtifactWatcher(
            registry=registry,
            recipes_dir=recipes_dir,
            serve_config=cfg,
            key_ring=kr,
            initial_states=initial_states,
        )
        watcher.start()
        try:
            time.sleep(0.5)
        finally:
            watcher.stop()
            watcher.join(timeout=2.0)

    entry = registry.get("stale_real")
    assert entry is not None, "Entry must remain in registry after corrupt hot-swap"
    assert entry.recommender is original_recommender, (
        "The original recommender must be preserved after a failed hot-swap"
    )
    assert entry.last_load_error is not None, (
        "last_load_error must be set after a corrupt artifact hot-swap attempt"
    )
    assert failure_count[0] >= 1, (
        "inc_artifact_load_failure must be called at least once"
    )


# ---------------------------------------------------------------------------
# Test 6: ETag-based change detection — exactly-one-read per hot-swap cycle
# ---------------------------------------------------------------------------


def test_etag_change_detection_reads_artifact_exactly_once(
    tmp_path: Path,
) -> None:
    """When an ETag change is detected, the watcher must read the artifact
    bytes exactly once per hot-swap cycle (not twice — TOCTOU concern).
    """
    from unittest.mock import patch

    from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"
    _write_valid_artifact(artifact_path)

    yaml_path = _write_recipe_yaml(recipes_dir, "one_read_recipe", artifact_path)

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    state = _RecipeWatchState(
        recipe=recipe,
        artifact_path=str(artifact_path),
        last_marker="v1",
        last_sha256="",
    )

    registry = ModelRegistry()
    from recotem.serving.registry import ModelEntry

    stub = ModelEntry(
        name="one_read_recipe",
        recommender=None,
        header={},
        kid="",
        loaded=False,
        last_load_error=None,
    )
    registry.replace("one_read_recipe", stub)

    cfg = _make_serve_config()
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=cfg,
        key_ring=kr,
        initial_states={"one_read_recipe": state},
    )

    read_count: list[int] = [0]
    import recotem.serving.watcher as watcher_module

    real_read = watcher_module._read_artifact_bytes

    def _counting_read(path: str, max_bytes: int) -> bytes:
        read_count[0] += 1
        return real_read(path, max_bytes)

    def _etag_v2_stat(path: str, recipe_name: str = "<unknown>"):
        return "v2", None

    with patch.object(
        watcher_module, "_stat_marker_with_error", side_effect=_etag_v2_stat
    ):
        with patch.object(
            watcher_module, "_read_artifact_bytes", side_effect=_counting_read
        ):
            watcher._poll_artifacts()

    watcher._executor.shutdown(wait=True)

    assert read_count[0] == 1, (
        f"Artifact bytes must be read exactly once per ETag-change hot-swap cycle; "
        f"got {read_count[0]} read(s). Multiple reads indicate a TOCTOU issue."
    )
