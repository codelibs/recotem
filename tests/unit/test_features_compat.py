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

The second half of this module covers ``check_artifact_feature_state``, which
reconciles that descriptor with the encoder state the PAYLOAD carries. The
version gate above validates the descriptor against nothing: it reads only
``raw["version"]``, so a descriptor naming columns the payload does not have --
or no descriptor at all over a payload that does -- loaded and served. Every
refusal below therefore needs a validly-signed artifact (the mutation is
re-signed with the test key ring), which is why these are defence-in-depth
tests rather than a security boundary: an attacker holding the signing key can
substitute the model outright. The value is that a mis-built or
partially-tampered artifact fails at load instead of serving quietly wrong
answers.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
import structlog.testing

from recotem._features import (
    FEATURE_STATE_MSG_PREFIX,
    FEATURE_VERSION_MSG_PREFIX,
    check_artifact_feature_state,
    check_artifact_feature_version,
)
from recotem._idmap import IDMappedRecommender
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


# ---------------------------------------------------------------------------
# Header <-> payload reconciliation (check_artifact_feature_state)
#
# `check_artifact_feature_version` runs on the header alone, so the descriptor
# it validates is compared against nothing. These cover the second gate, which
# runs after deserialization and is the first point where both halves exist.
# ---------------------------------------------------------------------------


class IALSRecommender:  # noqa: N801 - the NAME is the point; see below
    """Bare stand-in whose NAME is what ``_is_feature_capable`` matches on.

    ``IDMappedRecommender._is_feature_capable`` keys on
    ``type(self.recommender).__name__``, not the FQCN (see its docstring), so a
    locally-defined class of exactly that name is a faithful stand-in for the
    capable case without training a real IALS. The leading underscore a private
    test helper would normally carry is deliberately absent: it would land in
    ``__name__`` and make the stand-in read as incapable.

    Used only for the in-process gate tests. The load-path tests below cannot
    use it -- ``SafeUnpickler`` allow-lists FQCNs, and a test module's class is
    not on the list -- so they build the incapable case out of a builtin.
    """


class TopPopRecommender:  # noqa: N801 - as above
    """The incapable counterpart -- a search winner that cannot use features."""


def _make_state(*names: str) -> dict:
    """A real encoder state, built by the real builder.

    Hand-writing the dict would let the test drift from what
    ``build_encoder_state`` actually produces, which is exactly the pair the
    gate under test compares.
    """
    from recotem._features import build_encoder_state
    from recotem.recipe.models import FeatureColumn

    cols = names or ("genre",)
    frame = pd.DataFrame(
        {c: [f"{c}_a", f"{c}_b", f"{c}_a"] for c in cols},
        index=["e1", "e2", "e3"],
    )
    return build_encoder_state(
        frame, [FeatureColumn(name=c, encoding="categorical") for c in cols]
    )


def _payload(
    *,
    item: dict | None = None,
    user: dict | None = None,
    recommender: object | None = None,
) -> IDMappedRecommender:
    """A real IDMappedRecommender, so the real capability logic is exercised."""
    return IDMappedRecommender(
        recommender if recommender is not None else TopPopRecommender(),
        ["u1", "u2"],
        ["e1", "e2", "e3"],
        item_feature_state=item,
        user_feature_state=user,
    )


def _picklable_payload(
    *, item: dict | None = None, user: dict | None = None
) -> IDMappedRecommender:
    """Like ``_payload`` but with an inner recommender ``SafeUnpickler`` accepts.

    The allow-list is by FQCN, so a class defined in this test module cannot be
    reconstructed on load. A builtin ``dict`` stands in: it is allow-listed, and
    ``type({}).__name__`` is not a feature-capable name, which is exactly the
    ``features.active: false`` shape these load-path tests exercise.
    """
    return _payload(item=item, user=user, recommender={"stub": 1})


def _descriptor(state: dict) -> dict:
    from recotem._features import state_descriptor

    return state_descriptor(state)


def test_state_absent_header_absent_passes() -> None:
    """The pre-PR-148 case: no descriptor, no payload state, nothing to check."""
    check_artifact_feature_state({"recipe_name": "r"}, _payload(), name="r")


def test_state_agreeing_header_passes() -> None:
    state = _make_state()
    header = {"features": {"version": 1, "active": False, "item": _descriptor(state)}}
    check_artifact_feature_state(header, _payload(item=state), name="r")


def test_payload_state_without_header_descriptor_refused() -> None:
    """Deleting the "features" key deletes the version gate along with it."""
    with pytest.raises(ArtifactError, match="has no 'features' header"):
        check_artifact_feature_state(
            {"recipe_name": "r"}, _payload(item=_make_state()), name="r"
        )


def test_payload_state_without_side_descriptor_refused() -> None:
    """``{"version": 1}`` over a payload carrying item state is a half-header."""
    with pytest.raises(ArtifactError, match="declares no item features"):
        check_artifact_feature_state(
            {"features": {"version": 1}}, _payload(item=_make_state()), name="r"
        )


def test_header_side_without_payload_state_refused() -> None:
    with pytest.raises(ArtifactError, match="carries no item feature encoder state"):
        check_artifact_feature_state(
            {"features": {"version": 1, "item": _descriptor(_make_state())}},
            _payload(),
            name="r",
        )


def test_ghost_columns_refused() -> None:
    """The n_features/columns disagreement ``recotem inspect`` would print."""
    state = _make_state()
    header = {
        "features": {
            "version": 1,
            "item": {"n_features": 999, "columns": ["ghost", "phantom"]},
        }
    }
    with pytest.raises(ArtifactError, match="declares 999 item feature dim"):
        check_artifact_feature_state(header, _payload(item=state), name="r")


def test_inflated_n_features_alone_refused() -> None:
    """Columns agree, dimension does not -- caught at load, not at request time."""
    state = _make_state()
    desc = _descriptor(state)
    desc["n_features"] = desc["n_features"] + 1
    header = {"features": {"version": 1, "item": desc}}
    with pytest.raises(ArtifactError, match="item feature dimensions"):
        check_artifact_feature_state(header, _payload(item=state), name="r")


def test_unknown_descriptor_key_refused() -> None:
    """Ignoring what it does not understand is how a reader accepts a forgery."""
    state = _make_state()
    header = {"features": {"version": 1, "item": _descriptor(state), "sneaky": "v"}}
    with pytest.raises(ArtifactError, match="unrecognised key"):
        check_artifact_feature_state(header, _payload(item=state), name="r")


def test_unknown_side_descriptor_key_refused() -> None:
    state = _make_state()
    desc = _descriptor(state)
    desc["extra"] = 1
    header = {"features": {"version": 1, "item": desc}}
    with pytest.raises(ArtifactError, match="item feature descriptor with keys"):
        check_artifact_feature_state(header, _payload(item=state), name="r")


def test_payload_state_version_skew_refused() -> None:
    """``state["version"]`` was dead data; it is the payload-side anchor now."""
    state = _make_state()
    state["version"] = 99
    header = {"features": {"version": 1, "item": _descriptor(state)}}
    with pytest.raises(ArtifactError, match="at version 99"):
        check_artifact_feature_state(header, _payload(item=state), name="r")


def test_active_true_over_incapable_recommender_refused() -> None:
    state = _make_state()
    header = {"features": {"version": 1, "active": True, "item": _descriptor(state)}}
    with pytest.raises(ArtifactError, match="cannot consume feature state"):
        check_artifact_feature_state(
            header, _payload(item=state, recommender=TopPopRecommender()), name="r"
        )


def test_active_false_over_capable_recommender_refused() -> None:
    state = _make_state()
    header = {"features": {"version": 1, "active": False, "item": _descriptor(state)}}
    with pytest.raises(ArtifactError, match="can consume feature state"):
        check_artifact_feature_state(
            header, _payload(item=state, recommender=IALSRecommender()), name="r"
        )


def test_active_matching_capable_recommender_passes() -> None:
    state = _make_state()
    header = {"features": {"version": 1, "active": True, "item": _descriptor(state)}}
    check_artifact_feature_state(
        header, _payload(item=state, recommender=IALSRecommender()), name="r"
    )


def test_non_bool_active_refused() -> None:
    state = _make_state()
    header = {"features": {"version": 1, "active": 1, "item": _descriptor(state)}}
    with pytest.raises(ArtifactError, match="non-boolean 'features.active'"):
        check_artifact_feature_state(header, _payload(item=state), name="r")


def test_absent_active_still_loads() -> None:
    """Backward compatibility: artifacts written before the flag existed.

    PR #148 shipped a descriptor with no ``active`` key. Those carry a
    consistent header/payload pair and must keep loading, so the capability
    cross-check is skipped rather than fail-closed when the flag is absent.
    """
    state = _make_state()
    header = {"features": {"version": 1, "item": _descriptor(state)}}
    check_artifact_feature_state(
        header, _payload(item=state, recommender=IALSRecommender()), name="r"
    )


def test_user_side_is_checked_too() -> None:
    """Both sides, not just item -- a user-only mutation must not slip past."""
    item, user = _make_state("genre"), _make_state("band")
    header = {
        "features": {
            "version": 1,
            "item": _descriptor(item),
            "user": {"n_features": 42, "columns": ["nope"]},
        }
    }
    with pytest.raises(ArtifactError, match="42 user feature dimensions"):
        check_artifact_feature_state(header, _payload(item=item, user=user), name="r")


# ---------------------------------------------------------------------------
# Classification + metric label, mirroring the version gate's own wiring tests
# ---------------------------------------------------------------------------


def _feature_state_message() -> str:
    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_feature_state(
            {"recipe_name": "news"}, _payload(item=_make_state()), name="news"
        )
    return str(excinfo.value)


def test_feature_state_message_classifies_as_feature_state() -> None:
    assert _classify_artifact_error(_feature_state_message()) == "feature_state"


def test_feature_state_version_message_is_not_misclassified() -> None:
    """The payload-version refusal also contains "version" -- ordering matters."""
    state = _make_state()
    state["version"] = 99
    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_feature_state(
            {"features": {"version": 1, "item": _descriptor(state)}},
            _payload(item=state),
            name="news",
        )
    msg = str(excinfo.value)
    assert "version" in msg.lower(), "precondition: message contains 'version'"
    assert _classify_artifact_error(msg) == "feature_state"


def test_feature_state_is_an_allowed_metric_label() -> None:
    assert "feature_state" in _LOAD_FAILURE_REASONS


def test_state_classifier_prefix_matches_guard_prefix() -> None:
    assert _feature_state_message().lower().startswith(FEATURE_STATE_MSG_PREFIX.lower())


# ---------------------------------------------------------------------------
# Both load paths, over real signed artifacts.
#
# These are the tests that fail on the unfixed code by LOADING: each mutated
# artifact is re-signed with the test key ring, so nothing upstream of the new
# gate objects to it.
# ---------------------------------------------------------------------------


def _pickle_payload(obj: object) -> bytes:
    import pickle  # noqa: S403

    return pickle.dumps(obj, protocol=4)


def _base_header(features: dict | None) -> dict:
    header: dict = {
        "recipe_name": "news",
        "best_class": "TopPopRecommender",
        "trained_at": "2026-01-01T00:00:00Z",
    }
    if features is not None:
        header["features"] = features
    return header


def _load(tmp_path: Path, make_artifact, key_ring, header: dict, payload: object):
    data = make_artifact(header_dict=header, payload_bytes=_pickle_payload(payload))
    path = tmp_path / "feature_state.recotem"
    path.write_bytes(data)
    recipe = types.SimpleNamespace(
        name="news",
        output=types.SimpleNamespace(path=str(path)),
        item_metadata=None,
    )
    return _try_load_artifact(recipe, key_ring, ServeConfig())


def test_startup_path_refuses_payload_state_the_header_omits(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """Deleting the "features" key removed the version gate entirely, yet the
    payload still served feature cold start."""
    entry, reason = _load(
        tmp_path,
        make_artifact,
        single_key_ring,
        _base_header(None),
        _picklable_payload(item=_make_state()),
    )
    assert reason == "feature_state"
    assert entry.loaded is False
    assert "no 'features' header" in (entry.last_load_error or "")


def test_startup_path_refuses_ghost_columns(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """``recotem inspect`` printed ghost columns and the artifact served."""
    entry, reason = _load(
        tmp_path,
        make_artifact,
        single_key_ring,
        _base_header(
            {
                "version": 1,
                "item": {"n_features": 999, "columns": ["ghost", "phantom"]},
            }
        ),
        _picklable_payload(item=_make_state()),
    )
    assert reason == "feature_state"
    assert entry.loaded is False


def test_startup_path_refuses_payload_state_version_skew(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    state = _make_state()
    state["version"] = 99
    entry, reason = _load(
        tmp_path,
        make_artifact,
        single_key_ring,
        _base_header({"version": 1, "item": _descriptor(state)}),
        _picklable_payload(item=state),
    )
    assert reason == "feature_state"
    assert entry.loaded is False


def test_startup_path_refuses_unknown_descriptor_key(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """A descriptor field this build does not know was accepted and ignored."""
    state = _make_state()
    entry, reason = _load(
        tmp_path,
        make_artifact,
        single_key_ring,
        _base_header({"version": 1, "item": _descriptor(state), "sneaky": "v"}),
        _picklable_payload(item=state),
    )
    assert reason == "feature_state"
    assert entry.loaded is False


def test_startup_path_loads_agreeing_artifact(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """Positive control: a gate that refused everything would pass the above."""
    state = _make_state()
    entry, reason = _load(
        tmp_path,
        make_artifact,
        single_key_ring,
        _base_header({"version": 1, "active": False, "item": _descriptor(state)}),
        _picklable_payload(item=state),
    )
    assert reason == "ok", f"an agreeing artifact must load; got {reason!r}"
    assert entry.loaded is True


def test_startup_path_loads_pre_feature_payload(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """A 2.0.0-shaped payload: the feature attributes are absent from the
    pickle entirely, not merely ``None``.

    ``IDMappedRecommender.__getstate__`` returns ``dict(self.__dict__)``, so
    removing the keys before pickling reproduces exactly what a build predating
    those attributes wrote. ``__setstate__``'s ``setdefault`` restores them as
    ``None``; the gate must read that as "no features", not as an undeclared
    state.
    """
    old = _picklable_payload()
    del old.__dict__["item_feature_state"]
    del old.__dict__["user_feature_state"]
    blob = _pickle_payload(old)
    assert b"item_feature_state" not in blob, (
        "test setup invariant: the attribute must be absent from the pickle"
    )

    data = make_artifact(header_dict=_base_header(None), payload_bytes=blob)
    path = tmp_path / "pre_feature.recotem"
    path.write_bytes(data)
    recipe = types.SimpleNamespace(
        name="news",
        output=types.SimpleNamespace(path=str(path)),
        item_metadata=None,
    )
    entry, reason = _try_load_artifact(recipe, single_key_ring, ServeConfig())
    assert reason == "ok", f"a pre-feature artifact must load; got {reason!r}"
    assert entry.loaded is True


def test_watcher_path_refuses_payload_state_the_header_omits(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """A gate wired into app.py alone would still pass every test above."""
    artifact_path = tmp_path / "model.recotem"
    artifact_path.write_bytes(
        make_artifact(
            header_dict=_base_header(None),
            payload_bytes=_pickle_payload(_picklable_payload(item=_make_state())),
        )
    )

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
    assert failed, "watcher must refuse a header/payload feature mismatch"
    assert failed[0].get("reason") == "feature_state"


def test_watcher_path_loads_agreeing_artifact(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """Positive control for the hot-swap path."""
    encoder_state = _make_state()
    artifact_path = tmp_path / "model_ok.recotem"
    artifact_path.write_bytes(
        make_artifact(
            header_dict=_base_header(
                {"version": 1, "active": False, "item": _descriptor(encoder_state)}
            ),
            payload_bytes=_pickle_payload(_picklable_payload(item=encoder_state)),
        )
    )

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
    assert not failed, f"an agreeing artifact must not fail load; got {failed!r}"
    assert registry.get("news").loaded is True
