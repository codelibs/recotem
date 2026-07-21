"""Tests for the artifact feature-encoder version gate (Task 10).

``check_artifact_feature_version`` closes a payload-shape gap
``_irspack_compat`` does not cover: recotem has no ``recotem_version`` gate at
serve time, so this descriptor is the only thing standing between a shape
change in the feature-encoder state and silently wrong recommendations (a
request's features encoded into the wrong vector space).

The wiring tests below follow the shape of
``tests/unit/test_irspack_compat_wiring.py``: the gate is only useful if it is
actually reached from BOTH load paths -- ``app.py``'s startup loader and
``watcher.py``'s hot-swap loader -- and its ``ArtifactError`` is classified
under its own ``"feature_version"`` reason rather than falling into a
neighbouring bucket (the message contains the word "version", so it would
otherwise be swallowed by the "parse" catch-all).
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import structlog.testing

from recotem._features import (
    FEATURE_VERSION_MSG_PREFIX,
    check_artifact_feature_version,
)
from recotem.artifact.format import ArtifactError
from recotem.config import ServeConfig
from recotem.serving.app import _try_load_artifact
from recotem.serving.metrics import _LOAD_FAILURE_REASONS
from recotem.serving.registry import ModelRegistry
from recotem.serving.watcher import (
    ArtifactWatcher,
    _classify_artifact_error,
    _RecipeWatchState,
)

# ---------------------------------------------------------------------------
# Gate logic
# ---------------------------------------------------------------------------


def test_absent_features_key_passes() -> None:
    """Old artifact or a non-feature model: nothing to gate."""
    check_artifact_feature_version({"recipe_name": "r"}, name="r")


def test_known_version_passes() -> None:
    check_artifact_feature_version(
        {"features": {"version": 1, "item": {"n_features": 3}}}, name="r"
    )


def test_newer_version_refused() -> None:
    with pytest.raises(ArtifactError, match="feature encoder version"):
        check_artifact_feature_version({"features": {"version": 2}}, name="r")


def test_non_int_version_refused() -> None:
    with pytest.raises(ArtifactError):
        check_artifact_feature_version({"features": {"version": "1"}}, name="r")


def test_bool_version_refused() -> None:
    """``isinstance(True, int)`` is True in Python; the guard must exclude bools."""
    with pytest.raises(ArtifactError):
        check_artifact_feature_version({"features": {"version": True}}, name="r")


def test_missing_version_refused() -> None:
    """A features block with no version is malformed -- fail closed."""
    with pytest.raises(ArtifactError):
        check_artifact_feature_version({"features": {"item": {}}}, name="r")


def test_non_dict_features_refused() -> None:
    with pytest.raises(ArtifactError):
        check_artifact_feature_version({"features": "nope"}, name="r")


# ---------------------------------------------------------------------------
# Classification -- mirrors test_irspack_compat_wiring.py's structure
# ---------------------------------------------------------------------------


def _feature_version_message() -> str:
    """Return a real refusal message, produced by the guard itself.

    Built from the guard rather than hand-written so the test cannot drift
    away from the wording the guard actually emits.
    """
    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_feature_version({"features": {"version": 2}}, name="news")
    return str(excinfo.value)


def test_feature_version_message_classifies_as_feature_version() -> None:
    assert _classify_artifact_error(_feature_version_message()) == "feature_version"


def test_feature_version_message_is_not_misclassified_as_parse() -> None:
    """Regression: the "parse" branch claims any message containing "version".

    The refusal message contains "feature encoder version 2", so ordering in
    ``_classify_artifact_error`` is load-bearing, exactly as it is for the
    irspack skew guard's message.
    """
    msg = _feature_version_message()
    assert "version" in msg.lower(), "precondition: message contains 'version'"
    assert _classify_artifact_error(msg) != "parse"


def test_feature_version_is_an_allowed_metric_label() -> None:
    """Otherwise inc_artifact_load_failure silently coerces it to "unexpected"."""
    assert "feature_version" in _LOAD_FAILURE_REASONS


def test_classifier_prefix_matches_guard_prefix() -> None:
    """The classifier keys off the guard's prefix; keep them in sync."""
    assert (
        _feature_version_message()
        .lower()
        .startswith(FEATURE_VERSION_MSG_PREFIX.lower())
    )


# ---------------------------------------------------------------------------
# Startup path (app.py) -- its reason is a hardcoded literal, not classified
# ---------------------------------------------------------------------------

_REFUSED_HEADER = {
    "recipe_name": "news",
    "best_class": "TopPopRecommender",
    "trained_at": "2026-01-01T00:00:00Z",
    "features": {"version": 2},
}


def _load_with_header(tmp_path: Path, make_artifact, key_ring, header: dict):
    """Run serve's startup loader over an artifact carrying *header*."""
    data = make_artifact(header_dict=header)
    path = tmp_path / "feature_version.recotem"
    path.write_bytes(data)
    recipe = types.SimpleNamespace(
        name="news",
        output=types.SimpleNamespace(path=str(path)),
        item_metadata=None,
    )
    return _try_load_artifact(recipe, key_ring, ServeConfig())


def test_startup_path_reports_feature_version(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    entry, reason = _load_with_header(
        tmp_path, make_artifact, single_key_ring, dict(_REFUSED_HEADER)
    )
    assert reason == "feature_version"
    assert entry.loaded is False
    assert "feature encoder version" in (entry.last_load_error or "").lower()


def test_startup_path_loads_when_feature_version_matches(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """Positive control: a matching feature version must load, not just fail
    to be refused.

    Without this, a gate that unconditionally refused every artifact would
    still pass ``test_startup_path_reports_feature_version`` above.
    """
    header = dict(_REFUSED_HEADER)
    header["features"] = {"version": 1}
    entry, reason = _load_with_header(tmp_path, make_artifact, single_key_ring, header)
    assert reason == "ok", f"matching feature version must load; got {reason!r}"
    assert entry.loaded is True


# ---------------------------------------------------------------------------
# Hot-swap path (watcher.py) -- a gate wired into only ONE of app.py/watcher.py
# is a half-fix that a naive test (covering app.py alone) would not catch.
# ---------------------------------------------------------------------------


def _make_watcher_serve_config() -> ServeConfig:
    cfg = ServeConfig()
    cfg.max_artifact_bytes = 100 * 1024 * 1024
    return cfg


def test_watcher_path_reports_feature_version(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """``ArtifactWatcher._build_entry`` (the hot-swap loader) must also refuse.

    Drives ``_load_recipe`` directly -- the same synchronous pattern
    ``tests/unit/test_serving_watcher.py`` uses for its failure-path tests --
    rather than starting the watcher thread, to avoid a timing-dependent test.
    """
    artifact_path = tmp_path / "model.recotem"
    data = make_artifact(header_dict=dict(_REFUSED_HEADER))
    artifact_path.write_bytes(data)

    recipe = types.SimpleNamespace(item_metadata=None)
    state = _RecipeWatchState(recipe=recipe, artifact_path=str(artifact_path))

    registry = ModelRegistry()
    stub_entry = MagicMock()
    stub_entry.last_load_error = None
    stub_entry.loaded = False
    registry.replace("news", stub_entry)

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=tmp_path,
        serve_config=_make_watcher_serve_config(),
        key_ring=single_key_ring,
        initial_states={"news": state},
    )

    with structlog.testing.capture_logs() as cap:
        watcher._load_recipe("news", state, force=True)

    failed = [e for e in cap if e.get("event") == "artifact_load_failed"]
    assert failed, "watcher must log artifact_load_failed for a refused feature version"
    assert failed[0].get("reason") == "feature_version"

    entry = registry.get("news")
    assert entry is not None
    assert "feature encoder version" in (entry.last_load_error or "").lower()


def test_watcher_path_loads_when_feature_version_matches(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """Positive control for the hot-swap path, mirroring the startup-path one.

    Without this, a watcher-side gate that unconditionally refused every
    artifact would still pass ``test_watcher_path_reports_feature_version``.
    """
    artifact_path = tmp_path / "model_ok.recotem"
    header = dict(_REFUSED_HEADER)
    header["features"] = {"version": 1}
    data = make_artifact(header_dict=header)
    artifact_path.write_bytes(data)

    recipe = types.SimpleNamespace(item_metadata=None)
    state = _RecipeWatchState(recipe=recipe, artifact_path=str(artifact_path))

    registry = ModelRegistry()
    stub_entry = MagicMock()
    stub_entry.last_load_error = None
    stub_entry.loaded = False
    registry.replace("news", stub_entry)

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=tmp_path,
        serve_config=_make_watcher_serve_config(),
        key_ring=single_key_ring,
        initial_states={"news": state},
    )

    with structlog.testing.capture_logs() as cap:
        watcher._load_recipe("news", state, force=True)

    failed = [e for e in cap if e.get("event") == "artifact_load_failed"]
    assert not failed, f"matching feature version must not fail load; got {failed!r}"

    entry = registry.get("news")
    assert entry is not None
    assert entry.loaded is True
