"""The milestone gate, driven against a real git repository.

The property under test is git *reachability*, so the fixture builds an actual
repository reproducing PR #245's topology rather than mocking it — a mock would
only assert that the mock behaves.  The scenario is the real one:

    main:    A ── B ───────── S(squash of the branch)
                   \\
    branch:         C ─────────────── D   <- merged AFTER the squash

``S`` carries the branch's *content*, so main looks complete.  ``D`` is a later
merge into a branch that is no longer a path to main, so ``D`` is unreachable
from main even though GitHub reports its PR as MERGED.  That is exactly what
happened: #250 squash-merged `fix/probe-guard-contradicts-shipped-chart` at
05:51:54Z and #245 merged into that same branch at 05:57:03Z.

Every test here runs the real script with ``--pr-list``, which reads the
milestone's PRs from a file instead of calling ``gh``.  No network, no
credentials, no GitHub — but the ancestry decision under test is the shipped
one, not a reimplementation.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-milestone-landed.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is required to build the fixture"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def stranded_repo(tmp_path: Path) -> dict[str, object]:
    """A repository where one merge commit is genuinely unreachable from main.

    Returns the repo path and the commits the tests reason about.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")

    (repo / "file.txt").write_text("A\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "A")

    # The feature branch, with one commit that later gets squashed to main.
    _git(repo, "checkout", "-qb", "feature")
    (repo / "feature.txt").write_text("first\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "C: first feature commit")
    c_sha = _git(repo, "rev-parse", "HEAD")

    # main squash-merges the branch: the CONTENT arrives, the commit does not.
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--squash", "-q", "feature")
    _git(repo, "commit", "-qm", "S: squash-merge of feature (like #250)")
    squash_sha = _git(repo, "rev-parse", "HEAD")

    # A later commit onto the now-orphaned branch: this is #245.
    _git(repo, "checkout", "-q", "feature")
    (repo / "stranded.txt").write_text("the fix nobody received\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "D: merged after the squash (like #245)")
    stranded_sha = _git(repo, "rev-parse", "HEAD")

    # A PR that merged into main normally, as the control.
    _git(repo, "checkout", "-q", "main")
    (repo / "landed.txt").write_text("this one shipped\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "E: a normal merge to main")
    landed_sha = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "main")
    return {
        "repo": repo,
        "stranded": stranded_sha,
        "landed": landed_sha,
        "squash": squash_sha,
        "first_feature_commit": c_sha,
    }


def _run(
    repo: Path,
    pr_rows: list[str],
    reland_rows: str | None = None,
    tag: str = "v9.9.9",
) -> subprocess.CompletedProcess[str]:
    pr_list = repo / "prs.tsv"
    pr_list.write_text("\n".join(pr_rows) + "\n")
    args = ["bash", str(SCRIPT), tag, "--pr-list", str(pr_list)]
    if reland_rows is not None:
        reland = repo / "reland.tsv"
        reland.write_text(reland_rows)
        args += ["--reland-file", str(reland)]
    return subprocess.run(args, cwd=repo, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Positive control on the FIXTURE itself.
#
# Every assertion below depends on the fixture actually stranding a commit.  If
# it silently failed to, the other tests would pass for the wrong reason and
# this whole file would be decorative.
# ---------------------------------------------------------------------------


def test_the_stranded_commit_is_really_stranded(stranded_repo) -> None:
    repo = stranded_repo["repo"]
    stranded = stranded_repo["stranded"]
    landed = stranded_repo["landed"]

    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", stranded, "main"], cwd=repo
        ).returncode
        != 0
    ), "the fixture did not strand the commit; every other test is void"

    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", landed, "main"], cwd=repo
        ).returncode
        == 0
    ), "the control commit should be reachable from main"


def test_content_comparison_cannot_see_the_defect(stranded_repo) -> None:
    """Why this must be an ancestry check and not a content check.

    A stranded commit is a perfectly ordinary object: `git show` resolves it,
    its diff applies, its files read.  Nothing about *reading* it reveals that
    main cannot reach it.  Any future "simplification" of this gate into a
    content or diff comparison would therefore pass on the exact case it exists
    to catch — which is how a first pass at this problem cleared 43 PRs.
    """
    repo = stranded_repo["repo"]
    stranded = stranded_repo["stranded"]

    shown = subprocess.run(
        ["git", "show", "--stat", stranded], cwd=repo, capture_output=True, text=True
    )
    assert shown.returncode == 0, "git show should resolve a stranded commit fine"
    assert "stranded.txt" in shown.stdout

    # And the content of the squashed work IS on main, which is what makes the
    # absence so easy to miss: the branch's earlier commit is not reachable
    # either, yet its file is present.
    assert (repo / "feature.txt").exists(), "squashed content is on main"
    assert not (repo / "stranded.txt").exists(), "the later fix is not"


# ---------------------------------------------------------------------------
# The script's verdicts.
# ---------------------------------------------------------------------------


def test_script_flags_the_stranded_pr(stranded_repo) -> None:
    repo = stranded_repo["repo"]
    result = _run(
        repo,
        [
            f"101\t{stranded_repo['landed']}\ta PR that really landed",
            f"245\t{stranded_repo['stranded']}\tthe PR that did not",
        ],
    )
    assert result.returncode == 1, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "#245" in combined
    assert "#101" not in combined, "a PR that landed must not be reported"


def test_script_passes_when_everything_landed(stranded_repo) -> None:
    """Negative control: the script must not fail on a healthy milestone."""
    repo = stranded_repo["repo"]
    result = _run(repo, [f"101\t{stranded_repo['landed']}\ta PR that really landed"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK:" in result.stdout


def test_a_reland_record_clears_it_only_when_the_replacement_landed(
    stranded_repo,
) -> None:
    """The property that makes the record a guard rather than a comment."""
    repo = stranded_repo["repo"]
    rows = [
        f"245\t{stranded_repo['stranded']}\tthe stranded PR",
        f"256\t{stranded_repo['landed']}\tthe re-land, which DID land",
    ]

    cleared = _run(repo, rows, reland_rows="245\t256\tre-landed\n")
    assert cleared.returncode == 0, cleared.stdout + cleared.stderr
    assert "re-landed by #256" in cleared.stdout

    # #999 is still open, so it has no merge commit at all — the shape the real
    # record had while #256 was still under review.  The waiver must not clear
    # #245 on the strength of a PR that has not merged.
    not_cleared = _run(repo, rows, reland_rows="245\t999\tclaims a re-land\n")
    assert not_cleared.returncode == 1, "a replacement that never landed must not clear"
    combined = not_cleared.stdout + not_cleared.stderr
    assert "has not landed either" in combined
    assert "#245" in combined


def test_a_record_for_a_healthy_pr_does_not_hide_a_later_regression(
    stranded_repo,
) -> None:
    """A stale record must not mask a PR that is fine, nor one that is not."""
    repo = stranded_repo["repo"]
    result = _run(
        repo,
        [f"101\t{stranded_repo['landed']}\thealthy"],
        reland_rows="101\t256\tstale record for a PR that is fine\n",
    )
    assert result.returncode == 0
    assert "re-landed by" not in result.stdout, (
        "a healthy PR should pass on its own ancestry, not via the record"
    )


def test_an_unresolvable_commit_is_reported_not_passed(stranded_repo) -> None:
    """A commit absent from the clone is unverifiable — say so, do not pass it."""
    repo = stranded_repo["repo"]
    absent = "0" * 40
    result = _run(repo, [f"321\t{absent}\ta PR whose commit is not here"])
    combined = result.stdout + result.stderr
    assert "#321" in combined
    assert "could not be verified" in combined or "not in this clone" in combined


def test_a_shallow_clone_refuses_rather_than_passing(stranded_repo, tmp_path) -> None:
    """A shallow clone cannot answer the question, so it must not answer 'fine'."""
    repo = stranded_repo["repo"]
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow)],
        check=True,
        capture_output=True,
    )
    # The script resolves its record file relative to its own location, so run
    # the real script from inside the shallow clone.
    result = subprocess.run(
        ["bash", str(SCRIPT), "v9.9.9"],
        cwd=shallow,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "shallow" in (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# The one way this gate could report success for a question it never asked.
#
# Everything above runs in --pr-list mode, which skips the milestone lookup
# entirely — so none of it covers what happens when the milestone is missing.
# A gate whose setup silently no-ops looks exactly like a gate with nothing to
# report, which is the shape of the defect it exists to catch, so these two
# drive the real lookup through a stub `gh` on PATH.
# ---------------------------------------------------------------------------


def _stub_gh(tmp_path: Path, milestone_count: int) -> dict[str, str]:
    """A `gh` on PATH that reports *milestone_count* matching milestones."""
    bindir = tmp_path / "stubbin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        f'  *milestones*) echo "{milestone_count}" ;;\n'
        '  *"pr list"*|*pr*list*) : ;;\n'
        "  *) : ;;\n"
        "esac\n"
    )
    gh.chmod(0o755)
    import os

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    return env


def test_a_missing_milestone_fails_rather_than_passing(stranded_repo, tmp_path) -> None:
    """No milestone means the question cannot be asked, so it must not pass."""
    repo = stranded_repo["repo"]
    result = subprocess.run(
        ["bash", str(SCRIPT), "v9.9.9"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_stub_gh(tmp_path, 0),
    )
    assert result.returncode == 1, (
        "a release whose milestone does not exist was verified as fine; the "
        "gate would silently become a no-op if the milestone were renamed"
    )
    assert "no milestone titled" in (result.stdout + result.stderr)


def test_the_missing_milestone_opt_out_is_explicit(stranded_repo, tmp_path) -> None:
    """Releasing without a milestone stays possible, but only deliberately."""
    repo = stranded_repo["repo"]
    env = _stub_gh(tmp_path, 0)
    env["RECOTEM_ALLOW_NO_MILESTONE"] = "1"
    result = subprocess.run(
        ["bash", str(SCRIPT), "v9.9.9"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RECOTEM_ALLOW_NO_MILESTONE" in result.stdout
