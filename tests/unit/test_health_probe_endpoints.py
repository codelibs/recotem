"""Liveness and readiness must not share ``/health``'s count-based rule.

Dropping one untrained recipe into a live ``--recipes`` directory makes
``/v1/health`` return 503 within one watcher poll, while every already-loaded
model still serves 200.  The shipped chart and ``examples/k8s/`` pointed
readiness *and* liveness at that endpoint, so a routine "1 YAML = 1 model"
onboarding took every replica out of the Service and then CrashLooped it -- and
each restart reloaded the same missing artifact, so it never self-healed.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from recotem.config import ServeConfig

_ROOT = Path(__file__).resolve().parents[2]


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
