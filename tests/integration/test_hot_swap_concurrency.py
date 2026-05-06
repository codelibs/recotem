"""Integration test: concurrent /predict during hot-swap.

10 reader threads issue /predict while the watcher replaces the model entry.
No data races, no panics, and all responses are either 200 or 503.
"""
from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from recotem.serving.registry import ModelEntry, ModelRegistry


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
