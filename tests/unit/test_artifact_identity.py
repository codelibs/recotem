"""Tests for the recipe-name binding gate and its wiring into both load paths.

Before this gate existed, a correctly-signed artifact whose header said
``recipe_name: "other"`` loaded and served under recipe ``demo`` with no
signal anywhere: the swap logged at INFO, ``/v1/health/details`` said ``ok``,
and ``/v1/recipes/demo`` reported the other recipe's ``best_class`` and
``recipe_hash``.  Four recipes pointing at one artifact all reported success.

The gate is only useful if BOTH load paths call it — serve's startup loader in
``app.py`` and the watcher's hot-swap in ``watcher.py`` duplicate the same
header-decode sequence — and if its refusal is classified and counted rather
than swallowed by a neighbouring branch.  These tests pin all of that.
"""

from __future__ import annotations

import time
import types
from pathlib import Path

import pytest
import structlog.testing

from recotem._artifact_identity import (
    RECIPE_NAME_MSG_PREFIX,
    check_artifact_recipe_name,
)
from recotem.artifact.format import ArtifactError
from recotem.artifact.signing import KeyRing
from recotem.config import ServeConfig
from recotem.serving.app import _try_load_artifact
from recotem.serving.metrics import _LOAD_FAILURE_REASONS
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.watcher import (
    ArtifactWatcher,
    _classify_artifact_error,
    _RecipeWatchState,
)
from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact

# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------


def test_matching_name_passes() -> None:
    check_artifact_recipe_name({"recipe_name": "demo"}, name="demo")


def test_mismatched_name_refuses() -> None:
    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_recipe_name({"recipe_name": "other"}, name="demo")
    msg = str(excinfo.value)
    assert "demo" in msg, f"refusal must name the loading recipe; got {msg!r}"
    assert "other" in msg, f"refusal must name the artifact's recipe; got {msg!r}"
    assert "output.path" in msg, f"refusal must name the remedy; got {msg!r}"


def test_absent_name_fails_open() -> None:
    """A pre-2.0 artifact carries no ``recipe_name``; that is not evidence.

    Same posture ``check_artifact_irspack_version`` takes on an absent
    ``irspack_version``: nothing to compare, so do not refuse.
    """
    with structlog.testing.capture_logs() as cap:
        check_artifact_recipe_name({"best_class": "TopPop"}, name="demo")
    events = [line["event"] for line in cap]
    assert "artifact_recipe_name_absent_from_header" in events, (
        f"fail-open must leave a warning behind; got {events!r}"
    )


def test_non_string_name_refuses_and_is_reported_by_type() -> None:
    """Present-but-unusable is present, so it fails closed like a wrong name.

    The value is reported by type rather than quoted: a header carrying a
    non-string here was not written by this trainer, and the value could be an
    arbitrarily large nested structure.
    """
    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_recipe_name({"recipe_name": {"nested": "x"}}, name="demo")
    assert "dict" in str(excinfo.value)


def test_refusal_survives_health_error_truncation() -> None:
    """Both names must fit the 200-char ``last_load_error`` budget.

    ``sanitize_load_error`` truncates to 200 chars, which is what
    ``/v1/health/details`` exposes.  A message that buries the two names past
    that point is useless exactly where an operator reads it.
    """
    from recotem.serving.registry import sanitize_load_error

    long_a, long_b = "a" * 64, "b" * 64
    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_recipe_name({"recipe_name": long_b}, name=long_a)
    surfaced = sanitize_load_error(str(excinfo.value))
    assert long_a in surfaced, "loading recipe's name truncated away"
    assert long_b in surfaced, "artifact's recipe name truncated away"


# ---------------------------------------------------------------------------
# Classification / metric wiring
# ---------------------------------------------------------------------------


def _refusal_message() -> str:
    """A real refusal, produced by the gate rather than hand-written."""
    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_recipe_name({"recipe_name": "other"}, name="demo")
    return str(excinfo.value)


def test_refusal_classifies_as_recipe_name() -> None:
    assert _classify_artifact_error(_refusal_message()) == "recipe_name"


def test_recipe_name_is_an_allowed_metric_label() -> None:
    """Otherwise inc_artifact_load_failure silently coerces it to "unexpected"."""
    assert "recipe_name" in _LOAD_FAILURE_REASONS


def test_classifier_prefix_matches_gate_prefix() -> None:
    """The classifier keys off the gate's prefix; keep them in sync."""
    assert _refusal_message().lower().startswith(RECIPE_NAME_MSG_PREFIX.lower())


# ---------------------------------------------------------------------------
# Startup path (app.py)
# ---------------------------------------------------------------------------


def _startup_load(tmp_path: Path, make_artifact, key_ring, header: dict, name: str):
    """Run serve's startup loader over an artifact carrying *header*."""
    path = tmp_path / f"{name}.recotem"
    path.write_bytes(make_artifact(header_dict=header))
    recipe = types.SimpleNamespace(
        name=name,
        output=types.SimpleNamespace(path=str(path)),
        item_metadata=None,
    )
    return _try_load_artifact(recipe, key_ring, ServeConfig())


def test_startup_path_refuses_mismatched_recipe_name(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    entry, reason = _startup_load(
        tmp_path,
        make_artifact,
        single_key_ring,
        {"recipe_name": "other", "best_class": "TopPopRecommender"},
        name="demo",
    )
    assert reason == "recipe_name", (
        f"startup must report the binding failure, not a neighbouring "
        f"category; got {reason!r}"
    )
    assert entry.loaded is False
    assert "other" in (entry.last_load_error or "")


def test_startup_path_loads_matching_recipe_name(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    entry, reason = _startup_load(
        tmp_path,
        make_artifact,
        single_key_ring,
        {"recipe_name": "demo", "best_class": "TopPopRecommender"},
        name="demo",
    )
    assert reason == "ok", f"a matching name must still load; got {reason!r}"
    assert entry.loaded is True


def test_startup_path_loads_artifact_without_recipe_name(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """Fail-open reaches the startup path too, not just the gate in isolation."""
    entry, reason = _startup_load(
        tmp_path,
        make_artifact,
        single_key_ring,
        {"best_class": "TopPopRecommender"},
        name="demo",
    )
    assert reason == "ok", f"a header with no recipe_name must load; got {reason!r}"
    assert entry.loaded is True


# ---------------------------------------------------------------------------
# Watcher path (watcher.py)
# ---------------------------------------------------------------------------


def _write_artifact(path: Path, recipe_name: str, tag: str) -> None:
    import pickle  # noqa: S403  # test fixture: payload built locally

    payload = pickle.dumps({"tag": tag}, protocol=4)  # noqa: S301
    path.write_bytes(
        build_raw_artifact(
            kid="active",
            key_hex=ACTIVE_KEY_HEX,
            header_dict={
                "recipe_name": recipe_name,
                "best_class": "TopPop",
                "trained_at": "2026-01-01T00:00:00Z",
            },
            payload_bytes=payload,
        )
    )


def _write_recipe_yaml(recipes_dir: Path, name: str, artifact_path: Path) -> Path:
    yaml_path = recipes_dir / f"{name}.yaml"
    yaml_path.write_text(
        f"""\
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
    )
    return yaml_path


def _make_serve_config() -> ServeConfig:
    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.watch_interval = 0.05
    cfg.max_artifact_bytes = 100 * 1024 * 1024
    return cfg


def _build_watcher(tmp_path: Path, name: str, artifact_path: Path):
    """Watcher over one recipe, primed to load on its first tick."""
    from recotem.recipe.loader import load_recipe

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir(exist_ok=True)
    yaml_path = _write_recipe_yaml(recipes_dir, name, artifact_path)

    registry = ModelRegistry()
    # Serve registers a stub before the first load attempt so a failure has an
    # entry to attach ``last_load_error`` to; mirror that here.
    registry.replace(
        name,
        ModelEntry(
            name=name,
            recommender=None,
            header={},
            kid="",
            artifact_path=str(artifact_path),
            loaded=False,
        ),
    )
    states = {
        name: _RecipeWatchState(
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
    return watcher, registry, states


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_watcher_path_refuses_mismatched_recipe_name(tmp_path: Path) -> None:
    artifact_path = tmp_path / "model.recotem"
    _write_artifact(artifact_path, recipe_name="other", tag="v1")
    watcher, registry, _ = _build_watcher(tmp_path, "demo", artifact_path)

    watcher.start()
    try:
        got_error = _wait_until(
            lambda: (e := registry.get("demo")) is not None and e.last_load_error
        )
    finally:
        watcher.stop()
        watcher.join(timeout=3.0)

    assert got_error, "watcher must record the binding failure within 3s"
    entry = registry.get("demo")
    assert entry is not None
    assert entry.loaded is False
    assert "other" in (entry.last_load_error or "")


def test_watcher_path_loads_matching_recipe_name(tmp_path: Path) -> None:
    artifact_path = tmp_path / "model.recotem"
    _write_artifact(artifact_path, recipe_name="demo", tag="v1")
    watcher, registry, _ = _build_watcher(tmp_path, "demo", artifact_path)

    watcher.start()
    try:
        loaded = _wait_until(
            lambda: (
                (e := registry.get("demo")) is not None
                and e.loaded
                and e.last_load_error is None
            )
        )
    finally:
        watcher.stop()
        watcher.join(timeout=3.0)

    assert loaded, "a matching name must still hot-load within 3s"
    assert registry.get("demo").recommender == {"tag": "v1"}


def test_mismatched_hot_swap_keeps_previous_model_and_degrades_health(
    tmp_path: Path,
) -> None:
    """The whole point of routing the refusal through ``ArtifactError``.

    A hot-swap that fails must leave the previously-loaded model in place —
    availability is not sacrificed to reject the wrong file — while
    ``/v1/health/details`` flips to ``degraded`` so the condition is visible
    rather than silent, which is the defect this gate closes.
    """
    from fastapi.testclient import TestClient

    from tests.conftest import build_v1_app

    artifact_path = tmp_path / "model.recotem"
    _write_artifact(artifact_path, recipe_name="demo", tag="good")
    watcher, registry, states = _build_watcher(tmp_path, "demo", artifact_path)

    client = TestClient(build_v1_app(registry))

    watcher.start()
    try:
        assert _wait_until(
            lambda: (e := registry.get("demo")) is not None and e.loaded
        ), "precondition: the matching artifact must load first"
        assert client.get("/v1/health/details").json()["status"] == "ok"
        good_model = registry.get("demo").recommender

        # Overwrite with an artifact trained for a different recipe — the
        # "copied the recipe, forgot to change output.path" scenario.
        _write_artifact(artifact_path, recipe_name="other", tag="wrong")
        assert _wait_until(
            lambda: (e := registry.get("demo")) is not None and e.last_load_error
        ), "watcher must reject the crossed artifact within 3s"
    finally:
        watcher.stop()
        watcher.join(timeout=3.0)

    entry = registry.get("demo")
    assert entry.loaded is True, "a refused swap must not take the model out of service"
    assert entry.recommender is good_model, (
        f"the previous model must still be the one served; got {entry.recommender!r}"
    )

    details = client.get("/v1/health/details")
    assert details.status_code == 503
    body = details.json()
    assert body["status"] == "degraded", (
        f"a refused swap must be visible in health, not silent; got {body!r}"
    )
    assert "other" in body["recipes"]["demo"]["error"]
