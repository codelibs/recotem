"""RecipeError with YAML line-number context."""

from __future__ import annotations

# Valid category values for RecipeError.category.
#
# ``"security"``
#     Containment violation (symlink escape, path traversal), scheme policy
#     (disallowed output/input scheme), or embedded credentials in a URI.
#     Logged at ERROR level in the lenient loader — these must not be silently
#     swallowed.
# ``"schema"``
#     Pydantic validation failure, missing required field, invalid name regex,
#     or plugin source resolution failure.
# ``"parse"``
#     YAML syntax error.
# ``"io"``
#     File-level OS error (permission denied, not found, etc.).
# ``"unknown"``
#     Catch-all for errors that do not fall into the above categories.
_VALID_CATEGORIES: frozenset[str] = frozenset(
    {"security", "schema", "parse", "io", "unknown"}
)


class RecipeError(Exception):
    """Raised when a recipe file is invalid or cannot be loaded.

    Attributes
    ----------
    message:
        Human-readable description. Must never include env-var values.
    line:
        1-based line number inside the YAML source, if available.
    category:
        Short string describing the error origin.  One of ``"security"``,
        ``"schema"``, ``"parse"``, ``"io"``, or ``"unknown"`` (default).
        Security-category errors are logged at ERROR level by the lenient
        loader; all others are logged at WARN level.
    """

    def __init__(
        self,
        message: str,
        *,
        line: int | None = None,
        category: str = "unknown",
    ) -> None:
        self.message = message
        self.line = line
        self.category = category if category in _VALID_CATEGORIES else "unknown"
        suffix = f" (line {line})" if line is not None else ""
        super().__init__(f"{message}{suffix}")

    def __repr__(self) -> str:
        return (
            f"RecipeError({self.message!r}, line={self.line!r}, "
            f"category={self.category!r})"
        )
