"""Unit tests for the Kubernetes manifests under helm/ and examples/k8s/.

These cover defects that no schema validator catches on its own, and that the
pinned-tool gate in .github/scripts/validate-manifests.sh only catches once a
values permutation actually exercises them.

Tests:
- The objectStore init container merges the chart's /recipes mount into an
  operator-supplied volumeMounts list instead of emitting a duplicate
  `volumeMounts` key (Go's YAML decoder is last-key-wins, so the operator's
  mounts — credentials, CA bundles — were silently dropped).
- An operator-supplied init-container `name` is honoured, again without a
  duplicate key; `sync-recipes` remains the default.
- The chart's own `recipes` mount wins over an operator entry of the same name,
  so the shared emptyDir can never be unmounted or redirected.
- examples/k8s/cronjob.yaml fails loudly on an empty recipes directory, like
  both of its siblings (the chart's all-recipes branch and bootstrap-job.yaml).
- Every `/bin/sh -c` script in examples/k8s/ parses under a real POSIX shell.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "helm" / "recotem"
K8S_EXAMPLES = REPO_ROOT / "examples" / "k8s"

_HELM = shutil.which("helm")
# /bin/sh in the python:3.12-slim runtime image is dash.  bash in POSIX mode
# still accepts arrays and here-strings, so only dash proves the script runs.
_DASH = shutil.which("dash")

requires_helm = pytest.mark.skipif(_HELM is None, reason="helm not on PATH")
requires_dash = pytest.mark.skipif(_DASH is None, reason="dash not on PATH")


# ---------------------------------------------------------------------------
# Duplicate-key-detecting YAML loader
#
# yaml.safe_load silently accepts a repeated mapping key and keeps the last
# occurrence — exactly what Go's YAML decoder does, and exactly why the
# duplicate-`volumeMounts` defect was invisible to `helm template`, `helm lint`
# and `kubectl apply --dry-run=server --validate=strict` alike.  A normal parse
# of the broken render therefore looks perfectly healthy.
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


def _load_all_strict(text: str) -> list[dict[str, Any]]:
    """Parse a multi-document manifest, raising on any duplicate mapping key."""
    return [d for d in yaml.load_all(text, Loader=_StrictLoader) if isinstance(d, dict)]


def _helm_template(*set_args: str) -> str:
    """Render the chart, failing the test with helm's own stderr on error."""
    argv = [str(_HELM), "template", "rv", str(CHART), "--namespace", "recotem"]
    for arg in set_args:
        argv += ["--set", arg]
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"helm template failed:\n{proc.stderr}"
    return proc.stdout


def _init_containers(docs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Map kind -> initContainers for the Deployment and the train CronJob."""
    out: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        if doc.get("kind") == "Deployment":
            spec = doc["spec"]["template"]["spec"]
        elif doc.get("kind") == "CronJob":
            spec = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        else:
            continue
        out[doc["kind"]] = spec.get("initContainers", [])
    return out


_OBJECT_STORE = (
    "recipes.source=objectStore",
    "recipes.objectStore.initContainer.image=amazon/aws-cli:latest",
    "train.enabled=true",
)


# ---------------------------------------------------------------------------
# Helm chart: objectStore init container
# ---------------------------------------------------------------------------


@requires_helm
def test_operator_volume_mounts_are_merged_not_duplicated() -> None:
    """An operator volumeMounts list must survive alongside the /recipes mount.

    Before the fix both templates emitted a second `volumeMounts:` key under
    the same init container, and last-key-wins dropped the operator's entry.
    """
    rendered = _helm_template(
        *_OBJECT_STORE,
        # `tmp` is one of the volumes the chart itself declares — the chart has
        # no extraVolumes hook, so a mount must name one of those.
        "recipes.objectStore.initContainer.volumeMounts[0].name=tmp",
        "recipes.objectStore.initContainer.volumeMounts[0].mountPath=/tmp",
    )
    docs = _load_all_strict(rendered)  # raises on the duplicate key

    init = _init_containers(docs)
    assert set(init) == {"Deployment", "CronJob"}, init
    for kind, containers in init.items():
        assert len(containers) == 1, f"{kind}: {containers}"
        mounts = containers[0]["volumeMounts"]
        by_name = {m["name"]: m for m in mounts}
        assert by_name["tmp"]["mountPath"] == "/tmp", f"{kind}: {mounts}"
        assert by_name["recipes"]["mountPath"] == "/recipes", f"{kind}: {mounts}"


@requires_helm
def test_operator_supplied_name_is_honoured() -> None:
    """A `name` in the operator spec must win, without duplicating the key."""
    rendered = _helm_template(
        *_OBJECT_STORE,
        "recipes.objectStore.initContainer.name=gcs-sync",
    )
    for kind, containers in _init_containers(_load_all_strict(rendered)).items():
        assert [c["name"] for c in containers] == ["gcs-sync"], kind


@requires_helm
def test_init_container_name_defaults_to_sync_recipes() -> None:
    """Omitting `name` keeps the historical `sync-recipes` container name."""
    rendered = _helm_template(*_OBJECT_STORE)
    for kind, containers in _init_containers(_load_all_strict(rendered)).items():
        assert [c["name"] for c in containers] == ["sync-recipes"], kind


@requires_helm
def test_chart_recipes_mount_wins_over_operator_entry() -> None:
    """The chart's /recipes mount cannot be redirected by a same-named entry."""
    rendered = _helm_template(
        *_OBJECT_STORE,
        "recipes.objectStore.initContainer.volumeMounts[0].name=recipes",
        "recipes.objectStore.initContainer.volumeMounts[0].mountPath=/somewhere-else",
    )
    for kind, containers in _init_containers(_load_all_strict(rendered)).items():
        mounts = containers[0]["volumeMounts"]
        recipes = [m for m in mounts if m["name"] == "recipes"]
        assert recipes == [{"name": "recipes", "mountPath": "/recipes"}], (
            f"{kind}: {mounts}"
        )


# ---------------------------------------------------------------------------
# examples/k8s/cronjob.yaml: empty recipes directory must fail the job
# ---------------------------------------------------------------------------


def _sh_scripts(path: Path) -> dict[str, str]:
    """Extract every `/bin/sh -c <script>` in a manifest, keyed by container."""
    scripts: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            command = node.get("command")
            if (
                isinstance(command, list)
                and len(command) >= 3
                and isinstance(command[0], str)
                and command[0].rsplit("/", 1)[-1] == "sh"
                and command[1] == "-c"
                and isinstance(command[2], str)
            ):
                scripts[str(node.get("name", len(scripts)))] = command[2]
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(_load_all_strict(path.read_text(encoding="utf-8")))
    return scripts


def test_example_cronjob_fails_on_empty_recipes_dir() -> None:
    """The example CronJob must exit 1, not 0, when /recipes holds no recipes.

    Both siblings — the chart's all-recipes branch and bootstrap-job.yaml —
    already do this, so a misconfigured ConfigMap or PVC surfaces as a job
    failure rather than a silent success.
    """
    scripts = _sh_scripts(K8S_EXAMPLES / "cronjob.yaml")
    assert "train" in scripts, f"no `train` /bin/sh -c command found: {list(scripts)}"
    script = scripts["train"]
    assert script.strip(), "extracted train script is empty"

    assert "count=$((count + 1))" in script, script
    assert 'if [ "$count" -eq 0 ]; then' in script, script
    assert 'echo "no recipe files found under /recipes" >&2' in script, script
    assert "exit 1" in script, script


@requires_dash
def test_example_k8s_sh_commands_parse_under_dash(tmp_path: Path) -> None:
    """Every rendered `/bin/sh -c` script must parse under a real POSIX shell."""
    checked = 0
    for manifest in sorted(K8S_EXAMPLES.glob("*.yaml")):
        for name, script in _sh_scripts(manifest).items():
            assert script.strip(), f"{manifest.name}/{name}: empty script"
            path = tmp_path / f"{manifest.stem}-{name}.sh"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run(
                [str(_DASH), "-n", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert proc.returncode == 0, (
                f"{manifest.name}/{name} is not valid POSIX sh:\n"
                f"{proc.stderr}\n--- script ---\n{script}"
            )
            checked += 1
    assert checked, "no /bin/sh -c commands found under examples/k8s/"
