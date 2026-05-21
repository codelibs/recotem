"""ModelRegistry and ModelEntry for the Recotem serving layer.

The registry is a ``dict[str, ModelEntry]`` guarded by a ``threading.Lock``.
All public methods are synchronous (no asyncio) — FastAPI route handlers run
in a threadpool so they can acquire the lock without blocking the event loop.

A plain ``threading.Lock`` (non-reentrant) is sufficient because no public
method calls another public method while holding the lock.  Every method
acquires ``self._lock`` exactly once on entry and releases it on exit.

Atomic replace strategy
-----------------------
``replace(name, entry)`` acquires the lock, drops the old reference, and
inserts the new one.  Python's GIL plus the Lock means in-flight request
threads that already hold the old ``ModelEntry`` reference continue safely
until they finish — there is no shared mutable state between the old and new
entries.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

# ---------------------------------------------------------------------------
# ModelEntry
# ---------------------------------------------------------------------------


@dataclass
class ModelEntry:
    """A single loaded model and its associated metadata.

    Attributes
    ----------
    name:
        Recipe name (matches the path parameter in ``/v1/recipes/{name}:*`` endpoints).
    recommender:
        The deserialized ``IDMappedRecommender`` instance.
    header:
        Parsed artifact header dict (from header JSON; already ``json.loads``-ed).
    kid:
        Key-id from the artifact header.
    metadata_df:
        Optional pandas DataFrame of item metadata, indexed by item_id string.
        ``None`` if no item_metadata is configured for this recipe.
        Retained alongside ``metadata_index`` to allow debug introspection
        and to support future analytics without re-loading the file.
        Memory cost is less than 2× because ``metadata_index`` stores the
        same string/scalar data that the DataFrame already holds in object
        columns — no large numeric arrays are duplicated.
    metadata_index:
        Pre-flattened ``dict[str, dict[str, Any]]`` keyed by item_id for
        O(1) per-item lookups during ``:recommend`` / ``:recommend-related``
        response metadata join.  Built once at model-load
        time by :func:`~recotem.metadata.loader.build_metadata_index` with
        NaN→None normalisation and deny-list filtering already applied.
        ``None`` when no item_metadata is configured for this recipe.
    last_load_error:
        If the most recent load attempt failed, this holds the error string.
        A non-None value here means the entry is *stale* (it was loaded on a
        previous attempt and the watcher failed to replace it with a fresh one).
    artifact_path:
        The filesystem or object-store path last successfully loaded.
    loaded:
        ``True`` when ``recommender`` is a usable model.  ``False`` for stub
        entries inserted at startup when the artifact failed to load — these
        entries appear in ``/health`` as ``loaded=false`` so operators can see
        which recipes are not serving, and the v1 inference endpoints
        (``:recommend``, ``:recommend-related``, ``:batch-recommend``,
        ``:batch-recommend-related``) should reject them with 503.
    """

    name: str
    recommender: Any  # IDMappedRecommender | None when loaded=False
    header: dict[str, Any]
    kid: str
    metadata_df: Any | None = None  # pd.DataFrame | None
    metadata_index: dict[str, Any] | None = None  # dict[str, dict[str, Any]] | None
    last_load_error: str | None = None
    artifact_path: str = ""
    loaded: bool = True
    # Internal watcher state: (mtime_or_etag, sha256_hex)
    _loaded_marker: tuple[Any, str] = field(default_factory=lambda: (None, ""))
    # v1 additions. The watcher sets loaded_at_unix on every successful
    # (re-)load.  Stays at 0.0 for stub entries that never loaded.
    loaded_at_unix: float = 0.0
    # Optional artifact-derived metadata used by /v1/recipes/{name}.
    config_digest: str = ""
    algorithms: list[str] = field(default_factory=list)

    # --- v1 API additions ---
    @property
    def artifact_sha256(self) -> str:
        """SHA-256 of the artifact bytes (hex, no prefix).

        Derived from ``_loaded_marker[1]`` which the watcher populates
        at every successful (re-)load.  Empty for stub entries.
        """
        return self._loaded_marker[1] if self._loaded_marker else ""

    @property
    def model_version(self) -> str:
        """Deterministic artifact identifier exposed via the v1 API.

        Format: ``sha256:<hex>``.  Stub entries return ``sha256:``.
        """
        return f"sha256:{self.artifact_sha256}"

    @property
    def loaded_at(self) -> str:
        """ISO-8601 UTC timestamp of the last successful (re-)load.

        Falls back to the unix epoch for stub entries.
        """
        from datetime import datetime

        return datetime.fromtimestamp(self.loaded_at_unix or 0.0, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    @property
    def kind(self) -> str:
        """Inference kind exposed via /v1/recipes.

        Currently every irspack algorithm shipped by recotem is a
        user-item collaborative filter, so this returns "user-item"
        unconditionally.
        """
        return "user-item"

    @property
    def supported_verbs(self) -> list[str]:
        """List of v1 verbs this entry can serve."""
        if self.kind == "user-item":
            return [
                "recommend",
                "recommend-related",
                "batch-recommend",
                "batch-recommend-related",
            ]
        return []

    @property
    def trained_at(self) -> str | None:
        """ISO-8601 timestamp from the header, or None."""
        return self.header.get("trained_at")

    @property
    def best_class(self) -> str | None:
        """Algorithm class name from the header, or None."""
        return self.header.get("best_class")

    def health_dict(self) -> dict[str, Any]:
        """Summarise entry state for the ``/health`` endpoint."""
        d: dict[str, Any] = {"loaded": self.loaded}
        if self.trained_at:
            d["trained_at"] = self.trained_at
        if self.best_class:
            d["best_class"] = self.best_class
        if self.kid:
            d["kid"] = self.kid
        if self.last_load_error:
            d["error"] = self.last_load_error
        return d

    def __post_init__(self) -> None:
        """Pre-build the immutable models view so ``models_dict()`` is O(1)."""
        # Build once at construction time.  The header and kid are set before
        # this runs and are never mutated after construction.
        self._models_view: dict[str, Any] = dict(self.header)
        self._models_view["kid"] = self.kid
        self._models_view["name"] = self.name

    def models_dict(self) -> dict[str, Any]:
        """Return header metadata for introspection (e.g. tests, tooling).

        The artifact header JSON never contains ``hmac`` or ``key`` fields —
        those are stored in separate binary regions of the artifact format
        (see ``artifact/format.py``).  All header fields are safe to expose.

        Returns the pre-built immutable view; callers must not mutate it.
        """
        return self._models_view


# ---------------------------------------------------------------------------
# ModelRegistry
# ---------------------------------------------------------------------------


class ModelRegistry:
    """Thread-safe name → ModelEntry registry.

    Methods
    -------
    get(name) → ModelEntry | None
    list() → list[ModelEntry]
    replace(name, entry)
        Atomically replace (or insert) the entry for *name*.
    remove(name)
        Remove the entry for *name* if present.
    health_snapshot() → dict[str, Any]
        Return a shallow copy of the health state of all entries.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ModelEntry] = {}
        # Plain Lock is sufficient: no public method calls another public
        # method while holding the lock (no reentrant code paths).
        self._lock = threading.Lock()
        # Maintained atomically inside the lock on every mutation that changes
        # entry.loaded; enables O(1) loaded_count() without an O(N) walk.
        self._loaded_count: int = 0

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def get(self, name: str) -> ModelEntry | None:
        """Return the entry for *name*, or ``None`` if not present."""
        with self._lock:
            return self._entries.get(name)

    def list(self) -> list[ModelEntry]:
        """Return a snapshot list of all current entries."""
        with self._lock:
            return list(self._entries.values())

    def replace(self, name: str, entry: ModelEntry) -> None:
        """Atomically replace (or insert) the entry for *name*.

        The previous entry — if any — is dereferenced; its memory is
        reclaimed once all in-flight request threads drop their references.
        ``_loaded_count`` is updated under the same lock.
        """
        with self._lock:
            old = self._entries.get(name)
            old_loaded = old.loaded if old is not None else False
            self._entries[name] = entry
            # Adjust counter by the net change in loaded-ness.
            if entry.loaded and not old_loaded:
                self._loaded_count += 1
            elif not entry.loaded and old_loaded:
                self._loaded_count -= 1

    def replace_with_marker(
        self, name: str, entry: ModelEntry, marker: tuple[Any, str]
    ) -> None:
        """Atomically replace entry AND set its ``_loaded_marker`` in one lock.

        Compared to calling ``replace()`` followed by ``update_loaded_marker()``,
        this method holds the lock across both mutations so readers iterating
        ``list()`` between the two ops can never observe a fresh recommender
        with a stale ``_loaded_marker``.  ``_loaded_count`` is kept consistent
        under the same lock.
        """
        with self._lock:
            old = self._entries.get(name)
            old_loaded = old.loaded if old is not None else False
            entry._loaded_marker = marker
            self._entries[name] = entry
            if entry.loaded and not old_loaded:
                self._loaded_count += 1
            elif not entry.loaded and old_loaded:
                self._loaded_count -= 1

    def remove(self, name: str) -> None:
        """Remove the entry for *name*.  No-op if not present.

        Decrements ``_loaded_count`` when a loaded entry is removed.
        """
        with self._lock:
            old = self._entries.pop(name, None)
            if old is not None and old.loaded:
                self._loaded_count -= 1

    def set_load_error(self, name: str, error: str | None) -> bool:
        """Record the latest artifact-load failure (if any) on the entry.

        Holds the registry lock so the watcher's failure annotation never
        races with ``replace()`` / readers iterating ``list()``.  Returns
        True when the entry exists, False when there is nothing to mark
        (caller can decide whether that is worth logging).

        Note: ``set_load_error`` does NOT change ``entry.loaded``; it only
        annotates a stale-but-loaded entry with an error string.  Therefore
        ``_loaded_count`` is not adjusted here.
        """
        with self._lock:
            entry = self._entries.get(name)
            if entry is None:
                return False
            entry.last_load_error = error
            return True

    def update_loaded_marker(self, name: str, marker: tuple[Any, str]) -> bool:
        """Update the watcher's internal ``_loaded_marker`` on the entry.

        Same locking contract as :meth:`set_load_error` — keeps watcher
        state mutations on a registered entry inside the registry lock so
        contracts hold even if a future change moves to a finer-grained
        lock.
        """
        with self._lock:
            entry = self._entries.get(name)
            if entry is None:
                return False
            entry._loaded_marker = marker
            return True

    # ------------------------------------------------------------------
    # Health / observability helpers
    # ------------------------------------------------------------------

    def loaded_count(self) -> int:
        """Return the number of currently loaded (``loaded=True``) entries.

        O(1) — maintained atomically inside the lock by every mutation that
        changes the ``loaded`` boolean.  Safe to call from multiple threads.
        """
        with self._lock:
            return self._loaded_count

    def health_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return per-recipe health info (safe copy, no model objects).

        The snapshot is taken in two phases to minimise lock hold time:

        1. Under the lock: copy ``(name, entry)`` pairs into a local list.
           This is O(N) in item count but performs no per-entry work.
        2. Outside the lock: call ``entry.health_dict()`` for each entry.

        ``health_dict()`` reads only immutable fields (``loaded``,
        ``trained_at``, ``best_class``, ``kid``) plus ``last_load_error``
        which is a single reference assignment — bytecode-atomic on CPython.
        A concurrent ``set_load_error`` between steps 1 and 2 may cause the
        snapshot to reflect either the old or the new error string, but never
        a partially-written value.  This is a deliberate trade-off: ``/health``
        is a monitoring endpoint, not a consistency primitive.
        """
        with self._lock:
            items = list(self._entries.items())
        # Build the dict outside the lock so /health cannot block `:recommend`
        # threads waiting to acquire the lock.
        return {name: entry.health_dict() for name, entry in items}

    def names(self) -> list[str]:
        """Return a sorted list of currently registered recipe names."""
        with self._lock:
            return sorted(self._entries.keys())
