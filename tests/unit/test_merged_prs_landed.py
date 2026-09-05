"""A merged pull request whose commit never reached ``main`` must fail CI.

GitHub reports a pull request as MERGED when it is merged into *its own base
branch*.  If that base is another feature branch which is squash-merged first,
the base survives as a ref but stops being a path to ``main``, and a later merge
into it lands on an orphan.  recotem #245 failed exactly this way: base
``fix/probe-guard-contradicts-shipped-chart``, squash-merged to main as #250 at
05:51:54Z, then #245 merged into the branch at 05:57:03Z.  The PR shows MERGED,
``gh pr list --state merged`` lists it, the milestone counts it, and the driver
probe it carried shipped nowhere.

The tests below build a real git repository reproducing that topology rather
than mocking it, because the property under test is a property of git's
reachability, and a mock would only assert that the mock behaves as expected.

The distinction that matters: comparing a PR's file or line stats against
``git show <merge_sha>`` MATCHES for a stranded commit, because ``git show``
reads any object in the database whether or not a branch reaches it.  An audit
built on content comparison clears a stranded PR as healthy.  Only ancestry
separates the two cases, and ``test_content_comparison_cannot_see_the_defect``
pins that so nobody 'simplifies' this check into one that cannot work.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / ".github" / "scripts" / "check-merged-prs-landed.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _run_check(repo: Path, pairs: str) -> subprocess.CompletedProcess[str]:
    """Run the check against a fixture, with waivers explicitly emptied.

    ``--waivers`` defaults to the real ``.github/merged-pr-waivers.txt``, which
    is resolved from the script's own location rather than the working
    directory -- correct in production, but it means a fixture using a PR number
    that happens to be waived there would pass for the wrong reason. That
    actually happened while writing these tests: the fixture numbers its
    stranded commit 245, and the shipped file waives 245, so the test asserting
    that stranding fails was green against a script that had simply skipped it.
    """
    pairs_file = repo / "pairs.txt"
    pairs_file.write_text(pairs, encoding="utf-8")
    empty_waivers = repo / "no-waivers.txt"
    empty_waivers.write_text("# intentionally empty\n", encoding="utf-8")
    return subprocess.run(
        [
            "bash",
            str(_SCRIPT),
            "--from-file",
            str(pairs_file),
            "--waivers",
            str(empty_waivers),
            "--target",
            "refs/heads/main",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def stranded_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A repo reproducing #245's topology.

    Returns ``(repo, landed_sha, stranded_sha)``.  ``landed_sha`` is on main;
    ``stranded_sha`` is reachable only from the feature branch, exactly as
    ``26a8c3b`` is reachable only from
    ``origin/fix/probe-guard-contradicts-shipped-chart``.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")

    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "base")

    # A feature branch, squash-merged into main -- the shape of #250.
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "feature work")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--squash", "feature")
    _git(repo, "commit", "-qm", "feature work (#250)")
    landed = _git(repo, "rev-parse", "HEAD")

    # The branch still exists and still looks mergeable, but merging into it now
    # lands on an orphan.  This is #245.
    _git(repo, "checkout", "-q", "feature")
    (repo / "c.txt").write_text("stranded\n", encoding="utf-8")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-qm", "the fix that never shipped (#245)")
    stranded = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "main")
    return repo, landed, stranded


def test_the_stranded_commit_is_really_stranded(
    stranded_repo: tuple[Path, str, str],
) -> None:
    """Positive control for the fixture itself.

    If the fixture did not actually strand the commit, every assertion below
    would pass for the wrong reason and the check would look effective while
    testing nothing.
    """
    repo, landed, stranded = stranded_repo
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", landed, "main"]
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", stranded, "main"]
        ).returncode
        != 0
    ), "fixture did not strand the commit; the rest of this file proves nothing"


def test_a_stranded_merged_pr_fails(stranded_repo: tuple[Path, str, str]) -> None:
    repo, landed, stranded = stranded_repo
    result = _run_check(repo, f"250 {landed}\n245 {stranded}\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "#245" in result.stdout
    assert "not on" in result.stdout
    assert "#250" not in result.stdout.split("reachable only from")[0].split("\n")[-1]


def test_all_landed_passes(stranded_repo: tuple[Path, str, str]) -> None:
    repo, landed, _ = stranded_repo
    result = _run_check(repo, f"250 {landed}\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_an_empty_pr_list_fails_rather_than_reporting_success(
    stranded_repo: tuple[Path, str, str],
) -> None:
    """A run that examined nothing is a broken check, not a clean repository.

    An API change, a wrong ``--jq``, or a bad ``--limit`` all yield an empty
    list, and "zero stranded out of zero examined" reads identically to health.
    docs #37 spent a whole release cycle red for an unrelated infrastructure
    reason while looking like a monitor doing its job; the inverse -- green
    while doing nothing -- is worse.
    """
    repo, _, _ = stranded_repo
    result = _run_check(repo, "\n  \n")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no merged pull requests were examined" in result.stdout


def test_an_unresolvable_commit_fails_rather_than_being_skipped(
    stranded_repo: tuple[Path, str, str],
) -> None:
    """A commit this clone cannot see is not evidence of health.

    Deleting the stranded branch is a normal tidy-up and makes the object
    unreachable; treating "not found" as "fine" would hide the defect at the
    exact moment it becomes hardest to recover.
    """
    repo, landed, _ = stranded_repo
    absent = "0" * 40
    result = _run_check(repo, f"250 {landed}\n999 {absent}\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "#999" in result.stdout


def test_a_missing_target_ref_fails(stranded_repo: tuple[Path, str, str]) -> None:
    """A shallow or single-branch checkout cannot decide ancestry."""
    repo, landed, _ = stranded_repo
    pairs_file = repo / "pairs.txt"
    pairs_file.write_text(f"250 {landed}\n", encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            str(_SCRIPT),
            "--from-file",
            str(pairs_file),
            "--target",
            "refs/heads/no-such-branch",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "does not resolve" in result.stdout


def _run_with_waivers(
    repo: Path, pairs: str, waivers: str
) -> subprocess.CompletedProcess[str]:
    (repo / "pairs.txt").write_text(pairs, encoding="utf-8")
    (repo / "waivers.txt").write_text(waivers, encoding="utf-8")
    return subprocess.run(
        [
            "bash",
            str(_SCRIPT),
            "--from-file",
            str(repo / "pairs.txt"),
            "--waivers",
            str(repo / "waivers.txt"),
            "--target",
            "refs/heads/main",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_a_waived_stranded_pr_passes(stranded_repo: tuple[Path, str, str]) -> None:
    """Re-landing does not un-strand the original commit.

    The re-land is a NEW commit; the original merge commit stays unreachable
    forever. Without a waiver this check would go permanently red the moment it
    succeeded at its job, which is precisely how docs #37 stopped being read.
    """
    repo, landed, stranded = stranded_repo
    result = _run_with_waivers(
        repo,
        f"250 {landed}\n245 {stranded}\n",
        "245 re-landed as #256\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Waived" in result.stdout and "245" in result.stdout


def test_a_waiver_without_a_reason_is_rejected(
    stranded_repo: tuple[Path, str, str],
) -> None:
    repo, landed, stranded = stranded_repo
    result = _run_with_waivers(repo, f"245 {stranded}\n", "245\n")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no reason" in result.stdout


def test_a_stale_waiver_is_rejected(stranded_repo: tuple[Path, str, str]) -> None:
    """A waiver that no longer describes anything is a hole.

    If the waived PR turns out to be on the target branch after all, the entry
    is silently protecting nothing -- and would go on protecting nothing if that
    number were ever reused or mistyped.
    """
    repo, landed, _ = stranded_repo
    result = _run_with_waivers(repo, f"250 {landed}\n", "250 not actually stranded\n")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "stale waiver" in result.stdout


def test_the_shipped_waiver_file_is_honest() -> None:
    """Every entry in the real waiver file must have a reason.

    Cheap, and it runs without network or a fixture, so the shipped list cannot
    drift into unexplained numbers.
    """
    waivers = _ROOT / ".github" / "merged-pr-waivers.txt"
    if not waivers.exists():
        pytest.skip("no waiver file in this tree")
    entries = [
        line
        for line in waivers.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert entries, "waiver file exists but lists nothing; delete it instead"
    for line in entries:
        number, _, reason = line.partition(" ")
        assert number.isdigit(), f"not a PR number: {line!r}"
        assert reason.strip(), f"waiver for #{number} has no reason"


def test_content_comparison_cannot_see_the_defect(
    stranded_repo: tuple[Path, str, str],
) -> None:
    """Pin the reason this check must be ancestry and not content.

    ``git show`` reads any object in the database, reachable or not, so an audit
    that compares a PR's diff against its merge commit reports a stranded PR as
    healthy.  That mistake was made once already while hunting this very defect,
    clearing 43 PRs on that basis.  If someone later 'simplifies' this script
    into a content comparison, this test says why not.
    """
    repo, _, stranded = stranded_repo
    shown = subprocess.run(
        ["git", "-C", str(repo), "show", "--stat", "--format=", stranded],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "c.txt" in shown, (
        "git show resolved the stranded commit's contents -- which is exactly "
        "why content comparison cannot detect stranding"
    )
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", stranded, "main"]
        ).returncode
        != 0
    ), "ancestry, unlike content, does detect it"
