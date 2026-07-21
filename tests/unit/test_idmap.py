"""Unit tests for recotem._idmap.IDMappedRecommender.

Covers:
- Fix 4: unknown user_id raises KeyError without calling underlying recommender.
- Fix 4: known user_id that causes RuntimeError in the underlying recommender
  propagates as RuntimeError (not masked to KeyError).
- Task 8: item_feature_state / user_feature_state class-level default +
  round-trip persistence, including through the real signed-artifact path.

NOTE: ``IDMappedRecommender`` is pickled directly (via ``pickle.dumps`` /
``pickle.loads``) because that is production behaviour: irspack recommenders
carry scipy sparse matrices / numpy arrays that only pickle supports. This is
the same intentional, defence-in-depth-guarded usage documented in
``tests/conftest.py`` -- the artifact-path test below additionally goes
through ``recotem.artifact.signing.unpickle_payload``'s HMAC verification and
FQCN allow-list (``SafeUnpickler``), not bare ``pickle.loads``, for anything
that also needs to be safe against untrusted input.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import scipy.sparse as sps


class _FakeRecommender:
    """Minimal picklable stand-in for a trained irspack recommender.

    A bare ``unittest.mock.MagicMock``/``Mock`` is NOT picklable (raises
    ``PicklingError: Can't pickle <class 'unittest.mock.MagicMock'>``), so it
    cannot stand in for the recommender in tests that pickle
    ``IDMappedRecommender`` end-to-end (the feature-state round-trip tests
    below, and every real artifact). This class exposes just the surface
    ``irspack.utils.id_mapping.IDMapper.recommend_for_known_user_id`` needs
    (``n_users`` and ``get_score_remove_seen``) so those round trips can also
    exercise the real recommend path, not just attribute persistence.
    """

    def __init__(self, n_users: int = 1, n_items: int = 1) -> None:
        self.n_users = n_users
        self.n_items = n_items

    def get_score_remove_seen(self, user_indices: np.ndarray) -> np.ndarray:
        return np.zeros((len(user_indices), self.n_items), dtype=np.float64)


@pytest.fixture()
def mock_rec() -> _FakeRecommender:
    """A minimal picklable stand-in for a trained irspack recommender.

    Sized for the single-user/single-item fixtures used throughout this
    module (``IDMappedRecommender(mock_rec, ["u1"], ["i1"])``).
    """
    return _FakeRecommender(n_users=1, n_items=1)


def _make_idmapped(user_ids: list[str], item_ids: list[str]) -> object:
    """Build an IDMappedRecommender with a real IDMapper but a mock recommender."""
    from recotem._idmap import IDMappedRecommender

    mock_rec = MagicMock()
    return IDMappedRecommender(mock_rec, user_ids, item_ids)


# ---------------------------------------------------------------------------
# Unknown user raises KeyError — does NOT call underlying recommender
# ---------------------------------------------------------------------------


def test_unknown_user_raises_key_error() -> None:
    """get_recommendation_for_known_user_id must raise KeyError for an
    unknown user_id without invoking the underlying recommender."""
    idmapped = _make_idmapped(["u1", "u2"], ["i1", "i2"])

    with pytest.raises(KeyError) as exc_info:
        idmapped.get_recommendation_for_known_user_id("unknown_user", cutoff=5)

    assert str(exc_info.value) == "'unknown_user'", (
        f"KeyError must contain the user_id; got {exc_info.value!r}"
    )
    # Confirm recommender was never called
    idmapped.recommender.assert_not_called()  # type: ignore[attr-defined]


def test_unknown_user_key_error_not_called_for_any_unknown_variant() -> None:
    """Confirm the pre-check fires for various unknown user strings."""
    idmapped = _make_idmapped(["alice", "bob"], ["item1"])

    for uid in ("", "charlie", "Alice", " alice", "bob ", "ALICE"):
        with pytest.raises(KeyError):
            idmapped.get_recommendation_for_known_user_id(uid, cutoff=1)


# ---------------------------------------------------------------------------
# Known user with internal RuntimeError propagates (NOT masked to KeyError)
# ---------------------------------------------------------------------------


def test_known_user_internal_runtime_error_propagates() -> None:
    """When the underlying recommender raises RuntimeError for a KNOWN user_id,
    the error must propagate as RuntimeError — not be swallowed into KeyError.

    This ensures that genuine internal failures (e.g. numpy/scipy errors) are
    surfaced as 500 errors rather than silently becoming 404 responses.
    """
    from unittest.mock import patch

    from recotem._idmap import IDMappedRecommender

    mock_rec = MagicMock()
    idmapped = IDMappedRecommender(mock_rec, ["u1"], ["i1"])

    # Patch the mapper's recommend_for_known_user_id to raise RuntimeError
    with patch.object(
        idmapped._mapper,
        "recommend_for_known_user_id",
        side_effect=RuntimeError("internal scipy error"),
    ):
        with pytest.raises(RuntimeError, match="internal scipy error"):
            idmapped.get_recommendation_for_known_user_id("u1", cutoff=5)


def test_known_user_internal_runtime_error_is_not_key_error() -> None:
    """Double-check that the RuntimeError is not wrapped in a KeyError."""
    from unittest.mock import patch

    from recotem._idmap import IDMappedRecommender

    mock_rec = MagicMock()
    idmapped = IDMappedRecommender(mock_rec, ["u1"], ["i1"])

    with patch.object(
        idmapped._mapper,
        "recommend_for_known_user_id",
        side_effect=RuntimeError("matrix dimension mismatch"),
    ):
        try:
            idmapped.get_recommendation_for_known_user_id("u1")
            pytest.fail("Expected RuntimeError was not raised")
        except KeyError:
            pytest.fail("RuntimeError must not be caught and re-raised as KeyError")
        except RuntimeError:
            pass  # correct: propagates unchanged


# ---------------------------------------------------------------------------
# M-4 (IPython): _ipython_stub.install() idempotency scenarios
# ---------------------------------------------------------------------------


def test_ipython_stub_installs_both_when_neither_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When neither 'IPython' nor 'IPython.display' are in sys.modules,
    install() must add both."""
    import sys

    from recotem._ipython_stub import install

    monkeypatch.delitem(sys.modules, "IPython", raising=False)
    monkeypatch.delitem(sys.modules, "IPython.display", raising=False)

    install()

    assert "IPython" in sys.modules, "install() must add 'IPython' to sys.modules"
    assert "IPython.display" in sys.modules, (
        "install() must add 'IPython.display' to sys.modules"
    )
    assert callable(sys.modules["IPython.display"].display), (
        "IPython.display.display must be callable"
    )


def test_ipython_stub_installs_display_when_ipython_present_but_display_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When 'IPython' is already in sys.modules but 'IPython.display' is not,
    install() must add 'IPython.display' WITHOUT replacing the real 'IPython'."""
    import sys
    import types

    from recotem._ipython_stub import install

    # Simulate partial real-IPython: IPython present but IPython.display absent.
    real_ipython_stub = types.ModuleType("IPython")
    real_ipython_stub.__version__ = "7.0.0"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "IPython", real_ipython_stub)
    monkeypatch.delitem(sys.modules, "IPython.display", raising=False)

    install()

    # IPython must NOT be replaced -- we keep the one already in sys.modules.
    assert sys.modules["IPython"] is real_ipython_stub, (
        "install() must not replace an already-present 'IPython' module"
    )
    # IPython.display must now be present.
    assert "IPython.display" in sys.modules, (
        "install() must add 'IPython.display' when it is absent even if 'IPython' exists"
    )
    assert callable(sys.modules["IPython.display"].display), (
        "The installed IPython.display.display must be callable"
    )


def test_ipython_stub_noop_when_both_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both 'IPython' and 'IPython.display' are already in sys.modules,
    install() must be a no-op -- it must not replace either."""
    import sys
    import types

    from recotem._ipython_stub import install

    existing_ipython = types.ModuleType("IPython")
    existing_display = types.ModuleType("IPython.display")
    monkeypatch.setitem(sys.modules, "IPython", existing_ipython)
    monkeypatch.setitem(sys.modules, "IPython.display", existing_display)

    install()

    assert sys.modules["IPython"] is existing_ipython, (
        "install() must not replace an already-present 'IPython' module"
    )
    assert sys.modules["IPython.display"] is existing_display, (
        "install() must not replace an already-present 'IPython.display' module"
    )


# ---------------------------------------------------------------------------
# Task 8: item_feature_state / user_feature_state persistence
# ---------------------------------------------------------------------------


def test_feature_state_defaults_to_none(mock_rec: _FakeRecommender) -> None:
    from recotem._idmap import IDMappedRecommender

    idm = IDMappedRecommender(mock_rec, ["u1"], ["i1"])
    assert idm.item_feature_state is None
    assert idm.user_feature_state is None


def test_feature_state_is_class_level_not_init_only() -> None:
    """__setstate__ bypasses __init__, so the default must resolve via the class."""
    from recotem._idmap import IDMappedRecommender

    assert "item_feature_state" in IDMappedRecommender.__dict__
    assert IDMappedRecommender.item_feature_state is None
    assert "user_feature_state" in IDMappedRecommender.__dict__
    assert IDMappedRecommender.user_feature_state is None


def test_feature_state_round_trips(mock_rec: _FakeRecommender) -> None:
    from recotem._idmap import IDMappedRecommender

    state = {"version": 1, "n_features": 2, "columns": []}
    idm = IDMappedRecommender(mock_rec, ["u1"], ["i1"], item_feature_state=state)
    back = pickle.loads(pickle.dumps(idm))  # noqa: S301
    assert back.item_feature_state == state
    assert back.user_feature_state is None


def test_old_pickle_without_state_resolves_default(mock_rec: _FakeRecommender) -> None:
    """Simulate an artifact pickled before these attributes existed.

    ``__getstate__`` output with the two keys stripped stands in for a state
    dict produced by an old build of ``IDMappedRecommender``.  Restoring it
    via ``__setstate__`` must still resolve both attributes to ``None``
    without raising ``AttributeError``.
    """
    from recotem._idmap import IDMappedRecommender

    idm = IDMappedRecommender(mock_rec, ["u1"], ["i1"])
    raw = idm.__getstate__()
    raw.pop("item_feature_state", None)
    raw.pop("user_feature_state", None)
    revived = IDMappedRecommender.__new__(IDMappedRecommender)
    revived.__setstate__(raw)
    assert revived.item_feature_state is None
    assert revived.get_recommendation_for_known_user_id("u1", 1) is not None


def test_feature_state_round_trips_through_real_artifact_path(
    tmp_path: Path,
) -> None:
    """Prove the encoder state survives the REAL artifact path, not just a
    bare ``pickle.dumps``/``pickle.loads`` round trip.

    ``write_artifact`` -> ``read_artifact`` -> ``unpickle_payload`` is the
    production path, and ``unpickle_payload`` enforces
    ``recotem.artifact.signing.SafeUnpickler``'s hand-enumerated FQCN
    allow-list.  A plain dict + numpy array value (what
    ``recotem._features.build_encoder_state`` produces, per the brief) is
    expected to clear that allow-list with NO change to it -- that is the
    claim this whole design rests on, so it is worth pinning here.

    The ``recommender`` slot uses a plain ``dict`` (an allow-listed builtin)
    rather than a mock: mocks are not on the FQCN allow-list and would make
    this fail for a reason unrelated to feature-state persistence.
    """
    from recotem._idmap import IDMappedRecommender
    from recotem.artifact.io import read_artifact, write_artifact
    from recotem.artifact.signing import KeyRing, unpickle_payload

    state = {"version": 1, "n_features": 2, "weights": np.array([1.0, 2.0, 3.0])}
    idm = IDMappedRecommender({}, ["u1"], ["i1"], item_feature_state=state)

    key_ring = KeyRing("probe:" + "ab" * 32)
    output_path = str(tmp_path / "probe.recotem")
    write_artifact(
        payload_obj=idm,
        header_dict={"recipe_name": "probe"},
        key_ring=key_ring,
        fs_path=output_path,
        versioning="always_overwrite",
    )

    _, payload_bytes = read_artifact(output_path, key_ring)
    revived = unpickle_payload(payload_bytes)

    assert revived.item_feature_state["version"] == 1
    assert revived.item_feature_state["n_features"] == 2
    np.testing.assert_array_equal(
        revived.item_feature_state["weights"], state["weights"]
    )
    assert revived.user_feature_state is None


# ---------------------------------------------------------------------------
# Task 11: feature-based cold-start methods
# ---------------------------------------------------------------------------


@pytest.fixture
def fa_model() -> object:
    """A real, tiny 2-epoch feature-aware IALS wrapped in IDMappedRecommender.

    Benchmarked at ~1-2ms to train (40 users x 20 items, 2 epochs, K=8) --
    far under the threshold for @pytest.mark.slow, so this runs in the
    default suite.
    """
    import pandas as pd
    from irspack import IALSRecommender

    from recotem._features import build_encoder_state, encode
    from recotem._idmap import IDMappedRecommender
    from recotem.recipe.models import FeatureColumn

    rng = np.random.default_rng(0)
    n_users, n_items = 40, 20
    X = sps.csr_matrix((rng.random((n_users, n_items)) > 0.6).astype(np.float64))
    item_df = pd.DataFrame(
        {
            "item_id": [f"i{i}" for i in range(n_items)],
            "genre": ["action" if i % 2 else "drama" for i in range(n_items)],
        }
    ).set_index("item_id")
    user_df = pd.DataFrame(
        {
            "user_id": [f"u{u}" for u in range(n_users)],
            "band": ["young" if u % 2 else "old" for u in range(n_users)],
        }
    ).set_index("user_id")

    istate = build_encoder_state(
        item_df, [FeatureColumn(name="genre", encoding="categorical")]
    )
    ustate = build_encoder_state(
        user_df, [FeatureColumn(name="band", encoding="categorical")]
    )
    F = encode(istate, item_df, index_order=[f"i{i}" for i in range(n_items)])
    U = encode(ustate, user_df, index_order=[f"u{u}" for u in range(n_users)])

    rec = IALSRecommender(
        X,
        n_components=8,
        alpha0=0.1,
        train_epochs=2,
        random_seed=42,
        item_features=F,
        user_features=U,
        lambda_item_feature=1e-1,
        lambda_user_feature=1e-1,
    ).learn()
    return IDMappedRecommender(
        rec,
        [f"u{u}" for u in range(n_users)],
        [f"i{i}" for i in range(n_items)],
        item_feature_state=istate,
        user_feature_state=ustate,
    )


def test_cold_user_from_features(fa_model: object) -> None:
    recs, unknown = fa_model.get_recommendation_for_cold_user(
        {"band": "young"}, cutoff=5
    )
    assert len(recs) == 5
    assert unknown == []
    assert all(isinstance(r[0], str) for r in recs)


def test_cold_user_reports_unknown_category(fa_model: object) -> None:
    _, unknown = fa_model.get_recommendation_for_cold_user(
        {"band": "martian"}, cutoff=3
    )
    assert unknown == ["band"]


def test_cold_start_methods_take_no_exclude_items_parameter() -> None:
    """Client-requested exclusion is the router's post-filter, never a
    ranker argument.

    These two methods used to accept ``exclude_items`` and pass it to
    irspack as ``forbidden_item_ids``, which makes the ranker BACK-FILL to a
    full ``cutoff``. Every pre-existing path instead post-filters in
    ``routes._build_items``, which truncates -- so the same
    ``exclude_items`` request returned a full page here and a short one
    everywhere else, and merely adding ``user_features`` to a request
    changed how many items came back.
    ``tests/unit/test_serving_cold_start.py``'s
    ``test_exclude_items_truncates_and_never_backfills`` proves the three
    cold-start cases now agree end-to-end; this pins the signature, because
    that test cannot see a re-added parameter that nothing happens to pass.
    """
    import inspect

    from recotem._idmap import IDMappedRecommender

    for method in (
        IDMappedRecommender.get_recommendation_for_cold_user,
        IDMappedRecommender.get_recommendation_for_cold_seeds,
    ):
        assert "exclude_items" not in inspect.signature(method).parameters, (
            f"{method.__name__} must not take exclude_items: exclusion "
            "post-filters in routes._build_items so limit stays a ceiling"
        )


def test_new_user_with_features_differs_from_without(fa_model: object) -> None:
    without = fa_model.get_recommendation_for_new_user(["i0", "i2"], cutoff=5)
    with_f, _ = fa_model.get_recommendation_for_new_user(
        ["i0", "i2"], cutoff=5, user_features={"band": "young"}
    )
    assert without != with_f  # the joint solve is genuinely different


def test_new_user_without_features_is_backward_compatible(fa_model: object) -> None:
    recs = fa_model.get_recommendation_for_new_user(["i0"], cutoff=3)
    assert isinstance(recs, list)  # NOT a tuple: old signature preserved


def test_new_user_with_features_reports_unknown_category(fa_model: object) -> None:
    """Case B: an out-of-vocabulary ``band`` value must surface in `unknown`.

    ``test_new_user_with_features_differs_from_without`` only ever feeds a
    KNOWN category ("young"), so it cannot tell "correctly empty" apart from
    "silently discarded" -- `unknown` could be hardcoded to `[]` and that
    test would still pass. This test feeds a value absent from the `band`
    vocabulary built in the `fa_model` fixture ("young"/"old" only) and
    requires it to be reported, since `unknown_columns` is the only signal
    serving gets that a category silently degraded to an all-zero segment.
    """
    _, unknown = fa_model.get_recommendation_for_new_user(
        ["i0", "i2"], cutoff=5, user_features={"band": "martian"}
    )
    assert unknown == ["band"]


def test_cold_seeds_from_item_features(fa_model: object) -> None:
    recs, unknown = fa_model.get_recommendation_for_cold_seeds(
        ["i0", "brand_new"],
        {"brand_new": {"genre": "action"}},
        cutoff=5,
    )
    assert len(recs) == 5
    assert unknown == []
    # A known seed must not recommend itself back. This is the "remove seen"
    # behavior at _idmap.py's `forbidden.extend(...)` -- deleting that line
    # leaves this assertion as the ONLY thing in the suite that would catch
    # the regression (verified: see task-11-report.md's mutation-proof log).
    assert "i0" not in {r[0] for r in recs}


def test_cold_seeds_removes_all_known_seeds_from_own_output(
    fa_model: object,
) -> None:
    """Dedicated proof for the seed-removal behavior, independent of ranking.

    Seeds with every item in the (small, 20-item) catalog at a cutoff that
    would otherwise return the whole catalog: if seed removal were broken,
    every seed would have to appear somewhere in the output since there is
    nothing else to return instead. Using only known seeds (no unknown ones)
    isolates the exact line under review (`forbidden.extend` over known
    seeds) from the unrelated "unknown seed" code path.
    """
    all_items = [f"i{i}" for i in range(20)]
    recs, _ = fa_model.get_recommendation_for_cold_seeds(all_items, {}, cutoff=20)
    rec_ids = {r[0] for r in recs}
    assert rec_ids.isdisjoint(set(all_items))


def test_cold_seeds_reports_unknown_category(fa_model: object) -> None:
    """Case C: an out-of-vocabulary ``genre`` for the UNKNOWN seed must
    surface in `unknown`.

    ``test_cold_seeds_from_item_features`` only ever feeds a KNOWN category
    ("action") for its unknown seed, so it cannot distinguish "correctly
    empty" from "silently discarded" -- `unknown` could be hardcoded to `[]`
    and that test would still pass. This test feeds a `genre` value absent
    from the vocabulary built in the `fa_model` fixture ("action"/"drama"
    only) for the unknown seed "brand_new", and requires it to be reported.
    """
    _, unknown = fa_model.get_recommendation_for_cold_seeds(
        ["i0", "brand_new"],
        {"brand_new": {"genre": "sci-fi"}},
        cutoff=5,
    )
    assert unknown == ["genre"]


# ---------------------------------------------------------------------------
# Review finding 1 (MEDIUM): an extreme-but-finite numerical feature value
# standardizes to a magnitude that makes irspack's per-request
# conjugate-gradient cold-start solve ill-conditioned. irspack's native core
# raises a bare RuntimeError ("Conjugate-gradient solver encountered a
# singular system.") with no awareness that the input came from an
# untrusted client. Each of the three cold-start call sites below must catch
# that RuntimeError and re-raise ColdStartNumericalError -- a distinct,
# non-RuntimeError type -- so serving/routes.py can map it to a 400 instead
# of letting a bare RuntimeError surface as an unhandled 500.
#
# Mocked (not a genuine 1e22 value through a real solve) so this pins the
# WRAPPING contract deterministically, independent of BLAS/threading
# variance in exactly which magnitude triggers the native solver's failure.
# The reproduction with a REAL trained model and a real extreme value lives
# in tests/unit/test_serving_cold_start.py (route-level, end-to-end).
# ---------------------------------------------------------------------------

_SINGULAR_SYSTEM_MSG = "Conjugate-gradient solver encountered a singular system."


def test_cold_user_from_features_wraps_runtime_error(fa_model: object) -> None:
    from unittest.mock import patch

    from recotem._idmap import ColdStartNumericalError

    with patch.object(
        fa_model.recommender,
        "get_score_cold_user_from_features",
        side_effect=RuntimeError(_SINGULAR_SYSTEM_MSG),
    ):
        with pytest.raises(ColdStartNumericalError):
            fa_model.get_recommendation_for_cold_user({"band": "young"}, cutoff=3)


def test_new_user_with_features_wraps_runtime_error(fa_model: object) -> None:
    """Case B: ``get_score_cold_user`` (joint history + feature-prior solve)
    must have the same wrapping as the features-only case A path above."""
    from unittest.mock import patch

    from recotem._idmap import ColdStartNumericalError

    with patch.object(
        fa_model.recommender,
        "get_score_cold_user",
        side_effect=RuntimeError(_SINGULAR_SYSTEM_MSG),
    ):
        with pytest.raises(ColdStartNumericalError):
            fa_model.get_recommendation_for_new_user(
                ["i0"], cutoff=3, user_features={"band": "young"}
            )


def test_cold_seeds_wraps_runtime_error(fa_model: object) -> None:
    """Case C: ``compute_item_embedding_from_features`` -- the exact call
    site in the review's reproduction (``:recommend-related`` with a cold
    seed's ``item_features`` carrying an extreme numerical value)."""
    from unittest.mock import patch

    from recotem._idmap import ColdStartNumericalError

    with patch.object(
        fa_model.recommender,
        "compute_item_embedding_from_features",
        side_effect=RuntimeError(_SINGULAR_SYSTEM_MSG),
    ):
        with pytest.raises(ColdStartNumericalError):
            fa_model.get_recommendation_for_cold_seeds(
                ["i0", "brand_new"], {"brand_new": {"genre": "action"}}, cutoff=3
            )


# ---------------------------------------------------------------------------
# Review round 2, Important 1: the wrap above was previously BLANKET (any
# RuntimeError, no message check), which also swallowed non-numerical
# RuntimeErrors the exact same irspack calls can raise for reasons that have
# nothing to do with the request -- e.g. irspack/recommenders/ials.py's
# ``trainer_as_ials`` raising RuntimeError("tried to fetch trainer before
# the training.") when ``trainer`` is unexpectedly None. Each of the three
# call sites must now re-raise a non-matching RuntimeError UNCHANGED (not
# wrap it in ColdStartNumericalError), so it surfaces as a 500 at the route
# layer rather than a mislabeled 400. See
# tests/unit/test_serving_cold_start.py for the route-level 500 proof.
# ---------------------------------------------------------------------------

_NON_NUMERICAL_TRAINER_MSG = "tried to fetch trainer before the training."


def test_cold_user_from_features_propagates_non_numerical_runtime_error(
    fa_model: object,
) -> None:
    from unittest.mock import patch

    with patch.object(
        fa_model.recommender,
        "get_score_cold_user_from_features",
        side_effect=RuntimeError(_NON_NUMERICAL_TRAINER_MSG),
    ):
        with pytest.raises(RuntimeError, match=_NON_NUMERICAL_TRAINER_MSG):
            fa_model.get_recommendation_for_cold_user({"band": "young"}, cutoff=3)


def test_new_user_with_features_propagates_non_numerical_runtime_error(
    fa_model: object,
) -> None:
    from unittest.mock import patch

    with patch.object(
        fa_model.recommender,
        "get_score_cold_user",
        side_effect=RuntimeError(_NON_NUMERICAL_TRAINER_MSG),
    ):
        with pytest.raises(RuntimeError, match=_NON_NUMERICAL_TRAINER_MSG):
            fa_model.get_recommendation_for_new_user(
                ["i0"], cutoff=3, user_features={"band": "young"}
            )


def test_cold_seeds_propagates_non_numerical_runtime_error(fa_model: object) -> None:
    from unittest.mock import patch

    with patch.object(
        fa_model.recommender,
        "compute_item_embedding_from_features",
        side_effect=RuntimeError(_NON_NUMERICAL_TRAINER_MSG),
    ):
        with pytest.raises(RuntimeError, match=_NON_NUMERICAL_TRAINER_MSG):
            fa_model.get_recommendation_for_cold_seeds(
                ["i0", "brand_new"], {"brand_new": {"genre": "action"}}, cutoff=3
            )


def test_cold_user_without_state_raises(mock_rec: _FakeRecommender) -> None:
    from recotem._idmap import IDMappedRecommender

    idm = IDMappedRecommender(mock_rec, ["u1"], ["i1"])
    with pytest.raises(ValueError, match="no user feature state"):
        idm.get_recommendation_for_cold_user({"band": "young"}, cutoff=1)


def test_cold_seeds_without_state_raises(mock_rec: _FakeRecommender) -> None:
    from recotem._idmap import IDMappedRecommender

    idm = IDMappedRecommender(mock_rec, ["u1"], ["i1"])
    with pytest.raises(ValueError, match="no item feature state"):
        idm.get_recommendation_for_cold_seeds(["i1"], {}, cutoff=1)


def test_new_user_with_features_without_state_raises(
    mock_rec: _FakeRecommender,
) -> None:
    from recotem._idmap import IDMappedRecommender

    idm = IDMappedRecommender(mock_rec, ["u1"], ["i1"])
    with pytest.raises(ValueError, match="no user feature state"):
        idm.get_recommendation_for_new_user(
            ["i1"], cutoff=1, user_features={"band": "young"}
        )


# ---------------------------------------------------------------------------
# Task 11 capability gate: non-None feature state does NOT imply the winning
# recommender can act on it (TopPop/CosineKNN/etc. can carry feature state
# unconditionally persisted by Task 9, but have no cold-start-from-features
# API at all). This must fail with a clean ValueError, not AttributeError.
# ---------------------------------------------------------------------------


def test_cold_start_methods_reject_non_feature_capable_recommender() -> None:
    """A real TopPopRecommender wrapped with non-None item/user feature state.

    TopPop is a legal search winner even when the recipe lists a
    feature-capable algorithm alongside it (``Recipe._validate_features_algorithms``
    requires only one feature-capable entry), and Task 9 persists feature
    state unconditionally so the header always agrees with the payload.
    TopPopRecommender has no ``get_score_cold_user_from_features``, no
    ``get_item_embedding``/``compute_item_embedding_from_features``, and its
    ``get_score_cold_user`` does not accept ``user_features`` -- so the ONLY
    guard against a bare ``AttributeError``/``TypeError`` escaping to a caller
    is the capability check in ``IDMappedRecommender._require_capability``.
    """
    import pandas as pd
    from irspack import TopPopRecommender

    from recotem._features import build_encoder_state
    from recotem._idmap import IDMappedRecommender
    from recotem.recipe.models import FeatureColumn

    n_users, n_items = 5, 4
    X = sps.csr_matrix(np.ones((n_users, n_items)))
    rec = TopPopRecommender(X).learn()

    item_df = pd.DataFrame(
        {
            "item_id": [f"i{i}" for i in range(n_items)],
            "genre": ["a", "b", "a", "b"],
        }
    ).set_index("item_id")
    user_df = pd.DataFrame(
        {
            "user_id": [f"u{u}" for u in range(n_users)],
            "band": ["x"] * n_users,
        }
    ).set_index("user_id")
    istate = build_encoder_state(
        item_df, [FeatureColumn(name="genre", encoding="categorical")]
    )
    ustate = build_encoder_state(
        user_df, [FeatureColumn(name="band", encoding="categorical")]
    )

    idm = IDMappedRecommender(
        rec,
        [f"u{u}" for u in range(n_users)],
        [f"i{i}" for i in range(n_items)],
        item_feature_state=istate,
        user_feature_state=ustate,
    )
    # The precondition this test exists to cover: state IS present.
    assert idm.item_feature_state is not None
    assert idm.user_feature_state is not None

    with pytest.raises(ValueError, match="does not support"):
        idm.get_recommendation_for_cold_user({"band": "x"}, cutoff=2)

    with pytest.raises(ValueError, match="does not support"):
        idm.get_recommendation_for_cold_seeds(
            ["i0", "brand_new"], {"brand_new": {"genre": "a"}}, cutoff=2
        )

    with pytest.raises(ValueError, match="does not support"):
        idm.get_recommendation_for_new_user(
            ["i0"], cutoff=2, user_features={"band": "x"}
        )

    # The untouched path (no user_features) must still work -- TopPop's
    # existing cold-start-from-history behavior is unaffected by this gate.
    recs = idm.get_recommendation_for_new_user(["i0"], cutoff=2)
    assert isinstance(recs, list)


class _LookAlikeRecommender:
    """Exposes the exact method names/signatures the capability gate looks
    for, but is NOT ``IALSRecommender`` and must NOT be treated as capable.

    This is the class the allow-list gate (as opposed to duck-typing) exists
    to defend against: a `hasattr(rec, "get_score_cold_user_from_features")`
    / `inspect.signature(rec.get_score_cold_user)` check would happily accept
    this class -- every symbol it looks for is present. Each method raises
    `AssertionError` rather than returning a real score so that a test
    reaching into the method body (i.e. the gate failed to stop it) fails
    loudly with an unambiguous, distinguishable error, not a passing test
    with garbage data.
    """

    def get_score_cold_user_from_features(self, matrix: object) -> object:
        raise AssertionError("must not be called: not on the allow-list")

    def get_item_embedding(self) -> object:
        raise AssertionError("must not be called: not on the allow-list")

    def compute_item_embedding_from_features(self, matrix: object) -> object:
        raise AssertionError("must not be called: not on the allow-list")

    def get_score_from_user_embedding(self, matrix: object) -> object:
        raise AssertionError("must not be called: not on the allow-list")

    def get_score_cold_user(self, X: object, user_features: object = None) -> object:
        raise AssertionError("must not be called: not on the allow-list")


def test_cold_start_gate_rejects_lookalike_class_not_on_allowlist() -> None:
    """A class exposing the right method names/signatures must still be
    refused if its class name is not on the explicit allow-list.

    This is the test that only makes sense once the gate is an allow-list
    rather than duck-typing: `_LookAlikeRecommender` above would sail through
    the OLD `hasattr`/`inspect.signature` checks (it defines every symbol
    they look for) and fail deep inside the method body with a bare
    `AssertionError` instead of the clean `ValueError` this gate exists to
    guarantee. Under the fixed allow-list gate, `type(self.recommender).__name__`
    is `"_LookAlikeRecommender"`, which is absent from
    `_idmap._FEATURE_CAPABLE_CLASS_NAMES`, so it must be refused BEFORE any
    of the methods above are ever invoked.
    """
    from recotem._idmap import IDMappedRecommender

    rec = _LookAlikeRecommender()
    state = {"version": 1, "columns": [], "bias_offset": 0, "n_features": 1}
    idm = IDMappedRecommender(
        rec,
        ["u1"],
        ["i1"],
        item_feature_state=state,
        user_feature_state=state,
    )

    with pytest.raises(ValueError, match="does not support"):
        idm.get_recommendation_for_cold_user({}, cutoff=1)

    with pytest.raises(ValueError, match="does not support"):
        idm.get_recommendation_for_cold_seeds(["i1"], {}, cutoff=1)

    with pytest.raises(ValueError, match="does not support"):
        idm.get_recommendation_for_new_user(["i1"], cutoff=1, user_features={})


def test_feature_capable_class_names_stay_in_sync_with_training() -> None:
    """`_idmap._FEATURE_CAPABLE_CLASS_NAMES` must equal
    `training.algorithms.FEATURE_CAPABLE_CLASS_NAMES` value-for-value.

    The two sets are hand-duplicated (`_idmap.py` is a neutral module that
    must not import `recotem.training` -- see the comment above
    `_FEATURE_CAPABLE_CLASS_NAMES`), so nothing at import time enforces they
    stay identical. A test is allowed to import both sides (tests are not
    bound by the training/serving import boundary), so this is the one
    place that can catch a silent divergence -- e.g. a new feature-capable
    irspack class added to only one of the two sets, which would otherwise
    fail closed (a capable model 400s on cold start at serve time) with no
    signal until an operator noticed.
    """
    from recotem._idmap import _FEATURE_CAPABLE_CLASS_NAMES
    from recotem.training.algorithms import FEATURE_CAPABLE_CLASS_NAMES

    assert _FEATURE_CAPABLE_CLASS_NAMES == FEATURE_CAPABLE_CLASS_NAMES
