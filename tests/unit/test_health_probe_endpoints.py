"""Liveness and readiness must not share ``/health``'s count-based rule.

Dropping one untrained recipe into a live ``--recipes`` directory makes
``/v1/health`` return 503 within one watcher poll, while every already-loaded
model still serves 200.  The shipped chart and ``examples/k8s/`` pointed
readiness *and* liveness at that endpoint, so a routine "1 YAML = 1 model"
onboarding took every replica out of the Service and then CrashLooped it -- and
each restart reloaded the same missing artifact, so it never self-healed.

Four files ship a probe pointed at this server, not two, and the check below
covers all four.  Two of them are Docker's: the image ``HEALTHCHECK`` and
``compose.yaml``'s ``serve`` healthcheck, which orchestrators that act on
container health (Swarm, ECS, ``docker compose up --wait``, another service's
``depends_on: condition: service_healthy``) read as the readiness question.
The other two are the Helm chart and ``examples/k8s/``.  A guard that reads
only one of them lets the same defect return through any of the others.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from recotem.config import ServeConfig

_ROOT = Path(__file__).resolve().parents[2]

# The count-based endpoint.  Fine for a startup gate ("has train produced an
# artifact for every recipe yet?"); wrong for anything that removes traffic or
# restarts a process, because one untrained recipe among many trips it.
_COUNT_BASED = "/v1/health"
_READY = "/v1/health/ready"
_LIVE = "/v1/health/live"

_HEALTH_PATH_RE = re.compile(r"/v1/health(?:/[a-z]+)?")


def _config(tmp_path: Path) -> ServeConfig:
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    cfg = ServeConfig()
    cfg.signing_keys_raw = "active:" + "aa" * 32
    cfg.recipes_dir = str(recipes_dir)  # type: ignore[attr-defined]
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]
    return cfg


def _write_recipe(recipes_dir: Path, name: str, artifact: Path) -> None:
    (recipes_dir / f"{name}.yaml").write_text(
        f"""
name: {name}
source:
  type: csv
  path: {recipes_dir / "data.csv"}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms:
    - TopPop
output:
  path: {artifact}
"""
    )


def _client(cfg: ServeConfig) -> TestClient:
    from recotem.serving.app import create_app

    return TestClient(create_app(cfg))


def _probe_paths(manifest: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in manifest["spec"]["template"]["spec"]["containers"]:
        for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
            if probe in c:
                out[probe] = c[probe]["httpGet"]["path"]
    return out


def test_liveness_is_200_even_when_no_recipe_loads(tmp_path: Path) -> None:
    """A restart cannot fix a missing artifact, so liveness must not ask."""
    cfg = _config(tmp_path)
    recipes = Path(cfg.recipes_dir)  # type: ignore[arg-type]
    _write_recipe(recipes, "never_trained", tmp_path / "does-not-exist.recotem")
    client = _client(cfg)

    assert client.get("/v1/health").status_code == 503
    live = client.get("/v1/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "alive"


def test_readiness_survives_one_untrained_recipe_among_loaded_ones(
    tmp_path: Path, valid_artifact_path: Path
) -> None:
    """The regression this endpoint exists for."""
    cfg = _config(tmp_path)
    recipes = Path(cfg.recipes_dir)  # type: ignore[arg-type]
    # the fixture artifact is signed for the recipe name "test"
    _write_recipe(recipes, "test", valid_artifact_path)
    _write_recipe(recipes, "new_tenant", tmp_path / "does-not-exist.recotem")
    client = _client(cfg)

    health = client.get("/v1/health")
    assert health.status_code == 503, "count-based /health still reports degraded"
    assert health.json()["loaded"] == 1

    ready = client.get("/v1/health/ready")
    assert ready.status_code == 200, (
        "one untrained recipe must not take a replica that serves another "
        "recipe out of the Service"
    )
    assert ready.json() == {"status": "ready", "total": 2, "loaded": 1}


def test_readiness_fails_a_cold_fleet(tmp_path: Path) -> None:
    """The documented first-install guarantee must still hold."""
    cfg = _config(tmp_path)
    recipes = Path(cfg.recipes_dir)  # type: ignore[arg-type]
    _write_recipe(recipes, "never_trained", tmp_path / "does-not-exist.recotem")
    client = _client(cfg)

    ready = client.get("/v1/health/ready")
    assert ready.status_code == 503
    assert ready.json()["status"] == "unready"


def test_shipped_example_splits_the_three_probes() -> None:
    manifest = yaml.safe_load(
        (_ROOT / "examples" / "k8s" / "serve-deployment.yaml").read_text()
    )
    paths = _probe_paths(manifest)
    assert paths["startupProbe"] == "/v1/health/ready", (
        "a failing startup probe RESTARTS the container rather than merely "
        "withholding traffic, so the strict count-based gate turned one "
        "untrained recipe into a restart loop for every new pod; readiness' "
        "question still 503s on a cold store, keeping the first-install "
        "guarantee"
    )
    assert paths["readinessProbe"] == "/v1/health/ready"
    assert paths["livenessProbe"] == "/v1/health/live", (
        "a restart cannot fix a missing artifact -- the replacement pod reads "
        "the same store, dies the same way, and drops the models that did load"
    )


def _template_probe_paths(text: str) -> dict[str, str]:
    """Probe -> path from a Go-templated manifest, without rendering it.

    ``helm template`` is not used here on purpose: helm is not installed in the
    ``pytest`` CI job (only in ``manifests.yml``), so a helm-gated assertion
    would silently skip in the job that runs on every source PR -- the exact
    way a guard gets blinded by its own environment rather than by its target.
    The probe blocks are plain YAML inside the template, so a line scan reads
    them on any machine.
    """
    out: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        for probe in ("startupProbe:", "readinessProbe:", "livenessProbe:"):
            if stripped == probe:
                current = probe.rstrip(":")
        if current and stripped.startswith("path:"):
            out.setdefault(current, stripped.split(":", 1)[1].strip())
            current = None
    return out


def _healthcheck_urls(text: str) -> list[str]:
    """Every /v1/health* path mentioned on a container healthcheck line."""
    return [
        m
        for line in text.splitlines()
        if "urlopen" in line
        for m in _HEALTH_PATH_RE.findall(line)
    ]


def test_helm_chart_splits_the_three_probes() -> None:
    """The chart is the primary production deployment path and had no guard.

    ``test_shipped_example_splits_the_three_probes`` reads ``examples/k8s/``
    only, and no other test or ``validate-manifests.sh`` check looks at the
    probe paths, so reverting the chart's readiness and liveness back to
    ``/v1/health`` left the whole suite and the manifest gate green.

    This test is the one that actually runs in the ``pytest`` job. Its sibling
    in ``tests/unit/test_k8s_manifests.py`` renders with ``helm template`` and
    is therefore ``@requires_helm``-skipped there, so the line scan below is
    the only chart probe assertion that executes on a source PR -- which is
    why the expectations here have to be kept in step with that file by hand.
    """
    paths = _template_probe_paths(
        (_ROOT / "helm" / "recotem" / "templates" / "deployment.yaml").read_text()
    )
    assert paths.get("startupProbe") == _READY, (
        "a failing startupProbe RESTARTS the container, it does not withhold "
        "traffic -- so the strict count-based endpoint there puts every newly "
        "created pod into a restart loop while one recipe is untrained, "
        "stalling rollouts and scale-outs (#241, measured on a live cluster). "
        "/v1/health/ready still 503s on a cold store, which is the "
        "first-install guarantee this endpoint was chosen for"
    )
    assert paths.get("readinessProbe") == _READY, (
        "readiness on the count-based endpoint takes every replica out of the "
        "Service at once when one recipe is added and not yet trained -- they "
        "all read the same recipes directory"
    )
    assert paths.get("livenessProbe") == _LIVE, (
        "a restart cannot fix a missing artifact; liveness on the count-based "
        "endpoint CrashLoops the pod and drops the models that did load"
    )
    # The invariant the three assertions above are each an instance of: the
    # strict endpoint answers "is EVERY recipe present?", and every probe
    # reacts to a 503 by removing a pod that is serving the recipes it does
    # have. It is an alerting endpoint, not a probe endpoint.
    assert _COUNT_BASED not in paths.values(), (
        f"a chart probe reads the strict count-based {_COUNT_BASED}: {paths}"
    )


def test_container_healthchecks_use_the_readiness_endpoint() -> None:
    """The image and Compose healthchecks are probes too, and were missed.

    A container has exactly ONE healthcheck, and Swarm, ECS, `docker compose
    up --wait` and `depends_on: condition: service_healthy` all read it as the
    readiness question.  Pointed at the count-based endpoint it reports a
    server that is still serving every model it loaded as unhealthy the moment
    one untrained recipe appears in the mounted recipes directory -- and the
    replacement container reads the same directory, so it never self-heals.
    """
    sources = {
        "Dockerfile": _ROOT / "Dockerfile",
        "compose.yaml": _ROOT / "compose.yaml",
        "docs/deployment/docker.md": _ROOT / "docs" / "deployment" / "docker.md",
    }
    seen = 0
    for label, path in sources.items():
        urls = _healthcheck_urls(path.read_text(encoding="utf-8"))
        assert urls, f"{label}: expected at least one healthcheck probe URL"
        for url in urls:
            seen += 1
            assert url == _READY, (
                f"{label}: healthcheck probes {url}, which answers 'is EVERY "
                "recipe present?'. One untrained recipe then marks a container "
                "that is still serving every loaded model unhealthy, and the "
                f"replacement fails identically. Use {_READY}."
            )
    assert seen >= 3, f"expected the shipped healthcheck probes, found {seen}"


def test_shipped_allowed_hosts_example_keeps_localhost() -> None:
    """Any RECOTEM_ALLOWED_HOSTS example must include `localhost`.

    The variable REPLACES the default rather than extending it
    (``config._split_csv_env`` falls back only on an empty value), and all
    three probes send ``Host: localhost``. A copied example without it fails
    every probe with HTTP 400 -- a correct TrustedHostMiddleware rejection,
    which is exactly why nothing in the pod logs points at the cause. The chart
    prepends it (#206); a hand-written env var does not.
    """
    sources = [
        _ROOT / "examples" / "k8s" / "serve-deployment.yaml",
        _ROOT / "docs" / "deployment" / "k8s.md",
    ]
    seen = 0
    for path in sources:
        for line in path.read_text().splitlines():
            if "RECOTEM_ALLOWED_HOSTS" in line or "recotem.example.com" not in line:
                continue
            if "value:" not in line:
                continue
            seen += 1
            assert "localhost" in line, (
                f"{path.name}: {line.strip()} omits localhost; copying it fails "
                "every probe with HTTP 400 and CrashLoops the Deployment"
            )
    assert seen >= 2, f"expected the shipped ALLOWED_HOSTS examples, found {seen}"
