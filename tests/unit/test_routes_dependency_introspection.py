"""Regression test: routes.py must NOT use `from __future__ import annotations`.

CLAUDE.md prohibits `from __future__ import annotations` in routes.py because
it defers annotation evaluation and can break FastAPI's dependency introspection
for patterns like `kid: str = Depends(_require_auth)`.

This test locks in two invariants:
1. The source file does not contain `from __future__ import annotations`.
2. Endpoint parameters that use `Depends` (predict, models) resolve `kid` as
   a plain `str` — not a forward-reference string — so FastAPI can introspect
   them correctly at router-construction time.
"""

import ast
import inspect
from pathlib import Path

import fastapi.params

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROUTES_PATH = (
    Path(__file__).parent.parent.parent / "src" / "recotem" / "serving" / "routes.py"
)


def _make_minimal_router():
    """Build a router with empty api_keys so all endpoints are registered."""
    from recotem.serving.registry import ModelRegistry
    from recotem.serving.routes import make_router

    registry = ModelRegistry()
    return make_router(registry, api_keys=[])


# ---------------------------------------------------------------------------
# Source-level check
# ---------------------------------------------------------------------------


def test_routes_has_no_future_annotations_import():
    """routes.py source must not contain `from __future__ import annotations`."""
    source = _ROUTES_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            names = [alias.name for alias in node.names]
            assert "annotations" not in names, (
                "routes.py contains `from __future__ import annotations`, "
                "which is prohibited by CLAUDE.md because it breaks FastAPI "
                "dependency introspection for `kid: str = Depends(...)` patterns."
            )


# ---------------------------------------------------------------------------
# Runtime dependency-introspection checks
# ---------------------------------------------------------------------------


def test_predict_kid_parameter_is_depends():
    """The `kid` parameter on `predict` has a Depends default, not a string annotation."""
    router = _make_minimal_router()

    # Find the predict endpoint function from the registered routes.
    predict_fn = None
    for route in router.routes:
        if hasattr(route, "path") and route.path == "/predict/{name}":
            predict_fn = route.endpoint
            break

    assert predict_fn is not None, "Could not find /predict/{name} route"

    sig = inspect.signature(predict_fn)
    assert "kid" in sig.parameters, "predict endpoint is missing the `kid` parameter"

    kid_param = sig.parameters["kid"]
    # The annotation should resolve to the real `str` type, not the string "str".
    assert kid_param.annotation is str, (
        f"Expected `kid` annotation to be `str` type, got {kid_param.annotation!r}. "
        "This suggests deferred annotation evaluation is active."
    )
    # The default must be a FastAPI Depends instance.
    assert isinstance(kid_param.default, fastapi.params.Depends), (
        f"Expected `kid` default to be fastapi.params.Depends, "
        f"got {type(kid_param.default)!r}"
    )


def test_models_kid_parameter_is_depends():
    """The `kid` parameter on `models` has a Depends default, not a string annotation."""
    router = _make_minimal_router()

    models_fn = None
    for route in router.routes:
        if hasattr(route, "path") and route.path == "/models":
            models_fn = route.endpoint
            break

    assert models_fn is not None, "Could not find /models route"

    sig = inspect.signature(models_fn)
    assert "kid" in sig.parameters, "models endpoint is missing the `kid` parameter"

    kid_param = sig.parameters["kid"]
    assert kid_param.annotation is str, (
        f"Expected `kid` annotation to be `str` type, got {kid_param.annotation!r}. "
        "This suggests deferred annotation evaluation is active."
    )
    assert isinstance(kid_param.default, fastapi.params.Depends), (
        f"Expected `kid` default to be fastapi.params.Depends, "
        f"got {type(kid_param.default)!r}"
    )


def test_health_has_no_kid_parameter():
    """`/health` is unauthenticated and must NOT have a `kid` parameter."""
    router = _make_minimal_router()

    health_fn = None
    for route in router.routes:
        if hasattr(route, "path") and route.path == "/health":
            health_fn = route.endpoint
            break

    assert health_fn is not None, "Could not find /health route"

    sig = inspect.signature(health_fn)
    assert "kid" not in sig.parameters, (
        "/health should be unauthenticated but has a `kid` parameter"
    )
