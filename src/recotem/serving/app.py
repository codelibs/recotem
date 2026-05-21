"""FastAPI application factory for the Recotem serving layer.

``create_app(serve_config)`` is the single entry point.  It:
1. Validates security posture flags.
2. Emits the canonical ``security.posture`` log line.
3. Loads all recipes from ``serve_config.recipes_dir``.
4. Attempts initial artifact load for each recipe.
5. Builds the ``ModelRegistry``.
6. Registers FastAPI middlewares (TrustedHost, CORS).
7. Registers routes (via ``make_v1_router`` mounted at ``/v1``).
8. Wires the app lifespan to start the ``ArtifactWatcher`` and stop it
   gracefully on shutdown.

Notes:
- ``serve_config.recipes_dir`` is injected by the CLI before calling this
  function (it is not an env var).
- The ``KeyRing`` is built here from ``serve_config.signing_keys_raw``.  If
  that string is empty and ``dev_allow_unsigned`` is False, startup fails.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
import structlog.contextvars
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from recotem.artifact.format import ArtifactError, parse_header_from_bytes
from recotem.artifact.signing import KeyRing, unpickle_payload, verify_hmac
from recotem.config import ConfigError, ServeConfig
from recotem.recipe.loader import load_recipes_directory_lenient
from recotem.serving import metrics as _metrics
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.v1_router import make_v1_router
from recotem.serving.watcher import (
    ArtifactWatcher,
    build_initial_states,
    load_metadata,
    read_artifact_bytes,
    sha256_bytes,
    stat_marker,
)
from recotem.version import __version__

logger = structlog.get_logger(__name__)

# Allowed characters and length for echoing a client-supplied X-Request-ID.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


# ---------------------------------------------------------------------------
# Request-ID middleware
# ---------------------------------------------------------------------------


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a request-scoped ID to every response.

    - Reads ``X-Request-ID`` from the incoming request.  If the value passes
      the allow-list (``[A-Za-z0-9_-]``, 1–128 chars) it is echoed back;
      otherwise a server-generated 12-hex-char ID is used.
    - Binds ``request_id`` into structlog's context-var store so all log
      records emitted during the request carry the ID automatically.
    - Writes ``X-Request-ID`` onto the response regardless of status code,
      including 404/503 responses raised via ``HTTPException``.
    - Stores the resolved ID on ``request.state.request_id`` so handlers that
      need explicit access can read it without re-parsing the header.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        raw = request.headers.get("x-request-id", "")
        if _REQUEST_ID_RE.match(raw):
            request_id = raw
        else:
            request_id = uuid.uuid4().hex[:12]

        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            structlog.contextvars.unbind_contextvars("request_id")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(serve_config: ServeConfig) -> FastAPI:
    """Create and configure the FastAPI application.

    Parameters
    ----------
    serve_config:
        Fully populated ServeConfig.  The caller (CLI serve command) must
        have set ``serve_config.recipes_dir`` before calling this function.

    Returns
    -------
    FastAPI
        Configured application, ready for uvicorn.

    Raises
    ------
    ConfigError
        If security posture rules are violated or signing keys are missing
        and dev_allow_unsigned is False.
    """
    # 1. Validate unsafe flags.
    serve_config.validate_insecure_flags()

    # 2. Enforce host binding based on auth posture.
    serve_config.apply_auth_posture()

    # 3. Build KeyRing (or None for dev-unsigned path).
    # Always emit security.posture even when key-ring construction fails so
    # SIEM rules that look for the posture event still fire and operators see
    # the "missing" status in the log before the ConfigError propagates.
    _key_ring_build_exc: Exception | None = None
    key_ring: KeyRing | None = None
    try:
        key_ring = _build_key_ring(serve_config)
    except Exception as _exc:
        _key_ring_build_exc = _exc

    # 4. Emit security.posture log line (always, even on key-ring failure).
    _emit_security_posture(serve_config, key_ring)

    if _key_ring_build_exc is not None:
        raise _key_ring_build_exc

    # 5. Load recipes directory.
    recipes_dir_str: str = serve_config.recipes_dir
    if not recipes_dir_str:
        raise ValueError(
            "serve_config.recipes_dir must be set before calling create_app()"
        )
    recipes_dir = Path(recipes_dir_str).resolve()

    # Use lenient loader so a single broken YAML does not abort serve startup.
    # Failed files are inserted as stubs (loaded=False) so /health surfaces them.
    lenient_results = load_recipes_directory_lenient(recipes_dir)

    # Separate successfully-parsed recipes from YAML-parse failures.
    recipes = []
    yaml_failed_stubs: list[ModelEntry] = []
    # Maps stub_name → yaml_path for preseed_yaml_path calls after watcher init.
    yaml_failed_stub_paths: dict[str, Path] = {}
    _yaml_names_seen: dict[str, str] = {}  # name → filename for duplicate tracking

    for yaml_path, recipe, exc in lenient_results:
        if recipe is None:
            # YAML parse failed — insert stub keyed by file stem so /health
            # surfaces the problem.  File stem is the only available identifier
            # (recipe.name is unknown).
            stem = yaml_path.stem
            # Guard against duplicate stems in edge cases (two files whose stems
            # collide after the recipe name cannot be read).
            stub_name = stem
            _suffix = 0
            while stub_name in _yaml_names_seen:
                _suffix += 1
                stub_name = f"{stem}_{_suffix}"
            _yaml_names_seen[stub_name] = yaml_path.name
            yaml_failed_stub_paths[stub_name] = yaml_path
            logger.warning(
                "recipe_yaml_parse_failed_at_startup",
                file=yaml_path.name,
                name=stub_name,
                error=str(exc),
            )
            yaml_failed_stubs.append(
                ModelEntry(
                    name=stub_name,
                    recommender=None,
                    header={},
                    kid="",
                    metadata_df=None,
                    last_load_error=f"YAML parse failed: {exc}",
                    artifact_path="",
                    loaded=False,
                )
            )
        else:
            _yaml_names_seen[recipe.name] = yaml_path.name
            recipes.append(recipe)

    # 6. Build registry and attempt initial artifact loads.
    #
    # Spec contract: every recipe found on disk must appear in /health.
    # On successful load we insert a fully populated ModelEntry; on failure
    # we still insert a stub (loaded=False, last_load_error=<reason>) so
    # /health returns degraded and operators can see which recipes are not
    # serving.  /predict checks  and returns 503 for stubs.
    #
    # Loads are parallelised via a ThreadPoolExecutor so startup time is
    # bounded by the slowest single artifact rather than the sum of all
    # artifact load times.  Parallelism is configurable via
    # RECOTEM_STARTUP_PARALLELISM (clamped [1, 32]; default min(N, 8)).
    registry = ModelRegistry()
    loaded_entries: dict[str, ModelEntry] = {}

    # Register YAML-parse-failed stubs first so they appear in /health.
    for stub in yaml_failed_stubs:
        registry.replace(stub.name, stub)
        _metrics.inc_artifact_load_failure(stub.name)
        _metrics.set_model_loaded(stub.name, False)

    n_recipes = len(recipes)
    if serve_config.startup_parallelism <= 0:
        # Sentinel: derive default from recipe count, capped at 8.
        max_workers = min(n_recipes, 8) if n_recipes > 0 else 1
    else:
        max_workers = serve_config.startup_parallelism

    _startup_t0 = time.perf_counter()

    n_yaml_failed = len(yaml_failed_stubs)
    if n_recipes == 0:
        # Nothing to load; emit the summary immediately.
        logger.info(
            "startup_artifact_load_complete",
            total_recipes=n_yaml_failed,
            succeeded=0,
            failed=n_yaml_failed,
            wall_seconds=0.0,
            max_workers=max_workers,
        )
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_recipe = {
                executor.submit(
                    _try_load_artifact, recipe, key_ring, serve_config
                ): recipe
                for recipe in recipes
            }
            # executor.shutdown(wait=True) is called on __exit__; we consume
            # results as they complete so we can register entries promptly
            # even if one recipe is slow.
            for future in as_completed(future_to_recipe):
                recipe = future_to_recipe[future]
                # _try_load_artifact never raises (it catches internally and
                # returns a stub), but guard defensively.
                try:
                    entry = future.result()
                except Exception as exc:  # pragma: no cover — defensive only
                    logger.warning(
                        "recipe_load_future_error",
                        name=recipe.name,
                        error=str(exc),
                    )
                    entry = _failed_entry(recipe, f"unexpected error: {exc}")

                registry.replace(recipe.name, entry)
                _metrics.set_model_loaded(recipe.name, entry.loaded)
                if entry.loaded:
                    loaded_entries[recipe.name] = entry
                else:
                    _metrics.inc_artifact_load_failure(recipe.name)
                    logger.warning(
                        "recipe_not_loaded_at_startup",
                        name=recipe.name,
                        error=entry.last_load_error,
                    )

        _wall_seconds = time.perf_counter() - _startup_t0
        _total = n_recipes + n_yaml_failed
        logger.info(
            "startup_artifact_load_complete",
            total_recipes=_total,
            succeeded=len(loaded_entries),
            failed=_total - len(loaded_entries),
            wall_seconds=round(_wall_seconds, 3),
            max_workers=max_workers,
        )

    _metrics.set_active_recipes(len(loaded_entries))

    # Build watcher initial states — captures mtime/sha to avoid re-load on
    # first tick (spec: "capture initial mtime/sha inside the watcher's own
    # state right when it starts").
    initial_states = build_initial_states(recipes, loaded_entries)

    # 7. Lifespan manages the watcher thread.
    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[type-arg]
        watcher = ArtifactWatcher(
            registry=registry,
            recipes_dir=recipes_dir,
            serve_config=serve_config,
            key_ring=key_ring,
            initial_states=initial_states,
        )
        # Pre-seed the watcher's _yaml_path_to_name with startup-failed stubs
        # so that the first rescan can look up the stub_name by yaml_path (I-9).
        for _stub_name, _stub_yaml_path in yaml_failed_stub_paths.items():
            watcher.preseed_yaml_path(_stub_yaml_path, _stub_name)
        watcher.start()

        banner_task = None
        if serve_config.insecure_no_auth or serve_config.dev_allow_unsigned:
            import asyncio

            async def _warn_loop() -> None:
                while True:
                    await asyncio.sleep(60)
                    if serve_config.insecure_no_auth:
                        _emit_insecure_banner(serve_config)
                    if serve_config.dev_allow_unsigned:
                        _emit_dev_unsigned_banner(serve_config)

            banner_task = asyncio.create_task(_warn_loop())

        yield

        watcher.stop()
        # Join with a bounded timeout so we don't block process shutdown if
        # the watcher is wedged inside an fsspec call.  The watcher is a
        # daemon thread, so the process can still exit if join() times out;
        # the timeout exists to give in-flight stat/read calls a chance to
        # finish cleanly before the host tears down their underlying sockets.
        watcher_join_timeout = max(1.0, min(5.0, float(serve_config.drain_seconds)))
        watcher.join(timeout=watcher_join_timeout)
        if watcher.is_alive():
            logger.warning(
                "artifact_watcher_join_timeout",
                timeout=watcher_join_timeout,
            )
        if banner_task is not None:
            import asyncio

            banner_task.cancel()
            try:
                await banner_task
            except asyncio.CancelledError:
                pass
        logger.info(
            "serve_shutdown",
            drain_seconds=serve_config.drain_seconds,
        )

    # 8. Build app.
    # Fail-secure: OpenAPI UI is disabled unless the environment is explicitly
    # set to a known development value.  An unset (production) environment must
    # never expose /docs to avoid accidental schema disclosure.
    _dev_envs = {"development", "dev", "test"}
    _is_dev = (serve_config.env or "").lower() in _dev_envs
    _docs_url = "/docs" if _is_dev else None
    _redoc_url = "/redoc" if _is_dev else None
    _openapi_url = "/openapi.json" if _is_dev else None

    app = FastAPI(
        title="Recotem Inference API",
        version=__version__,
        lifespan=lifespan,
        docs_url=_docs_url,
        redoc_url=_redoc_url,
        openapi_url=_openapi_url,
    )

    # 9. Structured exception handler for unhandled non-HTTP exceptions.
    # FastAPI's default 500 response is a plain text "Internal Server Error"
    # string which leaks no details.  We register our own handler to ensure
    # the response is JSON-formatted with a stable structure that clients can
    # parse, while still NOT leaking stack traces.  HTTPException is
    # intentionally excluded so FastAPI's own handler keeps it.
    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        if isinstance(exc, HTTPException):
            # Let FastAPI's built-in HTTPException handler deal with it.
            raise exc
        return JSONResponse(
            status_code=500,
            content={"detail": "internal error", "code": "internal_error"},
        )

    # 10. Middlewares.
    # Starlette processes add_middleware calls in LIFO order: the last one added
    # is the outermost wrapper (first to process the request, last to process
    # the response).  We want RequestIDMiddleware to be outermost so it sets
    # X-Request-ID on every response regardless of what inner layers do.
    # Therefore RequestIDMiddleware is added LAST (after TrustedHost and CORS).

    # allowed_hosts is always non-empty after ServeConfig.from_env() because
    # _split_csv_env falls back to _DEFAULT_ALLOWED_HOSTS on empty/unset input.
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=serve_config.allowed_hosts,
    )

    if serve_config.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=serve_config.allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

    # Added last so it is outermost: ensures X-Request-ID is on every response.
    app.add_middleware(RequestIDMiddleware)

    # 11. Routes.
    # ``--insecure-no-auth`` must short-circuit the X-API-Key check even when
    # ``RECOTEM_API_KEYS`` is still set in the environment, otherwise the flag
    # is documented but silently ineffective.
    router_api_keys = [] if serve_config.insecure_no_auth else serve_config.api_keys
    v1_router = make_v1_router(
        registry=registry,
        api_keys=router_api_keys,
        metadata_field_deny=serve_config.metadata_field_deny,
    )
    app.include_router(v1_router, prefix="/v1")

    return app


# ---------------------------------------------------------------------------
# Key ring construction
# ---------------------------------------------------------------------------


def _build_key_ring(serve_config: ServeConfig) -> KeyRing | None:
    """Build a KeyRing from signing_keys_raw, or None for dev-unsigned mode."""
    if serve_config.dev_allow_unsigned:
        logger.warning(
            "signing_key_verification_disabled",
            reason="dev_allow_unsigned",
        )
        return None

    if not serve_config.signing_keys_raw:
        raise ConfigError(
            "RECOTEM_SIGNING_KEYS is not set. "
            "A signing key is required to verify artifacts. "
            "Use 'recotem keygen --type signing' to generate one, "
            "or set RECOTEM_SIGNING_KEYS=<kid>:<hex64>."
        )

    return KeyRing(serve_config.signing_keys_raw)


# ---------------------------------------------------------------------------
# Security posture log line
# ---------------------------------------------------------------------------


def _emit_security_posture(serve_config: ServeConfig, key_ring: KeyRing | None) -> None:
    """Emit the canonical security.posture log line.

    The ``signing_keys`` field is a list of ``{"kid", "fingerprint"}`` pairs
    where the fingerprint is the first 8 hex chars of ``sha256(key_bytes)``.
    Operators can confirm prod ≠ staging without ever logging key material.
    The legacy ``signing_kids`` field is preserved for SIEM rules built
    against the earlier schema.

    The signing_key_status field reflects:
    - "configured"          — keys are present and the KeyRing was built.
    - "dev_allow_unsigned"  — dev-unsigned mode, no keys required.
    - "missing"             — keys absent and dev-unsigned not set; startup
                                    will fail after this log line.
    """
    if key_ring is not None:
        kids = key_ring.kids()
        signing_keys = [
            {"kid": kid, "fingerprint": key_ring.fingerprint(kid)} for kid in kids
        ]
        signing_key_status = "configured"
    elif serve_config.dev_allow_unsigned:
        kids = []
        signing_keys = []
        signing_key_status = "dev_allow_unsigned"
    else:
        kids = []
        signing_keys = []
        signing_key_status = "missing"

    logger.info(
        "security.posture",
        auth_enabled=(
            bool(serve_config.api_keys) and not serve_config.insecure_no_auth
        ),
        bind_host=serve_config.host,
        signing_keys=signing_keys,
        signing_kids=kids,
        signing_key_status=signing_key_status,
        env=serve_config.env,
        allowed_hosts=serve_config.allowed_hosts,
        allowed_origins=serve_config.allowed_origins,
        unsafe_mode=(serve_config.insecure_no_auth or serve_config.dev_allow_unsigned),
    )


def _emit_insecure_banner(serve_config: ServeConfig) -> None:
    """Emit a WARN banner when running without API key authentication."""
    logger.warning(
        "INSECURE_NO_AUTH_ACTIVE",
        message=(
            "recotem serve is running WITHOUT API key authentication. "
            "All predict requests are accepted without credentials. "
            "This is only permitted in development/test environments. "
            "Set RECOTEM_API_KEYS or remove --insecure-no-auth before "
            "exposing this service to any network."
        ),
        env=serve_config.env,
        bind_host=serve_config.host,
    )


def _emit_dev_unsigned_banner(serve_config: ServeConfig) -> None:
    """Emit a WARN banner when running with --dev-allow-unsigned.

    This posture loads artifacts that may be signed with the deterministic
    in-memory dev key, which means the server will accept any artifact
    anyone in the org has produced under that key.  Far more dangerous
    than insecure-no-auth on its own.
    """
    logger.warning(
        "DEV_ALLOW_UNSIGNED_ACTIVE",
        message=(
            "recotem serve is running with --dev-allow-unsigned. "
            "Artifacts signed with the deterministic dev key are accepted. "
            "This is only permitted in development/test environments. "
            "Remove --dev-allow-unsigned and set RECOTEM_SIGNING_KEYS to "
            "a unique value before exposing this service to any network."
        ),
        env=serve_config.env,
        bind_host=serve_config.host,
    )


# ---------------------------------------------------------------------------
# Initial artifact load
# ---------------------------------------------------------------------------


def _failed_entry(recipe: Any, reason: str) -> ModelEntry:
    """Stub ModelEntry inserted at startup when an artifact failed to load.

    Carries enough context for ``/health`` to show ``loaded=false`` plus the
    reason string.  The route handlers must check ``entry.loaded`` before
    dereferencing ``entry.recommender`` (which is ``None`` here).
    """
    return ModelEntry(
        name=recipe.name,
        recommender=None,
        header={},
        kid="",
        metadata_df=None,
        last_load_error=reason,
        artifact_path=recipe.output.path,
        loaded=False,
    )


def _try_load_artifact(
    recipe: Any,
    key_ring: KeyRing | None,
    serve_config: ServeConfig,
) -> ModelEntry:
    """Attempt to load the artifact for *recipe* at startup.

    Returns a fully-populated ModelEntry on success, or a stub entry with
    ``loaded=False`` and ``last_load_error`` set on any failure.  Either way
    the caller registers the entry so ``/health`` reports the recipe.
    """
    artifact_path = recipe.output.path
    max_artifact_bytes = serve_config.max_artifact_bytes
    max_payload_bytes = serve_config.max_payload_bytes

    try:
        data = read_artifact_bytes(artifact_path, max_artifact_bytes)
    except ArtifactError as exc:
        logger.warning("initial_artifact_read_failed", name=recipe.name, error=str(exc))
        return _failed_entry(recipe, f"read failed: {exc}")
    except (MemoryError, RecursionError):
        raise
    except Exception as exc:
        logger.warning("initial_artifact_read_error", name=recipe.name, error=str(exc))
        return _failed_entry(recipe, f"read error: {exc}")

    sha256 = sha256_bytes(data)

    try:
        # Use max_payload_bytes (not max_artifact_bytes) as the payload cap so
        # serve-side deserialization memory is bounded independently of the outer
        # container size.
        hdr = parse_header_from_bytes(data, max_payload_bytes)
    except ArtifactError as exc:
        logger.warning(
            "initial_artifact_parse_failed", name=recipe.name, error=str(exc)
        )
        return _failed_entry(recipe, f"parse failed: {exc}")

    payload_bytes = data[hdr.payload_offset :]

    if key_ring is not None:
        try:
            verify_hmac(
                key_ring,
                hdr.kid,
                hdr.kid.encode("utf-8"),
                hdr.header_data,
                payload_bytes,
                hdr.hmac_digest,
            )
        except ArtifactError as exc:
            logger.warning(
                "initial_artifact_hmac_failed",
                name=recipe.name,
                kid=hdr.kid,
                error=str(exc),
            )
            return _failed_entry(recipe, f"HMAC verify failed: {exc}")
    else:
        logger.warning(
            "initial_artifact_hmac_skipped_dev",
            name=recipe.name,
            kid=hdr.kid,
        )

    # Decode header JSON FIRST — failing here with a corrupt header should not
    # require a working payload (unpickle on an empty payload would fail with
    # an unrelated "ran out of input" error and mask the actual problem).
    try:
        header_bytes = hdr.header_data
        header_dict: dict[str, Any] = json.loads(header_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning(
            "initial_artifact_header_json_failed",
            name=recipe.name,
            kid=hdr.kid,
            error=str(exc),
        )
        return _failed_entry(recipe, f"header JSON decode failed: {exc}")

    try:
        recommender = unpickle_payload(payload_bytes)
    except ArtifactError as exc:
        logger.warning(
            "initial_artifact_deserialize_failed",
            name=recipe.name,
            kid=hdr.kid,
            error=str(exc),
        )
        return _failed_entry(recipe, f"deserialize failed: {exc}")

    metadata_df = None
    metadata_index = None
    if recipe.item_metadata is not None:
        try:
            metadata_df = load_metadata(recipe, recipe.name)
            from recotem.metadata.loader import build_metadata_index  # noqa: PLC0415

            deny_set: frozenset[str] = frozenset(
                s.lower() for s in (serve_config.metadata_field_deny or [])
            )
            metadata_index = build_metadata_index(metadata_df, deny_set)
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            logger.warning(
                "initial_artifact_metadata_failed",
                name=recipe.name,
                error=str(exc),
            )
            return _failed_entry(recipe, f"metadata load failed: {exc}")

    marker = stat_marker(artifact_path)
    entry = ModelEntry(
        name=recipe.name,
        recommender=recommender,
        header=header_dict,
        kid=hdr.kid,
        metadata_df=metadata_df,
        metadata_index=metadata_index,
        last_load_error=None,
        artifact_path=artifact_path,
        _loaded_marker=(marker, sha256),
        loaded_at_unix=time.time(),
        config_digest=header_dict.get("config_digest", "") or "",
        algorithms=header_dict.get("algorithms", []) or [],
    )

    logger.info(
        "recipe_loaded",
        name=recipe.name,
        kid=hdr.kid,
        trained_at=header_dict.get("trained_at"),
        best_class=header_dict.get("best_class"),
    )
    return entry
