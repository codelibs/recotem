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
- The NetworkPolicy selects the train CronJob's pods, and `extraEgress` is the
  hook that lets those pods reach a SQL or plain-HTTP data source (the
  built-in rules only open 53 / 443 / 8080).
- The Deployment omits `spec.replicas` when an HPA owns it, so `helm upgrade`
  cannot undo the autoscaler's decisions.
- The PodDisruptionBudget selects serve pods only.  While it also matched the
  train pods, a running training job counted towards `currentHealthy` and
  inflated the budget, so the protection lapsed exactly when a job was
  running.  The component label reaches the pod template but must stay out of
  the Deployment's immutable `spec.selector`.
- The Service keeps train pods out of its Endpoints only because `targetPort`
  is the *name* `http`, which the train container does not declare — pinned,
  because that safety is a side effect rather than a statement of intent.
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
# Helm chart: NetworkPolicy egress
# ---------------------------------------------------------------------------


def _by_kind(docs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    """Return the single document of `kind`, failing the test if not unique."""
    matches = [d for d in docs if d.get("kind") == kind]
    assert len(matches) == 1, f"expected exactly one {kind}, got {len(matches)}"
    return matches[0]


def _egress_ports(policy: dict[str, Any]) -> set[tuple[int, str]]:
    return {
        (port["port"], port.get("protocol", "TCP"))
        for rule in policy["spec"]["egress"]
        for port in rule.get("ports", [])
    }


@requires_helm
def test_network_policy_also_selects_the_train_pods() -> None:
    """The policy is not serve-only — it captures the train CronJob's pods.

    Its podSelector matches on name+instance, which the CronJob's pod template
    carries too (it only adds `app.kubernetes.io/component: train`).  That is
    why `extraEgress` exists: the built-in egress rules are serve-shaped, so a
    training run whose recipe points at a SQL database or a plain-http:// URL
    is dropped by this policy with nothing but a connection timeout to show
    for it.  Pinned so a future selector change is a deliberate one.
    """
    docs = _load_all_strict(_helm_template("train.enabled=true"))
    selector = _by_kind(docs, "NetworkPolicy")["spec"]["podSelector"]["matchLabels"]
    train_labels = _by_kind(docs, "CronJob")["spec"]["jobTemplate"]["spec"]["template"][
        "metadata"
    ]["labels"]

    assert selector, "podSelector is empty — the policy would select every pod"
    assert selector.items() <= train_labels.items(), (
        f"policy selector {selector} no longer matches train pod labels {train_labels}"
    )


@requires_helm
def test_network_policy_extra_egress_is_appended() -> None:
    """extraEgress rules must reach the rendered policy, verbatim.

    Without this hook the only way to let a train pod reach PostgreSQL (5432),
    MySQL (3306), SQL Server (1433) or a plain-http:// source (80) is to
    disable the policy wholesale — the built-in rules open 53, 443 and 8080.
    """
    rendered = _helm_template(
        "train.enabled=true",
        "networkPolicy.extraEgress[0].to[0].ipBlock.cidr=10.0.0.0/8",
        "networkPolicy.extraEgress[0].ports[0].port=5432",
        "networkPolicy.extraEgress[0].ports[0].protocol=TCP",
        "networkPolicy.extraEgress[1].ports[0].port=80",
        "networkPolicy.extraEgress[1].ports[0].protocol=TCP",
    )
    policy = _by_kind(_load_all_strict(rendered), "NetworkPolicy")

    ports = _egress_ports(policy)
    assert (5432, "TCP") in ports, ports
    assert (80, "TCP") in ports, ports
    # The built-in rules must survive alongside the operator's.
    for builtin in ((53, "UDP"), (53, "TCP"), (443, "TCP"), (8080, "TCP")):
        assert builtin in ports, f"{builtin} lost from egress: {ports}"

    # `to:` is emitted as given, so a rule can be scoped to one subnet rather
    # than opening the port to every destination.
    scoped = [r for r in policy["spec"]["egress"] if r.get("to")]
    assert scoped == [
        {
            "to": [{"ipBlock": {"cidr": "10.0.0.0/8"}}],
            "ports": [{"port": 5432, "protocol": "TCP"}],
        }
    ], scoped


@requires_helm
def test_network_policy_egress_defaults_to_builtin_rules_only() -> None:
    """An unset extraEgress must not add, drop or reorder anything."""
    policy = _by_kind(_load_all_strict(_helm_template()), "NetworkPolicy")
    assert _egress_ports(policy) == {
        (53, "UDP"),
        (53, "TCP"),
        (443, "TCP"),
        (8080, "TCP"),
    }
    assert not [r for r in policy["spec"]["egress"] if r.get("to")]


# ---------------------------------------------------------------------------
# Helm chart: Deployment replicas vs. HorizontalPodAutoscaler
# ---------------------------------------------------------------------------


@requires_helm
def test_deployment_omits_replicas_when_hpa_owns_it() -> None:
    """`spec.replicas` must be absent whenever the HPA is enabled.

    Rendering it makes every `helm upgrade` re-apply replicaCount, snapping a
    fleet the HPA had scaled out back down until the HPA reacts.
    """
    docs = _load_all_strict(_helm_template("replicaCount=3", "hpa.enabled=true"))
    deployment = _by_kind(docs, "Deployment")

    assert "replicas" not in deployment["spec"], deployment["spec"]
    # The floor now comes from the autoscaler, not from replicaCount.
    assert _by_kind(docs, "HorizontalPodAutoscaler")["spec"]["minReplicas"] == 2


@requires_helm
def test_deployment_sets_replicas_without_an_hpa() -> None:
    """With no HPA the Deployment still owns its replica count."""
    docs = _load_all_strict(_helm_template("replicaCount=3"))
    assert _by_kind(docs, "Deployment")["spec"]["replicas"] == 3
    assert not [d for d in docs if d.get("kind") == "HorizontalPodAutoscaler"]


# ---------------------------------------------------------------------------
# Helm chart: PodDisruptionBudget scope
# ---------------------------------------------------------------------------


def _pod_labels(docs: list[dict[str, Any]], kind: str) -> dict[str, str]:
    """Pod-template labels for the serve Deployment or the train CronJob."""
    doc = _by_kind(docs, kind)
    spec = doc["spec"] if kind == "Deployment" else doc["spec"]["jobTemplate"]["spec"]
    return spec["template"]["metadata"]["labels"]


@requires_helm
def test_pdb_selects_serve_pods_but_not_train_pods() -> None:
    """The disruption budget must be computed over serve pods alone.

    A PDB's allowed-disruption count is `currentHealthy - minAvailable` over
    the pods it selects.  While the selector still matched the train CronJob's
    pods, a concurrent training run counted as healthy and inflated the
    budget: with one serve replica and minAvailable=1, allowed disruptions
    went from 0 to 1 and a node drain could evict the only serve pod.  The
    protection therefore lapsed exactly while a training job was running.
    """
    docs = _load_all_strict(
        _helm_template("train.enabled=true", "pdb.enabled=true", "replicaCount=1")
    )
    selector = _by_kind(docs, "PodDisruptionBudget")["spec"]["selector"]["matchLabels"]

    serve_labels = _pod_labels(docs, "Deployment")
    train_labels = _pod_labels(docs, "CronJob")

    assert selector.items() <= serve_labels.items(), (
        f"PDB selector {selector} no longer matches the serve pods {serve_labels}"
    )
    assert not selector.items() <= train_labels.items(), (
        f"PDB selector {selector} still matches the train pods {train_labels}"
    )


@requires_helm
def test_deployment_selector_stays_free_of_the_component_label() -> None:
    """The component label must reach the pod template but not spec.selector.

    A Deployment's `spec.selector` is immutable, so adding the label there
    would make `helm upgrade` fail on every already-installed release.  The
    selector is a subset match, so the extra pod label costs nothing.
    """
    deployment = _by_kind(_load_all_strict(_helm_template()), "Deployment")

    selector = deployment["spec"]["selector"]["matchLabels"]
    labels = deployment["spec"]["template"]["metadata"]["labels"]

    assert "app.kubernetes.io/component" not in selector, selector
    assert labels["app.kubernetes.io/component"] == "serve", labels
    assert selector.items() <= labels.items(), (selector, labels)


@requires_helm
def test_service_named_target_port_is_what_excludes_train_pods() -> None:
    """Pin the reason train pods stay out of the Service's Endpoints.

    The Service selector carries no component label, so it matches the train
    pods too.  They are skipped only because `targetPort` is the *name*
    `http`, which the endpoints controller resolves against each pod's own
    container ports — and the train container declares none.  That safety is
    a side effect, so pin it: give the train container a port named `http`
    and user traffic would start load-balancing onto training pods.

    The selector is deliberately left unscoped; narrowing it would empty the
    Service's Endpoints for the duration of the rollout that adds the
    matching pod label.  See the comment in templates/service.yaml.
    """
    docs = _load_all_strict(_helm_template("train.enabled=true"))

    service = _by_kind(docs, "Service")
    ports = service["spec"]["ports"]
    assert [p["targetPort"] for p in ports] == ["http"], ports
    assert service["spec"]["selector"].items() <= _pod_labels(docs, "CronJob").items()

    serve = _by_kind(docs, "Deployment")["spec"]["template"]["spec"]["containers"]
    train = _by_kind(docs, "CronJob")["spec"]["jobTemplate"]["spec"]["template"][
        "spec"
    ]["containers"]
    assert [p["name"] for c in serve for p in c.get("ports", [])] == ["http"]
    assert [p["name"] for c in train for p in c.get("ports", [])] == [], (
        "the train container now declares a named port — if it is `http` the "
        "Service will route user traffic to training pods"
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
