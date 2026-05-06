"""Env-var allow-list and blacklist logic for recipe expansion.

Only variables matching the prefix ``RECOTEM_RECIPE_*`` are expanded.
A secondary blacklist blocks sensitive variable names regardless of prefix.
"""
from __future__ import annotations

import fnmatch
import os
import re

import structlog

from recotem.recipe.errors import RecipeError

logger = structlog.get_logger(__name__)

# Pattern that identifies an env-var reference in a YAML string value.
_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")

# Required prefix for any expandable variable.
_ALLOWED_PREFIX = "RECOTEM_RECIPE_"

# Blacklist patterns (case-insensitive glob).  A variable whose *upper-cased*
# name matches any of these is rejected even if it satisfies the prefix.
_BLACKLIST_PATTERNS: tuple[str, ...] = (
    "RECOTEM_SIGNING_KEY",
    "RECOTEM_API_KEYS",
    "*_SECRET*",
    "*_PASSWORD*",
    "AWS_*",
    "GOOGLE_*",
    "GCP_*",
)


def _is_blacklisted(name: str) -> bool:
    """Return True if *name* (case-insensitive) matches any blacklist pattern."""
    upper = name.upper()
    return any(fnmatch.fnmatchcase(upper, pat) for pat in _BLACKLIST_PATTERNS)


def _is_allowed(name: str) -> bool:
    """Return True if *name* may be substituted."""
    return name.upper().startswith(_ALLOWED_PREFIX) and not _is_blacklisted(name)


def expand_env_vars(
    value: str,
    *,
    extra_allowed: dict[str, str] | None = None,
) -> str:
    """Replace ``${RECOTEM_RECIPE_*}`` references in *value* with env values.

    Parameters
    ----------
    value:
        The raw string from the YAML (must not be a query or query_parameters
        string — callers are responsible for not passing those).
    extra_allowed:
        Additional name→value pairs supplied via ``--env-var KEY=...`` on the
        CLI.  These are merged on top of ``os.environ``; they must still pass
        the allow/blacklist check.

    Raises
    ------
    RecipeError
        If the referenced variable is not allowed, is blacklisted, or is
        missing.  The error message includes the variable *name* but never any
        variable *value*.
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)

        if _is_blacklisted(name):
            raise RecipeError(
                f"Environment variable '${{{name}}}' is blacklisted and cannot "
                "be used in a recipe."
            )

        if not _is_allowed(name):
            raise RecipeError(
                f"Environment variable '${{{name}}}' is not allowed in a recipe. "
                f"Only variables with the prefix '{_ALLOWED_PREFIX}' are expanded."
            )

        # Check explicit overrides first, then os.environ.
        env_source: dict[str, str] = os.environ  # type: ignore[assignment]
        if extra_allowed and name in extra_allowed:
            return extra_allowed[name]
        if name in env_source:
            return env_source[name]

        raise RecipeError(
            f"Environment variable '${{{name}}}' is referenced in the recipe "
            "but is not set."
        )

    return _ENV_VAR_RE.sub(_replace, value)


def has_env_var_references(value: str) -> bool:
    """Return True if *value* contains any ``${...}`` references."""
    return bool(_ENV_VAR_RE.search(value))
