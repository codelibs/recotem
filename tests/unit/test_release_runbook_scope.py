"""The release runbook must describe what `check-release-tag.sh` really checks.

The runbook is what a release is actually run from, so a wrong statement about
which files are machine-checked sends the operator either round an avoidable
re-run loop or -- worse -- past a manual step that is the only thing covering a
version string.

Two claims are pinned here, both against the real script rather than against
prose:

* the three version strings the runbook lists as "not machine-checked" really
  do slip past the script, so its step 3 stays load-bearing; and
* the strings it lists as covered really are refused.

The asymmetry between `docs/deployment/k8s.md`'s `app.kubernetes.io/version`
label (invisible to the script) and `examples/k8s/`'s identical label (checked)
is the surprising one, so it is asserted in both directions.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-release-tag.sh"
SKILL = REPO_ROOT / ".claude" / "skills" / "release-recotem" / "SKILL.md"
VERSION_LOCATIONS = (
    REPO_ROOT
    / ".claude"
    / "skills"
    / "release-recotem"
    / "references"
    / "version-locations.md"
)

_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(_BASH is None, reason="bash not on PATH")

NEW = "2.1.0"
OLD = "2.0.0"


def _tree(
    root: Path,
    *,
    docs_label: str = NEW,
    docs_values_excerpt: str = NEW,
    docker_prose: str = NEW,
    examples_label: str = NEW,
    docs_pin: str = NEW,
) -> Path:
    """A minimal tree carrying every version string the runbook table lists."""
    (root / ".github" / "scripts").mkdir(parents=True)
    (root / "src" / "recotem").mkdir(parents=True)
    (root / "helm" / "recotem").mkdir(parents=True)
    (root / "examples" / "k8s").mkdir(parents=True)
    (root / "docs" / "deployment").mkdir(parents=True)

    script = root / ".github" / "scripts" / SCRIPT.name
    shutil.copy(SCRIPT, script)

    (root / "pyproject.toml").write_text(
        f'[project]\nname = "recotem"\nversion = "{NEW}"\n', encoding="utf-8"
    )
    (root / "src" / "recotem" / "version.py").write_text(
        f'__version__ = "{NEW}"\n', encoding="utf-8"
    )
    (root / "helm" / "recotem" / "Chart.yaml").write_text(
        f"apiVersion: v2\nname: recotem\ntype: application\nversion: {NEW}\n"
        f'appVersion: "{NEW}"\n',
        encoding="utf-8",
    )
    (root / "helm" / "recotem" / "values.yaml").write_text(
        "image:\n  repository: ghcr.io/codelibs/recotem\n"
        f'  tag: "{NEW}"\n  pullPolicy: IfNotPresent\n',
        encoding="utf-8",
    )
    (root / "examples" / "k8s" / "serve-deployment.yaml").write_text(
        "spec:\n  template:\n    metadata:\n      labels:\n"
        f'        app.kubernetes.io/version: "{examples_label}"\n'
        "    spec:\n      containers:\n        - name: serve\n"
        f"          image: ghcr.io/codelibs/recotem:{NEW}\n",
        encoding="utf-8",
    )
    # docs/deployment/k8s.md carries all three of: a real image pin, a version
    # label, and a values.yaml excerpt.  Only the first is machine-checked.
    (root / "docs" / "deployment" / "k8s.md").write_text(
        "# Kubernetes\n\n"
        f"          image: ghcr.io/codelibs/recotem:{docs_pin}\n\n"
        f'        app.kubernetes.io/version: "{docs_label}"\n\n'
        "```yaml\nimage:\n  repository: ghcr.io/codelibs/recotem\n"
        f'  tag: "{docs_values_excerpt}"\n```\n',
        encoding="utf-8",
    )
    (root / "docs" / "deployment" / "docker.md").write_text(
        "# Docker\n\nThe Helm chart and `examples/k8s/` already pin "
        f"`{docker_prose}`.\n",
        encoding="utf-8",
    )
    return script


def _run(script: Path, tag: str = "v" + NEW) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_BASH), str(script), tag], capture_output=True, text=True, check=False
    )


def test_the_fixture_passes_when_everything_is_bumped(tmp_path: Path) -> None:
    """Control: without it, every "still passes" case below proves nothing."""
    proc = _run(_tree(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "stale",
    ["docs_label", "docs_values_excerpt", "docker_prose"],
)
def test_the_strings_the_runbook_calls_unchecked_really_are(
    tmp_path: Path, stale: str
) -> None:
    """Each row of the runbook's "not machine-checked" table, measured.

    If one of these starts failing, the script grew to cover that string and
    the runbook table must lose the row -- otherwise it keeps telling operators
    a manual step is the only thing catching something CI already catches.
    """
    proc = _run(_tree(tmp_path, **{stale: OLD}))
    assert proc.returncode == 0, (
        f"check-release-tag.sh now refuses a stale {stale}; "
        "version-locations.md still lists it as machine-unchecked.\n" + proc.stdout
    )


@pytest.mark.parametrize("stale", ["examples_label", "docs_pin"])
def test_the_strings_the_runbook_calls_checked_really_are(
    tmp_path: Path, stale: str
) -> None:
    """The other direction: what the runbook credits the script with.

    `examples_label` and `docs_label` are the same YAML key in two trees; only
    the first is scanned, and the runbook's table gives that as the reason.
    """
    proc = _run(_tree(tmp_path, **{stale: OLD}))
    assert proc.returncode == 1, (
        f"check-release-tag.sh no longer refuses a stale {stale}, which the "
        "runbook says it catches.\n" + proc.stdout
    )


def test_version_locations_names_the_three_unchecked_strings() -> None:
    text = VERSION_LOCATIONS.read_text(encoding="utf-8")
    assert "Not machine-checked" in text, (
        "version-locations.md no longer distinguishes the strings "
        "check-release-tag.sh covers from the ones only step 3 catches."
    )
    for needed in (
        "the label scan covers `examples/` only",
        "not a bare `tag:` key inside a fenced block",
        "already pin",
    ):
        assert needed in text, f"{needed!r} missing from version-locations.md"


def test_the_runbook_does_not_understate_the_scripts_scope() -> None:
    """Post-#248 the script also scans the pins under examples/ and docs/."""
    text = VERSION_LOCATIONS.read_text(encoding="utf-8")
    assert (
        "The rest of the table is verified by step 3 of the block below" not in text
    ), (
        "version-locations.md still claims every row but the chart is checked "
        "by step 3 alone. The script scans the examples/ and docs/ image pins "
        "and the examples/ version label too."
    )


def test_skill_md_lists_values_yaml_among_the_version_locations() -> None:
    """values.yaml image.tag decides what a cluster pulls and is gate-checked."""
    text = SKILL.read_text(encoding="utf-8")
    assert "helm/recotem/values.yaml" in text, (
        "SKILL.md omits helm/recotem/values.yaml from the places the version "
        "must be identical, though check-release-tag.sh refuses a tag that "
        "disagrees with its image.tag."
    )
