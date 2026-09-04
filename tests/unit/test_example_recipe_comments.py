"""The `# Choices:` comments in shipped recipes must name usable algorithms.

A reader copies the algorithm list out of `examples/quickstart/recipe.yaml`
before they have read `docs/recipe-reference.md`. When that comment offered
`BPRFM` -- gated behind `lightfm`, which has no Python 3.12 release, so irspack
never exports it -- following the shipped example produced exit 4 while the
CHANGELOG said the choice had been withdrawn.

The comment is documentation the product ships, so it is checked against what
the product can actually construct.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
EXAMPLE_RECIPES = sorted((REPO_ROOT / "examples").rglob("recipe.yaml"))

_CHOICES = re.compile(r"#\s*Choices:\s*(?P<names>[^.\n]+)\.")


def _algorithm_choice_lines(text: str) -> list[str]:
    """The `# Choices:` comments that list algorithms, not sources or metrics."""
    out = []
    for match in _CHOICES.finditer(text):
        names = [n.strip() for n in match.group("names").split(",")]
        if any(n in {"IALS", "TopPop", "CosineKNN"} for n in names):
            out.append(match.group("names"))
    return out


@pytest.mark.parametrize(
    "recipe_path", EXAMPLE_RECIPES, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_choices_comment_names_only_constructible_algorithms(
    recipe_path: Path,
) -> None:
    from recotem.training.algorithms import (
        UnknownAlgorithmError,
        constructible_class_names,
        resolve_algorithm_name,
    )

    available = constructible_class_names()

    for line in _algorithm_choice_lines(recipe_path.read_text()):
        for alias in (n.strip() for n in line.split(",")):
            try:
                class_name = resolve_algorithm_name(alias)
            except UnknownAlgorithmError:
                pytest.fail(
                    f"{recipe_path.name} offers unknown algorithm {alias!r} "
                    "in its `# Choices:` comment"
                )
            assert class_name in available, (
                f"{recipe_path.name} offers {alias!r} in its `# Choices:` "
                f"comment, but {class_name} cannot be constructed on this "
                "host, so a reader copying the comment gets exit 4"
            )


def test_at_least_one_example_carries_an_algorithm_choices_comment() -> None:
    """Guards the parser: a silent zero-match would make the check vacuous."""
    total = sum(len(_algorithm_choice_lines(p.read_text())) for p in EXAMPLE_RECIPES)
    assert total >= 4, f"expected the shipped examples to list choices, found {total}"
