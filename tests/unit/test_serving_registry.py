"""Unit tests for recotem.serving.registry.

Tests:
- Atomic replace
- RLock semantics (concurrent access)
- In-flight references stay valid
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from recotem.serving.registry import ModelEntry, ModelRegistry


def _make_entry(name: str, recommender: object | None = None) -> ModelEntry:
    return ModelEntry(
        name=name,
        recommender=recommender or MagicMock(),
        header={"best_class": "TopPopRecommender", "trained_at": "2026-01-01T00:00:00Z"},
        kid="active",
    )


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------

def test_registry_get_returns_none_for_unknown() -> None:
    reg = ModelRegistry()
    assert reg.get("nonexistent") is None


def test_registry_replace_and_get() -> None:
    reg = ModelRegistry()
    entry = _make_entry("recipe1")
    reg.replace("recipe1", entry)
    assert reg.get("recipe1") is entry


def test_registry_remove_clears_entry() -> None:
    reg = ModelRegistry()
    entry = _make_entry("recipe1")
    reg.replace("recipe1", entry)
    reg.remove("recipe1")
    assert reg.get("recipe1") is None


def test_registry_remove_noop_if_not_present() -> None:
    reg = ModelRegistry()
    reg.remove("ghost")  # should not raise


def test_registry_list_returns_all_entries() -> None:
    reg = ModelRegistry()
    e1 = _make_entry("r1")
    e2 = _make_entry("r2")
    reg.replace("r1", e1)
    reg.replace("r2", e2)
    entries = reg.list()
    assert len(entries) == 2


def test_registry_names_sorted() -> None:
    reg = ModelRegistry()
    reg.replace("zzz", _make_entry("zzz"))
    reg.replace("aaa", _make_entry("aaa"))
    assert reg.names() == ["aaa", "zzz"]


# ---------------------------------------------------------------------------
# Atomic replace
# ---------------------------------------------------------------------------

def test_atomic_replace_old_entry_replaced() -> None:
    reg = ModelRegistry()
    old = _make_entry("r", recommender=MagicMock(name="old"))
    new = _make_entry("r", recommender=MagicMock(name="new"))
    reg.replace("r", old)
    reg.replace("r", new)
    assert reg.get("r") is new


# ---------------------------------------------------------------------------
# Concurrent access
# ---------------------------------------------------------------------------

def test_concurrent_replace_no_data_race() -> None:
    """Multiple threads replacing and reading do not cause data races."""
    reg = ModelRegistry()
    reg.replace("shared", _make_entry("shared"))

    errors = []

    def _reader():
        for _ in range(100):
            entry = reg.get("shared")
            if entry is None:
                errors.append("entry is None during read")
            time.sleep(0.0001)

    def _writer():
        for i in range(50):
            reg.replace("shared", _make_entry("shared"))
            time.sleep(0.0001)

    threads = [threading.Thread(target=_reader) for _ in range(5)]
    threads += [threading.Thread(target=_writer) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert errors == [], f"Data race detected: {errors}"


# ---------------------------------------------------------------------------
# In-flight references stay valid
# ---------------------------------------------------------------------------

def test_in_flight_reference_stays_valid_after_replace() -> None:
    """An existing reference to an old entry is not invalidated by replace."""
    reg = ModelRegistry()
    old_entry = _make_entry("r")
    reg.replace("r", old_entry)

    # Simulate in-flight: grab a reference before the swap
    in_flight_ref = reg.get("r")
    assert in_flight_ref is old_entry

    # Swap
    new_entry = _make_entry("r")
    reg.replace("r", new_entry)

    # The old reference is still alive and usable
    assert in_flight_ref is old_entry
    assert in_flight_ref.kid == "active"


# ---------------------------------------------------------------------------
# Health snapshot
# ---------------------------------------------------------------------------

def test_health_snapshot_contains_loaded_true() -> None:
    reg = ModelRegistry()
    entry = _make_entry("healthy_recipe")
    reg.replace("healthy_recipe", entry)
    snap = reg.health_snapshot()
    assert "healthy_recipe" in snap
    assert snap["healthy_recipe"]["loaded"] is True


def test_health_snapshot_shows_last_load_error() -> None:
    reg = ModelRegistry()
    entry = _make_entry("broken")
    entry.last_load_error = "hmac mismatch"
    reg.replace("broken", entry)
    snap = reg.health_snapshot()
    assert snap["broken"].get("error") == "hmac mismatch"
