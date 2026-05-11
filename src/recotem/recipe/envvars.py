"""Env-var allow-list and blacklist logic for recipe expansion.

Only variables matching the prefix ``RECOTEM_RECIPE_*`` are expanded.
A secondary blacklist blocks sensitive variable names regardless of prefix.
"""

from __future__ import annotations

import os
import re

import structlog

from recotem.recipe.errors import RecipeError

logger = structlog.get_logger(__name__)

# Pattern that identifies an env-var reference in a YAML string value.
_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")

# Required prefix for any expandable variable.
_ALLOWED_PREFIX = "RECOTEM_RECIPE_"

# Substring-based blacklist: if any of these substrings appears in the
# uppercased variable name, the variable is blocked even if it satisfies the
# RECOTEM_RECIPE_* prefix check.
#
# Using substring matching instead of glob word-boundary patterns closes the
# gap where e.g. RECOTEM_RECIPE_APIKEY (no underscore before KEY) passed the
# old glob *_KEY* filter.
_BLACKLIST_SUBSTRINGS: tuple[str, ...] = (
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "TOKEN",
    "KEY",
    "AUTH",
    "BEARER",
    "CRED",
    "PRIVATE",
)

# Prefix-based blacklist: names whose uppercased form starts with any of these
# are blocked unconditionally (cloud credential env vars).
_BLACKLIST_PREFIXES: tuple[str, ...] = (
    "AWS_",
    "GCP_",
    "GOOGLE_",
    "AZURE_",
)

# Exact names (uppercased) that are always blocked regardless of prefix.
_BLACKLIST_EXACT: frozenset[str] = frozenset(
    {"RECOTEM_SIGNING_KEYS", "RECOTEM_API_KEYS"}
)


def _is_blacklisted(name: str) -> bool:
    """Return True if *name* (case-insensitive) matches any blacklist rule.

    Rules (checked in order — first match wins):
    1. Exact match against ``_BLACKLIST_EXACT``.
    2. Name uppercased starts with any prefix in ``_BLACKLIST_PREFIXES``.
    3. Uppercased name contains any substring in ``_BLACKLIST_SUBSTRINGS``.
    """
    upper = name.upper()
    if upper in _BLACKLIST_EXACT:
        return True
    if any(upper.startswith(p) for p in _BLACKLIST_PREFIXES):
        return True
    return any(s in upper for s in _BLACKLIST_SUBSTRINGS)


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
