"""An unregistered recipe name must not become its own Prometheus label.

The v1 verbs enter ``_request_metrics`` with the raw path segment, before
``_resolve_entry`` decides whether that recipe exists, and the 422 handler in
``serving/app.py`` reads the name straight out of the URL.  The route pattern
bounds only the shape of the name (``^[A-Za-z0-9_-]{1,64}$``), so without a
bound on the label an authenticated caller mints one counter series and one
latency histogram per name it invents, and ``prometheus_client`` never evicts
either.

A registered recipe keeps its own label -- that cardinality is the operator's
recipes directory, which is the whole point of the label.
"""

from __future__ import annotations

import hashlib
import re

import pytest

pytest.importorskip("prometheus_client")

from recotem.serving.metrics import UNKNOWN_RECIPE_LABEL  # noqa: E402

_RECIPE_LABEL_RE = re.compile(
    r'^recotem_v1_requests_total\{[^}]*recipe="([^"]*)"', re.MULTILINE
)
_LATENCY_LABEL_RE = re.compile(
    r'^recotem_v1_request_latency_seconds_bucket\{[^}]*recipe="([^"]*)"', re.MULTILINE
)

_PLAINTEXT = "metrics-label-test-key-long-enough-to-pass"

_RECIPE_YAML = """
name: {name}
source:
  type: csv
  path: {csv}
  delimiter: ","
  encoding: utf-8
  header: 0
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  metric: ndcg
  cutoff: 5
  n_trials: 1
output:
  path: {artifact}
  versioning: always_overwrite
"""


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from recotem.config import ServeConfig
    from recotem.serving.app import create_app

    recipes = tmp_path / "recipes"
    recipes.mkdir()
    # A recipe whose artifact does not exist registers as a stub: it is a
    # *known* name (503 on the verbs), which is exactly the arm that must keep
    # its own label.
    (recipes / "known.yaml").write_text(
        _RECIPE_YAML.format(
            name="known_recipe",
            csv=tmp_path / "missing.csv",
            artifact=tmp_path / "missing.recotem",
        )
    )

    digest = hashlib.scrypt(
        _PLAINTEXT.encode("utf-8"),
        salt=b"recotem.api-key.v1",
        n=2,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    monkeypatch.setenv("RECOTEM_API_KEYS", f"k1:sha256:{digest}")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", "s1:" + "cd" * 32)
    monkeypatch.setenv("RECOTEM_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "1")

    cfg = ServeConfig.from_env()
    cfg.recipes_dir = str(recipes)
    with TestClient(create_app(cfg)) as c:
        yield c


def _labels(client, pattern: re.Pattern[str]) -> set[str]:  # type: ignore[no-untyped-def]
    resp = client.get("/v1/metrics", headers={"X-API-Key": _PLAINTEXT})
    assert resp.status_code == 200, resp.status_code
    return set(pattern.findall(resp.text))


def test_unknown_recipe_names_collapse_to_one_label(client) -> None:  # type: ignore[no-untyped-def]
    """Fifty invented names must add one label value, not fifty."""
    before = _labels(client, _RECIPE_LABEL_RE)
    before_latency = _labels(client, _LATENCY_LABEL_RE)

    for i in range(50):
        resp = client.post(
            f"/v1/recipes/nosuchrecipe-{i:04d}:recommend",
            headers={"X-API-Key": _PLAINTEXT},
            json={"user_id": "u1", "limit": 5},
        )
        assert resp.status_code == 404, resp.status_code

    added = _labels(client, _RECIPE_LABEL_RE) - before
    added_latency = _labels(client, _LATENCY_LABEL_RE) - before_latency
    assert added == {UNKNOWN_RECIPE_LABEL}, added
    assert added_latency == {UNKNOWN_RECIPE_LABEL}, added_latency


def test_unknown_recipe_names_collapse_on_the_422_path(client) -> None:  # type: ignore[no-untyped-def]
    """The validation-error handler reads the name from the URL; same bound."""
    before = _labels(client, _RECIPE_LABEL_RE)

    for i in range(50):
        resp = client.post(
            f"/v1/recipes/badbody-{i:04d}:recommend",
            headers={"X-API-Key": _PLAINTEXT},
            json={"user_id": "u1", "limit": -7},
        )
        assert resp.status_code == 422, resp.status_code

    added = _labels(client, _RECIPE_LABEL_RE) - before
    assert added <= {UNKNOWN_RECIPE_LABEL}, added


def test_registered_recipe_keeps_its_own_label(client) -> None:
    """Control: a name the operator deployed is still labelled by name.

    Without this arm the first two tests would also pass if the label were
    hard-coded to the sentinel for every request.
    """
    before = _labels(client, _RECIPE_LABEL_RE)

    resp = client.post(
        "/v1/recipes/known_recipe:recommend",
        headers={"X-API-Key": _PLAINTEXT},
        json={"user_id": "u1", "limit": 5},
    )
    # The artifact is missing, so the recipe is registered but not loaded.
    assert resp.status_code == 503, resp.status_code

    added = _labels(client, _RECIPE_LABEL_RE) - before
    assert "known_recipe" in added, added
    assert UNKNOWN_RECIPE_LABEL not in added, added


def test_sentinel_cannot_collide_with_a_real_recipe_name() -> None:
    """The sentinel is unrepresentable under the recipe-name pattern."""
    from recotem.serving.routes import _RECIPE_NAME_RE

    assert re.match(_RECIPE_NAME_RE, UNKNOWN_RECIPE_LABEL) is None
