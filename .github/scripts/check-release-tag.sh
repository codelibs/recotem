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
#        - examples/k8s/ and docs/    pinned ghcr.io/codelibs/recotem:X.Y.Z
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
# chart and leaves these behind hands that image to everyone who follows the
# deployment docs, and it is not visible from the package version.
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
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets
    ):
        print(ast.literal_eval(node.value))
        break
else:
    raise SystemExit("no __version__ assignment found")
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

chart_key() {
    awk -v key="$1:" '$1 == key { value = $2; gsub(/"/, "", value); print value; exit }' \
        "${CHART}"
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
PIN_RE='ghcr\.io/codelibs/recotem:[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*'
LABEL_RE='app\.kubernetes\.io/version: *"[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*"'

PIN_HITS="$(cd "${REPO_ROOT}" && grep -rnoE "${PIN_RE}" examples docs 2>/dev/null || true)"
LABEL_HITS="$(cd "${REPO_ROOT}" && grep -rnoE "${LABEL_RE}" examples 2>/dev/null || true)"

# A scan that finds nothing is refused rather than passed.  The release
# procedure bumps these pins, so zero hits means the pattern stopped matching,
# not that there is nothing to check -- and a vacuous check is worse than a
# missing one, because the success message below would vouch for pins nobody
# looked at.  Same reasoning as the empty `image.tag` case above.
[ -n "${PIN_HITS}" ] || \
    fail "No pinned 'ghcr.io/codelibs/recotem:X.Y.Z' reference found under examples/ or docs/." \
         "The release procedure bumps these, so finding none means this check stopped" \
         "matching rather than that there is nothing to check.  Refused rather than" \
         "skipped: a vacuous check would make this script's success message untrue."

# Both hit shapes end in the version, one after a ':' and one inside quotes:
#   examples/k8s/cronjob.yaml:60:ghcr.io/codelibs/recotem:2.0.0
#   examples/k8s/serve-deployment.yaml:27:app.kubernetes.io/version: "2.0.0"
# Dropping a trailing quote and then everything through the last ':' or '"'
# leaves the version in both cases.
pin_version() {
    printf '%s' "$1" | sed -e 's/"$//' -e 's/.*[:"]//'
}

# Collected as array elements, not as one newline-joined string, so `fail`
# indents every line the same way rather than only the first.
STALE_PINS=()
while IFS= read -r hit; do
    [ -n "${hit}" ] || continue
    [ "$(pin_version "${hit}")" = "${EXPECTED}" ] || STALE_PINS+=("  ${hit}")
done < <(printf '%s\n%s\n' "${PIN_HITS}" "${LABEL_HITS}")

if [ "${#STALE_PINS[@]}" -gt 0 ]; then
    fail "Tag '${TAG}' does not match every deployment pin.  Still on another version:" \
         "${STALE_PINS[@]}" \
         "" \
         "These are not illustrative.  Applying examples/k8s/ verbatim deploys the" \
         "image named there, and docs/deployment/k8s.md is what a reader copies. The" \
         "published 2.0.0 arm64 image cannot start at all -- its console script carries" \
         "the build-stage shebang '#!/build/.venv/bin/python' -- so a release still" \
         "pointing at it is a CrashLoopBackOff for every arm64 reader of those docs." \
         "" \
         "To fix, set every reference above to ${EXPECTED} (or pick another number):" \
         "  git grep -nE 'ghcr[.]io/codelibs/recotem:[0-9]+[.][0-9]+[.][0-9]+' examples docs" \
         "  git grep -n  'app.kubernetes.io/version' examples"
fi

if [ -n "${MISMATCH}" ]; then
    fail "Tag '${TAG}' does not match the project version: ${MISMATCH}." \
         "The tag, pyproject.toml, src/recotem/version.py, helm/recotem/Chart.yaml" \
         "and helm/recotem/values.yaml must all agree.  A mismatch in the first two" \
         "uploads a wheel carrying a version nobody tagged; a mismatch in values.yaml" \
         "ships a chart whose manifests deploy some other image tag." \
         "" \
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
echo "    and every pinned image reference under examples/ and docs/."
echo "    Not checked here: uv.lock (run 'uv lock --check'), and version strings"
echo "    outside those files — see the verification block in"
echo "    .claude/skills/release-recotem/references/version-locations.md."
