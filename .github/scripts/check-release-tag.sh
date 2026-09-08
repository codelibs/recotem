#!/usr/bin/env bash
# Assert that a release tag is safe to publish to PyPI.
#
# Publishing is irreversible: PyPI never lets a filename be reused, so a
# `.dev0` / `a0` / `rc1` uploaded by accident is permanent.  This script is the
# gate that runs before any build or upload step in .github/workflows/publish.yml,
# and — on tag-triggered runs only — before any build or push step in
# .github/workflows/docker.yml, so both release paths enforce one set of rules.
#
# It enforces two things:
#   1. The tag is a clean PEP 440 *final release* — vMAJOR.MINOR.PATCH with no
#      .dev / a / b / rc / .post / +local suffix.
#   2. The tag agrees with the version declarations that decide what a user
#      actually receives:
#        - pyproject.toml            [project] version
#        - src/recotem/version.py    __version__
#        - helm/recotem/Chart.yaml   version: and appVersion:
#        - helm/recotem/values.yaml  image.tag
#
# values.yaml is here because it, not appVersion, is what the chart deploys:
# `recotem.image` renders `.Values.image.tag | default .Chart.AppVersion`, and
# values.yaml sets image.tag, so appVersion is only a fallback that never
# fires.  Checking appVersion alone let a release tagged vX.Y.Z ship a chart
# whose manifests pull the *previous* image, with this script reporting OK.
#
#        - examples/k8s/              pinned ghcr.io/codelibs/recotem:X.Y.Z
#        - src/, README.md, examples/, .claude/
#                                     recotem.org/MAJOR.MINOR/ documentation URLs
#
# The deployment pins are checked for a reason that was measured rather than
# assumed.  They used to be excluded as "illustrative rather than
# load-bearing".  They are not: applying examples/k8s/ verbatim to a live arm64
# cluster deploys the image named there, and the published 2.0.0 arm64 variant
# cannot start --
#
#   $ head -1 /opt/venv/bin/recotem   # ghcr.io/codelibs/recotem:2.0.0, arm64
#   #!/build/.venv/bin/python         # the BUILD stage venv, absent at runtime
#
# giving `exec /opt/venv/bin/recotem: no such file or directory`, a failed
# bootstrap Job and CrashLoopBackOff on every replica.  A release that bumps the
# chart and leaves these behind hands that image to everyone who applies those
# manifests, and it is not visible from the package version.
#
# `:latest` references are deliberately exempt -- compose.yaml and the
# getting-started docs track the moving tag on purpose.
#
# Usage (CI):    bash .github/scripts/check-release-tag.sh          # reads GITHUB_REF
# Usage (local): bash .github/scripts/check-release-tag.sh v2.1.0   # before tagging

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
PYPROJECT="${REPO_ROOT}/pyproject.toml"
VERSION_PY="${REPO_ROOT}/src/recotem/version.py"
CHART="${REPO_ROOT}/helm/recotem/Chart.yaml"
VALUES="${REPO_ROOT}/helm/recotem/values.yaml"

fail() {
    echo "::error::$1"
    shift
    for line in "$@"; do
        echo "  ${line}"
    done
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Determine the tag
# ---------------------------------------------------------------------------
TAG="${1:-}"
if [ -z "${TAG}" ]; then
    REF="${GITHUB_REF:-}"
    case "${REF}" in
        refs/tags/*) TAG="${REF#refs/tags/}" ;;
        *)
            fail "publish requires a tag ref, got '${REF:-<empty>}'." \
                 "Run this workflow from a tag (git tag v1.2.3 && git push origin v1.2.3)," \
                 "or pass the tag explicitly: bash $0 v1.2.3"
            ;;
    esac
fi
echo "Release tag: ${TAG}"

# ---------------------------------------------------------------------------
# 2. The tag must be a PEP 440 final release
# ---------------------------------------------------------------------------
# Deliberately strict: exactly three numeric segments, no pre/post/dev/local
# suffix.  docker.yml's guard job calls this same script, so the container and
# PyPI paths accept exactly one tag shape — and docker.yml's `type=semver`
# metadata patterns, which emit no version tag for anything else, never get
# handed one.
if [[ ! "${TAG}" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    SUFFIX_HINT="it is not of the form vMAJOR.MINOR.PATCH"
    case "${TAG}" in
        *.dev*)          SUFFIX_HINT="it carries a '.dev' development suffix" ;;
        *[0-9]a[0-9]*)   SUFFIX_HINT="it carries an 'a' (alpha) pre-release suffix" ;;
        *[0-9]b[0-9]*)   SUFFIX_HINT="it carries a 'b' (beta) pre-release suffix" ;;
        *rc[0-9]*)       SUFFIX_HINT="it carries an 'rc' (release candidate) suffix" ;;
        *.post*)         SUFFIX_HINT="it carries a '.post' post-release suffix" ;;
        *+*)             SUFFIX_HINT="it carries a '+local' version suffix" ;;
    esac
    fail "Refusing to publish '${TAG}': ${SUFFIX_HINT}." \
         "Only clean PEP 440 final releases may be published to PyPI — vMAJOR.MINOR.PATCH," \
         "e.g. v2.1.0.  Uploads are irreversible: PyPI never allows a filename to be reused," \
         "so a pre-release published by mistake can never be replaced." \
         "" \
         "To fix:" \
         "  1. delete the bad tag:  git tag -d ${TAG} && git push origin :refs/tags/${TAG}" \
         "  2. set a final version in pyproject.toml and src/recotem/version.py" \
         "  3. re-tag:              git tag vX.Y.Z && git push origin vX.Y.Z"
fi
EXPECTED="${TAG#v}"

# ---------------------------------------------------------------------------
# 2b. The files this script is about to read must match the commit
# ---------------------------------------------------------------------------
# Sections 3-4 read the WORKING TREE.  A tag names a COMMIT.  When the two
# differ, every line this script prints -- and its "OK" -- describes a tree
# nobody is about to publish.  Section 5 makes this sharper rather than milder:
# it reports "The tagged commit is on main", a fact about HEAD, in the same
# success block as four worktree-derived version lines.
# One message, two different objects.
#
# Measured at 7871f9f, whose committed pyproject.toml says 2.1.0.dev0, whose
# chart says 2.0.0.  Edit
# only the working tree to the release-ready values, commit nothing:
#
#   $ bash .github/scripts/check-release-tag.sh v2.1.0
#   pyproject.toml       version = 2.1.0
#           The tagged commit is on main.                        # exit 0
#
# The release procedure has an adjacent `git status --porcelain  # MUST be
# empty` step (release-recotem, Phase 3 step 1), and it works -- but the same
# procedure calls THIS script the authoritative check, and an authoritative
# check that quietly reads different bytes from the ones being tagged is the
# shape a gate is supposed to remove.  In CI the checkout is clean and this is
# a no-op; the local pre-tag rehearsal is where it earns its place, which is
# precisely the run the procedure tells an operator to trust.
#
# Scoped to the paths this script reads, not to the whole tree: an untracked
# scratch file elsewhere cannot change the verdict, and refusing on one would
# train operators to look past this gate.
#
# GIT_TOPLEVEL is computed once here and reused by section 5.  Skipped outside
# a git work tree, and when the enclosing repository is not this tree -- the
# unit tests build synthetic trees in tmp dirs, which may sit inside some
# unrelated checkout.
GIT_TOPLEVEL="$(git -C "${REPO_ROOT}" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "${GIT_TOPLEVEL}" ] && [ "${GIT_TOPLEVEL}" = "${REPO_ROOT}" ]; then
    DIRTY="$(
        git -C "${REPO_ROOT}" status --porcelain -- \
            pyproject.toml \
            src/recotem/version.py \
            helm/recotem/Chart.yaml \
            helm/recotem/values.yaml \
            examples \
            2>/dev/null || true
    )"
    if [ -n "${DIRTY}" ]; then
        DIRTY_LINES=()
        while IFS= read -r line; do
            [ -n "${line}" ] || continue
            DIRTY_LINES+=("  ${line}")
        done <<< "${DIRTY}"
        fail "Refusing to verify '${TAG}': files this check reads differ from the commit." \
             "${DIRTY_LINES[@]}" \
             "" \
             "This script reads the working tree; a tag names a commit.  With these" \
             "uncommitted, everything below would describe a tree that is not the one" \
             "being tagged — including the 'OK' line and its claim about main." \
             "" \
             "To fix: commit the release changes (they belong in the release PR), then" \
             "re-run against the merge commit you are about to tag:" \
             "  git status --porcelain" \
             "  bash $0 ${TAG}"
    fi
fi

# ---------------------------------------------------------------------------
# 3. Every in-tree version declaration must equal the tag
# ---------------------------------------------------------------------------
PYPROJECT_VERSION="$(
    python3 - "${PYPROJECT}" <<'PYEOF'
import sys
import tomllib

with open(sys.argv[1], "rb") as handle:
    print(tomllib.load(handle)["project"]["version"])
PYEOF
)"

# Parsed textually rather than imported: importing recotem here would pull in
# the whole dependency tree just to read a string literal.
VERSION_PY_VERSION="$(
    python3 - "${VERSION_PY}" <<'PYEOF'
import ast
import sys

tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
found = None
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets
    ):
        # Keep going rather than stopping at the first assignment: Python
        # itself takes the LAST one, so stopping early let the guard read a
        # different string from the one `import recotem` reports.
        found = ast.literal_eval(node.value)
if found is None:
    raise SystemExit("no __version__ assignment found")
print(found)
PYEOF
)"

# The Helm chart's version keys are deployment pins rather than package
# metadata, but they must track the release for the same reason the wheel must:
# the chart published for X.Y.Z deploys `ghcr.io/codelibs/recotem:X.Y.Z`, and
# the release procedure lists them among the things a release bumps.
# Checking them here is what makes that fail closed.  Left behind, the chart
# ships a release whose manifests pull the *previous* image; bumped early during
# a dev cycle, it pins a tag that was never built — and neither is visible from
# the package version alone.
#
# Read with awk rather than a YAML parser for the same reason version.py is read
# with `ast`: the guard jobs run this script straight after `actions/checkout`
# with nothing installed, so no YAML library is on the runner.  Both keys are
# top-level scalars whose spelling the release procedure already fixes.
[ -f "${CHART}" ] || fail "Cannot read helm/recotem/Chart.yaml." \
     "The Helm chart is part of the release and its version must match the tag."

# The match is anchored to column 0.  awk's `$1` is the first *field*, not the
# start of the line, so an indented `version:` matches `$1 == "version:"` just
# as a top-level one does -- and awk stops at the first hit.  A nested key
# therefore used to shadow the real one, and `dependencies:` is the shape that
# makes this ordinary rather than exotic:
#
#   dependencies:
#     - name: redis
#       version: 2.1.0     <- read as the chart version
#   version: 2.0.0         <- the real key, never reached
#
# Measured: with exactly that Chart.yaml the script printed
# `helm Chart.yaml version = 2.1.0` and exited 0 for tag v2.1.0, publishing a
# chart still declaring 2.0.0.  Requiring a non-blank, non-comment character in
# column 1 restores the "top-level scalar" the comment above already assumed.
chart_key() {
    awk -v key="$1:" '
        /^[^[:space:]#]/ && $1 == key {
            value = $2; gsub(/"/, "", value); print value; exit
        }' "${CHART}"
}
CHART_VERSION="$(chart_key version)"
CHART_APP_VERSION="$(chart_key appVersion)"

[ -n "${CHART_VERSION}" ] || \
    fail "helm/recotem/Chart.yaml has no top-level 'version:' key." \
         "The chart version must equal the release tag; a chart that does not declare" \
         "one cannot be checked, so this is refused rather than skipped."
[ -n "${CHART_APP_VERSION}" ] || \
    fail "helm/recotem/Chart.yaml has no top-level 'appVersion:' key." \
         "appVersion is the fallback image tag for a chart whose values.yaml leaves" \
         "image.tag empty, and must equal the release tag; a chart that does not" \
         "declare one cannot be checked."

# image.tag is the value that actually reaches a cluster.  Read the same way as
# the chart keys and for the same reason -- no YAML library on the runner -- but
# it is nested, so track which top-level block we are inside rather than
# matching `tag:` anywhere in the file.
[ -f "${VALUES}" ] || fail "Cannot read helm/recotem/values.yaml." \
     "image.tag in that file is the image every chart install pulls; it is part of" \
     "the release and must match the tag."

VALUES_IMAGE_TAG="$(
    awk '
        /^[^[:space:]#]/ { in_image = ($0 ~ /^image:[[:space:]]*$/) }
        in_image && $1 == "tag:" { value = $2; gsub(/"/, "", value); print value; exit }
    ' "${VALUES}"
)"

[ -n "${VALUES_IMAGE_TAG}" ] || \
    fail "helm/recotem/values.yaml has no 'image.tag' value." \
         "An empty image.tag falls back to Chart.yaml appVersion, which would make this" \
         "check vacuous.  Set it explicitly so the deployed image is pinned and checked."

echo "pyproject.toml       version = ${PYPROJECT_VERSION}"
echo "src/recotem/version.py       = ${VERSION_PY_VERSION}"
echo "helm Chart.yaml version      = ${CHART_VERSION}"
echo "helm Chart.yaml appVersion   = ${CHART_APP_VERSION}"
echo "helm values.yaml image.tag   = ${VALUES_IMAGE_TAG}"
echo "expected (from tag)          = ${EXPECTED}"

# Every declaration is compared before reporting, so one run names every file
# that did not move.  Reporting the first mismatch alone would send an operator
# round the fix/re-run loop once per stale file.
MISMATCH=""
add_mismatch() {
    [ -z "${MISMATCH}" ] || MISMATCH="${MISMATCH} and "
    MISMATCH="${MISMATCH}$1"
}

[ "${PYPROJECT_VERSION}" = "${EXPECTED}" ] || \
    add_mismatch "pyproject.toml (${PYPROJECT_VERSION})"
[ "${VERSION_PY_VERSION}" = "${EXPECTED}" ] || \
    add_mismatch "src/recotem/version.py (${VERSION_PY_VERSION})"
[ "${CHART_VERSION}" = "${EXPECTED}" ] || \
    add_mismatch "helm/recotem/Chart.yaml version: (${CHART_VERSION})"
[ "${CHART_APP_VERSION}" = "${EXPECTED}" ] || \
    add_mismatch "helm/recotem/Chart.yaml appVersion: (${CHART_APP_VERSION})"
[ "${VALUES_IMAGE_TAG}" = "${EXPECTED}" ] || \
    add_mismatch "helm/recotem/values.yaml image.tag: (${VALUES_IMAGE_TAG})"

# ---------------------------------------------------------------------------
# 4. Deployment pins outside the chart
# ---------------------------------------------------------------------------
# Read with grep rather than a YAML/Markdown parser, for the same reason the
# chart keys are read with awk: the guard jobs run this straight after
# `actions/checkout` with nothing installed.  These are literal image
# references in manifests and in fenced code blocks, so a textual scan is the
# right shape as well as the only available one.
#
# `-o` prints just the match, so each hit is `path:lineno:<match>`.
#
# The tag part matches the WHOLE tag, not a three-segment prefix of it.  With
# the prefix form `...:[0-9]+\.[0-9]+\.[0-9]+`, grep -o returned `recotem:2.1.0`
# for a pin reading `recotem:2.1.0-alpine` or `recotem:2.1.0rc1`, which then
# compared equal to the tag and passed -- a pin naming an image that was never
# published.  Matching the whole tag and classifying it below closes that.
#
# The pin and label scans read `examples` only.  They used to read `examples
# docs`, and a third scan (`EXCERPT_RE`, an anchored `tag: "X.Y.Z"`) existed
# solely to reach the copy-pasteable `values.yaml` block in
# docs/deployment/k8s.md -- the one location in that file with no `ghcr.io/`
# prefix for PIN_RE to match.  That whole tree is gone: the documentation now
# lives at recotem.org, in the recotem-docs repository, whose own release
# procedure bumps its copies (Phase 4B).  Deleting docs/ took the excerpt scan
# from exactly one hit to zero, which the vacuity guard below correctly reads
# as "this check stopped matching" -- it would refuse every future tag.
#
# Section 4b replaces it rather than dropping it, so the number of things this
# script vouches for does not quietly shrink by one.
PIN_RE='ghcr\.io/codelibs/recotem:[A-Za-z0-9_][A-Za-z0-9_.-]*'
LABEL_RE='app\.kubernetes\.io/version: *"[^"]*"'

PIN_HITS="$(cd "${REPO_ROOT}" && grep -rnoE "${PIN_RE}" examples 2>/dev/null || true)"

# The `docs/upgrading.md` exemption that used to sit here is gone with the
# file.  That page named PREVIOUS releases -- `ghcr.io/codelibs/recotem:2.0.0`
# as the subject of a sentence, not as something a reader deploys -- so
# scanning it made the gate and the release mutually unsatisfiable.  Its
# successor is https://recotem.org/2.1/docs/upgrading.html, outside this repository
# and outside this scan, so no filter is needed to keep the two satisfiable.
LABEL_HITS="$(cd "${REPO_ROOT}" && grep -rnoE "${LABEL_RE}" examples 2>/dev/null || true)"

# Both hit shapes end in the tag, one after a ':' and one inside quotes:
#   examples/k8s/cronjob.yaml:60:ghcr.io/codelibs/recotem:2.0.0
#   examples/k8s/serve-deployment.yaml:27:app.kubernetes.io/version: "2.0.0"
# Dropping a trailing quote and then everything through the last ':' or '"'
# leaves the tag in both cases.
pin_version() {
    printf '%s' "$1" | sed -e 's/"$//' -e 's/.*[:"]//'
}

# A tag that starts with a digit is a version pin and must equal the release.
# Anything else -- `latest`, `main`, `sha-abc1234` -- is a deliberately moving
# reference and is left alone; `:latest` in compose.yaml is the reason that
# exemption exists.
#
# `vX.Y.Z` counts too.  Keying only on a leading digit read `recotem:v2.0.0` as
# a moving reference and skipped it -- so a stale pin written the way the git
# TAG is written was the one spelling this check could not see, which is the
# spelling a release is most likely to produce by hand.  Measured: with every
# other location bumped and one pin left at `recotem:v2.0.0`, the script exited
# 0.  A bare `v` followed by a digit is never a moving tag in this repository;
# `latest`, `main` and `sha-abc1234` all still fall through.
is_version_pin() {
    case "$1" in
        [0-9]*)   return 0 ;;
        v[0-9]*)  return 0 ;;
        *)        return 1 ;;
    esac
}

# Compare tags after dropping a leading `v`, so `v2.1.0` and `2.1.0` are the
# same version.  The pin is still reported verbatim, so the fix is obvious.
pin_matches_expected() {
    [ "${1#v}" = "${EXPECTED}" ]
}

# Count the hits that are actually subject to the comparison, so the vacuity
# guards below test what they claim to.
VERSION_PIN_COUNT=0
VERSION_LABEL_COUNT=0
STALE_PINS=()
classify() {
    local hit="$1" kind="$2" tag
    [ -n "${hit}" ] || return 0
    tag="$(pin_version "${hit}")"
    is_version_pin "${tag}" || return 0
    case "${kind}" in
        label)   VERSION_LABEL_COUNT=$((VERSION_LABEL_COUNT + 1)) ;;
        *)       VERSION_PIN_COUNT=$((VERSION_PIN_COUNT + 1)) ;;
    esac
    # Collected as array elements, not as one newline-joined string, so `fail`
    # indents every line the same way rather than only the first.
    pin_matches_expected "${tag}" || STALE_PINS+=("  ${hit}")
}

while IFS= read -r hit; do classify "${hit}" pin; done   < <(printf '%s\n' "${PIN_HITS}")
while IFS= read -r hit; do classify "${hit}" label; done < <(printf '%s\n' "${LABEL_HITS}")

# A scan that finds nothing is refused rather than passed.  The release
# procedure bumps these pins, so zero hits means the pattern stopped matching,
# not that there is nothing to check -- and a vacuous check is worse than a
# missing one, because the success message below would vouch for pins nobody
# looked at.  Same reasoning as the empty `image.tag` case above.
[ "${VERSION_PIN_COUNT}" -gt 0 ] || \
    fail "No pinned 'ghcr.io/codelibs/recotem:X.Y.Z' reference found under examples/." \
         "The release procedure bumps these, so finding none means this check stopped" \
         "matching rather than that there is nothing to check.  Refused rather than" \
         "skipped: a vacuous check would make this script's success message untrue."

# The same guard for the label scan, which had none: deleting every
# `app.kubernetes.io/version` label from examples/k8s/ silently reduced that
# half of the check to nothing while the script still reported OK.
[ "${VERSION_LABEL_COUNT}" -gt 0 ] || \
    fail "No 'app.kubernetes.io/version: \"X.Y.Z\"' label found under examples/." \
         "It is a version declaration the release procedure bumps, so finding none" \
         "means this check stopped matching rather than that there is nothing to" \
         "check.  Refused rather than skipped, for the same reason as the image pins."

# ---------------------------------------------------------------------------
# 4b. Documentation-site URLs
# ---------------------------------------------------------------------------
# This repository carries no docs/ tree.  Every documentation pointer is a
# versioned URL into recotem.org, and several of them ship inside artefacts a
# reader cannot edit and this repository cannot correct after the fact: the
# text of a DataSourceError, the JSON Schema `recotem schema` emits for IDEs,
# the HELP string served at /v1/metrics, README.md as rendered on PyPI.  A
# stale version segment there points a user at another version's documentation.
#
# The segment is MAJOR.MINOR, not the full release: a patch does not create a
# documentation line, so v2.1.1 still points at /2.1/.  These URLs are bumped
# at the DEV bump (Phase 5), not at release -- the opposite cadence to the
# deployment pins above -- which is exactly why a release-time gate is the
# thing that notices when the bump was skipped.
SITE_ROOTS=(src tests examples helm .claude .github README.md CLAUDE.md CONTRIBUTING.md pyproject.toml)
SITE_RE='recotem\.org/[0-9]+\.[0-9]+/'
SITE_HITS="$(cd "${REPO_ROOT}" && grep -rnoE "${SITE_RE}" "${SITE_ROOTS[@]}" 2>/dev/null || true)"

EXPECTED_MM="${EXPECTED%.*}"
VERSION_SITE_COUNT=0
STALE_SITE_URLS=()
while IFS= read -r hit; do
    [ -n "${hit}" ] || continue
    VERSION_SITE_COUNT=$((VERSION_SITE_COUNT + 1))
    HIT_MM="${hit##*recotem.org/}"
    HIT_MM="${HIT_MM%/}"
    [ "${HIT_MM}" = "${EXPECTED_MM}" ] || STALE_SITE_URLS+=("  ${hit}")
done < <(printf '%s\n' "${SITE_HITS}")

# The same vacuity guard as the two above, and for the same reason: this scan
# replaced the values.yaml-excerpt scan that died with docs/, so a silent zero
# here would shrink what this script vouches for without saying so.
[ "${VERSION_SITE_COUNT}" -gt 0 ] || \
    fail "No 'recotem.org/X.Y/' documentation URL found in the tree." \
         "Error messages, the JSON Schema, the /v1/metrics HELP text and README.md" \
         "all carry one, so finding none means this check stopped matching rather" \
         "than that there is nothing to check.  Refused rather than skipped, for the" \
         "same reason as the image pins."

# ---------------------------------------------------------------------------
# 5. The commit being tagged must be on main
# ---------------------------------------------------------------------------
# Everything above reads files, so it describes the tree and says nothing about
# where that tree sits in history.  A tag placed on a feature branch -- or on a
# commit whose PR was merged into a branch that had already stopped being a path
# to main -- carries a perfectly consistent set of version strings and passes
# every check above.
#
# HEAD is the right commit to test in both usages: in CI `actions/checkout` has
# checked out the tag, and locally the operator runs this before tagging, so
# HEAD is the commit about to be tagged.
#
# This is the script's first and only use of git, and it is deliberately
# fail-closed rather than skip-quietly.  `actions/checkout`'s default
# `fetch-depth: 1` produces a work tree in which `origin/main` does not exist at
# all -- measured: a depth-1 single-branch clone reports
# `--is-inside-work-tree true`, `rev-parse origin/main` fails, and
# `rev-list --count HEAD` is 1.  Skipping in that case would make this check
# vacuous exactly where it matters, so the guard jobs set `fetch-depth: 0` and
# a work tree without a main ref is refused.  That makes the workflow setting
# self-enforcing: remove it and this fails loudly instead of passing silently.
#
# Outside a git work tree (the unit tests build synthetic trees in tmp dirs)
# there is nothing to check and nothing to enforce; the success message says so
# rather than implying it was verified.
BRANCH_PROBLEM=""
BRANCH_DETAIL=()
BRANCH_CHECKED=0

# GIT_TOPLEVEL was resolved in section 2b, which needed the same answer.
if [ -n "${GIT_TOPLEVEL}" ] && [ "${GIT_TOPLEVEL}" = "${REPO_ROOT}" ]; then
    BRANCH_CHECKED=1

    # A shallow repository is refused before the ancestry question is asked,
    # because in a shallow clone git answers it *wrongly* rather than failing.
    # Measured: with a feature commit that genuinely is an ancestor of main,
    # a full clone gives `--is-ancestor` exit 0, and a `--depth 1` clone of the
    # same repository gives exit 1 -- the connecting history is cut, the tip
    # object is still present, and git reports "not an ancestor" with no hint
    # that it could not see.  (A missing object gives 128; this case does not,
    # which is what makes it dangerous.)  Left unguarded, a shallow checkout
    # would fail a legitimate release and name the wrong reason.
    if [ "$(git -C "${REPO_ROOT}" rev-parse --is-shallow-repository 2>/dev/null)" \
         = "true" ]; then
        BRANCH_PROBLEM="cannot be checked against main (shallow clone)"
        BRANCH_DETAIL=(
            "This is a shallow repository, so whether the tagged commit is on main" \
            "cannot be determined: git answers the ancestry question from truncated" \
            "history and reports 'not an ancestor' for commits that are on main." \
            "" \
            "In CI, set fetch-depth: 0 on the guard job's actions/checkout step." \
            "Locally:  git fetch --unshallow origin"
        )
    fi

    MAIN_REF=""
    for candidate in refs/remotes/origin/main refs/heads/main; do
        if git -C "${REPO_ROOT}" rev-parse --verify -q "${candidate}" > /dev/null 2>&1
        then
            MAIN_REF="${candidate}"
            break
        fi
    done

    if [ -n "${BRANCH_PROBLEM}" ]; then
        : # already refused above; do not overwrite the more specific reason
    elif [ -z "${MAIN_REF}" ]; then
        BRANCH_PROBLEM="cannot be checked against main"
        BRANCH_DETAIL=(
            "This is a git work tree, but neither origin/main nor refs/heads/main exists," \
            "so whether the tagged commit is on main cannot be determined." \
            "" \
            "In CI this means the checkout was shallow: actions/checkout defaults to" \
            "fetch-depth: 1, which fetches only the tagged commit.  The guard jobs set" \
            "fetch-depth: 0 for this reason -- restore it rather than removing this check." \
            "" \
            "Locally:  git fetch origin main"
        )
    elif ! git -C "${REPO_ROOT}" merge-base --is-ancestor HEAD "${MAIN_REF}" \
            > /dev/null 2>&1; then
        BRANCH_PROBLEM="is on a commit that is not on main"
        BRANCH_DETAIL=(
            "HEAD ($(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null)) is not an" \
            "ancestor of ${MAIN_REF}." \
            "" \
            "A release is cut from main.  A tag on a commit that never reached main" \
            "publishes a tree nobody reviewed on main, and the version strings above" \
            "cannot detect it -- they describe the tree, not where it sits in history." \
            "" \
            "If the branch really is merged, fetch first: git fetch origin main" \
            "Otherwise merge it, then tag the merge commit."
        )
    fi
fi

# ---------------------------------------------------------------------------
# 6. Report -- every class of failure in a single run
# ---------------------------------------------------------------------------
# Section 3 compares every version declaration before reporting so that one run
# names every file that did not move.  The pin scan used to defeat that: it
# called `fail` (which exits) before the version mismatches were ever printed,
# so a tree with both kinds of staleness -- the normal state at the start of a
# release -- reported the pins, and only after those were fixed did a second run
# reveal that pyproject.toml, version.py and the chart had not moved either.
# On the tag-triggered release path each of those round trips costs a tag
# delete, a re-tag and a re-push.  Both are collected here and reported once.
REPORT=()
if [ "${#STALE_PINS[@]}" -gt 0 ]; then
    REPORT+=("Deployment pins still on another version:" \
             "${STALE_PINS[@]}" \
             "" \
             "These are not illustrative.  Applying examples/k8s/ verbatim deploys the" \
             "image named there.  The published 2.0.0 arm64 image cannot start at all --" \
             "its console script carries the build-stage shebang" \
             "'#!/build/.venv/bin/python' -- so a release still pointing at it is a" \
             "CrashLoopBackOff for everyone who applies those manifests." \
             "" \
             "To fix, set every reference above to ${EXPECTED} (or pick another number):" \
             "  git grep -nE 'ghcr[.]io/codelibs/recotem:[0-9]+[.][0-9]+[.][0-9]+' examples" \
             "  git grep -n  'app.kubernetes.io/version' examples" \
             "" \
             "The deployment docs carry their own copies of these pins; they live in the" \
             "recotem-docs repository and are bumped there, in Phase 4B of the release." \
             "")
fi
if [ "${#STALE_SITE_URLS[@]}" -gt 0 ]; then
    REPORT+=("Documentation-site URLs naming another version:" \
             "${STALE_SITE_URLS[@]}" \
             "" \
             "These point a reader at ${EXPECTED_MM%%.*}.x documentation for a version that is not" \
             "the one being released.  Some of them ship where nobody can correct them" \
             "later -- a DataSourceError message, the JSON Schema 'recotem schema' emits," \
             "the /v1/metrics HELP string, README.md as rendered on PyPI." \
             "" \
             "These are bumped at the DEV bump, not at release, so reaching this point" \
             "means Phase 5 of the previous cycle skipped them.  To fix, set every URL" \
             "above to /${EXPECTED_MM}/:" \
             "  git grep -nE 'recotem[.]org/[0-9]+[.][0-9]+/'" \
             "")
fi
if [ -n "${MISMATCH}" ]; then
    REPORT+=("Version declarations that do not match: ${MISMATCH}." \
             "The tag, pyproject.toml, src/recotem/version.py, helm/recotem/Chart.yaml" \
             "and helm/recotem/values.yaml must all agree.  A mismatch in the first two" \
             "uploads a wheel carrying a version nobody tagged; a mismatch in values.yaml" \
             "ships a chart whose manifests deploy some other image tag." \
             "")
fi
if [ -n "${BRANCH_PROBLEM}" ]; then
    REPORT+=("${BRANCH_DETAIL[@]}" "")
fi

if [ "${#REPORT[@]}" -gt 0 ]; then
    # Clauses joined rather than concatenated by hand, so adding a fourth class
    # of failure later does not require rewriting the sentence.
    CLAUSES=()
    [ "${#STALE_PINS[@]}" -eq 0 ] || \
        CLAUSES+=("does not match every deployment pin")
    [ -z "${MISMATCH}" ] || \
        CLAUSES+=("does not match the project version: ${MISMATCH}")
    [ -z "${BRANCH_PROBLEM}" ] || \
        CLAUSES+=("${BRANCH_PROBLEM}")
    HEADLINE="Tag '${TAG}'"
    SEPARATOR=" "
    for clause in "${CLAUSES[@]}"; do
        HEADLINE="${HEADLINE}${SEPARATOR}${clause}"
        SEPARATOR=", and "
    done
    fail "${HEADLINE}." \
         "${REPORT[@]}" \
         "To fix:" \
         "  1. delete the bad tag:  git tag -d ${TAG} && git push origin :refs/tags/${TAG}" \
         "  2. set version = \"${EXPECTED}\" in pyproject.toml," \
         "     __version__ = \"${EXPECTED}\" in src/recotem/version.py," \
         "     version: ${EXPECTED} / appVersion: \"${EXPECTED}\" in" \
         "     helm/recotem/Chart.yaml, and tag: \"${EXPECTED}\" under image: in" \
         "     helm/recotem/values.yaml (or pick another number)" \
         "  3. commit, merge, and re-tag the merge commit"
fi

echo "OK: ${TAG} is a final release and matches pyproject.toml,"
echo "    src/recotem/version.py, helm/recotem/Chart.yaml, helm/recotem/values.yaml,"
echo "    every pinned image reference under examples/, and every recotem.org/${EXPECTED_MM}/"
echo "    documentation URL in the tree."
# Say which tree the lines above describe.  Without this the success message
# reads the same whether it inspected the commit or an uncommitted edit of it —
# and the next line makes a claim about HEAD, so the two must not be confused.
if [ "${BRANCH_CHECKED}" -eq 1 ]; then
    echo "    Those files are committed, so the lines above describe the tree"
    echo "    ${TAG} would publish."
    echo "    The tagged commit is on main."
else
    echo "    NOT a git work tree: the lines above describe the files on disk,"
    echo "    which may not be the ones ${TAG} would publish, and whether the"
    echo "    tagged commit is on main was NOT checked."
fi
echo "    Not checked here: uv.lock (run 'uv lock --check'), and version strings"
echo "    outside those files — see the verification block in"
echo "    .claude/skills/release-recotem/references/version-locations.md."
