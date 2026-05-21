"""Shared pytest fixtures for the Recotem test suite.

NOTE: This test suite tests a system whose payload format is pickle (required
by irspack for scipy sparse matrices). The pickle usage here is intentional
and required: we are testing the HMAC-signed artifact security layer, which
specifically tests that the SafeUnpickler allow-list blocks gadgets.

Provides:
- signing_key_bytes: deterministic 32-byte signing key
- key_ring: KeyRing with two kids ("active", "old")
- tmp_recipe_yaml: factory for writing recipe YAML files to tmp_path
- make_artifact: factory for building signed artifact bytes
- movielens_df: session-scoped MovieLens100K DataFrame (downloaded once)
"""

from __future__ import annotations

import json
import struct
import sys
import textwrap
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Make example plugins importable without requiring a separate `pip install`.
# The echo-source plugin lives under examples/plugins/echo-source/src/ and is
# not installed in the main project venv, but its source is always present in
# the repository.  Adding its src/ to sys.path lets unit tests import it
# directly without needing a separate installation step.
# ---------------------------------------------------------------------------

_ECHO_SRC = (
    Path(__file__).parent.parent / "examples" / "plugins" / "echo-source" / "src"
)
if _ECHO_SRC.is_dir() and str(_ECHO_SRC) not in sys.path:
    sys.path.insert(0, str(_ECHO_SRC))

# ---------------------------------------------------------------------------
# Signing key fixtures
# ---------------------------------------------------------------------------

ACTIVE_KEY_HEX: str = "aa" * 32  # 64 hex chars = 32 bytes, deterministic
OLD_KEY_HEX: str = "bb" * 32


@pytest.fixture(autouse=True)
def _allow_loopback_http_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests use ``http.server`` / ``pytest-httpserver`` which bind to 127.0.0.1.

    Production defaults to refusing private/loopback fetches (see
    ``RECOTEM_HTTP_ALLOW_PRIVATE`` in :mod:`recotem.config`).  Setting it to
    ``1`` here keeps existing HTTP tests working.  Tests that exercise the
    SSRF guard explicitly call ``monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")``
    or ``monkeypatch.delenv(..., raising=False)``.
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "1")


@pytest.fixture(autouse=True)
def _isolate_structlog_state(monkeypatch: pytest.MonkeyPatch):
    """Prevent CLI-invocation tests from polluting structlog global state.

    ``recotem.logging.configure_logging`` calls ``structlog.configure(...)`` with
    ``cache_logger_on_first_use=True``.  Once invoked (e.g. via a CLI test that
    runs ``recotem inspect``), every ``structlog.get_logger()`` proxy resolves
    its underlying logger via the stdlib ``logging`` factory and caches it on
    the proxy itself.  After this point ``structlog.testing.capture_logs()``
    cannot intercept those cached loggers, breaking subsequent tests that rely
    on capture_logs (e.g. ``tests/unit/test_serving_app.py``).

    Two-pronged fix:
    1. Replace ``configure_logging`` with a version that sets
       ``cache_logger_on_first_use=False`` so each ``.info()`` re-resolves the
       logger from the current config.  This still routes through the stdlib
       LoggerFactory (so ``RECOTEM_LOG_FORMAT=json`` tests that parse stderr
       lines keep working) but makes the configuration honour subsequent
       ``capture_logs()`` context managers.
    2. After each test, ``structlog.reset_defaults()`` and clear any per-proxy
       ``_logger`` cache that survived from before the patch took effect.
    """
    import structlog

    from recotem.logging import configure_logging as _real_configure_logging

    def _no_cache_configure_logging(log_format: str = "auto") -> None:
        _real_configure_logging(log_format)
        # Override the module-level config with the same processors but with
        # caching disabled so capture_logs() can intercept future calls.
        cfg = structlog.get_config()
        structlog.configure(
            processors=cfg["processors"],
            wrapper_class=cfg["wrapper_class"],
            context_class=cfg["context_class"],
            logger_factory=cfg["logger_factory"],
            cache_logger_on_first_use=False,
        )

    monkeypatch.setattr(
        "recotem.logging.configure_logging", _no_cache_configure_logging
    )
    monkeypatch.setattr(
        "recotem.cli.configure_logging", _no_cache_configure_logging, raising=False
    )

    yield

    structlog.reset_defaults()


@pytest.fixture(autouse=True)
def _clear_datasource_registry_cache() -> None:
    """Clear the datasource registry LRU cache before and after every test.

    ``get_source_types()`` is decorated with ``@lru_cache(maxsize=1)``.  Without
    clearing it, a monkeypatch applied in one test would be visible to subsequent
    tests that call ``get_source_types()`` without patching, because the cached
    result from the first call is returned for all later calls in the same
    process.

    This fixture is function-scoped (the default) so it does not interfere with
    session-scoped fixtures that do not touch the registry.
    """
    from recotem.datasource.registry import clear_registry_cache

    clear_registry_cache()
    yield
    clear_registry_cache()


@pytest.fixture(scope="session")
def signing_key_bytes() -> bytes:
    """Deterministic 32-byte signing key for 'active' kid."""
    return bytes.fromhex(ACTIVE_KEY_HEX)


@pytest.fixture(scope="session")
def key_ring():
    """KeyRing with two kids: 'active' and 'old'."""
    from recotem.artifact.signing import KeyRing

    return KeyRing(f"active:{ACTIVE_KEY_HEX}", f"old:{OLD_KEY_HEX}")


@pytest.fixture(scope="session")
def single_key_ring():
    """KeyRing with just the 'active' kid."""
    from recotem.artifact.signing import KeyRing

    return KeyRing(f"active:{ACTIVE_KEY_HEX}")


# ---------------------------------------------------------------------------
# Artifact builder helper (used by multiple test modules)
# ---------------------------------------------------------------------------


def build_raw_artifact(
    kid: str,
    key_hex: str,
    header_dict: dict[str, Any] | None = None,
    payload_bytes: bytes | None = None,
) -> bytes:
    """Build a valid .recotem artifact byte string.

    Uses irspack-style pickle for the payload because the system under test
    requires it. The HMAC signs over kid || header || payload.
    """
    import hmac as _hmac
    import pickle  # noqa: S403

    if header_dict is None:
        header_dict = {
            "recipe_name": "test",
            "trained_at": "2026-01-01T00:00:00Z",
            "best_class": "TopPopRecommender",
            "best_score": 0.42,
        }
    if payload_bytes is None:
        # Use a simple builtin dict — allowed by SafeUnpickler allow-list
        payload_bytes = pickle.dumps({"key": "value"}, protocol=4)  # noqa: S301

    header_json: bytes = json.dumps(header_dict, separators=(",", ":")).encode("utf-8")
    kid_bytes: bytes = kid.encode("utf-8")
    key_bytes: bytes = bytes.fromhex(key_hex)

    h = _hmac.new(key_bytes, digestmod="sha256")
    h.update(kid_bytes)
    h.update(header_json)
    h.update(payload_bytes)
    digest = h.digest()

    from recotem.artifact.format import FORMAT_VERSION, MAGIC

    kid_len = len(kid_bytes)
    header_len = len(header_json)

    parts: list[bytes] = [
        MAGIC,
        struct.pack("<HH", FORMAT_VERSION, 0),
        bytes([kid_len]),
        kid_bytes,
        digest,
        struct.pack("<I", header_len),
        header_json,
        payload_bytes,
    ]
    return b"".join(parts)


@pytest.fixture()
def make_artifact(tmp_path: Path):
    """Factory that returns raw .recotem artifact bytes.

    Usage: make_artifact(payload_bytes=None, header_dict=None, kid='active',
                         key_hex=ACTIVE_KEY_HEX) -> bytes
    """

    def _factory(
        payload_bytes: bytes | None = None,
        header_dict: dict[str, Any] | None = None,
        kid: str = "active",
        key_hex: str = ACTIVE_KEY_HEX,
    ) -> bytes:
        return build_raw_artifact(
            kid=kid,
            key_hex=key_hex,
            header_dict=header_dict,
            payload_bytes=payload_bytes,
        )

    return _factory


@pytest.fixture()
def valid_artifact_path(tmp_path: Path, make_artifact):
    """Write a valid artifact to tmp_path/test.recotem and return its Path."""
    data = make_artifact()
    path = tmp_path / "test.recotem"
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# Recipe YAML factory
# ---------------------------------------------------------------------------

_MINIMAL_RECIPE_TEMPLATE = """\
name: {name}
source:
  type: csv
  path: {csv_path}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {output_path}
"""


@pytest.fixture()
def tmp_recipe_yaml(tmp_path: Path):
    """Factory: tmp_recipe_yaml(name, csv_path, output_path, extra_yaml) -> Path.

    Creates a minimal valid recipe YAML in tmp_path.
    """

    def _factory(
        name: str = "test_recipe",
        csv_path: str | None = None,
        output_path: str | None = None,
        extra_yaml: str = "",
    ) -> Path:
        if csv_path is None:
            csv_file = tmp_path / "data.csv"
            if not csv_file.exists():
                csv_file.write_text("user_id,item_id\nu1,i1\nu1,i2\nu2,i1\n")
            csv_path = str(csv_file)
        if output_path is None:
            output_path = str(tmp_path / f"{name}.recotem")

        content = _MINIMAL_RECIPE_TEMPLATE.format(
            name=name,
            csv_path=csv_path,
            output_path=output_path,
        )
        if extra_yaml:
            content += "\n" + textwrap.dedent(extra_yaml)

        yaml_path = tmp_path / f"{name}.yaml"
        yaml_path.write_text(content)
        return yaml_path

    return _factory


# ---------------------------------------------------------------------------
# MovieLens 100K fixture (session-scoped, downloaded once)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def movielens_df() -> pd.DataFrame:
    """Return the MovieLens100K interactions as a pandas DataFrame.

    Downloaded once per test session via irspack's MovieLens100KDataManager.
    Columns normalised to: user_id (str), item_id (str).
    """
    # Apply the IPython stub required by irspack's transitive fastprogress dep.
    from irspack.dataset import MovieLens100KDataManager

    import recotem.training._compat  # noqa: F401

    # force_download=True bypasses irspack's interactive input() prompt,
    # which fails under pytest output capture (OSError: reading from stdin
    # while output is captured).  Once the cached zip exists at DEFAULT_PATH
    # the flag is a no-op, so this does not re-download on every session.
    dm = MovieLens100KDataManager(force_download=True)
    df = dm.read_interaction()
    rename_map = {
        "userId": "user_id",
        "movieId": "item_id",
        "userid": "user_id",
        "itemid": "item_id",
        "movie_id": "item_id",
        "movieid": "item_id",
        "user": "user_id",
        "item": "item_id",
    }
    df = df.rename(columns=rename_map)
    for col in ("user_id", "item_id"):
        if col in df.columns:
            df[col] = df[col].astype(str)
    return df


@pytest.fixture(scope="session")
def movielens_small_df(movielens_df: pd.DataFrame) -> pd.DataFrame:
    """50 most-active users from MovieLens100K — fast training slice."""
    top_users = movielens_df["user_id"].value_counts().head(50).index
    return movielens_df[movielens_df["user_id"].isin(top_users)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Serving app builder for unit / integration tests
# ---------------------------------------------------------------------------
#
# ``create_app`` (in src/recotem/serving/app.py) registers a RequestIDMiddleware
# that sets ``request.state.request_id`` and two custom exception handlers
# that flatten error bodies and route 422s through a validation handler.
#
# Most tests don't want to build a full ServeConfig; they just want a router
# wired to a hand-built ModelRegistry.  This helper mirrors what create_app
# does (middleware + handlers) so the tests exercise the production response
# shape without dragging in recipes-dir loading.
# ---------------------------------------------------------------------------


def build_v1_app(
    registry,
    api_keys=None,
    metadata_field_deny=None,
):
    """Build a FastAPI app mounting the v1 router with production middleware.

    Parameters
    ----------
    registry:
        ``ModelRegistry`` populated with the model entries the test needs.
    api_keys:
        Optional list of ``ApiKeyEntry`` (defaults to []).
    metadata_field_deny:
        Optional deny-list passed through to ``make_router``.

    Returns
    -------
    FastAPI
        Application with the v1 router mounted at ``/v1`` plus the
        RequestIDMiddleware and the flat-body / validation-error
        exception handlers registered exactly as ``create_app`` would.
    """

    from fastapi import FastAPI, Request
    from fastapi.exceptions import HTTPException, RequestValidationError
    from fastapi.responses import JSONResponse

    from recotem.serving import metrics as _metrics
    from recotem.serving.app import (
        _DEFAULT_DETAIL_FOR,
        _V1_VERB_PATH_RE,
        RequestIDMiddleware,
    )
    from recotem.serving.routes import make_router

    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        headers = getattr(exc, "headers", None)
        if isinstance(exc.detail, dict):
            content: dict[str, Any] = dict(exc.detail)
            content.setdefault(
                "detail", _DEFAULT_DETAIL_FOR.get(exc.status_code, "Error")
            )
        else:
            content = {"detail": exc.detail}
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        match = _V1_VERB_PATH_RE.match(request.url.path)
        if match is not None:
            _metrics.record_v1_request(
                recipe=match.group("name"),
                verb=match.group("verb"),
                status="validation_error",
                latency_seconds=0.0,
            )
        request_id = getattr(request.state, "request_id", "")
        sanitized_errors = [
            {k: v for k, v in err.items() if k not in ("input", "ctx")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "request_id": request_id,
                "detail": "Request validation failed",
                "code": "VALIDATION_ERROR",
                "errors": sanitized_errors,
            },
        )

    # Structured exception handler for unhandled non-HTTP exceptions.
    # FastAPI's default 500 response is a plain text "Internal Server Error"
    # string which leaks no details.  We register our own handler to ensure
    # the response is JSON-formatted with a stable structure that clients can
    # parse, while still NOT leaking stack traces.  HTTPException is
    # intentionally excluded so the dedicated HTTPException handler above
    # keeps responsibility for it.
    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        if isinstance(exc, HTTPException):
            # Let the dedicated HTTPException handler deal with it.
            raise exc
        request_id = getattr(request.state, "request_id", "")
        headers = {"X-Request-ID": request_id} if request_id else None
        return JSONResponse(
            status_code=500,
            content={"detail": "internal error", "code": "internal_error"},
            headers=headers,
        )

    app.add_middleware(RequestIDMiddleware)

    router = make_router(
        registry=registry,
        api_keys=api_keys or [],
        metadata_field_deny=metadata_field_deny,
    )
    app.include_router(router, prefix="/v1")
    return app
