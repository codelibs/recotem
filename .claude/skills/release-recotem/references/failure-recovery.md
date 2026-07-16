# When a release goes wrong

**PyPI is append-only.** A published version can never be replaced, reverted, or
reused. `yank` hides it from resolvers but does **not** free the number.
Deleting the git tag does not unpublish anything. If a bad version reaches PyPI,
the only remedy is to yank it and release the next patch.

Read the matching section before touching anything — several of these have a
wrong "obvious" fix that makes things worse.

## Wrong tag, not yet pushed

Free to fix. Nothing has fired.

```bash
git tag -d vX.Y.Z
```

## Wrong tag pushed, workflows still running

Race the publish step. If the PyPI upload has not completed, you can still stop
it:

```bash
gh run list --repo codelibs/recotem --branch vX.Y.Z --json databaseId,name,status
gh run cancel --repo codelibs/recotem <id>          # cancel first
git push origin :refs/tags/vX.Y.Z                   # then delete the remote tag
```

The `pypi` environment's required-reviewer gate helps here: `publish-pypi` waits
for a human approval, so an un-approved run has not uploaded yet. **Do not
approve it.**

If the upload step already completed, the version is gone forever — go to "Bad
version published to PyPI".

## Bad version published to PyPI

Do **not** retry the tag. A re-upload fails with `400 File already exists`, and
deleting the tag changes nothing on PyPI.

1. Tell the user plainly that the version is permanent.
2. Recommend yanking it (`pypi yank`, or the PyPI web UI) so resolvers skip it.
3. Release the next patch version with the fix.

## `publish.yml` succeeded but `docker.yml` emitted no version tag

The image is on GHCR only as `sha-<commit>`; no `X.Y.Z` tag exists. Almost
always the pre-release trap — the tag was not valid SemVer (see SKILL.md). The
`docker build` job is **green**, so run colour will not tell you.

Do **not** delete and re-push the tag: PyPI already has the version, so the tag
name is spent. Confirm what GHCR actually has, then decide with the user:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:codelibs/recotem:pull&service=ghcr.io" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" \
  https://ghcr.io/v2/codelibs/recotem/tags/list | python3 -m json.tool
```

The usual outcome: PyPI and GHCR are out of sync for that version, and the fix
is a new final release with a SemVer-valid tag.

## `docker.yml` succeeded but `publish.yml` failed

Recoverable — the version is **not** on PyPI, so the number is still free.
Confirm that first:

```bash
curl -s https://pypi.org/pypi/recotem/json \
  | python3 -c "import json,sys; print('X.Y.Z' in json.load(sys.stdin)['releases'])"
```

If `False`, read the run log. For a transient or OIDC/approval failure, re-run
the workflow rather than re-tagging:

```bash
gh run rerun --repo codelibs/recotem <id> --failed
```

## Red Trivy on the tag run

`trivy` is `needs: build`, so it runs *after* `Build and push` has already made
the image public. A red Trivy does not mean the image was withheld — it is
already on GHCR. The remedy is a patch release with the bumped dependency, not
a retag.

## Tag landed on the wrong commit

If the publish already fired, the wrong code is on PyPI — go to "Bad version
published". If it has not, cancel the runs, delete the remote tag, and re-tag
with an explicit SHA (`git tag vX.Y.Z <SHA>`).

## PR merged but the tag was never pushed

Harmless — nothing published. Confirm `main` is at `X.Y.Z` and resume Phase 3
from step 1.

## `uv lock` churned unrelated dependencies

Caught before commit, this is free. `git diff uv.lock` must touch only the
`recotem` entry; unrelated upgrades belong in their own PR. If it already
shipped in the release PR, decide with the user whether to revert-and-retag
(only if untagged) or accept it and note it in the CHANGELOG.
