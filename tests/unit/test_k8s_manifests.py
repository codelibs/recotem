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
- docs/deployment/k8s.md's manifest excerpts do not contradict the files they
  name (subset check, so a doc may still trim what it is not teaching).
"""

from __future__ import annotations

import re
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


# ---------------------------------------------------------------------------
# docs/deployment/k8s.md excerpt drift
# ---------------------------------------------------------------------------
DEPLOY_DOC = REPO_ROOT / "docs" / "deployment" / "k8s.md"

_DOC_EXCERPT_RE = re.compile(
    r"^```yaml\n(# examples/k8s/(?P<name>[\w.-]+)\n.*?)^```$",
    re.MULTILINE | re.DOTALL,
)


def _doc_excerpts() -> list[tuple[str, str]]:
    """Return (filename, yaml text) for every excerpt tagged with its source.

    Only fenced blocks whose first line names a file under examples/k8s/ are
    returned.  Blocks quoting chart values (``networkPolicy:``, ``image:``)
    carry no such tag and are deliberately out of scope here.
    """
    text = DEPLOY_DOC.read_text(encoding="utf-8")
    return [(m.group("name"), m.group(1)) for m in _DOC_EXCERPT_RE.finditer(text)]


def _assert_subset(excerpt: Any, actual: Any, where: str) -> None:
    """Assert *excerpt* is a non-contradicting subset of *actual*.

    Subset, not equality: a doc may legitimately trim a manifest down to the
    fields it is teaching.  What it may never do is show a value the file does
    not have — that is the drift this catches (a stale ``schedule:``, a probe
    pointing at a port number the manifest renamed, a single-recipe ``command``
    the file replaced with a loop).  Lists are matched order-independently
    because an excerpt may reorder or drop ``env`` entries without being wrong.
    """
    if isinstance(excerpt, dict):
        assert isinstance(actual, dict), f"{where}: expected a mapping in the file"
        for key, value in excerpt.items():
            assert key in actual, f"{where}: doc has key {key!r}, file does not"
            _assert_subset(value, actual[key], f"{where}.{key}")
    elif isinstance(excerpt, list):
        assert isinstance(actual, list), f"{where}: expected a list in the file"
        for i, item in enumerate(excerpt):
            matched = False
            for candidate in actual:
                try:
                    _assert_subset(item, candidate, where)
                except AssertionError:
                    continue
                matched = True
                break
            assert matched, f"{where}[{i}]: {item!r} matches no entry in the file"
    else:
        assert excerpt == actual, f"{where}: doc says {excerpt!r}, file says {actual!r}"


def test_deployment_doc_excerpts_match_their_manifests() -> None:
    """Every ``# examples/k8s/…``-tagged excerpt must agree with that file.

    The excerpts had drifted: the CronJob showed ``schedule: "0 3 * * *"`` and a
    single-recipe ``recotem train /recipes/my_recipe.yaml`` command, the
    Deployment showed ``RECOTEM_WATCH_INTERVAL: "30"`` and probes on a numeric
    port, and the Service showed ``targetPort: 8080`` — none of which the named
    files contain.  A reader copying the doc got a manifest that differs from
    the one ``kubectl apply -f examples/k8s/`` installs.
    """
    excerpts = _doc_excerpts()
    assert {name for name, _ in excerpts} == {
        "cronjob.yaml",
        "serve-deployment.yaml",
        "serve-service.yaml",
    }, f"unexpected set of tagged excerpts: {[n for n, _ in excerpts]}"

    for name, text in excerpts:
        docs = _load_all_strict(text)
        assert len(docs) == 1, f"{name}: excerpt must hold exactly one document"
        actual = _load_all_strict((K8S_EXAMPLES / name).read_text(encoding="utf-8"))
        assert len(actual) == 1, f"{name}: manifest must hold exactly one document"
        _assert_subset(docs[0], actual[0], f"k8s.md::{name}")


# ---------------------------------------------------------------------------
# Probes, Pod Security Standards, and the RWO / spread interaction
# ---------------------------------------------------------------------------


def _pod_specs_in(docs: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Every pod spec in *docs*, labelled by the owning object's kind/name."""
    out: list[tuple[str, dict[str, Any]]] = []
    for doc in docs:
        kind = doc.get("kind")
        name = doc.get("metadata", {}).get("name", "?")
        if kind in {"Deployment", "Job"}:
            out.append((f"{kind}/{name}", doc["spec"]["template"]["spec"]))
        elif kind == "CronJob":
            out.append(
                (
                    f"{kind}/{name}",
                    doc["spec"]["jobTemplate"]["spec"]["template"]["spec"],
                )
            )
    return out


@requires_helm
@pytest.mark.parametrize(
    "set_args,label",
    [
        (("env.RECOTEM_ALLOWED_HOSTS=api.example.com",), "operator override"),
        (
            (
                "env.RECOTEM_ALLOWED_HOSTS=api.example.com\\,api-internal.svc",
                "ingress.enabled=true",
                "ingress.hosts[0].host=api.example.com",
            ),
            "override alongside ingress",
        ),
        (
            (
                "env.RECOTEM_ALLOWED_HOSTS=recotem.recotem.svc.cluster.local",
                "ingress.enabled=true",
                "ingress.hosts[0].host=api.example.com",
            ),
            "override that does not restate the ingress host",
        ),
        (
            ("ingress.enabled=true", "ingress.hosts[0].host=api.example.com"),
            "ingress-derived",
        ),
    ],
)
def test_allowed_hosts_always_admits_the_probe_host(
    set_args: tuple[str, ...], label: str
) -> None:
    """Whenever the chart renders the var at all, `localhost` is in it.

    The three probes hard-code `Host: localhost`. A list without it makes
    `TrustedHostMiddleware` return 400 for every readiness and liveness
    request, and the Deployment never becomes ready — while the application
    log shows only ordinary rejected requests.

    The ingress-derived branch always prepended `localhost`; the operator
    override branch did not, and `docs/deployment/k8s.md` tells operators to
    use exactly that branch.
    """
    docs = _load_all_strict(_helm_template(*set_args))
    values = [
        env["value"]
        for _, spec in _pod_specs_in(docs)
        for container in spec.get("containers", [])
        for env in container.get("env", [])
        if env.get("name") == "RECOTEM_ALLOWED_HOSTS"
    ]
    assert values, f"{label}: expected the chart to render RECOTEM_ALLOWED_HOSTS"
    for value in values:
        hosts = [h.strip() for h in value.split(",")]
        assert "localhost" in hosts, (
            f"{label}: rendered {value!r}, which excludes the probe host; "
            "every probe would return 400"
        )
        assert hosts.count("localhost") == 1, (
            f"{label}: duplicated localhost in {value!r}"
        )


@requires_helm
def test_chart_pod_specs_satisfy_pod_security_standards_restricted() -> None:
    """`seccompProfile` is the one restricted-profile field that was missing.

    A namespace labelled `pod-security.kubernetes.io/enforce=restricted`
    rejects a pod that does not set it, whatever else the pod gets right.
    """
    docs = _load_all_strict(_helm_template("train.enabled=true"))
    specs = _pod_specs_in(docs)
    assert specs, "expected the chart to render at least one pod spec"
    for label, spec in specs:
        profile = spec.get("securityContext", {}).get("seccompProfile", {})
        assert profile.get("type") in {"RuntimeDefault", "Localhost"}, (
            f"{label}: pod securityContext has no seccompProfile; the "
            "restricted Pod Security Standard refuses it"
        )


@pytest.mark.parametrize(
    "manifest",
    ["serve-deployment.yaml", "cronjob.yaml", "bootstrap-job.yaml"],
)
def test_example_pod_specs_satisfy_pod_security_standards_restricted(
    manifest: str,
) -> None:
    docs = _load_all_strict((K8S_EXAMPLES / manifest).read_text())
    specs = _pod_specs_in(docs)
    assert specs, f"{manifest}: expected a pod spec"
    for label, spec in specs:
        profile = spec.get("securityContext", {}).get("seccompProfile", {})
        assert profile.get("type") in {"RuntimeDefault", "Localhost"}, (
            f"{manifest} {label}: no seccompProfile; the restricted Pod "
            "Security Standard refuses it"
        )


def test_example_spread_constraint_does_not_deadlock_a_read_write_once_volume() -> None:
    """A hard hostname spread and an RWO PVC cannot both be satisfied.

    `examples/k8s/README.md` offers `ReadWriteOnce` for a single-node cluster.
    RWO binds every mounting pod to one node; `whenUnsatisfiable:
    DoNotSchedule` on `kubernetes.io/hostname` demands the opposite. Shipped
    together they leave `replicas: 2` permanently at 1/2 ready.
    """
    docs = _load_all_strict((K8S_EXAMPLES / "serve-deployment.yaml").read_text())
    deployments = [d for d in docs if d.get("kind") == "Deployment"]
    assert len(deployments) == 1

    spec = deployments[0]["spec"]["template"]["spec"]
    mounts_a_claim = any(
        "persistentVolumeClaim" in volume for volume in spec.get("volumes", [])
    )
    constraints = spec.get("topologySpreadConstraints", [])

    if mounts_a_claim and deployments[0]["spec"].get("replicas", 1) > 1:
        for constraint in constraints:
            if constraint.get("topologyKey") == "kubernetes.io/hostname":
                assert constraint.get("whenUnsatisfiable") == "ScheduleAnyway", (
                    "a hard hostname spread on a Deployment that mounts a PVC "
                    "strands the second replica whenever the volume is RWO"
                )


# ---------------------------------------------------------------------------
# Probe paths
#
# #219 split /v1/health into /v1/health/live and /v1/health/ready but left the
# startupProbe on the strict, count-based /v1/health.  A failing startup probe
# RESTARTS the container, so one valid-but-untrained recipe put every newly
# created pod into a restart loop while the running replicas served normally:
# `kubectl rollout restart` (the documented way to pick up a new recipe) and
# every HPA scale-out stalled indefinitely.  Measured on a 3-node cluster:
# `Killing  Container serve failed startup probe, will be restarted` with
# `Startup probe failed: HTTP probe failed with statuscode: 503`, while the
# same pod answered /v1/health/ready with 200.
#
# /v1/health/ready still 503s on a cold store, so pointing the startup probe
# there keeps the first-install guarantee that these tests and
# docs/deployment/k8s.md both describe.
# ---------------------------------------------------------------------------

# The strict endpoint answers "is EVERY recipe present?".  No probe may read
# it: readiness would drop the whole fleet, liveness and startup would restart
# pods that a restart cannot help.
_STRICT_HEALTH_PATH = "/v1/health"
_EXPECTED_PROBE_PATHS = {
    "startupProbe": "/v1/health/ready",
    "readinessProbe": "/v1/health/ready",
    "livenessProbe": "/v1/health/live",
}


def _serve_containers(specs: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [
        container
        for _, spec in specs
        for container in spec.get("containers", [])
        if any(probe in container for probe in _EXPECTED_PROBE_PATHS)
    ]


@requires_helm
def test_chart_probes_never_read_the_strict_health_endpoint() -> None:
    docs = _load_all_strict(_helm_template("train.enabled=true"))
    containers = _serve_containers(_pod_specs_in(docs))
    assert containers, "expected the chart to render a probed container"
    for container in containers:
        for probe, expected in _EXPECTED_PROBE_PATHS.items():
            path = container.get(probe, {}).get("httpGet", {}).get("path")
            if path is None:
                continue
            assert path != _STRICT_HEALTH_PATH, (
                f"chart {container['name']}.{probe} reads {_STRICT_HEALTH_PATH}, "
                "which 503s whenever any single recipe is untrained; a probe "
                "there restarts or de-registers pods that are serving fine"
            )
            assert path == expected, (
                f"chart {container['name']}.{probe} is {path!r}, expected {expected!r}"
            )


def test_example_probes_never_read_the_strict_health_endpoint() -> None:
    docs = _load_all_strict((K8S_EXAMPLES / "serve-deployment.yaml").read_text())
    containers = _serve_containers(_pod_specs_in(docs))
    assert containers, (
        "expected examples/k8s/serve-deployment.yaml to probe a container"
    )
    for container in containers:
        for probe, expected in _EXPECTED_PROBE_PATHS.items():
            path = container.get(probe, {}).get("httpGet", {}).get("path")
            if path is None:
                continue
            assert path != _STRICT_HEALTH_PATH, (
                f"examples/k8s {container['name']}.{probe} reads "
                f"{_STRICT_HEALTH_PATH}; see the chart test for why"
            )
            assert path == expected, (
                f"examples/k8s {container['name']}.{probe} is {path!r}, "
                f"expected {expected!r}"
            )


# ---------------------------------------------------------------------------
# RECOTEM_ALLOWED_HOSTS is a union, not a winner-takes-all
#
# The two sources used to be if/else: an explicit `env.RECOTEM_ALLOWED_HOSTS`
# replaced the ingress-derived list outright.  values.yaml tells operators to
# set it "when traffic reaches the pod under a hostname not listed in
# ingress.hosts (e.g. internal Service DNS)" -- i.e. to ADD a name -- and doing
# exactly that dropped every host the chart's own Ingress routes:
#
#   $ helm template ... --set ingress.enabled=true \
#       --set ingress.hosts[0].host=recotem.example.com \
#       --set env.RECOTEM_ALLOWED_HOSTS=recotem.recotem.svc.cluster.local
#     RECOTEM_ALLOWED_HOSTS = "localhost,recotem.recotem.svc.cluster.local"
#     Ingress rules:          host: recotem.example.com
#
# The chart renders an Ingress for a host it then refuses. Measured on a live
# cluster: a request whose Host header is absent from the list gets 400 from
# TrustedHostMiddleware while the pod stays Ready, so the failure looks like a
# broken Ingress rather than a chart value.
#
# `test_allowed_hosts_always_admits_the_probe_host` could not see this: its
# "override alongside ingress" case happens to name the same host in both
# places, and it only asserts `localhost` is present.
# ---------------------------------------------------------------------------


def _allowed_hosts(set_args: tuple[str, ...]) -> list[str]:
    docs = _load_all_strict(_helm_template(*set_args))
    values = [
        env["value"]
        for _, spec in _pod_specs_in(docs)
        for container in spec.get("containers", [])
        for env in container.get("env", [])
        if env.get("name") == "RECOTEM_ALLOWED_HOSTS"
    ]
    assert values, "expected the chart to render RECOTEM_ALLOWED_HOSTS"
    return [h.strip() for h in values[0].split(",")]


@requires_helm
def test_allowed_hosts_override_does_not_drop_the_ingress_hosts() -> None:
    hosts = _allowed_hosts(
        (
            "ingress.enabled=true",
            "ingress.hosts[0].host=recotem.example.com",
            "env.RECOTEM_ALLOWED_HOSTS=recotem.recotem.svc.cluster.local",
        )
    )
    assert "recotem.example.com" in hosts, (
        "the chart renders an Ingress routing recotem.example.com and then "
        f"refuses it: {hosts}. TrustedHostMiddleware 400s every external "
        "request while the pod stays Ready"
    )
    assert "recotem.recotem.svc.cluster.local" in hosts, (
        f"the operator's own hostname was dropped: {hosts}"
    )
    assert "localhost" in hosts, f"the probe host was dropped: {hosts}"


@requires_helm
def test_allowed_hosts_union_does_not_duplicate() -> None:
    """A name in both sources must appear once, and `localhost` never twice."""
    hosts = _allowed_hosts(
        (
            "ingress.enabled=true",
            "ingress.hosts[0].host=api.example.com",
            "env.RECOTEM_ALLOWED_HOSTS=api.example.com\\,localhost",
        )
    )
    assert sorted(hosts) == sorted(set(hosts)), f"duplicated entries: {hosts}"
    assert set(hosts) == {"api.example.com", "localhost"}, hosts


@requires_helm
def test_allowed_hosts_stays_unset_when_neither_source_supplies_one() -> None:
    """No ingress and no override must still omit the var, not render an empty one.

    An empty value would be worse than absent: `_split_csv_env` falls back to
    the app default only when the variable strips to empty, and an omitted
    variable is the shape the rest of the chart's comments describe.
    """
    docs = _load_all_strict(_helm_template())
    rendered = [
        env
        for _, spec in _pod_specs_in(docs)
        for container in spec.get("containers", [])
        for env in container.get("env", [])
        if env.get("name") == "RECOTEM_ALLOWED_HOSTS"
    ]
    assert rendered == [], f"expected the variable to be omitted, got {rendered}"
