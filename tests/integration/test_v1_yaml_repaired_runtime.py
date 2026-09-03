# tests/integration/test_v1_yaml_repaired_runtime.py
"""Break then repair a loaded recipe's YAML → /v1/health/details recovers.

Scenario:
  1. Start serving with a recipes dir containing one valid recipe + loaded
     artifact.  GET /v1/health/details — assert 200 / "ok".
  2. Overwrite the recipe YAML with unparseable content.
  3. Wait for the watcher to rescan — assert 503 / "degraded" with the parse
     error surfaced (the model itself keeps serving, per the M-2 contract).
  4. Restore the original YAML.
  5. Wait for the watcher to rescan — assert /v1/health/details returns to
     200 / "ok" without a process restart.

Why: the recovery branch in _scan_recipes_dir only fired for YAML-failure
stubs, so an already-loaded recipe stayed degraded forever once its YAML had
been broken.  This exercises the whole loop — real watcher thread, real
endpoint — rather than the registry state alone.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from recotem.artifact.signing import KeyRing
from recotem.config import ServeConfig
from recotem.serving.registry import ModelRegistry
from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState
from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact, build_v1_app

WATCH_INTERVAL = 0.05  # seconds — must be fast for the test to be tractable
RECIPE_NAME = "yaml_repaired"


def _make_serve_config() -> ServeConfig:
    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.watch_interval = WATCH_INTERVAL
    cfg.max_artifact_bytes = 50 * 1024 * 1024
    return cfg


def _write_artifact(path: Path) -> None:
    """Write a minimal but valid signed artifact to *path*.

    Note: build_raw_artifact uses pickle internally (required by the artifact
    format — irspack uses scipy sparse matrices which require pickle).
    This is a test fixture using the same pattern as conftest.py.
    """
    import pickle  # noqa: S403  # test fixture: HMAC-signed artifact under test

    payload = pickle.dumps({"tag": "v1"}, protocol=4)  # noqa: S301
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


def _await_details(client: TestClient, want_status: int, timeout: float = 5.0):
    """Poll /v1/health/details until it returns *want_status*; return the response.

    Returns the last response either way so the caller can assert on the body
    of a timeout as well as of a success.
    """
    deadline = time.monotonic() + timeout
    response = client.get("/v1/health/details")
    while time.monotonic() < deadline and response.status_code != want_status:
        time.sleep(WATCH_INTERVAL)
        response = client.get("/v1/health/details")
    return response


def _await_first_load(registry: ModelRegistry, timeout: float = 5.0) -> bool:
    """Block until the watcher has loaded RECIPE_NAME for the first time.

    /v1/health/details answers 200 for an empty registry too, so polling the
    endpoint alone would let a test race past the initial load.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entry = registry.get(RECIPE_NAME)
        if entry is not None and entry.loaded and entry.last_load_error is None:
            return True
        time.sleep(WATCH_INTERVAL)
    return False


def test_yaml_repaired_at_runtime_restores_health_details(tmp_path: Path) -> None:
    """A repaired recipe YAML clears the parse error without a restart."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"

    _write_artifact(artifact_path)
    yaml_path = _write_recipe_yaml(recipes_dir, RECIPE_NAME, artifact_path)
    good_yaml = yaml_path.read_text()

    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    registry = ModelRegistry()
    cfg = _make_serve_config()

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)

    # Force initial load on first tick by using last_sha256="".
    initial_states: dict[str, _RecipeWatchState] = {
        RECIPE_NAME: _RecipeWatchState(
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
    try:
        client = TestClient(build_v1_app(registry), raise_server_exceptions=False)

        # --- Step 1: baseline — the artifact loads and health is ok ----------
        assert _await_first_load(registry), (
            "Watcher did not load the artifact within 5s"
        )
        baseline = client.get("/v1/health/details")
        assert baseline.status_code == 200, (
            f"A freshly loaded recipe must be healthy: {baseline.text}"
        )
        assert baseline.json()["status"] == "ok"

        # --- Step 2/3: break the YAML → degraded -----------------------------
        yaml_path.write_text(
            f"name: {RECIPE_NAME}\nsource:\n  type: csv\n  path: [unclosed\n"
        )
        broken = _await_details(client, 503)
        assert broken.status_code == 503, (
            f"Breaking the recipe YAML must degrade /v1/health/details; "
            f"got {broken.status_code}: {broken.text}"
        )
        broken_body = broken.json()
        assert broken_body["status"] == "degraded"
        broken_recipe = broken_body["recipes"][RECIPE_NAME]
        assert "parse error" in broken_recipe.get("error", "").lower(), (
            f"the parse failure must be surfaced; got {broken_recipe!r}"
        )
        assert broken_recipe["loaded"] is True, (
            "a parse error must not stop the loaded model from serving (M-2)"
        )

        # --- Step 4/5: repair the YAML → back to ok --------------------------
        yaml_path.write_text(good_yaml)
        repaired = _await_details(client, 200)
        assert repaired.status_code == 200, (
            f"Repairing the recipe YAML must restore /v1/health/details without "
            f"a restart; got {repaired.status_code}: {repaired.text}"
        )
        repaired_body = repaired.json()
        assert repaired_body["status"] == "ok"
        assert "error" not in repaired_body["recipes"][RECIPE_NAME], (
            f"the stale parse error must be retracted; got "
            f"{repaired_body['recipes'][RECIPE_NAME]!r}"
        )
    finally:
        watcher.stop()
        watcher.join(timeout=3.0)


def test_yaml_reparse_does_not_clear_artifact_load_error(tmp_path: Path) -> None:
    """An artifact failure stays visible even though the YAML parses fine.

    The negative half of the recovery contract: ``last_load_error`` is shared
    with the artifact-load paths, so a rescan that parses the YAML must not be
    read as evidence that the artifact is healthy again.

    The watcher thread is stopped before the deciding rescans.  A failed load
    never advances ``last_marker``, so the poll loop re-writes the artifact
    error on every tick — which would mask a wrongly-cleared error a few
    microseconds later and make the assertion meaningless.  Driving
    ``_scan_recipes_dir`` directly isolates the rescan path.
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    artifact_path = tmp_path / "model.recotem"

    _write_artifact(artifact_path)
    yaml_path = _write_recipe_yaml(recipes_dir, RECIPE_NAME, artifact_path)

    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    registry = ModelRegistry()
    cfg = _make_serve_config()

    from recotem.recipe.loader import load_recipe

    recipe = load_recipe(yaml_path)
    initial_states: dict[str, _RecipeWatchState] = {
        RECIPE_NAME: _RecipeWatchState(
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
    try:
        client = TestClient(build_v1_app(registry), raise_server_exceptions=False)
        assert _await_first_load(registry), (
            "Watcher did not load the artifact within 5s"
        )

        # Corrupt the artifact in place: the pointer changes, so the next tick
        # reloads it and the HMAC check fails.  The YAML is left untouched and
        # keeps parsing on every rescan.
        artifact_path.write_bytes(b"not a recotem artifact")
        degraded = _await_details(client, 503)
        assert degraded.status_code == 503, (
            f"A corrupt artifact must degrade /v1/health/details; "
            f"got {degraded.status_code}: {degraded.text}"
        )
        artifact_error = degraded.json()["recipes"][RECIPE_NAME].get("error")
        assert artifact_error, "the artifact failure must be surfaced"
    finally:
        watcher.stop()
        watcher.join(timeout=3.0)

    # Several further rescans, each of which parses the YAML successfully.
    assert yaml_path.exists()
    watcher._scan_recipes_dir()
    watcher._scan_recipes_dir()

    still = client.get("/v1/health/details")
    assert still.status_code == 503, (
        f"A successful YAML reparse must not clear an artifact load error; "
        f"got {still.status_code}: {still.text}"
    )
    assert still.json()["recipes"][RECIPE_NAME].get("error") == artifact_error, (
        "the artifact failure must still be reported verbatim after the YAML reparses"
    )
