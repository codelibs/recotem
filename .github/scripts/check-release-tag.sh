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
#   2. The tag agrees with BOTH in-tree version declarations:
#        - pyproject.toml            [project] version
#        - src/recotem/version.py    __version__
#
# Usage (CI):    bash .github/scripts/check-release-tag.sh          # reads GITHUB_REF
# Usage (local): bash .github/scripts/check-release-tag.sh v2.1.0   # before tagging

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
PYPROJECT="${REPO_ROOT}/pyproject.toml"
VERSION_PY="${REPO_ROOT}/src/recotem/version.py"

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
# 3. Both in-tree version declarations must equal the tag
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

echo "pyproject.toml       version = ${PYPROJECT_VERSION}"
echo "src/recotem/version.py       = ${VERSION_PY_VERSION}"
echo "expected (from tag)          = ${EXPECTED}"

MISMATCH=""
[ "${PYPROJECT_VERSION}" = "${EXPECTED}" ] || MISMATCH="pyproject.toml (${PYPROJECT_VERSION})"
if [ "${VERSION_PY_VERSION}" != "${EXPECTED}" ]; then
    [ -n "${MISMATCH}" ] && MISMATCH="${MISMATCH} and "
    MISMATCH="${MISMATCH}src/recotem/version.py (${VERSION_PY_VERSION})"
fi

if [ -n "${MISMATCH}" ]; then
    fail "Tag '${TAG}' does not match the project version: ${MISMATCH}." \
         "The tag, pyproject.toml and src/recotem/version.py must all agree, or the" \
         "wheel uploaded to PyPI would carry a version nobody tagged." \
         "" \
         "To fix:" \
         "  1. delete the bad tag:  git tag -d ${TAG} && git push origin :refs/tags/${TAG}" \
         "  2. set version = \"${EXPECTED}\" in pyproject.toml and" \
         "     __version__ = \"${EXPECTED}\" in src/recotem/version.py (or pick another number)" \
         "  3. commit, merge, and re-tag the merge commit"
fi

echo "OK: ${TAG} is a final release and matches both version declarations."
