"""Integration test: in-process train + serve + v1 recommend call.

Uses FastAPI TestClient for synchronous testing without a real server.
Trains a TopPop model on synthetic data, writes a signed artifact,
then serves it and calls the v1 recommend endpoints.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recotem.config import ApiKeyEntry
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.v1_router import make_v1_router

ACTIVE_KEY_HEX = "aa" * 32


def _make_mock_recommender(users: list[str], items: list[str]):
    """Build a MagicMock recommender that returns fixed recommendations."""
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
        _loaded_marker=(None, "deadbeef"),
        loaded_at_unix=1.0,
    )

    registry = ModelRegistry()
    registry.replace("test_model", entry)

    plaintext = "integration_test_api_key_32bytes"
    api_entry = _make_api_entry(plaintext)
    router = make_v1_router(registry=registry, api_keys=[api_entry])
    app = FastAPI()
    app.include_router(router, prefix="/v1")
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
        _loaded_marker=(None, "deadbeef"),
        loaded_at_unix=1.0,
    )

    registry = ModelRegistry()
    registry.replace("test_model", entry)

    plaintext = "integration_test_api_key_32bytes"
    api_entry = _make_api_entry(plaintext)
    router = make_v1_router(registry=registry, api_keys=[api_entry])
    app = FastAPI()
    app.include_router(router, prefix="/v1")
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
        _loaded_marker=(None, "deadbeef"),
        loaded_at_unix=1.0,
    )
    registry = ModelRegistry()
    registry.replace("model2", entry)

    router = make_v1_router(registry=registry, api_keys=[])
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/v1/recipes/model2:recommend",
        json={"user_id": "total_stranger"},
    )
    assert response.status_code == 404


def test_serve_predict_e2e_missing_recipe_503() -> None:
    """Recipe not in registry returns 503."""
    registry = ModelRegistry()
    router = make_v1_router(registry=registry, api_keys=[])
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/v1/recipes/does_not_exist:recommend",
        json={"user_id": "user1"},
    )
    assert response.status_code == 503


def test_serve_health_endpoint_ok_with_loaded_model() -> None:
    """GET /v1/health returns ok when a model is loaded."""
    rec = _make_mock_recommender(["u1"], ["i1"])
    entry = ModelEntry(
        name="healthy_recipe",
        recommender=rec,
        header={"best_class": "TopPop", "trained_at": "2026-01-01T00:00:00Z"},
        kid="k1",
        _loaded_marker=(None, "deadbeef"),
        loaded_at_unix=1.0,
    )
    registry = ModelRegistry()
    registry.replace("healthy_recipe", entry)

    router = make_v1_router(registry=registry, api_keys=[])
    app = FastAPI()
    app.include_router(router, prefix="/v1")
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
