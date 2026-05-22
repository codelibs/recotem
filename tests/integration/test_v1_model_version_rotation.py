# tests/integration/test_v1_model_version_rotation.py
"""T1: model_version actually rotates after a successful hot-swap.

Asserts that model_version in the response body (and the matching
X-Recotem-Model-Version header) change after registry.replace_with_marker
swaps in a new artifact, and that both values conform to ``sha256:<64 hex>``.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app

_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

_SHA256_HEX_A = "a" * 64  # 64 lowercase hex chars
_SHA256_HEX_B = "b" * 64  # different 64 lowercase hex chars


def _make_entry(name: str, sha256_hex: str) -> ModelEntry:
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    rec._mapper = MagicMock()
    rec._mapper.user_id_to_index = {"u1": 0}
    return ModelEntry(
        name=name,
        recommender=rec,
        header={"best_class": "TopPop"},
        kid="active",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, sha256_hex),
        loaded_at_unix=1747800000.0,
    )


def test_model_version_rotates_after_hot_swap() -> None:
    """model_version changes after registry.replace_with_marker swaps in artifact B.

    1. Load artifact A, call :recommend, capture model_version_a.
    2. Swap in artifact B via replace_with_marker.
    3. Call :recommend again, capture model_version_b.
    4. Assert model_version_a != model_version_b and both match sha256:<64 hex>.
    5. Assert X-Recotem-Model-Version header equals body model_version in both calls.
    """
    registry = ModelRegistry()
    entry_a = _make_entry("demo", _SHA256_HEX_A)
    registry.replace("demo", entry_a)

    app = build_v1_app(registry)
    client = TestClient(app)

    # --- Call 1: artifact A ---
    r1 = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 1})
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    model_version_a = body1["model_version"]
    header_version_a = r1.headers.get("x-recotem-model-version")

    assert _SHA256_PATTERN.match(model_version_a), (
        f"model_version_a must match sha256:<64 hex>; got {model_version_a!r}"
    )
    assert header_version_a == model_version_a, (
        "X-Recotem-Model-Version header must equal body model_version for artifact A"
    )

    # --- Swap in artifact B ---
    entry_b = _make_entry("demo", _SHA256_HEX_B)
    marker_b = (None, _SHA256_HEX_B)
    registry.replace_with_marker("demo", entry_b, marker_b)

    # --- Call 2: artifact B ---
    r2 = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 1})
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    model_version_b = body2["model_version"]
    header_version_b = r2.headers.get("x-recotem-model-version")

    assert _SHA256_PATTERN.match(model_version_b), (
        f"model_version_b must match sha256:<64 hex>; got {model_version_b!r}"
    )
    assert header_version_b == model_version_b, (
        "X-Recotem-Model-Version header must equal body model_version for artifact B"
    )

    # --- Core assertion ---
    assert model_version_a != model_version_b, (
        "model_version must rotate after a hot-swap: "
        f"model_version_a={model_version_a!r}, model_version_b={model_version_b!r}"
    )
    assert model_version_a == f"sha256:{_SHA256_HEX_A}"
    assert model_version_b == f"sha256:{_SHA256_HEX_B}"
