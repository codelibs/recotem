"""The release runbook must describe the workflows and the gate it points at.

`.claude/skills/release-recotem/` is the document a release manager follows
*while* releasing, and two of its claims had gone stale against the tree:

- It said `build` is ``needs: [smoke, trivy]`` in five places, while
  `docker.yml` says ``needs: [test, smoke, trivy]``.  Phase 1 item 6 tells the
  reader to *verify that exact wiring*, so a reader doing what they are told
  either "fixes" a correct workflow to match a wrong runbook or concludes the
  workflow is broken.  A runbook that instructs you to verify a fact it gets
  wrong is worse than one that stays silent.
- It enumerated what `check-release-tag.sh` checks as "all four places" /
  "the first three" / "all four declarations" and omitted
  `helm/recotem/values.yaml`'s `image.tag` -- the one declaration that decides
  what a cluster actually pulls, and the reason a tagged release once shipped a
  chart pointing at the previous image.  The same file contradicted itself: the
  Phase 3 comment already said "pyproject, version.py, Chart.yaml and
  values.yaml".

Both are the same failure: prose restating a fact that lives in a file next to
it.  These tests derive the fact from the file instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "release-recotem"
RUNBOOK_FILES = sorted(SKILL_DIR.rglob("*.md"))
WORKFLOWS = (
    REPO_ROOT / ".github" / "workflows" / "docker.yml",
    REPO_ROOT / ".github" / "workflows" / "publish.yml",
)
GATE = REPO_ROOT / ".github" / "scripts" / "check-release-tag.sh"

# The runbook writes a dependency claim in two shapes, and both must be seen:
#   `build` — the only job that pushes — is `needs: [test, smoke, trivy]`
#   (`build: needs: [test, smoke, trivy]`)          <- one backtick span
# Anchoring on a backtick immediately before `needs:` finds only the first, and
# the second is what the Common-mistakes table uses -- so a stale value there
# survived the first version of this test.  Match `needs:` wherever it appears
# and resolve the owner from the span or from the preceding backticked tokens.
_NEEDS_CLAIM = re.compile(r"needs:\s*(\[[^\]]*\]|[A-Za-z][A-Za-z0-9_-]*)")
_BACKTICK_SPAN = re.compile(r"`([^`]*)`")
_BACKTICKED = re.compile(r"`([A-Za-z][A-Za-z0-9_-]*)`")
_SPAN_OWNER = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*needs:")


def _real_needs() -> dict[str, set[frozenset[str]]]:
    """job name -> the set of `needs` sets it legitimately has, across workflows.

    A job name alone is ambiguous: `build` exists in both workflows with
    different dependencies (`[test, smoke, trivy]` in docker.yml, `test` in
    publish.yml).  A claim is accepted if it matches the job in *either*, which
    is weaker than pinning the workflow but still refuses a set that appears in
    neither -- which is what the stale `[smoke, trivy]` was.
    """
    out: dict[str, set[frozenset[str]]] = {}
    for path in WORKFLOWS:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job, spec in (data.get("jobs") or {}).items():
            needs = spec.get("needs")
            if needs is None:
                continue
            if isinstance(needs, str):
                needs = [needs]
            out.setdefault(job, set()).add(frozenset(needs))
    return out


def _parse_claim(raw: str) -> frozenset[str]:
    return frozenset(
        part.strip() for part in raw.strip(" []").split(",") if part.strip()
    )


def _needs_claims() -> list[tuple[Path, str, frozenset[str], str]]:
    """(file, job, claimed needs, raw text) for every `needs:` claim in the runbook.

    Whitespace is collapsed first: the prose is hard-wrapped, so a job name and
    its ``needs:`` regularly land on different lines.
    """
    jobs = set(_real_needs())
    claims: list[tuple[Path, str, frozenset[str], str]] = []
    for path in RUNBOOK_FILES:
        text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
        spans = [
            (m.start(1), m.end(1), m.group(1)) for m in _BACKTICK_SPAN.finditer(text)
        ]
        for match in _NEEDS_CLAIM.finditer(text):
            owner = None
            # `build: needs: [...]` -- the owner is inside the span itself.
            for start, end, body in spans:
                if start <= match.start() < end:
                    named = _SPAN_OWNER.match(body)
                    if named and named.group(1) in jobs:
                        owner = named.group(1)
                    break
            if owner is None:
                # ``  `build` ... is `needs: [...]`  `` -- the nearest backticked
                # job name before it.  Backticked only: `test`, `build` and
                # `guard` are ordinary English words and a bare-word scan would
                # attach a claim to whichever happened to appear last.
                for token in _BACKTICKED.finditer(text[: match.start()]):
                    if token.group(1) in jobs:
                        owner = token.group(1)
            if owner is None:
                continue
            claims.append((path, owner, _parse_claim(match.group(1)), match.group(0)))
    return claims


def test_the_needs_scan_still_finds_claims() -> None:
    """An empty scan is a failure, not a pass."""
    claims = _needs_claims()
    assert len(claims) >= 5, (
        f"only {len(claims)} `needs:` claims were parsed out of the release "
        "runbook. The scan has stopped matching the prose, which would make the "
        "check below vacuous."
    )
    assert any(job == "build" for _, job, _, _ in claims), (
        "no `needs:` claim about the `build` job was found. `build` is the only "
        "job that pushes to GHCR, so it is the one whose dependencies the "
        "runbook most needs to state correctly."
    )


def test_every_needs_claim_matches_a_real_workflow_job() -> None:
    real = _real_needs()
    wrong = [
        f"{path.relative_to(REPO_ROOT)}: `{job}` is described as {raw}, "
        f"but the workflows say {sorted(sorted(n) for n in real[job])}"
        for path, job, claimed, raw in _needs_claims()
        if claimed not in real.get(job, set())
    ]
    assert not wrong, (
        "the release runbook states job dependencies the workflows do not have:\n"
        + "\n".join(f"  {line}" for line in wrong)
        + "\nPhase 1 item 6 tells the reader to verify this exact wiring, so a "
        "wrong value here is worse than no value."
    )


def _gate_paths() -> list[str]:
    """The repo-relative paths `check-release-tag.sh` declares it reads."""
    text = GATE.read_text(encoding="utf-8")
    paths = re.findall(r'^[A-Z_]+="\$\{REPO_ROOT\}/([^"]+)"', text, re.MULTILINE)
    assert paths, (
        'no `NAME="${REPO_ROOT}/..."` assignments found in '
        "check-release-tag.sh. This test derives the expected file list from "
        "the script; if the script stopped declaring them that way, teach it here."
    )
    return paths


# Blocks whose whole job is to say what the gate covers.  Each must name every
# file the gate reads -- that is the claim, and it is the claim that was wrong.
#
# The prose used to say "all four places" / "the first three" / "all four
# declarations" as well.  Those counting words are a second claim about the same
# list and the half that goes stale silently -- and a partial revert that fixed
# the number but not the list, or the reverse, would still read as authoritative.
# They have been removed rather than tested: with no number in the sentence, the
# list is the only claim, and it is the one this test derives from the script.
_ENUMERATING_ANCHORS = (
    "must be identical everywhere it is declared",
    "fails closed and reads every declaration",
    "check-release-tag.sh is authoritative for",
)


def _units(text: str) -> list[str]:
    """Blank-line blocks, further split into individual top-level bullets.

    Splitting on blank lines alone is too coarse here: the "Non-negotiable
    principles" list has no blank lines between its bullets, so the whole
    3386-character list came back as one unit and a bullet that had *lost*
    `values.yaml` still passed because a neighbouring bullet mentioned it.
    """
    units: list[str] = []
    for block in text.split("\n\n"):
        if re.match(r"^[-*] ", block):
            units.extend(re.split(r"\n(?=[-*] )", block))
        else:
            units.append(block)
    return units


@pytest.mark.parametrize("anchor", _ENUMERATING_ANCHORS)
def test_each_enumeration_of_the_gate_names_every_file_it_reads(anchor: str) -> None:
    expected = _gate_paths()
    found_anchor = False
    missing: list[str] = []
    for path in RUNBOOK_FILES:
        for block in _units(path.read_text(encoding="utf-8")):
            if anchor not in block:
                continue
            found_anchor = True
            flat = re.sub(r"\s+", " ", block)
            for rel in expected:
                if rel not in flat and Path(rel).name not in flat:
                    missing.append(f"{path.relative_to(REPO_ROOT)}: {rel!r}")
    assert found_anchor, (
        f"no block in the release runbook contains {anchor!r}. The wording that "
        "enumerates what check-release-tag.sh covers has moved; point this test "
        "at the new phrasing rather than deleting it."
    )
    assert not missing, (
        f"blocks introduced by {anchor!r} enumerate what check-release-tag.sh "
        "checks and leave out files it actually reads:\n"
        + "\n".join(f"  {line}" for line in missing)
        + "\nAn undercount here is how helm/recotem/values.yaml -- the value "
        "that decides which image a cluster pulls -- went unbumped."
    )


def test_the_phase_three_commit_read_covers_every_file_the_gate_reads() -> None:
    """Phase 3 step 1 reads the COMMIT with `git show`; the gate reads the tree.

    That block exists precisely because the two can differ, so it is the one
    place a missing file is not compensated for by running the script.
    """
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    block = re.search(r'(   git show "\$\{SHA\}:.*?\n)\n', text, re.DOTALL)
    assert block is not None, (
        'SKILL.md Phase 3 step 1 no longer has a run of `git show "${SHA}:..."` '
        "lines. That block is what reads the commit rather than the working tree."
    )
    shown = block.group(1)
    missing = [rel for rel in _gate_paths() if rel not in shown]
    assert not missing, (
        f"Phase 3 step 1 does not read {missing} out of the commit, though "
        "check-release-tag.sh reads them out of the working tree. The block "
        "exists because those two can disagree."
    )
