"""Every place we install from `uv.lock` must first check it against pyproject.

``uv sync --frozen`` installs from the lockfile *without* comparing it to
``pyproject.toml``.  That is not only a version-string problem: a dependency
declared in ``pyproject.toml`` and absent from ``uv.lock`` is silently skipped,
with no warning and exit 0.  Measured on this repository, with ``cowsay>=6``
added to ``[project].dependencies`` and ``uv.lock`` left alone::

    $ uv sync --frozen --dev
    Checked 78 packages in 1ms
    EXIT=0
    $ .venv/bin/python -c "import cowsay"
    ModuleNotFoundError: No module named 'cowsay'

    $ uv sync --locked --dev
    error: The lockfile at `uv.lock` needs to be updated, but `--locked` was provided.
    EXIT=1

    $ uv lock --check
    EXIT=1

And end-to-end through the image, same mutation::

    --frozen  docker build -> exit 0, `import cowsay` in the image raises
    --locked  docker build -> exit 1, naming the stale lockfile

``--frozen`` was used at all six sites that sync (Dockerfile, publish.yml,
docker.yml, test.yml x3) and ``--locked`` at none, so anyone who edited
dependencies and forgot ``uv lock`` got green CI and a published image built
against the *older* dependency set, with the whole suite passing against
dependencies that are not the declared ones.  ``pyproject.toml`` carries
security pins added exactly that way -- see the ``starlette>=0.49.1``
CVE-2025-62727 comment -- and one of those would not have reached CI or the
image.

Two documents also credited ``uv sync --frozen`` with catching a stale
lockfile at the tag.  ``.github/scripts/check-release-tag.sh`` did not: it
never mentions ``uv.lock``, and its success message enumerates what it matched
and names what it did not.  The script is correctly scoped; the prose was
wrong, which is why this guard checks the documents and not the script.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_DOCKERFILE = _ROOT / "Dockerfile"
_SKILL = _ROOT / ".claude" / "skills" / "release-recotem"
_PROCEDURE_DOCS = (
    _SKILL / "SKILL.md",
    _SKILL / "references" / "version-locations.md",
)

# A `uv sync` invocation and the rest of its command.  Shell line
# continuations are joined first, so the Dockerfile's multi-line `RUN ... uv
# sync \\ --extra ... \\ --locked` is seen as one command; comments are
# stripped first so the explanatory prose around these call sites can neither
# satisfy nor trip the assertions.
_UV_SYNC = re.compile(r"uv sync\b[^\n]*")


def _strip_comments(text: str) -> str:
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    return re.sub(r"\\\s*\n\s*", " ", body)


def _workflow_sync_commands() -> list[tuple[str, str, str]]:
    """(workflow, job, command) for every `uv sync` in every workflow."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job, spec in (workflow.get("jobs") or {}).items():
            for step in spec.get("steps") or []:
                run = step.get("run")
                if isinstance(run, str) and "uv sync" in run:
                    for match in _UV_SYNC.finditer(_strip_comments(run)):
                        found.append((path.name, job, match.group(0)))
    return found


def _dockerfile_sync_commands() -> list[str]:
    body = _strip_comments(_DOCKERFILE.read_text(encoding="utf-8"))
    return [m.group(0) for m in _UV_SYNC.finditer(body)]


def _normalise(command: str) -> str:
    return " ".join(command.replace("\\", " ").split())


def test_every_uv_sync_validates_the_lockfile() -> None:
    sites = [(f"{wf}:{job}", cmd) for wf, job, cmd in _workflow_sync_commands()]
    sites += [("Dockerfile", cmd) for cmd in _dockerfile_sync_commands()]
    assert sites, (
        "no `uv sync` found in any workflow or in the Dockerfile -- this guard "
        "has stopped matching and is watching nothing."
    )
    for where, command in sites:
        flat = _normalise(command)
        assert "--locked" in flat, (
            f"{where} installs without `--locked`: {flat!r}. `--frozen` (and a "
            "bare `uv sync` in CI) installs from uv.lock without comparing it "
            "to pyproject.toml, so a declared-but-unlocked dependency is "
            "silently skipped and everything downstream goes green against the "
            "older dependency set."
        )
        assert "--frozen" not in flat, (
            f"{where} passes `--frozen`, which defeats `--locked`: {flat!r}"
        )


def test_ci_runs_an_explicit_lockfile_check() -> None:
    """`uv sync --locked` refuses the same condition, but says it less clearly.

    Kept as its own assertion so the actionable message survives a future
    change to how dependencies are installed.
    """
    workflow = yaml.safe_load((_WORKFLOWS / "test.yml").read_text(encoding="utf-8"))
    runs = [
        step.get("run", "")
        for spec in workflow["jobs"].values()
        for step in spec.get("steps") or []
    ]
    assert any("uv lock --check" in run for run in runs), (
        "test.yml runs no `uv lock --check`. It is the one step whose failure "
        "message names the lockfile as the problem rather than reporting an "
        "install error."
    )


def test_release_procedure_does_not_credit_frozen_with_the_lockfile_check() -> None:
    """`uv sync --frozen` is named in the procedure only to claim it checks.

    Both documents mentioned it exactly once, in the sentence crediting it with
    catching a stale lockfile at the tag.  Nothing syncs with `--frozen` any
    more, so the string should not come back.
    """
    for doc in _PROCEDURE_DOCS:
        text = doc.read_text(encoding="utf-8")
        assert "uv sync --frozen" not in text, (
            f"{doc.relative_to(_ROOT)} credits `uv sync --frozen` with the "
            "uv.lock check. It exits 0 on a stale lockfile, and on one missing "
            "a declared dependency."
        )
