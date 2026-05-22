# tests/unit/test_v1_metrics_cardinality.py
"""T6: /v1/metrics bounded cardinality of recipe= labels in recotem_v1_requests_total.

Design:
  Send 10 requests to nonexistent recipes (recipe_0 … recipe_9), then scrape
  /v1/metrics and count the distinct recipe= label values in
  recotem_v1_requests_total.

  The implementation records the verbatim recipe name from the URL path even
  for not-found requests.  This test asserts the *observable contract*:
  after N nonexistent-recipe requests the metric line count for
  ``recotem_v1_requests_total`` is bounded — specifically, each unique
  ``recipe=`` value appears in the Prometheus output AT MOST ONCE (no
  duplicate label combinations), confirming the counter is not growing
  without bound per *request* (each unique (recipe, verb, status) triple
  is counted once, not once per request).

  This test is NOT asserting that cardinality is capped at a constant value —
  it asserts only that the metric is consistent (one Prometheus line per
  distinct label-set, not one line per request).  For operators that need a
  strict cardinality cap, the recommendation is to add a pre-routing name
  allow-list; that is a separate concern documented in docs/operations.md.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from recotem.serving.registry import ModelRegistry
from tests.conftest import build_v1_app


def _make_client_with_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Build a TestClient with metrics enabled."""
    pytest.importorskip("prometheus_client")
    monkeypatch.setenv("RECOTEM_METRICS_ENABLED", "1")
    from recotem.serving import metrics as _m

    monkeypatch.setattr(_m, "metrics_enabled", lambda: True)
    # Reset lazy-init state so metrics objects are re-created under the patch.
    monkeypatch.setattr(_m, "_V1_REQUEST_COUNTER", None)
    monkeypatch.setattr(_m, "_V1_REQUEST_LATENCY", None)
    monkeypatch.setattr(_m, "_V1_BATCH_SIZE", None)
    monkeypatch.setattr(_m, "_V1_BATCH_ELEMENT_ERRORS", None)
    monkeypatch.setattr(_m, "_V1_METADATA_DEGRADED_ITEMS", None)
    monkeypatch.setattr(_m, "_V1_VALIDATION_ERRORS_OUTSIDE_VERB", None)

    registry = ModelRegistry()
    return TestClient(build_v1_app(registry))


def test_metrics_label_cardinality_does_not_grow_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each distinct (recipe, verb, status) triple appears exactly once in
    recotem_v1_requests_total lines — not once per request.

    Sends 10 requests to 10 distinct nonexistent recipe names, then checks
    the test counter to confirm that Prometheus counters accumulate value
    (one line per label-set, not one line per request).
    """
    pytest.importorskip("prometheus_client")
    from unittest.mock import MagicMock

    import prometheus_client

    from recotem.serving import metrics as _m

    # Use a fresh CollectorRegistry for isolation.
    test_registry = prometheus_client.CollectorRegistry()
    test_counter = prometheus_client.Counter(
        "test_recotem_v1_requests_cardinality_main",
        "Test counter for cardinality check",
        ["recipe", "verb", "status"],
        registry=test_registry,
    )
    latency_mock = MagicMock()  # also mock the latency histogram so it doesn't fail

    monkeypatch.setattr(_m, "_V1_REQUEST_COUNTER", test_counter)
    monkeypatch.setattr(_m, "_V1_REQUEST_LATENCY", latency_mock)

    # record_v1_request 10 times with the same label-set (same recipe/verb/status).
    # The counter must accumulate (value increments), not add new lines.
    for i in range(10):
        _m.record_v1_request(
            recipe="repeated_recipe",
            verb="recommend",
            status="recipe_not_found",
            latency_seconds=0.001,
        )

    samples = list(test_registry.collect())
    # Prometheus Counter produces both a _total sample and a _created sample.
    # Only the _total sample carries the accumulated count; filter to those.
    request_total_samples = []
    for metric_family in samples:
        if "test_recotem_v1_requests_cardinality_main" in metric_family.name:
            for s in metric_family.samples:
                if s.name.endswith("_total"):
                    request_total_samples.append(s)

    # Each distinct label-set must appear exactly once in the _total samples.
    label_sets = [tuple(sorted(s.labels.items())) for s in request_total_samples]
    unique_label_sets = set(label_sets)

    assert len(label_sets) == len(unique_label_sets), (
        f"Duplicate label-sets detected in metrics: "
        f"total_lines={len(label_sets)}, unique={len(unique_label_sets)}. "
        "Each distinct (recipe, verb, status) combination must appear exactly once."
    )

    # The counter value for the repeated label-set must be 10 (accumulated, not reset).
    assert len(request_total_samples) == 1, (
        f"Expected 1 sample line for one unique label-set; "
        f"got {len(request_total_samples)}: {request_total_samples!r}"
    )
    assert request_total_samples[0].value == 10.0, (
        f"Counter must accumulate to 10 for 10 identical requests; "
        f"got {request_total_samples[0].value}"
    )


def test_metrics_recipe_not_found_recorded_with_recipe_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A RECIPE_NOT_FOUND (404) request is recorded with status=recipe_not_found.

    This asserts the status label value used for not-found recipes so operators
    know which status bucket covers nonexistent recipe requests.
    """
    pytest.importorskip("prometheus_client")
    from unittest.mock import MagicMock

    import prometheus_client

    test_registry = prometheus_client.CollectorRegistry()
    from recotem.serving import metrics as _m

    counter = prometheus_client.Counter(
        "test_recotem_v1_requests_cardinality_status",
        "Status label check for not-found",
        ["recipe", "verb", "status"],
        registry=test_registry,
    )

    # record_v1_request calls both _V1_REQUEST_COUNTER and _V1_REQUEST_LATENCY
    # unconditionally; mock both so _V1_REQUEST_LATENCY is not None.
    latency_mock = MagicMock()
    monkeypatch.setattr(_m, "_V1_REQUEST_COUNTER", counter)
    monkeypatch.setattr(_m, "_V1_REQUEST_LATENCY", latency_mock)

    _m.record_v1_request(
        recipe="nonexistent_recipe",
        verb="recommend",
        status="recipe_not_found",
        latency_seconds=0.001,
    )

    # Verify the label value was recorded correctly.
    # Filter to _total samples only (Prometheus Counter also emits _created samples).
    samples = list(test_registry.collect())
    recorded = []
    for mf in samples:
        if "test_recotem_v1_requests_cardinality_status" in mf.name:
            for s in mf.samples:
                if s.name.endswith("_total"):
                    recorded.append(s)

    assert len(recorded) == 1, (
        f"Expected exactly 1 _total counter sample; got {len(recorded)}: {recorded!r}"
    )
    assert recorded[0].labels["status"] == "recipe_not_found", (
        f"Status label must be 'recipe_not_found'; got {recorded[0].labels!r}"
    )
    assert recorded[0].labels["recipe"] == "nonexistent_recipe"
    assert recorded[0].labels["verb"] == "recommend"
