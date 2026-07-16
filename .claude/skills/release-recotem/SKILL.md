---
name: release-recotem
description: >-
  Use when cutting, shipping, publishing, or tagging a recotem release,
  preparing a release PR, bumping the version for a release or for the next dev
  cycle, creating a GitHub Release, publishing to PyPI, or syncing recotem-docs
  after a release — even if the word "release" isn't used.
---

# Release recotem

Take recotem from "main looks ready" to "published on PyPI + GHCR with a
GitHub Release and the next dev cycle open." Publishing is **tag-driven**:
pushing a `vX.Y.Z` tag fires `publish.yml` (PyPI, OIDC trusted publishing) and
`docker.yml` (GHCR multi-arch image). Everything else is preparation around
that single irreversible trigger.

```
audit readiness → prepare PR (version + CHANGELOG + docs) → verify →
merge → push tag (human) → GitHub Release → sync recotem-docs → open dev cycle
```

## Non-negotiable principles

- **Every change lands via a branch + PR — never commit to `main` directly.**
  Releases are outward-facing and hard to reverse; the PR is the human review
  gate.
- **You never push the release tag. There is no authorization path.** The tag
  is the publish trigger and PyPI is append-only: a published version can never
  be replaced, reverted, or reused — only yanked, which does not free the
  number. Prepare everything, verify everything, confirm the exact tag name,
  then hand the command to the user and stop. "Release it", "go ahead", and
  "you do it" are not exceptions to this.
- **Nothing tests the code between the tag and PyPI.** `test.yml` has no `tags:`
  trigger, `publish.yml` has no test step, and `main` has no required status
  checks. The pre-tag verification in Phase 3 is the *only* gate. (2.0.0's
  `publish-pypi` completed while the main test run was still finishing — the
  code was on PyPI before its own tests were green.)
- **The version must be identical in all three places**: `pyproject.toml`,
  `src/recotem/version.py`, and `uv.lock`. A mismatch between the first two
  nearly shipped in 2.0.0.
- **A release version must be a clean PEP 440 final** (`2.0.0`), never a
  pre-release (`2.0.0a1.dev0`, `2.0.0a0`). Two independent reasons: `pip install
  recotem` skips pre-releases by default, and `docker.yml` cannot tag one — see
  "The pre-release trap" below.
- **Delegate the readiness audit to subagents.** It spans the whole tree and is
  exactly the context-heavy research that belongs in parallel subagents.
- **Verify every command against the tree before trusting it.** Commands that
  hardcode a version literal go stale silently. This skill's own first draft
  shipped `sed` commands that matched zero lines while reporting success.
- **Keep commit/PR text publicly shareable** — no secrets, internal URLs, or
  Claude conversation links.

## Red flags — STOP

Each of these means stop and hand control back to the user:

- About to run `git push origin vX.Y.Z` yourself.
- "The user said 'release it' — that's authorization enough."
- "I'll push the tag; they can delete it if it's wrong." (PyPI cannot be undone.)
- "Committing straight to `main` saves a round-trip on a one-line bump."
- "The version only differs in `uv.lock`, that's cosmetic."
- A verification step printed nothing and you read that as success.

## The pre-release trap

A pre-release tag publishes to PyPI **and pushes an image with no version tag,
while the build stays green**. Both workflows fire, but `docker.yml`'s
`docker/metadata-action` uses `type=semver`, which parses SemVer only. PEP 440
and SemVer disagree on pre-releases: PEP 440 writes `2.0.0a0`, SemVer requires
`2.0.0-a0`. metadata-action cannot parse the PEP 440 form, logs
`##[warning]v2.0.0a0 is not a valid semver`, and emits **no version tags** —
only `sha-<commit>`. The `Extract Docker metadata` step still *succeeds*, so the
build is green and the image is pushed under the sha tag alone.

This is not theoretical: `v2.0.0a0` put `2.0.0a0` on PyPI while GHCR received
only `sha-9444999`. No `2.0.0a0` image tag exists to this day. That run did go
red, but from an unrelated Trivy CVE failure in a separate job — **never rely on
run colour to catch this.** For a clean final version the two standards agree,
so `type=semver` resolves and emits `X.Y.Z`, `X.Y`, and `latest`.

## Phase 1 — Audit readiness (subagents)

Spawn parallel subagents to gather facts; do not fix anything yet. Cover:

1. **Version state** — current string in `pyproject.toml`, `version.py`,
   `uv.lock`, and every image pin in `helm/`, `examples/k8s/`, `docs/`. See
   `references/version-locations.md` for the exhaustive list.
2. **Quality gates** — `uv run pytest tests`, `uv run pytest tests -m slow`,
   `uv run ruff check src tests`, `uv run ruff format --check src tests`. All
   must be green. **`-m slow` is the one tier CI never runs** (`test.yml` runs
   `-m "not slow"`), so release is the only time it gets exercised.
3. **CHANGELOG** — is there a section for the version being released, including
   breaking-change/migration notes? See `references/release-notes.md`.
4. **Open PR queue** — `gh pr list --author app/dependabot`. Dependabot opens up
   to 13 PRs/week (5 uv + 5 github-actions + 3 docker) and the 2.0.0 release
   drained six of them in the hours before the tag. Decide what lands before the
   tag. Check separately for pending CVE bumps: Trivy-flagged fixes are what
   populate the Security section, and Trivy fails this project in practice.
5. **Docs staleness** — wrong version-specific advice, unrendered placeholders,
   missing index links.
6. **Publishing infra** — confirm `publish.yml` (tag `v*` → PyPI) and
   `docker.yml` (tag `v[0-9]+.[0-9]+.[0-9]*` → GHCR) are present and that
   `pyproject.toml` metadata (name, description, readme, license, authors,
   classifiers, urls) is complete.

Report blockers vs. nice-to-haves. Present findings to the user and confirm the
target version and scope before changing anything.

## Phase 2 — Prepare the release PR

Branch (e.g. `release/vX.Y.Z`) from up-to-date `main`, then:

1. **Bump the version to the final `X.Y.Z`** in `pyproject.toml` and
   `src/recotem/version.py`, then run `uv lock`. Use `uv` only — never `pip` /
   `python` directly. **`git diff uv.lock` must touch only the `recotem`
   entry** — unrelated dependency churn belongs in its own PR.
2. **Bump the deployment image pins** to `X.Y.Z` across `helm/`,
   `examples/k8s/`, and `docs/deployment/`. Use the verified commands in
   `references/version-locations.md` — they distinguish real pins from
   illustrative examples, which a blanket replace corrupts.
3. **Update `CHANGELOG.md`** — see `references/release-notes.md`. If an
   `Unreleased` section for this version already exists, rename it; do not add a
   second one.
4. **Fold in agreed nice-to-haves** — doc inaccuracies, missing index links.

Then run the full verification block in `references/version-locations.md` before
committing. It covers the three version locations, every deployment pin, and the
quality gates.

Also sanity-check the artifact *before* the irreversible step, because
`publish.yml` builds the wheel for the first time at the tag:

```bash
uv build && uv run --with twine twine check dist/*
```

Commit, push, and open the PR with `gh pr create --base main`.

## Phase 3 — Release (tag + GitHub Release)

**Everything is verified before the tag, because nothing can be fixed after it.**

1. **Pin and verify the merge commit.** Do not rely on `main` staying put —
   dependabot merges land roughly daily and would silently retarget the tag.

   ```bash
   git checkout main && git pull
   SHA=$(git rev-parse HEAD)
   git log -1 --format='%h %s' "$SHA"                      # the release PR merge
   git show "${SHA}:pyproject.toml" | grep '^version'      # X.Y.Z
   git show "${SHA}:src/recotem/version.py"                # X.Y.Z
   ```
   (Brace the variable: `"$SHA:src/..."` triggers zsh's `:s` history modifier
   and mangles the path; `"${SHA}:src/..."` is safe in both bash and zsh.)

2. **Confirm CI is green on that exact commit** — not on a recent run, and not
   just locally. Never filter `gh run list` by a lookback window; an old commit
   falls out of the window and reads as "no runs". Query the SHA directly:

   ```bash
   gh api "repos/codelibs/recotem/commits/$SHA/check-runs" \
     --jq '[.check_runs[] | select(.conclusion | IN("success","neutral","skipped") | not)] | length'
   ```
   Must print `0`. Anything else means do not tag.

3. **Confirm the version is not already published.** PyPI refuses re-upload.

   ```bash
   curl -s https://pypi.org/pypi/recotem/json \
     | python3 -c "import json,sys; print('X.Y.Z' in json.load(sys.stdin)['releases'])"
   ```
   Must print `False`.

4. **STOP — the user pushes the tag.** Confirm the exact tag name character by
   character (watch for `O`/`0` and `l`/`1`), then give them these two lines as
   plain text and wait. Do not run them:

       git tag vX.Y.Z <SHA>
       git push origin vX.Y.Z

5. **Tell the user to approve the `pypi` deployment.** The `pypi` environment
   has a required reviewer (`marevol`) and a `v*` tag branch policy, so
   `publish-pypi` halts pending a manual approval in the Actions UI. Without it
   the release never publishes — and only that reviewer can approve.

6. **Watch both runs** — `publish` and `docker`. `gh run watch` follows one run
   at a time, and the important one is `publish` (it holds the PyPI upload behind
   the approval gate). List both, then watch each:

   ```bash
   gh run list --repo codelibs/recotem --branch vX.Y.Z \
     --json databaseId,name,status --jq '.[] | "\(.databaseId) \(.name)"'
   gh run watch --repo codelibs/recotem <publish-run-id>
   gh run watch --repo codelibs/recotem <docker-run-id>
   ```

   Note that `docker.yml`'s `trivy` job is `needs: build`, so it scans *after*
   the image is already public. A red Trivy means the image is already out
   there; the remedy is a patch release, not a retag.

7. **Verify both registries actually received the version.** A green
   `docker.yml` does not prove a version tag was emitted (see "The pre-release
   trap"):

   ```bash
   curl -s https://pypi.org/pypi/recotem/json \
     | python3 -c "import json,sys; print('X.Y.Z' in json.load(sys.stdin)['releases'])"

   TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:codelibs/recotem:pull&service=ghcr.io" \
     | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
   curl -s -H "Authorization: Bearer $TOKEN" \
     https://ghcr.io/v2/codelibs/recotem/tags/list \
     | python3 -c "import json,sys; print('X.Y.Z' in json.load(sys.stdin)['tags'])"
   ```
   Both must print `True`.

8. **Create the GitHub Release** from the tag, marked latest, with notes derived
   from the CHANGELOG section:

   ```bash
   gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <notes.md> --latest
   ```

If anything goes wrong, read `references/failure-recovery.md` before touching
anything.

## Phase 4 — Sync the recotem-docs site

`recotem-docs` (https://github.com/codelibs/recotem-docs, expected at
`../recotem-docs`) is a separate VitePress repo serving recotem.org with EN + JA
and a `1.0/` archive. It carries its own copies of the deployment docs, whose
version pins go stale after a release. If it is not checked out locally, clone
it; if you lack push access, hand the change list to the user.

- Branch there and bump the pins to `X.Y.Z` in the EN and JA
  `docs/deployment/{docker,kubernetes}.md`. See `references/version-locations.md`.
- **Leave the `1.0/` archive untouched** — it documents the legacy 1.x app.
- Verify no stale pin remains outside `1.0/`, then open a PR there too.

## Phase 5 — Open the next dev cycle

Bump the version to the next development pre-release so ongoing work is clearly
post-release:

- Set `pyproject.toml` and `src/recotem/version.py` to the next dev version,
  then `uv lock`. Pick the base from what is actually expected next, and ask if
  it is not obvious: after a major/minor release, `X.(Y+1).0.dev0` (2.0.0 →
  2.1.0.dev0); after a patch release, `X.Y.(Z+1).dev0` (2.0.1 → 2.0.2.dev0).
- **Deployment manifests keep pinning the released `X.Y.Z` image tag** — they
  reference a published image, not the dev version. Do not bump those, and do
  not run the Phase 2 pin verification here; it would flag them.
- **Never tag a dev version.** Publishing is triggered by *tags*, not by version
  strings. A `.dev0` version in `pyproject.toml` is inert on its own, but
  pushing a `v2.1.0.dev0` tag *would* fire `publish.yml` (whose filter is `v*`)
  and burn that version on PyPI permanently.
- Branch + PR as usual.

## Common mistakes

| Symptom | Cause / fix |
|---|---|
| Tag pushed, no PyPI release | `publish.yml` needs a leading `v`; or the `pypi` environment approval is still pending. |
| Tag pushed, no GHCR **version** tag (only `sha-`) | Tag isn't valid SemVer — the pre-release trap. The build stays green. |
| PyPI rejects the upload | Version already published. PyPI never allows re-upload — bump the patch. |
| `pip install recotem` gets the old version | A pre-release was released; pip skips pre-releases by default. |
| Verification printed nothing and you called it clean | A grep that only looks for an old literal cannot fail. Use the inverted block in `references/version-locations.md`. |
| Deployment pins still on the previous release | The replace commands matched nothing and exited 0. The diff must be non-empty. |
| Tag landed on the wrong commit | `git tag` without an explicit SHA tags whatever `main` is right now. |

## References

- `references/version-locations.md` — every file that carries a version string,
  the verified bump commands, and the verification block.
- `references/release-notes.md` — how CHANGELOG entries accumulate, plus the
  CHANGELOG section and GitHub Release note templates.
- `references/failure-recovery.md` — what to do when a release goes wrong.
