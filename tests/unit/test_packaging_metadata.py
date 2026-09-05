"""The declared Python support must match the interpreters CI actually tests.

Trove classifiers are the only machine-readable statement of *which* CPython
versions a release supports -- ``requires-python`` is an open-ended lower bound,
and PyPI's version facet reads the classifiers.  They are also written exactly
once: metadata is fixed at upload and a filename can never be reused, so a wrong
set stays wrong until the next release.  That is the same irreversibility
``scripts/check-release-tag.sh`` exists to protect, so it is checked here rather
than discovered after a tag.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _ROOT / "pyproject.toml"
_TEST_WORKFLOW = _ROOT / ".github" / "workflows" / "test.yml"
_RECIPE_REFERENCE = _ROOT / "docs" / "recipe-reference.md"
_CHANGELOG = _ROOT / "CHANGELOG.md"

_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (\d+\.\d+)$")

# The CPython versions `lightfm-next` publishes any wheel for, read off
# https://pypi.org/pypi/lightfm-next/1.19.0/json and pinned here deliberately.
# The pin is what turns "a new interpreter joined the classifiers" or "the
# lightfm-next pin moved" into a failing test instead of into a user's
# `error: command 'gcc' failed` at install time.  Everything outside this set
# builds the C extension from source on EVERY platform, not only on arm64.
_LIGHTFM_NEXT_PIN = "lightfm-next==1.19.0"
_LIGHTFM_NEXT_WHEEL_PYTHONS = frozenset({"3.12", "3.13"})


def _classifier_versions() -> set[str]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return {
        m.group(1)
        for c in data["project"]["classifiers"]
        if (m := _CLASSIFIER.match(c))
    }


def _ci_versions() -> set[str]:
    workflow = yaml.safe_load(_TEST_WORKFLOW.read_text(encoding="utf-8"))
    matrix = workflow["jobs"]["pytest"]["strategy"]["matrix"]["python-version"]
    return {str(v) for v in matrix}


def test_classifiers_match_the_tested_interpreters() -> None:
    classifiers, ci = _classifier_versions(), _ci_versions()
    assert classifiers == ci, (
        f"pyproject classifiers declare Python {sorted(classifiers)} but "
        f".github/workflows/test.yml runs the suite on {sorted(ci)}. "
        "Publishing the narrower set understates support and cannot be "
        "corrected after upload; publishing the wider set claims interpreters "
        "nothing tests. Change both together."
    )


def test_requires_python_floor_is_the_lowest_tested_interpreter() -> None:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    floor = data["project"]["requires-python"]
    lowest = min(_ci_versions(), key=lambda v: tuple(int(p) for p in v.split(".")))
    assert floor == f">={lowest}", (
        f"requires-python is {floor!r} but the lowest tested interpreter is "
        f"{lowest}. An installer would accept an interpreter the suite never runs on."
    )


def test_lightfm_wheel_matrix_pin_still_describes_the_pinned_dependency() -> None:
    """The pinned wheel matrix is only meaningful for the pinned lightfm-next."""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    bprfm = data["project"]["optional-dependencies"]["bprfm"]
    assert _LIGHTFM_NEXT_PIN in bprfm, (
        f"the bprfm extra is {bprfm}, but _LIGHTFM_NEXT_WHEEL_PYTHONS in this "
        f"file was read off PyPI for {_LIGHTFM_NEXT_PIN}. Re-read the wheel "
        "list for the new pin and update both together."
    )


def test_interpreters_without_a_lightfm_wheel_are_named_in_the_bprfm_caveat() -> None:
    """A declared interpreter with no lightfm wheel must be documented as such.

    `recotem[bprfm]` and `recotem[all]` install `lightfm-next`, whose C
    extension is built from source whenever no wheel matches the running
    interpreter -- which needs a compiler the slim images and most CI runners
    do not have.  The docs described this as an *architecture* limitation
    ("no linux/aarch64 wheel"), so a reader on x86_64 concluded they were
    unaffected.  They are not: an interpreter with no wheel for any platform
    fails identically on x86_64.  Measured on `python:3.14-slim`, linux/amd64::

        error: command 'gcc' failed: No such file or directory
        ERROR: Failed building wheel for lightfm-next

    Declaring the interpreter in the trove classifiers is a support claim, so
    the caveat has to name it.
    """
    uncovered = _classifier_versions() - _LIGHTFM_NEXT_WHEEL_PYTHONS
    if not uncovered:
        return
    caveat = _RECIPE_REFERENCE.read_text(encoding="utf-8")
    changelog = _CHANGELOG.read_text(encoding="utf-8")
    for version in sorted(uncovered):
        for name, text in (
            ("docs/recipe-reference.md", caveat),
            ("CHANGELOG.md", changelog),
        ):
            # Look only where BPRFM installation is discussed.
            relevant = [
                para
                for para in text.split("\n\n")
                if "lightfm-next" in para or "recotem[bprfm]" in para
            ]
            assert any(version in para for para in relevant), (
                f"pyproject declares support for Python {version}, which "
                f"{_LIGHTFM_NEXT_PIN} publishes no wheel for on any platform, "
                f"so `pip install recotem[bprfm]` builds from source and needs "
                f"a C compiler there. {name} does not say so where it explains "
                "the bprfm extra."
            )
