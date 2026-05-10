"""FastAPI application factory for the Recotem serving layer.

``create_app(serve_config)`` is the single entry point.  It:
1. Validates security posture flags.
2. Emits the canonical ``security.posture`` log line.
3. Loads all recipes from ``serve_config.recipes_dir``.
4. Attempts initial artifact load for each recipe.
5. Builds the ``ModelRegistry``.
6. Registers FastAPI middlewares (TrustedHost, CORS).
7. Registers routes (via ``make_router``).
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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from recotem.artifact.format import ArtifactError, parse_header_from_bytes
from recotem.artifact.signing import KeyRing, unpickle_payload, verify_hmac
from recotem.config import ServeConfig
from recotem.recipe.loader import load_recipes_directory
from recotem.serving import metrics as _metrics
from recotem.serving.registry import ModelEntry, ModelRegistry
from recotem.serving.routes import make_router
from recotem.serving.watcher import (
    ArtifactWatcher,
    _load_metadata,
    _read_artifact_bytes,
    _sha256_bytes,
    _stat_marker,
    build_initial_states,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


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
    ValueError
        If security posture rules are violated.
    ArtifactError
        If signing keys are missing and dev_allow_unsigned is False.
    """
    # 1. Validate unsafe flags.
    serve_config.validate_insecure_flags()

    # 2. Enforce host binding based on auth posture.
    serve_config.apply_auth_posture()

    # 3. Build KeyRing (or None for dev-unsigned path).
    key_ring: KeyRing | None = _build_key_ring(serve_config)

    # 4. Emit security.posture log line.
    _emit_security_posture(serve_config, key_ring)

    # 5. Load recipes directory.
    recipes_dir_str: str = getattr(serve_config, "recipes_dir", "")
    if not recipes_dir_str:
        raise ValueError(
            "serve_config.recipes_dir must be set before calling create_app()"
        )
    recipes_dir = Path(recipes_dir_str).resolve()

    recipes = load_recipes_directory(recipes_dir)

    # 6. Build registry and attempt initial artifact loads.
    #
    # Spec contract: every recipe found on disk must appear in /health.
    # On successful load we insert a fully populated ModelEntry; on failure
    # we still insert a stub (loaded=False, last_load_error=<reason>) so
    # /health returns degraded and operators can see which recipes are not
    # serving.  /predict checks `loaded` and returns 503 for stubs.
    registry = ModelRegistry()
    loaded_entries: dict[str, ModelEntry] = {}

    for recipe in recipes:
        entry = _try_load_artifact(recipe, key_ring, serve_config)
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
            banner_task.cancel()
        logger.info(
            "serve_shutdown",
            drain_seconds=serve_config.drain_seconds,
        )

    # 8. Build app.
    app = FastAPI(
        title="Recotem Inference API",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # 9. Middlewares.
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
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    # 10. Routes.
    # ``--insecure-no-auth`` must short-circuit the X-API-Key check even when
    # ``RECOTEM_API_KEYS`` is still set in the environment, otherwise the flag
    # is documented but silently ineffective.
    router_api_keys = [] if serve_config.insecure_no_auth else serve_config.api_keys
    router = make_router(
        registry=registry,
        api_keys=router_api_keys,
        metadata_field_deny=serve_config.metadata_field_deny,
    )
    app.include_router(router)

    if serve_config.insecure_no_auth:
        _emit_insecure_banner(serve_config)
    if serve_config.dev_allow_unsigned:
        _emit_dev_unsigned_banner(serve_config)

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
        raise ArtifactError(
            "RECOTEM_SIGNING_KEYS is not set. "
            "A signing key is required to verify artifacts. "
            "Use 'recotem keygen --type signing' to generate one."
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
    """
    if key_ring is not None:
        kids = key_ring.kids()
        signing_keys = [
            {"kid": kid, "fingerprint": key_ring.fingerprint(kid)} for kid in kids
        ]
    else:
        kids = []
        signing_keys = []

    logger.info(
        "security.posture",
        auth_enabled=(
            bool(serve_config.api_keys) and not serve_config.insecure_no_auth
        ),
        bind_host=serve_config.host,
        signing_keys=signing_keys,
        signing_kids=kids,
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
    max_bytes = serve_config.max_artifact_bytes

    try:
        data = _read_artifact_bytes(artifact_path, max_bytes)
    except ArtifactError as exc:
        logger.warning("initial_artifact_read_failed", name=recipe.name, error=str(exc))
        return _failed_entry(recipe, f"read failed: {exc}")
    except Exception as exc:
        logger.warning("initial_artifact_read_error", name=recipe.name, error=str(exc))
        return _failed_entry(recipe, f"read error: {exc}")

    sha256 = _sha256_bytes(data)

    try:
        hdr = parse_header_from_bytes(data, max_bytes)
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
    if recipe.item_metadata is not None:
        try:
            metadata_df = _load_metadata(recipe, recipe.name)
        except Exception as exc:
            logger.warning(
                "initial_artifact_metadata_failed",
                name=recipe.name,
                error=str(exc),
            )
            return _failed_entry(recipe, f"metadata load failed: {exc}")

    marker = _stat_marker(artifact_path)
    entry = ModelEntry(
        name=recipe.name,
        recommender=recommender,
        header=header_dict,
        kid=hdr.kid,
        metadata_df=metadata_df,
        last_load_error=None,
        artifact_path=artifact_path,
        _loaded_marker=(marker, sha256),
    )

    logger.info(
        "recipe_loaded",
        name=recipe.name,
        kid=hdr.kid,
        trained_at=header_dict.get("trained_at"),
        best_class=header_dict.get("best_class"),
    )
    return entry
