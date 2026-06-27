# CHANGELOG and GitHub Release notes

recotem keeps a `CHANGELOG.md` in [Keep a Changelog](https://keepachangelog.com/)
style with [SemVer](https://semver.org/). The GitHub Release notes are derived
from the CHANGELOG section for the version — same content, plus an install
block and links resolved at the tag.

## CHANGELOG section template

Add a new section at the top, above the previous version. Use today's date
(the release date). Only include the subsections that apply.

```markdown
## [X.Y.Z] - YYYY-MM-DD

<one-paragraph summary; for a major bump, state plainly that it is a rewrite /
breaking and point to "Migrating from" below>

### Added
- ...

### Changed
- ...

### Removed
- ...

### Security
- ... (name the CVEs that were patched)

### Migrating from <prev-major>.x   <!-- major bumps only -->
1. ...
2. ...

[X.Y.Z]: https://github.com/codelibs/recotem/releases/tag/vX.Y.Z
```

Keep entries user-facing and concrete. Prefer "the `/predict/{name}` endpoints
no longer exist" over "refactored routing." For a major rewrite, a migration
guide is the most valuable part — it is the thing users actually need.

## GitHub Release notes template

The release notes are the CHANGELOG section with an install block on top and
links pointing at the tagged tree (so they keep working as `main` moves on).
(The outer fence below uses four backticks so the inner ```` ```bash ```` block
renders intact — the notes file itself is plain markdown with normal fences.)

````markdown
<summary paragraph, same as the CHANGELOG>

## Install

```bash
pip install recotem            # https://pypi.org/project/recotem/
# or
docker pull ghcr.io/codelibs/recotem:X.Y.Z
```

## Added
...
## Changed
...
## Removed
...
## Security
...
## Migrating from <prev-major>.x   <!-- major bumps only -->
...

**Full changelog:** [CHANGELOG.md](https://github.com/codelibs/recotem/blob/vX.Y.Z/CHANGELOG.md)
````

Create it with:

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
- **Migrating from 1.x** gave four steps: re-train (don't migrate model state),
  drop the DB/broker, update API clients from `/predict/{name}`, generate keys.

Released as: https://github.com/codelibs/recotem/releases/tag/v2.0.0
