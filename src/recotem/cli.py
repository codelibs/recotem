"""Recotem CLI --- Typer-based command interface.

Commands:
  train     Fetch data, tune hyperparameters, train, and sign an artifact.
  serve     Start the FastAPI prediction server with hot-swap.
  inspect   Read and verify an artifact header (no deserialization).
  validate  Validate a recipe file and probe data-source connectivity.
  schema    Emit the JSON Schema for the Recipe model.
  keygen    Generate a signing or API key (kid, plaintext, hash triple).

Exit codes:
  0  success
  1  unexpected / unknown error
  2  RecipeError (bad schema, env expansion, validation)
  3  DataSourceError (fetch failure, missing file, bad credentials)
  4  TrainingError (Optuna search, split, evaluation failures)
  5  ArtifactError (sign / verify / parse failure)
  6  LockContestedError (recipe lock held by another process, --fail-on-busy)
  7  HttpFetchError (SSRF guard, timeout, HTTP error during source fetch)
  8  configuration error (missing RECOTEM_SIGNING_KEYS, bad env var)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Annotated

import structlog
import typer

from recotem._exit_codes import (
    _EXIT_ARTIFACT,
    _EXIT_CONFIG,
    _EXIT_DATASOURCE,
    _EXIT_HTTP_FETCH,
    _EXIT_LOCK_CONTESTED,
    _EXIT_RECIPE,
    _EXIT_SUCCESS,
    _EXIT_TRAINING,
    _EXIT_UNKNOWN,
    _map_exception_to_exit,
)

app = typer.Typer(
    name="recotem",
    help="Recipe-driven recommender training and serving.",
    add_completion=False,
)


# ---------------------------------------------------------------------------
# Exit-code helpers
# ---------------------------------------------------------------------------

# Re-export constants for backward-compat with existing tests that import from
# recotem.cli directly (e.g. ``from recotem.cli import _EXIT_HTTP_FETCH``).
# The canonical definitions live in recotem._exit_codes.
__all__ = [
    "_EXIT_SUCCESS",
    "_EXIT_UNKNOWN",
    "_EXIT_RECIPE",
    "_EXIT_DATASOURCE",
    "_EXIT_TRAINING",
    "_EXIT_ARTIFACT",
    "_EXIT_LOCK_CONTESTED",
    "_EXIT_HTTP_FETCH",
    "_EXIT_CONFIG",
    "_map_exception_to_exit",
]


def _exit(code: int, message: str | None = None) -> None:
    """Print *message* to stderr (if provided) and sys.exit with *code*."""
    if message:
        typer.echo(message, err=True)
    raise typer.Exit(code=code)


# ---------------------------------------------------------------------------
# recotem train
# ---------------------------------------------------------------------------


@app.command()
def train(
    recipe: Annotated[
        Path,
        typer.Argument(help="Path to the recipe YAML file.", exists=True),
    ],
    no_lock: Annotated[
        bool,
        typer.Option("--no-lock", help="Disable per-recipe file lock."),
    ] = False,
    fail_on_busy: Annotated[
        bool,
        typer.Option(
            "--fail-on-busy",
            help="Exit non-zero if the recipe lock is held instead of skipping.",
        ),
    ] = False,
    lock_timeout: Annotated[
        float,
        typer.Option(
            "--lock-timeout",
            help=(
                "Seconds to wait for the per-recipe lock before failing. "
                "0.0 = non-blocking immediate failure (default). "
                "-1 = wait indefinitely."
            ),
        ),
    ] = 0.0,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Suppress per-trial output.")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Dump per-trial params.")
    ] = False,
    dev_allow_unsigned: Annotated[
        bool,
        typer.Option(
            "--dev-allow-unsigned",
            help=(
                "Skip HMAC signing.  Requires RECOTEM_ENV=development "
                "AND --i-understand-this-loads-arbitrary-code."
            ),
        ),
    ] = False,
    i_understand_this_loads_arbitrary_code: Annotated[
        bool,
        typer.Option(
            "--i-understand-this-loads-arbitrary-code",
            help="Required companion flag for --dev-allow-unsigned.",
        ),
    ] = False,
    env_var: Annotated[
        list[str] | None,
        typer.Option(
            "--env-var",
            help="Extra KEY=VALUE pairs for recipe env expansion.",
        ),
    ] = None,
    run_id_opt: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            help=(
                "Stable run identifier. Reuse the same value across "
                "invocations to resume a persistent Optuna study "
                "(requires `training.storage_path` set in the recipe). "
                "Defaults to a fresh random id."
            ),
        ),
    ] = None,
) -> None:
    """Fetch data, tune hyperparameters, train, and sign a model artifact."""
    _configure_logging_from_env()

    if dev_allow_unsigned and not i_understand_this_loads_arbitrary_code:
        # Flag-pair misuse is a configuration error (CLI invocation), not a
        # recipe error — map to EXIT_CONFIG so operators can distinguish it
        # from a malformed recipe via the documented exit-code table.
        _exit(
            _EXIT_CONFIG,
            "--dev-allow-unsigned requires "
            "--i-understand-this-loads-arbitrary-code to also be passed.",
        )
    if dev_allow_unsigned:
        _check_dev_env("--dev-allow-unsigned")

    # Validate lock_timeout: -1 = indefinite wait; 0 = nonblocking; >0 = wait N seconds.
    # Any other negative value is not a recognised sentinel and would silently behave
    # as indefinite wait, which is almost certainly a caller mistake.
    if lock_timeout < 0 and lock_timeout != -1.0:
        _exit(
            _EXIT_CONFIG,
            f"--lock-timeout must be -1 (indefinite), 0 (non-blocking), or a "
            f"positive number of seconds; got {lock_timeout}.",
        )

    extra_allowed: dict[str, str] = {}
    for pair in env_var or []:
        if "=" not in pair:
            _exit(_EXIT_RECIPE, f"--env-var {pair!r} must be in KEY=VALUE format.")
        k, _, v = pair.partition("=")
        extra_allowed[k] = v

    try:
        from recotem.recipe.loader import load_recipe

        loaded_recipe = load_recipe(recipe, extra_allowed=extra_allowed or None)
    except Exception as exc:
        _exit(_map_exception_to_exit(exc), f"Recipe error: {exc}")

    if run_id_opt is not None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", run_id_opt):
            _exit(
                _EXIT_RECIPE,
                "--run-id must match [A-Za-z0-9_.-]{1,64}.",
            )
        run_id = run_id_opt
    else:
        run_id = uuid.uuid4().hex[:12]

    try:
        from recotem.training.pipeline import (
            run_training,  # type: ignore[import-untyped]
        )

        run_training(
            loaded_recipe,
            run_id=run_id,
            no_lock=no_lock,
            fail_on_busy=fail_on_busy,
            lock_timeout=lock_timeout,
            quiet=quiet,
            verbose=verbose,
            dev_allow_unsigned=dev_allow_unsigned,
        )
    except SystemExit as exc:
        # Preserve well-known recotem exit codes (0 and 2–8); normalize
        # everything else (None, arbitrary int, str) to _EXIT_UNKNOWN so
        # we never collapse a real failure to 0 or pass an unrecognised code
        # to the shell.  Literal int 0 is the only success sentinel.
        code = exc.code
        if isinstance(code, int) and code in (0, 2, 3, 4, 5, 6, 7, 8):
            raise typer.Exit(code=code) from exc
        raise typer.Exit(code=_EXIT_UNKNOWN) from exc
    except Exception as exc:
        # The canonical train_error event is emitted inside run_training so
        # library callers receive it too — we only need to map the exception
        # to the operator-visible exit code here.
        code = _map_exception_to_exit(exc)
        _exit(code, f"Training failed: {exc}")


# ---------------------------------------------------------------------------
# recotem serve
# ---------------------------------------------------------------------------


@app.command()
def serve(
    recipes: Annotated[
        Path,
        typer.Option(
            "--recipes",
            help="Directory containing *.yaml recipe files.",
            exists=True,
            file_okay=False,
            dir_okay=True,
        ),
    ],
    port: Annotated[
        int | None,
        typer.Option("--port", "-p", help="Bind port (overrides RECOTEM_PORT)."),
    ] = None,
    host: Annotated[
        str | None,
        typer.Option("--host", "-H", help="Bind host (overrides RECOTEM_HOST)."),
    ] = None,
    insecure_no_auth: Annotated[
        bool,
        typer.Option(
            "--insecure-no-auth",
            help=(
                "Disable API key authentication.  Requires "
                "RECOTEM_ENV in {development,dev,test}."
            ),
        ),
    ] = False,
    dev_allow_unsigned: Annotated[
        bool,
        typer.Option(
            "--dev-allow-unsigned",
            help=(
                "Skip HMAC verification on artifact load.  "
                "Requires RECOTEM_ENV=development AND "
                "--i-understand-this-loads-arbitrary-code."
            ),
        ),
    ] = False,
    i_understand_this_loads_arbitrary_code: Annotated[
        bool,
        typer.Option(
            "--i-understand-this-loads-arbitrary-code",
            help="Required companion flag for --dev-allow-unsigned.",
        ),
    ] = False,
) -> None:
    """Start the FastAPI prediction server with artifact hot-swap."""
    _configure_logging_from_env()

    if dev_allow_unsigned and not i_understand_this_loads_arbitrary_code:
        # Flag-pair misuse: configuration error per the exit-code table.
        _exit(
            _EXIT_CONFIG,
            "--dev-allow-unsigned requires "
            "--i-understand-this-loads-arbitrary-code to also be passed.",
        )

    try:
        from recotem.config import ConfigError, ServeConfig

        cfg = ServeConfig.from_env()
    except ConfigError as exc:
        _exit(_EXIT_CONFIG, f"Configuration error: {exc}")

    if host is not None:
        cfg.host = host
    if port is not None:
        cfg.port = port
    cfg.insecure_no_auth = insecure_no_auth
    cfg.dev_allow_unsigned = dev_allow_unsigned
    cfg.recipes_dir = str(recipes.resolve())

    try:
        from recotem.serving.app import create_app

        fastapi_app = create_app(cfg)
    except Exception as exc:
        code = _map_exception_to_exit(exc)
        _exit(code, f"Server startup failed: {exc}")

    import uvicorn

    _srv_log = structlog.get_logger(__name__)

    try:
        uvicorn.run(
            fastapi_app,
            host=cfg.host,
            port=cfg.port,
            timeout_graceful_shutdown=cfg.drain_seconds,
            log_config=None,
        )
    except KeyboardInterrupt:
        _srv_log.info("serve_terminated", reason="KeyboardInterrupt")
        raise typer.Exit(code=0) from None
    except (MemoryError, RecursionError):
        # Never collapse OOM/recursion — the operator needs the real cause to
        # size the host correctly.  Round-12 OOM-propagation policy.
        raise
    except OSError as exc:
        import errno as _errno  # noqa: PLC0415

        if exc.errno in (_errno.EADDRINUSE, _errno.EACCES, _errno.EADDRNOTAVAIL):
            _srv_log.error(
                "serve_bind_failed",
                error=str(exc),
                host=cfg.host,
                port=cfg.port,
                errno=exc.errno,
            )
            _exit(_EXIT_CONFIG, f"Server bind failed: {exc}")
        _srv_log.error(
            "serve_runtime_oserror",
            error=str(exc),
            errno=exc.errno,
            host=cfg.host,
            port=cfg.port,
        )
        _exit(_map_exception_to_exit(exc), f"Server runtime failure: {exc}")
    except Exception as exc:
        _srv_log.error("serve_startup_failed", error=str(exc))
        code = _map_exception_to_exit(exc)
        _exit(code, f"Server error: {exc}")


# ---------------------------------------------------------------------------
# recotem inspect
# ---------------------------------------------------------------------------


def _repair_uri(value: str) -> str:
    """Restore a double-slash URI collapsed by pathlib.Path on POSIX.

    ``pathlib.Path("s3://bucket/key")`` normalises to ``s3:/bucket/key``.
    Detect the single-slash form (``scheme:/non-slash``) and reinsert the
    missing slash so fsspec receives a valid ``scheme://…`` URI.

    Local paths (``/abs/path``, ``relative/path``) and already-correct URIs
    (``scheme://…``) are returned unchanged.

    Pattern matched: ``^[a-z][a-z0-9+.\\-]+:/[^/]``  (RFC 3986 scheme +
    exactly one slash + a non-slash character).
    """
    import re as _re

    if _re.match(r"^[a-z][a-z0-9+.\-]+:/[^/]", value):
        scheme, rest = value.split(":/", 1)
        return f"{scheme}://{rest}"
    return value


@app.command()
def inspect(
    artifact: Annotated[
        str,
        # Accepted as str (not Path) so that Typer does not collapse remote
        # URIs: pathlib.Path("s3://bucket/key") → "s3:/bucket/key" on POSIX.
        # The raw string is passed verbatim to fsspec.core.url_to_fs() after
        # applying _repair_uri() to restore any collapsed double-slash.
        # Missing-path errors are surfaced by the fsspec.open() call inside
        # this function and reported as exit 5 (ArtifactError).
        typer.Argument(help="Path or URI to the .recotem artifact file."),
    ],
    dev_allow_unsigned: Annotated[
        bool,
        typer.Option(
            "--dev-allow-unsigned",
            help=(
                "Verify against the deterministic in-memory dev signing key "
                "when RECOTEM_SIGNING_KEYS is unset.  Requires "
                "RECOTEM_ENV=development.  Useful for inspecting "
                "artifacts produced by `recotem train --dev-allow-unsigned`."
            ),
        ),
    ] = False,
) -> None:
    """Read and verify an artifact header without deserializing the payload.

    Reads the structural fields and HMAC, verifies against RECOTEM_SIGNING_KEYS,
    and prints the header JSON.  Does not invoke the deserializer — no
    ``--i-understand-this-loads-arbitrary-code`` flag is needed because
    inspect only reads the signed header, never the serialized payload.

    Requires RECOTEM_SIGNING_KEYS to be set (or --dev-allow-unsigned with
    RECOTEM_ENV=development) so that a scripted pipeline can distinguish
    verified output from unverified output.  Exits non-zero (exit 5) when
    signing keys are absent and --dev-allow-unsigned is not passed.
    """
    _configure_logging_from_env()

    # Gate dev-only fallback behind RECOTEM_ENV=development immediately so
    # flag-pair validation fires before any I/O or parsing takes place.
    # Previously this check was deferred until after file read + header parse,
    # which meant a dev flag mis-use in a production environment was only
    # caught after potentially expensive I/O.
    if dev_allow_unsigned:
        _check_dev_env("--dev-allow-unsigned")

    import fsspec

    from recotem.artifact.format import (
        ArtifactError,
        parse_header_from_bytes,
    )
    from recotem.artifact.io import resolve_artifact_pointer
    from recotem.config import ServeConfig

    # Use max_artifact_bytes as the file read cap (matches the serving-watcher
    # protocol) and max_payload_bytes as the payload-parse cap (matches the
    # serve-side deserialization bound).  Previously both used max_payload_bytes,
    # which could reject valid artifacts larger than 512 MiB at read time even
    # though the artifact container itself is bounded by max_artifact_bytes.
    cfg = ServeConfig.from_env()
    read_cap = cfg.max_artifact_bytes
    parse_cap = cfg.max_payload_bytes
    artifact_uri = _repair_uri(artifact)
    try:
        # Bounded read so a 100 GiB file cannot OOM the CLI before the cap
        # check fires; matches the serving-watcher protocol.
        fs, resolved_path = fsspec.core.url_to_fs(artifact_uri)
        with fs.open(resolved_path, "rb") as fh:
            data = fh.read(read_cap + 1)
    except ImportError as exc:
        # A missing optional fsspec backend (gcsfs, s3fs, adlfs, …) raises
        # ImportError when url_to_fs resolves the scheme.  Surface a targeted
        # install hint so the operator can fix it without deciphering a stack trace.
        artifact_str = artifact_uri
        _SCHEME_EXTRA: dict[str, str] = {
            "gs": "gcs",
            "gcs": "gcs",
            "s3": "s3",
            "s3a": "s3",
            "az": "azure",
            "abfs": "azure",
            "abfss": "azure",
        }
        # artifact_uri has been repaired by _repair_uri, so double-slash URIs
        # are already restored.  Only match "scheme://" here.
        extra = None
        for prefix, extra_name in _SCHEME_EXTRA.items():
            if artifact_str.lower().startswith(f"{prefix}://"):
                extra = extra_name
                break
        if extra:
            hint = f"pip install recotem[{extra}]"
        else:
            hint = "pip install the required fsspec backend for your storage scheme"
        _exit(
            _EXIT_ARTIFACT,
            f"Cannot open artifact '{artifact_uri}': missing optional dependency "
            f"({type(exc).__name__}: {exc}). "
            f"Hint: {hint}",
        )
    except OSError as exc:
        _exit(_EXIT_ARTIFACT, f"Cannot read artifact '{artifact_uri}': {exc}")
    except (MemoryError, RecursionError):
        # Never collapse OOM/recursion into a "read failed" message — the
        # operator needs to see the real cause to size the host correctly.
        raise
    except Exception as exc:  # noqa: BLE001
        # fsspec backends raise SDK-specific exceptions that do not derive
        # from OSError (botocore.NoCredentialsError, gcsfs.HttpError, etc.).
        # Without this branch they would bubble up to typer's default handler
        # and surface as exit 1, breaking the documented exit-code contract.
        _exit(
            _EXIT_ARTIFACT,
            f"Cannot open artifact '{artifact_uri}' ({type(exc).__name__}): {exc}",
        )

    try:
        # Resolve pointer files written by the default ``append_sha`` versioning
        # mode so users can inspect via the recipe's output.path directly.
        data, _resolved = resolve_artifact_pointer(data, resolved_path, fs, read_cap)

        if len(data) > read_cap:
            raise ArtifactError(
                f"artifact size {len(data)} exceeds cap {read_cap}; refusing to load"
            )

        hdr = parse_header_from_bytes(data, parse_cap)
    except (MemoryError, RecursionError):
        raise
    except Exception as exc:
        code = _map_exception_to_exit(exc)
        _exit(code, f"Artifact parse failed: {exc}")

    _inspect_log = structlog.get_logger(__name__)
    signing_keys_raw = os.environ.get("RECOTEM_SIGNING_KEYS", "").strip()
    if signing_keys_raw and dev_allow_unsigned:
        # Keys are configured; --dev-allow-unsigned has no effect here.
        _inspect_log.warning(
            "dev_allow_unsigned_ignored",
            reason="RECOTEM_SIGNING_KEYS is set; --dev-allow-unsigned is ignored",
        )
    if not signing_keys_raw and dev_allow_unsigned:
        # Same in-memory dev key as run_training / serve dev mode.
        signing_keys_raw = "dev:" + ("0" * 64)
    if signing_keys_raw:
        try:
            from recotem.artifact.signing import KeyRing, verify_hmac

            key_ring = KeyRing(signing_keys_raw)
            payload_bytes = data[hdr.payload_offset :]
            verify_hmac(
                key_ring,
                hdr.kid,
                hdr.kid.encode("utf-8"),
                hdr.header_data,
                payload_bytes,
                hdr.hmac_digest,
            )
            typer.echo(f"HMAC: OK  (kid={hdr.kid!r})")
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            code = _map_exception_to_exit(exc)
            _exit(code, f"HMAC verification failed: {exc}")
    else:
        # Missing signing keys without --dev-allow-unsigned is a configuration
        # error (exit 8), not an artifact error (exit 5).  CLAUDE.md documents
        # this as _EXIT_CONFIG matching the train side.
        _exit(
            _EXIT_CONFIG,
            f"Cannot verify artifact (kid={hdr.kid!r}): RECOTEM_SIGNING_KEYS is "
            "not set and --dev-allow-unsigned was not passed.\n"
            "  - Set RECOTEM_SIGNING_KEYS=<kid>:<hex64> to verify the HMAC, or\n"
            "  - Pass --dev-allow-unsigned with RECOTEM_ENV=development to skip "
            "verification in a development environment.",
        )

    try:
        header_dict = json.loads(hdr.header_data.decode("utf-8"))
    except (MemoryError, RecursionError):
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        _exit(_EXIT_ARTIFACT, f"Header JSON parse failed: {exc}")

    typer.echo(json.dumps(header_dict, indent=2))


# ---------------------------------------------------------------------------
# recotem validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    recipe: Annotated[
        Path,
        typer.Argument(help="Path to the recipe YAML file.", exists=True),
    ],
) -> None:
    """Validate a recipe file and probe data-source connectivity."""
    _configure_logging_from_env()

    try:
        from recotem.recipe.loader import load_recipe

        loaded_recipe = load_recipe(recipe)
        typer.echo(f"Recipe '{loaded_recipe.name}': schema OK")
    except Exception as exc:
        code = _map_exception_to_exit(exc)
        _exit(code, f"Recipe validation failed: {exc}")

    try:
        from recotem.datasource.registry import get_source_class

        source_cfg = loaded_recipe.source
        type_name = getattr(source_cfg, "type", None)
        if type_name is None:
            raise RuntimeError("Recipe source is missing the 'type' discriminator.")

        source_cls = get_source_class(type_name)
        # Instantiate the source.  Plugins defer optional-dependency imports
        # to __init__, so this catches missing extras (e.g. google-cloud-bigquery)
        # and config / Config-class mismatches.  We do NOT call .fetch() here —
        # full data loads can be expensive (BigQuery scans, large CSV reads).
        source = source_cls(source_cfg)

        # Optional connectivity probe — plugin-authoring.md documents this hook.
        # Built-ins implement it; third-party plugins may opt in.
        probe = getattr(source, "probe", None)
        if callable(probe):
            probe()
            typer.echo(f"DataSource: probe OK ({type_name})")
        else:
            typer.echo(f"DataSource: extras OK ({type_name}, no probe defined)")
    except Exception as exc:
        code = _map_exception_to_exit(exc)
        _exit(code, f"DataSource probe failed: {exc}")

    typer.echo("Validation passed.")


# ---------------------------------------------------------------------------
# recotem schema
# ---------------------------------------------------------------------------


@app.command()
def schema() -> None:
    """Emit the JSON Schema for the Recipe model (for IDE integration).

    ``Recipe.source`` is declared as ``Any`` to break the circular import
    between recipe/models.py and datasource/registry.py, so a naive
    ``Recipe.model_json_schema()`` would emit an empty ``{"title": "Source"}``
    object — useless for IDE autocompletion.  This command rebuilds the
    schema against a runtime subclass whose ``source`` field is the
    discriminated union of every registered DataSource ``Config`` (see
    ``build_source_config_union()``), so CSV / Parquet / BigQuery /
    plugin-provided sources all appear with their full field definitions.
    """
    # Configure logging first so that ``datasource_plugin_registered`` debug
    # lines emitted during plugin discovery are filtered to INFO+ and do not
    # contaminate the JSON schema written to stdout.
    _configure_logging_from_env()

    try:
        from pydantic import create_model

        from recotem.datasource.registry import build_source_config_union
        from recotem.recipe.models import Recipe

        source_union = build_source_config_union()
        schema_recipe = create_model(
            "Recipe",
            __base__=Recipe,
            source=(source_union, ...),
        )
        schema_dict = schema_recipe.model_json_schema()
        typer.echo(json.dumps(schema_dict, indent=2))
    except Exception as exc:
        code = _map_exception_to_exit(exc)
        _exit(code, f"Schema generation failed: {exc}")


# ---------------------------------------------------------------------------
# recotem keygen
# ---------------------------------------------------------------------------


@app.command()
def keygen(
    kid: Annotated[
        str | None,
        typer.Option(
            "--kid", help="Key identifier (default: auto-generated UUID prefix)."
        ),
    ] = None,
    key_type: Annotated[
        str,
        typer.Option(
            "--type",
            help="Key type: 'signing' (HMAC key) or 'api' (API key).",
        ),
    ] = "signing",
) -> None:
    """Generate a signing or API key and print the (kid, plaintext, hash) triple.

    For signing keys:  plaintext is a 64-char hex string (32 raw bytes).
                       env_entry format: RECOTEM_SIGNING_KEYS=<kid>:<hex64>
    For API keys:      plaintext is a 43-char base64url string (32 raw bytes).
                       hash format: deterministic scrypt(N=2, r=8, p=1, dklen=32,
                       salt=b"recotem.api-key.v1") of the plaintext as
                       64-char hex.  The wire prefix remains ``sha256:``
                       — it identifies the digest family / 32-byte hex
                       digest, not the construction.
                       env_entry format: RECOTEM_API_KEYS=<kid>:sha256:<hex64>
    """
    if key_type not in ("signing", "api"):
        _exit(_EXIT_CONFIG, f"--type must be 'signing' or 'api', got {key_type!r}.")

    if kid is None:
        kid = str(uuid.uuid4())[:8]

    raw_bytes = os.urandom(32)

    if key_type == "signing":
        plaintext = raw_bytes.hex()
        # Fingerprint matches KeyRing.fingerprint semantics: sha256(key_bytes)[:8].
        # Use this value to correlate with the /security.posture log line — it is
        # NOT the value that goes into RECOTEM_SIGNING_KEYS (the raw hex is).
        fingerprint = hashlib.sha256(raw_bytes).hexdigest()[:8]
        typer.echo(f"kid={kid}")
        typer.echo(f"plaintext={plaintext}")
        typer.echo(
            f"fingerprint={fingerprint}  # matches /security.posture log; NOT for config"
        )
        typer.echo(f"env_entry=RECOTEM_SIGNING_KEYS={kid}:{plaintext}")
    else:
        from recotem.serving.auth import _hash_api_key  # noqa: PLC0415

        plaintext = base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")
        hex_hash = _hash_api_key(plaintext)
        typer.echo(f"kid={kid}")
        typer.echo(f"plaintext={plaintext}")
        typer.echo(f"hash=sha256:{hex_hash}")
        typer.echo(f"env_entry=RECOTEM_API_KEYS={kid}:sha256:{hex_hash}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _configure_logging_from_env() -> None:
    """Configure structlog from RECOTEM_LOG_FORMAT (best-effort).

    Failures are silenced so that a misconfigured logging backend never
    prevents the CLI from running.  ImportError / OSError (e.g. a missing
    optional structlog renderer dependency, or a read-only log file) are
    swallowed quietly.  Any other unexpected exception is printed to stderr
    as a one-liner so operators can diagnose it without a traceback wall.
    """
    import sys  # noqa: PLC0415

    try:
        try:
            from recotem.logging import configure_logging  # noqa: PLC0415

            fmt = os.environ.get("RECOTEM_LOG_FORMAT", "auto").strip().lower()
            configure_logging(fmt)
        except (ImportError, OSError):
            # Missing optional dependency or unreadable log sink — safe to ignore.
            pass
    except Exception as exc:  # noqa: BLE001
        # Unexpected failure: surface as a single stderr line so operators can
        # diagnose it, but do not abort the CLI invocation.
        print(
            f"[recotem] log config failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )


def _check_dev_env(flag: str) -> None:
    """Exit with EXIT_CONFIG (8) if RECOTEM_ENV is not 'development'.

    Per the documented exit-code table, environment-gated flag misuse is a
    configuration error (8), not a recipe error (2).  Sibling guards in the
    code-base for "signing keys missing without --dev-allow-unsigned" exit
    with the same code, so CronJob retry logic can branch on a single value.
    """
    env = os.environ.get("RECOTEM_ENV", "").strip().lower()
    if env != "development":
        _exit(
            _EXIT_CONFIG,
            f"{flag} requires RECOTEM_ENV=development. "
            f"Current RECOTEM_ENV={os.environ.get('RECOTEM_ENV', '')!r}.",
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
