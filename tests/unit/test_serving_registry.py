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


# ---------------------------------------------------------------------------
# MAJOR-11: update_loaded_marker lock contract
# ---------------------------------------------------------------------------


def test_update_loaded_marker_takes_lock() -> None:
    """update_loaded_marker must execute inside the registry lock.

    We verify this by replacing the registry lock with a tracked proxy that
    records acquire/release calls, then confirm that acquire was called at
    least once during update_loaded_marker execution.
    """

    reg = ModelRegistry()
    entry = _make_entry("lock_test")
    reg.replace("lock_test", entry)

    acquire_count = [0]
    original_lock = reg._lock

    class _TrackedRLock:
        """Proxy that counts acquire calls on the underlying RLock."""

        def __init__(self, inner):
            self._inner = inner

        def acquire(self, *a, **kw):
            acquire_count[0] += 1
            return self._inner.acquire(*a, **kw)

        def release(self):
            self._inner.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *exc):
            self.release()

    tracked = _TrackedRLock(original_lock)
    reg._lock = tracked  # type: ignore[assignment]

    new_marker = ("new_mtime", "abc123" * 10)
    count_before = acquire_count[0]
    result = reg.update_loaded_marker("lock_test", new_marker)

    assert result is True
    assert reg.get("lock_test")._loaded_marker == new_marker
    # The lock must have been acquired at least once during update_loaded_marker
    assert acquire_count[0] > count_before, (
        "update_loaded_marker must acquire the registry lock; "
        f"acquire count did not change (before={count_before}, after={acquire_count[0]})"
    )


def test_update_loaded_marker_returns_false_for_missing() -> None:
    """update_loaded_marker returns False for a name not in the registry."""
    reg = ModelRegistry()
    result = reg.update_loaded_marker("does_not_exist", ("mtime", "sha256"))
    assert result is False


# ---------------------------------------------------------------------------
# I-D: models_dict() — hmac/key not in header schema, strip removed
# ---------------------------------------------------------------------------


def test_models_dict_returns_all_header_fields() -> None:
    """models_dict() must include all header fields without dropping any.

    The artifact header JSON never contains 'hmac' or 'key' fields — those
    live in separate binary regions.  After the I-D fix the misleading filter
    is removed and all header fields pass through.
    """
    header = {
        "recipe_name": "test_recipe",
        "recipe_hash": "abc123",
        "best_class": "TopPopRecommender",
        "best_params": {},
        "best_score": 0.75,
        "metric": "ndcg",
        "cutoff": 10,
        "tuning": {},
        "data_stats": {"n_users": 100, "n_items": 50, "n_interactions": 500},
        "recotem_version": "2.0.0",
        "irspack_version": "0.3.0",
        "trained_at": "2026-01-01T00:00:00Z",
    }
    entry = ModelEntry(
        name="test_recipe",
        recommender=MagicMock(),
        header=header,
        kid="active",
    )
    result = entry.models_dict()

    # All header keys must be present.
    for key in header:
        assert key in result, f"models_dict() dropped header field {key!r}"

    # Mandatory additions must also be present.
    assert result["kid"] == "active"
    assert result["name"] == "test_recipe"


def test_models_dict_does_not_strip_hmac_key_if_present() -> None:
    """If a header somehow contains 'hmac' or 'key' (defensive), they are retained.

    The artifact format never puts these in header_json, but a future extension
    or test fixture might add them.  The old filter would silently drop them;
    after the I-D fix they pass through unchanged so callers see the full dict.
    """
    header_with_extras = {
        "recipe_name": "defensive_test",
        "best_score": 0.5,
        # These would never appear from the real artifact writer, but confirm
        # no silent data loss occurs if they did.
        "hmac": "should_not_be_dropped",
        "key": "also_not_dropped",
    }
    entry = ModelEntry(
        name="defensive_test",
        recommender=MagicMock(),
        header=header_with_extras,
        kid="active",
    )
    result = entry.models_dict()

    # Nothing should be silently dropped.
    assert result.get("hmac") == "should_not_be_dropped", (
        "models_dict() must not strip 'hmac' from header"
    )
    assert result.get("key") == "also_not_dropped", (
        "models_dict() must not strip 'key' from header"
    )


def test_models_dict_normal_header_has_no_hmac_or_key() -> None:
    """A normally-constructed ModelEntry header does not contain 'hmac' or 'key'.

    This documents the invariant: the artifact format stores HMAC and kid in
    binary fields, never in header_json.  Confirms the strip was genuinely dead
    code, not a security guard against real header content.
    """
    # Standard header as produced by artifact/io.py.
    standard_header = {
        "recipe_name": "normal",
        "best_class": "TopPopRecommender",
        "best_score": 0.9,
        "trained_at": "2026-01-01T00:00:00Z",
    }
    entry = ModelEntry(
        name="normal",
        recommender=MagicMock(),
        header=standard_header,
        kid="active",
    )
    result = entry.models_dict()

    assert "hmac" not in standard_header, (
        "A real artifact header must never contain an 'hmac' field"
    )
    assert "key" not in standard_header, (
        "A real artifact header must never contain a 'key' field"
    )
    # The result is the header plus kid/name — nothing more, nothing less.
    expected_keys = set(standard_header.keys()) | {"kid", "name"}
    assert set(result.keys()) == expected_keys, (
        f"models_dict() added unexpected keys: {set(result.keys()) - expected_keys}"
    )


# ---------------------------------------------------------------------------
# P-1: ModelEntry carries metadata_index dict
# ---------------------------------------------------------------------------


def test_registry_entry_carries_metadata_index_dict() -> None:
    """ModelEntry must accept and expose a metadata_index dict.

    The metadata_index field is the pre-flattened dict[str, dict[str, Any]]
    built by build_metadata_index at model-load time.  Verify that:
    - ModelEntry can be constructed with metadata_index set.
    - The field value is preserved exactly (same object identity).
    - metadata_index defaults to None when not supplied.
    - Both metadata_df and metadata_index can coexist (dual-carry design).
    """
    import pandas as pd

    metadata_index = {
        "i1": {"title": "Widget A", "category": "tools"},
        "i2": {"title": "Widget B", "category": "garden"},
    }
    df = pd.DataFrame(
        {"title": ["Widget A", "Widget B"], "category": ["tools", "garden"]},
        index=pd.Index(["i1", "i2"], name="item_id"),
    )

    entry = ModelEntry(
        name="recipe_with_index",
        recommender=MagicMock(),
        header={"best_class": "TopPop", "trained_at": "2026-01-01T00:00:00Z"},
        kid="active",
        metadata_df=df,
        metadata_index=metadata_index,
    )

    # The index is carried as-is (same object).
    assert entry.metadata_index is metadata_index, (
        "ModelEntry.metadata_index must be the same object passed at construction"
    )
    # The DataFrame is also carried alongside the index.
    assert entry.metadata_df is df, (
        "ModelEntry.metadata_df must be carried alongside metadata_index"
    )
    # Spot-check a lookup.
    assert entry.metadata_index["i1"]["title"] == "Widget A"
    assert entry.metadata_index["i2"]["category"] == "garden"


def test_registry_entry_metadata_index_defaults_to_none() -> None:
    """ModelEntry.metadata_index must default to None when not provided."""
    entry = ModelEntry(
        name="no_metadata_recipe",
        recommender=MagicMock(),
        header={},
        kid="active",
    )
    assert entry.metadata_index is None, (
        "metadata_index must default to None when not set"
    )
