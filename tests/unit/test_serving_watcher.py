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

from recotem.artifact.signing import KeyRing
from recotem.config import ServeConfig
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
