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

## `publish.yml` succeeded but GHCR has no image for the version

Not the pre-release trap. Both workflows run the same
`.github/scripts/check-release-tag.sh` guard, so a tag `docker.yml` refuses is a
tag `publish.yml` refuses too — neither registry receives anything, and the runs
are red. (The converse does **not** hold; see the next section.) What is still
reachable is `docker.yml` going red *after* `publish-pypi` succeeded, almost
always at `trivy`: the scan gates the push (`build` is
`needs: [test, smoke, trivy]`), so a CVE finding leaves the version on PyPI with
nothing on GHCR.

Do **not** delete and re-push the tag: PyPI already has the version, so the tag
name is spent. Confirm what GHCR actually has, then decide with the user:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:codelibs/recotem:pull&service=ghcr.io" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" \
  https://ghcr.io/v2/codelibs/recotem/tags/list | python3 -m json.tool
```

The usual outcome: PyPI and GHCR are out of sync for that version. Re-run the
failed `docker.yml` jobs if Debian has since published the CVE fix (the
Dockerfile runs `apt-get upgrade`, so no repo change is needed); if the fix
needs a repo change it has to be in the *tagged* tree, which means a patch
release. SKILL.md Phase 3, step 6 has the full decision.

## `docker.yml` succeeded but `publish.yml` failed

The two guards are **not** mirror images. `publish.yml`'s `guard` runs a second
script that `docker.yml`'s does not — `.github/scripts/check-milestone-landed.sh`
— so this state is reachable by design, not only by a transient fault. Read the
run log and split on which step went red.

Either way the version is **not** on PyPI, so the number is still free. Confirm
that first:

```bash
curl -s https://pypi.org/pypi/recotem/json \
  | python3 -c "import json,sys; print('X.Y.Z' in json.load(sys.stdin)['releases'])"
```

**Case 1 — transient, OIDC, or approval failure.** Re-run rather than re-tag:

```bash
gh run rerun --repo codelibs/recotem <id> --failed
```

**Case 2 — red at *release tag guard*, on the milestone step.** A PR the release
milestone calls MERGED is not reachable from the tagged commit. Do **not**
re-run: the script asks whether each merge commit is an ancestor of the *tagged*
commit, and the remedy for a stranded PR is a re-land, which produces a **new**
commit on `main` that the existing tag can never reach. The run will fail
identically forever. `.github/relanded-prs.tsv` does not help either — a record
is honoured only when the replacement PR's own merge commit is already an
ancestor, which is exactly what is missing.

The sequence is:

1. Re-land the stranded PR onto `main` and merge it (`git cherry-pick -n
   <merge-commit>`, new PR with `base=main`; add the
   `.github/relanded-prs.tsv` row so future releases do not trip on the dead
   merge commit).
2. Delete the tag — `git push origin :refs/tags/vX.Y.Z` — and re-cut it on the
   new `main`. Reusing the number is safe for PyPI, which received nothing.
3. Say plainly that **GHCR already carries `X.Y.Z`, built from the tree without
   the missing PR** — `docker.yml` passed its own guard and pushed. Until the
   re-cut run finishes, anyone pulling that tag gets the wrong image, and it
   was never announced. Re-check the digest afterwards rather than assuming the
   second push replaced it (SKILL.md Phase 3, step 7).

Prevent it instead: `bash .github/scripts/check-milestone-landed.sh vX.Y.Z` is
part of Phase 3 step 1 and costs one API call.

## Red Trivy on the tag run

`trivy` is `needs: smoke` and `build` is `needs: [test, smoke, trivy]`, so the
scan runs *before* `Build and push`. A red Trivy means the push **was**
withheld: nothing reached GHCR, and there is nothing published to replace or
un-publish. PyPI publishes from a separate workflow and is unaffected, so the
release is half-landed — the version is on PyPI with no image beside it, and a
green `publish` hides that. Say so before doing anything else.

Fix it forward; the tag does not move. `ignore-unfixed: true` means the finding
is fixable, so there are two cases:

1. Debian has published the fix since the run. The Dockerfile's `apt-get
   upgrade` picks it up on a plain re-run, with no repo change at all:

   ```bash
   gh run rerun --repo codelibs/recotem <docker-run-id> --failed
   ```

2. The fix needs a repo change. It has to be in the *tagged* tree, so it takes
   a patch release — that is the only case where one is warranted here.

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
(only if untagged) or accept it and note it in the GitHub Release notes.
