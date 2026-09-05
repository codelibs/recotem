"""A merged PR whose commit never reached main must fail the check.

#245 was merged, green, milestoned 2.1.0, and contributed nothing: it targeted
``fix/probe-guard-contradicts-shipped-chart`` and merged into that branch five
minutes after #250 had merged the same branch into ``main``.  350 lines,
including a 199-line test file, were absent from the release.

These tests drive ``check-merged-prs-landed.sh`` against synthetic repositories
built here, so the detection is exercised with no token and no network.  The
central one is ``test_stranded_commit_is_refused``: it strands a commit on an
abandoned branch **on purpose** and asserts the script fails.  Without that
positive control the passing cases below prove only that the script can say
"OK", which is what a check that looks at nothing also says.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-merged-prs-landed.sh"

_BASH = shutil.which("bash")
requires_bash = pytest.mark.skipif(_BASH is None, reason="bash not on PATH")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, message: str, content: str) -> str:
    (repo / "file.txt").write_text(content, encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with a `main`, one landed commit, and one stranded commit.

    The stranded commit reproduces #245's shape exactly: a side branch that was
    merged into `main`, then committed to again afterwards.  The later commit
    is a real object in the database -- `git show` renders it in full -- and is
    not reachable from `main`.
    """
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "test@example.invalid")
    _git(r, "config", "user.name", "Test")
    _commit(r, "root", "root\n")

    _git(r, "checkout", "-q", "-b", "side")
    landed = _commit(r, "landed via the side branch", "root\nside\n")

    # `main` takes the side branch, exactly as #250 took the probe-guard branch.
    _git(r, "checkout", "-q", "main")
    _git(r, "merge", "-q", "--no-ff", "-m", "merge side into main", "side")

    # ...and only THEN does another PR merge into the side branch.  The branch
    # is still alive; it is no longer a path to main.
    _git(r, "checkout", "-q", "side")
    stranded = _commit(
        r, "merged into side AFTER side had landed", "root\nside\nlate\n"
    )
    _git(r, "checkout", "-q", "main")

    r_landed, r_stranded = r / ".landed", r / ".stranded"
    r_landed.write_text(landed, encoding="utf-8")
    r_stranded.write_text(stranded, encoding="utf-8")
    return r


def _run(repo: Path, stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_BASH), str(SCRIPT), "--stdin", "main"],
        cwd=repo,
        input=stdin,
        capture_output=True,
        text=True,
    )


def _sha(repo: Path, which: str) -> str:
    return (repo / f".{which}").read_text(encoding="utf-8").strip()


def _waive(repo: Path, text: str) -> None:
    d = repo / ".github"
    d.mkdir(exist_ok=True)
    (d / "merged-pr-waivers.txt").write_text(text, encoding="utf-8")


@requires_bash
def test_landed_commit_is_accepted(repo: Path) -> None:
    proc = _run(repo, f"1\tside\t{_sha(repo, 'landed')}\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK: all 1 merged pull requests are accounted for" in proc.stdout


@requires_bash
def test_stranded_commit_is_refused(repo: Path) -> None:
    """The positive control: the case the check exists for must fail.

    Note the commit is a perfectly good object -- this is why a content
    comparison against ``git show`` passes for it, and why the absence has to
    be detected by reachability rather than by inspecting the commit.
    """
    stranded = _sha(repo, "stranded")
    # It really is in the database: the wrong check would be satisfied here.
    assert (
        subprocess.run(
            ["git", "show", "--stat", stranded], cwd=repo, capture_output=True
        ).returncode
        == 0
    )

    proc = _run(repo, f"2\tside\t{stranded}\n")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "NOT reachable from main" in proc.stdout
    assert f"#2 {stranded}" in proc.stdout


@requires_bash
def test_one_stranded_among_several_is_still_caught(repo: Path) -> None:
    """A single loss must not be diluted by the PRs around it that are fine."""
    landed, stranded = _sha(repo, "landed"), _sha(repo, "stranded")
    proc = _run(
        repo,
        f"1\tside\t{landed}\n2\tside\t{stranded}\n3\tmain\t{landed}\n",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"#2 {stranded}" in proc.stdout
    assert "#1" not in proc.stdout and "#3" not in proc.stdout
    assert "Checked 3 merged pull requests." in proc.stdout


@requires_bash
def test_empty_input_is_refused_rather_than_passed(repo: Path) -> None:
    """A scan that finds nothing must fail, not vouch for an empty set."""
    proc = _run(repo, "")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "No merged pull requests found" in proc.stdout


@requires_bash
def test_missing_merge_commit_is_refused(repo: Path) -> None:
    """A merged PR with no recorded merge commit is unverifiable, not fine."""
    proc = _run(repo, "4\tside\t\n")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "no merge commit recorded" in proc.stdout


@requires_bash
def test_commit_absent_from_the_repository_is_named(repo: Path) -> None:
    """A deleted branch takes its commits with it; say so rather than crash."""
    proc = _run(repo, "5\tgone\t" + "0" * 40 + "\n")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "not in this repository" in proc.stdout


@requires_bash
def test_waiver_is_satisfied_once_the_replacement_is_on_main(repo: Path) -> None:
    """A repaired strand goes green by itself, with no follow-up edit.

    This is the arm that keeps the check from being permanently red. Re-landing
    #245's content under #256 leaves #245's own merge commit unreachable for
    good, so without this the alarm would never clear and would be muted --
    which is the failure this check exists to prevent, by another route.
    """
    landed, stranded = _sha(repo, "landed"), _sha(repo, "stranded")
    _waive(repo, "245 relanded-by 256\n")
    proc = _run(repo, f"245\tside\t{stranded}\n256\tmain\t{landed}\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "#245 re-landed by #256" in proc.stdout
    assert "accounted for" in proc.stdout


@requires_bash
def test_waiver_does_not_help_while_the_replacement_is_also_absent(repo: Path) -> None:
    """A waiver is not a mute: naming an unlanded replacement changes nothing.

    This is what stops the file being used to wave through work that has not
    arrived. It is also the state of `main` at the time this was written --
    #256 was still open -- so the live run correctly stayed red.
    """
    stranded = _sha(repo, "stranded")
    _waive(repo, "245 relanded-by 256\n")
    # #256 present in the list but itself unreachable.
    proc = _run(repo, f"245\tside\t{stranded}\n256\tside\t{stranded}\n")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "waiver names" in proc.stdout
    assert "#256, which is not on main either" in proc.stdout


@requires_bash
def test_waiver_naming_an_unknown_pr_does_not_satisfy(repo: Path) -> None:
    """A replacement that is not in the milestone at all must not count."""
    stranded = _sha(repo, "stranded")
    _waive(repo, "245 relanded-by 999\n")
    proc = _run(repo, f"245\tside\t{stranded}\n")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "#999" in proc.stdout


@requires_bash
def test_malformed_waiver_line_is_refused(repo: Path) -> None:
    """A typo must fail loudly rather than silently waive nothing -- or all."""
    _waive(repo, "245 see-pr-256\n")
    proc = _run(repo, f"1\tside\t{_sha(repo, 'landed')}\n")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Malformed line" in proc.stdout


@requires_bash
def test_waiver_comments_and_blank_lines_are_ignored(repo: Path) -> None:
    _waive(repo, "# a comment\n\n245 relanded-by 256\n")
    landed, stranded = _sha(repo, "landed"), _sha(repo, "stranded")
    proc = _run(repo, f"245\tside\t{stranded}\n256\tmain\t{landed}\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_shipped_waiver_file_is_well_formed() -> None:
    """Every waiver in the repo parses, and none is a bare mute."""
    path = REPO_ROOT / ".github" / "merged-pr-waivers.txt"
    if not path.exists():
        pytest.skip("no waivers recorded")
    entries = [
        line.split()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert entries, (
        "the waiver file exists but records nothing; delete it rather than "
        "shipping an empty exception list"
    )
    for parts in entries:
        assert len(parts) == 3 and parts[1] == "relanded-by", (
            f"malformed waiver {parts!r}; expected "
            "'<stranded PR> relanded-by <replacement PR>'"
        )
        assert parts[0].isdigit() and parts[2].isdigit(), (
            f"waiver {parts!r} must name two PR numbers"
        )


@requires_bash
def test_unresolvable_ref_is_refused(repo: Path) -> None:
    """A shallow clone resolves no ref; that must fail loudly, not pass."""
    proc = subprocess.run(
        [str(_BASH), str(SCRIPT), "--stdin", "no/such/ref"],
        cwd=repo,
        input="1\tside\tHEAD\n",
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "does not resolve to a commit" in proc.stdout
    assert "fetch-depth: 0" in proc.stdout


def test_the_workflow_actually_runs_this_script() -> None:
    """A gate nobody invokes is not a gate.

    P8 measured a workflow this round that could have been dead for a whole
    release cycle without anyone noticing.  This pins the wiring: the script
    exists, is executable, and is named by a workflow that triggers on pushes
    to main rather than on a schedule alone.
    """
    assert SCRIPT.exists(), f"{SCRIPT} is missing"
    assert SCRIPT.stat().st_mode & 0o111, f"{SCRIPT} is not executable"

    workflows = (REPO_ROOT / ".github" / "workflows").glob("*.yml")
    callers = [
        path for path in workflows if SCRIPT.name in path.read_text(encoding="utf-8")
    ]
    assert callers, f"no workflow invokes {SCRIPT.name}; the check would never run"
    text = "\n".join(p.read_text(encoding="utf-8") for p in callers)
    assert "push:" in text, (
        f"{[p.name for p in callers]} does not trigger on push. A "
        "schedule-only gate can be dead for an entire release cycle."
    )
    assert "fetch-depth: 0" in text, (
        "the checkout must be unshallow, or merge-base has no history to "
        "resolve and the check fails for the wrong reason"
    )
