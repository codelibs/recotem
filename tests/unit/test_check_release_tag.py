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
    docs_version_label: str | None = "2.1.0",
    docs_values_tag: str | None = "2.1.0",
    changelog_heading: str | None = "## [2.1.0] - 2026-01-01",
    changelog_link: str | None = (
        "[2.1.0]: https://github.com/codelibs/recotem/releases/tag/v2.1.0"
    ),
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
    # The deployment doc carries two more version declarations the release
    # bumps and the gate used not to read: a copy of the k8s version label,
    # and a copy-pasteable values.yaml excerpt.
    if docs_version_label is not None:
        docs += f'    app.kubernetes.io/version: "{docs_version_label}"\n'
    if docs_values_tag is not None:
        docs += "\n```yaml\nimage:\n  repository: ghcr.io/codelibs/recotem\n"
        docs += f'  tag: "{docs_values_tag}"\n```\n'
    (root / "docs" / "deployment" / "k8s.md").write_text(docs, encoding="utf-8")

    # The GitHub Release notes are derived from this section, so the release
    # heading is a release artifact like any other.  `None` omits the file.
    if changelog_heading is not None:
        body = f"# Changelog\n\n{changelog_heading}\n\n### Added\n\n- a thing\n"
        if changelog_link is not None:
            body += f"\n{changelog_link}\n"
        (root / "CHANGELOG.md").write_text(body, encoding="utf-8")

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
    The scan now covers `docs/` as well as `examples/`, so "every" means both.
    """
    script = _make_tree(tmp_path, version_label=None, docs_version_label=None)
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
        "## [2.1.0] - 2026-01-01\n\n### Added\n\n- this release\n\n"
        "[2.1.0]: https://github.com/codelibs/recotem/releases/tag/v2.1.0\n",
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


# ---------------------------------------------------------------------------
# The tagged commit must be on main
#
# Everything above reads files, so it describes the *tree* and says nothing
# about where that tree sits in history.  PR #245 is the worked example: merged
# against milestone 2.1.0, shown as merged on GitHub, and its merge commit is
# not an ancestor of main -- `get_driver_name` appears 0 times in
# `origin/main:src/recotem/datasource/sql.py`.  A tag on such a commit carries a
# perfectly consistent set of version strings.
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
    )


def _make_repo(root: Path, *, with_main: bool = True) -> Path:
    """Turn a synthetic tree into a real git repo whose toplevel is `root`."""
    script = _make_tree(root)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-qm", "base")
    if not with_main:
        # A work tree with no main ref at all -- what actions/checkout's default
        # fetch-depth: 1 produces on a tag build.
        _git(root, "checkout", "-q", "-b", "detached-from-main")
        _git(root, "branch", "-q", "-D", "main")
    return script


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_a_commit_on_main_passes(tmp_path: Path) -> None:
    """Control: without it, the two failure cases below prove nothing."""
    script = _make_repo(tmp_path)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "The tagged commit is on main." in proc.stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_a_commit_that_never_reached_main_is_refused(tmp_path: Path) -> None:
    """The #245 shape: a consistent tree on a commit main does not contain."""
    script = _make_repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "stranded")
    (tmp_path / "extra.txt").write_text("only on the branch\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(
        tmp_path,
        "-c",
        "user.email=a@b.c",
        "-c",
        "user.name=a",
        "commit",
        "-qm",
        "never merged",
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "is on a commit that is not on main" in proc.stdout
    assert "is not an" in proc.stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_a_work_tree_without_a_main_ref_is_refused_not_skipped(
    tmp_path: Path,
) -> None:
    """A shallow CI checkout must fail loudly rather than skip.

    `actions/checkout`'s default `fetch-depth: 1` yields a work tree in which
    `origin/main` does not resolve.  Skipping there would make the check
    vacuous exactly where it matters, so the guard jobs set `fetch-depth: 0`
    and this state is refused -- which is what makes that setting
    self-enforcing.
    """
    script = _make_repo(tmp_path, with_main=False)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cannot be checked against main" in proc.stdout
    assert "fetch-depth" in proc.stdout


def test_outside_a_git_work_tree_the_success_message_says_so(
    tmp_path: Path,
) -> None:
    """Not a repo: nothing to check, and the script must not imply otherwise."""
    script = _make_tree(tmp_path)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "NOT a git work tree" in proc.stdout
    assert "tagged commit is on main was NOT checked" in proc.stdout
    assert "The tagged commit is on main." not in proc.stdout
    assert "Those files are committed" not in proc.stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_branch_and_version_problems_are_reported_in_one_run(
    tmp_path: Path,
) -> None:
    """Fourth class of failure, same single-run contract as the other three."""
    script = _make_repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "stranded")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "recotem"\nversion = "2.0.0"\n', encoding="utf-8"
    )
    _git(tmp_path, "add", "-A")
    _git(
        tmp_path,
        "-c",
        "user.email=a@b.c",
        "-c",
        "user.name=a",
        "commit",
        "-qm",
        "stale and stranded",
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1
    for expected in ("pyproject.toml (2.0.0)", "is on a commit that is not on main"):
        assert expected in proc.stdout, f"{expected!r} missing from:\n{proc.stdout}"


@pytest.mark.parametrize("workflow", ["publish.yml", "docker.yml"])
def test_guard_jobs_check_out_full_history(workflow: str) -> None:
    """The workflow half of the check.

    Without `fetch-depth: 0` the guard's checkout has no main ref and the
    script refuses -- so this asserts the setting that keeps the guard green
    for the right reason rather than red for the wrong one.
    """
    import yaml

    spec = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    )
    checkouts = [
        step
        for step in spec["jobs"]["guard"]["steps"]
        if "actions/checkout" in str(step.get("uses", ""))
    ]
    assert checkouts, f"{workflow} guard job has no checkout step"
    for step in checkouts:
        assert (step.get("with") or {}).get("fetch-depth") == 0, (
            f"{workflow} guard job checks out shallow; check-release-tag.sh "
            "needs main to verify the tagged commit is on it, and refuses "
            "rather than skipping when it cannot."
        )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_a_shallow_clone_is_refused_rather_than_answered_wrongly(
    tmp_path: Path,
) -> None:
    """Shallowness poisons the answer, and git gives no hint that it has.

    A missing object makes `merge-base --is-ancestor` exit 128, which is loud.
    A *shallow* repository is worse: the tip object is present, the connecting
    history is not, and git returns a confident "not an ancestor" for a commit
    that is on main.  Measured on the fixture below -- exit 0 in the full
    clone, exit 1 in the depth-1 clone of the same repository.  Unguarded, that
    would fail a legitimate release and name the wrong reason.
    """
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "main")
    author = ("-c", "user.email=a@b.c", "-c", "user.name=a")
    _git(upstream, *author, "commit", "-q", "--allow-empty", "-m", "c1")
    for i in range(2, 6):
        _git(upstream, *author, "commit", "-q", "--allow-empty", "-m", f"c{i}")

    work = tmp_path / "work"
    # `--depth` is silently ignored for a local *path* clone (git hardlinks the
    # object store), so the URL has to be file:// for this to be shallow at all.
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{upstream}", str(work)],
        check=True,
        capture_output=True,
    )
    assert (
        _git(work, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    ), "fixture is not shallow; the case under test was not created"

    script = _make_tree(work)
    _git(work, "add", "-A")
    _git(work, *author, "commit", "-qm", "release tree")

    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "shallow clone" in proc.stdout
    assert "fetch-depth: 0" in proc.stdout
    # It must NOT claim the commit is off main -- that is the wrong reason.
    assert "is on a commit that is not on main" not in proc.stdout


# ---------------------------------------------------------------------------
# Holes that survived #259 as well, closed here
# ---------------------------------------------------------------------------


def test_a_v_prefixed_pin_is_a_version_pin_not_a_moving_reference(
    tmp_path: Path,
) -> None:
    """`recotem:v2.0.0` is a stale pin, not a floating tag like `latest`.

    `is_version_pin` keyed on a leading digit, so the one spelling this check
    could not see was the spelling the git *tag* uses -- which is the spelling
    a hand-written pin is most likely to acquire. Measured before the fix: with
    every other location bumped and one pin left at `recotem:v2.0.0`, the
    script exited 0.
    """
    script = _make_tree(tmp_path, example_pin="v2.0.0")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "recotem:v2.0.0" in proc.stdout, proc.stdout


def test_a_v_prefixed_pin_matching_the_release_is_accepted(tmp_path: Path) -> None:
    """The comparison ignores the leading `v`; it does not demand one."""
    script = _make_tree(tmp_path, example_pin="v2.1.0")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_moving_references_are_still_exempt(tmp_path: Path) -> None:
    """Widening `is_version_pin` must not sweep in `latest` / `main` / `sha-`.

    Without this, accepting a `v` prefix could be "fixed" by accepting
    everything, which would fail the release on the `:latest` references
    compose.yaml and the getting-started docs carry on purpose.
    """
    script = _make_tree(tmp_path)
    docs = tmp_path / "docs" / "deployment" / "k8s.md"
    docs.write_text(
        docs.read_text(encoding="utf-8")
        + "    ghcr.io/codelibs/recotem:main\n"
        + "    ghcr.io/codelibs/recotem:sha-abc1234\n",
        encoding="utf-8",
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_stale_version_label_under_docs_is_refused(tmp_path: Path) -> None:
    """The label scan read `examples` while the pin scan read `examples docs`.

    `docs/deployment/k8s.md` carries its own copy of the k8s version label, so
    that asymmetry meant the script could exit 0 while the deployment doc still
    declared the previous release -- with a success message claiming coverage
    "under examples/ and docs/".
    """
    script = _make_tree(tmp_path, docs_version_label="2.0.0")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "k8s.md" in proc.stdout, proc.stdout
    assert '"2.0.0"' in proc.stdout, proc.stdout


def test_a_stale_values_excerpt_under_docs_is_refused(tmp_path: Path) -> None:
    """The second unread location in the same file.

    `docs/deployment/k8s.md` also carries a copy-pasteable `values.yaml`
    excerpt. The `image.tag` reader in section 3 is hard-wired to
    `helm/recotem/values.yaml`, and the excerpt has no `ghcr.io/` prefix for
    the pin scan to match, so nothing read it.
    """
    script = _make_tree(tmp_path, docs_values_tag="2.0.0")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "k8s.md" in proc.stdout, proc.stdout


def test_no_values_excerpt_anywhere_is_refused_rather_than_passed(
    tmp_path: Path,
) -> None:
    """The excerpt scan gets the same vacuity guard as the other two."""
    script = _make_tree(tmp_path, docs_values_tag=None)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "values.yaml excerpt" in proc.stdout, proc.stdout


# ---------------------------------------------------------------------------
# The tree this script reads must be the tree the tag would publish
# ---------------------------------------------------------------------------
# Sections 3-5 read the working tree; section 6 reports a fact about HEAD.
# Before this check they shared one success message. Measured at 7871f9f, whose
# committed pyproject.toml says 2.1.0.dev0 and whose CHANGELOG says Unreleased:
# editing only the working tree made the script print `pyproject.toml version =
# 2.1.0`, `CHANGELOG.md declares 2.1.0 released.` and `The tagged commit is on
# main.` together, and exit 0.


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=tests@recotem.invalid",
            "-c",
            "user.name=recotem tests",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def _commit_everything(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "synthetic release tree")


requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


@requires_git
def test_a_committed_release_tree_passes_and_says_which_tree_it_read(
    tmp_path: Path,
) -> None:
    script = _make_tree(tmp_path)
    _commit_everything(tmp_path)

    proc = _run(script, "v2.1.0")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Those files are committed" in proc.stdout, proc.stdout
    assert "The tagged commit is on main." in proc.stdout, proc.stdout


@requires_git
def test_an_uncommitted_edit_to_a_checked_file_is_refused(tmp_path: Path) -> None:
    """The exact shape that used to pass: commit stale, edit the work tree."""
    script = _make_tree(
        tmp_path,
        pyproject="2.0.0",
        chart_version="2.0.0",
        changelog_heading="## [2.1.0] - Unreleased",
    )
    _commit_everything(tmp_path)

    # Fix only the working tree. A tag here would publish 2.0.0 and a CHANGELOG
    # that calls 2.1.0 unreleased.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "recotem"\nversion = "2.1.0"\n', encoding="utf-8"
    )
    (tmp_path / "helm" / "recotem" / "Chart.yaml").write_text(
        "apiVersion: v2\nname: recotem\ntype: application\n"
        'version: 2.1.0\nappVersion: "2.1.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [2.1.0] - 2026-01-01\n\n### Added\n\n- a thing\n",
        encoding="utf-8",
    )

    proc = _run(script, "v2.1.0")

    assert proc.returncode == 1, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "differ from the commit" in combined, combined
    assert "pyproject.toml" in combined, combined
    assert "CHANGELOG.md" in combined, combined
    # None of the worktree-derived claims may be printed: emitting them is how
    # the old behaviour looked correct.
    assert "OK:" not in combined, combined
    assert "The tagged commit is on main." not in combined, combined


@requires_git
def test_an_untracked_file_outside_the_checked_paths_does_not_block(
    tmp_path: Path,
) -> None:
    """Only the files this script reads matter.

    Refusing on an unrelated scratch file would make the gate fire on releases
    it has nothing to say about, which is how operators learn to look past it.
    """
    script = _make_tree(tmp_path)
    _commit_everything(tmp_path)
    (tmp_path / "release-notes-draft.md").write_text("scratch\n", encoding="utf-8")

    proc = _run(script, "v2.1.0")

    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_changelog_heading_without_its_link_definition_is_refused(
    tmp_path: Path,
) -> None:
    """`## [X.Y.Z]` is a reference link; without the definition it is plain text.

    Only the newest heading is affected -- every earlier release still has its
    definition and still renders as a link -- so the page looks correct unless
    you scroll to the one that matters. Measured at 7871f9f:
    `grep -nE '^\\[[0-9]' CHANGELOG.md` returns 2.0.0 and 1.0.0 and no 2.1.0.
    The release procedure says to add it; nothing checked that it was.
    """
    script = _make_tree(tmp_path, changelog_link=None)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "link definition" in combined, combined
    assert "releases/tag/v2.1.0" in combined, combined
