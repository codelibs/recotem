"""`docs/getting-started.md` Path B must work from a bare `pip install`.

Path B is the "everything in your venv" route: its only stated prerequisite is
Python 3.12+, and it never tells the reader to clone anything.  It used to run
`recotem validate examples/tutorial-purchase-log/recipe.yaml` immediately after
`pip install recotem` -- but the wheel ships `recotem/` and nothing else, so
that command failed for every reader who followed it literally::

    $ pip install recotem && recotem validate examples/tutorial-purchase-log/recipe.yaml
    Invalid value for 'RECIPE': Path 'examples/tutorial-purchase-log/recipe.yaml'
    does not exist.                                                    # exit 2

Path B now writes the recipe with a heredoc instead, which makes the page
self-contained -- and makes it a second copy of a recipe that already exists in
the repository.  These tests pin both halves of that: the wheel really does not
carry `examples/`, so the heredoc is load-bearing rather than decorative; the
embedded recipe is a valid recipe; and it still agrees with
`examples/tutorial-purchase-log/recipe.yaml` on every field that decides what
the tutorial trains.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
GETTING_STARTED = REPO_ROOT / "docs" / "getting-started.md"
TUTORIAL_RECIPE = REPO_ROOT / "examples" / "tutorial-purchase-log" / "recipe.yaml"

# The heredoc Path B tells the reader to run, i.e. everything between
# `cat > recipes/purchase_log.yaml <<'EOF'` and the closing `EOF`.
_HEREDOC = re.compile(
    r"cat > recipes/purchase_log\.yaml <<'EOF'\n(?P<body>.*?)\nEOF\n",
    re.DOTALL,
)


def _embedded_recipe_text() -> str:
    text = GETTING_STARTED.read_text(encoding="utf-8")
    match = _HEREDOC.search(text)
    assert match is not None, (
        "docs/getting-started.md Path B no longer writes the recipe with a "
        "`cat > recipes/purchase_log.yaml <<'EOF'` heredoc. Path B must stay "
        "runnable from a bare `pip install recotem`, which ships no examples/."
    )
    return match.group("body")


def test_path_b_does_not_reference_the_examples_directory() -> None:
    """A `pip install` reader has no `examples/` -- Path B must not use one.

    Scoped to the Path B section: Path A opens by saying the repo ships
    `compose.yaml` and the example recipe, and is explicitly run from a
    checkout, so its `examples/` references are correct.
    """
    text = GETTING_STARTED.read_text(encoding="utf-8")
    start = text.index("## Path B — pip install")
    end = text.index("## What just happened", start)
    path_b = text[start:end]

    offenders = [
        line
        for line in path_b.splitlines()
        if re.search(r"^\s*(recotem|uv run recotem)\b.*\bexamples/", line)
    ]
    assert not offenders, (
        "Path B runs a recotem command against examples/, which a reader who "
        "only ran `pip install recotem` does not have:\n  " + "\n  ".join(offenders)
    )


def test_the_wheel_really_ships_no_examples_directory() -> None:
    """Why the heredoc exists.

    Path B installs a *wheel*: `pip install recotem` prefers the wheel whenever
    one matches, so what the sdist contains does not reach that reader.  Scoped
    to the wheel target for exactly that reason -- the sdist may legitimately
    ship `examples/` (it is the tree a `--no-binary` install unpacks) without
    making the heredoc redundant.
    """
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = (
        config.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
    )
    named = [
        entry
        for value in wheel.values()
        for entry in (value if isinstance(value, list) else [value])
        if isinstance(entry, str) and "examples" in entry
    ]
    assert not named, (
        "the wheel target now ships examples/ "
        f"({named}); Path B's heredoc may no longer be necessary."
    )


def test_embedded_recipe_is_a_valid_recipe() -> None:
    """The heredoc is a command the reader runs, so it has to parse."""
    from recotem.recipe.models import Recipe

    data = yaml.safe_load(_embedded_recipe_text())
    recipe = Recipe.model_validate(data)
    assert recipe.name == "purchase_log"


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "source.type",
        "source.path",
        "source.sha256",
        "schema.user_column",
        "schema.item_column",
        "training.algorithms",
        "output.path",
    ],
)
def test_embedded_recipe_agrees_with_the_shipped_example(field: str) -> None:
    """The doc copy must not drift from examples/tutorial-purchase-log/.

    The sha256 pin is the one that matters most: if upstream rotates the CSV
    and only the example recipe is updated, Path B hands the reader a stale
    pin and `train` exits 3 on a sha256 mismatch.
    """
    embedded = yaml.safe_load(_embedded_recipe_text())
    shipped = yaml.safe_load(TUTORIAL_RECIPE.read_text(encoding="utf-8"))

    def dig(data: dict, path: str) -> object:
        for part in path.split("."):
            data = data[part]
        return data

    assert dig(embedded, field) == dig(shipped, field), (
        f"docs/getting-started.md Path B and "
        f"{TUTORIAL_RECIPE.relative_to(REPO_ROOT)} disagree on {field!r}. "
        "Update both, or the tutorial trains something different depending on "
        "which path the reader took."
    )
