# Phase 4 — recotem-docs site sync

Read this when Phase 4 of `SKILL.md` sends you here. Everything below runs in
the `recotem-docs` repo, not in `recotem`. Do 4A before 4B.

## Phase 4A — Freeze the old stable, promote the new one

Skip this for a patch release (`X.Y.Z` where `Z > 0`) — a patch does not create
a new documentation line. Run it for every minor or major.

The site model (`specs/2026-07-20-docs-versioning-design.md` §2–§3): the
unversioned root (`/docs/ /guide/ /learn/` + `/ja/…`) is the current stable and
the only indexed tree; every `X.Y/` directory is a noindexed, self-canonical
archive or preview. At release, the outgoing stable is frozen into its own
`X.Y/` directory and the incoming preview is promoted to the root, so **every
canonical URL keeps working and starts serving the new version**.

Releasing 2.1.0 (`OLD=2.0`, `NEW=2.1`), from the `recotem-docs` root:

1. **Freeze the outgoing stable into `$OLD/`.** Copy the root tree, EN and JA,
   into a new `$OLD/` directory laid out like the existing `1.0/`: version
   directories are self-contained under the root locale, so JA goes to
   `$OLD/ja/…`, not into a locale-routed path.

   ```bash
   mkdir -p 2.0/ja
   cp -R docs guide learn index.md 2.0/
   cp -R ja/docs ja/guide ja/learn ja/index.md 2.0/ja/
   ```

   Copy **every** root content directory, not just the ones the preview
   happens to contain. `learn/` in particular exists at the root but **not**
   under `2.1/`; if the freeze skips it, the archive has a hole and the
   promote in step 2 must not touch it either.

2. **Promote the preview to the root.** Replace only the directories the
   preview actually carries, and delete the old ones first so a file removed
   during 2.1 development does not survive the copy:

   ```bash
   rm -rf docs guide ja/docs ja/guide
   cp -R 2.1/docs 2.1/guide ./
   cp -R 2.1/ja/docs 2.1/ja/guide ja/
   ```

   **Do NOT copy `2.1/index.md` over the root `index.md`.** The root landing
   page is a VitePress `layout: home` hero (`hero:` / `features:`
   frontmatter); `2.1/index.md` is a plain preview landing page carrying an
   "in-development preview" warning banner and links into `/2.1/…`. Overwriting
   the hero replaces the site's front page with a preview notice. Same for
   `ja/index.md` and `2.1/ja/index.md`. Leave `learn/` and `ja/learn/` in
   place for the same reason the freeze had to copy them: the preview has no
   `learn/`.

3. **Rewrite the promoted tree's absolute version links.** The preview's pages
   link with absolute `/2.1/…` URLs (~40 of them across the EN and JA guide,
   `docs/data-sources/plugins.md`, and both index pages). Once promoted these
   must point at the unversioned root, or the new stable docs link back into a
   preview directory that is about to be deleted:

   ```bash
   grep -rl '/2\.1/' --include='*.md' docs guide ja/docs ja/guide \
     | xargs perl -pi -e 's{/2\.1/ja/}{/ja/}g; s{/2\.1/}{/}g;'
   grep -rn '/2\.1/' --include='*.md' docs guide ja/docs ja/guide   # MUST be empty
   ```

   Order matters in that substitution: rewrite `/2.1/ja/` before the bare
   `/2.1/`, or the JA links lose their locale prefix.

4. **Retire the preview directory.** `rm -rf 2.1`. Note the deploy is `scp`
   without `--delete`, so the removed files linger server-side; that is
   expected and harmless (they are noindexed), but do not treat a still-live
   `/2.1/` URL as a failed release.

5. **Update the VitePress wiring** (`.vitepress/config.ts`):

   - **Sidebars.** The EN locale registers `/2.1/guide/`, `/2.1/docs/`,
     `/2.1/ja/guide/`, `/2.1/ja/docs/`, and the JA locale registers the two
     `/2.1/ja/…` keys again. Delete all six and add the frozen archive's:
     `'/2.0/guide/': v2GuideSidebar('en', '/2.0')`, `'/2.0/docs/':
     v2DocsSidebar('en', '/2.0')`, and the `/2.0/ja/…` pair in both locales —
     mirroring exactly how the `/2.1/` keys were registered. The root
     `'/guide/'` / `'/docs/'` / `'/learn/'` registrations already point at the
     unversioned tree and need no change.
   - **No noindex work is required.** `transformPageData` and
     `sitemap.transformItems` already match every version directory generically
     (`/^\d+\.\d+\//`), so the new `2.0/` tree is noindexed and out of the
     sitemap the moment it exists.

6. **Update the version switcher**
   (`.vitepress/theme/VersionSwitcher.vue`). It hardcodes the version set. For
   a 2.1 release: the `isV21` check becomes an `isV20` check on `2.0/`,
   `currentVersion` returns `'2.1'` for the unversioned tree, `v21Link` becomes
   `v20Link` (`/2.0/` and `/2.0/ja/`), **`isJa`'s `p.startsWith('2.1/ja/')`
   becomes `p.startsWith('2.0/ja/')`**, and the three menu entries become
   **2.1** (unversioned root, marked latest), **2.0** (`/2.0/…`), **1.0**
   (`/1.0/…`). Check the `:class="{ active: … }"` bindings too — they key off
   the same booleans and will silently highlight the wrong entry.

   `isJa` is the one that gets missed, because its name carries no version.
   Leave it on `2.1/ja/` and every page of the freshly frozen JA archive
   evaluates `isJa === false`, so `latestLink` and `v1Link` resolve to `/` and
   `/1.0/`: the switcher sends Japanese readers to the English tree. Do not
   eyeball it — no version *path* for the promoted version may survive in this
   file, and the frozen one must appear:

   ```bash
   grep -Fn '2.1/' .vitepress/theme/VersionSwitcher.vue   # MUST be empty
   grep -Fc '2.0/' .vitepress/theme/VersionSwitcher.vue   # MUST be > 0
   ```

   Match on `2.1/` **with the trailing slash**, not on `2.1`: after the promote
   `2.1` legitimately survives as the `currentVersion` string and as a menu
   label — only the *paths* move.

7. **Verify before opening the PR.**

   ```bash
   yarn docs:build
   ```

   The build's dead-link check is the gate that catches a missed link rewrite.
   Then confirm in the built output that `/2.0/**` and `/1.0/**` carry
   `noindex`, that the unversioned `/docs/**` does **not**, that the sitemap
   excludes every `X.Y/` directory, and that the root front page still renders
   the hero rather than a preview banner.

   **Then check the version button the reader actually sees.** Step 6's two
   greps are path-only *by design* — they match `2.1/` with a trailing slash,
   because after the promote the bare string `2.1` legitimately survives as
   `currentVersion` and as a menu label. So neither of them, and not the build
   either, can see a `currentVersion` ternary whose stable arm was left on the
   outgoing version. That mutation renders **`2.0`** on every page of the tree
   that just became stable, in both languages, with all three checks green:

   ```
   currentVersion stable arm left on '2.0'   grep 2.1/ : pass   grep 2.0/ : pass   build: 0   rendered: 2.0
   ternary arms rotated (all tokens kept)    grep 2.1/ : pass   grep 2.0/ : pass   build: 0   rendered: 2.0
   isJa left on '2.1/ja/'                    grep 2.1/ : FAIL   grep 2.0/ : pass   build: 0   rendered: 2.1
   ```

   The third row is the case step 6 warns about, and its grep does catch it.
   The first two are the label, which nothing was watching. Assert the rendered
   string, on one page per locale:

   ```bash
   # `cut`, not ${NEW%.*}: this phase sets NEW=2.1 (see the top of 4A) while
   # version-locations.md sets NEW=2.1.0. ${NEW%.*} yields "2" under
   # the first convention and fails a correct promote; cut yields 2.1 under both.
   MM=$(printf '%s' "$NEW" | cut -d. -f1,2)
   MM_RE=$(printf '%s' "$MM" | sed 's/\./\\./g')   # the dot is a literal, not "any char"
   BAD=""
   for f in docs/security.html ja/docs/operations.html; do
     grep -oE 'class="version-button"[^>]*>[^<]*' ".vitepress/dist/$f" \
       | grep -qE ">[[:space:]]*${MM_RE}[[:space:]]*$" || BAD="$BAD $f"
   done
   [ -z "$BAD" ] || { echo "FAIL: version switcher does not read $MM on:$BAD"; exit 1; }
   echo "OK: the promoted tree's version button reads $MM"
   ```

   Both locales, because `isJa` decides which link set the switcher uses and a
   JA-only regression is exactly the one step 6 calls "the one that gets
   missed".

## Phase 4B — Bump the version pins

The docs repo carries its own copies of the deployment docs, whose version pins
go stale after a release.

- Branch there and bump the pins to `X.Y.Z`. `version-locations.md`
  has the exact edit and the verification; it discovers the files to edit
  rather than hardcoding them, because the set changes as version directories
  come and go.
- **Leave every archived `X.Y/` directory untouched** — `1.0/` documents the
  legacy 1.x app, and a freshly frozen `2.0/` must keep documenting 2.0.
- Verify no stale pin remains in any live (non-archive) tree, then open a PR
  there too. If 4A ran, both jobs belong in the same PR.
