"""Unit tests for the release gates encoded in .github/workflows/.

GitHub provides no dependency edge between workflows, so a gate that lives in
one workflow protects nothing in another.  These tests pin the edges that keep
an unverified or over-privileged build from reaching a registry, because
nothing else in CI parses the workflow files at all.

Tests:
- The job that publishes the image transitively depends on a job that runs
  pytest, so a red test suite blocks the GHCR push.  test.yml runs concurrently
  with docker.yml and cannot block it.
- No workflow grants `packages` at workflow level, and only the job that logs
  in to GHCR grants it at all.  A workflow-level grant is inherited by every
  job that declares no `permissions:` block of its own, including jobs added
  later — so asserting on the default, not just on today's jobs, is what keeps
  the scope from silently spreading.
- The PyPI publish action is pinned to a commit.  It runs in the only job with
  `id-token: write`, and a PyPI filename can never be reused, so a mutable ref
  there is a permanent-consequence supply-chain hole.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# A job that grants no `permissions:` of its own inherits the workflow-level
# block wholesale; GitHub does not intersect it with what the job uses.
_NO_BLOCK = object()


# ---------------------------------------------------------------------------
# Duplicate-key-detecting YAML loader
#
# yaml.safe_load silently keeps the last of a repeated mapping key, which is
# precisely the mutation these tests must not miss: a second `permissions:` or
# `needs:` on a job would parse clean and quietly replace the gate being
# asserted here.  Mirrors the loader in test_k8s_manifests.py.
# ---------------------------------------------------------------------------
class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys instead of taking the last one."""


def _no_duplicate_keys(
    loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys
)


def _load(name: str) -> dict[str, Any]:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=_StrictLoader)


def _needs(job: dict[str, Any]) -> list[str]:
    """`needs:` accepts a bare string or a list; normalise to a list."""
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _transitive_needs(jobs: dict[str, Any], start: str) -> set[str]:
    seen: set[str] = set()
    pending = _needs(jobs[start])
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending.extend(_needs(jobs[name]))
    return seen


def _runs_pytest(job: dict[str, Any]) -> bool:
    return any("pytest" in str(step.get("run", "")) for step in job.get("steps", []))


def _effective_permissions(
    workflow: dict[str, Any], job: dict[str, Any]
) -> dict[str, str] | object:
    """What the job actually holds: its own block, else the workflow default."""
    return job.get("permissions", workflow.get("permissions", _NO_BLOCK))


def test_docker_push_waits_on_the_test_suite() -> None:
    """A red pytest must stop the image reaching GHCR.

    docker.yml, test.yml and publish.yml all start at once on a version tag.
    publish.yml protects PyPI with its own `test` job; without the equivalent
    edge here, `build` pushed to GHCR — moving the mutable `latest` tag on
    every push to main — while pytest was still running or had already failed.
    """
    jobs = _load("docker.yml")["jobs"]
    upstream = _transitive_needs(jobs, "build")
    testing = {name for name in upstream if _runs_pytest(jobs[name])}
    assert testing, (
        f"docker.yml's `build` job depends on {sorted(upstream)}, none of "
        "which runs pytest — a failing test suite would not stop the image "
        "being pushed to GHCR"
    )


def test_no_workflow_grants_packages_write_by_default() -> None:
    """A workflow-level grant reaches every job that declares no block.

    docker.yml used to grant `packages: write` here, so `smoke` and `trivy` —
    which only build and execute the image locally — held registry write, and
    so would any job added later.
    """
    for path in sorted(WORKFLOWS.glob("*.yml")):
        permissions = _load(path.name).get("permissions") or {}
        assert "packages" not in permissions, (
            f"{path.name} grants `packages` at workflow level; every job "
            "without its own `permissions:` block inherits registry write. "
            "Grant it on the job that pushes instead."
        )


def test_only_the_publishing_job_can_write_to_the_registry() -> None:
    """`build` is the only job that logs in to the registry."""
    workflow = _load("docker.yml")
    jobs = workflow["jobs"]
    privileged = {
        name
        for name, job in jobs.items()
        if "packages" in (_effective_permissions(workflow, job) or {})
    }
    assert privileged == {"build"}, (
        f"jobs holding `packages` scope: {sorted(privileged)}; only `build` "
        "logs in to GHCR, so any other entry is scope the job never uses"
    )


@pytest.mark.parametrize(
    "workflow_file", sorted(p.name for p in WORKFLOWS.glob("*.yml"))
)
def test_pypi_publish_action_is_pinned_to_a_commit(workflow_file: str) -> None:
    """`@release/v1` is a branch: its tip decides what runs under OIDC."""
    for line in (WORKFLOWS / workflow_file).read_text().splitlines():
        if "pypa/gh-action-pypi-publish@" not in line:
            continue
        ref = line.split("pypa/gh-action-pypi-publish@", 1)[1].split()[0]
        assert re.fullmatch(r"[0-9a-f]{40}", ref), (
            f"{workflow_file} uses pypa/gh-action-pypi-publish@{ref}; pin it to "
            "a full commit SHA (with the version in a trailing comment) — this "
            "action runs in the job holding `id-token: write` and a PyPI "
            "filename can never be reused"
        )
