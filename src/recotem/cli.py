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

app = typer.Typer(
    name="recotem",
    help="Recipe-driven recommender training and serving.",
    add_completion=False,
)


# ---------------------------------------------------------------------------
# Exit-code helpers
# ---------------------------------------------------------------------------

_EXIT_SUCCESS = 0
_EXIT_UNKNOWN = 1
_EXIT_RECIPE = 2
_EXIT_DATASOURCE = 3
_EXIT_TRAINING = 4
_EXIT_ARTIFACT = 5
_EXIT_LOCK_CONTESTED = 6
_EXIT_HTTP_FETCH = 7
_EXIT_CONFIG = 8


def _exit(code: int, message: str | None = None) -> None:
    """Print *message* to stderr (if provided) and sys.exit with *code*."""
    if message:
        typer.echo(message, err=True)
    raise typer.Exit(code=code)


def _map_exception_to_exit(exc: Exception) -> int:
    """Map a known exception type to its canonical exit code.

    Checked in priority order so that the most-specific mapping wins when
    exception hierarchies overlap (e.g. a subclass of TrainingError that
    signals a configuration problem should map to _EXIT_CONFIG).
    """
    # --- configuration errors (missing signing keys, bad env) ---
    try:
        from recotem.training.errors import TrainingError as _TrainingError

        if isinstance(exc, _TrainingError) and getattr(exc, "code", "") in (
            "signing_key_missing",
        ):
            return _EXIT_CONFIG
    except (ImportError, AttributeError):
        pass

    # --- recipe errors ---
    try:
        from recotem.recipe.errors import RecipeError as _RecipeError

        if isinstance(exc, _RecipeError):
            return _EXIT_RECIPE
    except ImportError:
        pass

    # --- HTTP fetch errors (checked BEFORE DataSourceError so that a
    # DataSourceError wrapping an HttpFetchError still maps to exit 7).
    # CronJob retry semantics distinguish transient HTTP/SSRF failures (7)
    # from structural data-source failures (3).
    try:
        from recotem._http_fetch import HttpFetchError as _HttpFetchError

        # Walk the __cause__ chain — datasource layers wrap HttpFetchError
        # into DataSourceError via ``raise DataSourceError(...) from exc``.
        cur: BaseException | None = exc
        while cur is not None:
            if isinstance(cur, _HttpFetchError):
                return _EXIT_HTTP_FETCH
            cur = cur.__cause__
    except (ImportError, AttributeError):
        pass

    # --- datasource errors ---
    try:
        from recotem.datasource.base import DataSourceError as _DataSourceError

        if isinstance(exc, _DataSourceError):
            return _EXIT_DATASOURCE
    except ImportError:
        pass

    # --- config errors (must come before ArtifactError so that a signing-key
    # misconfiguration on the serve path exits 8, not 5)
    try:
        from recotem.config import ConfigError as _ConfigError

        if isinstance(exc, _ConfigError):
            return _EXIT_CONFIG
    except ImportError:
        pass

    # --- artifact errors ---
    try:
        from recotem.artifact.format import ArtifactError as _ArtifactError

        if isinstance(exc, _ArtifactError):
            return _EXIT_ARTIFACT
    except ImportError:
        pass

    # --- lock contested ---
    try:
        from recotem.training.lock import LockContestedError as _LockContestedError

        if isinstance(exc, _LockContestedError):
            return _EXIT_LOCK_CONTESTED
    except (ImportError, AttributeError):
        pass

    # --- general training errors ---
    try:
        from recotem.training.errors import TrainingError as _TrainingError2

        if isinstance(exc, _TrainingError2):
            return _EXIT_TRAINING
    except (ImportError, AttributeError):
        pass

    return _EXIT_UNKNOWN


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
        _exit(
            _EXIT_RECIPE,
            "--dev-allow-unsigned requires "
            "--i-understand-this-loads-arbitrary-code to also be passed.",
        )
    if dev_allow_unsigned:
        _check_dev_env("--dev-allow-unsigned")

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
        _exit(
            _EXIT_RECIPE,
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
    except OSError as exc:
        _srv_log.error(
            "serve_startup_failed",
            error=str(exc),
            host=cfg.host,
            port=cfg.port,
        )
        _exit(_EXIT_CONFIG, f"Server bind/startup failed: {exc}")
    except Exception as exc:
        _srv_log.error("serve_startup_failed", error=str(exc))
        code = _map_exception_to_exit(exc)
        _exit(code, f"Server error: {exc}")


# ---------------------------------------------------------------------------
# recotem inspect
# ---------------------------------------------------------------------------


@app.command()
def inspect(
    artifact: Annotated[
        Path,
        # exists=True is intentionally omitted: read_artifact_header / fsspec
        # support remote schemes (s3://, gs://, etc.) that Typer cannot stat
        # locally.  Missing-path errors are surfaced by the fsspec.open() call
        # inside this function and reported as exit 5 (ArtifactError).
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

    import fsspec

    from recotem.artifact.format import (
        ArtifactError,
        parse_header_from_bytes,
    )
    from recotem.artifact.io import resolve_artifact_pointer
    from recotem.config import ServeConfig

    # Honor RECOTEM_MAX_PAYLOAD_BYTES so inspect uses the same payload cap as
    # the serving layer.  ServeConfig.from_env() reads only env vars — it does
    # not require signing keys to be present (those are validated later in this
    # function by the explicit RECOTEM_SIGNING_KEYS check below).
    max_bytes = ServeConfig.from_env().max_payload_bytes
    try:
        # Bounded read so a 100 GiB file cannot OOM the CLI before the cap
        # check fires; matches the serving-watcher protocol.
        fs, resolved_path = fsspec.core.url_to_fs(str(artifact))
        with fs.open(resolved_path, "rb") as fh:
            data = fh.read(max_bytes + 1)
    except OSError as exc:
        _exit(_EXIT_ARTIFACT, f"Cannot read artifact '{artifact}': {exc}")

    try:
        # Resolve pointer files written by the default ``append_sha`` versioning
        # mode so users can inspect via the recipe's output.path directly.
        data, _resolved = resolve_artifact_pointer(data, resolved_path, fs, max_bytes)

        if len(data) > max_bytes:
            raise ArtifactError(
                f"artifact size {len(data)} exceeds cap {max_bytes}; refusing to load"
            )

        hdr = parse_header_from_bytes(data, max_bytes)
    except Exception as exc:
        _exit(_EXIT_ARTIFACT, f"Artifact parse failed: {exc}")

    if dev_allow_unsigned:
        # Gate dev-only fallback behind RECOTEM_ENV=development, mirroring
        # train and serve.  Otherwise an operator who runs
        # ``recotem inspect --dev-allow-unsigned`` against a production
        # artifact would silently fall back to a deterministic public key.
        _check_dev_env("--dev-allow-unsigned")

    signing_keys_raw = os.environ.get("RECOTEM_SIGNING_KEYS", "").strip()
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
        except Exception as exc:
            _exit(_EXIT_ARTIFACT, f"HMAC verification failed: {exc}")
    else:
        _exit(
            _EXIT_ARTIFACT,
            f"Cannot verify artifact (kid={hdr.kid!r}): RECOTEM_SIGNING_KEYS is "
            "not set and --dev-allow-unsigned was not passed.\n"
            "  - Set RECOTEM_SIGNING_KEYS=<kid>:<hex64> to verify the HMAC, or\n"
            "  - Pass --dev-allow-unsigned with RECOTEM_ENV=development to skip "
            "verification in a development environment.",
        )

    try:
        header_dict = json.loads(hdr.header_data.decode("utf-8"))
    except Exception as exc:
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
        _exit(_EXIT_UNKNOWN, f"Schema generation failed: {exc}")


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
        _exit(_EXIT_UNKNOWN, f"--type must be 'signing' or 'api', got {key_type!r}.")

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
    """Exit with code 2 if RECOTEM_ENV is not 'development'."""
    env = os.environ.get("RECOTEM_ENV", "").strip().lower()
    if env != "development":
        _exit(
            _EXIT_RECIPE,
            f"{flag} requires RECOTEM_ENV=development. "
            f"Current RECOTEM_ENV={os.environ.get('RECOTEM_ENV', '')!r}.",
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
