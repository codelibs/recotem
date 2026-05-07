"""ModelRegistry and ModelEntry for the Recotem serving layer.

The registry is a ``dict[str, ModelEntry]`` guarded by a ``threading.RLock``.
All public methods are synchronous (no asyncio) — FastAPI route handlers run
in a threadpool so they can acquire the lock without blocking the event loop.

Atomic replace strategy
-----------------------
``replace(name, entry)`` acquires the lock, drops the old reference, and
inserts the new one.  Python's GIL plus the RLock means in-flight request
threads that already hold the old ``ModelEntry`` reference continue safely
until they finish — there is no shared mutable state between the old and new
entries.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
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
        Recipe name (matches ``/predict/{name}``).
    recommender:
        The deserialized ``IDMappedRecommender`` instance.
    header:
        Parsed artifact header dict (from header JSON; already ``json.loads``-ed).
    kid:
        Key-id from the artifact header.
    metadata_df:
        Optional pandas DataFrame of item metadata, indexed by item_id string.
        ``None`` if no item_metadata is configured for this recipe.
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
        which recipes are not serving, and ``/predict`` should reject them
        with 503.
    """

    name: str
    recommender: Any  # IDMappedRecommender | None when loaded=False
    header: dict[str, Any]
    kid: str
    metadata_df: Any | None = None  # pd.DataFrame | None
    last_load_error: str | None = None
    artifact_path: str = ""
    loaded: bool = True
    # Internal watcher state: (mtime_or_etag, sha256_hex)
    _loaded_marker: tuple[Any, str] = field(default_factory=lambda: (None, ""))

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

    def models_dict(self) -> dict[str, Any]:
        """Return header metadata suitable for the ``/models`` endpoint.

        Key material is never included.
        """
        safe = {k: v for k, v in self.header.items() if k not in ("hmac", "key")}
        safe["kid"] = self.kid
        safe["name"] = self.name
        return safe


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
        self._lock = threading.RLock()

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
        """
        with self._lock:
            self._entries[name] = entry

    def remove(self, name: str) -> None:
        """Remove the entry for *name*.  No-op if not present."""
        with self._lock:
            self._entries.pop(name, None)

    # ------------------------------------------------------------------
    # Health / observability helpers
    # ------------------------------------------------------------------

    def health_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return per-recipe health info (safe copy, no model objects)."""
        with self._lock:
            return {name: entry.health_dict() for name, entry in self._entries.items()}

    def names(self) -> list[str]:
        """Return a sorted list of currently registered recipe names."""
        with self._lock:
            return sorted(self._entries.keys())
