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
  either key is refused rather than skipped;
- `values.yaml`'s `image.tag` is checked, because that -- not `appVersion` --
  is the image a chart install actually pulls;
- the pinned `ghcr.io/codelibs/recotem:X.Y.Z` references under `examples/` and
  `docs/`, and the `app.kubernetes.io/version` label in `examples/k8s/`, are
  checked too, and a scan that matches nothing is refused rather than passed.
"""

from __future__ import annotations

import os
import re
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
    values_image_tag: str | None = "2.1.0",
    example_pin: str | None = "2.1.0",
    docs_pin: str | None = "2.1.0",
    version_label: str | None = "2.1.0",
    changelog_heading: str | None = "## [2.1.0] - 2026-01-01",
) -> Path:
    """Build a minimal tree the script can read, and return its script path.

    The script derives REPO_ROOT from its own location, so the copy has to sit
    at `<root>/.github/scripts/` for the relative lookups to resolve.  `None`
    for any field omits that declaration entirely.
    """
    (root / ".github" / "scripts").mkdir(parents=True)
    (root / "src" / "recotem").mkdir(parents=True)
    (root / "helm" / "recotem").mkdir(parents=True)
    (root / "examples" / "k8s").mkdir(parents=True)
    (root / "docs" / "deployment").mkdir(parents=True)

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

    # A decoy `tag:` under a different top-level key guards the extractor
    # against matching the name anywhere in the file.
    values = 'nameOverride: ""\ntrain:\n  image:\n    tag: "decoy"\nimage:\n'
    values += "  repository: ghcr.io/codelibs/recotem\n"
    if values_image_tag is not None:
        values += f'  tag: "{values_image_tag}"\n'
    values += "  pullPolicy: IfNotPresent\n"
    (root / "helm" / "recotem" / "values.yaml").write_text(values, encoding="utf-8")

    # Deployment pins.  `:latest` sits alongside the pinned reference in both
    # files so the scan has to leave a deliberately-floating tag alone; it is
    # what compose.yaml and the getting-started docs use on purpose.
    deployment = "spec:\n  template:\n    metadata:\n      labels:\n"
    if version_label is not None:
        deployment += f'        app.kubernetes.io/version: "{version_label}"\n'
    deployment += "    spec:\n      containers:\n"
    deployment += "        - name: serve\n"
    if example_pin is not None:
        deployment += f"          image: ghcr.io/codelibs/recotem:{example_pin}\n"
    (root / "examples" / "k8s" / "serve-deployment.yaml").write_text(
        deployment, encoding="utf-8"
    )

    docs = "# Kubernetes\n\nPull the image:\n\n"
    docs += "    docker run --rm ghcr.io/codelibs/recotem:latest --help\n\n"
    if docs_pin is not None:
        docs += f"          image: ghcr.io/codelibs/recotem:{docs_pin}\n"
    (root / "docs" / "deployment" / "k8s.md").write_text(docs, encoding="utf-8")

    # The GitHub Release notes are derived from this section, so the release
    # heading is a release artifact like any other.  `None` omits the file.
    if changelog_heading is not None:
        (root / "CHANGELOG.md").write_text(
            f"# Changelog\n\n{changelog_heading}\n\n### Added\n\n- a thing\n",
            encoding="utf-8",
        )

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
        (
            {"values_image_tag": "2.0.0"},
            "helm/recotem/values.yaml image.tag: (2.0.0)",
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


# ---------------------------------------------------------------------------
# values.yaml image.tag
#
# `recotem.image` renders `.Values.image.tag | default .Chart.AppVersion`, so
# appVersion is a fallback that never fires while values.yaml pins a tag.
# Checking appVersion alone let a release tagged vX.Y.Z ship a chart whose
# manifests pull the previous image, with the script reporting OK.
# ---------------------------------------------------------------------------


def test_stale_values_image_tag_is_refused(tmp_path: Path) -> None:
    """The regression: everything else bumped, values.yaml left behind."""
    script = _make_tree(tmp_path, values_image_tag="2.0.0")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "helm/recotem/values.yaml image.tag: (2.0.0)" in proc.stdout


def test_values_without_image_tag_is_refused(tmp_path: Path) -> None:
    """An absent image.tag would silently fall back to appVersion.

    Refused rather than skipped: a vacuous check is worse than a missing one,
    because the success message would then vouch for a pin nobody set.
    """
    script = _make_tree(tmp_path, values_image_tag=None)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1
    assert "no 'image.tag' value" in proc.stdout


def test_missing_values_file_is_refused(tmp_path: Path) -> None:
    script = _make_tree(tmp_path)
    (tmp_path / "helm" / "recotem" / "values.yaml").unlink()
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1
    assert "Cannot read helm/recotem/values.yaml" in proc.stdout


def test_a_tag_under_another_key_is_not_mistaken_for_image_tag(
    tmp_path: Path,
) -> None:
    """`train.image.tag` sits above `image:` in the fixture and must be ignored.

    The extractor tracks which top-level block it is in; a plain search for
    `tag:` would read the decoy and pass a stale release.
    """
    script = _make_tree(tmp_path)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "helm values.yaml image.tag   = 2.1.0" in proc.stdout


def test_success_message_does_not_overclaim(tmp_path: Path) -> None:
    """The script checks four files, not "every version declaration".

    The release procedure bumps twelve pins across seven files; the rest are
    covered only by step 3 of its verification block.  Claiming otherwise let
    an operator reading this line believe step 3 was already done.
    """
    script = _make_tree(tmp_path)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "every version declaration" not in proc.stdout
    assert "helm/recotem/values.yaml" in proc.stdout
    assert "version-locations.md" in proc.stdout


def test_real_values_declares_an_image_tag(tmp_path: Path) -> None:
    """The shipped chart keeps the shape the extractor parses.

    Asserts the key exists and is readable, not its value: the repository's
    pins deliberately sit on the last *released* version during a dev cycle.
    """
    values = (REPO_ROOT / "helm" / "recotem" / "values.yaml").read_text(
        encoding="utf-8"
    )
    in_image = False
    found = None
    for line in values.splitlines():
        if line[:1] not in ("", " ", "#"):
            in_image = line.rstrip() == "image:"
        elif in_image and line.strip().startswith("tag:"):
            found = line.split(":", 1)[1].strip().strip('"')
            break
    assert found, "helm/recotem/values.yaml must pin image.tag explicitly"


# ---------------------------------------------------------------------------
# Deployment pins outside the chart
#
# These used to be excluded from the script as "illustrative rather than
# load-bearing".  Measured on a live arm64 kind cluster, applying
# `examples/k8s/` verbatim deploys the image pinned there, and the published
# 2.0.0 arm64 variant cannot start at all -- its console script carries the
# build-stage shebang `#!/build/.venv/bin/python`, which does not exist in the
# runtime stage, so the bootstrap Job fails and every replica goes
# CrashLoopBackOff.  A release that bumps the chart and leaves these behind
# hands that image to everyone who follows the deployment docs.
# ---------------------------------------------------------------------------


def test_stale_examples_k8s_pin_is_refused(tmp_path: Path) -> None:
    """Everything else bumped, examples/k8s left on the previous image."""
    script = _make_tree(tmp_path, example_pin="2.0.0")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "does not match every deployment pin" in proc.stdout
    assert "examples/k8s/serve-deployment.yaml" in proc.stdout
    assert "ghcr.io/codelibs/recotem:2.0.0" in proc.stdout


def test_stale_docs_pin_is_refused(tmp_path: Path) -> None:
    """docs/deployment/k8s.md is what a reader copies, so it is checked too."""
    script = _make_tree(tmp_path, docs_pin="2.0.0")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "docs/deployment/k8s.md" in proc.stdout


def test_stale_version_label_is_refused(tmp_path: Path) -> None:
    """`app.kubernetes.io/version` is a version declaration like any other."""
    script = _make_tree(tmp_path, version_label="2.0.0")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "app.kubernetes.io/version" in proc.stdout


def test_every_stale_pin_is_named_in_one_run(tmp_path: Path) -> None:
    """One run, one fix pass -- the same contract the version pins have."""
    script = _make_tree(
        tmp_path, example_pin="2.0.0", docs_pin="1.9.9", version_label="2.0.0"
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1
    for expected in (
        "examples/k8s/serve-deployment.yaml",
        "docs/deployment/k8s.md",
        "app.kubernetes.io/version",
    ):
        assert expected in proc.stdout, f"{expected!r} missing from:\n{proc.stdout}"


def test_latest_tag_is_not_treated_as_a_pin(tmp_path: Path) -> None:
    """`:latest` tracks the moving tag on purpose and must not be flagged.

    The docs fixture carries a `:latest` reference alongside its pinned one; a
    scan that refused it would make every release unable to pass.
    """
    script = _make_tree(tmp_path)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "latest" not in proc.stdout


def test_no_pin_anywhere_is_refused_rather_than_passed(tmp_path: Path) -> None:
    """A vacuous scan is worse than a missing one.

    If the pattern stops matching -- a directory renamed, the registry path
    changed -- the script must say so rather than print a success message
    vouching for pins it never looked at.  Same reasoning as the empty
    `image.tag` case above.
    """
    script = _make_tree(tmp_path, example_pin=None, docs_pin=None)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "No pinned" in proc.stdout


def test_repo_deployment_pins_are_where_the_script_looks(tmp_path: Path) -> None:
    """Pins the script scans for must actually exist in this repository.

    The synthetic-tree cases above would all still pass if `examples/k8s/` were
    restructured so the real pins moved out of the scanned paths.  This one
    reads the repository.
    """
    hits = []
    for rel in ("examples", "docs"):
        for path in (REPO_ROOT / rel).rglob("*"):
            if not path.is_file() or path.suffix not in {".yaml", ".yml", ".md"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"ghcr\.io/codelibs/recotem:[0-9]+\.[0-9]+\.[0-9]+", text):
                hits.append(path.relative_to(REPO_ROOT))
    assert hits, (
        "no pinned ghcr.io/codelibs/recotem:X.Y.Z reference found under "
        "examples/ or docs/. The script refuses this case at release time; if "
        "the pins genuinely moved, teach the script where they went."
    )


# ---------------------------------------------------------------------------
# Ways the gate used to pass a release it should have refused
#
# Each case below made the script print "OK" on a tree that would have shipped
# a stale or non-existent version.  They are grouped because they share one
# shape: the value the script reads is not the value that reaches a user.
# ---------------------------------------------------------------------------


def test_nested_version_key_does_not_shadow_the_chart_version(
    tmp_path: Path,
) -> None:
    """`chart_key` must read a top-level key, not the first one at any depth.

    awk's `$1` is the first *field*, so an indented `version:` matched the same
    test as a top-level one and awk stopped there.  A `dependencies:` block is
    the ordinary way a Helm chart acquires exactly that shape.
    """
    script = _make_tree(tmp_path, chart_version="2.0.0")
    chart = tmp_path / "helm" / "recotem" / "Chart.yaml"
    chart.write_text(
        "apiVersion: v2\nname: recotem\ntype: application\n"
        "dependencies:\n  - name: redis\n    version: 2.1.0\n"
        "version: 2.0.0\n"
        'appVersion: "2.1.0"\n',
        encoding="utf-8",
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "helm/recotem/Chart.yaml version: (2.0.0)" in proc.stdout


def test_last_version_assignment_wins_as_python_reads_it(tmp_path: Path) -> None:
    """The guard must read the string `import recotem` reports.

    Stopping at the first `__version__` assignment read a different value from
    the one Python binds, which is the last.  A file carrying both would have
    passed the gate while the wheel reported the stale version.
    """
    script = _make_tree(tmp_path)
    version_py = tmp_path / "src" / "recotem" / "version.py"
    version_py.write_text(
        '__version__ = "2.1.0"\n__version__ = "2.0.0"\n', encoding="utf-8"
    )
    # What Python itself binds, so the assertion is anchored to real semantics.
    namespace: dict[str, str] = {}
    exec(version_py.read_text(encoding="utf-8"), namespace)  # noqa: S102
    assert namespace["__version__"] == "2.0.0"

    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "src/recotem/version.py (2.0.0)" in proc.stdout


@pytest.mark.parametrize("pin", ["2.1.0-alpine", "2.1.0rc1", "2.1.0.1"])
def test_a_pin_whose_tag_merely_starts_with_the_version_is_refused(
    tmp_path: Path, pin: str
) -> None:
    """The comparison is against the whole tag, not a three-segment prefix.

    `grep -o` with a prefix pattern returned `recotem:2.1.0` for a pin reading
    `recotem:2.1.0-alpine`, which compared equal to the tag and passed --
    vouching for an image tag that was never published.
    """
    script = _make_tree(tmp_path, example_pin=pin)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "does not match every deployment pin" in proc.stdout
    assert pin in proc.stdout


def test_a_version_label_with_a_suffix_is_refused(tmp_path: Path) -> None:
    """Same hole on the label side, where the old pattern simply missed it."""
    script = _make_tree(tmp_path, version_label="2.1.0-rc1")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "2.1.0-rc1" in proc.stdout


def test_no_version_label_anywhere_is_refused_rather_than_passed(
    tmp_path: Path,
) -> None:
    """The label scan gets the vacuity guard the pin scan already had.

    Deleting every `app.kubernetes.io/version` label reduced that half of the
    check to nothing while the script still reported OK for the release.
    """
    script = _make_tree(tmp_path, version_label=None)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "app.kubernetes.io/version" in proc.stdout


def test_stale_pins_and_stale_versions_are_reported_in_one_run(
    tmp_path: Path,
) -> None:
    """Both classes of failure in a single run -- the contract section 3 claims.

    The pin scan used to `fail` (which exits) before the version mismatches
    were printed, so a tree stale in both ways -- the normal state at the start
    of a release -- reported only the pins.  On the tag-triggered release path
    each extra round trip costs a tag delete, a re-tag and a re-push.
    """
    script = _make_tree(
        tmp_path, pyproject="2.0.0", version_py="2.0.0", example_pin="2.0.0"
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    for expected in (
        "examples/k8s/serve-deployment.yaml",
        "pyproject.toml (2.0.0)",
        "src/recotem/version.py (2.0.0)",
    ):
        assert expected in proc.stdout, f"{expected!r} missing from:\n{proc.stdout}"


# ---------------------------------------------------------------------------
# The CHANGELOG section for the release
#
# The GitHub Release notes are derived from it (see the release procedure's
# references/release-notes.md), and entries accumulate during the cycle under a
# heading marked `Unreleased` which the release renames to a date.  Nothing
# verified that rename: `grep -ci changelog` over the script returned 0, so a
# tag could publish notes drawn from a section still headed "Unreleased" -- and
# the CHANGELOG at the tagged commit would say so permanently.
# ---------------------------------------------------------------------------


def test_changelog_still_marked_unreleased_is_refused(tmp_path: Path) -> None:
    """The exact state `main` is in during a dev cycle."""
    script = _make_tree(tmp_path, changelog_heading="## [2.1.0] - Unreleased")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "still marked Unreleased" in proc.stdout
    assert "## [2.1.0] - Unreleased" in proc.stdout


def test_changelog_without_a_section_for_the_release_is_refused(
    tmp_path: Path,
) -> None:
    """A release whose notes are derived from a section that does not exist."""
    script = _make_tree(tmp_path, changelog_heading="## [2.0.0] - 2026-06-27")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "no CHANGELOG.md section" in proc.stdout


def test_missing_changelog_is_refused(tmp_path: Path) -> None:
    script = _make_tree(tmp_path, changelog_heading=None)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "CHANGELOG.md is missing" in proc.stdout


@pytest.mark.parametrize(
    "heading",
    [
        "## [2.1.0] - 2026-01-01",
        '## [2.1.0] - 2026-01-01 "Codename"',
        "## [2.1.0]",
    ],
)
def test_a_dated_or_bare_release_heading_passes(tmp_path: Path, heading: str) -> None:
    """Only the word "Unreleased" is refused, not a particular date format.

    The procedure's template is `## [X.Y.Z] - YYYY-MM-DD`, but pinning the exact
    shape would refuse a heading that is perfectly clear about being released.
    """
    script = _make_tree(tmp_path, changelog_heading=heading)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_an_unreleased_section_for_a_different_version_is_ignored(
    tmp_path: Path,
) -> None:
    """A dev cycle opened for the *next* version must not fail this release.

    Immediately after a release the procedure opens `## [X.Y+1.0] - Unreleased`.
    That heading is correct and must not be mistaken for this release's.
    """
    script = _make_tree(tmp_path, changelog_heading="## [2.2.0] - Unreleased")
    tmp_path.joinpath("CHANGELOG.md").write_text(
        "# Changelog\n\n## [2.2.0] - Unreleased\n\n### Added\n\n- next cycle\n\n"
        "## [2.1.0] - 2026-01-01\n\n### Added\n\n- this release\n",
        encoding="utf-8",
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_changelog_and_version_problems_are_reported_in_one_run(
    tmp_path: Path,
) -> None:
    """Third class of failure, same single-run contract as the other two."""
    script = _make_tree(
        tmp_path,
        pyproject="2.0.0",
        example_pin="2.0.0",
        changelog_heading="## [2.1.0] - Unreleased",
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1
    for expected in (
        "examples/k8s/serve-deployment.yaml",
        "pyproject.toml (2.0.0)",
        "still marked Unreleased",
    ):
        assert expected in proc.stdout, f"{expected!r} missing from:\n{proc.stdout}"


def test_the_repository_changelog_has_a_section_for_its_own_version() -> None:
    """The shipped CHANGELOG keeps the shape the script parses.

    Asserts the heading exists, not that it is dated: during a dev cycle it is
    deliberately `Unreleased`, which is exactly what the release renames.
    """
    version = (REPO_ROOT / "src" / "recotem" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', version)
    assert match is not None
    base = match.group(1).split(".dev")[0].split("a")[0].split("rc")[0]
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(base)}\]", changelog, re.M), (
        f"CHANGELOG.md has no '## [{base}]' heading; the release procedure "
        "renames that section rather than creating one, and the guard in "
        "check-release-tag.sh refuses a tag without it."
    )
