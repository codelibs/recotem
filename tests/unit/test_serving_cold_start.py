# tests/unit/test_serving_cold_start.py
"""Cold-start coverage for the ``:recommend`` / ``:recommend-related`` verbs.

Case table (see ``routes.py`` docstrings for the full rationale):

| Case | Verb                | Trigger                          | Method                              |
|------|---------------------|-----------------------------------|--------------------------------------|
| A    | :recommend           | unknown user + user_features      | get_recommendation_for_cold_user     |
| B    | :recommend-related    | known/mixed seeds + user_features | get_recommendation_for_new_user(...) |
| C    | :recommend-related    | a cold seed carries item_features | get_recommendation_for_cold_seeds    |

Two fixtures back every test:

- ``client`` / ``fa`` recipe: a REAL, tiny (2-epoch) feature-aware IALS --
  same construction as ``tests/unit/test_idmap.py``'s ``fa_model`` fixture
  (40 users ``u0..u39``, 20 items ``i0..i19``, categorical ``band``/``genre``
  features) -- so cold-start calls exercise the genuine irspack API surface,
  not a mock standing in for it.
- ``plain_client`` / ``plain`` recipe: a TopPop model with NO feature state
  at all, and a user-id space that deliberately excludes ``"u1"`` so a
  request naming ``"u1"`` is a genuine cold-start attempt (not the
  known-user path) that must hit the "no feature state" guard and 400.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sps
import structlog.contextvars
import structlog.testing
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app

_FAKE_SHA256_HEX = "d" * 64  # 64 lowercase hex chars for a valid Sha256Hex marker


def _fa_recommender(*, n_threads: int | None = None) -> object:
    """Build the same real feature-aware IALS as test_idmap.py's fa_model.

    Not imported from there because that fixture is function-scoped and
    private to test_idmap.py; duplicated here (benchmarked at ~1-2ms to
    train) so this module has no cross-test-module fixture dependency.

    *n_threads* defaults to irspack's own auto-sizing -- the production
    default, and what every test here that does not assert an exact ranking
    should use. Pass ``1`` only to make the ranking itself reproducible; see
    ``stable_ranking_client``.
    """
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
        n_threads=n_threads,
    ).learn()
    return IDMappedRecommender(
        rec,
        [f"u{u}" for u in range(n_users)],
        [f"i{i}" for i in range(n_items)],
        item_feature_state=istate,
        user_feature_state=ustate,
    )


def _fa_recommender_with_numerical_item_feature() -> object:
    """Same construction as ``_fa_recommender``, plus a numerical ``tight``
    item column with a realistic small std.

    Reproduces review finding 1: a client-supplied ``numerical`` feature
    value that is extreme but finite (e.g. ``1e22``) standardizes
    (``recotem._features._row_values``) to a magnitude that makes irspack's
    per-request conjugate-gradient cold-start solve ill-conditioned,
    independently confirmed against this exact fixture shape to raise
    ``RuntimeError: Conjugate-gradient solver encountered a singular
    system.`` pre-fix.
    """
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
            "tight": rng.normal(loc=0.0, scale=0.5, size=n_items),
        }
    ).set_index("item_id")

    istate = build_encoder_state(
        item_df,
        [
            FeatureColumn(name="genre", encoding="categorical"),
            FeatureColumn(name="tight", encoding="numerical"),
        ],
    )
    F = encode(istate, item_df, index_order=[f"i{i}" for i in range(n_items)])

    rec = IALSRecommender(
        X,
        n_components=8,
        alpha0=0.1,
        train_epochs=2,
        random_seed=42,
        item_features=F,
        lambda_item_feature=1e-1,
    ).learn()
    return IDMappedRecommender(
        rec,
        [f"u{u}" for u in range(n_users)],
        [f"i{i}" for i in range(n_items)],
        item_feature_state=istate,
    )


def _fa_recommender_with_near_constant_numerical_item_feature() -> object:
    """Same construction as ``_fa_recommender_with_numerical_item_feature``,
    except the SERVE-TIME ``tight`` encoder state has its ``std`` overridden
    to a hand-crafted, near-zero-but-nonzero value (``1.36e-15``, matching
    the review's reported reproduction) after training completes.

    Training itself uses the item feature matrix built from the ORIGINAL,
    normally-scaled ``std`` (so this override cannot make training
    ill-conditioned); only ``item_feature_state`` -- read exclusively at
    serve-time cold-start scoring -- is mutated afterwards. This exists to
    prove the route-level ``FEATURE_VALUE_UNUSABLE`` `detail` message is
    truthful independent of *how* a tiny std ends up in a feature state: an
    ordinary-looking raw request value (e.g. ``1e4``, not ``1e22``) against
    a near-constant column must still 400 with a message that blames the
    STANDARDIZED value, not the raw one -- the raw value here is not, by any
    reasonable definition, extreme.
    """
    rec = _fa_recommender_with_numerical_item_feature()
    tight_spec = next(
        s for s in rec.item_feature_state["columns"] if s["name"] == "tight"
    )
    assert tight_spec["std"] > 1e-3, (
        "test setup invariant: the original std must be a normal, non-tiny "
        "value so this override is an observable change, not a no-op"
    )
    tight_spec["std"] = 1.36e-15
    return rec


def _fa_recommender_with_numerical_user_feature() -> object:
    """Same construction as ``_fa_recommender_with_numerical_item_feature``,
    but the ``tight`` numerical column lives on the USER side.

    Exercises case A (``get_score_cold_user_from_features``, reached via
    ``:recommend``) and case B (``get_score_cold_user``, reached via
    ``:recommend-related``) with a genuine numerical cold-start failure --
    the item-only fixture above only reaches case C. Independently confirmed
    against this exact fixture shape: ``{"band": "young", "tight": 1e22}``
    raises ``RuntimeError: Conjugate-gradient solver encountered a singular
    system.`` pre-fix for both ``get_score_cold_user_from_features`` and
    ``get_score_cold_user``.
    """
    from irspack import IALSRecommender

    from recotem._features import build_encoder_state, encode
    from recotem._idmap import IDMappedRecommender
    from recotem.recipe.models import FeatureColumn

    rng = np.random.default_rng(0)
    n_users, n_items = 40, 20
    X = sps.csr_matrix((rng.random((n_users, n_items)) > 0.6).astype(np.float64))
    user_df = pd.DataFrame(
        {
            "user_id": [f"u{u}" for u in range(n_users)],
            "band": ["young" if u % 2 else "old" for u in range(n_users)],
            "tight": rng.normal(loc=0.0, scale=0.5, size=n_users),
        }
    ).set_index("user_id")

    ustate = build_encoder_state(
        user_df,
        [
            FeatureColumn(name="band", encoding="categorical"),
            FeatureColumn(name="tight", encoding="numerical"),
        ],
    )
    U = encode(ustate, user_df, index_order=[f"u{u}" for u in range(n_users)])

    rec = IALSRecommender(
        X,
        n_components=8,
        alpha0=0.1,
        train_epochs=2,
        random_seed=42,
        user_features=U,
        lambda_user_feature=1e-1,
    ).learn()
    return IDMappedRecommender(
        rec,
        [f"u{u}" for u in range(n_users)],
        [f"i{i}" for i in range(n_items)],
        user_feature_state=ustate,
    )


def _entry(name: str, recommender: object) -> ModelEntry:
    return ModelEntry(
        name=name,
        recommender=recommender,
        header={},
        kid="t",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )


def _plain_recommender() -> object:
    """A TopPop model with no feature state and no "u1" in its user space."""
    from irspack import TopPopRecommender

    from recotem._idmap import IDMappedRecommender

    n_users, n_items = 3, 3
    X = sps.csr_matrix(np.ones((n_users, n_items)))
    rec = TopPopRecommender(X).learn()
    return IDMappedRecommender(rec, ["p1", "p2", "p3"], ["x1", "x2", "x3"])


def _toppop_with_feature_state_recommender() -> object:
    """A TopPop model that carries a non-None ``user_feature_state``.

    Task 9 persists the feature encoder state unconditionally -- even when
    the Optuna search winner is not feature-capable -- so the artifact
    header and payload agree. That means ``state is not None`` does NOT
    imply cold start is available: TopPop is not in
    ``_FEATURE_CAPABLE_CLASS_NAMES``, so Task 11's ``_require_capability``
    must still refuse it. This is a genuinely different code path from
    ``_plain_recommender`` (no state at all), which hits the earlier
    ``user_feature_state is None`` guard instead.
    """
    from irspack import TopPopRecommender

    from recotem._features import build_encoder_state
    from recotem._idmap import IDMappedRecommender
    from recotem.recipe.models import FeatureColumn

    n_users, n_items = 3, 3
    X = sps.csr_matrix(np.ones((n_users, n_items)))
    rec = TopPopRecommender(X).learn()
    user_df = pd.DataFrame(
        {"user_id": ["p1", "p2", "p3"], "band": ["young", "old", "young"]}
    ).set_index("user_id")
    ustate = build_encoder_state(
        user_df, [FeatureColumn(name="band", encoding="categorical")]
    )
    return IDMappedRecommender(
        rec,
        ["p1", "p2", "p3"],
        ["x1", "x2", "x3"],
        user_feature_state=ustate,
    )


@pytest.fixture()
def client() -> TestClient:
    registry = ModelRegistry()
    registry.replace("fa", _entry("fa", _fa_recommender()))
    return TestClient(build_v1_app(registry))


@pytest.fixture()
def stable_ranking_client() -> TestClient:
    """``client``'s model, but with a reproducible ranking.

    irspack sizes its thread pool automatically, and its cold-start solve
    reduces in whatever order the threads finish, so two identical calls can
    return slightly different scores. This fixture's catalog has genuinely
    near-tied items (measured: ranks 2-4 sit within 1.4e-6 of each other in
    float32), which that jitter is more than enough to reorder -- measured
    at 2 distinct top-5 orderings across 50 identical in-process calls, and
    it reordered ACROSS the cutoff boundary, so the top-5 membership itself
    changed, not just the order within it. Any test that asserts which items
    come back is therefore flaky against the default fixture.

    ``n_threads=1`` removes the reduction-order jitter and nothing else: it
    is the same model, same seed, same scores. Measured deterministic at 200
    identical calls per case (A, B, and C) vs. the 2 orderings above.

    Use ``client`` for anything that does not pin an exact ranking; this
    trades irspack's production thread default away for reproducibility, so
    it earns its keep only where the ranking IS the assertion.
    """
    registry = ModelRegistry()
    registry.replace("fa", _entry("fa", _fa_recommender(n_threads=1)))
    return TestClient(build_v1_app(registry))


@pytest.fixture()
def plain_client() -> TestClient:
    registry = ModelRegistry()
    registry.replace("plain", _entry("plain", _plain_recommender()))
    return TestClient(build_v1_app(registry))


@pytest.fixture()
def toppop_with_state_client() -> TestClient:
    registry = ModelRegistry()
    registry.replace(
        "toppop_state",
        _entry("toppop_state", _toppop_with_feature_state_recommender()),
    )
    return TestClient(build_v1_app(registry))


# ---------------------------------------------------------------------------
# :recommend -- case A (features only) + the "ignored, not rejected" rule
# ---------------------------------------------------------------------------


def test_known_user_unchanged_without_features(client: TestClient) -> None:
    r = client.post("/v1/recipes/fa:recommend", json={"user_id": "u1", "limit": 3})
    assert r.status_code == 200


def test_known_user_ignores_supplied_features(client: TestClient) -> None:
    """The learned embedding was fit to real interactions and strictly
    dominates the profile prior. Rejecting with 400 would break the natural
    client pattern of always sending the profile."""
    plain = client.post("/v1/recipes/fa:recommend", json={"user_id": "u1", "limit": 3})
    with_f = client.post(
        "/v1/recipes/fa:recommend",
        json={"user_id": "u1", "limit": 3, "user_features": {"band": "young"}},
    )
    assert with_f.status_code == 200
    assert with_f.json()["items"] == plain.json()["items"]


def test_unknown_user_with_features_is_served(client: TestClient) -> None:
    r = client.post(
        "/v1/recipes/fa:recommend",
        json={"user_id": "never_seen", "limit": 3, "user_features": {"band": "young"}},
    )
    assert r.status_code == 200
    assert len(r.json()["items"]) == 3


def test_unknown_user_without_features_still_404(client: TestClient) -> None:
    r = client.post("/v1/recipes/fa:recommend", json={"user_id": "never_seen"})
    assert r.status_code == 404
    # Response body is flat (``{"detail": ..., "code": ...}``), not nested
    # under an "error" key -- verified against the unmodified handler via
    # ``_http_exception_handler`` in ``tests/conftest.py``'s ``build_v1_app``.
    assert r.json()["code"] == "UNKNOWN_USER"


def test_features_on_model_without_state_is_400(plain_client: TestClient) -> None:
    r = plain_client.post(
        "/v1/recipes/plain:recommend",
        json={"user_id": "u1", "user_features": {"band": "young"}},
    )
    assert r.status_code == 400


def test_features_on_toppop_with_state_is_400_not_500(
    toppop_with_state_client: TestClient,
) -> None:
    """Task 9 persists feature state unconditionally, so a TopPop artifact
    can carry a non-None ``user_feature_state`` despite TopPop not being
    feature-capable. ``state is not None`` must NOT be read as "cold start
    is available" -- only ``_require_capability``'s class-name allow-list
    decides that (see ``_idmap.py``). This is a different code path from
    ``test_features_on_model_without_state_is_400`` above, which covers "no
    state at all" and never reaches ``_require_capability``. Must produce a
    clean 400 FEATURES_NOT_SUPPORTED, never a 500, and must not leak the raw
    feature value into the response body.
    """
    r = toppop_with_state_client.post(
        "/v1/recipes/toppop_state:recommend",
        json={
            "user_id": "never_seen",
            "user_features": {"band": "super_secret_value"},
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "FEATURES_NOT_SUPPORTED"
    assert "super_secret_value" not in r.text


# ---------------------------------------------------------------------------
# :recommend-related -- case B (features + history) and case C (cold seed)
# ---------------------------------------------------------------------------


def test_related_with_user_features_is_case_b(client: TestClient) -> None:
    r = client.post(
        "/v1/recipes/fa:recommend-related",
        json={"seed_items": ["i0"], "limit": 3, "user_features": {"band": "young"}},
    )
    assert r.status_code == 200


def test_related_with_cold_seed_is_case_c(client: TestClient) -> None:
    r = client.post(
        "/v1/recipes/fa:recommend-related",
        json={
            "seed_items": ["brand_new"],
            "limit": 3,
            "item_features": {"brand_new": {"genre": "action"}},
        },
    )
    assert r.status_code == 200


def test_related_all_known_seeds_unchanged(client: TestClient) -> None:
    r = client.post(
        "/v1/recipes/fa:recommend-related", json={"seed_items": ["i0"], "limit": 3}
    )
    assert r.status_code == 200


def test_unknown_seed_without_features_still_404(client: TestClient) -> None:
    r = client.post(
        "/v1/recipes/fa:recommend-related", json={"seed_items": ["nope"], "limit": 3}
    )
    assert r.status_code == 404
    assert r.json()["code"] == "UNKNOWN_SEED_ITEMS"


# ---------------------------------------------------------------------------
# Precedence proof -- case C must win over case B when both are supplied.
# ---------------------------------------------------------------------------
#
# A cold seed has no row in the seed interaction matrix, so if case B's
# solve (get_recommendation_for_new_user) ran on a request that names a
# cold seed, that seed would be silently dropped from the interaction
# history it is supposed to seed. This test names ONE cold seed with
# item_features AND supplies user_features, so both case B's and case C's
# trigger conditions are simultaneously true -- only the precedence rule
# decides which one runs.
# ---------------------------------------------------------------------------


def test_cold_seed_with_item_and_user_features_prefers_case_c(
    client: TestClient,
) -> None:
    r = client.post(
        "/v1/recipes/fa:recommend-related",
        json={
            "seed_items": ["brand_new"],
            "limit": 3,
            "user_features": {"band": "young"},
            "item_features": {"brand_new": {"genre": "action"}},
        },
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Metrics coverage -- every call site on all three cold-start paths must
# actually fire (Task 12 wiring).
#
# The reviewer mutated each of the five ``_metrics`` call sites in
# ``routes.py`` individually (case A ~:368-370, case C ~:531-533, case B
# ~:562-564) and found that deleting ``inc_cold_start_request`` on ANY of
# the three paths caused zero test failures, and deleting
# ``inc_feature_unknown_value`` on cases B/C also caused zero test
# failures -- only case A's ``inc_feature_unknown_value`` was pinned (by
# the single-case test this one replaces). An unknown feature value cannot
# fail the request -- it silently degrades to an all-zero segment -- so
# these counters are the only operator-facing signal; a metrics call that
# no test can kill is free to be deleted by any future refactor.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body", "expected_case", "expected_unknown"),
    [
        pytest.param(
            "/v1/recipes/fa:recommend",
            {
                "user_id": "never_seen",
                "limit": 3,
                "user_features": {"band": "martian"},
            },
            "features_only",
            [("fa", "user", "band")],
            id="case-a-features-only",
        ),
        pytest.param(
            "/v1/recipes/fa:recommend-related",
            {
                "seed_items": ["i0"],
                "limit": 3,
                "user_features": {"band": "martian"},
            },
            "features_and_history",
            [("fa", "user", "band")],
            id="case-b-features-and-history",
        ),
        pytest.param(
            "/v1/recipes/fa:recommend-related",
            {
                "seed_items": ["brand_new"],
                "limit": 3,
                "item_features": {"brand_new": {"genre": "martian_genre"}},
            },
            "cold_seeds",
            [("fa", "item", "genre")],
            id="case-c-cold-seeds",
        ),
    ],
)
def test_cold_start_metrics_fire_for_every_case(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    body: dict,
    expected_case: str,
    expected_unknown: list[tuple[str, str, str]],
) -> None:
    cold_start_calls: list[tuple[str, str]] = []
    unknown_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_cold_start_request",
        lambda recipe, case: cold_start_calls.append((recipe, case)),
    )
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_value",
        lambda recipe, side, column: unknown_calls.append((recipe, side, column)),
    )
    r = client.post(path, json=body)
    assert r.status_code == 200
    assert cold_start_calls == [("fa", expected_case)]
    assert unknown_calls == expected_unknown


# ---------------------------------------------------------------------------
# Unknown feature COLUMN (a request key outside the recipe)
# ---------------------------------------------------------------------------
#
# ``_features._row_values`` iterates ``state["columns"]`` and does
# ``values.get(name)``, so a request key the recipe never declared is simply
# never read: a fully typo'd body encodes bias-only and is byte-identical to
# sending ``{}``. That is a strictly MORE severe silent degradation than an
# unknown VALUE (which already has a counter), so it must not be the one
# place ``encode_one``'s own rule -- "an unknown category degrades the
# recommendation silently, so it must not also be invisible" -- goes
# unapplied. The response stays 200 (deliberately: rejecting would break
# clients that legitimately send a superset profile); the counter is the
# signal.
#
# The column name is deliberately NOT a metric label. Unlike
# ``inc_feature_unknown_value``'s ``column`` (bounded by the operator's own
# recipe), an unknown column name comes from request input -- an unbounded
# label there is a metrics-cardinality DoS.


def test_unknown_user_feature_column_is_counted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_column",
        lambda recipe, side: calls.append((recipe, side)),
    )
    r = client.post(
        "/v1/recipes/fa:recommend",
        json={"user_id": "never_seen", "limit": 3, "user_features": {"bandd": "young"}},
    )
    assert r.status_code == 200
    assert calls == [("fa", "user")]


def test_fully_typoed_feature_body_is_counted_not_silent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported case: every key typo'd, so the encode is bias-only and the
    response is byte-identical to sending ``user_features: {}``. Before the
    counter this was indistinguishable from a correct request."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_column",
        lambda recipe, side: calls.append((recipe, side)),
    )
    typoed = client.post(
        "/v1/recipes/fa:recommend",
        json={"user_id": "never_seen", "limit": 3, "user_features": {"bandd": "young"}},
    )
    empty = client.post(
        "/v1/recipes/fa:recommend",
        json={"user_id": "never_seen", "limit": 3, "user_features": {}},
    )
    assert typoed.json()["items"] == empty.json()["items"], (
        "precondition: a typo'd column is invisible in the RESPONSE -- the "
        "counter is the only place it can surface"
    )
    assert calls == [("fa", "user")], "only the typo'd request may increment"


def test_multiple_unknown_columns_count_once_per_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-request, not per-key: without a ``column`` label, n increments are
    an uninterpretable magnitude (3 typos once vs 1 typo 3 times collide),
    whereas one-per-request stays normalizable against
    ``recotem_v1_requests_total``."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_column",
        lambda recipe, side: calls.append((recipe, side)),
    )
    r = client.post(
        "/v1/recipes/fa:recommend",
        json={
            "user_id": "never_seen",
            "limit": 3,
            "user_features": {"bandd": "young", "agee": 30, "cityy": "tokyo"},
        },
    )
    assert r.status_code == 200
    assert calls == [("fa", "user")]


def test_declared_column_does_not_count_as_unknown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_column",
        lambda recipe, side: calls.append((recipe, side)),
    )
    r = client.post(
        "/v1/recipes/fa:recommend",
        json={"user_id": "never_seen", "limit": 3, "user_features": {"band": "young"}},
    )
    assert r.status_code == 200
    assert calls == []


def test_unknown_value_is_not_an_unknown_column(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``band`` IS declared; only its value is out-of-vocabulary. That is the
    pre-existing ``inc_feature_unknown_value`` signal and must not also trip
    the column counter -- the two conditions have different remedies."""
    column_calls: list[tuple[str, str]] = []
    value_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_column",
        lambda recipe, side: column_calls.append((recipe, side)),
    )
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_value",
        lambda recipe, side, column: value_calls.append((recipe, side, column)),
    )
    r = client.post(
        "/v1/recipes/fa:recommend",
        json={
            "user_id": "never_seen",
            "limit": 3,
            "user_features": {"band": "martian"},
        },
    )
    assert r.status_code == 200
    assert column_calls == []
    assert value_calls == [("fa", "user", "band")]


def test_unknown_user_feature_column_counted_on_case_b(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_column",
        lambda recipe, side: calls.append((recipe, side)),
    )
    r = client.post(
        "/v1/recipes/fa:recommend-related",
        json={"seed_items": ["i0"], "limit": 3, "user_features": {"bandd": "young"}},
    )
    assert r.status_code == 200
    assert calls == [("fa", "user")]


def test_unknown_item_feature_column_counted_on_case_c(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_column",
        lambda recipe, side: calls.append((recipe, side)),
    )
    r = client.post(
        "/v1/recipes/fa:recommend-related",
        json={
            "seed_items": ["brand_new"],
            "limit": 3,
            "item_features": {"brand_new": {"genree": "action"}},
        },
    )
    assert r.status_code == 200
    assert calls == [("fa", "item")]


def test_cold_seed_fanout_counts_once_per_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many cold seeds sharing one typo'd key must increment once, not once
    per seed -- otherwise a 100-seed request drowns the signal."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_column",
        lambda recipe, side: calls.append((recipe, side)),
    )
    r = client.post(
        "/v1/recipes/fa:recommend-related",
        json={
            "seed_items": ["new_a", "new_b", "new_c"],
            "limit": 3,
            "item_features": {
                "new_a": {"genree": "action"},
                "new_b": {"genree": "drama"},
                "new_c": {"genree": "action"},
            },
        },
    )
    assert r.status_code == 200
    assert calls == [("fa", "item")]


def test_item_features_for_known_seed_are_not_inspected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A KNOWN seed's features entry is never encoded (``_idmap`` uses its
    learned embedding and skips the dict entirely), so a typo inside it is
    not a degradation and must not be counted. ``i0`` is known; ``brand_new``
    is what actually reaches the encoder."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_column",
        lambda recipe, side: calls.append((recipe, side)),
    )
    r = client.post(
        "/v1/recipes/fa:recommend-related",
        json={
            "seed_items": ["i0", "brand_new"],
            "limit": 3,
            "item_features": {
                "i0": {"totally_bogus": "x"},
                "brand_new": {"genre": "action"},
            },
        },
    )
    assert r.status_code == 200
    assert calls == []


# ---------------------------------------------------------------------------
# Batch verbs -- same cold-start branch logic, reached through the shared
# ``_resolve_recommend`` / ``_resolve_recommend_related`` helpers so a
# per-element failure degrades that element only (``BatchResultErr``),
# never the whole batch.
# ---------------------------------------------------------------------------


def test_batch_recommend_mixes_known_and_cold(client: TestClient) -> None:
    r = client.post(
        "/v1/recipes/fa:batch-recommend",
        json={
            "requests": [
                {"user_id": "u1", "limit": 2},
                {
                    "user_id": "never_seen",
                    "limit": 2,
                    "user_features": {"band": "young"},
                },
            ]
        },
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "ok"


def test_batch_cold_element_without_features_errors_only_that_element(
    client: TestClient,
) -> None:
    r = client.post(
        "/v1/recipes/fa:batch-recommend",
        json={
            "requests": [
                {"user_id": "u1", "limit": 2},
                {"user_id": "never_seen", "limit": 2},
            ]
        },
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "error"
    assert results[1]["error"]["code"] == "UNKNOWN_USER"


def test_batch_features_on_model_without_state_errors_only_that_element(
    plain_client: TestClient,
) -> None:
    r = plain_client.post(
        "/v1/recipes/plain:batch-recommend",
        json={
            "requests": [
                {"user_id": "p1", "limit": 2},
                {
                    "user_id": "u1",
                    "limit": 2,
                    "user_features": {"band": "young"},
                },
            ]
        },
    )
    assert r.status_code == 200
    results = r.json()["results"]
    # "p1" is a known user in the plain recipe's own space -- unaffected by
    # this change.
    assert results[0]["status"] == "ok"
    # "u1" is deliberately excluded from plain's user space (see module
    # docstring), so this is a genuine cold-start attempt that must hit the
    # "no feature state" guard rather than the known-user path.
    assert results[1]["status"] == "error"
    assert results[1]["error"]["code"] == "FEATURES_NOT_SUPPORTED"


def test_batch_related_cold_seed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case C on the batch verb must fire both ``inc_cold_start_request``
    (always) and ``inc_feature_unknown_value`` (for the out-of-vocabulary
    ``genre`` value below) -- mirroring the single-verb
    ``test_cold_start_metrics_fire_for_every_case``'s ``case-c-cold-seeds``
    parametrization. Without an out-of-vocabulary value, ``unknown_columns``
    would be empty and ``inc_feature_unknown_value`` would never fire,
    leaving that call site unpinned by this test.
    """
    cold_start_calls: list[tuple[str, str]] = []
    unknown_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_cold_start_request",
        lambda recipe, case: cold_start_calls.append((recipe, case)),
    )
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_value",
        lambda recipe, side, column: unknown_calls.append((recipe, side, column)),
    )
    r = client.post(
        "/v1/recipes/fa:batch-recommend-related",
        json={
            "requests": [
                {
                    "seed_items": ["brand_new"],
                    "limit": 2,
                    "item_features": {"brand_new": {"genre": "martian_genre"}},
                },
            ]
        },
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["status"] == "ok"
    assert cold_start_calls == [("fa", "cold_seeds")]
    assert unknown_calls == [("fa", "item", "genre")]


def test_batch_related_with_user_features_is_case_b(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known seed plus ``user_features`` must take case B (the joint
    solve), not silently fall back to the plain seed-only path -- which
    would also return ``status: "ok"`` (since "i0" is a known seed) without
    ever exercising the feature prior. Asserting on the ``inc_cold_start_request``
    call, not just on ``status == "ok"``, is what makes this test able to
    fail against that bug.

    Uses an out-of-vocabulary ``band`` value (``"martian"``) rather than a
    valid one so that ``unknown_columns`` is non-empty and
    ``inc_feature_unknown_value`` actually fires -- pinning that call site
    too, not just ``inc_cold_start_request``.
    """
    cold_start_calls: list[tuple[str, str]] = []
    unknown_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_cold_start_request",
        lambda recipe, case: cold_start_calls.append((recipe, case)),
    )
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_value",
        lambda recipe, side, column: unknown_calls.append((recipe, side, column)),
    )
    r = client.post(
        "/v1/recipes/fa:batch-recommend-related",
        json={
            "requests": [
                {
                    "seed_items": ["i0"],
                    "limit": 3,
                    "user_features": {"band": "martian"},
                },
            ]
        },
    )
    assert r.json()["results"][0]["status"] == "ok"
    assert cold_start_calls == [("fa", "features_and_history")]
    assert unknown_calls == [("fa", "user", "band")]


def test_batch_related_cold_seed_with_user_features_prefers_case_c(
    client: TestClient,
) -> None:
    """Batch counterpart of ``test_cold_seed_with_item_and_user_features_prefers_case_c``.

    Both case B's and case C's trigger conditions are simultaneously true;
    only the precedence rule (case C wins) decides which solve runs. If case
    B ran instead, it would treat ``"brand_new"`` (absent from the id-map) as
    part of the seed interaction history and the underlying irspack call
    would raise -- surfacing as a batch element error rather than "ok".
    """
    r = client.post(
        "/v1/recipes/fa:batch-recommend-related",
        json={
            "requests": [
                {
                    "seed_items": ["brand_new"],
                    "limit": 3,
                    "user_features": {"band": "young"},
                    "item_features": {"brand_new": {"genre": "action"}},
                },
            ]
        },
    )
    result = r.json()["results"][0]
    assert result["status"] == "ok", result


def test_batch_related_unknown_seed_without_features_errors_only_that_element(
    client: TestClient,
) -> None:
    r = client.post(
        "/v1/recipes/fa:batch-recommend-related",
        json={
            "requests": [
                {"seed_items": ["i0"], "limit": 2},
                {"seed_items": ["nope"], "limit": 2},
            ]
        },
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "error"
    assert results[1]["error"]["code"] == "UNKNOWN_SEED_ITEMS"


def test_batch_related_item_features_on_model_without_state_is_error(
    plain_client: TestClient,
) -> None:
    r = plain_client.post(
        "/v1/recipes/plain:batch-recommend-related",
        json={
            "requests": [
                {
                    "seed_items": ["brand_new"],
                    "limit": 2,
                    "item_features": {"brand_new": {"genre": "action"}},
                },
            ]
        },
    )
    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["status"] == "error"
    assert result["error"]["code"] == "FEATURES_NOT_SUPPORTED"


def test_batch_cold_start_metrics_fire(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same mutation-pinning intent as ``test_cold_start_metrics_fire_for_every_case``,
    but through the batch wiring -- proves the shared helper's metrics calls
    fire identically regardless of which verb's loop invoked it."""
    cold_start_calls: list[tuple[str, str]] = []
    unknown_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_cold_start_request",
        lambda recipe, case: cold_start_calls.append((recipe, case)),
    )
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_value",
        lambda recipe, side, column: unknown_calls.append((recipe, side, column)),
    )
    r = client.post(
        "/v1/recipes/fa:batch-recommend",
        json={
            "requests": [
                {
                    "user_id": "never_seen",
                    "limit": 3,
                    "user_features": {"band": "martian"},
                },
            ]
        },
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["status"] == "ok"
    assert cold_start_calls == [("fa", "features_only")]
    assert unknown_calls == [("fa", "user", "band")]


# ---------------------------------------------------------------------------
# Finding 1 (Task 14 review): ``idx`` must ride along on the
# ``recommender_unexpected_key_error`` event for BOTH batch verbs.
#
# Pre-refactor, ``batch_recommend`` / ``batch_recommend_related`` each had
# their own ``except KeyError:`` block that logged this event with
# ``idx=idx``, so an operator could map the log line straight to
# ``results[idx]``. The refactor moved the log call into the shared
# ``_resolve_recommend`` / ``_resolve_recommend_related`` resolvers, which
# have no batch-index parameter and log only the single-verb's fields
# (``user_id_hash`` / ``seed_items_count``) -- silently dropping ``idx`` for
# the batch call sites. ``_bind_batch_idx`` (routes.py) restores it by
# binding ``idx`` as a structlog contextvar for the duration of each batch
# element's processing, so it is merged into any log event emitted anywhere
# during that element -- including ones raised deep inside the resolvers --
# without the resolvers' signatures needing to change.
#
# Each test below uses TWO elements, both of which independently trigger
# the "recommender layout unexpected" KeyError path, and asserts each
# element's log event carries ITS OWN idx (0 and 1 respectively, not the
# other element's) -- this is what would catch a regression where binding
# happened once outside the loop (or leaked from the previous iteration)
# instead of being freshly bound per element.
# ---------------------------------------------------------------------------


def test_batch_recommend_unexpected_key_error_logs_idx_per_element() -> None:
    """Simulates an internal irspack layout bug: both users ARE in the
    id-map (so this is not the "genuine unknown user" 404 path), but
    ``get_recommendation_for_known_user_id`` raises ``KeyError`` anyway for
    both. Each element's ``recommender_unexpected_key_error`` event must
    carry that element's own ``idx`` -- 0 and 1, never the other's.
    """
    rec = MagicMock()
    rec._mapper.user_id_to_index = {"u-known0": 0, "u-known1": 1}
    rec.get_recommendation_for_known_user_id.side_effect = KeyError("irspack-internal")
    entry = _entry("demo", rec)
    registry = ModelRegistry()
    registry.replace("demo", entry)
    client = TestClient(build_v1_app(registry))

    with structlog.testing.capture_logs(
        processors=(structlog.contextvars.merge_contextvars,)
    ) as cap:
        r = client.post(
            "/v1/recipes/demo:batch-recommend",
            json={
                "requests": [
                    {"user_id": "u-known0", "limit": 2},
                    {"user_id": "u-known1", "limit": 2},
                ]
            },
        )

    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["status"] == "error"
    assert results[0]["error"]["code"] == "INTERNAL_ERROR"
    assert results[1]["status"] == "error"
    assert results[1]["error"]["code"] == "INTERNAL_ERROR"

    key_error_events = [
        e for e in cap if e.get("event") == "recommender_unexpected_key_error"
    ]
    assert len(key_error_events) == 2, (
        f"expected one recommender_unexpected_key_error event per element; "
        f"got: {key_error_events!r}"
    )
    # Each event must carry ITS OWN idx -- not the other element's (proves
    # no cross-iteration contextvar leak).
    assert key_error_events[0]["idx"] == 0
    assert key_error_events[1]["idx"] == 1


def test_batch_recommend_related_unexpected_key_error_logs_idx_per_element() -> None:
    """Same as above for ``:batch-recommend-related``'s "all seeds known,
    no user_features" path: both seeds ARE in the id-map, but
    ``get_recommendation_for_new_user`` raises ``KeyError`` anyway for both.
    """
    rec = MagicMock()
    rec._mapper.item_id_to_index = {"s0": 0, "s1": 1}
    rec.get_recommendation_for_new_user.side_effect = KeyError("irspack-internal")
    entry = _entry("demo", rec)
    registry = ModelRegistry()
    registry.replace("demo", entry)
    client = TestClient(build_v1_app(registry))

    with structlog.testing.capture_logs(
        processors=(structlog.contextvars.merge_contextvars,)
    ) as cap:
        r = client.post(
            "/v1/recipes/demo:batch-recommend-related",
            json={
                "requests": [
                    {"seed_items": ["s0"], "limit": 2},
                    {"seed_items": ["s1"], "limit": 2},
                ]
            },
        )

    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["status"] == "error"
    assert results[0]["error"]["code"] == "INTERNAL_ERROR"
    assert results[1]["status"] == "error"
    assert results[1]["error"]["code"] == "INTERNAL_ERROR"

    key_error_events = [
        e for e in cap if e.get("event") == "recommender_unexpected_key_error"
    ]
    assert len(key_error_events) == 2, (
        f"expected one recommender_unexpected_key_error event per element; "
        f"got: {key_error_events!r}"
    )
    assert key_error_events[0]["idx"] == 0
    assert key_error_events[1]["idx"] == 1


# ---------------------------------------------------------------------------
# Review finding 1 (MEDIUM): a client-supplied extreme-but-finite numerical
# feature value must never produce an HTTP 500.
#
# Reproduced end-to-end against a REAL trained feature-aware IALS model (not
# mocked): ``item_features={"tight": 1e22}`` on a cold seed makes irspack's
# per-request conjugate-gradient cold-start solve ill-conditioned, and
# irspack's native core raises a bare ``RuntimeError`` with no awareness the
# input came from an untrusted client. Pre-fix, that propagated unhandled
# through ``_resolve_recommend_related`` to the router's bare ``except
# Exception`` and became ``500 {"detail": "internal error", "code":
# "INTERNAL_ERROR"}``.
# ---------------------------------------------------------------------------


@pytest.fixture()
def numerical_client() -> TestClient:
    registry = ModelRegistry()
    registry.replace(
        "fa_num", _entry("fa_num", _fa_recommender_with_numerical_item_feature())
    )
    return TestClient(build_v1_app(registry))


def test_extreme_numerical_item_feature_value_returns_4xx_not_500(
    numerical_client: TestClient,
) -> None:
    """The exact reproduction from the review report: POST
    ``:recommend-related`` with a cold seed whose ``item_features`` carries
    an extreme-but-finite ``numerical`` value must return a 4xx with an
    actionable code -- never a 500.
    """
    r = numerical_client.post(
        "/v1/recipes/fa_num:recommend-related",
        json={
            "seed_items": ["zzz"],
            "limit": 2,
            "item_features": {"zzz": {"tight": 1e22}},
        },
    )
    assert 400 <= r.status_code < 500, (
        f"a client-supplied value must never produce a 500; got "
        f"{r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["code"] == "FEATURE_VALUE_UNUSABLE", body
    # Review finding 2: the detail message must not claim the RAW supplied
    # value (1e22 here) was extreme in some absolute sense -- it must blame
    # the STANDARDIZED value, which is the thing that is actually unusable.
    assert "extreme magnitude" not in body["detail"], body["detail"]
    assert "standardized value" in body["detail"], body["detail"]


def test_over_cap_feature_value_is_rejected_at_validation_422(
    client: TestClient,
) -> None:
    """A cold-start feature value longer than the per-value character cap is
    rejected at request validation (422) -- the same status every other
    request-schema cap returns -- before it can reach the multi_label
    tokenizer. Pins the HTTP status the schema-level ``AfterValidator``
    produces (the unit tests assert only the model-level ValidationError), and
    matches docs/recipe-reference.md + CHANGELOG.
    """
    from recotem.serving.schemas import _MAX_FEATURE_VALUE_CHARS

    r = client.post(
        "/v1/recipes/fa:recommend",
        json={
            "user_id": "unseen_u",
            "limit": 3,
            "user_features": {"band": "a" * (_MAX_FEATURE_VALUE_CHARS + 1)},
        },
    )
    assert r.status_code == 422, r.text
    # A value one char under the cap is NOT rejected by the cap (premise guard
    # so this cannot pass vacuously by rejecting everything).
    r_ok = client.post(
        "/v1/recipes/fa:recommend",
        json={
            "user_id": "unseen_u",
            "limit": 3,
            "user_features": {"band": "a" * _MAX_FEATURE_VALUE_CHARS},
        },
    )
    assert r_ok.status_code != 422, r_ok.text


@pytest.fixture()
def near_constant_numerical_client() -> TestClient:
    registry = ModelRegistry()
    registry.replace(
        "fa_near_constant",
        _entry(
            "fa_near_constant",
            _fa_recommender_with_near_constant_numerical_item_feature(),
        ),
    )
    return TestClient(build_v1_app(registry))


# ---------------------------------------------------------------------------
# Review finding 2 (IMPORTANT): the 400's message is false for a
# near-constant column. A column whose training std is tiny (e.g. 1.36e-15,
# not exactly 0.0) turns an entirely ORDINARY raw request value into an
# astronomically large standardized one -- the same 400 as the 1e22-on-a-
# normal-column case above, but the raw value itself (1e5 here) is not
# extreme by any reasonable definition. The message must describe the
# STANDARDIZED value as unusable, not claim the client's raw value was
# extreme.
#
# 1e5, not 1e4: independently verified (see the mutation proof in the task
# report) that against THIS fixture's exact dimensionality/lambda, the
# solver singularity crossover for std=1.36e-15 falls between 1e4 (still
# 200) and 1e5 (400) -- docs/api-reference.md already discloses that this
# crossover is not a fixed constant across models, so the test uses a value
# empirically confirmed to trip it here rather than assuming the review's
# reported number transfers exactly to this fixture's shape.
# ---------------------------------------------------------------------------


def test_ordinary_numerical_value_on_near_constant_column_returns_4xx_with_honest_message(
    near_constant_numerical_client: TestClient,
) -> None:
    """An unremarkable raw value (1e5) against a near-constant column (std
    1.36e-15) must still 400 -- via the exact same solver-singularity
    mechanism as the 1e22-on-a-normal-column case -- but the message must
    not blame the raw magnitude, which is not extreme here."""
    r = near_constant_numerical_client.post(
        "/v1/recipes/fa_near_constant:recommend-related",
        json={
            "seed_items": ["zzz"],
            "limit": 2,
            "item_features": {"zzz": {"tight": 1e5}},
        },
    )
    assert 400 <= r.status_code < 500, (
        f"a client-supplied ordinary value must never produce a 500; got "
        f"{r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["code"] == "FEATURE_VALUE_UNUSABLE", body
    assert "extreme magnitude" not in body["detail"], body["detail"]
    assert "standardized value" in body["detail"], body["detail"]


# ---------------------------------------------------------------------------
# Review finding 3 (IMPORTANT): a directly-supplied +inf/-inf numerical value
# was a silent no-op -- byte-identical to omitting the column entirely, with
# no `unknown` entry recorded and no `recotem_v1_feature_unknown_value_total`
# increment. Contrast with an unknown CATEGORICAL value (already covered
# elsewhere, e.g. ``test_cold_start_metrics_fire_for_every_case``), which
# correctly fires the counter today.
# ---------------------------------------------------------------------------


def test_infinite_numerical_item_feature_fires_unknown_value_counter(
    numerical_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """+inf on a numerical feature must still degrade like a missing value
    (200, the feature contributes nothing to the row) but MUST fire
    ``inc_feature_unknown_value`` -- pre-fix this was a silent no-op,
    indistinguishable from omitting the column (see the contrast test
    below).

    Sends the raw JSON body as bytes with a literal ``1e309`` number token
    (the review's exact reproduction) rather than via the ``json=`` kwarg's
    Python-side encoding: standard-compliant JSON has no ``Infinity``
    literal, so httpx's own encoder (``allow_nan=False``) refuses to
    serialize a Python ``float("inf")`` object -- but ``1e309`` is an
    ordinary, spec-legal JSON number token that merely overflows float64 on
    the *server's* parse, which is exactly the real-world attack surface
    this finding describes.
    """
    unknown_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_value",
        lambda recipe, side, column: unknown_calls.append((recipe, side, column)),
    )
    raw_body = (
        b'{"seed_items": ["zzz"], "limit": 2, '
        b'"item_features": {"zzz": {"tight": 1e309}}}'
    )
    r = numerical_client.post(
        "/v1/recipes/fa_num:recommend-related",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    assert unknown_calls == [("fa_num", "item", "tight")]


def test_omitted_numerical_item_feature_does_not_fire_unknown_value_counter(
    numerical_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contrast case: simply OMITTING the numerical column entirely (rather
    than supplying +inf) must still NOT fire the counter -- a missing value
    is a separate, deliberately uncounted gap that this fix must not widen.
    """
    unknown_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "recotem.serving.routes._metrics.inc_feature_unknown_value",
        lambda recipe, side, column: unknown_calls.append((recipe, side, column)),
    )
    r = numerical_client.post(
        "/v1/recipes/fa_num:recommend-related",
        json={
            "seed_items": ["zzz"],
            "limit": 2,
            "item_features": {"zzz": {}},
        },
    )
    assert r.status_code == 200, r.text
    assert unknown_calls == []


def test_extreme_numerical_user_feature_value_on_incapable_side_stays_400(
    numerical_client: TestClient,
) -> None:
    """``fa_num`` carries item features only (no ``user_feature_state``), so
    a request combining a known seed with an extreme ``user_features`` value
    must still hit the pre-existing "no user feature state"
    ``FEATURES_NOT_SUPPORTED`` guard, not the new numerical-error path --
    proving the new exception handler did not shadow the existing
    capability check for an unrelated side. The genuinely
    numerically-unstable case B path (``get_score_cold_user`` with a real
    user feature state) is covered at the ``_idmap`` layer by
    ``test_new_user_with_features_wraps_runtime_error`` in
    ``tests/unit/test_idmap.py``.
    """
    r = numerical_client.post(
        "/v1/recipes/fa_num:recommend-related",
        json={
            "seed_items": ["i0"],
            "limit": 2,
            "user_features": {"tight": 1e22},
        },
    )
    assert 400 <= r.status_code < 500
    assert r.json()["code"] == "FEATURES_NOT_SUPPORTED"


# ---------------------------------------------------------------------------
# Review round 2, Important 1: the ``except RuntimeError`` at the three
# ``_idmap.py`` call sites was previously BLANKET (no message check), so a
# non-numerical server fault (e.g. ``trainer is None``) was mislabeled as a
# 400 ``FEATURE_VALUE_UNUSABLE`` telling the client their feature value was
# at fault. The fix scopes the catch to a verified allow-list of numerical
# failure signatures; anything else must still reach the router's generic
# handler as a 500 -- proven end-to-end (route layer, real trained model)
# here. The mock-level proof for each of the three ``_idmap.py`` call sites
# lives in ``tests/unit/test_idmap.py``.
# ---------------------------------------------------------------------------


def test_non_numerical_runtime_error_from_cold_start_returns_500_not_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tell that the pre-fix breadth was accidental: with
    ``recommender.trainer = None``, irspack's ``trainer_as_ials`` raises
    ``RuntimeError("tried to fetch trainer before the training.")`` -- a
    server fault, not a client-value problem. A blanket ``except
    RuntimeError`` at ``get_score_cold_user_from_features``'s call site
    (``_idmap.py``) mapped this to ``400 FEATURE_VALUE_UNUSABLE`` with a
    message actively blaming the client. Post-fix, only messages matching
    the verified numerical-failure signatures are wrapped; everything else
    must reach the router's generic handler as ``500 INTERNAL_ERROR``.
    """
    idmapped = _fa_recommender()
    registry = ModelRegistry()
    registry.replace("fa", _entry("fa", idmapped))
    local_client = TestClient(build_v1_app(registry), raise_server_exceptions=False)

    monkeypatch.setattr(
        idmapped.recommender,
        "get_score_cold_user_from_features",
        MagicMock(
            side_effect=RuntimeError("tried to fetch trainer before the training.")
        ),
    )
    r = local_client.post(
        "/v1/recipes/fa:recommend",
        json={
            "user_id": "never_seen",
            "limit": 2,
            "user_features": {"band": "young"},
        },
    )
    assert r.status_code == 500, (
        f"a non-numerical server fault must never be mislabeled as a client "
        f"4xx; got {r.status_code}: {r.text}"
    )
    assert r.json()["code"] == "INTERNAL_ERROR", r.json()


# ---------------------------------------------------------------------------
# Review round 2, Minor 5: 3 of the 4 new cold-start route handlers had no
# test at all -- only ``:recommend-related`` (single) did, above. The
# reviewer verified all three empirically; this pins that verification as a
# standing regression guard: ``:recommend`` (routes.py:583, case A),
# ``:batch-recommend`` (routes.py:838/845, batch case A), and
# ``:batch-recommend-related`` (routes.py:1012/1019, batch case C).
# ---------------------------------------------------------------------------


@pytest.fixture()
def numerical_user_client() -> TestClient:
    registry = ModelRegistry()
    registry.replace(
        "fa_num_user",
        _entry("fa_num_user", _fa_recommender_with_numerical_user_feature()),
    )
    return TestClient(build_v1_app(registry))


def test_extreme_numerical_user_feature_value_on_recommend_returns_4xx_not_500(
    numerical_user_client: TestClient,
) -> None:
    """Case A through the single ``:recommend`` verb (routes.py:583,
    previously untested): an unknown user cold-started from an
    extreme-but-finite ``numerical`` ``user_features`` value must return a
    4xx with ``FEATURE_VALUE_UNUSABLE`` -- never a 500.
    """
    r = numerical_user_client.post(
        "/v1/recipes/fa_num_user:recommend",
        json={
            "user_id": "never_seen",
            "limit": 2,
            "user_features": {"band": "young", "tight": 1e22},
        },
    )
    assert 400 <= r.status_code < 500, (
        f"a client-supplied value must never produce a 500; got "
        f"{r.status_code}: {r.text}"
    )
    assert r.json()["code"] == "FEATURE_VALUE_UNUSABLE", r.json()


def test_batch_extreme_numerical_user_feature_value_errors_only_that_element(
    numerical_user_client: TestClient,
) -> None:
    """Batch counterpart on ``:batch-recommend`` (routes.py:838/845,
    previously untested): the numerical failure must degrade only the
    offending element, leaving the batch response itself ``200`` and the
    other element ``ok``.
    """
    r = numerical_user_client.post(
        "/v1/recipes/fa_num_user:batch-recommend",
        json={
            "requests": [
                {
                    "user_id": "never_seen_1",
                    "limit": 2,
                    "user_features": {"band": "young", "tight": 1e22},
                },
                {
                    "user_id": "never_seen_2",
                    "limit": 2,
                    "user_features": {"band": "old"},
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["status"] == "error"
    assert results[0]["error"]["code"] == "FEATURE_VALUE_UNUSABLE"
    assert results[1]["status"] == "ok", results[1]


def test_batch_extreme_numerical_item_feature_value_on_related_errors_only_that_element(
    numerical_client: TestClient,
) -> None:
    """Batch counterpart on ``:batch-recommend-related`` (routes.py:1012/1019,
    previously untested): case C's numerical failure must degrade only the
    offending element.
    """
    r = numerical_client.post(
        "/v1/recipes/fa_num:batch-recommend-related",
        json={
            "requests": [
                {
                    "seed_items": ["zzz"],
                    "limit": 2,
                    "item_features": {"zzz": {"tight": 1e22}},
                },
                {"seed_items": ["i0"], "limit": 2},
            ]
        },
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["status"] == "error"
    assert results[0]["error"]["code"] == "FEATURE_VALUE_UNUSABLE"
    assert results[1]["status"] == "ok", results[1]


# ---------------------------------------------------------------------------
# exclude_items means the same thing in every case
# ---------------------------------------------------------------------------
#
# ``exclude_items`` is post-filtered off the ranker's output
# (``routes._build_items``) and is never pushed down into the ranker as
# ``forbidden_item_ids``. It therefore removes items from a page instead of
# freeing a slot for a replacement: ``limit`` is a ceiling, not a promise.
# That is what every pre-existing verb has always done, so no cold-start case
# may read it differently -- otherwise adding ``user_features`` to an
# otherwise identical request would silently change how many items come back.

_EXCLUDE_ITEMS_CASES = (
    pytest.param(
        "recommend",
        {"user_id": "never_seen", "user_features": {"band": "young"}},
        id="case-a-cold-user-features-only",
    ),
    pytest.param(
        "recommend-related",
        {"seed_items": ["i0"], "user_features": {"band": "young"}},
        id="case-b-known-seed-plus-user-features",
    ),
    pytest.param(
        "recommend-related",
        {
            "seed_items": ["brand_new"],
            "item_features": {"brand_new": {"genre": "action"}},
        },
        id="case-c-cold-seed-item-features",
    ),
)


@pytest.mark.parametrize(("verb", "body"), _EXCLUDE_ITEMS_CASES)
def test_exclude_items_truncates_and_never_backfills(
    stable_ranking_client: TestClient, verb: str, body: dict
) -> None:
    """Excluding 3 of a case's own top 5 must leave exactly those 5 minus 3.

    The ids to exclude are derived from each case's OWN unexcluded page
    rather than hardcoded, so the test states the invariant -- "excluding k
    of your own top-`limit` returns the other `limit - k`, unchanged and in
    order" -- independently of how any one case happens to rank the catalog.

    Asserting the surviving ids, not just their count, is what separates
    "post-filtered" from "back-filled": a ranker handed ``forbidden_item_ids``
    returns a FULL page of 5 whose last 3 entries are items the unexcluded
    page never contained, which would still satisfy a count-only check the
    moment someone "fixed" the count by re-ranking.

    Runs on ``stable_ranking_client`` because it compares two separate
    scoring calls: on the default fixture the ranker's own jitter can change
    the second call's top 5, which fails this assertion for a reason that
    has nothing to do with exclusion (see that fixture).
    """
    url = f"/v1/recipes/fa:{verb}"
    baseline = stable_ranking_client.post(url, json={**body, "limit": 5})
    assert baseline.status_code == 200, baseline.text
    top5 = [item["item_id"] for item in baseline.json()["items"]]
    assert len(top5) == 5, "fixture invariant: a 20-item catalog must fill limit=5"

    r = stable_ranking_client.post(
        url, json={**body, "limit": 5, "exclude_items": top5[:3]}
    )
    assert r.status_code == 200, r.text
    assert [item["item_id"] for item in r.json()["items"]] == top5[3:]
