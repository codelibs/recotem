"""RecipeError with YAML line-number context."""

from __future__ import annotations


class RecipeError(Exception):
    """Raised when a recipe file is invalid or cannot be loaded.

    Attributes
    ----------
    message:
        Human-readable description. Must never include env-var values.
    line:
        1-based line number inside the YAML source, if available.
    """

    def __init__(self, message: str, *, line: int | None = None) -> None:
        self.message = message
        self.line = line
        suffix = f" (line {line})" if line is not None else ""
        super().__init__(f"{message}{suffix}")

    def __repr__(self) -> str:
        return f"RecipeError({self.message!r}, line={self.line!r})"
