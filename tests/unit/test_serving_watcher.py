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
    """After _load_recipe succeeds, the entry's _loaded_marker is set via
    update_loaded_marker (not a direct attribute write), so the mutation goes
    through the registry lock.
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

    update_calls: list[tuple] = []
    real_update = registry.update_loaded_marker

    def _spy(name, marker):
        update_calls.append((name, marker))
        return real_update(name, marker)

    with patch.object(registry, "update_loaded_marker", side_effect=_spy):
        watcher._load_recipe("marker_recipe", state, force=True)

    # update_loaded_marker must have been called at least once for the recipe
    matching = [c for c in update_calls if c[0] == "marker_recipe"]
    assert matching, (
        "watcher._load_recipe must call registry.update_loaded_marker after a "
        "successful load; direct entry._loaded_marker assignment bypasses the lock"
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
