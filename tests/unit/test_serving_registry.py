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

from recotem.serving.registry import ModelEntry, ModelRegistry


def _make_entry(name: str, recommender: object | None = None) -> ModelEntry:
    return ModelEntry(
        name=name,
        recommender=recommender or MagicMock(),
        header={
            "best_class": "TopPopRecommender",
            "trained_at": "2026-01-01T00:00:00Z",
        },
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


# ---------------------------------------------------------------------------
# E1–E4: set_load_error and update_loaded_marker
# ---------------------------------------------------------------------------


def test_registry_set_load_error_marks_existing_entry() -> None:
    """set_load_error sets last_load_error on an existing entry and returns True."""
    reg = ModelRegistry()
    entry = _make_entry("recipe_err")
    reg.replace("recipe_err", entry)

    result = reg.set_load_error("recipe_err", "hmac mismatch during hot-swap")

    assert result is True
    assert reg.get("recipe_err").last_load_error == "hmac mismatch during hot-swap"


def test_registry_set_load_error_no_op_on_missing_entry() -> None:
    """set_load_error returns False (no-op) when the entry does not exist."""
    reg = ModelRegistry()

    result = reg.set_load_error("nonexistent", "some error")

    assert result is False


def test_registry_set_load_error_clears_when_none() -> None:
    """Passing None to set_load_error clears a previously-set error."""
    reg = ModelRegistry()
    entry = _make_entry("recipe_clr")
    entry.last_load_error = "previous error"
    reg.replace("recipe_clr", entry)

    reg.set_load_error("recipe_clr", None)

    assert reg.get("recipe_clr").last_load_error is None


def test_registry_update_loaded_marker_writes_under_lock() -> None:
    """update_loaded_marker is safe under concurrent read/write.

    We run 100 iterations of concurrent readers and a writer to verify that
    the marker tuple is always a valid tuple (no partial-write tearing).
    """
    reg = ModelRegistry()
    entry = _make_entry("shared_marker")
    reg.replace("shared_marker", entry)

    errors: list[str] = []

    def _reader() -> None:
        for _ in range(100):
            e = reg.get("shared_marker")
            if e is None:
                errors.append("entry disappeared")
                return
            m = e._loaded_marker
            # A valid marker is always a 2-tuple
            if not (isinstance(m, tuple) and len(m) == 2):
                errors.append(f"unexpected marker type/length: {m!r}")
            time.sleep(0.0001)

    def _writer() -> None:
        for i in range(50):
            reg.update_loaded_marker("shared_marker", (i, f"sha{i:04d}" * 16))
            time.sleep(0.0001)

    readers = [threading.Thread(target=_reader) for _ in range(4)]
    writer = threading.Thread(target=_writer)
    threads = readers + [writer]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert errors == [], f"Concurrent marker access errors: {errors}"
