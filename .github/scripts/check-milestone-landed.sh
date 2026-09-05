#!/usr/bin/env bash
# Assert that every PR the release milestone calls MERGED is actually in the
# tree being released.
#
# A pull request can read `state: MERGED` on GitHub and still not be in `main`.
# It happens when a PR is stacked on another branch and that base is
# squash-merged before the child merges: the squash leaves the branch alive but
# no longer a path to `main`, so the child's merge commit lands on an orphan.
# GitHub keeps reporting MERGED, `gh pr list --state merged` keeps listing it,
# and the milestone keeps claiming it shipped.
#
# This is not hypothetical.  PR #245 ("fix(sql): probe the driver the DSN routes
# to, not the extra's"), milestone 2.1.0, merged at 05:57:03Z into
# `fix/probe-guard-contradicts-shipped-chart` — a branch PR #250 had
# squash-merged into main five minutes earlier, at 05:51:54Z.  Its merge commit
# `26a8c3b` is reachable only from that dead branch.  The fix was absent from
# main while the milestone, the merged-PR list and the round's own report all
# recorded it as delivered.  Nothing in the repository noticed:
# check-release-tag.sh compares file contents and makes no git or API call, and
# no workflow ran `git merge-base`.
#
# The detecting question is the reverse of the usual one.  "Is every commit on
# main explained by a PR?" cannot see this, because the commit is *absent*, not
# unexplained.  What finds it is: for every merged PR in this milestone, is its
# merge commit an ancestor of what we are about to ship?
#
# Scope is the release milestone rather than all history: it is the set the
# release claims to contain, it is bounded, and it is what an operator reads
# when deciding whether a fix is in a version.
#
# A re-land is normally a cherry-pick, which produces a NEW commit — the
# original merge commit stays unreachable forever.  So "the fix is in the tree"
# and "this PR's merge commit is an ancestor" stop agreeing the moment a lost PR
# is recovered, and a check that only asked the second question would flag the
# recovered PR at every future release.  `.github/relanded-prs.tsv` records the
# exception, and the record is only honoured when the replacement PR's own merge
# commit IS an ancestor — a waiver has to point at something that actually
# landed, so it cannot be used to wave a fix through.
#
# Usage (CI):    bash .github/scripts/check-milestone-landed.sh          # reads GITHUB_REF
# Usage (local): bash .github/scripts/check-milestone-landed.sh v2.1.0
#
# Requires `gh` authenticated for the repository and a full-history checkout
# (actions/checkout with fetch-depth: 0) — a shallow clone cannot answer an
# ancestry question and this script refuses rather than guessing.

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

fail() {
    echo "::error::$1"
    shift
    for line in "$@"; do
        echo "  ${line}"
    done
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Determine the tag, and from it the milestone title
# ---------------------------------------------------------------------------
TAG=""
RELAND_OVERRIDE=""
while [ $# -gt 0 ]; do
    case "$1" in
        # The record is resolved relative to THIS SCRIPT, not the working
        # directory, so a test running against a synthetic repository cannot
        # supply one by writing a file there -- it would silently exercise the
        # production record instead, and change behaviour whenever that file is
        # edited.  This is the seam the behavioural tests use.
        --reland-file)  RELAND_OVERRIDE="$2"; shift 2 ;;
        -*)             fail "unknown option '$1'" ;;
        *)              TAG="$1"; shift ;;
    esac
done
if [ -z "${TAG}" ]; then
    REF="${GITHUB_REF:-}"
    case "${REF}" in
        refs/tags/*) TAG="${REF#refs/tags/}" ;;
        *)
            fail "this check requires a tag ref, got '${REF:-<empty>}'." \
                 "Pass the tag explicitly: bash $0 v1.2.3"
            ;;
    esac
fi
MILESTONE="${TAG#v}"
echo "Release tag: ${TAG}"
echo "Milestone:   ${MILESTONE}"

# ---------------------------------------------------------------------------
# 2. Preconditions.  Each is fatal rather than a skip.
# ---------------------------------------------------------------------------
# A gate that quietly does nothing when a tool is missing is worse than no gate:
# it reports success for a question it never asked.  That is the same shape as
# the defect this script exists to catch, so every precondition below fails
# loudly.
command -v gh >/dev/null 2>&1 || fail \
    "gh is not installed, so the milestone cannot be read." \
    "Install GitHub CLI, or run this check from CI where gh is preinstalled."

if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
    fail "the checkout is shallow, so ancestry cannot be determined." \
         "Use actions/checkout with fetch-depth: 0 for this job."
fi

HEAD_SHA="$(git rev-parse HEAD)"
echo "Verifying against: ${HEAD_SHA}"

# ---------------------------------------------------------------------------
# 3. Read the milestone's merged PRs
# ---------------------------------------------------------------------------
# `gh pr list --search` is used rather than --milestone so that a milestone
# with no PRs is distinguishable from one that does not exist (checked next).
MILESTONE_EXISTS="$(
    gh api "repos/{owner}/{repo}/milestones?state=all&per_page=100" \
        --jq "[.[] | select(.title == \"${MILESTONE}\")] | length"
)"
if [ "${MILESTONE_EXISTS}" = "0" ]; then
    # Fail, do not pass.  A missing milestone is the one way this gate could
    # report success for a question it never asked: rename the milestone, or
    # forget to create it, and the check silently becomes a no-op forever while
    # still printing a green tick.  That is precisely the "a monitor whose
    # setup fails looks like a monitor with nothing to report" shape, so it is
    # fatal, with a documented opt-out for a deliberate release that has no
    # milestone.
    if [ "${RECOTEM_ALLOW_NO_MILESTONE:-}" = "1" ]; then
        echo "::notice::No milestone '${MILESTONE}'; skipped via" \
             "RECOTEM_ALLOW_NO_MILESTONE=1."
        exit 0
    fi
    fail "no milestone titled '${MILESTONE}' exists, so this release cannot be verified." \
         "This gate answers 'is every PR the milestone calls MERGED actually in" \
         "this tree?'.  With no milestone there is nothing to check against, and" \
         "passing would report success for a question that was never asked." \
         "" \
         "To fix, either:" \
         "  - create a milestone named '${MILESTONE}' and assign the release's PRs, or" \
         "  - set RECOTEM_ALLOW_NO_MILESTONE=1 to release without one deliberately."
fi

PRS="$(
    gh pr list --state merged --search "milestone:${MILESTONE}" \
        --limit 200 --json number,title,mergeCommit \
        --jq '.[] | "\(.number)\t\(.mergeCommit.oid // "none")\t\(.title)"'
)"

if [ -z "${PRS}" ]; then
    echo "Milestone '${MILESTONE}' has no merged pull requests. Nothing to verify."
    exit 0
fi

# ---------------------------------------------------------------------------
# 4. Every merge commit must be an ancestor of HEAD
# ---------------------------------------------------------------------------
RELAND_FILE="${RELAND_OVERRIDE:-${REPO_ROOT}/.github/relanded-prs.tsv}"

# reland_replacement <pr-number> -> replacement PR number, or empty.
reland_replacement() {
    [ -f "${RELAND_FILE}" ] || return 0
    awk -v n="$1" '
        /^[[:space:]]*#/ { next }
        NF >= 2 && $1 == n { print $2; exit }
    ' "${RELAND_FILE}"
}

# pr_merge_oid <pr-number> -> its merge commit, or empty.  Resolved from the
# PR list first so an offline run is self-contained, then from the API.
pr_merge_oid() {
    local found
    found="$(printf '%s\n' "${PRS}" | awk -F'\t' -v n="$1" '$1 == n { print $2; exit }')"
    if [ -n "${found}" ] && [ "${found}" != "none" ]; then
        printf '%s' "${found}"
        return 0
    fi
    gh pr view "$1" --json mergeCommit --jq '.mergeCommit.oid // ""' 2>/dev/null || true
}

# PR titles are author-controlled text and this script echoes them into a log
# that GitHub parses for `::workflow commands::`.  A title containing `::error::`
# would otherwise emit a forged annotation.  Strip the delimiter and any control
# characters; the title is only ever shown to a human, so mangling a pathological
# one costs nothing.
sanitize_title() {
    printf '%s' "$1" | tr -d '\000-\037' | sed 's/::/;;/g'
}

STRANDED=()
STRANDED_COUNT=0
MISSING_COMMIT=()
MISSING_COUNT=0
UNKNOWN=()
RELANDED=()
CHECKED=0

while IFS=$'\t' read -r NUMBER OID TITLE; do
    [ -z "${NUMBER}" ] && continue
    CHECKED=$((CHECKED + 1))
    TITLE="$(sanitize_title "${TITLE}")"
    # The PR number is used in a `gh pr view` argument below; accept only
    # digits so a malformed API row cannot turn into an option or a path.
    case "${NUMBER}" in
        ''|*[!0-9]*)
            UNKNOWN+=("(unparseable PR number in API response)")
            continue
            ;;
    esac
    if [ "${OID}" = "none" ]; then
        # Warn, do not fail.  Unlike the absent-object case below this is not
        # locally determinate: the API can report a null merge commit for a
        # transient reason, and a release should not be blocked by one.  It is
        # still reported, never passed over in silence.
        UNKNOWN+=("#${NUMBER}  ${TITLE}")
        continue
    fi
    if ! git cat-file -e "${OID}^{commit}" 2>/dev/null; then
        # FATAL, unlike the `none` case above.  This one is locally
        # determinate -- the clone is complete (checked above) and the object
        # is still absent -- and it is a fail-open on the gate's own motivating
        # case: delete the branch a stranded merge sits on and its commit
        # leaves the clone entirely, turning a correct "STRANDED, exit 1" into
        # "could not be verified, exit 0".  The gate would go quiet on exactly
        # the situation it exists to catch, at the moment someone tidies up
        # merged branches.  Unverifiable and unreachable are the same state of
        # knowledge here: we cannot say the release contains this PR.
        MISSING_COMMIT+=("#${NUMBER}  ${OID}  ${TITLE}")
        MISSING_COUNT=$((MISSING_COUNT + 1))
        continue
    fi
    if git merge-base --is-ancestor "${OID}" "${HEAD_SHA}"; then
        continue
    fi

    # Not an ancestor.  A recorded re-land clears it only if the replacement
    # PR's own merge commit is an ancestor — the waiver must point at something
    # that really landed.
    REPLACEMENT="$(reland_replacement "${NUMBER}")"
    case "${REPLACEMENT}" in
        *[!0-9]*) REPLACEMENT="" ;;  # not a PR number; ignore the row
    esac
    if [ -n "${REPLACEMENT}" ]; then
        REPL_OID="$(pr_merge_oid "${REPLACEMENT}")"
        if [ -n "${REPL_OID}" ] \
            && git cat-file -e "${REPL_OID}^{commit}" 2>/dev/null \
            && git merge-base --is-ancestor "${REPL_OID}" "${HEAD_SHA}"; then
            RELANDED+=("#${NUMBER} -> re-landed by #${REPLACEMENT} (${REPL_OID})")
            continue
        fi
        STRANDED+=("#${NUMBER}  ${OID}  ${TITLE}"$'\n'\
"       (relanded-prs.tsv names #${REPLACEMENT}, which has not landed either)")
        STRANDED_COUNT=$((STRANDED_COUNT + 1))
        continue
    fi
    STRANDED+=("#${NUMBER}  ${OID}  ${TITLE}")
    STRANDED_COUNT=$((STRANDED_COUNT + 1))
done <<< "${PRS}"

echo "Checked ${CHECKED} merged PR(s) in milestone '${MILESTONE}'."

if [ ${#RELANDED[@]} -gt 0 ]; then
    echo "${#RELANDED[@]} PR(s) cleared by a recorded re-land:"
    for line in "${RELANDED[@]}"; do
        echo "  ${line}"
    done
fi

if [ ${#UNKNOWN[@]} -gt 0 ]; then
    echo "::warning::${#UNKNOWN[@]} merged PR(s) could not be verified:"
    for line in "${UNKNOWN[@]}"; do
        echo "  ${line}"
    done
fi

if [ ${#MISSING_COMMIT[@]} -gt 0 ]; then
    {
        echo "::error::${MISSING_COUNT} PR(s) in milestone '${MILESTONE}' have a merge"
        echo "  commit that is not in this clone, so it cannot be verified:"
        for line in "${MISSING_COMMIT[@]}"; do
            echo "    ${line}"
        done
        echo ""
        echo "  The checkout is complete, so the object is genuinely absent -- the"
        echo "  usual cause is that the branch it sat on was deleted. That is the"
        echo "  same state of knowledge as a stranded PR: this release cannot be"
        echo "  shown to contain it."
        echo ""
        echo "  To fix: restore the ref so the object is fetchable"
        echo "    git fetch origin '+refs/pull/<n>/merge:refs/remotes/pr/<n>'"
        echo "  or, if the change was re-landed, record it in"
        echo "  .github/relanded-prs.tsv naming the PR that did land."
    } >&2
    exit 1
fi

if [ ${#STRANDED[@]} -gt 0 ]; then
    {
        echo "::error::${STRANDED_COUNT} PR(s) in milestone '${MILESTONE}' are marked"
        echo "  MERGED but are NOT in the tree ${TAG} would publish:"
        for line in "${STRANDED[@]}"; do
            echo "    ${line}"
        done
        echo ""
        echo "  Each merge commit above is unreachable from ${HEAD_SHA}. The usual"
        echo "  cause is a PR stacked on a branch that was squash-merged before the"
        echo "  PR itself merged, leaving the merge on an orphan."
        echo ""
        echo "  To fix, for each PR listed:"
        echo "    1. re-land it onto main:  git cherry-pick -n <merge-commit>"
        echo "    2. open a new PR with base=main, referencing the original"
        echo "    3. record it in .github/relanded-prs.tsv as three tab-separated"
        echo "       columns:  <original-pr>  <new-pr>  <why>"
        echo "       (the new PR must itself be merged into main for this to pass —"
        echo "        the record is not a way to skip the check)"
        echo "    4. re-tag once it has landed"
        echo ""
        echo "  To confirm by hand:"
        echo "    git merge-base --is-ancestor <merge-commit> HEAD && echo on-main"
    } >&2
    exit 1
fi

echo "OK: every merged PR in milestone '${MILESTONE}' is an ancestor of ${HEAD_SHA}."
