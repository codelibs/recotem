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


# The lowest release of each of these that a clean `pip install recotem`
# resolves into a *working* CLI.  Measured, one throwaway `python:3.12-slim`
# container per candidate, no compiler present, pip left free to pick the
# transitive dependencies for that candidate:
#
#   typer  0.12.0-0.12.3  `recotem --help` exits 1 with `RuntimeError: Type not
#                         yet supported: list[str] | None` -- the `env_var`
#                         option in cli.py is annotated that way, and those
#                         releases cannot render it.  No subcommand runs.
#          0.12.4-0.15.3  build the CLI, but declare `click>=8.0.0` with no
#                         upper bound, so a fresh resolve pairs them with
#                         click 8.5 and every command exits 1 in
#                         `Parameter.make_metavar()`.
#          0.15.4         first release that resolves to a click it works with.
#   pyyaml 6.0            publishes no wheel for any interpreter in the
#                         classifiers above, and its sdist no longer builds
#                         under Cython 3 -- so the version cannot be installed
#                         on any supported interpreter, with or without a
#                         compiler.
#          6.0.1          installs on 3.12, 3.13 and 3.14 (3.13/3.14 fall back
#                         to PyYAML's pure-Python parser); 6.0.3 is the first
#                         with a wheel for all three.
#
# A floor below these is a claim the project cannot honour: the resolver is
# free to pick the floor whenever something else in the environment caps the
# package, and what the user then gets is a `recotem` that cannot print its
# own help.  Re-measure before lowering either one.
_MEASURED_FLOORS = {"typer": (0, 15, 4), "pyyaml": (6, 0, 1)}

_FLOOR = re.compile(r"^([A-Za-z0-9._-]+)>=([0-9]+(?:\.[0-9]+)*)")


def _declared_floors() -> dict[str, tuple[int, ...]]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    floors: dict[str, tuple[int, ...]] = {}
    for spec in data["project"]["dependencies"]:
        if (m := _FLOOR.match(spec)) is not None:
            floors[m.group(1).lower()] = tuple(int(p) for p in m.group(2).split("."))
    return floors


def test_declared_floors_are_versions_that_actually_run() -> None:
    declared = _declared_floors()
    for name, measured in _MEASURED_FLOORS.items():
        assert name in declared, (
            f"{name} no longer declares a `>=` floor in [project.dependencies]; "
            "_MEASURED_FLOORS in this file records that one was measured. "
            "Remove the entry or restore the floor."
        )
        assert declared[name] >= measured, (
            f"{name} declares a floor of "
            f"{'.'.join(str(p) for p in declared[name])}, below the measured "
            f"{'.'.join(str(p) for p in measured)}. Everything under the "
            "measured value either fails to install or leaves `recotem --help` "
            "exiting 1 -- see the comment above _MEASURED_FLOORS. Re-measure in "
            "a bare slim container before lowering this."
        )
