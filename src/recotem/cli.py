"""Recotem 2.0 CLI --- Typer-based command interface.

Commands:
  train     Fetch data, tune hyperparameters, train, and sign an artifact.
  serve     Start the FastAPI prediction server with hot-swap.
  inspect   Read and verify an artifact header (no deserialization).
  validate  Validate a recipe file and probe data-source connectivity.
  schema    Emit the JSON Schema for the Recipe model.
  keygen    Generate a signing or API key (kid, plaintext, hash triple).

Exit codes (spec Section 6):
  0  success
  2  RecipeError
  3  DataSourceError
  4  TrainingError
  5  ArtifactError
  1  anything else
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Annotated

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
_EXIT_RECIPE = 2
_EXIT_DATASOURCE = 3
_EXIT_TRAINING = 4
_EXIT_ARTIFACT = 5
_EXIT_UNKNOWN = 1


def _exit(code: int, message: str | None = None) -> None:
    """Print *message* to stderr (if provided) and sys.exit with *code*."""
    if message:
        typer.echo(message, err=True)
    raise typer.Exit(code=code)


def _map_exception_to_exit(exc: Exception) -> int:
    """Map a known exception type to its canonical exit code."""
    try:
        from recotem.recipe.errors import RecipeError as _RecipeError

        if isinstance(exc, _RecipeError):
            return _EXIT_RECIPE
    except ImportError:
        pass

    try:
        from recotem.datasource.base import DataSourceError as _DataSourceError

        if isinstance(exc, _DataSourceError):
            return _EXIT_DATASOURCE
    except ImportError:
        pass

    try:
        from recotem.artifact.format import ArtifactError as _ArtifactError

        if isinstance(exc, _ArtifactError):
            return _EXIT_ARTIFACT
    except ImportError:
        pass

    try:
        from recotem.training.pipeline import (
            TrainingError as _TrainingError,  # type: ignore[import-untyped]
        )

        if isinstance(exc, _TrainingError):
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

    try:
        from recotem.training.pipeline import (
            run_training,  # type: ignore[import-untyped]
        )

        run_training(
            loaded_recipe,
            no_lock=no_lock,
            fail_on_busy=fail_on_busy,
            quiet=quiet,
            verbose=verbose,
            dev_allow_unsigned=dev_allow_unsigned,
        )
    except SystemExit as exc:
        raise typer.Exit(code=exc.code or 0) from exc
    except Exception as exc:
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
        from recotem.config import ServeConfig

        cfg = ServeConfig.from_env()
    except ValueError as exc:
        _exit(_EXIT_RECIPE, f"Configuration error: {exc}")

    if host is not None:
        cfg.host = host
    if port is not None:
        cfg.port = port
    cfg.insecure_no_auth = insecure_no_auth
    cfg.dev_allow_unsigned = dev_allow_unsigned
    cfg.recipes_dir = str(recipes.resolve())  # type: ignore[attr-defined]

    try:
        from recotem.serving.app import create_app

        fastapi_app = create_app(cfg)
    except Exception as exc:
        code = _map_exception_to_exit(exc)
        _exit(code, f"Server startup failed: {exc}")

    import uvicorn

    uvicorn.run(
        fastapi_app,
        host=cfg.host,
        port=cfg.port,
        timeout_graceful_shutdown=cfg.drain_seconds,
        log_config=None,
    )


# ---------------------------------------------------------------------------
# recotem inspect
# ---------------------------------------------------------------------------


@app.command()
def inspect(
    artifact: Annotated[
        Path,
        typer.Argument(help="Path to the .recotem artifact file.", exists=True),
    ],
) -> None:
    """Read and verify an artifact header without deserializing the payload.

    Reads the structural fields and HMAC, verifies against RECOTEM_SIGNING_KEYS,
    and prints the header JSON.  Never invokes the unpickler.
    """
    _configure_logging_from_env()

    try:
        data = artifact.read_bytes()
    except OSError as exc:
        _exit(_EXIT_ARTIFACT, f"Cannot read artifact '{artifact}': {exc}")

    try:
        from recotem.artifact.format import (
            DEFAULT_MAX_PAYLOAD_BYTES,
            parse_header_from_bytes,
        )

        hdr = parse_header_from_bytes(data, DEFAULT_MAX_PAYLOAD_BYTES)
    except Exception as exc:
        _exit(_EXIT_ARTIFACT, f"Artifact parse failed: {exc}")

    signing_keys_raw = os.environ.get("RECOTEM_SIGNING_KEYS", "").strip()
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
        typer.echo(
            f"HMAC: SKIPPED (RECOTEM_SIGNING_KEYS not set)  kid={hdr.kid!r}",
            err=True,
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
        from recotem.datasource.registry import (
            get_source_for_recipe,  # type: ignore[import-untyped]
        )

        get_source_for_recipe(loaded_recipe)
        typer.echo("DataSource: probe OK")
    except ImportError:
        typer.echo("DataSource: skipped (registry not available)", err=True)
    except Exception as exc:
        code = _map_exception_to_exit(exc)
        _exit(code, f"DataSource probe failed: {exc}")

    typer.echo("Validation passed.")


# ---------------------------------------------------------------------------
# recotem schema
# ---------------------------------------------------------------------------


@app.command()
def schema() -> None:
    """Emit the JSON Schema for the Recipe model (for IDE integration)."""
    try:
        from recotem.recipe.models import Recipe

        schema_dict = Recipe.model_json_schema()
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
        typer.Option("--kid", help="Key identifier (default: auto-generated UUID prefix)."),
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
                       hash format: sha256(<plaintext_utf8>) as 64-char hex.
                       env_entry format: RECOTEM_API_KEYS=<kid>:sha256:<hex64>
    """
    if key_type not in ("signing", "api"):
        _exit(_EXIT_UNKNOWN, f"--type must be 'signing' or 'api', got {key_type!r}.")

    if kid is None:
        import uuid

        kid = str(uuid.uuid4())[:8]

    raw_bytes = os.urandom(32)

    if key_type == "signing":
        plaintext = raw_bytes.hex()
        hex_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        typer.echo(f"kid={kid}")
        typer.echo(f"plaintext={plaintext}")
        typer.echo(f"hash=sha256:{hex_hash}")
        typer.echo(f"env_entry=RECOTEM_SIGNING_KEYS={kid}:{plaintext}")
    else:
        plaintext = base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")
        hex_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        typer.echo(f"kid={kid}")
        typer.echo(f"plaintext={plaintext}")
        typer.echo(f"hash=sha256:{hex_hash}")
        typer.echo(f"env_entry=RECOTEM_API_KEYS={kid}:sha256:{hex_hash}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _configure_logging_from_env() -> None:
    """Configure structlog from RECOTEM_LOG_FORMAT (best-effort)."""
    try:
        from recotem.logging import configure_logging

        fmt = os.environ.get("RECOTEM_LOG_FORMAT", "auto").strip().lower()
        configure_logging(fmt)
    except Exception:
        pass


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
