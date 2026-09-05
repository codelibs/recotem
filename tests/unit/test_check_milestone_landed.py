"""Behavioural tests for ``check-milestone-landed.sh``.

The wiring tests in ``test_workflow_gates.py`` assert that this gate is CALLED:
the script exists, ``publish.yml`` invokes it, the checkout is deep, the job can
read pull requests.  Every one of them still passes if the ancestry comparison
inside the script is wrong or deleted — the gate would be proven to run and not
proven to work, which is the same shape as the defect the gate exists to catch.

These tests drive the real script against synthetic repositories, with a fake
``gh`` on ``PATH`` supplying canned API responses.  The script is not modified
to accommodate them: the `gh` calls it really makes are the ones intercepted, so
the code path under test is the shipped one.

The fixture reproduces #245's topology exactly — a side branch merged into
``main``, then committed to again afterwards. ``test_stranded_commit_is_refused``
is the positive control: without it the passing cases prove only that the script
can say OK, which is also what a script that checks nothing says.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-milestone-landed.sh"

_BASH = shutil.which("bash")
requires_bash = pytest.mark.skipif(_BASH is None, reason="bash not on PATH")

# A fake `gh` that answers the three calls the script makes, from files the
# test writes.  Anything unrecognised exits non-zero, so a future call the
# fixture does not model shows up as a failure rather than an empty string.
_FAKE_GH = """#!/usr/bin/env bash
case "$*" in
    *milestones*)   cat "${FAKE_GH_DIR}/milestone_count" ;;
    *"pr list"*|*"--state merged"*) cat "${FAKE_GH_DIR}/pr_list" ;;
    *"pr view"*)
        for arg in "$@"; do
            case "${arg}" in
                ''|*[!0-9]*) ;;
                *) n="${arg}" ;;
            esac
        done
        if [ -f "${FAKE_GH_DIR}/pr_${n}_oid" ]; then
            cat "${FAKE_GH_DIR}/pr_${n}_oid"
        else
            echo ""
        fi
        ;;
    *) echo "fake gh: unhandled call: $*" >&2; exit 97 ;;
esac
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str, content: str) -> str:
    (repo / "file.txt").write_text(content, encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """#245's topology: a side branch merged to main, then committed to again.

    The stranded commit is a real object — ``git show`` renders it in full — and
    is not reachable from ``main``. That combination is the whole difficulty.
    """
    r = tmp_path / "repo"
    (r / ".github").mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "test@example.invalid")
    _git(r, "config", "user.name", "Test")
    _commit(r, "root", "root\n")

    _git(r, "checkout", "-q", "-b", "side")
    landed = _commit(r, "landed via the side branch", "root\nside\n")
    _git(r, "checkout", "-q", "main")
    _git(r, "merge", "-q", "--no-ff", "-m", "merge side into main", "side")

    _git(r, "checkout", "-q", "side")
    stranded = _commit(r, "merged into side AFTER it landed", "root\nside\nlate\n")
    _git(r, "checkout", "-q", "main")

    fake = r / ".fakegh"
    fake.mkdir()
    (fake / "gh").write_text(_FAKE_GH, encoding="utf-8")
    (fake / "gh").chmod(0o755)
    (fake / "milestone_count").write_text("1\n", encoding="utf-8")
    (fake / "pr_list").write_text("", encoding="utf-8")

    (r / ".landed").write_text(landed, encoding="utf-8")
    (r / ".stranded").write_text(stranded, encoding="utf-8")
    return r


def _sha(repo: Path, which: str) -> str:
    return (repo / f".{which}").read_text(encoding="utf-8").strip()


def _run(
    repo: Path, tag: str = "v2.1.0", env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    fake = repo / ".fakegh"
    env = dict(os.environ)
    env["PATH"] = f"{fake}{os.pathsep}{env['PATH']}"
    env["FAKE_GH_DIR"] = str(fake)
    if env_extra:
        env.update(env_extra)
    # --reland-file is passed explicitly.  The script resolves the record
    # relative to its OWN location, so a file written into the fixture repo is
    # never read: without this the re-land tests silently exercise the
    # production .github/relanded-prs.tsv instead, and change behaviour
    # whenever that file is edited.
    args = [str(_BASH), str(SCRIPT), tag]
    reland = repo / ".github" / "relanded-prs.tsv"
    if reland.exists():
        args += ["--reland-file", str(reland)]
    return subprocess.run(args, cwd=repo, capture_output=True, text=True, env=env)


def _set_prs(repo: Path, *rows: str) -> None:
    (repo / ".fakegh" / "pr_list").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _set_replacement(repo: Path, number: int, oid: str) -> None:
    (repo / ".fakegh" / f"pr_{number}_oid").write_text(oid + "\n", encoding="utf-8")


def _reland(repo: Path, text: str) -> None:
    (repo / ".github" / "relanded-prs.tsv").write_text(text, encoding="utf-8")


@requires_bash
def test_fixture_really_strands_the_commit(repo: Path) -> None:
    """Positive control on the fixture itself.

    If the setup failed to strand anything, every assertion below would pass
    for the wrong reason and the suite would look healthy.
    """
    stranded = _sha(repo, "stranded")
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", stranded, "main"], cwd=repo
        ).returncode
        != 0
    ), "fixture did not strand the commit"
    assert (
        subprocess.run(
            ["git", "show", "--stat", stranded], cwd=repo, capture_output=True
        ).returncode
        == 0
    ), "stranded commit should still be a readable object"


@requires_bash
def test_landed_pr_passes(repo: Path) -> None:
    _set_prs(repo, f"1\t{_sha(repo, 'landed')}\ta landed change")
    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK: every merged PR in milestone '2.1.0'" in proc.stdout


@requires_bash
def test_stranded_commit_is_refused(repo: Path) -> None:
    """The case the gate exists for. Content comparison cannot see it."""
    stranded = _sha(repo, "stranded")
    _set_prs(repo, f"245\t{stranded}\tfix(sql): probe the driver the DSN routes to")
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "are marked" in proc.stderr and "NOT in the tree" in proc.stderr
    assert "#245" in proc.stderr


@requires_bash
def test_content_comparison_cannot_see_the_defect(repo: Path) -> None:
    """Why this must be ancestry: `git show` resolves a stranded commit.

    An audit that compared each merged PR's files and line counts against
    `git show <merge_sha>` cleared 43 PRs while hunting this exact defect.
    """
    stranded = _sha(repo, "stranded")
    shown = subprocess.run(
        ["git", "show", "--numstat", "--format=", stranded],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert shown.returncode == 0 and "file.txt" in shown.stdout, (
        "git show must succeed on the stranded commit -- that is precisely why "
        "a content comparison passes for a commit that never landed"
    )
    _set_prs(repo, f"245\t{stranded}\tstranded")
    assert _run(repo).returncode == 1


@requires_bash
def test_one_stranded_among_landed_ones_is_still_caught(repo: Path) -> None:
    landed, stranded = _sha(repo, "landed"), _sha(repo, "stranded")
    _set_prs(
        repo,
        f"1\t{landed}\tfine",
        f"245\t{stranded}\tstranded",
        f"3\t{landed}\talso fine",
    )
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "#245" in proc.stderr
    assert "Checked 3 merged PR(s)" in proc.stdout


@requires_bash
def test_reland_clears_only_when_the_replacement_landed(repo: Path) -> None:
    """The record is not a mute: the replacement must itself be an ancestor."""
    landed, stranded = _sha(repo, "landed"), _sha(repo, "stranded")
    _set_prs(repo, f"245\t{stranded}\tstranded")
    _reland(repo, "245\t256\tre-landed by cherry-pick\n")
    _set_replacement(repo, 256, landed)

    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "re-landed by #256" in proc.stdout


@requires_bash
def test_reland_naming_an_unlanded_replacement_still_fails(repo: Path) -> None:
    stranded = _sha(repo, "stranded")
    _set_prs(repo, f"245\t{stranded}\tstranded")
    _reland(repo, "245\t256\tclaimed but not landed\n")
    _set_replacement(repo, 256, stranded)  # replacement is itself stranded

    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "has not landed either" in proc.stderr


@requires_bash
def test_missing_merge_commit_warns_but_does_not_fail_the_gate(repo: Path) -> None:
    """A null merge commit is reported, and deliberately does NOT block.

    The name matters: an earlier version of this test was called
    ``..._is_reported_not_passed`` and asserted only that the PR appeared in
    the output, never checking the exit code — so it claimed blocking
    behaviour that neither the test nor the script had.  The exit code is
    asserted explicitly here, whichever way it goes.

    Warning rather than failing is a judgement, not an oversight: unlike an
    object missing from a complete clone, a null ``mergeCommit`` is not
    locally determinate — the API can return one transiently, and a release
    should not be blocked by that.  The absent-object case below IS fatal.
    """
    _set_prs(repo, "7\tnone\ta merged PR with no merge commit")
    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "could not be verified" in proc.stdout
    assert "#7" in proc.stdout


@requires_bash
def test_commit_absent_from_a_complete_clone_fails_the_gate(repo: Path) -> None:
    """Fatal, because it is a fail-open on this gate's own motivating case.

    The clone is complete (a shallow one is refused earlier), so an absent
    object is genuinely absent.  The usual cause is that the branch it sat on
    was deleted — and a stranded merge commit lives on exactly such a branch.
    Delete `fix/probe-guard-contradicts-shipped-chart` and 26a8c3b leaves the
    clone, turning a correct "STRANDED, exit 1" into "could not be verified,
    exit 0": the gate goes quiet on #245 the moment someone tidies up merged
    branches.  Unverifiable and unreachable are the same state of knowledge —
    the release cannot be shown to contain the PR.
    """
    _set_prs(repo, "8\t" + "0" * 40 + "\tcommit from a deleted branch")
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "not in this clone" in proc.stderr


@requires_bash
def test_title_cannot_forge_a_workflow_command(repo: Path) -> None:
    """PR titles are author-controlled and are echoed into a parsed log."""
    stranded = _sha(repo, "stranded")
    _set_prs(repo, f"9\t{stranded}\t::error::forged annotation")
    proc = _run(repo)
    combined = proc.stdout + proc.stderr
    assert "::error::forged annotation" not in combined, (
        "an author-controlled title reached the log with its :: delimiter intact"
    )
    assert ";;error;;forged annotation" in combined


@requires_bash
def test_absent_milestone_fails_rather_than_passing(repo: Path) -> None:
    """No milestone means the question cannot be asked, so it must not pass.

    This is the one path where the gate could report success for a question it
    was never able to ask: rename or forget the milestone and it becomes a
    permanent no-op behind a green tick.  Fatal, with a documented opt-out.
    """
    (repo / ".fakegh" / "milestone_count").write_text("0\n", encoding="utf-8")
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "no milestone titled" in (proc.stdout + proc.stderr)


@requires_bash
def test_absent_milestone_opt_out_is_explicit(repo: Path) -> None:
    """Releasing without a milestone stays possible, but only deliberately."""
    (repo / ".fakegh" / "milestone_count").write_text("0\n", encoding="utf-8")
    proc = _run(repo, env_extra={"RECOTEM_ALLOW_NO_MILESTONE": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RECOTEM_ALLOW_NO_MILESTONE" in proc.stdout


@requires_bash
def test_shallow_clone_is_refused(repo: Path) -> None:
    """The precondition prevents a WRONG answer, not merely an unanswerable one.

    Measured on a six-commit repository, asking whether a commit that genuinely
    IS an ancestor of HEAD is one::

        full clone                           -> exit 0    correct
        shallow, ancestor object fetched in  -> exit 1    confidently WRONG
        object absent entirely               -> exit 128  loud

    The middle row is the hazard. In a shallow clone the tip object can be
    present while the connecting history is not, and ``git merge-base
    --is-ancestor`` then answers "not an ancestor" with nothing to signal that
    it could not see. Exit 1 is indistinguishable from a genuine negative, so a
    gate without this precondition would not merely fail to answer — it would
    fail a legitimate release and name the wrong PRs as stranded. Only the
    third row is loud, and the ``cat-file -e`` pre-check handles that one.

    The refusal therefore has to come BEFORE any ancestry question is asked,
    which is where the script puts it.
    """
    shallow = repo.parent / "shallow"
    # Cloned over file://, not as a local path: `--depth` is SILENTLY IGNORED
    # when cloning a local path, because git hardlinks the object store
    # instead of fetching. Measured -- a local-path `--depth 1` clone of this
    # fixture reports is-shallow false and carries all six commits. The
    # assertion below is what stops a future "simplification" to a plain path
    # from leaving this test green while testing nothing.
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow)],
        check=True,
        capture_output=True,
    )
    assert _git(shallow, "rev-parse", "--is-shallow-repository") == "true", (
        "the fixture is not actually shallow, so this test proves nothing"
    )
    shutil.copytree(repo / ".fakegh", shallow / ".fakegh", dirs_exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = f"{shallow / '.fakegh'}{os.pathsep}{env['PATH']}"
    env["FAKE_GH_DIR"] = str(shallow / ".fakegh")
    proc = subprocess.run(
        [str(_BASH), str(SCRIPT), "v2.1.0"],
        cwd=shallow,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "shallow" in proc.stdout + proc.stderr


@requires_bash
def test_non_tag_invocation_is_refused(repo: Path) -> None:
    env = dict(os.environ)
    env["PATH"] = f"{repo / '.fakegh'}{os.pathsep}{env['PATH']}"
    env["FAKE_GH_DIR"] = str(repo / ".fakegh")
    env.pop("GITHUB_REF", None)
    proc = subprocess.run(
        [str(_BASH), str(SCRIPT)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "requires a tag ref" in proc.stdout + proc.stderr


@requires_bash
def test_a_stale_record_for_a_healthy_pr_does_not_mask_it(repo: Path) -> None:
    """A record row must not become the reason a healthy PR passes.

    If a PR is reachable it should pass on its own ancestry.  Were the record
    consulted first, a stale row would keep reporting success after the PR it
    names stopped being reachable.
    """
    landed = _sha(repo, "landed")
    _set_prs(repo, f"101\t{landed}\thealthy")
    _reland(repo, "101\t256\tstale row for a PR that is fine\n")

    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "re-landed by" not in proc.stdout, (
        "a reachable PR passed via the record rather than on its own ancestry"
    )


# ---------------------------------------------------------------------------
# Reverted PRs: the half of the question ancestry cannot answer.
#
# `merge-base --is-ancestor` detects "never reached main" (#245).  A PR that
# reached main and was then reverted keeps its merge commit as an ancestor
# forever, so the ancestry test reports it LANDED while the tree has none of
# its lines.  Both end in the same false claim -- the milestone says the
# release contains something it does not -- and only one was checked.
#
# `test_ancestry_alone_cannot_see_a_revert` is the positive control: it asserts
# the blindness is real, so these tests cannot pass for the wrong reason.
# ---------------------------------------------------------------------------


def _revert(repo: Path, sha: str) -> str:
    """Revert *sha* on the current branch and return the revert commit."""
    _git(repo, "revert", "--no-edit", sha)
    return _git(repo, "rev-parse", "HEAD")


@requires_bash
def test_ancestry_alone_cannot_see_a_revert(repo: Path) -> None:
    """Positive control on the defect, not on the fix.

    The reverted commit is still an ancestor of main and `git show` still
    renders it, exactly as for a healthy PR.  Nothing about the merge commit
    distinguishes the two, which is why the check had to grow a second
    question rather than a better version of the first one.
    """
    landed = _sha(repo, "landed")
    _revert(repo, landed)

    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", landed, "main"], cwd=repo
        ).returncode
        == 0
    ), "fixture is wrong: a reverted commit must still be an ancestor"

    content = (repo / "file.txt").read_text(encoding="utf-8")
    assert "side" not in content, "fixture is wrong: the revert removed nothing"


@requires_bash
def test_reverted_pr_is_refused(repo: Path) -> None:
    """The case this check grew for. Ancestry passes; the content is gone."""
    landed = _sha(repo, "landed")
    revert = _revert(repo, landed)
    _set_prs(repo, f"259\t{landed}\tfix(release): close six gate holes")

    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "REVERTED" in proc.stderr, proc.stderr
    assert "#259" in proc.stderr
    assert revert[:12] in proc.stderr, "the report must name the revert commit"
    # A revert is not a stranding and must not be given the stranding remedy.
    assert "stacked on a branch" not in proc.stderr
    assert "move it off milestone" in proc.stderr


@requires_bash
def test_one_reverted_among_healthy_ones_is_still_caught(repo: Path) -> None:
    landed = _sha(repo, "landed")
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    _revert(repo, landed)
    _set_prs(repo, f"1\t{root}\tfine", f"259\t{landed}\tbacked out")

    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "#259" in proc.stderr and "#1" not in proc.stderr


@requires_bash
def test_a_revert_that_was_itself_reverted_is_not_reported(repo: Path) -> None:
    """ "We put it back" is not a missing change.

    Without this the gate would fail a release whose content is present,
    which is the failure mode that gets a gate switched off.
    """
    landed = _sha(repo, "landed")
    revert = _revert(repo, landed)
    _revert(repo, revert)
    assert "side" in (repo / "file.txt").read_text(encoding="utf-8"), (
        "fixture is wrong: the un-revert did not restore the content"
    )
    _set_prs(repo, f"259\t{landed}\treverted then restored")

    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@requires_bash
def test_reland_record_clears_a_reverted_pr(repo: Path) -> None:
    """The waiver works the same for both failure modes."""
    landed = _sha(repo, "landed")
    _revert(repo, landed)
    replacement = _commit(repo, "re-land of #259", "root\nside\nrelanded\n")
    _set_prs(repo, f"259\t{landed}\tbacked out")
    _set_replacement(repo, 290, replacement)
    _reland(repo, "259\t290\treverted by #276; re-landed for 2.2.0\n")

    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "re-landed by #290" in proc.stdout


@requires_bash
def test_a_reland_that_was_itself_reverted_does_not_clear(repo: Path) -> None:
    """A waiver must point at something that is in the tree, not merely merged.

    Without the revert test on the replacement, reverting the re-land would
    leave the record clearing the original -- the waiver would outlive the
    change it vouches for.
    """
    landed = _sha(repo, "landed")
    _revert(repo, landed)
    replacement = _commit(repo, "re-land of #259", "root\nside\nrelanded\n")
    _revert(repo, replacement)
    _set_prs(repo, f"259\t{landed}\tbacked out")
    _set_replacement(repo, 290, replacement)
    _reland(repo, "259\t290\tre-landed, then that was reverted too\n")

    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "#259" in proc.stderr


@requires_bash
def test_a_healthy_pr_is_not_flagged_by_an_unrelated_revert(repo: Path) -> None:
    """The trailer names a specific SHA; a nearby revert must not splash."""
    landed = _sha(repo, "landed")
    other = _commit(repo, "an unrelated change", "root\nside\nother\n")
    _revert(repo, other)
    _set_prs(repo, f"259\t{landed}\tuntouched by the revert above")

    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# The live instance, run against this repository's real history.
#
# PR #276 reverted #259 (90c96f0) and #261 (d0118fc).  Both are still
# ancestors of main, so this is the blind class sitting in the tree rather
# than a shape invented for a fixture.  Skipped rather than failed where the
# history is not available (a shallow CI clone), because "cannot see" and
# "not present" are different states of knowledge.
# ---------------------------------------------------------------------------

_LIVE_REVERTED = {
    "259": "90c96f08525b7bfbad1b99591bcea79266e73de9",
    "261": "d0118fc66ac656d0f747a34813398c8f62e0665a",
}
_LIVE_REVERT = "504905c70d6946e2fea5ba2fb5f85fdbd30275ab"


def _have_live_history() -> bool:
    if (
        subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "true"
    ):
        return False
    return all(
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=REPO_ROOT
        ).returncode
        == 0
        for sha in (*_LIVE_REVERTED.values(), _LIVE_REVERT)
    )


requires_live_history = pytest.mark.skipif(
    not _have_live_history(),
    reason="needs full history containing #259/#261 and their revert #276",
)


@requires_bash
@requires_live_history
def test_the_live_reverted_prs_still_pass_the_ancestry_test(tmp_path: Path) -> None:
    """The blindness, on this repository, stated as a fact about main."""
    for sha in _LIVE_REVERTED.values():
        assert (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=REPO_ROOT
            ).returncode
            == 0
        ), f"{sha} is expected to be a reverted-but-still-ancestor commit"


@requires_bash
@requires_live_history
def test_the_shipped_script_catches_the_live_reverted_prs(tmp_path: Path) -> None:
    """Run the real script over this repository's own history.

    The fixtures above build the shape; this one uses the instance that is
    actually in the tree, so the check is pinned to a revert someone really
    made rather than to one written to be caught.
    """
    fake = tmp_path / "fakegh"
    fake.mkdir()
    (fake / "gh").write_text(_FAKE_GH, encoding="utf-8")
    (fake / "gh").chmod(0o755)
    (fake / "milestone_count").write_text("1\n", encoding="utf-8")
    (fake / "pr_list").write_text(
        "\n".join(
            f"{number}\t{sha}\tbacked out by #276"
            for number, sha in _LIVE_REVERTED.items()
        )
        + "\n",
        encoding="utf-8",
    )
    empty_reland = tmp_path / "relanded-prs.tsv"
    empty_reland.write_text("# none\n", encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{fake}{os.pathsep}{env['PATH']}"
    env["FAKE_GH_DIR"] = str(fake)
    proc = subprocess.run(
        [str(_BASH), str(SCRIPT), "v2.2.0", "--reland-file", str(empty_reland)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "REVERTED" in proc.stderr
    assert "#259" in proc.stderr and "#261" in proc.stderr
    assert _LIVE_REVERT[:12] in proc.stderr


# ---------------------------------------------------------------------------
# No-op merges: the third class, and the one where ancestry does not merely
# fail to notice but actively vouches.
#
# A PR can merge cleanly and move no bytes.  The live shape is PR #277, titled
# "re-land of #259" and stacked on #276 which reverted #259: the branch reverts
# #259, reverts #261, then restores #259, and the first and third cancel.
# Merging it into main is clean and its net diff is empty.
#
# Measured on this repository before the check was written: of all 329
# first-parent commits on main, ZERO have an empty diff against their first
# parent.  The false-positive rate is not predicted here, it is counted.
# ---------------------------------------------------------------------------


def _empty_commit(repo: Path, message: str) -> str:
    _git(repo, "commit", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _reland_that_cancels_itself(repo: Path, sha: str) -> str:
    """The #277 shape: revert *sha*, then restore it, and merge the pair.

    The branch's net contribution to main is nothing, but it merges cleanly and
    its merge commit becomes an ancestor.
    """
    _git(repo, "checkout", "-q", "-b", "reland", sha)
    _git(repo, "revert", "--no-edit", sha)
    _git(repo, "revert", "--no-edit", "HEAD")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "re-land that cancels itself", "reland")
    return _git(repo, "rev-parse", "HEAD")


@requires_bash
def test_a_no_op_merge_is_refused(repo: Path) -> None:
    """The case ancestry vouches for rather than merely missing."""
    noop = _empty_commit(repo, "PR that changed nothing")
    _set_prs(repo, f"277\t{noop}\tfix(release): re-land of #259")

    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "moved" in proc.stderr and "no bytes" in proc.stderr
    assert "#277" in proc.stderr
    # It is neither stranded nor reverted, and must not get either remedy.
    assert "stacked on a branch" not in proc.stderr
    assert "reverted by" not in proc.stderr
    assert "rebuild it onto main" in proc.stderr


@requires_bash
def test_the_reland_that_cancels_itself_is_refused(repo: Path) -> None:
    """#277's actual shape, built rather than asserted: revert then restore."""
    landed = _sha(repo, "landed")
    merge = _reland_that_cancels_itself(repo, landed)

    assert (
        subprocess.run(["git", "diff", "--quiet", f"{merge}^", merge], cwd=repo)
    ).returncode == 0, "fixture is wrong: the merge was supposed to be a no-op"
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", merge, "main"], cwd=repo
        ).returncode
        == 0
    ), "fixture is wrong: a no-op merge is still an ancestor"

    _set_prs(repo, f"277\t{merge}\tre-land of #259")
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "#277" in proc.stderr and "no bytes" in proc.stderr


@requires_bash
def test_a_no_op_reland_record_does_not_clear_the_original(repo: Path) -> None:
    """The hole a no-op opens in the waiver, which is the worst of the three.

    Recording a no-op as the re-land of a reverted PR would clear the original
    on the strength of a PR that restored nothing -- the gate would not just
    miss the gap, it would certify it closed.
    """
    landed = _sha(repo, "landed")
    _revert(repo, landed)
    noop = _empty_commit(repo, "re-land that restored nothing")
    _set_prs(repo, f"259\t{landed}\tbacked out")
    _set_replacement(repo, 277, noop)
    _reland(repo, "259\t277\tre-landed for 2.2.0\n")

    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "#259" in proc.stderr
    assert "re-landed by #277" not in proc.stdout, (
        "a no-op cleared the PR it was recorded against"
    )


@requires_bash
def test_an_ordinary_pr_is_not_flagged_as_a_no_op(repo: Path) -> None:
    """Negative control: a real change must stay green."""
    real = _commit(repo, "a real change", "root\nside\nreal\n")
    _set_prs(repo, f"300\t{real}\tan ordinary change")

    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@requires_bash
def test_success_message_states_what_is_not_checked(repo: Path) -> None:
    """A gate that lets a reader infer completeness is worse than a loud one.

    Three exact questions are asked; a fourth -- did a later commit remove the
    change with no revert trailer -- is not, because no cheap form of it
    survives legitimate refactoring. The output has to say that itself.
    """
    _set_prs(repo, f"1\t{_sha(repo, 'landed')}\ta landed change")
    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "NOT checked" in proc.stdout, proc.stdout
    assert "This reverts commit" in proc.stdout
    assert "no-op" in proc.stdout


@requires_bash
@requires_live_history
def test_no_commit_in_this_repositorys_history_is_a_false_positive() -> None:
    """The false-positive rate, counted rather than predicted.

    A check that flags ordinary work gets switched off, which costs more than
    the case it catches. This pins the measurement the design rests on, so a
    future change that starts flagging real commits fails here rather than in
    a release.
    """
    shas = subprocess.run(
        ["git", "rev-list", "--first-parent", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert len(shas) > 300, "history looks truncated; the measurement is not meaningful"

    flagged = []
    for sha in shas:
        parents = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", sha],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()[1:]
        if not parents:
            continue
        if (
            subprocess.run(
                ["git", "diff", "--quiet", parents[0], sha], cwd=REPO_ROOT
            ).returncode
            == 0
        ):
            flagged.append(sha)

    assert not flagged, (
        "the no-op check would flag real commits in this repository's history, "
        f"so it is not the exact test it claims to be: {flagged}"
    )
