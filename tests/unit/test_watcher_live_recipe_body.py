"""What the watcher reads out of a recipe once the file on disk has changed.

``_RecipeWatchState.recipe`` was the body parsed at process start.  Nothing
refreshed it: the two assignments that existed sat inside YAML-error-recovery
branches, so a recipe that always parsed cleanly kept its startup body for the
life of the process.  Three things read that body and wanted the current one:

* ``item_metadata`` -- ``_build_entry`` joins the response against the table the
  *startup* body named, so an operator who repointed ``item_metadata.path`` and
  retrained got the new model joined onto the old table, silently;
* ``output.path`` -- captured once into ``state.artifact_path`` at discovery, so
  an edited output path was ignored and the watcher kept polling the old file
  forever; and
* the ``.sha256`` sidecar suppression latch, whose documented "clear it when the
  recipe changes" recovery was reached through
  ``getattr(recipe, "_yaml_path", None)`` -- an attribute ``Recipe`` does not
  have and, with ``extra="forbid"`` and no private attributes, cannot have.

All three shipped in 2.0.0.  These tests edit the YAML *under a running
watcher*, which is the only way the startup body and the current one differ.
"""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path
from unittest.mock import patch

import structlog.testing

from recotem.artifact.signing import KeyRing
from recotem.config import ServeConfig
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.watcher import (
    ArtifactWatcher,
    _check_sidecar_changed,
    _RecipeWatchState,
)
from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_artifact(
    path: Path, recipe_name: str, tag: str, recipe_hash: str | None = None
) -> None:
    import pickle  # noqa: S403  # test fixture: payload built locally

    payload = pickle.dumps({"tag": tag}, protocol=4)  # noqa: S301
    header: dict = {
        "recipe_name": recipe_name,
        "best_class": "TopPop",
        "trained_at": "2026-01-01T00:00:00Z",
    }
    if recipe_hash is not None:
        header["recipe_hash"] = recipe_hash
    path.write_bytes(
        build_raw_artifact(
            kid="active",
            key_hex=ACTIVE_KEY_HEX,
            header_dict=header,
            payload_bytes=payload,
        )
    )


def _recipe_text(
    *, output_path: Path | str, metadata_path: Path | str | None = None
) -> str:
    metadata_block = (
        ""
        if metadata_path is None
        else f"""\
item_metadata:
  type: csv
  path: {metadata_path}
  fields: [title]
  on_field_missing: error
"""
    )
    return f"""\
name: demo
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
{metadata_block}output:
  path: {output_path}
"""


def _rewrite(yaml_path: Path, text: str) -> None:
    """Rewrite the recipe and push its mtime forward.

    The watcher re-parses a recipe only when ``st_mtime`` differs from the value
    it cached (``_yaml_mtime_cache``).  Two writes inside one mtime tick are
    invisible to it; local filesystems here resolve consecutive writes but a CI
    runner's need not, and a test that leans on that resolution fails there and
    nowhere else.  Bump it explicitly.
    """
    yaml_path.write_text(text)
    bumped = os.stat(yaml_path).st_mtime + 10
    os.utime(yaml_path, (bumped, bumped))


def _make_serve_config() -> ServeConfig:
    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.watch_interval = 0.05
    cfg.max_artifact_bytes = 100 * 1024 * 1024
    return cfg


def _build_watcher(tmp_path: Path, yaml_text: str, artifact_path: Path):
    """A watcher over one recipe, primed to load on its first tick."""
    from recotem.recipe.loader import load_recipe

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir(exist_ok=True)
    yaml_path = recipes_dir / "demo.yaml"
    yaml_path.write_text(yaml_text)

    registry = ModelRegistry()
    registry.replace(
        "demo",
        ModelEntry(
            name="demo",
            recommender=None,
            header={},
            kid="",
            artifact_path=str(artifact_path),
            loaded=False,
        ),
    )
    states = {
        "demo": _RecipeWatchState(
            recipe=load_recipe(yaml_path),
            artifact_path=str(artifact_path),
            last_sha256="",
            last_marker=None,
        )
    }
    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=_make_serve_config(),
        key_ring=KeyRing(f"active:{ACTIVE_KEY_HEX}"),
        initial_states=states,
    )
    return watcher, registry, states, yaml_path


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _titles(registry: ModelRegistry) -> list:
    entry = registry.get("demo")
    if entry is None or entry.metadata_index is None:
        return []
    return [v.get("title") for v in entry.metadata_index.values()]


# ---------------------------------------------------------------------------
# item_metadata
# ---------------------------------------------------------------------------


def test_item_metadata_follows_the_edited_recipe_after_a_retrain(
    tmp_path: Path,
) -> None:
    """Repoint ``item_metadata.path``, retrain, and the join must follow.

    This is the ordinary operator move -- edit the recipe and retrain -- and it
    is exactly the case where the startup body is provably the wrong one: the
    artifact that swaps in was trained FROM the edited recipe, so the recipe on
    disk and the body the model was built from are the same body, and the
    startup body is neither.  The hashes agree, so the drift warning stays
    silent and nothing else marks the mismatch: the response is the new model
    joined onto the old table.
    """
    from recotem._recipe_hash import compute_recipe_hash
    from recotem.recipe.loader import load_recipe

    old_meta = tmp_path / "meta_old.csv"
    old_meta.write_text("item_id,title\ni1,OLD_TITLE\n")
    new_meta = tmp_path / "meta_new.csv"
    new_meta.write_text("item_id,title\ni1,NEW_TITLE\n")

    artifact_path = tmp_path / "model.recotem"
    watcher, registry, _, yaml_path = _build_watcher(
        tmp_path,
        _recipe_text(output_path=artifact_path, metadata_path=old_meta),
        artifact_path,
    )
    startup_hash = compute_recipe_hash(load_recipe(yaml_path))
    _write_artifact(
        artifact_path, recipe_name="demo", tag="v1", recipe_hash=startup_hash
    )

    with structlog.testing.capture_logs() as logs:
        watcher.start()
        try:
            assert _wait_until(
                lambda: (e := registry.get("demo")) is not None and e.loaded
            ), "precondition: the artifact must load"
            assert _titles(registry) == ["OLD_TITLE"], (
                "precondition: the startup body's metadata table must be the one "
                f"loaded; got {_titles(registry)}"
            )

            # The operator repoints item_metadata AND retrains, so the new
            # artifact carries the hash of the recipe as edited.
            _rewrite(
                yaml_path,
                _recipe_text(output_path=artifact_path, metadata_path=new_meta),
            )
            edited_hash = compute_recipe_hash(load_recipe(yaml_path))
            _n = len(logs)
            assert _wait_until(
                lambda: any(r.get("event") == "recipe_loaded" for r in logs[_n:])
            ), "the watcher must re-scan the edited YAML"

            _write_artifact(
                artifact_path, recipe_name="demo", tag="v2", recipe_hash=edited_hash
            )
            assert _wait_until(
                lambda: (
                    (e := registry.get("demo")) is not None
                    and e.recommender == {"tag": "v2"}
                )
            ), "the retrained artifact must hot-swap in"
        finally:
            watcher.stop()
            watcher.join(timeout=5.0)

    assert _titles(registry) == ["NEW_TITLE"], (
        "the artifact that just swapped in was trained FROM the edited recipe, "
        "yet the metadata join still uses the table the STARTUP body named. The "
        f"response is the new model on the old table. Titles: {_titles(registry)}"
    )
    warned = [r for r in logs if r.get("event") == "artifact_recipe_hash_mismatch"]
    assert not warned, (
        "control: artifact and recipe agree here, so nothing may warn -- the "
        f"stale join is silent, which is the point. Emitted: {warned}"
    )


def test_item_metadata_is_not_reloaded_without_a_hot_swap(tmp_path: Path) -> None:
    """Control for the test above: an edit alone must not move the join.

    Metadata is read where the model is built, so an edit with no retrain
    leaves the old model AND the old table in place -- which is the coherent
    answer, and the drift warning is what says the recipe has moved on.  Without
    this arm, "the join followed the edit" above could as easily mean "the join
    re-reads on every tick", which would be a different behaviour entirely.
    """
    from recotem._recipe_hash import compute_recipe_hash
    from recotem.recipe.loader import load_recipe

    old_meta = tmp_path / "meta_old.csv"
    old_meta.write_text("item_id,title\ni1,OLD_TITLE\n")
    new_meta = tmp_path / "meta_new.csv"
    new_meta.write_text("item_id,title\ni1,NEW_TITLE\n")

    artifact_path = tmp_path / "model.recotem"
    watcher, registry, _, yaml_path = _build_watcher(
        tmp_path,
        _recipe_text(output_path=artifact_path, metadata_path=old_meta),
        artifact_path,
    )
    startup_hash = compute_recipe_hash(load_recipe(yaml_path))
    _write_artifact(
        artifact_path, recipe_name="demo", tag="v1", recipe_hash=startup_hash
    )

    with structlog.testing.capture_logs() as logs:
        watcher.start()
        try:
            assert _wait_until(
                lambda: (e := registry.get("demo")) is not None and e.loaded
            ), "precondition: the artifact must load"

            _rewrite(
                yaml_path,
                _recipe_text(output_path=artifact_path, metadata_path=new_meta),
            )
            _n = len(logs)
            assert _wait_until(
                lambda: any(r.get("event") == "recipe_loaded" for r in logs[_n:])
            ), "the watcher must re-scan the edited YAML"
            # Several further ticks, with no new artifact.
            time.sleep(0.5)
        finally:
            watcher.stop()
            watcher.join(timeout=5.0)

    assert _titles(registry) == ["OLD_TITLE"], (
        "no artifact changed, so the served model is still the old one and its "
        f"metadata must be too; got {_titles(registry)}"
    )


# ---------------------------------------------------------------------------
# output.path
# ---------------------------------------------------------------------------


def test_an_edited_output_path_is_followed_to_the_new_artifact(
    tmp_path: Path,
) -> None:
    """``output.path`` is where train writes; serve must poll where it points now.

    ``state.artifact_path`` was taken from ``recipe.output.path`` once, at
    discovery.  Editing it under a running server was ignored for the life of
    the process -- and silently: the drift warning cannot cover this, because it
    is emitted from the load path and no load ever happens at the new location.
    """
    old_path = tmp_path / "old.recotem"
    new_path = tmp_path / "new.recotem"
    _write_artifact(old_path, recipe_name="demo", tag="v1")

    watcher, registry, states, yaml_path = _build_watcher(
        tmp_path, _recipe_text(output_path=old_path), old_path
    )

    watcher.start()
    try:
        assert _wait_until(
            lambda: (
                (e := registry.get("demo")) is not None
                and e.recommender == {"tag": "v1"}
            )
        ), "precondition: the artifact at the original path must load"

        _write_artifact(new_path, recipe_name="demo", tag="NEWPATH")
        _rewrite(yaml_path, _recipe_text(output_path=new_path))

        followed = _wait_until(
            lambda: (
                (e := registry.get("demo")) is not None
                and e.recommender == {"tag": "NEWPATH"}
            )
        )
    finally:
        watcher.stop()
        watcher.join(timeout=5.0)

    assert followed, (
        "output.path was repointed under a running watcher and the artifact at "
        "the new path never loaded: the watcher is still polling "
        f"{states['demo'].artifact_path!r}, the path captured at discovery."
    )
    assert states["demo"].artifact_path == str(new_path)
    assert registry.get("demo").artifact_path == str(new_path)


def test_repointing_output_path_at_a_missing_file_keeps_the_model_serving(
    tmp_path: Path,
) -> None:
    """The half of following an edit that must not become an outage.

    An operator repoints ``output.path`` and has not retrained yet, so nothing
    is there.  Following the edit must degrade health -- the recipe now names a
    file that does not exist -- without evicting the model that is serving,
    which is the same contract every other missing artifact gets (M-2).
    """
    old_path = tmp_path / "old.recotem"
    missing = tmp_path / "not-trained-yet.recotem"
    _write_artifact(old_path, recipe_name="demo", tag="v1")

    watcher, registry, states, yaml_path = _build_watcher(
        tmp_path, _recipe_text(output_path=old_path), old_path
    )

    watcher.start()
    try:
        assert _wait_until(
            lambda: (
                (e := registry.get("demo")) is not None
                and e.recommender == {"tag": "v1"}
            )
        ), "precondition: the artifact at the original path must load"

        _rewrite(yaml_path, _recipe_text(output_path=missing))

        degraded = _wait_until(
            lambda: (e := registry.get("demo")) is not None and e.last_load_error
        )
    finally:
        watcher.stop()
        watcher.join(timeout=5.0)

    assert degraded, (
        "output.path now names a file that does not exist and nothing was "
        "recorded against the entry: the watcher is still polling the old path "
        f"({states['demo'].artifact_path!r}) and reporting success for it."
    )
    entry = registry.get("demo")
    assert entry.loaded is True, "the model that is serving must not be evicted"
    assert entry.recommender == {"tag": "v1"}


# ---------------------------------------------------------------------------
# the .sha256 sidecar suppression latch
# ---------------------------------------------------------------------------


def _latch_sidecar_unsupported(state: _RecipeWatchState) -> None:
    """Drive the real latching path: three consecutive non-ENOENT read errors."""
    perm_error = PermissionError("permission denied")
    perm_error.errno = errno.EACCES
    with patch.object(Path, "read_text", side_effect=perm_error):
        for _ in range(3):
            _check_sidecar_changed(state)


def test_sidecar_suppression_clears_when_the_recipe_yaml_is_reparsed(
    tmp_path: Path,
) -> None:
    """The documented C4 recovery, against a real ``Recipe``.

    Three unreadable sidecar reads latch ``sidecar_unsupported`` so a broken
    sidecar cannot drive a reload every tick.  Nothing else clears it, so the
    latch is permanent -- and the sidecar is the only backstop the watcher has
    when the change marker cannot discriminate (an ``append_sha`` pointer file
    is a constant 22 bytes, so ``(mtime, size)`` degenerates to mtime alone).
    The documented escape is "the recipe YAML changed, re-evaluate", reached via
    ``getattr(state.recipe, "_yaml_path", None)``, which is ``None`` for every
    ``Recipe`` that has ever existed.
    """
    artifact_path = tmp_path / "model.recotem"
    _write_artifact(artifact_path, recipe_name="demo", tag="v1")
    sidecar = Path(str(artifact_path) + ".sha256")
    sidecar.write_text("sha_v1\n")

    watcher, _registry, states, yaml_path = _build_watcher(
        tmp_path, _recipe_text(output_path=artifact_path), artifact_path
    )
    state = states["demo"]
    state.last_sidecar_contents = "sha_v1\n"
    # Warm the YAML mtime cache the way a running watcher's first tick does, so
    # the scans below are the steady-state "nothing changed" case.
    watcher._scan_recipes_dir()

    _latch_sidecar_unsupported(state)
    assert state.sidecar_unsupported is True, (
        "precondition: three EACCES sidecar reads must latch the suppression"
    )

    # Control: scans that do NOT re-parse (the YAML has not changed) must leave
    # the latch alone -- otherwise every tick would clear it and the
    # reload-storm guard the latch exists for would be gone.
    watcher._scan_recipes_dir()
    watcher._scan_recipes_dir()
    assert state.sidecar_unsupported is True, (
        "an unchanged YAML must not clear the latch; the guard would be useless"
    )
    sidecar.write_text("sha_v2\n")
    assert _check_sidecar_changed(state) is False, (
        "control: while latched, a readable and genuinely CHANGED sidecar is "
        "ignored -- this is the backstop that is off"
    )

    # The operator edits the recipe.  That is the documented signal to
    # re-evaluate -- and after it the sidecar path itself may have moved,
    # because it is derived from output.path.
    _rewrite(yaml_path, _recipe_text(output_path=artifact_path) + "# edited\n")
    watcher._scan_recipes_dir()

    assert state.sidecar_unsupported is False, (
        "the recipe YAML was re-parsed and the suppression was not cleared: the "
        "documented recovery reads Recipe._yaml_path, which does not exist, so "
        "the latch is permanent and the sidecar backstop is off until restart."
    )
    assert _check_sidecar_changed(state) is True, (
        "with the latch cleared, the sidecar change made above must be seen"
    )
    assert _check_sidecar_changed(state) is False, (
        "control: and the call after it must answer False -- the True above is "
        "a change signal, not an unconditional yes"
    )
