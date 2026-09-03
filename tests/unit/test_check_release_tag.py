"""Unit tests for .github/scripts/check-release-tag.sh.

This script is the only gate on two irreversible publications: the PyPI
filename (which can never be reused) and — since the container push was put
behind the same guard — the GHCR image.  Its sibling
.github/scripts/validate-manifests.sh is covered by test_k8s_manifests.py; this
one had no coverage at all, so a refactor could have loosened it silently.

Every case runs the real script against a synthetic tree in `tmp_path`, never
against the repository, so the tests neither mutate the working tree nor go
red when the project's own version is mid-bump.

Covered:
- a clean full bump passes;
- each partial-bump direction fails and names the file that did not move;
- `.dev`, `a`/`b`/`rc`, a tag with no leading `v`, and a malformed tag are all
  refused before any version is read;
- the chart's `version:` and `appVersion:` are checked, and a chart missing
  either key is refused rather than skipped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-release-tag.sh"

_BASH = shutil.which("bash")
requires_bash = pytest.mark.skipif(_BASH is None, reason="bash not on PATH")

pytestmark = requires_bash


def _make_tree(
    root: Path,
    *,
    pyproject: str | None = "2.1.0",
    version_py: str | None = "2.1.0",
    chart_version: str | None = "2.1.0",
    chart_app_version: str | None = "2.1.0",
) -> Path:
    """Build a minimal tree the script can read, and return its script path.

    The script derives REPO_ROOT from its own location, so the copy has to sit
    at `<root>/.github/scripts/` for the relative lookups to resolve.  `None`
    for any field omits that declaration entirely.
    """
    (root / ".github" / "scripts").mkdir(parents=True)
    (root / "src" / "recotem").mkdir(parents=True)
    (root / "helm" / "recotem").mkdir(parents=True)

    script = root / ".github" / "scripts" / SCRIPT.name
    shutil.copy(SCRIPT, script)

    body = '[project]\nname = "recotem"\n'
    if pyproject is not None:
        body += f'version = "{pyproject}"\n'
    (root / "pyproject.toml").write_text(body, encoding="utf-8")

    (root / "src" / "recotem" / "version.py").write_text(
        "" if version_py is None else f'__version__ = "{version_py}"\n',
        encoding="utf-8",
    )

    chart = "apiVersion: v2\nname: recotem\ntype: application\n"
    if chart_version is not None:
        chart += f"version: {chart_version}\n"
    if chart_app_version is not None:
        chart += f'appVersion: "{chart_app_version}"\n'
    (root / "helm" / "recotem" / "Chart.yaml").write_text(chart, encoding="utf-8")

    return script


def _run(script: Path, tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_BASH), str(script), tag],
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_clean_full_bump_passes(tmp_path: Path) -> None:
    """Package and chart all at the tagged version — the only accepting case."""
    script = _make_tree(tmp_path)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK: v2.1.0 is a final release" in proc.stdout


# ---------------------------------------------------------------------------
# Partial bumps — every direction, each naming the file that did not move
#
# This is the failure the script exists to catch: the declarations are separate
# strings that cannot self-sync, so any one of them can be left behind.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected_file"),
    [
        ({"pyproject": "2.0.0"}, "pyproject.toml (2.0.0)"),
        ({"version_py": "2.0.0"}, "src/recotem/version.py (2.0.0)"),
        ({"chart_version": "2.0.0"}, "helm/recotem/Chart.yaml version: (2.0.0)"),
        (
            {"chart_app_version": "2.0.0"},
            "helm/recotem/Chart.yaml appVersion: (2.0.0)",
        ),
    ],
)
def test_partial_bump_fails_naming_the_stale_file(
    tmp_path: Path, kwargs: dict[str, str], expected_file: str
) -> None:
    script = _make_tree(tmp_path, **kwargs)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1
    assert expected_file in proc.stdout


def test_all_stale_declarations_are_reported_in_one_run(tmp_path: Path) -> None:
    """One run names every file that did not move, not just the first."""
    script = _make_tree(
        tmp_path,
        pyproject="2.0.0",
        version_py="2.0.0",
        chart_version="2.0.0",
        chart_app_version="2.0.0",
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1
    for expected in (
        "pyproject.toml (2.0.0)",
        "src/recotem/version.py (2.0.0)",
        "helm/recotem/Chart.yaml version: (2.0.0)",
        "helm/recotem/Chart.yaml appVersion: (2.0.0)",
    ):
        assert expected in proc.stdout


# ---------------------------------------------------------------------------
# Tag shape
#
# A non-final tag is refused before any version is read, so the tree matching
# it makes no difference — these trees are deliberately consistent with the tag.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "version", "hint"),
    [
        ("v2.1.0.dev0", "2.1.0.dev0", "'.dev' development suffix"),
        ("v2.1.0a0", "2.1.0a0", "'a' (alpha) pre-release suffix"),
        ("v2.1.0b1", "2.1.0b1", "'b' (beta) pre-release suffix"),
        ("v2.1.0rc1", "2.1.0rc1", "'rc' (release candidate) suffix"),
        ("v2.1.0.post1", "2.1.0.post1", "'.post' post-release suffix"),
        ("v2.1.0+local", "2.1.0+local", "'+local' version suffix"),
        ("2.1.0", "2.1.0", "not of the form vMAJOR.MINOR.PATCH"),
        ("v2.1", "2.1", "not of the form vMAJOR.MINOR.PATCH"),
        ("release-2.1.0", "2.1.0", "not of the form vMAJOR.MINOR.PATCH"),
        ("v2.1.0 ; rm -rf /", "2.1.0", "not of the form vMAJOR.MINOR.PATCH"),
    ],
)
def test_non_final_or_malformed_tag_is_refused(
    tmp_path: Path, tag: str, version: str, hint: str
) -> None:
    script = _make_tree(
        tmp_path,
        pyproject=version,
        version_py=version,
        chart_version=version,
        chart_app_version=version,
    )
    proc = _run(script, tag)
    assert proc.returncode == 1
    assert "Refusing to publish" in proc.stdout
    assert hint in proc.stdout


def _run_with_ref(script: Path, ref: str | None) -> subprocess.CompletedProcess[str]:
    """Run with no argument, controlling GITHUB_REF.

    The environment is inherited rather than replaced: the script shells out to
    `python3` for tomllib, and a stripped PATH finds whichever interpreter the
    OS ships (3.9 on macOS), not the one the project runs under.  GITHUB_REF is
    set by GitHub Actions itself, so the absent case has to remove it.
    """
    env = dict(os.environ)
    env.pop("GITHUB_REF", None)
    if ref is not None:
        env["GITHUB_REF"] = ref
    return subprocess.run(
        [str(_BASH), str(script)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_missing_tag_and_ref_is_refused(tmp_path: Path) -> None:
    """No argument and no GITHUB_REF must fail, not fall through to a default."""
    proc = _run_with_ref(_make_tree(tmp_path), None)
    assert proc.returncode == 1
    assert "requires a tag ref" in proc.stdout


def test_tag_is_read_from_github_ref(tmp_path: Path) -> None:
    """CI passes no argument; the tag comes from GITHUB_REF."""
    proc = _run_with_ref(_make_tree(tmp_path), "refs/tags/v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK: v2.1.0 is a final release" in proc.stdout


# ---------------------------------------------------------------------------
# Fail-closed on an unreadable declaration
#
# A declaration the script cannot read must be an error, never a pass: a silent
# skip is indistinguishable from a match and would reopen the gap.
# ---------------------------------------------------------------------------


def test_chart_without_version_key_is_refused(tmp_path: Path) -> None:
    script = _make_tree(tmp_path, chart_version=None)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1
    assert "no top-level 'version:' key" in proc.stdout


def test_chart_without_app_version_key_is_refused(tmp_path: Path) -> None:
    script = _make_tree(tmp_path, chart_app_version=None)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1
    assert "no top-level 'appVersion:' key" in proc.stdout


def test_missing_chart_file_is_refused(tmp_path: Path) -> None:
    script = _make_tree(tmp_path)
    (tmp_path / "helm" / "recotem" / "Chart.yaml").unlink()
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1
    assert "Cannot read helm/recotem/Chart.yaml" in proc.stdout


def test_version_py_without_assignment_is_refused(tmp_path: Path) -> None:
    script = _make_tree(tmp_path, version_py=None)
    proc = _run(script, "v2.1.0")
    assert proc.returncode != 0
    assert "no __version__ assignment found" in proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# The chart is genuinely consulted
#
# `apiVersion: v2` sits above `version:` in the real Chart.yaml and must not be
# mistaken for it — a substring match on "version" would read `v2`.
# ---------------------------------------------------------------------------


def test_api_version_is_not_mistaken_for_the_chart_version(tmp_path: Path) -> None:
    script = _make_tree(tmp_path)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "helm Chart.yaml version      = 2.1.0" in proc.stdout


def test_real_chart_declares_both_keys(tmp_path: Path) -> None:
    """The shipped chart keeps the shape the script parses.

    Asserts the keys exist and are readable, not their values: the repository's
    pins deliberately sit on the last *released* version during a dev cycle.
    """
    chart = (REPO_ROOT / "helm" / "recotem" / "Chart.yaml").read_text(encoding="utf-8")
    lines = chart.splitlines()
    assert any(line.startswith("version: ") for line in lines)
    assert any(line.startswith("appVersion: ") for line in lines)
