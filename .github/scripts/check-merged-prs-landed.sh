#!/usr/bin/env bash
# Assert that every merged pull request actually reached the target branch.
#
# GitHub reports a pull request as MERGED when it is merged into *its own base
# branch*, which is not necessarily `main`.  When a PR is stacked on another
# branch and that branch is squash-merged first, the base survives as a ref but
# stops being a path to `main`; a later merge into it lands on what is now an
# orphan.  The PR shows MERGED, `gh pr list --state merged` lists it, the
# milestone counts it, and the change is absent from the release.
#
# recotem #245 failed exactly this way.  Its base was
# `fix/probe-guard-contradicts-shipped-chart`; #250 squash-merged that branch to
# main at 05:51:54Z and #245 merged into the branch at 05:57:03Z, five minutes
# later.  `git merge-base --is-ancestor 26a8c3b origin/main` is false and the fix
# -- a driver probe whose absence makes three documented DSN spellings fail --
# shipped nowhere, while every tracking surface said delivered.
#
# The check that finds this is ancestry, and only ancestry.  Comparing a PR's
# file or line stats against `git show <merge_sha>` MATCHES for a stranded
# commit, because `git show` reads any object in the database whether or not it
# is reachable from a branch.  An audit built on content comparison clears a
# stranded PR as healthy; that mistake was made once already while looking for
# this very defect.
#
# Usage (CI):    bash .github/scripts/check-merged-prs-landed.sh
# Usage (local): bash .github/scripts/check-merged-prs-landed.sh --limit 200
#
# Offline / testing: pass a file of "<pr-number> <merge-sha>" lines instead of
# querying the API, so the logic can be exercised against a fixture repository:
#                bash .github/scripts/check-merged-prs-landed.sh --from-file pairs.txt

set -euo pipefail

TARGET_REF="${TARGET_REF:-origin/main}"
LIMIT=100
FROM_FILE=""
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
WAIVERS="${REPO_ROOT}/.github/merged-pr-waivers.txt"

while [ $# -gt 0 ]; do
    case "$1" in
        --limit)     LIMIT="$2"; shift 2 ;;
        --from-file) FROM_FILE="$2"; shift 2 ;;
        --target)    TARGET_REF="$2"; shift 2 ;;
        --waivers)   WAIVERS="$2"; shift 2 ;;
        *) echo "::error::unknown argument: $1" >&2; exit 2 ;;
    esac
done

# A stranded PR stays stranded forever: re-landing it creates a NEW commit, and
# the original merge commit is still unreachable.  Without a waiver this check
# would go permanently red the moment it succeeds at its job -- which is how a
# monitor stops being read.  Waivers are one PR number per line with a reason
# after whitespace; a waiver without a reason is rejected, and a waiver for a PR
# that is NOT stranded is rejected too, so the list cannot rot into a blanket
# suppression.
waived_numbers=""
if [ -f "${WAIVERS}" ]; then
    while IFS= read -r line || [ -n "${line}" ]; do
        case "${line}" in ''|'#'*) continue ;; esac
        w_num="${line%%[[:space:]]*}"
        w_reason="$(printf '%s' "${line#"${w_num}"}" | sed 's/^[[:space:]]*//')"
        if [ -z "${w_reason}" ]; then
            echo "::error::waiver for PR #${w_num} has no reason"
            echo "  ${WAIVERS} entries must read: <pr-number> <why it is waived>"
            echo "  A waiver with no reason is indistinguishable from a typo."
            exit 2
        fi
        waived_numbers="${waived_numbers} ${w_num}"
    done < "${WAIVERS}"
fi

if ! git rev-parse --verify --quiet "${TARGET_REF}^{commit}" >/dev/null; then
    echo "::error::target ref '${TARGET_REF}' does not resolve to a commit"
    echo "  In CI this usually means the checkout was shallow or single-branch."
    echo "  Ancestry cannot be decided without the target branch's history, and"
    echo "  a check that cannot decide must fail rather than report success."
    exit 2
fi

if [ -n "${FROM_FILE}" ]; then
    if [ ! -r "${FROM_FILE}" ]; then
        echo "::error::cannot read --from-file '${FROM_FILE}'"
        exit 2
    fi
    PAIRS="$(cat -- "${FROM_FILE}")"
else
    if ! command -v gh >/dev/null 2>&1; then
        echo "::error::gh is required to list merged pull requests"
        exit 2
    fi
    PAIRS="$(gh pr list --state merged --limit "${LIMIT}" \
        --json number,mergeCommit \
        --jq '.[] | select(.mergeCommit != null) | "\(.number) \(.mergeCommit.oid)"')"
fi

# A run that examined nothing must not report success.  An API change, a bad
# --jq, an empty repository or a wrong --limit all produce an empty list, and
# "zero stranded PRs out of zero examined" is indistinguishable from a healthy
# repository unless the count is asserted.
if [ -z "${PAIRS//[[:space:]]/}" ]; then
    echo "::error::no merged pull requests were examined"
    echo "  Expected at least one merged PR to check against ${TARGET_REF}."
    echo "  This is a failure of the check itself, not a clean result."
    exit 2
fi

checked=0
missing=0
unknown=0
report=""
seen_waived=""

is_waived() {
    case " ${waived_numbers} " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

while read -r number sha; do
    [ -n "${number:-}" ] || continue
    checked=$((checked + 1))

    if is_waived "${number}"; then
        # Still confirm it IS stranded, so a stale waiver is caught rather than
        # silently masking a healthy PR -- and, more importantly, so the waiver
        # cannot outlive the condition it describes.
        if git cat-file -e "${sha}^{commit}" 2>/dev/null \
           && git merge-base --is-ancestor "${sha}" "${TARGET_REF}"; then
            echo "::error::stale waiver: PR #${number} IS on ${TARGET_REF}"
            echo "  Remove it from ${WAIVERS}. A waiver that no longer describes"
            echo "  anything is a hole waiting for the next stranded PR to fall"
            echo "  into."
            exit 2
        fi
        seen_waived="${seen_waived} ${number}"
        continue
    fi

    if ! git cat-file -e "${sha}^{commit}" 2>/dev/null; then
        # The object is not present locally.  Do not call this healthy: a
        # stranded commit on a deleted branch is one of the ways an object goes
        # missing, which is precisely the case being looked for.
        unknown=$((unknown + 1))
        report="${report}
  PR #${number}  ${sha}  (object not in this clone -- fetch it and re-run)"
        continue
    fi

    if ! git merge-base --is-ancestor "${sha}" "${TARGET_REF}"; then
        missing=$((missing + 1))
        base_hint="$(git branch -a --contains "${sha}" 2>/dev/null \
            | sed 's/^[* ]*//' | paste -sd, - )"
        report="${report}
  PR #${number}  ${sha}  reachable only from: ${base_hint:-<no branch>}"
    fi
done <<EOF
${PAIRS}
EOF

if [ "${missing}" -gt 0 ] || [ "${unknown}" -gt 0 ]; then
    echo "::error::merged pull requests whose commit is not on ${TARGET_REF}"
    echo "  Checked ${checked} merged PR(s): ${missing} stranded, ${unknown} unresolvable."
    # shellcheck disable=SC2001
    echo "${report}"
    echo ""
    echo "  A stranded PR is reported MERGED by GitHub and counted by its"
    echo "  milestone, but its change is not in the release.  Re-land it by"
    echo "  cherry-picking the merge commit onto ${TARGET_REF} and opening a new"
    echo "  PR whose base IS ${TARGET_REF}:"
    echo ""
    echo "      git cherry-pick -n <merge-sha>"
    echo ""
    echo "  Do not re-open the original PR: its base branch is the problem."
    exit 1
fi

if [ -n "${seen_waived// /}" ]; then
    echo "Waived (known stranded, re-landed elsewhere):${seen_waived}"
fi
echo "OK: all ${checked} merged pull request(s) are ancestors of ${TARGET_REF}."
