"""Integration test: concurrent /predict during hot-swap.

10 reader threads issue /predict while the watcher replaces the model entry.
No data races, no panics, and all responses are either 200 or 503.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app


def _make_entry(name: str, version: int) -> ModelEntry:
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [
        (f"item{i}", 1.0 - i * 0.1) for i in range(5)
    ]
    return ModelEntry(
        name=name,
        recommender=rec,
        header={
            "best_class": "TopPop",
            "trained_at": f"2026-01-0{version}T00:00:00Z",
        },
        kid="active",
    )


def test_concurrent_predict_during_swap_no_data_race() -> None:
    """10 reader threads + 5 writer threads run for 1s without data races."""
    registry = ModelRegistry()
    registry.replace("concurrent_recipe", _make_entry("concurrent_recipe", 1))

    errors = []
    request_counts = {"ok": 0, "error": 0}
    swap_counts = {"swaps": 0}
    lock = threading.Lock()

    def _reader():
        for _ in range(50):
            entry = registry.get("concurrent_recipe")
            if entry is None:
                with lock:
                    errors.append("entry is None")
                return
            try:
                result = entry.recommender.get_recommendation_for_known_user_id(
                    "user1", 5
                )
                assert isinstance(result, list)
                with lock:
                    request_counts["ok"] += 1
            except Exception as e:
                with lock:
                    errors.append(str(e))
            time.sleep(0.001)

    def _swapper():
        for v in range(10):
            new_entry = _make_entry("concurrent_recipe", v + 2)
            registry.replace("concurrent_recipe", new_entry)
            with lock:
                swap_counts["swaps"] += 1
            time.sleep(0.005)

    readers = [threading.Thread(target=_reader) for _ in range(10)]
    swappers = [threading.Thread(target=_swapper) for _ in range(2)]

    for t in readers + swappers:
        t.start()
    for t in readers + swappers:
        t.join(timeout=10.0)

    assert errors == [], f"Data races or errors during concurrent access: {errors}"
    assert request_counts["ok"] > 0
    assert swap_counts["swaps"] > 0


def test_in_flight_request_completes_with_old_model_during_swap() -> None:
    """An in-flight request holds the old entry reference across a swap."""
    registry = ModelRegistry()
    old_entry = _make_entry("recipe", 1)
    registry.replace("recipe", old_entry)

    # Grab reference to old entry before swap
    in_flight = registry.get("recipe")
    assert in_flight is old_entry

    # Swap while in-flight holds its reference
    new_entry = _make_entry("recipe", 2)
    registry.replace("recipe", new_entry)

    # Old reference still works
    result = in_flight.recommender.get_recommendation_for_known_user_id("u1", 5)
    assert isinstance(result, list)

    # New entry is now active
    current = registry.get("recipe")
    assert current is new_entry


def test_two_consecutive_swaps_register_second_artifact() -> None:
    """After two swaps, the registry reflects the second (latest) artifact."""
    registry = ModelRegistry()
    e1 = _make_entry("r", 1)
    e2 = _make_entry("r", 2)
    e3 = _make_entry("r", 3)

    registry.replace("r", e1)
    registry.replace("r", e2)
    registry.replace("r", e3)

    assert registry.get("r") is e3


# ---------------------------------------------------------------------------
# Finding 2: concurrent HTTP recommend requests during registry.replace_with_marker
# ---------------------------------------------------------------------------


def _make_http_entry(version: int) -> ModelEntry:
    """Build a loaded ModelEntry for HTTP testing with stable recommender."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [
        (f"item{i}", 1.0 - i * 0.1) for i in range(3)
    ]
    return ModelEntry(
        name="concurrent_recipe",
        recommender=rec,
        header={"best_class": "TopPop"},
        kid="active",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, f"{version:064x}"),
        loaded_at_unix=float(version),
    )


def test_http_concurrent_recommend_during_registry_replace() -> None:
    """N (>=20) concurrent :recommend requests while a background thread calls
    registry.replace_with_marker repeatedly.

    Assertions:
    - No 500 responses; every response is 200 or 503.
    - Every 200 carries X-Recotem-Model-Version matching the body's model_version.
    """
    N_REQUESTS = 30
    N_SWAPS = 20

    registry = ModelRegistry()
    registry.replace("concurrent_recipe", _make_http_entry(1))

    app = build_v1_app(registry)
    # TestClient is WSGI-based; use ThreadPoolExecutor for concurrent requests
    client = TestClient(app, raise_server_exceptions=False)

    errors: list[str] = []
    responses: list[tuple[int, dict]] = []
    lock = threading.Lock()

    def _do_request() -> None:
        try:
            r = client.post(
                "/v1/recipes/concurrent_recipe:recommend",
                json={"user_id": "u1", "limit": 3},
            )
            with lock:
                responses.append((r.status_code, r.headers))
                if r.status_code not in (200, 503):
                    errors.append(f"Unexpected status {r.status_code}: {r.text[:200]}")
                elif r.status_code == 200:
                    body = r.json()
                    hdr_val = r.headers.get("x-recotem-model-version", "")
                    body_ver = body.get("model_version", "")
                    if hdr_val != body_ver:
                        errors.append(
                            f"Header version {hdr_val!r} != body version {body_ver!r}"
                        )
        except Exception as exc:
            with lock:
                errors.append(f"Request raised: {exc}")

    swap_stop = threading.Event()

    def _swap_loop() -> None:
        version = 2
        while not swap_stop.is_set():
            new_entry = _make_http_entry(version)
            marker = (None, f"{version:064x}")
            registry.replace_with_marker("concurrent_recipe", new_entry, marker)
            version += 1
            time.sleep(0.001)

    swap_thread = threading.Thread(target=_swap_loop, daemon=True)
    swap_thread.start()

    with ThreadPoolExecutor(max_workers=N_REQUESTS) as pool:
        futures = [pool.submit(_do_request) for _ in range(N_REQUESTS)]
        for f in futures:
            f.result(timeout=10.0)

    swap_stop.set()
    swap_thread.join(timeout=2.0)

    ok_count = sum(1 for sc, _ in responses if sc == 200)
    assert len(responses) == N_REQUESTS, (
        f"Expected {N_REQUESTS} responses; got {len(responses)}"
    )
    assert not errors, "Concurrent recommend/swap errors:\n" + "\n".join(errors)
    assert ok_count > 0, "At least some requests must succeed with 200"
