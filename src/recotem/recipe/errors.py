"""RecipeError with YAML line-number context."""

from __future__ import annotations

from pathlib import Path

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


def describe_recipe_load_failure(exc: Exception | None) -> str:
    """Return the phrase naming what actually failed, for an operator-facing string.

    ``load_recipe`` already distinguishes a YAML *syntax* error (category
    ``"parse"``) from every other way a recipe can be rejected, but serving
    labelled them all "YAML parse", producing the self-contradictory::

        YAML parse failed: Recipe '...' failed validation:
          - training.metric: ...

    The file parsed; the schema rejected it. An operator reading that goes
    hunting for a syntax error that does not exist.

    Two phrases, not five: a syntax error is the only case whose remedy
    ("open the file in an editor") is not already spelled out by the rest of
    the message, which names the offending field, the security check, or the
    OS error on its own.
    """
    if isinstance(exc, RecipeError) and exc.category == "parse":
        return "YAML parse failed"
    return "recipe load failed"


def format_recipe_load_failure(
    exc: Exception | None,
    *,
    path: Path,
    context: str = "",
) -> str:
    """Compose the whole operator-facing string for a failed recipe load.

    Serving stores this in ``ModelEntry.last_load_error``, which is truncated
    to 200 characters before ``/v1/health/details`` serves it.  That budget,
    not the message, decides what an operator actually gets to read, and
    composing the string at each call site spent it on the same fact twice:
    the caller named the file, then ``load_recipe`` named it again as an
    absolute path.  A real rescan failure surfaced as::

        recipe load failed on rescan in 'demo.yaml': Recipe '/very/long/
        tmp/.../recipes/demo.yaml' failed va

    — the offending field, the only part that says what to change, cut off
    entirely, and how much of it survived depending on how deep the recipes
    directory happened to sit.

    So the directory is dropped and only the basename kept.  It carries
    nothing the operator does not already have: every recipe a serve process
    holds comes from the one ``--recipes`` directory it was started with.

    *context* ("on rescan") qualifies *when* the load was attempted; it is
    the caller's, since the exception cannot know.  The file is named in the
    prefix only when the message does not already name it — most of
    ``load_recipe``'s messages quote the file, but a scheme or credential
    rejection quotes the offending *field* instead, and those still need the
    locus to be actionable.
    """
    reason = str(exc).replace(str(path), path.name)
    where = f" {context}" if context else ""
    locus = "" if f"'{path.name}'" in reason else f" in '{path.name}'"
    return f"{describe_recipe_load_failure(exc)}{where}{locus}: {reason}"
