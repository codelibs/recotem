---
name: release-recotem
description: >-
  Release a recotem version end to end. Use when cutting, shipping, or
  publishing a recotem release, bumping the version, or creating a GitHub
  Release / publishing to PyPI.
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

These exist because they each caught a real problem during 2.0.0.

- **Every change lands via a branch + PR — never commit to `main` directly.**
  Releases are outward-facing and hard to reverse; the PR is the human review
  gate.
- **The human pushes the release tag, not you.** The tag is the publish
  trigger. Prepare and verify everything, confirm the tag name with the user,
  but let them push it (or explicitly authorize you to). Re-publishing a
  version to PyPI is impossible.
- **The version must be identical in all three places**: `pyproject.toml`,
  `src/recotem/version.py`, and `uv.lock`. A mismatch between the first two
  nearly shipped in 2.0.0.
- **A release version must be a clean PEP 440 final** (`2.0.0`), never a
  pre-release (`2.0.0a1.dev0`, `2.0.0a0`). `pip install recotem` skips
  pre-releases by default, so a pre-release "release" is invisible to users.
- **Delegate the readiness audit to subagents.** It spans the whole tree
  (tests, lint, every version string, docs, workflows) and is exactly the
  context-heavy research that belongs in parallel subagents, not the main
  thread.
- **Keep commit/PR text publicly shareable** — no secrets, internal URLs, or
  Claude conversation links.

## Phase 1 — Audit readiness (subagents)

Spawn parallel subagents to gather facts; do not fix anything yet. Cover:

1. **Version state** — current string in `pyproject.toml`, `version.py`,
   `uv.lock`, and every `2.0.0a0`-style tag in `helm/`, `examples/k8s/`,
   `docs/`. See `references/version-locations.md` for the exhaustive list.
2. **Quality gates** — `uv run pytest tests`, `uv run ruff check src tests`,
   `uv run ruff format --check src tests`. All must be green.
3. **CHANGELOG** — does `CHANGELOG.md` exist and have a section for the version
   being released, including any breaking-change/migration notes?
4. **Docs staleness** — pre-release tags presented as production pins, wrong
   version-specific advice, unrendered template placeholders, missing index
   links.
5. **Publishing infra** — confirm `.github/workflows/publish.yml` (tag `v*` →
   PyPI) and `docker.yml` (tag `v[0-9]+.[0-9]+.[0-9]*` → GHCR) are present and
   that `pyproject.toml` package metadata (name, description, readme, license,
   authors, classifiers, urls) is complete.

Report blockers vs. nice-to-haves. Present findings to the user and confirm the
target version and scope before changing anything.

## Phase 2 — Prepare the release PR

Branch (e.g. `release/vX.Y.Z`) from up-to-date `main`, then:

1. **Bump the version to the final `X.Y.Z`** in `pyproject.toml` and
   `src/recotem/version.py`, then run `uv lock` to sync `uv.lock`. Use `uv`
   only — never `pip`/`python` directly.
2. **Replace pre-release image tags** (`2.0.0a0`, `2.0.0-alpha.0`, etc.) with
   `X.Y.Z` across `helm/recotem/{Chart.yaml,values.yaml}`,
   `examples/k8s/*.yaml`, and `docs/deployment/*`. Leave historical references
   in code comments / test docstrings (e.g. "since 2.0.0a0") untouched — those
   document migration history, not the current pin.
3. **Update `CHANGELOG.md`** — add the `X.Y.Z` section. For a major bump,
   include a migration guide. See `references/release-notes.md` for structure.
4. **Fold in agreed nice-to-haves** — doc inaccuracies, missing docs-index
   links, dependency upper bounds.

Then **verify before committing** (see `references/version-locations.md` for
the copy-paste verification block):

- `uv run ruff check src tests` and `uv run ruff format --check src tests`
- `uv run pytest tests`
- version is identical in all three files and `uv run python -c "from
  recotem.version import __version__; print(__version__)"` prints `X.Y.Z`
- no stale pre-release tag remains outside intentional historical comments

Commit, push, and open the PR with `gh pr create --base main`. The PR body
should summarize the changes and end with the post-merge release procedure.

## Phase 3 — Release (tag + GitHub Release)

After the PR is merged:

1. Confirm the merge commit and that the version on `main` is `X.Y.Z`.
2. **The user pushes the tag** on the merge commit:
   ```bash
   git checkout main && git pull
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```
   This triggers PyPI (`publish.yml`) and GHCR (`docker.yml`). Wait for the
   user to confirm PyPI shows the release before continuing.
3. Verify the tag points at the merge commit and that `pyproject.toml`,
   `version.py`, and `CHANGELOG.md` at the tag are correct.
4. **Create the GitHub Release** from the tag, marked latest, with notes
   derived from the CHANGELOG section:
   ```bash
   gh release create vX.Y.Z --title "vX.Y.Z" \
     --notes-file <notes.md> --latest
   ```
   See `references/release-notes.md` for the notes template (install block,
   Added/Changed/Removed/Security, migration guide, links to the tagged
   `getting-started.md` and `CHANGELOG.md`).

## Phase 4 — Sync the recotem-docs site

`recotem-docs` (separate repo, default at `../recotem-docs`, VitePress site at
recotem.org with EN + JA and a `1.0/` archive) carries its own copies of the
deployment docs. After a release its version-string references go stale.

- Branch in that repo, replace pre-release image tags with `X.Y.Z` in the EN
  and JA `docs/deployment/{docker,kubernetes}.md` (and any other version pins).
- **Leave the `1.0/` archive untouched** — it documents the legacy 1.x app.
- Verify no pre-release tag remains outside `1.0/`, then open a PR there too.

## Phase 5 — Open the next dev cycle

Bump the version to the next development pre-release so ongoing work is clearly
post-release:

- Set `pyproject.toml` and `src/recotem/version.py` to `X.(Y+1).0.dev0` (e.g.
  after `2.0.0` → `2.1.0.dev0`), then `uv lock`.
- **Deployment manifests keep pinning the released `X.Y.Z` image tag** — they
  reference a published image, not the dev version. Do not bump those.
- Branch + PR as usual. This is a PEP 440 dev pre-release, so it never triggers
  tag-driven publishing and `pip install recotem` still resolves the last final
  release.

## References

- `references/version-locations.md` — every file that carries a version string,
  the bump-and-replace commands, and the verification block.
- `references/release-notes.md` — CHANGELOG section and GitHub Release note
  templates, with the 2.0.0 release as a worked example.
