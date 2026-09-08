# Phase 4 — recotem-docs site sync

Read this when Phase 4 of `SKILL.md` sends you here. Everything below runs in
the `recotem-docs` repo, not in `recotem`. Do 4A before 4B.

## Phase 4A — Promote the new stable, keep its version directory

Skip this for a patch release (`X.Y.Z` where `Z > 0`) — a patch does not create
a new documentation line. Run it for every minor or major.

The site model lives in **`recotem-docs/CLAUDE.md`** ("Documentation
versioning" and "Version lifecycle"); the design detail is in
`specs/2026-07-20-docs-versioning-design.md` §2–§3. Keep this file and that one
consistent — if you change the lifecycle here, change it there in the same PR.
The unversioned root (`/docs/ /guide/ /learn/` + `/ja/…`) is the current stable
and the only indexed tree; every `X.Y/` directory is a noindexed,
self-canonical copy of one version's docs.

**A version directory is created once and never deleted.** It is the
in-development preview while `X.Y` is being built, and the permanent archive of
`X.Y` once it ships. The promote **copies** its content to the root; it does not
move it. So a release does two things to `X.Y/`: nothing to its content, and one
edit to its landing page (step 4).

**Why it is never deleted.** The product bakes `https://recotem.org/X.Y/docs/…`
URLs into shipped source — the text of a `DataSourceError`, the JSON Schema
`recotem schema` emits for IDEs, the HELP string served at `/v1/metrics`,
`README.md` as rendered on PyPI (see `references/version-locations.md`,
"Documentation-site URLs"). Those URLs name the version being released, so
deleting `X.Y/` as part of shipping `X.Y.0` would break every one of them for
that version's **entire support window** — precisely the period during which
they are read. An earlier version of this file ended 4A with `rm -rf 2.1` and
noted that the removed files linger because the deploy is `scp` without
`--delete`. That is an accident of the transport, not a contract; it does not
survive a clean redeploy, and nothing about it was ever verified.

The cost is that the current stable's content is served at two paths. That is
accepted: everything matching `/^\d+\.\d+\//` is `noindex` + self-canonical and
excluded from the sitemap, so the copy never competes with the root in search.

Releasing 2.1.0 (`OLD=2.0`, `NEW=2.1`), from the `recotem-docs` root:

1. **One-time, at the 2.1.0 release only: freeze the outgoing root into
   `$OLD/`.** 2.0 shipped before this model, so it has no directory of its own
   and its documentation exists only at the unversioned root that step 2 is
   about to overwrite. **From 2.2 onward this step does not exist** — every
   version directory already exists from its preview phase, and there is
   nothing to freeze.

   Copy the versioned content directories, EN and JA, into a new `$OLD/`
   directory laid out like the existing `1.0/`: version directories are
   self-contained under the root locale, so JA goes to `$OLD/ja/…`, not into a
   locale-routed path.

   ```bash
   mkdir -p 2.0/ja
   cp -R docs guide index.md 2.0/
   cp -R ja/docs ja/guide ja/index.md 2.0/ja/
   ```

   **Do not copy `learn/`.** It is version-agnostic and shared: the site's
   `CLAUDE.md` says so, `1.0/` has none, and the `2.1/` preview ships without
   one. Copying it duplicates 24 pages that no sidebar key matches — `/learn/`
   does not match `/2.0/learn/` — so they render with no sidebar at all, the
   only pages in the built site that do. It also leaves no hole: `docs/**` and
   `guide/**`, EN and JA, contain **zero** links into `/learn/`, while the same
   grep finds 64 inside `learn/` itself.

   Then **rewrite the frozen tree's absolute links so they stay inside
   `$OLD/`.** The stable tree links with absolute `/docs/…` and `/guide/…` URLs
   by design (see the site's `CLAUDE.md`). Copied verbatim, every one of them
   points at the tree that step 2 is about to replace with the *new* version:

   ```bash
   grep -rl '](/docs/\|](/guide/\|](/ja/docs/\|](/ja/guide/' --include='*.md' 2.0 \
     | xargs perl -pi -e 's{\]\(/ja/(docs|guide)/}{](/2.0/ja/$1/}g; s{\]\(/(docs|guide)/}{](/2.0/$1/}g;'
   # the two landing pages carry their hero CTA as a YAML `link:`, not markdown
   perl -pi -e 's{^(\s*link:\s*)/ja/(docs|guide)/}{$1/2.0/ja/$2/}; s{^(\s*link:\s*)/(docs|guide)/}{$1/2.0/$2/};' \
     2.0/index.md 2.0/ja/index.md
   # MUST print nothing
   grep -rno '](/[a-z0-9./-]*' --include='*.md' 2.0 | grep -v '](/2\.0/' | grep -v '/learn/'
   ```

   Rewrite `](/ja/…)` before the bare `](/…)`, for the same reason step 3 does.
   Leave `](/learn/…)` alone — `learn/` stays shared and unversioned, so those
   links are already right. This is the mirror image of the rewrite step 3 does
   on the promoted tree and of the one Phase 5 does when it seeds the next
   preview; the freeze needs it for the same reason and had no equivalent.

   A preview directory needs none of this at its own release: it was authored
   with `/X.Y/…` links from the start, so the copy that stays behind is already
   correct. Only the copy promoted to the root is rewritten (step 3).

2. **Promote the preview's content to the root.** Copy, do not move — `2.1/`
   stays exactly where it is. Replace only the directories the preview actually
   carries, and delete the old ones first so a file removed during 2.1
   development does not survive the copy:

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
   place: they are shared and unversioned, the preview has no `learn/` to
   promote, and the freeze deliberately did not copy them either.

3. **Rewrite the promoted copy's absolute version links.** The preview's pages
   link with absolute `/2.1/…` URLs (~40 of them across the EN and JA guide,
   `docs/data-sources/plugins.md`, and both index pages). At the root these
   must point at the unversioned root, or the new stable docs send every reader
   sideways into the archive:

   ```bash
   grep -rl '/2\.1/' --include='*.md' docs guide ja/docs ja/guide \
     | xargs perl -pi -e 's{/2\.1/ja/}{/ja/}g; s{/2\.1/}{/}g;'
   grep -rn '/2\.1/' --include='*.md' docs guide ja/docs ja/guide   # MUST be empty
   ```

   Order matters in that substitution: rewrite `/2.1/ja/` before the bare
   `/2.1/`, or the JA links lose their locale prefix.

   **That second grep is now the only thing watching this step.** When the
   promote deleted `2.1/`, a page left pointing at `/2.1/…` was a dead link and
   `yarn docs:build` failed on it. `2.1/` now still exists, so the same mistake
   builds clean and ships — a stable page quietly linking into the archive. Run
   the grep and read it; do not rely on step 7's build.

   Note the scope: `docs guide ja/docs ja/guide`, the promoted copies only.
   Never run this rewrite over `2.1/` itself — its `/2.1/…` links are correct
   and must stay.

4. **Turn the kept directory's landing page into an archive notice.** This
   replaces the old `rm -rf 2.1` and is the *only* edit `2.1/` receives.
   `2.1/index.md` and `2.1/ja/index.md` were written as preview landing pages:
   a title and `description` ending in "(in development)" / "(開発中)", an H1
   the same, and a `::: warning In-development preview` / `::: warning
   開発中プレビュー` container saying the content may still change. All of that
   is now false — this is the shipped 2.1 documentation.

   In both files, drop "(in development)" / "(開発中)" from the title,
   description and H1, and replace the warning container with an
   archived-version notice: this is the documentation for Recotem 2.1, kept at
   a stable URL for readers running that release, and the current stable
   documentation is at the site root. Keep the rest of both pages as they are —
   the `/2.1/guide/` and `/2.1/docs/` links are internal to the archive and
   correct, and the "Looking for the stable docs?" section already points at
   the root, which is exactly what an archive wants.

5. **Update the VitePress wiring** (`.vitepress/config.ts`):

   - **Sidebars.** Every `X.Y/` directory that exists needs its keys, and none
     are removed at a release, because no directory is removed at a release.
     The EN locale registers `/2.1/guide/`, `/2.1/docs/`, `/2.1/ja/guide/`,
     `/2.1/ja/docs/`, and the JA locale registers the two `/2.1/ja/…` keys
     again. **Keep all six** — 2.1 is now an archive, and an archive with no
     sidebar key renders with no sidebar at all. Add, in the same shape:
     `'/2.0/guide/': v2GuideSidebar('en', '/2.0')`, `'/2.0/docs/':
     v2DocsSidebar('en', '/2.0')`, and the `/2.0/ja/…` pair in both locales,
     for the one-time `2.0/` freeze of step 1. The next preview's `/2.2/…` keys
     are added when Phase 5 seeds that directory, not here. The root
     `'/guide/'` / `'/docs/'` / `'/learn/'` registrations already point at the
     unversioned tree and need no change.
   - **No noindex work is required.** `transformPageData` and
     `sitemap.transformItems` already match every version directory generically
     (`/^\d+\.\d+\//`), so the new `2.0/` tree is noindexed and out of the
     sitemap the moment it exists, and `2.1/` stays noindexed exactly as it was
     as a preview.

6. **Update the version switcher**
   (`.vitepress/theme/VersionSwitcher.vue`). It hardcodes the version set. For
   a 2.1 release: the `isV21` check becomes an `isV20` check on `2.0/`,
   `currentVersion` returns `'2.1'` for the unversioned tree, `v21Link` becomes
   `v20Link` (`/2.0/` and `/2.0/ja/`), **`isJa` gains
   `p.startsWith('2.0/ja/')` and keeps `p.startsWith('2.1/ja/')`**, and the
   three menu entries become **2.1** (unversioned root, marked latest), **2.0**
   (`/2.0/…`), **1.0** (`/1.0/…`). Check the `:class="{ active: … }"` bindings
   too — they key off the same booleans and will silently highlight the wrong
   entry.

   `isJa` is the one computed that **accumulates** rather than rotates. Every
   other name here refers to the version set the switcher offers, and that set
   moves on at a release. `isJa` answers a different question — "is this page
   Japanese?" — for every directory that exists, and after this release both
   `2.0/ja/` and `2.1/ja/` do. This is what changed when the promote stopped
   deleting `X.Y/`: replacing the `2.1/ja/` arm was correct while that
   directory was about to be removed, and is a regression now that it is
   kept.

   `2.1/` still exists after the promote, and it is deliberately **not** given
   its own menu entry: it holds the same pages as the root (differing only in
   the link rewrite of step 3), so listing it would offer the reader two
   entries serving one version. It
   starts appearing in the switcher at the next release, as `/2.1/…`, when the
   root moves on to 2.2. That is why the grep below is still right.

   `isJa` is the one that gets missed, because its name carries no version,
   and it can now be missed in **either** direction. Omit `2.0/ja/` and every
   page of the freshly frozen JA archive evaluates `isJa === false`; drop
   `2.1/ja/` and every page of the kept 2.1 archive does. Either way
   `latestLink` and `v1Link` resolve to `/` and `/1.0/` and the switcher sends
   Japanese readers to the English tree. The 2.1 half is the one that is read:
   `recotem.org/2.1/…` is what the product bakes into shipped source, so that
   archive carries this version's traffic for its whole support window.

   Do not eyeball it. No version *path* for the promoted version may survive
   **outside `isJa`**, the frozen one must appear, and the kept one must
   survive inside `isJa`:

   ```bash
   # Paths for the promoted version, excluding the isJa membership test.
   grep -Fn '2.1/' .vitepress/theme/VersionSwitcher.vue \
     | grep -v "startsWith('2.1/ja/')"                       # MUST be empty
   grep -Fc '2.0/' .vitepress/theme/VersionSwitcher.vue      # MUST be > 0
   grep -Fc "startsWith('2.1/ja/')" .vitepress/theme/VersionSwitcher.vue  # MUST be 1
   ```

   Match on `2.1/` **with the trailing slash**, not on `2.1`: after the promote
   `2.1` legitimately survives as the `currentVersion` string and as a menu
   label — only the *paths* move, and the `isJa` arm does not move at all.

   The bare `grep -Fn '2.1/' … # MUST be empty` that stood here until this was
   corrected did not merely fail to catch the regression: it **prescribed**
   it. A promote that followed it to the letter shipped a JA-broken 2.1
   archive with all three of step 6's and step 7's checks green.

7. **Verify before opening the PR.**

   ```bash
   yarn docs:build
   ```

   The build's dead-link check **no longer catches a missed link rewrite in
   either direction.** It used to catch one: a promoted page still pointing at
   `/2.1/…` failed the build because that page had been deleted. Keeping `2.1/`
   is what removed that safety net — both a promoted page pointing at `/2.1/…`
   and a frozen page pointing at `/docs/…` now name a target that exists and is
   simply the wrong version. Step 3's grep is the check for the first; assert
   the freeze's rewrite separately, against the built output:

   ```bash
   # No link on a 2.0/ page may leave the archive. MUST print nothing.
   grep -rhoE 'href="/(docs|guide|ja/docs|ja/guide)/[^"]+"' .vitepress/dist/2.0/ | sort -u
   ```

   The trailing `+` matters: the site nav is global and emits a bare
   `href="/docs/"` and `href="/guide/"` (and their `/ja/` pair) on **every**
   page including the archives, so a `*` there matches four nav links per page
   and never goes quiet. With `+` the pattern sees only links that name a page.

   Run the same command against `.vitepress/dist/1.0/` as the control — it
   prints nothing there, because that archive was created with the rewrite. On
   a freeze that skipped the rewrite it prints 166 occurrences across 40 pages.

   Then confirm in the built output that `/2.1/**`, `/2.0/**` and `/1.0/**`
   carry `noindex`, that the unversioned `/docs/**` does **not**, that the
   sitemap excludes every `X.Y/` directory, and that the root front page still
   renders the hero rather than a preview banner. `/2.1/**` is on that list for
   the first time and matters most: it now holds the same pages as the indexed
   root, so if the generic `/^\d+\.\d+\//` rule ever stopped matching it the
   site would be publishing the whole of its stable documentation twice to
   search engines.

   Confirm too that `2.1/`'s two landing pages no longer announce a preview
   (step 4) — the built `/2.1/` and `/2.1/ja/` pages must not carry the
   in-development warning, or the shipped release's own documentation tells its
   readers it may still change.

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
   isJa missing '2.0/ja/'                    grep 2.1/ : FAIL   grep 2.0/ : pass   build: 0   rendered: 2.1
   isJa missing '2.1/ja/'                    grep 2.1/ : pass   grep 2.0/ : pass   build: 0   rendered: 2.1
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
  are added.
- **Bump the release's own `X.Y/` directory along with the root.** It is not an
  archive yet — it holds the same pages as the root and it is what the product's
  baked `recotem.org/X.Y/docs/…` URLs resolve to for this release's whole
  support window, so a pin left behind there is read by exactly the operators
  running this version. It becomes an archive, and stops being bumped, when the
  next minor moves the root past it.
- **Leave every archived `X.Y/` directory untouched** — one whose line the root
  has already moved past. `1.0/` documents the legacy 1.x app, and a freshly
  frozen `2.0/` must keep documenting 2.0.
- Verify no stale pin remains in any live (non-archive) tree, then open a PR
  there too. If 4A ran, both jobs belong in the same PR.
