"""YAML → Recipe loader with restricted env expansion and path security."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

import pydantic
import structlog
import yaml

from recotem.datasource.base import DataSourceError
from recotem.recipe.envvars import expand_env_vars
from recotem.recipe.errors import RecipeError
from recotem.recipe.models import Recipe

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Path-scheme policy
# ---------------------------------------------------------------------------

# Allow-list of write-supported schemes for output.path (see docs/recipe-reference.md).
# Using an allow-list instead of a deny-list means unknown and novel schemes
# (e.g. data:, javascript:, vendor-specific) are rejected by default rather
# than admitted by oversight.
#
# Entries:
#   ""       — bare local path (no scheme prefix), e.g. /tmp/out.recotem
#   "file"   — file:// URI pointing at a local absolute path
#   "s3"     — Amazon S3 (fsspec s3fs)
#   "gs"     — Google Cloud Storage (fsspec gcsfs)
#   "az"     — Azure Blob Storage (fsspec adlfs)
#   "abfs"   — Azure Data Lake Storage Gen2 (fsspec adlfs)
#   "abfss"  — Azure Data Lake Storage Gen2 over TLS (fsspec adlfs)
_OUTPUT_ALLOWED_SCHEMES: frozenset[str] = frozenset(
    {"", "file", "s3", "gs", "az", "abfs", "abfss"}
)

# Allow-list of supported schemes for input paths (source.path, item_metadata.path).
# Compared to output, http/https are permitted (they go through SSRF-guarded fetch).
# All other novel or vendor-specific schemes are rejected by default.
#
# Entries:
#   ""       — bare local path
#   "file"   — file:// URI
#   "s3"     — Amazon S3
#   "gs"     — Google Cloud Storage
#   "az"     — Azure Blob Storage
#   "abfs"   — Azure Data Lake Storage Gen2
#   "abfss"  — Azure Data Lake Storage Gen2 over TLS
#   "http"   — HTTP (SSRF-guarded; sha256 required separately)
#   "https"  — HTTPS (SSRF-guarded; sha256 required separately)
_INPUT_ALLOWED_SCHEMES: frozenset[str] = frozenset(
    {"", "file", "s3", "gs", "az", "abfs", "abfss", "http", "https"}
)

# Schemes that involve an unauthenticated network fetch. Used by the
# sha256-required post-validator (added in Task 4).
_NETWORK_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def _network_scheme(path: str) -> bool:
    """True iff *path* uses a scheme in `_NETWORK_SCHEMES`."""
    return urlparse(path).scheme.lower() in _NETWORK_SCHEMES


# Schemes for which a `user:pass@host` component is treated as embedded
# credentials and must be rejected.  Object-store schemes (s3, gs, az, abfs,
# abfss) use `@` in their canonical addressing syntax (e.g. GCS
# `gs://project@bucket/key`) and must NOT be subject to userinfo checks.
# Aligned with `_USERINFO_SCHEMES` in `_http_fetch.py`.
_USERINFO_REJECT_SCHEMES: frozenset[str] = frozenset({"http", "https", "ftp", "ftps"})


def _check_userinfo(path: str, field_name: str) -> None:
    parsed = urlparse(path)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _USERINFO_REJECT_SCHEMES:
        # Object-store and bare paths: `@` may be part of the addressing
        # syntax (e.g. `gs://project@bucket/key`); skip the credentials check.
        return
    if parsed.username or parsed.password:
        raise RecipeError(
            f"'{field_name}' contains embedded credentials in the URI. "
            "Use environment-based authentication instead."
        )


def _validate_input_path(path: str, field_name: str) -> None:
    """Validate an input-side path (source.path, item_metadata.path).

    Enforces an allow-list of permitted schemes and rejects chained-scheme
    paths (e.g. ``simplecache::https://…``) which cannot be safely validated
    via ``urlparse``.
    """
    # Chained-scheme paths (fsspec protocol chaining syntax) are rejected
    # because urlparse cannot extract the effective scheme from them and they
    # may reference transient caching protocols wrapping disallowed backends.
    if "::" in path:
        raise RecipeError(
            f"'{field_name}' uses a chained scheme (contains '::'). "
            "Chained fsspec protocols are not permitted in recipes."
        )

    _check_userinfo(path, field_name)

    parsed = urlparse(path)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _INPUT_ALLOWED_SCHEMES:
        allowed_display = ", ".join(
            sorted(f"{s}://" if s else "(bare path)" for s in _INPUT_ALLOWED_SCHEMES)
        )
        raise RecipeError(
            f"'{field_name}' uses scheme '{scheme}://' which is not supported "
            f"for input paths. Allowed: {allowed_display}"
        )


def _validate_output_path(path: str, field_name: str) -> None:
    """Validate an output-side path (output.path).

    Uses an allow-list of write-supported schemes so that novel or unknown
    schemes (e.g. ``data:``, ``javascript:``, vendor-specific) are rejected
    by default rather than admitted by oversight of a deny-list.
    """
    _check_userinfo(path, field_name)
    parsed = urlparse(path)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _OUTPUT_ALLOWED_SCHEMES:
        allowed_display = ", ".join(
            sorted(f"{s}://" if s else "(bare path)" for s in _OUTPUT_ALLOWED_SCHEMES)
        )
        raise RecipeError(
            f"'{field_name}' uses scheme '{scheme}://' which is not supported. "
            f"Allowed: {allowed_display}"
        )
    if scheme == "file" and parsed.netloc not in ("", "localhost"):
        raise RecipeError(
            f"'{field_name}' uses 'file://{parsed.netloc}/...' which is "
            "ambiguous as a local path. Use 'file:///absolute/path' "
            "(empty host) instead."
        )


def _local_output_path(output_path: str) -> Path | None:
    """Return the local Path for a local-write output, else None.

    Handles bare absolute paths and ``file:///abs/path`` URIs.
    """
    parsed = urlparse(output_path)
    scheme = (parsed.scheme or "").lower()
    if scheme == "":
        return Path(output_path)
    if scheme == "file":
        return Path(url2pathname(parsed.path))
    return None


def _check_local_output_containment(local_path: Path) -> None:
    """If RECOTEM_ARTIFACT_ROOT is set, assert resolved path lies under it.

    ``Path.resolve(strict=False)`` on a non-existent file does not follow
    symlinks in the *parent* directory; an attacker who drops a symlink under
    the artifact root could therefore escape containment on the first write.
    This function explicitly resolves the *parent* with ``strict=True`` (the
    parent must already exist, or the recipe is rejected) and asserts that the
    resolved parent also lies inside the artifact root before the final
    component is appended.
    """
    artifact_root_env = os.environ.get("RECOTEM_ARTIFACT_ROOT", "")
    if not artifact_root_env:
        return

    artifact_root = Path(artifact_root_env).resolve()

    # --- resolve the parent directory with strict=True so that any symlink
    # in the parent chain is followed to its real destination before the
    # containment check.  The output file itself need not exist yet.
    parent = local_path.parent
    try:
        resolved_parent = parent.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise RecipeError(
            f"output.path parent directory '{parent}' does not exist or is not "
            "a directory. Create it before running training."
        ) from exc
    except OSError as exc:
        raise RecipeError(
            f"output.path parent directory '{parent}' could not be resolved: {exc}"
        ) from exc

    # Check the resolved parent is inside the artifact root.
    try:
        resolved_parent.relative_to(artifact_root)
    except ValueError:
        raise RecipeError(
            f"output.path parent '{resolved_parent}' is outside "
            f"RECOTEM_ARTIFACT_ROOT='{artifact_root}'. "
            "Symlink escapes are rejected."
        ) from None

    # Compose the final path from the already-resolved parent plus the
    # terminal filename component.  This avoids relying on a non-strict
    # resolve() of the full path, which does NOT follow nonexistent terminal
    # symlinks and could therefore miss an escape when the output file does
    # not yet exist but its name is a symlink that resolves outside the root.
    # By appending only the plain filename to the strictly-resolved parent we
    # guarantee no additional symlink traversal can occur.
    resolved = resolved_parent / local_path.name
    try:
        resolved.relative_to(artifact_root)
    except ValueError:
        raise RecipeError(
            f"output.path resolves to '{resolved}' which is outside "
            f"RECOTEM_ARTIFACT_ROOT='{artifact_root}'. "
            "Symlink escapes are rejected."
        ) from None


def _check_recipe_file_containment(recipe_path: Path, recipes_root: Path) -> None:
    """Assert recipe_path remains inside recipes_root after realpath resolution."""
    resolved_recipe = recipe_path.resolve()
    resolved_root = recipes_root.resolve()
    try:
        resolved_recipe.relative_to(resolved_root)
    except ValueError:
        raise RecipeError(
            f"Recipe file '{resolved_recipe}' lies outside the recipes "
            f"root '{resolved_root}'. Path traversal is rejected."
        ) from None


# ---------------------------------------------------------------------------
# Env expansion walker
# ---------------------------------------------------------------------------

# Fields that must never have env expansion applied (query / query_parameters
# on any source dict).  This global set is kept as a baseline; plugin-declared
# ``no_expand_fields`` are merged in during source-node expansion.
_NO_EXPAND_KEYS: frozenset[str] = frozenset({"query", "query_parameters"})


def _expand_node(
    node: Any,
    *,
    extra_allowed: dict[str, str] | None,
    _in_no_expand: bool = False,
    _extra_no_expand: frozenset[str] = frozenset(),
) -> Any:
    """Recursively walk a parsed YAML node and expand env-var references.

    Expansion is skipped entirely inside ``query`` and ``query_parameters``
    keys at any nesting level, plus any keys listed in *_extra_no_expand*
    (populated from the source plugin's ``no_expand_fields`` class variable
    when processing the ``source`` subtree).
    """
    if isinstance(node, str):
        if _in_no_expand:
            return node
        return expand_env_vars(node, extra_allowed=extra_allowed)
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        # Normalise to lowercase so a plugin that declares 'SQL' and a YAML
        # author who writes 'sql:' are both protected — case mismatch must not
        # silently bypass env-expansion blocking (injection defence-in-depth).
        combined_no_expand = frozenset(
            k.lower() for k in (_NO_EXPAND_KEYS | _extra_no_expand)
        )
        for k, v in node.items():
            in_no_expand = _in_no_expand or (k.lower() in combined_no_expand)
            result[k] = _expand_node(
                v,
                extra_allowed=extra_allowed,
                _in_no_expand=in_no_expand,
                _extra_no_expand=_extra_no_expand,
            )
        return result
    if isinstance(node, list):
        return [
            _expand_node(
                item,
                extra_allowed=extra_allowed,
                _in_no_expand=_in_no_expand,
                _extra_no_expand=_extra_no_expand,
            )
            for item in node
        ]
    return node


def _expand_with_source_no_expand(
    raw_data: dict[str, Any],
    *,
    extra_allowed: dict[str, str] | None,
) -> dict[str, Any]:
    """Expand env-var references in *raw_data*, honouring plugin ``no_expand_fields``.

    The ``source`` subtree is expanded separately so that the source plugin's
    ``no_expand_fields`` class attribute can be consulted before the recursive
    walk descends into source-specific fields.

    All other top-level keys are expanded with no additional restrictions
    beyond the global ``_NO_EXPAND_KEYS``.
    """
    result: dict[str, Any] = {}
    for key, value in raw_data.items():
        if key == "source" and isinstance(value, dict):
            # Determine extra protected fields from the plugin's class var.
            extra_no_expand: frozenset[str] = frozenset()
            type_name = value.get("type")
            if type_name:
                try:
                    from recotem.datasource.registry import get_source_class

                    src_cls = get_source_class(str(type_name))
                    # Normalise to lowercase so that a plugin declaring
                    # 'SQL' and a YAML key 'sql:' are both blocked —
                    # matching is case-insensitive (see _expand_node).
                    extra_no_expand = frozenset(
                        f.lower()
                        for f in getattr(src_cls, "no_expand_fields", frozenset())
                    )
                except DataSourceError:
                    # Unknown source type — later validation surfaces the real
                    # error; fall back to the global _NO_EXPAND_KEYS only.
                    pass
                except Exception as exc:
                    logger.warning(
                        "source_class_lookup_failed_during_expand",
                        type=type_name,
                        error_class=type(exc).__name__,
                    )
                    raise RecipeError(
                        f"Failed to resolve source plugin {type_name!r} "
                        f"during recipe load: {exc}"
                    ) from exc
            result[key] = _expand_node(
                value,
                extra_allowed=extra_allowed,
                _extra_no_expand=extra_no_expand,
            )
        else:
            result[key] = _expand_node(value, extra_allowed=extra_allowed)
    return result


# ---------------------------------------------------------------------------
# Pydantic validation error formatting
# ---------------------------------------------------------------------------


def _format_pydantic_errors(exc: pydantic.ValidationError) -> str:
    """Format pydantic v2 ValidationError into a human-readable multi-line message.

    Each error line contains the dotted field path and the error message, e.g.::

        - training.n_trials: Input should be greater than or equal to 1
        - output.path: Field required
    """
    lines: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error.get("loc", ()))
        msg = error.get("msg", "")
        lines.append(f"  - {loc}: {msg}" if loc else f"  - {msg}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Line-number extraction from YAML parse errors
# ---------------------------------------------------------------------------


def _line_from_exc(exc: Exception) -> int | None:
    """Try to extract a 1-based line number from a yaml exception."""
    mark = getattr(exc, "problem_mark", None)
    if mark is not None:
        return mark.line + 1  # yaml uses 0-based lines
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_recipe(
    path: str | Path,
    *,
    extra_allowed: dict[str, str] | None = None,
    recipes_root: Path | None = None,
) -> Recipe:
    """Load and validate a single recipe YAML file.

    Parameters
    ----------
    path:
        Absolute or relative path to the ``.yaml`` file.
    extra_allowed:
        Extra name→value pairs (from ``--env-var KEY=...``) merged on top of
        ``os.environ`` for expansion.  Must still pass the allow/blacklist check.
    recipes_root:
        If supplied, the recipe file must resolve to inside this directory.

    Returns
    -------
    Recipe
        Validated recipe instance.

    Raises
    ------
    RecipeError
        On any YAML, schema, security, or env-expansion error.
    """
    p = Path(path)

    # Containment check when loading from a directory.
    if recipes_root is not None:
        _check_recipe_file_containment(p, recipes_root)

    try:
        raw_text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecipeError(f"Cannot read recipe file '{p}': {exc}") from exc

    # Parse YAML into a raw dict (no env expansion yet).
    try:
        raw_data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        line = _line_from_exc(exc)
        raise RecipeError(
            f"YAML parse error in '{p}': {exc}",
            line=line,
        ) from exc

    if not isinstance(raw_data, dict):
        raise RecipeError(f"Recipe '{p}' must be a YAML mapping at the top level.")

    # Env expansion (never touches query / query_parameters, and honours
    # plugin-declared no_expand_fields for the source subtree).
    try:
        expanded = _expand_with_source_no_expand(raw_data, extra_allowed=extra_allowed)
    except RecipeError:
        raise
    except (MemoryError, RecursionError):
        raise
    except Exception as exc:
        raise RecipeError(
            f"Unexpected error during env-var expansion in '{p}': {exc}"
        ) from exc

    # Extract + validate name first (needed for path checks below).
    raw_name = expanded.get("name")
    if not raw_name:
        raise RecipeError(f"Recipe '{p}' is missing the required 'name' field.")

    _name_re = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
    if not _name_re.match(str(raw_name)):
        raise RecipeError(
            f"Recipe name {raw_name!r} must match ^[A-Za-z0-9_-]{{1,64}}$."
        )

    # Path security checks.
    _validate_path_fields(expanded)

    # Resolve the typed DataSource Config BEFORE building the Recipe so that
    # pydantic's extra="forbid" is enforced on the source and the Recipe is
    # constructed once with the typed source in place (no object.__setattr__
    # bypass of re-validation).
    raw_source = expanded.get("source")
    if isinstance(raw_source, dict):
        try:
            from recotem.datasource.registry import get_source_class

            type_name = raw_source.get("type")
            if not type_name:
                raise RecipeError(
                    f"Recipe '{p}' source is missing the 'type' discriminator."
                )
            source_cls = get_source_class(str(type_name))
            config_cls = source_cls.Config
            try:
                typed_source = config_cls.model_validate(raw_source)
            except pydantic.ValidationError as exc:
                detail = _format_pydantic_errors(exc)
                raise RecipeError(
                    f"Recipe '{p}' source failed validation:\n{detail}"
                ) from exc
            except RecipeError:
                raise
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                raise RecipeError(
                    f"Recipe '{p}' source failed validation: {exc}"
                ) from exc
        except RecipeError:
            raise
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            raise RecipeError(f"Recipe '{p}' source resolution failed: {exc}") from exc
        # Replace the raw dict with the validated typed config before passing
        # to Recipe.model_validate — this prevents object.__setattr__ bypass.
        expanded = {**expanded, "source": typed_source}

    # Build Recipe via pydantic (validates all sub-schemas).
    try:
        recipe = Recipe.model_validate(expanded)
    except pydantic.ValidationError as exc:
        # Format structured field errors so users know exactly which fields failed.
        detail = _format_pydantic_errors(exc)
        raise RecipeError(f"Recipe '{p}' failed validation:\n{detail}") from exc
    except RecipeError:
        raise
    except (MemoryError, RecursionError):
        raise
    except Exception as exc:
        logger.error(
            "unexpected_validation_error",
            recipe=str(p),
            exc_type=type(exc).__name__,
            exc=str(exc),
        )
        raise RecipeError(
            f"Recipe '{p}' failed validation (unexpected error): {exc}"
        ) from exc

    # Enforce sha256 integrity pin for network-scheme paths.
    _enforce_sha256_for_network_paths(recipe)

    # Local output path containment check (covers bare paths and file:// URIs).
    local_output = _local_output_path(recipe.output.path)
    if local_output is not None:
        _check_local_output_containment(local_output)

    logger.info("recipe_loaded", name=recipe.name, path=str(p))
    return recipe


def _enforce_sha256_for_network_paths(recipe: Recipe) -> None:
    """For source / item_metadata paths using a network scheme, require sha256.

    Raises
    ------
    RecipeError
        If a network-scheme path is missing the integrity pin.
    """
    src = recipe.source
    src_path = getattr(src, "path", None)
    if (
        isinstance(src_path, str)
        and _network_scheme(src_path)
        and not getattr(src, "sha256", None)
    ):
        raise RecipeError(
            f"'source.path' uses a network scheme "
            f"({urlparse(src_path).scheme}://) and requires a 'sha256' "
            "integrity pin. Compute it with `shasum -a 256 <file>` and "
            "set `source.sha256: <hex>`."
        )

    meta = recipe.item_metadata
    if meta is not None:
        meta_path = getattr(meta, "path", None)
        if (
            isinstance(meta_path, str)
            and _network_scheme(meta_path)
            and not getattr(meta, "sha256", None)
        ):
            raise RecipeError(
                f"'item_metadata.path' uses a network scheme "
                f"({urlparse(meta_path).scheme}://) and requires a "
                "'sha256' integrity pin."
            )


def _validate_path_fields(data: dict[str, Any]) -> None:
    """Validate scheme + credentials for all path fields in the raw dict."""
    output = data.get("output")
    if isinstance(output, dict):
        output_path = output.get("path")
        if isinstance(output_path, str):
            _validate_output_path(output_path, "output.path")

    source = data.get("source")
    if isinstance(source, dict):
        source_path = source.get("path")
        if isinstance(source_path, str):
            _validate_input_path(source_path, "source.path")

    item_metadata = data.get("item_metadata")
    if isinstance(item_metadata, dict):
        meta_path = item_metadata.get("path")
        if isinstance(meta_path, str):
            _validate_input_path(meta_path, "item_metadata.path")


def load_recipes_directory(
    path: str | Path,
    *,
    extra_allowed: dict[str, str] | None = None,
) -> list[Recipe]:
    """Load all ``*.yaml`` files directly under *path* as recipes.

    Returns
    -------
    list[Recipe]
        Recipes sorted by filename (deterministic order).

    Raises
    ------
    RecipeError
        On duplicate ``name`` values, containment violation, or any per-file
        error.
    """
    root = Path(path).resolve()

    if not root.is_dir():
        raise RecipeError(
            f"Recipes directory '{root}' does not exist or is not a directory."
        )

    # Non-recursive: only direct children ending in .yaml
    yaml_files = sorted(
        f for f in root.iterdir() if f.is_file() and f.suffix == ".yaml"
    )

    recipes: list[Recipe] = []
    names_seen: dict[str, str] = {}  # name → filename

    for yaml_file in yaml_files:
        recipe = load_recipe(
            yaml_file,
            extra_allowed=extra_allowed,
            recipes_root=root,
        )
        if recipe.name in names_seen:
            raise RecipeError(
                f"Duplicate recipe name '{recipe.name}' found in "
                f"'{yaml_file.name}' and '{names_seen[recipe.name]}'. "
                "Each recipe in a directory must have a unique name."
            )
        names_seen[recipe.name] = yaml_file.name
        recipes.append(recipe)

    logger.info("recipes_directory_loaded", path=str(root), count=len(recipes))
    return recipes


def load_recipes_directory_lenient(
    path: str | Path,
    *,
    extra_allowed: dict[str, str] | None = None,
) -> list[tuple[Path, Recipe | None, Exception | None]]:
    """Load all ``*.yaml`` files directly under *path*, returning per-file results.

    Unlike :func:`load_recipes_directory`, this variant never raises on a
    per-file parse/validation error.  Instead it returns a result triple for
    every YAML file found, so callers (e.g. a health endpoint) can surface
    failed-load entries without aborting the entire batch.

    Returns
    -------
    list[tuple[Path, Recipe | None, Exception | None]]
        Sorted by filename.  On success: ``(path, recipe, None)``.
        On failure: ``(path, None, exc)``.

    Raises
    ------
    RecipeError
        Only for directory-level errors (path does not exist / not a dir) or
        duplicate recipe names across successfully-loaded files.
    """
    root = Path(path).resolve()

    if not root.is_dir():
        raise RecipeError(
            f"Recipes directory '{root}' does not exist or is not a directory."
        )

    yaml_files = sorted(
        f for f in root.iterdir() if f.is_file() and f.suffix == ".yaml"
    )

    results: list[tuple[Path, Recipe | None, Exception | None]] = []
    names_seen: dict[str, str] = {}  # name → filename

    ok_count = 0
    err_count = 0
    for yaml_file in yaml_files:
        try:
            recipe = load_recipe(
                yaml_file,
                extra_allowed=extra_allowed,
                recipes_root=root,
            )
        except Exception as exc:
            logger.warning(
                "recipe_load_error_skipped",
                file=yaml_file.name,
                error_class=type(exc).__name__,
                error=str(exc),
            )
            results.append((yaml_file, None, exc))
            err_count += 1
            continue

        if recipe.name in names_seen:
            dup_err = RecipeError(
                f"Duplicate recipe name '{recipe.name}' found in "
                f"'{yaml_file.name}' and '{names_seen[recipe.name]}'. "
                "Each recipe in a directory must have a unique name."
            )
            logger.warning(
                "recipe_duplicate_name_skipped",
                file=yaml_file.name,
                name=recipe.name,
            )
            results.append((yaml_file, None, dup_err))
            err_count += 1
            continue

        names_seen[recipe.name] = yaml_file.name
        results.append((yaml_file, recipe, None))
        ok_count += 1

    logger.info(
        "recipes_directory_loaded_lenient",
        path=str(root),
        ok=ok_count,
        errors=err_count,
    )
    return results
