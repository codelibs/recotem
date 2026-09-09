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
- the pinned `ghcr.io/codelibs/recotem:X.Y.Z` references and the
  `app.kubernetes.io/version` labels under `examples/` are checked too, and a
  scan that matches nothing is refused rather than passed;
- every `recotem.org/MAJOR.MINOR/` documentation URL in the tree names the
  release's MAJOR.MINOR, since those URLs ship inside artefacts nobody can
  correct afterwards -- error message text, the JSON Schema, the
  `/v1/metrics` HELP string, `README.md` as rendered on PyPI.

There is no `docs/` tree in this repository any more: the documentation lives
at recotem.org, in the separate recotem-docs repository, which bumps its own
copies.  The scans that used to read `docs/` -- the pin scan, the label scan,
the `values.yaml`-excerpt scan and the `docs/upgrading.md` exemption -- went
with it, and the documentation-site URL scan took the excerpt scan's place.
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

# The documentation line the fixtures sit on, and one that is deliberately not
# it.  Both are spelled as a bare MAJOR.MINOR and pasted into a URL where they
# are used, so that no whole `recotem.org/<major>.<minor>/` is ever written out
# in this file -- `tests` is one of the roots section 4b scans, so such a
# literal is a hit the real gate reads.  Measured: with the stale one spelled
# out in an assertion, `check-release-tag.sh v2.1.0` run on this repository
# reported this very file as carrying a documentation URL naming another
# version, and refused the tag.  The matching one is no safer: the dev bump
# rewrites every such URL in the tree, so it would retarget an assertion at a
# version the fixture beside it never wrote.
RELEASE_MM = "2.1"
STALE_MM = "2.0"


def _make_tree(
    root: Path,
    *,
    pyproject: str | None = "2.1.0",
    version_py: str | None = "2.1.0",
    chart_version: str | None = "2.1.0",
    chart_app_version: str | None = "2.1.0",
    values_image_tag: str | None = "2.1.0",
    example_pin: str | None = "2.1.0",
    version_label: str | None = "2.1.0",
    site_url_version: str | None = RELEASE_MM,
) -> Path:
    """Build a minimal tree the script can read, and return its script path.

    The script derives REPO_ROOT from its own location, so the copy has to sit
    at `<root>/.github/scripts/` for the relative lookups to resolve.  `None`
    for any field omits that declaration entirely.

    `site_url_version` is a MAJOR.MINOR, not a full version: the documentation
    site publishes one line per minor release, so a patch keeps the previous
    segment.  It is applied to the whole tree at once, the copied script
    included -- `.github/` is one of the roots the script scans, and the
    script's own comments cite a versioned recotem.org URL, so a fixture that
    left it alone would carry a second, contradicting version this test file
    never set.  `None` therefore has to strip that URL as well to reach a tree
    with no site URL anywhere, which is the vacuity guard's case.
    """
    (root / ".github" / "scripts").mkdir(parents=True)
    (root / "src" / "recotem" / "datasource").mkdir(parents=True)
    (root / "helm" / "recotem").mkdir(parents=True)
    (root / "examples" / "k8s").mkdir(parents=True)

    script = root / ".github" / "scripts" / SCRIPT.name
    script.write_text(
        _set_site_url_version(SCRIPT.read_text(encoding="utf-8"), site_url_version),
        encoding="utf-8",
    )
    script.chmod(0o755)

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

    # Deployment pins.  A `:latest` sidecar sits alongside the pinned reference
    # so the scan has to leave a deliberately-floating tag alone; it is what
    # compose.yaml and the getting-started page use on purpose.
    deployment = "spec:\n  template:\n    metadata:\n      labels:\n"
    if version_label is not None:
        deployment += f'        app.kubernetes.io/version: "{version_label}"\n'
    deployment += "    spec:\n      containers:\n"
    deployment += "        - name: serve\n"
    if example_pin is not None:
        deployment += f"          image: ghcr.io/codelibs/recotem:{example_pin}\n"
    deployment += "        - name: warmup\n"
    deployment += "          image: ghcr.io/codelibs/recotem:latest\n"
    (root / "examples" / "k8s" / "serve-deployment.yaml").write_text(
        deployment, encoding="utf-8"
    )

    # A documentation-site URL of the kind section 4b exists for: one that
    # ships inside an error message, where a reader cannot correct it and this
    # repository cannot correct it after the fact.
    if site_url_version is not None:
        (root / "src" / "recotem" / "datasource" / "csv.py").write_text(
            "def _fail() -> None:\n"
            "    raise DataSourceError(\n"
            '        "CSV source could not be read; see "\n'
            f'        "https://recotem.org/{site_url_version}'
            '/docs/data-sources/csv.html"\n'
            "    )\n",
            encoding="utf-8",
        )

    return script


def _set_site_url_version(text: str, version: str | None) -> str:
    """Rewrite every `recotem.org/MAJOR.MINOR/` in `text`, or strip the segment.

    Mirrors `SITE_RE` in the script so a fixture cannot drift from what the
    scan actually matches.
    """
    if version is None:
        return re.sub(r"recotem\.org/[0-9]+\.[0-9]+/", "recotem.org/", text)
    return re.sub(r"recotem\.org/[0-9]+\.[0-9]+/", f"recotem.org/{version}/", text)


def _site_roots() -> list[str]:
    """The paths section 4b scans, read off the script itself.

    A hand-copied list is how the release-ready fixture below came to cover
    six of ten roots while reading as if it covered the tree.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"^SITE_ROOTS=\(([^)]*)\)", text, re.M)
    assert match, "SITE_ROOTS is no longer a literal array in the script"
    roots = match.group(1).split()
    assert roots, "SITE_ROOTS parsed as empty -- the pattern is broken"
    return roots


def _current_site_url_version() -> str:
    """The documentation line this tree belongs to: MAJOR.MINOR of its version.

    Read from the package version rather than by grepping the tree for a
    `recotem.org/X.Y/` URL, because a tree carrying a *stale* URL is exactly
    the case the caller must not normalise away.
    """
    text = (REPO_ROOT / "src" / "recotem" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "(\d+)\.(\d+)\.', text, re.M)
    assert match, "could not read MAJOR.MINOR from src/recotem/version.py"
    return f"{match.group(1)}.{match.group(2)}"


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


def test_success_message_says_urls_are_not_fetched(tmp_path: Path) -> None:
    """Section 4b reads the version segment; it never asks if the page exists.

    The distinction is not academic.  Measured on an otherwise release-ready
    tree, with two edits to the same line of README.md: a site URL on an older
    documentation line whose page is served (HTTP 200) is refused, rc=1; a URL
    on the released line naming a page that does not exist (HTTP 404) passes,
    rc=0.  So the gate is green while every URL it inspected is dead -- which
    is the state the tree was in when the local docs/ tree became site URLs.
    The success message has to say which of the two it established, or it is
    read as a link check that was never run.

    The URLs above are described rather than written out: `tests` is one of the
    roots section 4b scans, and a spelled-out URL naming another documentation
    line is, to that plain grep, indistinguishable from a real stale pointer.
    Writing one here refuses the tag -- see the module docstring.
    """
    script = _make_tree(tmp_path)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    # states what WAS established: the segment names the released line
    assert f"names the {RELEASE_MM} line" in out
    # and states what was not
    assert "RESOLVE" in out
    assert "404" in out


def test_a_url_that_names_the_right_line_but_cannot_resolve_still_passes(
    tmp_path: Path,
) -> None:
    """Pin the documented limitation, so narrowing it stays a deliberate act.

    If a future change makes section 4b fetch, this test fails and its docstring
    says what to decide: a release gate that reaches the network cannot publish
    while the documentation site is down, and this script gates both
    publish.yml and docker.yml.
    """
    script = _make_tree(tmp_path, site_url_version="2.1")
    root = script.parent.parent.parent
    csv_py = root / "src" / "recotem" / "datasource" / "csv.py"
    csv_py.write_text(
        csv_py.read_text(encoding="utf-8").replace(
            "/docs/data-sources/csv", "/docs/no-such-page-here"
        ),
        encoding="utf-8",
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, (
        "section 4b now refuses a URL on the correct line whose page does not "
        "exist.  If that is intended, decide first whether a release may be "
        "blocked by the documentation site being unreachable:\n"
        + proc.stdout
        + proc.stderr
    )


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


def test_stale_version_label_is_refused(tmp_path: Path) -> None:
    """`app.kubernetes.io/version` is a version declaration like any other."""
    script = _make_tree(tmp_path, version_label="2.0.0")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "app.kubernetes.io/version" in proc.stdout


def test_every_stale_pin_is_named_in_one_run(tmp_path: Path) -> None:
    """One run, one fix pass -- the same contract the version pins have."""
    script = _make_tree(tmp_path, example_pin="2.0.0", version_label="1.9.9")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1
    for expected in (
        "ghcr.io/codelibs/recotem:2.0.0",
        'app.kubernetes.io/version: "1.9.9"',
    ):
        assert expected in proc.stdout, f"{expected!r} missing from:\n{proc.stdout}"


def test_latest_tag_is_not_treated_as_a_pin(tmp_path: Path) -> None:
    """`:latest` tracks the moving tag on purpose and must not be flagged.

    The examples fixture carries a `:latest` container alongside its pinned
    one; a scan that refused it would make every release unable to pass.
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
    script = _make_tree(tmp_path, example_pin=None)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "No pinned" in proc.stdout


def test_repo_deployment_pins_are_where_the_script_looks(tmp_path: Path) -> None:
    """Pins the script scans for must actually exist in this repository.

    The synthetic-tree cases above would all still pass if `examples/k8s/` were
    restructured so the real pins moved out of the scanned path.  This one
    reads the repository.  `examples/` is the whole scan now: the deployment
    page that used to carry a second copy of these pins lives in recotem-docs,
    which bumps them in its own release phase.
    """
    hits = []
    for path in (REPO_ROOT / "examples").rglob("*"):
        if not path.is_file() or path.suffix not in {".yaml", ".yml", ".md"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"ghcr\.io/codelibs/recotem:[0-9]+\.[0-9]+\.[0-9]+", text):
            hits.append(path.relative_to(REPO_ROOT))
    assert hits, (
        "no pinned ghcr.io/codelibs/recotem:X.Y.Z reference found under "
        "examples/. The script refuses this case at release time; if the pins "
        "genuinely moved, teach the script where they went."
    )


def _bump(path: Path, pattern: str, repl: str) -> None:
    text = path.read_text(encoding="utf-8")
    new, count = re.subn(pattern, repl, text, flags=re.M)
    assert count, f"{path}: nothing matched {pattern!r} -- the bump is broken"
    path.write_text(new, encoding="utf-8")


def test_a_release_ready_copy_of_this_repository_passes(tmp_path: Path) -> None:
    """The real tree, bumped the way a release bumps it, must satisfy the gate.

    Every other case here builds a synthetic tree, deliberately -- so the suite
    does not go red while the project's own version is mid-bump.  The gap that
    leaves is that nothing measures the *real* tree until a tag is pushed, and
    by then the tag exists and has to be deleted and re-pushed.  A docs PR
    reached main that way: it added `ghcr.io/codelibs/recotem:2.0.0` to
    `docs/upgrading.md`, a path the pin scan read at the time, and `v2.1.0`
    became unreachable via the documented procedure with nothing on any PR to
    say so.

    Bumping to a synthetic version keeps this version-agnostic: it asserts the
    tree is *bumpable*, not what it happens to be pinned to today.  Only
    version-shaped values move, mirroring the script's own `is_version_pin`, so
    `:latest` stays a moving reference here exactly as it does at a release.
    """
    release = "9.9.9"
    release_mm = "9.9"
    root = tmp_path / "tree"
    (root / ".github").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / ".github" / "scripts", root / ".github" / "scripts")
    # Every root section 4b scans, not a subset.  The copy used to carry
    # `helm examples pyproject.toml README.md src/recotem/version.py` alone,
    # which left `.claude`, `CLAUDE.md`, `CONTRIBUTING.md`, the rest of `src`
    # and all of `tests` outside the fixture -- so a stale documentation URL in
    # any of them passed here and refused the real tag.  Read the roots off the
    # script rather than restating them, so the fixture cannot drift from the
    # scan again.
    # `__pycache__` is skipped so the copy is the clean checkout CI tags from.
    # Bytecode embeds these URLs in compiled docstrings, and a rewrite cannot
    # reach inside it -- a developer's stale .pyc would fail this test for a
    # reason no release has.
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for rel in _site_roots():
        src = REPO_ROOT / rel
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, root / rel, dirs_exist_ok=True, ignore=ignore)
        else:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, root / rel)
    (root / "src" / "recotem").mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    # README.md is copied for section 4b rather than for any pin: it is one of
    # the places a stale documentation URL ships where nobody can correct it,
    # since PyPI renders whatever the release uploaded.
    shutil.copy(REPO_ROOT / "README.md", root / "README.md")
    shutil.copy(
        REPO_ROOT / "src" / "recotem" / "version.py",
        root / "src" / "recotem" / "version.py",
    )

    _bump(root / "pyproject.toml", r'^version = "[^"]+"', f'version = "{release}"')
    _bump(
        root / "src" / "recotem" / "version.py",
        r'^__version__ = "[^"]+"',
        f'__version__ = "{release}"',
    )
    chart = root / "helm" / "recotem" / "Chart.yaml"
    _bump(chart, r"^version: .+$", f"version: {release}")
    _bump(chart, r'^appVersion: "[^"]*"$', f'appVersion: "{release}"')
    _bump(
        root / "helm" / "recotem" / "values.yaml",
        r'^(\s+tag: )"[0-9][^"]*"',
        rf'\g<1>"{release}"',
    )

    for path in (root / "examples").rglob("*"):
        if not path.is_file() or path.suffix not in {".yaml", ".yml", ".md"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        new = re.sub(
            r"(ghcr\.io/codelibs/recotem:)v?[0-9][A-Za-z0-9_.-]*",
            rf"\g<1>{release}",
            text,
        )
        new = re.sub(
            r'(app\.kubernetes\.io/version: )"[0-9][^"]*"',
            rf'\g<1>"{release}"',
            new,
        )
        if new != text:
            path.write_text(new, encoding="utf-8")

    # Section 4b's URLs are bumped at the dev bump rather than at release, but
    # a release-ready tree is one where that already happened -- so the copy
    # gets the same rewrite the dev bump performs, across every file, not just
    # the ones carrying a deployment pin.  MAJOR.MINOR only: a patch release
    # does not create a documentation line.
    #
    # It rewrites only the line the tree *belongs to*, exactly as the runbook's
    # `s{recotem\.org/\Q${OLD_MM}\E/}{...}` does.  A blanket
    # `recotem\.org/[0-9]+\.[0-9]+/` rewrite would also normalise a URL naming
    # some *other* line -- the one thing section 4b exists to refuse -- so the
    # fixture would erase the defect before the script could see it, and this
    # test would pass on a tree the real gate refuses.  Measured: with the
    # blanket form, copying `.claude` in was not enough to make this test fail
    # on a tree carrying a genuinely stale URL.
    current_mm = _current_site_url_version()
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        new = text.replace(f"recotem.org/{current_mm}/", f"recotem.org/{release_mm}/")
        if new != text:
            path.write_text(new, encoding="utf-8")

    proc = _run(root / ".github" / "scripts" / SCRIPT.name, f"v{release}")
    assert proc.returncode == 0, (
        "a release-ready copy of this repository does not satisfy the release "
        "gate, so pushing the tag would fail after the tag already exists:\n"
        + proc.stdout
        + proc.stderr
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
    compose.yaml and the getting-started page carry on purpose.
    """
    script = _make_tree(tmp_path)
    manifest = tmp_path / "examples" / "k8s" / "serve-deployment.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "        - name: nightly\n"
        + "          image: ghcr.io/codelibs/recotem:main\n"
        + "        - name: pinned-by-digest\n"
        + "          image: ghcr.io/codelibs/recotem:sha-abc1234\n",
        encoding="utf-8",
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# Documentation-site URLs (section 4b)
#
# This repository carries no `docs/` tree: the documentation lives at
# recotem.org, in the recotem-docs repository.  What is left here are versioned
# URLs into that site, and several of them ship where a reader cannot correct
# them and this repository cannot correct them afterwards -- the text of a
# DataSourceError, the JSON Schema `recotem schema` emits for IDEs, the HELP
# string served at /v1/metrics, README.md as rendered on PyPI.  A stale segment
# there sends a user to another version's documentation.
#
# These URLs are bumped at the DEV bump, not at release -- the opposite cadence
# to the deployment pins -- which is why a release-time gate is what notices
# that the dev bump skipped them.
# ---------------------------------------------------------------------------


def test_a_site_url_naming_another_version_is_refused(tmp_path: Path) -> None:
    """The stale-documentation-line case, with the offending hit named.

    The fix is a rewrite of specific lines, so the report has to say which:
    `grep -o` output is `path:line:match`, and all three parts are load-bearing
    for an operator who has to bump them before re-tagging.
    """
    script = _make_tree(tmp_path, site_url_version=STALE_MM)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Documentation-site URLs naming another version:" in proc.stdout
    hit = f"src/recotem/datasource/csv.py:4:recotem.org/{STALE_MM}/"
    assert hit in proc.stdout, proc.stdout


def test_site_urls_on_the_released_line_pass(tmp_path: Path) -> None:
    """Control: the same tree one minor line later is what a release looks like.

    Without it, the case above would also pass if section 4b refused every
    tree it scanned.
    """
    script = _make_tree(tmp_path, site_url_version=RELEASE_MM)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"names the {RELEASE_MM} line" in proc.stdout


def test_a_patch_release_keeps_the_minor_documentation_line(tmp_path: Path) -> None:
    """v2.1.1 must accept `/2.1/`, because a patch publishes no new doc line.

    The comparison a full-version check would make -- `2.1` against `2.1.1` --
    fails on every patch release, which is most of them, and the advice the
    script prints would tell an operator to rewrite every URL in the tree to a
    documentation line that does not exist.
    """
    script = _make_tree(
        tmp_path,
        pyproject="2.1.1",
        version_py="2.1.1",
        chart_version="2.1.1",
        chart_app_version="2.1.1",
        values_image_tag="2.1.1",
        example_pin="2.1.1",
        version_label="2.1.1",
        site_url_version=RELEASE_MM,
    )
    proc = _run(script, "v2.1.1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"names the {RELEASE_MM} line" in proc.stdout


def test_no_site_url_anywhere_is_refused_rather_than_passed(tmp_path: Path) -> None:
    """The third scan gets the vacuity guard the other two have.

    Section 4b replaced the `values.yaml`-excerpt scan that died with `docs/`,
    and it is only worth that if it cannot itself decay to nothing: a rename at
    the site, or a switch to unversioned URLs, must be reported rather than
    silently shrinking what the success message vouches for.
    """
    script = _make_tree(tmp_path, site_url_version=None)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "No 'recotem.org/X.Y/' documentation URL found" in proc.stdout


def test_a_stale_site_url_and_a_stale_pin_are_reported_in_one_run(
    tmp_path: Path,
) -> None:
    """Section 6's contract, extended to the class of failure 4b added.

    A gate that exits at the first class of failure costs a tag delete and a
    re-push per round trip on the tag-triggered release path, and a tree stale
    in both ways is the ordinary state when a dev bump was skipped.
    """
    script = _make_tree(tmp_path, example_pin="2.0.0", site_url_version=STALE_MM)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    for expected in (
        "ghcr.io/codelibs/recotem:2.0.0",
        f"src/recotem/datasource/csv.py:4:recotem.org/{STALE_MM}/",
    ):
        assert expected in proc.stdout, f"{expected!r} missing from:\n{proc.stdout}"


# ---------------------------------------------------------------------------
# Documentation-site URLs must carry `.html` (section 4c)
#
# The version segment is only half of what makes one of these resolve.
# recotem.org is a VitePress build with `cleanUrls: false`, served off disk by
# nginx with no try_files fallback, so a page URL without the suffix is a hard
# 404 -- in exactly the artefacts section 4b exists for, which nobody can
# correct after upload.  Directory URLs are the deliberate exception: nginx
# resolves them to index.html, and appending `.html` there would break them.
#
# What 4c does NOT check is whether the page on the other end exists.  A
# `.html` URL naming a deleted page passes here, by design: the check is a
# string test so that a slow or unreachable docs site cannot make the project
# unreleasable.
# ---------------------------------------------------------------------------


def _write_site_url(root: Path, url: str) -> None:
    """Replace the fixture's shipped-in-an-error-message URL with *url*."""
    (root / "src" / "recotem" / "datasource" / "csv.py").write_text(
        "def _fail() -> None:\n"
        "    raise DataSourceError(\n"
        '        "CSV source could not be read; see "\n'
        f'        "{url}"\n'
        "    )\n",
        encoding="utf-8",
    )


def test_a_page_url_without_the_html_suffix_is_refused(tmp_path: Path) -> None:
    """The 404 case: a page URL on the right line, but unreachable."""
    script = _make_tree(tmp_path, site_url_version=RELEASE_MM)
    _write_site_url(tmp_path, f"https://recotem.org/{RELEASE_MM}/docs/security")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "missing the '.html' suffix" in proc.stdout
    assert f"https://recotem.org/{RELEASE_MM}/docs/security" in proc.stdout


def test_a_page_url_with_the_html_suffix_passes(tmp_path: Path) -> None:
    """Control: the same tree with the suffix present is accepted.

    Without it the case above would also pass if 4c refused every tree.
    """
    script = _make_tree(tmp_path, site_url_version=RELEASE_MM)
    _write_site_url(tmp_path, f"https://recotem.org/{RELEASE_MM}/docs/security.html")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_an_anchor_does_not_hide_a_missing_suffix(tmp_path: Path) -> None:
    """`…/security#kid-rotation` is the same 404; the anchor must not excuse it.

    The suffix belongs before the fragment, which is the half an author is most
    likely to get wrong -- the URL still *looks* deep-linked.
    """
    script = _make_tree(tmp_path, site_url_version=RELEASE_MM)
    _write_site_url(
        tmp_path, f"https://recotem.org/{RELEASE_MM}/docs/security#kid-rotation"
    )
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "missing the '.html' suffix" in proc.stdout


def test_a_scheme_less_reference_is_still_checked(tmp_path: Path) -> None:
    """A bare host-and-path reference, with no scheme, must still be caught.

    Three occurrences in this repository's own test suite are exactly that
    shape -- an ``assert`` on the host, the version segment and a page name,
    with no scheme in front -- and they are the ones most likely to rot
    unnoticed: the extensionless literal is a *prefix* of the suffixed one, so
    the assertion keeps passing after the product string is corrected, and
    quietly stops being able to catch the regression it was written for.

    Described in words rather than shown, and assembled from ``RELEASE_MM``
    below, for the same reason every other fixture in this file is: ``tests`` is
    one of the roots the real gate scans, so a spelled-out example here is a hit
    the gate reads.  This test's first draft proved it by making the gate report
    its own docstring.
    """
    script = _make_tree(tmp_path, site_url_version=RELEASE_MM)
    _write_site_url(tmp_path, f"recotem.org/{RELEASE_MM}/docs/plugin-authoring")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "missing the '.html' suffix" in proc.stdout


def test_a_directory_url_is_not_reported_as_missing_the_suffix(
    tmp_path: Path,
) -> None:
    """`…/docs/` and `…/guide/` resolve to index.html and must stay bare.

    A check that demanded the suffix everywhere would order an operator to
    break the five URLs in the tree that currently work.
    """
    script = _make_tree(tmp_path, site_url_version=RELEASE_MM)
    _write_site_url(tmp_path, f"https://recotem.org/{RELEASE_MM}/guide/")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# The tree this script reads must be the tree the tag would publish
# ---------------------------------------------------------------------------
# Sections 3-4 read the working tree; section 5 reports a fact about HEAD.
# Before this check they shared one success message. Measured at 7871f9f, whose
# committed pyproject.toml says 2.1.0.dev0: editing only the working tree made
# the script print `pyproject.toml version = 2.1.0` and `The tagged commit is
# on main.` together, and exit 0.


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
        values_image_tag="2.0.0",
    )
    _commit_everything(tmp_path)

    # Fix only the working tree. A tag here would publish 2.0.0.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "recotem"\nversion = "2.1.0"\n', encoding="utf-8"
    )
    (tmp_path / "helm" / "recotem" / "Chart.yaml").write_text(
        "apiVersion: v2\nname: recotem\ntype: application\n"
        'version: 2.1.0\nappVersion: "2.1.0"\n',
        encoding="utf-8",
    )
    values = (tmp_path / "helm" / "recotem" / "values.yaml").read_text(encoding="utf-8")
    (tmp_path / "helm" / "recotem" / "values.yaml").write_text(
        values.replace('  tag: "2.0.0"', '  tag: "2.1.0"'), encoding="utf-8"
    )

    proc = _run(script, "v2.1.0")

    assert proc.returncode == 1, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "differ from the commit" in combined, combined
    assert "pyproject.toml" in combined, combined
    assert "helm/recotem/values.yaml" in combined, combined
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


def test_this_repository_carries_no_stale_site_url() -> None:
    """Read the real tree, and read it the way section 4b does.

    ``test_a_release_ready_copy_of_this_repository_passes`` is the only other
    case that touches the repository, and it cannot see this class of failure
    for two reasons.  It copies six of the ten roots section 4b scans --
    ``.claude``, ``tests``, ``CLAUDE.md`` and ``CONTRIBUTING.md`` are not among
    them -- and before running the gate it rewrites *every* ``recotem.org/X.Y/``
    in the copy to the synthetic release, which turns a stale URL into a fresh
    one.  That is the right shape for the deployment pins, which move at
    release; it is the wrong shape for these URLs, which move at the dev bump
    and must already be correct by the time a tag is pushed.

    So check the invariant directly instead of running the script: no file
    under any scanned root may name a documentation line other than this
    tree's own.  Version-agnostic -- the expected MAJOR.MINOR is read from
    ``src/recotem/version.py`` -- so this does not go red during a bump.

    Measured before this test existed: exactly one file in the tree carried a
    concrete non-current URL, and ``check-release-tag.sh v2.1.0`` on an
    otherwise release-ready copy exited 1 on it.  That gate runs inside
    ``publish.yml`` and, on tag runs, ``docker.yml`` -- both triggered by the
    tag -- so the failure would have arrived after the tag was pushed.
    """
    version_py = (REPO_ROOT / "src" / "recotem" / "version.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'__version__ = "(\d+)\.(\d+)', version_py)
    assert match, "could not read MAJOR.MINOR from src/recotem/version.py"
    expected_mm = f"{match.group(1)}.{match.group(2)}"

    # Mirrors SITE_ROOTS in the script.  A root added there and not here makes
    # this test vouch for less than the gate checks, so keep them in step.
    roots = (
        "src",
        "tests",
        "examples",
        "helm",
        ".claude",
        ".github",
        "README.md",
        "CLAUDE.md",
        "CONTRIBUTING.md",
        "pyproject.toml",
    )
    pattern = re.compile(r"recotem\.org/(\d+\.\d+)/")

    def _files() -> list[Path]:
        out: list[Path] = []
        for rel in roots:
            path = REPO_ROOT / rel
            if path.is_file():
                out.append(path)
            elif path.is_dir():
                out.extend(
                    p
                    for p in path.rglob("*")
                    if p.is_file()
                    and not p.is_symlink()
                    and "__pycache__" not in p.parts
                )
        return out

    stale: list[str] = []
    total = 0
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for found in pattern.finditer(line):
                total += 1
                if found.group(1) != expected_mm:
                    rel_path = path.relative_to(REPO_ROOT)
                    stale.append(f"{rel_path}:{lineno}: {found.group(0)}")

    # The gate's own vacuity guard, restated: finding nothing means the scan
    # stopped matching, not that there is nothing to check.
    assert total, (
        "no recotem.org/MAJOR.MINOR/ URL found under any scanned root, so this "
        "test is vouching for nothing. Either the URLs moved or the pattern "
        "stopped matching what the script matches."
    )
    assert not stale, (
        f"these name a documentation line other than {expected_mm}, which is "
        "what check-release-tag.sh refuses a tag over -- and it runs on the "
        "tag, so the refusal arrives after the tag exists:\n  " + "\n  ".join(stale)
    )


def test_compiled_modules_beside_the_source_do_not_fail_the_scan(
    tmp_path: Path,
) -> None:
    """A `__pycache__` in the tree must not be read as a stale URL.

    `src` and `tests` are scanned roots, and the docstrings this scan reads get
    compiled into the `.pyc` files that sit beside every module once the suite
    has run.  grep matches them, and with `-o` prints `Binary file <path>
    matches` rather than a URL -- a line with no `recotem.org/` in it, which the
    loop then reads as a MAJOR.MINOR of its own and reports as stale.

    CI checks out clean, so this never fires there.  The local invocation the
    script's own usage line documents (`bash .github/scripts/check-release-tag.sh
    v2.1.0   # before tagging`) fires it every time, which is precisely when a
    maintainer is trying to learn whether the tag will be refused -- and the
    answer they get is one spurious failure per compiled module.
    """
    script = _make_tree(tmp_path)
    cache = tmp_path / "src" / "recotem" / "datasource" / "__pycache__"
    cache.mkdir(parents=True)
    # A NUL byte is what makes grep call a file binary; the URL beside it is
    # what a real .pyc carries, since the scan's own targets are docstrings.
    (cache / "csv.cpython-313.pyc").write_bytes(
        b"\x00\x01\x02recotem.org/" + RELEASE_MM.encode() + b"/docs/x\x00"
    )

    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, (
        "a __pycache__ directory beside the source made the release gate refuse "
        "an otherwise release-ready tree:\n" + proc.stdout + proc.stderr
    )
    assert "Binary file" not in proc.stdout, proc.stdout


# ---------------------------------------------------------------------------
# A documentation URL assembled across source lines
#
# Sections 4b and 4c both grep, and grep reads one line at a time.  Python
# folds implicit string concatenation, so a URL written across a wrap point is
# a value no line of the file contains -- and neither scan can see it.  The
# tree really carries one such URL today (in a `TrainingError` message), which
# is what makes the shape ordinary rather than contrived: any formatter that
# wraps a long error string can produce it.
#
# What the scans have to read is therefore the value the *user* receives, not
# the line it was typed on.
# ---------------------------------------------------------------------------


def _write_split_site_url(root: Path, first: str, second: str) -> None:
    """Write a site URL built from two literals, one per source line.

    Python folds implicit concatenation, so what the message carries is
    ``first + second`` -- while no single line of the file contains it.  The
    comment between the halves mirrors the real occurrence in
    ``src/recotem/training/features.py``, where the anchor is explained before
    it is appended.
    """
    (root / "src" / "recotem" / "datasource" / "csv.py").write_text(
        "def _fail() -> None:\n"
        "    raise DataSourceError(\n"
        '        "CSV source could not be read; see "\n'
        f'        "{first}"\n'
        "        # the halves are split by a comment, as in the real occurrence\n"
        f'        "{second}"\n'
        "    )\n",
        encoding="utf-8",
    )


def _csv_source_lines(root: Path) -> list[str]:
    return (
        (root / "src" / "recotem" / "datasource" / "csv.py")
        .read_text(encoding="utf-8")
        .splitlines()
    )


def test_a_stale_site_url_assembled_across_lines_is_refused(tmp_path: Path) -> None:
    """The version segment itself falls across the wrap, so no line carries it."""
    script = _make_tree(tmp_path, site_url_version=RELEASE_MM)
    major, minor = STALE_MM.split(".")
    _write_split_site_url(
        tmp_path,
        f"https://recotem.org/{major}.",
        f"{minor}/docs/data-sources/csv.html",
    )

    # Prove the fixture really is the cross-line shape: a line-based scan that
    # could see this URL would make the assertion below pass for the wrong
    # reason.  This is the check the first draft of this test lacked.
    assert not any(
        f"recotem.org/{STALE_MM}/" in line for line in _csv_source_lines(tmp_path)
    ), "the fixture put the whole segment on one line, so grep would catch it"

    proc = _run(script, "v2.1.0")
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined
    assert "naming another version" in combined, combined


def test_an_extensionless_page_url_assembled_across_lines_is_refused(
    tmp_path: Path,
) -> None:
    """Each half, read alone, looks like a directory URL -- which 4c exempts.

    So the halves do not merely hide the offence from the scan; they disguise
    it as the one case the scan is required to let through.
    """
    script = _make_tree(tmp_path, site_url_version=RELEASE_MM)
    _write_split_site_url(
        tmp_path,
        f"https://recotem.org/{RELEASE_MM}/docs/",
        "data-sources/csv",
    )

    assert not any(
        f"recotem.org/{RELEASE_MM}/docs/data-sources" in line
        for line in _csv_source_lines(tmp_path)
    ), "the fixture put the whole page URL on one line"

    proc = _run(script, "v2.1.0")
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined
    assert "missing the '.html' suffix" in combined, combined


def test_a_correct_url_split_before_its_anchor_passes(tmp_path: Path) -> None:
    """Control: the shape the tree carries today must keep passing.

    ``src/recotem/training/features.py`` splits after ``.html`` and before the
    ``#anchor``, so both halves are innocent and so is the assembled value.
    Without this case the two above would also pass if the new scan simply
    refused every tree whose URLs span a wrap.
    """
    script = _make_tree(tmp_path, site_url_version=RELEASE_MM)
    _write_split_site_url(
        tmp_path,
        f"https://recotem.org/{RELEASE_MM}/docs/operations.html",
        "#recotem-train-exits-4-with-feature-axis-error.",
    )

    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_an_interpolated_version_segment_is_not_read_as_a_version(
    tmp_path: Path,
) -> None:
    """Control: a segment that is computed, not written, is nobody's stale URL.

    This file and the fixtures in it build their URLs from ``RELEASE_MM``, so a
    scan that resolved an f-string placeholder to *something* version-shaped
    would report the test suite as stale on every run.
    """
    script = _make_tree(tmp_path, site_url_version=RELEASE_MM)
    (tmp_path / "src" / "recotem" / "datasource" / "sql.py").write_text(
        "LINE = '2.0'\n"
        "\n"
        "def _fail() -> None:\n"
        '    raise DataSourceError(f"see https://recotem.org/{LINE}/docs/x")\n',
        encoding="utf-8",
    )

    proc = _run(script, "v2.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# 2b must watch every path sections 3, 4, 4b and 4c read
#
# 2b existed to stop the script vouching for bytes nobody is about to publish.
# It watched the version declarations and `examples/` only, while 4b and 4c
# read eight more roots -- so a stale URL could be committed, repaired in the
# working tree alone, and the script would print "every recotem.org/
# documentation URL in the tree names the <line> line" and "Those files are
# committed, so the lines above describe the tree v2.1.0 would publish".  Both
# false, and rc=0.
#
# CI is unaffected: a fresh checkout is clean.  This is the local pre-tag
# rehearsal, which the script's own comment calls the run "the procedure tells
# an operator to trust".
# ---------------------------------------------------------------------------


def _write_readme_site_url(root: Path, url: str) -> None:
    (root / "README.md").write_text(f"# recotem\n\nSee {url}\n", encoding="utf-8")


@requires_git
@pytest.mark.parametrize(
    ("relpath", "write"),
    [
        ("README.md", _write_readme_site_url),
        ("src/recotem/datasource/csv.py", _write_site_url),
    ],
)
def test_an_uncommitted_repair_to_a_scanned_file_is_refused(
    tmp_path: Path, relpath: str, write: object
) -> None:
    script = _make_tree(tmp_path)
    write(tmp_path, f"https://recotem.org/{STALE_MM}/docs/security.html")
    _commit_everything(tmp_path)

    # Repair the working tree and nothing else.  The commit -- the thing a tag
    # names -- still carries the stale URL.
    write(tmp_path, f"https://recotem.org/{RELEASE_MM}/docs/security.html")
    committed = _git(tmp_path, "show", f"HEAD:{relpath}").stdout
    assert f"recotem.org/{STALE_MM}/" in committed, committed

    proc = _run(script, "v2.1.0")
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined
    assert "differ from the commit" in combined, combined
    assert relpath in combined, combined
    # The worktree-derived claims must not be printed at all: printing them is
    # what made the old behaviour look correct.
    assert "OK:" not in combined, combined
    assert "Those files are committed" not in combined, combined


# ---------------------------------------------------------------------------
# An unreadable pyproject.toml is named, not traced
# ---------------------------------------------------------------------------


def test_pyproject_without_a_project_version_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """It already failed closed; what it did not do was say what was wrong.

    A `KeyError: 'version'` traceback from a heredoc names neither the file nor
    the key, and reads like a broken script rather than a malformed manifest.
    """
    script = _make_tree(tmp_path, pyproject=None)
    proc = _run(script, "v2.1.0")
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined
    assert "Traceback" not in combined, combined
    assert "Cannot read the project version from pyproject.toml" in combined, combined


def test_malformed_pyproject_toml_is_refused_by_name(tmp_path: Path) -> None:
    script = _make_tree(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project\nname =", encoding="utf-8")
    proc = _run(script, "v2.1.0")
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined
    assert "Traceback" not in combined, combined
    assert "Cannot read the project version from pyproject.toml" in combined, combined


# ---------------------------------------------------------------------------
# The headline names every class of failure the body reports
#
# `::error::` is the line an operator reads first, and on a tree whose only
# fault was a documentation URL it used to read `Tag 'v2.1.0'.` -- a sentence
# with no predicate.  The two URL classes populated the body and no clause.
# ---------------------------------------------------------------------------


def test_a_stale_site_url_is_named_in_the_headline(tmp_path: Path) -> None:
    script = _make_tree(tmp_path, site_url_version=STALE_MM)
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "::error::Tag 'v2.1.0' names another documentation line." in proc.stdout


def test_an_extensionless_site_url_is_named_in_the_headline(tmp_path: Path) -> None:
    script = _make_tree(tmp_path, site_url_version=RELEASE_MM)
    _write_site_url(tmp_path, f"https://recotem.org/{RELEASE_MM}/docs/security")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert (
        "::error::Tag 'v2.1.0' carries a documentation URL that cannot resolve."
        in proc.stdout
    )


def test_the_headline_joins_several_classes(tmp_path: Path) -> None:
    """Control: the clause list still reads as one sentence, not a list."""
    script = _make_tree(tmp_path, site_url_version=STALE_MM, pyproject="2.0.0")
    proc = _run(script, "v2.1.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert (
        "::error::Tag 'v2.1.0' names another documentation line, and does not "
        "match the project version: pyproject.toml (2.0.0)." in proc.stdout
    )
