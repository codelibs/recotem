# GitHub Release notes

recotem has **no `CHANGELOG.md`**. The GitHub Release for the tag is the change
record, and it is written once, at release time, from the merged-PR log. There
is nothing to accumulate per PR and nothing to reconcile — a PR that changes
behaviour updates the documentation in `recotem-docs` and stops there. (This
repository has no `docs/` tree; the documentation lives at
<https://recotem.org>, in the separate `recotem-docs` repo.)

`pyproject.toml`'s `Changelog` URL points at
<https://github.com/codelibs/recotem/releases>.

## Where the content comes from

```bash
git log vPREV..main --oneline --no-merges
```

That log is the raw material. Conventional-commit prefixes map onto the
headings below (`feat:` → Added, `fix:` → Fixed, `build(deps)` → usually
dropped). Filter out dependency bumps that carry no user-visible change, but
keep CVE fixes — those are the Security section.

Keep entries user-facing, concrete, and **one line each**. Prefer "the
`/predict/{name}` endpoints no longer exist" over "refactored routing." Link the
PR number. Rationale, measurements and migration detail belong on a page in
`recotem-docs`, not in the notes — if an entry needs a paragraph, the paragraph
belongs on a docs page and the entry links to it.

## Upgrade guidance is documentation, not release notes

Anything an operator must *do* to upgrade goes on the **upgrading page**, under
a `## <prev> → <this>` heading, and the release notes link to it. That page
outlives the release; the notes do not get read again.

**That page is in the other repository.** There is no `docs/upgrading.md` here —
do not go looking for one. It is `docs/upgrading.md` in `recotem-docs`, and it
exists per version directory and **per language**, so the edit is always at
least two files:

| Releasing | Edit, in `recotem-docs` |
|---|---|
| `X.Y.0` (minor / major) | `X.Y/docs/upgrading.md` **and** `X.Y/ja/docs/upgrading.md` — the in-development preview, which Phase 4A then promotes to the root |
| `X.Y.Z` (patch, `Z > 0`) | the root pair `docs/upgrading.md` + `ja/docs/upgrading.md`, **and** the `X.Y/` pair, which serves the same line at the URL the shipped release points at |

The English and Japanese pages are added together and kept in sync — that is a
standing rule of that repo, not a preference. Write this during Phase 2, in the
same window as the version bump, so the page exists before the release notes
link to it.

## Template

(The outer fence below uses four backticks so the inner ```` ```bash ```` block
renders intact — the notes file itself is plain markdown with normal fences.)

````markdown
<one-paragraph summary. For a major bump or a breaking change, say so plainly
in the first sentence and link the upgrading page.>

## Install

```bash
pip install recotem            # https://pypi.org/project/recotem/
# or
docker pull ghcr.io/codelibs/recotem:X.Y.Z
```

## Upgrading

<one or two sentences, then:>
See [Upgrading](https://recotem.org/X.Y/docs/upgrading#<anchor>).

## Added
- ... (#PR)

## Changed
- ... (#PR)

## Fixed
- ... (#PR)

## Removed
- ... (#PR)

## Security
- ... name the CVEs that were patched (#PR)
````

Substitute `X.Y` in that URL with the release's **MAJOR.MINOR**, not the full
version — `2.1.1`'s notes still link `/2.1/docs/upgrading`. Use the versioned
form deliberately, not the root: a release note is read months later, by
someone still on that version, and the root will by then be serving a newer
line. The `X.Y/` directory it points at is never deleted, precisely so links
like this one keep resolving (`references/docs-site-sync.md`, Phase 4A).

Check the link before publishing. Nothing in CI validates a release-note URL,
the notes are the most-read text the release produces, and the anchor is the
half that rots — heading slugs change when a page is reorganised:

```bash
curl -sSI -o /dev/null -w '%{http_code}\n' https://recotem.org/X.Y/docs/upgrading
```

Only include the sections that apply. Create the release with:

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <notes.md> --latest
```

Use a scratchpad file for `<notes.md>`. `--latest` makes it the default release
shown on the repo home; drop it for a back-port to an older line.

## Worked example — 2.0.0 (the first release of the rewrite)

2.0.0 replaced the 1.x Django/DRF/Channels/Vue/Celery multi-service web app
with a single `pip install recotem` package + one Docker image. The notes:

- **Summary** stated up front it was a complete rewrite with no in-place
  upgrade path.
- **Added** covered the recipe-driven workflow, the `train`/`serve` CLI, the
  FastAPI `/v1/recipes/{name}:<verb>` API, signed artifacts + key rotation,
  pluggable data sources (csv/parquet/bigquery/sql), Optuna search, and the
  security hardening (SSRF guard, FQCN allow-list, log redaction).
- **Changed** noted the move to `/v1/...:recommend`, artifact-only train↔serve
  communication, and Python 3.12+.
- **Removed** listed the whole 1.x stack and the GA4 Data API source.
- **Security** named the PyJWT/cryptography/Starlette/urllib3 CVE bumps.
- **Upgrading** pointed at the 1.x → 2.x steps, which now live on the
  upgrading page in `recotem-docs`.

Released as: https://github.com/codelibs/recotem/releases/tag/v2.0.0
