"""YAML → Recipe loader with restricted env expansion and path security."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog
import yaml

from recotem.recipe.envvars import expand_env_vars
from recotem.recipe.errors import RecipeError
from recotem.recipe.models import Recipe

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Path scheme allow-list
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"s3", "gs", "az"})
_REJECTED_SCHEMES: frozenset[str] = frozenset(
    {"file", "http", "https", "ftp", "ftps", "memory"}
)


def _validate_path(path: str, field_name: str) -> None:
    """Check path scheme and reject embedded credentials.

    Raises
    ------
    RecipeError
        On disallowed scheme or embedded userinfo credentials.
    """
    parsed = urlparse(path)

    scheme = parsed.scheme.lower() if parsed.scheme else ""

    # Reject explicitly disallowed schemes.
    if scheme in _REJECTED_SCHEMES:
        raise RecipeError(
            f"'{field_name}' uses the disallowed scheme '{scheme}://'. "
            f"Allowed schemes: bare local path, s3://, gs://, az://."
        )

    # Reject unknown non-empty schemes (forward-compat guard).
    if scheme and scheme not in _ALLOWED_SCHEMES:
        raise RecipeError(
            f"'{field_name}' uses unknown scheme '{scheme}://'. "
            f"Allowed: bare local path, s3://, gs://, az://."
        )

    # Reject embedded credentials (URI userinfo).
    if parsed.username or parsed.password:
        raise RecipeError(
            f"'{field_name}' contains embedded credentials in the URI. "
            "Use ADC / instance profile / environment for cloud authentication."
        )


def _check_local_output_containment(path_str: str) -> None:
    """If RECOTEM_ARTIFACT_ROOT is set, assert resolved path lies under it."""
    artifact_root_env = os.environ.get("RECOTEM_ARTIFACT_ROOT", "")
    if not artifact_root_env:
        return

    artifact_root = Path(artifact_root_env).resolve()
    resolved = Path(path_str).resolve()

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
# on any source dict).
_NO_EXPAND_KEYS: frozenset[str] = frozenset({"query", "query_parameters"})


def _expand_node(
    node: Any,
    *,
    extra_allowed: dict[str, str] | None,
    _in_no_expand: bool = False,
) -> Any:
    """Recursively walk a parsed YAML node and expand env-var references.

    Expansion is skipped entirely inside ``query`` and ``query_parameters``
    keys at any nesting level.
    """
    if isinstance(node, str):
        if _in_no_expand:
            return node
        return expand_env_vars(node, extra_allowed=extra_allowed)
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for k, v in node.items():
            in_no_expand = _in_no_expand or (k in _NO_EXPAND_KEYS)
            result[k] = _expand_node(v, extra_allowed=extra_allowed, _in_no_expand=in_no_expand)
        return result
    if isinstance(node, list):
        return [
            _expand_node(item, extra_allowed=extra_allowed, _in_no_expand=_in_no_expand)
            for item in node
        ]
    return node


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

    # Env expansion (never touches query / query_parameters).
    try:
        expanded = _expand_node(raw_data, extra_allowed=extra_allowed)
    except RecipeError:
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

    # Build Recipe via pydantic (validates all sub-schemas).
    try:
        recipe = Recipe.model_validate(expanded)
    except Exception as exc:
        # Translate pydantic validation errors into RecipeError.
        raise RecipeError(
            f"Recipe '{p}' failed validation: {exc}"
        ) from exc

    # Local output path containment check.
    output_path = recipe.output.path
    _parsed_output = urlparse(output_path)
    if not _parsed_output.scheme:
        # Local path — apply RECOTEM_ARTIFACT_ROOT containment.
        _check_local_output_containment(output_path)

    logger.info("recipe_loaded", name=recipe.name, path=str(p))
    return recipe


def _validate_path_fields(data: dict[str, Any]) -> None:
    """Validate scheme + credentials for all path fields in the raw dict."""
    # output.path
    output = data.get("output")
    if isinstance(output, dict):
        output_path = output.get("path")
        if isinstance(output_path, str):
            _validate_path(output_path, "output.path")

    # source.path (CSV / Parquet sources)
    source = data.get("source")
    if isinstance(source, dict):
        source_path = source.get("path")
        if isinstance(source_path, str):
            _validate_path(source_path, "source.path")

    # item_metadata.path
    item_metadata = data.get("item_metadata")
    if isinstance(item_metadata, dict):
        meta_path = item_metadata.get("path")
        if isinstance(meta_path, str):
            _validate_path(meta_path, "item_metadata.path")


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
        raise RecipeError(f"Recipes directory '{root}' does not exist or is not a directory.")

    # Non-recursive: only direct children ending in .yaml
    yaml_files = sorted(f for f in root.iterdir() if f.is_file() and f.suffix == ".yaml")

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
