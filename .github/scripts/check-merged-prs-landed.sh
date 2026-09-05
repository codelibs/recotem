#!/usr/bin/env bash
# Assert that every merged PR's commit is actually reachable from main.
#
# A PR can be merged, green, milestoned, and still contribute nothing to the
# release.  #245 was: it targeted `fix/probe-guard-contradicts-shipped-chart`
# instead of `main`, and merged into that branch at 05:57:03Z — five minutes
# after #250 had merged the same branch INTO main at 05:51:54Z.  The branch
# stayed alive but stopped being a path to main, so the merge landed nowhere.
# 350 lines, including a 199-line test file, were absent from the release while
# GitHub reported the PR as merged.
#
# Nothing caught it, and three plausible checks do not:
#
#   1. "Is every commit on main accounted for by a PR?"  This is a predicate
#      over the commits that ARE on main.  A commit that never arrived is not
#      in that set, so no amount of scrutiny of it can reveal the absence.
#      That is the check that ran and reported "commits belonging to no PR: 0".
#
#   2. "Does each merged PR's commit match the PR's files and line counts?"
#      `git show <sha>` renders any object in the database, including one
#      reachable only from an abandoned branch, so this passes for a commit
#      that never landed.  It is the most convincing wrong answer, because it
#      appears to be reading main.
#
#   3. "Does the PR have a non-main base?"  Too broad by far.  Measured over
#      the 96 merged milestone-2.1.0 PRs, 46 have a non-main base and 45 of
#      those landed correctly — #237 shares #245's exact base branch and is
#      fine.  A check keyed on that would fire 46 times and be muted.
#
# Only reachability separates the cases: `git merge-base --is-ancestor`.  Over
# those same 96 PRs it flags exactly one, #245, with no false positives.
#
# Usage:
#   check-merged-prs-landed.sh <milestone> [ref]
#       Ask `gh` for every merged PR with that milestone and check each.
#       `ref` defaults to origin/main.
#
#   check-merged-prs-landed.sh --stdin [ref]
#       Read `number<TAB>base<TAB>sha` lines instead of calling `gh`.  This is
#       the seam the unit tests drive: the reachability logic is exercised
#       against a synthetic repository with a deliberately stranded commit, so
#       the detection is proven without a token or a network.

set -euo pipefail

fail() {
    echo "::error::$1"
    shift
    for line in "$@"; do
        echo "  ${line}"
    done
    exit 1
}

MODE="${1:-}"
[ -n "${MODE}" ] || fail "usage: check-merged-prs-landed.sh <milestone>|--stdin [ref]"

REF="${2:-origin/main}"

git rev-parse --verify --quiet "${REF}^{commit}" >/dev/null || \
    fail "ref '${REF}' does not resolve to a commit in this repository." \
         "Fetch it first: git fetch origin main" \
         "A shallow clone will also fail here — use fetch-depth: 0."

if [ "${MODE}" = "--stdin" ]; then
    PR_LINES="$(cat)"
else
    command -v gh >/dev/null || \
        fail "gh is required to enumerate merged pull requests." \
             "Install it, or pass --stdin with number<TAB>base<TAB>sha lines."
    # `--limit` is deliberately far above the real count: gh silently truncates
    # at the limit, and a truncated list would make this check pass by not
    # looking at the stranded PR at all.
    PR_LINES="$(gh pr list --state merged --limit 1000 \
        --json number,baseRefName,mergeCommit,milestone \
        --jq ".[] | select(.milestone.title == \"${MODE}\") |
              \"\(.number)\t\(.baseRefName)\t\(.mergeCommit.oid // \"\")\"")"
fi

# An empty list means the query stopped matching, not that everything is fine.
# Refused rather than skipped, for the same reason the release gate refuses a
# deployment-pin scan that finds nothing: the success message below would
# otherwise vouch for a set nobody looked at.
[ -n "${PR_LINES}" ] || \
    fail "No merged pull requests found for '${MODE}'." \
         "Every release has some, so finding none means this query stopped" \
         "matching rather than that there is nothing to check."

# ---------------------------------------------------------------------------
# Waivers
# ---------------------------------------------------------------------------
# A stranded PR is repaired by re-landing its content under a NEW pull request,
# which leaves the original's merge commit unreachable forever.  Without a way
# to record that, this check would stay red for the rest of the repository's
# life and be muted — the precise failure it exists to prevent, arriving by a
# different route.
#
# So a waiver is not a mute.  It names the PR that re-landed the content, and
# that PR is then held to the same reachability test.  The waiver is satisfied
# only once the replacement is genuinely on main, so it cannot be used to wave
# through work that has not actually arrived, and it needs no follow-up edit
# once the replacement lands.
WAIVERS_FILE="$(git rev-parse --show-toplevel)/.github/merged-pr-waivers.txt"
declare -a WAIVED_NUM=() WAIVED_BY=()
if [ -f "${WAIVERS_FILE}" ]; then
    while read -r w_num w_kw w_by || [ -n "${w_num}" ]; do
        case "${w_num}" in ''|'#'*) continue ;; esac
        [ "${w_kw}" = "relanded-by" ] || \
            fail "Malformed line in ${WAIVERS_FILE##*/}: '${w_num} ${w_kw} ${w_by}'" \
                 "Expected: <stranded PR number> relanded-by <replacement PR number>"
        WAIVED_NUM+=("${w_num}")
        WAIVED_BY+=("${w_by}")
    done < "${WAIVERS_FILE}"
fi

waiver_for() {
    local n="$1" i
    for i in "${!WAIVED_NUM[@]}"; do
        if [ "${WAIVED_NUM[$i]}" = "${n}" ]; then printf '%s' "${WAIVED_BY[$i]}"; return 0; fi
    done
    return 1
}

reachable() {
    local sha="$1"
    [ -n "${sha}" ] || return 1
    git cat-file -e "${sha}^{commit}" 2>/dev/null || return 1
    git merge-base --is-ancestor "${sha}" "${REF}" 2>/dev/null
}

# Index every PR's merge commit first, so a waiver can be resolved against the
# replacement PR regardless of the order the two appear in.
declare -a IDX_NUM=() IDX_SHA=()
while IFS=$'\t' read -r number base sha; do
    [ -n "${number}" ] || continue
    IDX_NUM+=("${number}")
    IDX_SHA+=("${sha}")
done <<< "${PR_LINES}"

sha_of() {
    local n="$1" i
    for i in "${!IDX_NUM[@]}"; do
        if [ "${IDX_NUM[$i]}" = "${n}" ]; then printf '%s' "${IDX_SHA[$i]}"; return 0; fi
    done
    return 1
}

STRANDED=()
RELANDED=()
CHECKED=0
while IFS=$'\t' read -r number base sha; do
    [ -n "${number}" ] || continue
    CHECKED=$((CHECKED + 1))

    if [ -n "${sha}" ] && reachable "${sha}"; then
        continue
    fi

    # Not reachable.  Is it waived, and has the replacement actually arrived?
    if replacement="$(waiver_for "${number}")"; then
        if repl_sha="$(sha_of "${replacement}")" && reachable "${repl_sha}"; then
            RELANDED+=("  #${number} re-landed by #${replacement}")
            continue
        fi
        STRANDED+=("  #${number} ${sha:-<no merge commit>} (base ${base}) — waiver names" \
                   "    #${replacement}, which is not on ${REF} either")
        continue
    fi

    if [ -z "${sha}" ]; then
        STRANDED+=("  #${number} (base ${base}) has no merge commit recorded")
    elif ! git cat-file -e "${sha}^{commit}" 2>/dev/null; then
        STRANDED+=("  #${number} ${sha} (base ${base}) — commit not in this repository")
    else
        STRANDED+=("  #${number} ${sha} (base ${base})")
    fi
done <<< "${PR_LINES}"

if [ "${#STRANDED[@]}" -gt 0 ]; then
    fail "Merged pull requests whose commit is NOT reachable from ${REF}:" \
         "${STRANDED[@]}" \
         "" \
         "These are merged as far as GitHub is concerned, and their content is" \
         "not in the release.  The usual cause is a PR whose base was a branch" \
         "that had already been merged into main: merging into it afterwards" \
         "lands nowhere.  Re-land each one onto main (cherry-pick its merge" \
         "commit), then re-land it under a new PR and record the repair in" \
         ".github/merged-pr-waivers.txt as:  <stranded PR> relanded-by <new PR>" \
         "" \
         "Checked ${CHECKED} merged pull requests."
fi

if [ "${#RELANDED[@]}" -gt 0 ]; then
    echo "Re-landed under a replacement PR (waived, replacement verified on ${REF}):"
    printf '%s\n' "${RELANDED[@]}"
fi
echo "OK: all ${CHECKED} merged pull requests are accounted for on ${REF}."
