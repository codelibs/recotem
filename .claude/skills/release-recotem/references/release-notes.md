# GitHub Release notes

recotem has **no `CHANGELOG.md`**. The GitHub Release for the tag is the change
record, and it is written once, at release time, from the merged-PR log. There
is nothing to accumulate per PR and nothing to reconcile — a PR that changes
behaviour updates `docs/` and stops there.

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
PR number. Rationale, measurements and migration detail belong in `docs/`, not
in the notes — if an entry needs a paragraph, the paragraph belongs on a docs
page and the entry links to it.

## Upgrade guidance is documentation, not release notes

Anything an operator must *do* to upgrade goes in
[`docs/upgrading.md`](../../../../docs/upgrading.md), under a
`## <prev> → <this>` heading, and the release notes link to it. That page
outlives the release; the notes do not get read again.

## Template

(The outer fence below uses four backticks so the inner ```` ```bash ```` block
renders intact — the notes file itself is plain markdown with normal fences.)

````markdown
<one-paragraph summary. For a major bump or a breaking change, say so plainly
in the first sentence and link docs/upgrading.md.>

## Install

```bash
pip install recotem            # https://pypi.org/project/recotem/
# or
docker pull ghcr.io/codelibs/recotem:X.Y.Z
```

## Upgrading

<one or two sentences, then:>
See [docs/upgrading.md](https://github.com/codelibs/recotem/blob/vX.Y.Z/docs/upgrading.md#<anchor>).

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
- **Upgrading** pointed at the 1.x → 2.x steps, which now live in
  `docs/upgrading.md`.

Released as: https://github.com/codelibs/recotem/releases/tag/v2.0.0
