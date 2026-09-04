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

_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (\d+\.\d+)$")


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
