"""Integration test: in-process train + serve + v1 recommend call.

Uses FastAPI TestClient for synchronous testing without a real server.
Trains a TopPop model on synthetic data, writes a signed artifact,
then serves it and calls the v1 recommend endpoints.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app

ACTIVE_KEY_HEX = "aa" * 32
_FAKE_SHA256_HEX = "dead" * 16  # 64 lowercase hex chars for a valid Sha256Hex marker
_FAKE_CONFIG_DIGEST = "sha256:" + "cafe" * 16  # valid Sha256Hex for config_digest


def _make_mock_recommender(users: list[str], items: list[str]):
    """Build a MagicMock recommender that returns fixed recommendations.

    Sets up ``_mapper.item_id_to_index`` so the v1 ``:recommend-related``
    seed-known pre-check (added in M-4) finds the seed items.
    """
    rec = MagicMock()

    def _get_rec(user_id, cutoff=10):
        if user_id in users:
            return [(iid, 1.0 - i * 0.1) for i, iid in enumerate(items[:cutoff])]
        raise KeyError(f"Unknown user: {user_id}")

    def _get_rec_new_user(seed_items, cutoff=10):
        # Return a deterministic ranking of *items* that excludes the seeds —
        # mimics irspack's get_recommendation_for_new_user contract closely
        # enough for the v1 :recommend-related endpoint contract test.
        seed_set = set(seed_items)
        ranked = [iid for iid in items if iid not in seed_set]
        return [(iid, 1.0 - i * 0.1) for i, iid in enumerate(ranked[:cutoff])]

    rec.get_recommendation_for_known_user_id.side_effect = _get_rec
    rec.get_recommendation_for_new_user.side_effect = _get_rec_new_user
    rec._mapper.item_id_to_index = {iid: i for i, iid in enumerate(items)}
    return rec


def _make_api_entry(plaintext: str, kid: str = "api-key") -> ApiKeyEntry:
    # Mirror recotem.serving.auth._hash_api_key (scrypt KDF with the
    # ``recotem.api-key.v1`` domain-separation salt at minimum cost).
    sha256 = hashlib.scrypt(
        plaintext.encode(),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    return ApiKeyEntry(kid=kid, sha256_hex=sha256)


# ---------------------------------------------------------------------------
# In-process end-to-end test
# ---------------------------------------------------------------------------


def test_serve_predict_e2e_in_process() -> None:
    """Train-like mock → serve → v1 :recommend returns valid response."""
    users = [f"user{i}" for i in range(10)]
    items = [f"item{i}" for i in range(20)]

    rec = _make_mock_recommender(users, items)
    entry = ModelEntry(
        name="test_model",
        recommender=rec,
        header={
            "best_class": "TopPopRecommender",
            "trained_at": "2026-01-01T00:00:00Z",
            "recipe_name": "test_model",
        },
        kid="active",
        # _loaded_marker[1] is the artifact sha that backs model_version.
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1.0,
    )

    registry = ModelRegistry()
    registry.replace("test_model", entry)

    plaintext = "integration_test_api_key_32bytes"
    api_entry = _make_api_entry(plaintext)
    app = build_v1_app(registry, api_keys=[api_entry])
    client = TestClient(app)

    response = client.post(
        "/v1/recipes/test_model:recommend",
        json={"user_id": "user0", "limit": 5},
        headers={"x-api-key": plaintext},
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 5
    assert data["items"][0]["item_id"] == "item0"
    assert data["recipe"] == "test_model"
    assert data["model_version"].startswith("sha256:")
    assert "request_id" in data


def test_v1_related_endpoint_returns_items() -> None:
    """v1 :recommend-related returns items for a known seed item."""
    users = [f"user{i}" for i in range(10)]
    items = [f"item{i}" for i in range(20)]

    rec = _make_mock_recommender(users, items)
    entry = ModelEntry(
        name="test_model",
        recommender=rec,
        header={
            "best_class": "TopPopRecommender",
            "trained_at": "2026-01-01T00:00:00Z",
            "recipe_name": "test_model",
        },
        kid="active",
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1.0,
    )

    registry = ModelRegistry()
    registry.replace("test_model", entry)

    plaintext = "integration_test_api_key_32bytes"
    api_entry = _make_api_entry(plaintext)
    app = build_v1_app(registry, api_keys=[api_entry])
    client = TestClient(app)

    # "item0" is a known item id produced by _make_mock_recommender; using it
    # as the seed exercises the new-user (item-based) recommend path.
    response = client.post(
        "/v1/recipes/test_model:recommend-related",
        json={"seed_items": ["item0"], "limit": 5},
        headers={"x-api-key": plaintext},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["recipe"] == "test_model"
    assert data["model_version"].startswith("sha256:")
    assert "items" in data
    assert len(data["items"]) >= 1
    # The seed item itself must not appear in the related results.
    assert all(it["item_id"] != "item0" for it in data["items"])


def test_serve_predict_e2e_unknown_user_404() -> None:
    """Unknown user_id returns 404."""
    rec = _make_mock_recommender(["known_user"], ["item1", "item2"])
    entry = ModelEntry(
        name="model2",
        recommender=rec,
        header={"best_class": "TopPop", "trained_at": "2026-01-01T00:00:00Z"},
        kid="active",
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1.0,
    )
    registry = ModelRegistry()
    registry.replace("model2", entry)

    app = build_v1_app(registry)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/v1/recipes/model2:recommend",
        json={"user_id": "total_stranger"},
    )
    assert response.status_code == 404


def test_serve_predict_e2e_missing_recipe_404() -> None:
    """Recipe not in registry returns 404 (not found)."""
    registry = ModelRegistry()
    app = build_v1_app(registry)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/v1/recipes/does_not_exist:recommend",
        json={"user_id": "user1"},
    )
    assert response.status_code == 404


def test_serve_health_endpoint_ok_with_loaded_model() -> None:
    """GET /v1/health returns ok when a model is loaded."""
    rec = _make_mock_recommender(["u1"], ["i1"])
    entry = ModelEntry(
        name="healthy_recipe",
        recommender=rec,
        header={"best_class": "TopPop", "trained_at": "2026-01-01T00:00:00Z"},
        kid="k1",
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1.0,
    )
    registry = ModelRegistry()
    registry.replace("healthy_recipe", entry)

    app = build_v1_app(registry)
    client = TestClient(app)

    response = client.get("/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["total"] == 1
    assert data["loaded"] == 1

    details_resp = client.get("/v1/health/details")
    assert details_resp.status_code == 200
    details = details_resp.json()
    assert "healthy_recipe" in details["recipes"]
    assert details["recipes"]["healthy_recipe"]["loaded"] is True


# ---------------------------------------------------------------------------
# Full artifact roundtrip: train → write → SafeUnpickler read
# ---------------------------------------------------------------------------


def _make_tiny_synthetic_csv(
    tmp_path: Path, n_users: int = 10, n_items: int = 10
) -> Path:
    """Create a minimal synthetic CSV with n_users users and n_items items.

    Each user rates every item exactly once, yielding n_users * n_items rows
    with no duplicates.  With 10 users and 10 items the default cutoff=5
    stays safely below the item count.
    """
    rows = ["user_id,item_id"]
    for u in range(n_users):
        for i in range(n_items):
            rows.append(f"u{u},i{i}")
    csv_file = tmp_path / "synthetic.csv"
    csv_file.write_text("\n".join(rows) + "\n")
    return csv_file


def test_train_then_serve_full_artifact_roundtrip(tmp_path: Path) -> None:
    """Train on synthetic CSV with dev_allow_unsigned, write a real artifact,
    then read it back via SafeUnpickler and assert numpy / irspack submodule
    paths deserialize end-to-end without ArtifactError.
    """
    from recotem.artifact.io import read_artifact
    from recotem.artifact.signing import KeyRing, unpickle_payload
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.training._compat import IDMappedRecommender
    from recotem.training.pipeline import run_training

    # 10 users × 10 items = 100 rows, no duplicates.
    # cutoff=5 safely below the 10 unique items after split.
    csv_file = _make_tiny_synthetic_csv(tmp_path, n_users=10, n_items=10)
    artifact_path = str(tmp_path / "synthetic.recotem")

    recipe = Recipe(
        name="synthetic-e2e",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=["TopPop"],
            n_trials=1,
            cutoff=5,  # must be < n_items to avoid irspack ValueError
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=0),
        ),
        output=OutputConfig(path=artifact_path, versioning="always_overwrite"),
    )

    # Use an in-memory dev key so no RECOTEM_SIGNING_KEYS env var is required.
    kr = KeyRing("dev:" + ("0" * 64))
    result = run_training(
        recipe,
        key_ring=kr,
        signing_key="dev",
        no_lock=True,
        dev_allow_unsigned=True,
        quiet=True,
    )

    assert result is not None, "run_training returned None unexpectedly"
    assert result.best_class is not None

    # Read the artifact back with the same KeyRing — exercises write_artifact's
    # positional-arg signature and the full binary format end-to-end.
    written_path = result.artifact_path
    header, payload_bytes = read_artifact(written_path, kr)

    # Deserialize via SafeUnpickler.  This exercises the numpy / irspack prefix
    # allow-list with a real trained model object (numpy arrays, scipy sparse
    # matrices, irspack recommender classes).
    recommender = unpickle_payload(payload_bytes)
    assert recommender is not None

    # Must be the IDMappedRecommender wrapper that irspack training produces.
    assert isinstance(recommender, IDMappedRecommender)

    # The recommender has user/item mappings populated.
    assert len(recommender.user_ids) > 0
    assert len(recommender.item_ids) > 0

    # Try recommendations for a new (cold-start) user with a subset of items
    # seen in training — this always works for TopPop (non-personalised).
    known_items = recommender.item_ids[:3]
    recs = recommender.get_recommendation_for_new_user(known_items, cutoff=3)
    assert len(recs) > 0
    # Each recommendation is a (item_id, score) pair.
    assert isinstance(recs[0][0], str)
    assert isinstance(recs[0][1], float)


# Every algorithm recotem advertises (see training.algorithms.SUPPORTED_CLASS_NAMES).
_ROUNDTRIP_ALGOS = [
    "TopPop",
    "IALS",
    "CosineKNN",
    "RP3beta",
    "DenseSLIM",
    "TruncatedSVD",
    "BPRFM",
]


def test_roundtrip_algos_covers_every_supported_class_name() -> None:
    """Pin ``_ROUNDTRIP_ALGOS`` to the real source of truth.

    The list above is hand-maintained and its comment claims completeness
    ("every algorithm recotem advertises") without deriving from
    ``training.algorithms.SUPPORTED_CLASS_NAMES`` -- so an algorithm added to
    the supported set but forgotten here would silently stop being covered by
    ``test_every_algorithm_artifact_serve_roundtrip`` below. Resolving each
    alias to its canonical class name and comparing sets closes that gap
    cheaply without touching the (working) parametrized test itself.
    """
    from recotem.training.algorithms import (
        SUPPORTED_CLASS_NAMES,
        resolve_algorithm_name,
    )

    resolved = {resolve_algorithm_name(alias) for alias in _ROUNDTRIP_ALGOS}
    assert resolved == SUPPORTED_CLASS_NAMES, (
        f"_ROUNDTRIP_ALGOS (resolved: {sorted(resolved)}) has drifted from "
        f"SUPPORTED_CLASS_NAMES ({sorted(SUPPORTED_CLASS_NAMES)}); update "
        "_ROUNDTRIP_ALGOS to add/remove the alias."
    )


def _irspack_has_recommender(class_name: str) -> bool:
    """True when the installed irspack build exposes ``class_name``.

    Some recommenders (e.g. BPRFMRecommender) are gated behind optional irspack
    dependencies such as ``lightfm``; when the dependency is absent irspack does
    not export the class and recotem cannot train it.
    """
    import irspack.recommenders as irspack_recommenders

    import recotem.training._compat  # noqa: F401  (installs IPython stub)

    return hasattr(irspack_recommenders, class_name)


def _make_clustered_synthetic_csv(tmp_path: Path) -> Path:
    """Deterministic, non-degenerate interaction matrix for cross-algorithm tests.

    A fully-dense grid (every user interacts with every item) is rank-deficient
    and makes matrix-factorisation algorithms emit divide-by-zero warnings that
    the warnings-as-error suite turns into failures.  Instead lay out users in
    overlapping clusters with a few per-user idiosyncratic items, giving a matrix
    with real low-rank structure that every algorithm can factorise cleanly.
    """
    n_users, n_items, n_clusters, band = 60, 40, 6, 12
    pairs: set[tuple[str, str]] = set()
    for u in range(n_users):
        cluster = u % n_clusters
        for k in range(band):
            pairs.add(
                (f"u{u}", f"i{(cluster * (n_items // n_clusters) + k) % n_items}")
            )
        # idiosyncratic items so users within a cluster are not identical
        pairs.add((f"u{u}", f"i{(u * 7) % n_items}"))
        pairs.add((f"u{u}", f"i{(u * 13 + 3) % n_items}"))
    rows = ["user_id,item_id"]
    rows.extend(f"{u},{i}" for u, i in sorted(pairs))
    csv_file = tmp_path / "clustered.csv"
    csv_file.write_text("\n".join(rows) + "\n")
    return csv_file


# irspack's TruncatedSVD tunes n_components over [4, 512]; on a small item
# catalogue Optuna may pick a value >= n_items, which irspack clamps with a
# UserWarning.  The suite runs warnings-as-error, but production training does
# not, so silence only this specific clamp warning rather than aborting the trial.
@pytest.mark.filterwarnings("ignore:n_components >= than:UserWarning")
@pytest.mark.parametrize("algo", _ROUNDTRIP_ALGOS)
def test_every_algorithm_artifact_serve_roundtrip(tmp_path: Path, algo: str) -> None:
    """Each supported algorithm must train -> sign -> SafeUnpickler-load.

    Regression for the FQCN allow-list: a trained recommender is a pickle graph
    that embeds not just the top-level ``*Recommender`` class but the trainer,
    config dataclasses, enums, and (for TruncatedSVD) the scikit-learn estimator
    it holds as attributes.  If any of those embedded classes is missing from
    ``_ALLOWED_CLASSES`` the artifact is unloadable at serve time even though
    training + signing succeeded — the failure mode that left IALS models
    permanently stuck in ``RECIPE_UNAVAILABLE`` (503) at the serve layer.
    """
    from recotem.artifact.io import read_artifact
    from recotem.artifact.signing import KeyRing, unpickle_payload
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.training._compat import IDMappedRecommender
    from recotem.training.algorithms import resolve_algorithm_name
    from recotem.training.pipeline import run_training

    class_name = resolve_algorithm_name(algo)
    if not _irspack_has_recommender(class_name):
        pytest.skip(
            f"installed irspack build lacks {class_name} "
            "(optional dependency not installed)"
        )

    csv_file = _make_clustered_synthetic_csv(tmp_path)
    artifact_path = str(tmp_path / f"{algo}.recotem")

    recipe = Recipe(
        name=f"roundtrip-{algo}",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=[algo],
            n_trials=2,
            cutoff=5,  # must be < n_items to avoid irspack ValueError
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=0),
        ),
        output=OutputConfig(path=artifact_path, versioning="always_overwrite"),
    )

    kr = KeyRing("dev:" + ("0" * 64))
    result = run_training(
        recipe,
        key_ring=kr,
        signing_key="dev",
        no_lock=True,
        dev_allow_unsigned=True,
        quiet=True,
    )
    assert result is not None, f"{algo}: run_training returned None (zero score?)"
    assert resolve_algorithm_name(result.best_class) == class_name

    # The serve-side load path: HMAC verify, then SafeUnpickler with the FQCN
    # allow-list.  This must NOT raise ArtifactError("class not allowed: ...").
    _, payload_bytes = read_artifact(result.artifact_path, kr)
    recommender = unpickle_payload(payload_bytes)
    assert isinstance(recommender, IDMappedRecommender)
    assert len(recommender.user_ids) > 0
    assert len(recommender.item_ids) > 0


def test_train_append_sha_then_serve_resolves_pointer(tmp_path: Path) -> None:
    """Regression: serve must read artifacts written under the documented
    default ``versioning: append_sha``.

    Earlier the serving layer's ``_read_artifact_bytes`` did not call
    ``resolve_artifact_pointer``, so it tried to parse the pointer's ASCII
    contents as a binary container and failed with ``magic bytes mismatch``.
    The e2e shell test happens to use ``always_overwrite`` and missed this.
    """
    from recotem.artifact.io import read_artifact
    from recotem.artifact.signing import KeyRing, unpickle_payload
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.serving.watcher import _read_artifact_bytes
    from recotem.training.pipeline import run_training

    csv_file = _make_tiny_synthetic_csv(tmp_path, n_users=10, n_items=10)
    pointer_path = str(tmp_path / "synthetic.recotem")

    recipe = Recipe(
        name="synthetic-pointer",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=["TopPop"],
            n_trials=1,
            cutoff=5,
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=0),
        ),
        # Documented default — exercise the pointer-resolving path.
        output=OutputConfig(path=pointer_path, versioning="append_sha"),
    )

    kr = KeyRing("dev:" + ("0" * 64))
    result = run_training(
        recipe,
        key_ring=kr,
        signing_key="dev",
        no_lock=True,
        dev_allow_unsigned=True,
        quiet=True,
    )

    # write_artifact in append_sha mode returns the sha-suffixed path,
    # not the pointer.  The pointer file at recipe.output.path must contain
    # only an ASCII basename.
    assert result.artifact_path != pointer_path
    pointer_contents = Path(pointer_path).read_bytes()
    assert len(pointer_contents) < 512, "pointer file should be tiny ASCII"
    assert pointer_contents.strip().endswith(b".recotem")

    # 1. read_artifact via fsspec resolves the pointer transparently.
    header, payload_bytes = read_artifact(pointer_path, kr)
    recommender = unpickle_payload(payload_bytes)
    assert recommender is not None

    # 2. The serving layer's helper (which previously failed) must also
    # transparently resolve the pointer to artifact bytes.  The first eight
    # bytes of a real artifact are "RECOTEM\x00".
    resolved = _read_artifact_bytes(pointer_path, max_bytes=10 * 1024 * 1024)
    assert resolved.startswith(b"RECOTEM\x00"), (
        "_read_artifact_bytes must resolve the pointer to raw artifact bytes"
    )


# ---------------------------------------------------------------------------
# MF-1: lenient loader — broken YAML does not abort serve; others still serve
# ---------------------------------------------------------------------------


def _write_minimal_recipe_yaml(recipes_dir, name, artifact_path):
    content = (
        f"name: {name}\n"
        "source:\n"
        "  type: csv\n"
        "  path: /tmp/data.csv\n"
        "schema:\n"
        "  user_column: user_id\n"
        "  item_column: item_id\n"
        "training:\n"
        "  algorithms: [TopPop]\n"
        "  n_trials: 1\n"
        f"output:\n"
        f"  path: {artifact_path}\n"
    )
    yaml_path = recipes_dir / f"{name}.yaml"
    yaml_path.write_text(content)
    return yaml_path


def test_broken_yaml_does_not_abort_serve_other_recipes_still_serve(
    tmp_path,
) -> None:
    """MF-1: When one recipe YAML is unparseable, serve must still start.

    Expected:
    - The broken-YAML recipe appears in /health with loaded=false and error.
    - The good recipe (missing artifact at startup) also appears as a stub.
    - Both stubs must be visible; serve must not raise at create_app() time.
    """

    from fastapi.testclient import TestClient

    from recotem.config import ServeConfig
    from recotem.serving.app import create_app
    from tests.conftest import ACTIVE_KEY_HEX

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    # --- Good recipe pointing at a missing artifact (will appear as stub) ---
    missing_artifact = tmp_path / "does-not-exist.recotem"
    _write_minimal_recipe_yaml(recipes_dir, "good_recipe", missing_artifact)

    # --- Broken recipe (invalid YAML syntax) ---
    broken_yaml = recipes_dir / "broken_recipe.yaml"
    broken_yaml.write_text("name: broken\nthis is: [invalid yaml: {{{")

    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]

    # Must NOT raise — lenient loader absorbs the broken YAML
    app = create_app(cfg)
    client = TestClient(app)

    # /v1/health must be 503 because both recipes are degraded
    health_resp = client.get("/v1/health")
    assert health_resp.status_code == 503
    body = health_resp.json()
    assert body["status"] == "degraded"
    assert body["loaded"] == 0
    assert body["total"] == 2

    # Per-recipe detail moved to /v1/health/details (auth-gated path).
    # In this test, insecure_no_auth=True, so /v1/health/details is reachable
    # without API keys.
    details_resp = client.get("/v1/health/details")
    assert details_resp.status_code == 503
    details = details_resp.json()

    # Broken recipe must appear with loaded=false and error info.
    assert "broken_recipe" in details["recipes"], (
        f"broken_recipe must appear in /v1/health/details; "
        f"got: {list(details['recipes'].keys())}"
    )
    broken_entry = details["recipes"]["broken_recipe"]
    assert broken_entry["loaded"] is False, (
        f"broken YAML recipe must have loaded=false; got {broken_entry!r}"
    )
    assert broken_entry.get("error"), (
        "broken YAML recipe must have an error string in /v1/health/details"
    )
    assert "YAML parse failed" in (broken_entry.get("error") or ""), (
        f"error must mention YAML parse failed; got {broken_entry.get('error')!r}"
    )

    # Good recipe must also appear (as missing-artifact stub)
    assert "good_recipe" in details["recipes"], (
        "good_recipe must appear in /v1/health/details even when artifact is missing"
    )

    # v1 :recommend for the broken recipe must return 503
    predict_broken = client.post(
        "/v1/recipes/broken_recipe:recommend",
        json={"user_id": "u1", "limit": 5},
    )
    assert predict_broken.status_code == 503, (
        f"broken recipe :recommend must return 503; got {predict_broken.status_code}"
    )

    # v1 :recommend for the good (missing artifact) recipe must also return 503
    predict_good = client.post(
        "/v1/recipes/good_recipe:recommend",
        json={"user_id": "u1", "limit": 5},
    )
    assert predict_good.status_code == 503


# ---------------------------------------------------------------------------
# Shared setup helper for new v1-surface integration tests
# ---------------------------------------------------------------------------


def _make_registry_and_client(
    users: list[str],
    items: list[str],
    recipe_name: str = "test_model",
    plaintext: str = "integration_test_api_key_32bytes",
    algorithms: list[str] | None = None,
    config_digest: str = _FAKE_CONFIG_DIGEST,
) -> tuple[ModelRegistry, TestClient, str]:
    """Build a populated registry, a FastAPI TestClient, and the auth key.

    Returns (registry, client, plaintext_api_key).
    Used by at least two test functions — extracted to avoid duplication.
    """
    rec = _make_mock_recommender(users, items)
    entry = ModelEntry(
        name=recipe_name,
        recommender=rec,
        header={
            "best_class": "TopPopRecommender",
            "trained_at": "2026-01-01T00:00:00Z",
            "recipe_name": recipe_name,
        },
        kid="active",
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=time.time(),
        algorithms=algorithms or ["TopPopRecommender"],
        config_digest=config_digest,
    )
    registry = ModelRegistry()
    registry.replace(recipe_name, entry)

    api_entry = _make_api_entry(plaintext)
    app = build_v1_app(registry, api_keys=[api_entry])
    client = TestClient(app)
    return registry, client, plaintext


# ---------------------------------------------------------------------------
# New integration tests: batch-recommend, batch-recommend-related, discovery
# ---------------------------------------------------------------------------


def test_batch_recommend_train_serve_call() -> None:
    """POST :batch-recommend returns per-request results for known and unknown users."""
    users = [f"user{i}" for i in range(5)]
    items = [f"item{i}" for i in range(10)]

    _, client, plaintext = _make_registry_and_client(users, items)

    # Three requests: two known users, one unknown user.
    response = client.post(
        "/v1/recipes/test_model:batch-recommend",
        json={
            "requests": [
                {"user_id": "user0", "limit": 3},
                {"user_id": "user2", "limit": 3},
                {"user_id": "totally_unknown_user", "limit": 3},
            ]
        },
        headers={"x-api-key": plaintext},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    # Top-level envelope fields.
    assert "results" in data
    assert "recipe" in data
    assert "model_version" in data
    assert "request_id" in data
    assert data["recipe"] == "test_model"
    assert data["model_version"].startswith("sha256:")

    results = data["results"]
    assert len(results) == 3

    # Index 0: known user — must succeed with items.
    r0 = results[0]
    assert r0["index"] == 0
    assert r0["status"] == "ok"
    assert isinstance(r0["items"], list)
    assert len(r0["items"]) == 3
    assert r0["items"][0]["item_id"] == "item0"

    # Index 1: another known user — must also succeed.
    r1 = results[1]
    assert r1["index"] == 1
    assert r1["status"] == "ok"
    assert isinstance(r1["items"], list)

    # Index 2: unknown user — must carry UNKNOWN_USER error, no items.
    # Under the discriminated-union schema, _BatchResultErr has no "items" field.
    r2 = results[2]
    assert r2["index"] == 2
    assert r2["status"] == "error"
    assert "items" not in r2, "_BatchResultErr must not carry 'items' field"
    assert r2["error"] is not None
    assert r2["error"]["code"] == "UNKNOWN_USER"


def test_batch_recommend_related_train_serve_call() -> None:
    """POST :batch-recommend-related handles known seed items and unknown seeds."""
    users = [f"user{i}" for i in range(5)]
    items = [f"item{i}" for i in range(10)]

    _, client, plaintext = _make_registry_and_client(users, items)

    # Three requests, each exercising a distinct error branch:
    # - index 0: known seed → status=ok
    # - index 1: every item is a seed → ranker returns [] (NO_CANDIDATES,
    #   the seeds are all known to the id-map but nothing is left to rank)
    # - index 2: seed with no member in the id-map → UNKNOWN_SEED_ITEMS
    all_item_seeds = [f"item{i}" for i in range(10)]
    response = client.post(
        "/v1/recipes/test_model:batch-recommend-related",
        json={
            "requests": [
                {"seed_items": ["item0"], "limit": 3},
                {"seed_items": all_item_seeds, "limit": 3},
                {"seed_items": ["unknown-stranger"], "limit": 3},
            ]
        },
        headers={"x-api-key": plaintext},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    # Top-level envelope.
    assert "results" in data
    assert "recipe" in data
    assert "model_version" in data
    assert "request_id" in data
    assert data["recipe"] == "test_model"
    assert data["model_version"].startswith("sha256:")

    results = data["results"]
    assert len(results) == 3

    # Index 0: known seed item — returns items, seed excluded.
    r0 = results[0]
    assert r0["index"] == 0
    assert r0["status"] == "ok"
    assert isinstance(r0["items"], list)
    assert len(r0["items"]) >= 1
    # The seed "item0" must not appear in the related results.
    assert all(it["item_id"] != "item0" for it in r0["items"])

    # Index 1: seeds known but ranker returns [] → NO_CANDIDATES.
    # Under the discriminated-union schema, _BatchResultErr has no "items" field.
    r1 = results[1]
    assert r1["index"] == 1
    assert r1["status"] == "error"
    assert "items" not in r1, "_BatchResultErr must not carry 'items' field"
    assert r1["error"] is not None
    assert r1["error"]["code"] == "NO_CANDIDATES"

    # Index 2: seed not in id-map → UNKNOWN_SEED_ITEMS.
    r2 = results[2]
    assert r2["index"] == 2
    assert r2["status"] == "error"
    assert r2["error"]["code"] == "UNKNOWN_SEED_ITEMS"


def test_recipes_discovery_list_and_detail() -> None:
    """GET /v1/recipes and /v1/recipes/{name} return full schema after model load."""
    users = [f"user{i}" for i in range(5)]
    items = [f"item{i}" for i in range(10)]

    _, client, plaintext = _make_registry_and_client(
        users,
        items,
        recipe_name="discovery_model",
        algorithms=["TopPopRecommender"],
        config_digest=_FAKE_CONFIG_DIGEST,
    )

    # --- GET /v1/recipes (list) ---
    list_resp = client.get(
        "/v1/recipes",
        headers={"x-api-key": plaintext},
    )
    assert list_resp.status_code == 200, list_resp.text
    list_data = list_resp.json()

    assert "recipes" in list_data
    assert isinstance(list_data["recipes"], list)
    names = [r["name"] for r in list_data["recipes"]]
    assert "discovery_model" in names

    # Validate RecipeSummary shape in the list entry.
    summary = next(r for r in list_data["recipes"] if r["name"] == "discovery_model")
    assert "model_version" in summary
    assert summary["model_version"].startswith("sha256:")
    assert "loaded_at" in summary
    # loaded_at must be a non-empty ISO-8601 UTC string.
    assert summary["loaded_at"].endswith("Z")
    assert "kind" in summary
    assert summary["kind"] == "user-item"
    assert "supported_verbs" in summary
    assert isinstance(summary["supported_verbs"], list)
    expected_verbs = {
        "recommend",
        "recommend-related",
        "batch-recommend",
        "batch-recommend-related",
    }
    assert set(summary["supported_verbs"]) == expected_verbs

    # --- GET /v1/recipes/{name} (detail) ---
    detail_resp = client.get(
        "/v1/recipes/discovery_model",
        headers={"x-api-key": plaintext},
    )
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()

    # RecipeDetailResponse extends RecipeSummary with config_digest, algorithms,
    # and best_algorithm.
    for field in (
        "name",
        "model_version",
        "loaded_at",
        "kind",
        "supported_verbs",
        "config_digest",
        "algorithms",
        "best_algorithm",
    ):
        assert field in detail, f"Missing field '{field}' in detail response"

    assert detail["name"] == "discovery_model"
    assert detail["model_version"].startswith("sha256:")
    assert detail["loaded_at"].endswith("Z")
    assert detail["kind"] == "user-item"
    assert isinstance(detail["supported_verbs"], list)
    assert set(detail["supported_verbs"]) == expected_verbs

    # Config digest and algorithms must match what was set in ModelEntry.
    assert detail["config_digest"] == _FAKE_CONFIG_DIGEST
    assert isinstance(detail["algorithms"], list)
    assert "TopPopRecommender" in detail["algorithms"]

    # best_algorithm is derived from header["best_class"].
    assert detail["best_algorithm"] == "TopPopRecommender"

    # --- GET /v1/recipes/{name} for a non-existent recipe returns 404 ---
    missing_resp = client.get(
        "/v1/recipes/no_such_recipe",
        headers={"x-api-key": plaintext},
    )
    assert missing_resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 16: feature-aware iALS -- integration and compatibility coverage
# ---------------------------------------------------------------------------
#
# The three tests below prove, at the integration level, that the whole
# feature-aware stack (recipe -> training -> artifact -> serving) works
# TOGETHER, and that old and new artifacts interoperate:
#
# 1. test_feature_aware_artifact_serve_roundtrip -- a features.item AND
#    features.user recipe trains a real IALS, the resulting artifact is
#    served through the real v1 HTTP surface, and a known user, a cold user
#    (case A), and a cold seed (case C) all succeed.
# 2. test_old_artifact_loads_on_feature_aware_serve -- an artifact with no
#    "features" header key at all (pre-Task-9 shape) loads and serves
#    normally through create_app()'s real startup loader.
# 3. test_feature_version_2_artifact_fails_closed -- a hand-written artifact
#    declaring features.version=2 is refused with reason "feature_version",
#    visible through /v1/health and /v1/health/details.


def _make_item_features_csv(tmp_path: Path, n_items: int = 40) -> Path:
    """Item feature table: alternating categorical "genre" by item parity.

    Matches the id space of ``_make_clustered_synthetic_csv`` (item ids
    ``i0``..``i{n_items - 1}``).
    """
    rows = ["item_id,genre"]
    for i in range(n_items):
        genre = "action" if i % 2 else "drama"
        rows.append(f"i{i},{genre}")
    p = tmp_path / "item_features.csv"
    p.write_text("\n".join(rows) + "\n")
    return p


def _make_user_features_csv(tmp_path: Path, n_users: int = 60) -> Path:
    """User feature table: alternating categorical "band" by user parity.

    Matches the id space of ``_make_clustered_synthetic_csv`` (user ids
    ``u0``..``u{n_users - 1}``).
    """
    rows = ["user_id,band"]
    for u in range(n_users):
        band = "young" if u % 2 else "old"
        rows.append(f"u{u},{band}")
    p = tmp_path / "user_features.csv"
    p.write_text("\n".join(rows) + "\n")
    return p


def test_feature_aware_artifact_serve_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Train an IALS recipe with features.item AND features.user end to end,
    serve the resulting artifact through the real v1 HTTP surface, and
    exercise a known user, a cold user with ``user_features`` (case A), and
    a cold seed with ``item_features`` (case C).

    Mutation guard
    --------------
    ``lambda_item_feature`` / ``lambda_user_feature`` are tuned over a
    log-uniform ``[5e-2, 1e6]`` range (``training/search.py``); the top of
    that range drives the feature contribution toward zero -- close to plain
    iALS. That means "the cold-start calls below returned 200" proves
    nothing about features actually being used: the exact same 200s would
    come back from a model that quietly trained close to plain iALS (e.g.
    because Optuna's sampler landed near the top of the range), or from a
    future regression that silently stopped threading the encoded feature
    matrix through to irspack -- routes.py's case A/C branches only check
    that the model *carries* feature state, not that the request's feature
    values reach it. To make the assertions mean something, ``suggest_float``
    is patched to pin *only* those two parameter names to ``0.1`` -- the same
    order of magnitude ``tests/unit/test_idmap.py``'s ``fa_model`` fixture
    uses to get a genuinely feature-sensitive model -- while every other
    hyperparameter is still sampled by the real (seeded) TPESampler. The
    fetch, cleanse, split, search loop, final refit, artifact write, HMAC
    signing, and deserialization all run for real, unmocked.

    The proof itself: cold-start recommendations are requested twice, once
    per category value (``band: young`` vs ``band: old``; ``genre: action``
    vs ``genre: drama``), and the ITEM-ID sequences (not the raw score-
    bearing dicts) are asserted to DIFFER. Comparing item ids rather than
    scores matters: irspack's underlying BLAS calls are not bit-reproducible
    across two separate calls even with an UNCHANGED encoded feature vector
    (observed empirically -- repeated identical calls differ at the float32
    rounding level, ~1e-7 relative), so comparing raw scores would make this
    assertion pass on noise alone regardless of whether the feature value
    was ever used. Confirmed by temporarily forcing
    ``recotem._features.encode_one`` to ignore its ``values`` argument
    (simulating a request's feature dict never reaching the encoder): every
    call in this test still returned 200, but the item-id sequences for
    band='young' vs band='old' and genre='action' vs genre='drama' came out
    IDENTICAL while the raw per-item scores still jittered in the 7th
    decimal digit -- exactly the false-negative a naive "200 and the dicts
    differ" check would have missed.
    """
    import json

    import optuna

    from recotem.artifact.io import read_artifact
    from recotem.artifact.signing import KeyRing, unpickle_payload
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        FeatureColumn,
        FeaturesConfig,
        FeatureSideConfig,
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.training.pipeline import run_training

    original_suggest_float = optuna.trial.Trial.suggest_float

    def _pinned_suggest_float(
        self: Any, name: str, low: float, high: float, *args: Any, **kwargs: Any
    ) -> float:
        if name in ("lambda_item_feature", "lambda_user_feature"):
            # Collapse the sampled range to a single point (0.1) rather than
            # short-circuiting the call entirely: a bare early-return skips
            # Optuna's own bookkeeping, so the value would never land in
            # ``trial.params`` / ``best_trial.params`` -- and pipeline.py's
            # final refit builds its params from exactly that dict. A refit
            # missing lambda_item_feature/lambda_user_feature while still
            # receiving item_features/user_features hits irspack's "Feature
            # weight regularization must be positive" (its constructor
            # default is 0.0, invalid whenever a feature matrix is given) --
            # a real failure this monkeypatch must not manufacture. Routing
            # through the REAL suggest_float with low==high keeps every bit
            # of that bookkeeping intact while still being deterministic.
            return original_suggest_float(self, name, 0.1, 0.1, *args, **kwargs)
        return original_suggest_float(self, name, low, high, *args, **kwargs)

    monkeypatch.setattr(optuna.trial.Trial, "suggest_float", _pinned_suggest_float)

    csv_file = _make_clustered_synthetic_csv(tmp_path)
    items_csv = _make_item_features_csv(tmp_path)
    users_csv = _make_user_features_csv(tmp_path)
    artifact_path = str(tmp_path / "feature_roundtrip.recotem")

    recipe = Recipe(
        name="feature-aware-roundtrip",
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        features=FeaturesConfig(
            item=FeatureSideConfig(
                source=CSVConfig(type="csv", path=str(items_csv)),
                id_column="item_id",
                columns=[FeatureColumn(name="genre", encoding="categorical")],
            ),
            user=FeatureSideConfig(
                source=CSVConfig(type="csv", path=str(users_csv)),
                id_column="user_id",
                columns=[FeatureColumn(name="band", encoding="categorical")],
            ),
        ),
        training=TrainingConfig(
            algorithms=["IALS"],
            n_trials=2,
            cutoff=5,  # must be < n_items to avoid irspack ValueError
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=0),
        ),
        output=OutputConfig(path=artifact_path, versioning="always_overwrite"),
    )

    kr = KeyRing("dev:" + ("0" * 64))
    result = run_training(
        recipe,
        key_ring=kr,
        signing_key="dev",
        no_lock=True,
        dev_allow_unsigned=True,
        quiet=True,
    )
    assert result is not None
    assert result.best_class == "IALSRecommender", (
        "test setup invariant: IALS is the only listed algorithm, so it must win"
    )

    header, payload_bytes = read_artifact(result.artifact_path, kr)
    header_dict = json.loads(header.header_data)
    assert header_dict["features"]["version"] == 1
    assert header_dict["features"]["item"]["columns"] == ["genre"]
    assert header_dict["features"]["user"]["columns"] == ["band"]

    recommender = unpickle_payload(payload_bytes)
    assert recommender.item_feature_state is not None
    assert recommender.user_feature_state is not None

    # --- serve the REAL trained artifact through the real v1 HTTP surface ---
    entry = ModelEntry(
        name=recipe.name,
        recommender=recommender,
        header=header_dict,
        kid=header.kid,
        _loaded_marker=(None, hashlib.sha256(payload_bytes).hexdigest()),
        loaded_at_unix=time.time(),
    )
    registry = ModelRegistry()
    registry.replace(recipe.name, entry)

    plaintext = "feature_roundtrip_api_key_32byte"
    api_entry = _make_api_entry(plaintext)
    app = build_v1_app(registry, api_keys=[api_entry])
    client = TestClient(app)
    headers = {"x-api-key": plaintext}

    # 1. Known user -> 200 (unchanged path; not a cold start at all).
    known = client.post(
        f"/v1/recipes/{recipe.name}:recommend",
        json={"user_id": "u0", "limit": 5},
        headers=headers,
    )
    assert known.status_code == 200, known.text

    # 2. Unknown user + user_features -> 200 (case A).
    cold_young = client.post(
        f"/v1/recipes/{recipe.name}:recommend",
        json={
            "user_id": "brand_new_user",
            "limit": 5,
            "user_features": {"band": "young"},
        },
        headers=headers,
    )
    assert cold_young.status_code == 200, cold_young.text

    # 3. :recommend-related with a cold seed + item_features -> 200 (case C).
    cold_action = client.post(
        f"/v1/recipes/{recipe.name}:recommend-related",
        json={
            "seed_items": ["brand_new_item"],
            "limit": 5,
            "item_features": {"brand_new_item": {"genre": "action"}},
        },
        headers=headers,
    )
    assert cold_action.status_code == 200, cold_action.text

    # 4. :recommend-related with a KNOWN seed + user_features -> 200 (case B).
    # This endpoint carries no user_id at all, so "cold user" here just means
    # the profile prior is not backed by any known user's learned embedding
    # -- routes.py's case B branch (_resolve_recommend_related) adds it as a
    # prior alongside the ad-hoc seed-history solve. "i0" is a known/
    # in-training item id from _make_clustered_synthetic_csv (u0's cluster).
    known_seed_young = client.post(
        f"/v1/recipes/{recipe.name}:recommend-related",
        json={
            "seed_items": ["i0"],
            "limit": 5,
            "user_features": {"band": "young"},
        },
        headers=headers,
    )
    assert known_seed_young.status_code == 200, known_seed_young.text

    # Paired request, same known seed, opposite user_features value -- same
    # differential guard as cases A/C (see docstring): 200 alone proves
    # nothing here, since a feature-blind regression (encode_one silently
    # dropping its ``values`` argument) would return 200 too. Case B mixes a
    # strong known-seed signal with the user_features signal, so this also
    # confirms empirically that the seed doesn't drown out the profile prior.
    known_seed_old = client.post(
        f"/v1/recipes/{recipe.name}:recommend-related",
        json={
            "seed_items": ["i0"],
            "limit": 5,
            "user_features": {"band": "old"},
        },
        headers=headers,
    )
    assert known_seed_old.status_code == 200, known_seed_old.text
    known_seed_young_ids = [
        item["item_id"] for item in known_seed_young.json()["items"]
    ]
    known_seed_old_ids = [item["item_id"] for item in known_seed_old.json()["items"]]
    assert known_seed_young_ids != known_seed_old_ids, (
        "case-B recommendations (:recommend-related, known seed 'i0') for "
        "band='young' vs band='old' must differ -- otherwise the served "
        "model is not actually using user_feature_state for the case-B "
        "profile-prior solve (get_recommendation_for_new_user with "
        "user_features=...)."
    )

    # --- mutation guard: prove genuine feature-dependence, not just plumbing ---
    cold_old = client.post(
        f"/v1/recipes/{recipe.name}:recommend",
        json={
            "user_id": "brand_new_user",
            "limit": 5,
            "user_features": {"band": "old"},
        },
        headers=headers,
    )
    assert cold_old.status_code == 200, cold_old.text

    # Compare item-ID SEQUENCES, not the raw score-bearing dicts: irspack's
    # underlying BLAS calls are not guaranteed bit-reproducible across two
    # separate calls (observed empirically: repeated calls with an
    # UNCHANGED encoded feature vector still differ at the float32 rounding
    # level, ~1e-7 relative). Comparing full score dicts would make this
    # assertion pass on noise alone regardless of whether the feature value
    # was used. The item-id ranking, in contrast, only reorders when the
    # underlying embeddings differ by much more than that noise floor --
    # confirmed by re-running this exact scenario with the feature-blind
    # mutation described in this test's docstring: the item-id sequences
    # came out IDENTICAL while the scores still jittered in the 7th decimal
    # digit.
    young_ids = [item["item_id"] for item in cold_young.json()["items"]]
    old_ids = [item["item_id"] for item in cold_old.json()["items"]]
    assert young_ids != old_ids, (
        "cold-start recommendations for band='young' vs band='old' must "
        "differ -- otherwise the served model is not actually using "
        "user_feature_state (e.g. lambda_user_feature landed at the top of "
        "its tuned range, or the feature vector never reached irspack)."
    )

    cold_drama = client.post(
        f"/v1/recipes/{recipe.name}:recommend-related",
        json={
            "seed_items": ["brand_new_item"],
            "limit": 5,
            "item_features": {"brand_new_item": {"genre": "drama"}},
        },
        headers=headers,
    )
    assert cold_drama.status_code == 200, cold_drama.text
    action_ids = [item["item_id"] for item in cold_action.json()["items"]]
    drama_ids = [item["item_id"] for item in cold_drama.json()["items"]]
    assert action_ids != drama_ids, (
        "cold-seed recommendations for genre='action' vs genre='drama' must "
        "differ -- otherwise the served model is not actually using "
        "item_feature_state."
    )


def test_old_artifact_loads_on_feature_aware_serve(tmp_path: Path) -> None:
    """An artifact with NO feature state (pre-Task-9 shape) must load and
    serve normally through this feature-aware build.

    Trains a real, features-less TopPop recipe (its header omits the
    "features" key entirely -- see ``test_no_features_recipe_omits_header_key``
    in ``tests/unit/test_training_pipeline.py``), then drives the artifact
    through ``create_app()``'s real startup loader -- the same code path
    ``test_feature_version_2_artifact_fails_closed`` below proves fails
    CLOSED for a version mismatch. This is the positive control: the
    "features absent -> pass" branch of ``check_artifact_feature_version``
    must not regress backward compatibility for the (huge) population of
    already-deployed artifacts trained before this feature existed.
    """
    import json

    from recotem.artifact.io import read_artifact
    from recotem.artifact.signing import KeyRing, unpickle_payload
    from recotem.config import ServeConfig
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )
    from recotem.serving.app import create_app
    from recotem.training.pipeline import run_training

    # A dense "every user rates every item" grid (_make_tiny_synthetic_csv)
    # would leave a known user with nothing left to recommend (every item
    # already seen), so the final :recommend assertion below needs the
    # partial-density clustered dataset instead.
    csv_file = _make_clustered_synthetic_csv(tmp_path)
    artifact_path = str(tmp_path / "old_style.recotem")
    recipe_name = "old-style-no-features"

    recipe = Recipe(
        name=recipe_name,
        source=CSVConfig(type="csv", path=str(csv_file)),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=["TopPop"],
            n_trials=1,
            cutoff=5,  # must be < n_items to avoid irspack ValueError
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=0),
        ),
        output=OutputConfig(path=artifact_path, versioning="always_overwrite"),
    )

    signing_hex = "0" * 64
    kr = KeyRing("dev:" + signing_hex)
    result = run_training(
        recipe,
        key_ring=kr,
        signing_key="dev",
        no_lock=True,
        dev_allow_unsigned=True,
        quiet=True,
    )
    assert result is not None

    header, payload_bytes = read_artifact(result.artifact_path, kr)
    header_dict = json.loads(header.header_data)
    assert "features" not in header_dict, (
        "test setup invariant: a features-less recipe must omit the "
        "'features' header key entirely"
    )
    # Sanity: the artifact really does deserialize (would raise otherwise).
    assert unpickle_payload(payload_bytes) is not None

    # Drive it through the REAL startup loader, not a hand-built ModelEntry --
    # this is what actually calls check_artifact_feature_version.
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    _write_minimal_recipe_yaml(recipes_dir, recipe_name, result.artifact_path)

    cfg = ServeConfig()
    cfg.signing_keys_raw = f"dev:{signing_hex}"
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]

    app = create_app(cfg)
    client = TestClient(app)

    health = client.get("/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "total": 1, "loaded": 1}

    details = client.get("/v1/health/details")
    assert details.status_code == 200
    details_body = details.json()
    assert details_body["recipes"][recipe_name]["loaded"] is True
    assert not details_body["recipes"][recipe_name].get("error")

    predict = client.post(
        f"/v1/recipes/{recipe_name}:recommend",
        json={"user_id": "u0", "limit": 3},
    )
    assert predict.status_code == 200, predict.text
    assert len(predict.json()["items"]) == 3


def test_feature_version_2_artifact_fails_closed(tmp_path: Path) -> None:
    """A hand-written artifact declaring ``features.version: 2`` must be
    refused by serve's startup loader with reason ``"feature_version"``, and
    that refusal must be visible through the real ``/v1/health`` and
    ``/v1/health/details`` endpoints -- not just as a raised Python
    exception from calling ``check_artifact_feature_version`` directly
    (``tests/unit/test_features_compat.py`` already covers the gate function
    and both loader call sites in isolation; this proves the wiring holds
    all the way through ``create_app()``).

    Fails for the RIGHT reason, not merely fails: asserts the /health/details
    error text names the version-check gate specifically (not e.g. an HMAC
    or a deserialize failure), and separately asserts
    ``_classify_artifact_error`` -- the same function the watcher's hot-swap
    path uses to label the ``recotem_artifact_load_failures_total`` metric --
    maps that exact message to ``"feature_version"``, not the "parse"
    catch-all (the message contains the word "version", the same trap the
    irspack skew guard's message has).
    """
    from recotem.artifact.io import write_artifact
    from recotem.artifact.signing import KeyRing
    from recotem.config import ServeConfig
    from recotem.serving.app import create_app
    from recotem.serving.watcher import _classify_artifact_error

    kid_hex = "ab" * 32
    kr = KeyRing("probe:" + kid_hex)

    artifact_path = str(tmp_path / "feature_v2.recotem")
    recipe_name = "feature_v2_recipe"
    header_dict = {
        "recipe_name": recipe_name,
        "best_class": "TopPopRecommender",
        "trained_at": "2026-01-01T00:00:00Z",
        # No "irspack_version" key: the irspack skew guard (which runs
        # BEFORE the feature-version gate in the real loader) fails OPEN on
        # a missing version, so this artifact is refused for exactly one
        # reason -- the feature-version gate -- not two entangled ones.
        "features": {
            "version": 2,
            "item": {"n_features": 4, "columns": ["genre"]},
        },
    }
    write_artifact(
        {"dummy": "payload"},
        header_dict,
        kr,
        artifact_path,
        versioning="always_overwrite",
    )

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    _write_minimal_recipe_yaml(recipes_dir, recipe_name, artifact_path)

    cfg = ServeConfig()
    cfg.signing_keys_raw = f"probe:{kid_hex}"
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]

    app = create_app(cfg)
    client = TestClient(app)

    health = client.get("/v1/health")
    assert health.status_code == 503
    health_body = health.json()
    assert health_body["status"] == "degraded"
    assert health_body["loaded"] == 0
    assert health_body["total"] == 1

    details = client.get("/v1/health/details")
    assert details.status_code == 503
    details_body = details.json()
    recipe_health = details_body["recipes"][recipe_name]
    assert recipe_health["loaded"] is False
    error = (recipe_health.get("error") or "").lower()
    assert "feature version check failed" in error, (
        f"expected the feature-version gate's message prefix; got {error!r}"
    )
    assert "declares feature encoder version 2" in error, (
        f"expected the refusal to name the offending version; got {error!r}"
    )

    # The reason must classify as "feature_version" specifically -- proving
    # the failure is the version gate, not e.g. a coincidental deserialize
    # or HMAC failure that also happens to yield loaded=False.
    assert _classify_artifact_error(error) == "feature_version"

    predict = client.post(
        f"/v1/recipes/{recipe_name}:recommend",
        json={"user_id": "u1", "limit": 5},
    )
    assert predict.status_code == 503
