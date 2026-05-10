"""ArtifactWatcher — background thread that polls recipe artifacts for changes.

Design (Watcher loop):
- Runs as a daemon thread; started once during app lifespan.
- Polls every ``watch_interval`` seconds with +-10% jitter.
- For each known recipe, stats the artifact pointer via fsspec.
- If the pointer (mtime / ETag) has changed, reads the entire artifact once
  into memory, computes sha256, HMAC-verifies, deserializes, then atomically
  replaces the registry entry.
- Concurrent stat() calls are bounded at 16 in-flight.
- Rescans the recipes directory each cycle: new YAML files are added; removed
  YAML files cause the entry to be dropped from the registry.
- On any failure: logs ERROR with the kid (never the key), marks
  ``last_load_error`` on the existing entry, and leaves the old model serving.
- Graceful stop: call ``stop()`` to request the thread to exit.

Integration assumptions:
- recotem.artifact.format.parse_header_from_bytes exists.
- recotem.artifact.signing.{verify_hmac, unpickle_payload, SafeUnpickler} exist.
- recotem.recipe.loader.load_recipe exists.
- recotem.metadata.loader.load_item_metadata exists.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fsspec
import structlog

from recotem.artifact.format import ArtifactError
from recotem.serving import metrics as _metrics
from recotem.serving.registry import ModelEntry, ModelRegistry

if TYPE_CHECKING:
    from recotem.artifact.signing import KeyRing
    from recotem.config import ServeConfig
    from recotem.recipe.models import Recipe

logger = structlog.get_logger(__name__)

_MAX_CONCURRENT_STATS = 16


# ---------------------------------------------------------------------------
# Artifact I/O helpers
# ---------------------------------------------------------------------------


def _read_artifact_bytes(path: str, max_bytes: int) -> bytes:
    """Read artifact bytes once from *path* via fsspec, resolving pointers.

    For ``versioning: append_sha`` (the documented default), ``path`` is a
    small ASCII pointer file whose contents reference the actual sha-suffixed
    artifact in the same directory.  We delegate to
    ``resolve_artifact_pointer`` so the rest of the serving layer always
    sees real artifact bytes regardless of the writer's versioning mode.

    Raises
    ------
    ArtifactError
        If the file cannot be opened or exceeds *max_bytes*.
    """
    from recotem.artifact.io import resolve_artifact_pointer

    try:
        fs, fpath = fsspec.core.url_to_fs(path)
        with fs.open(fpath, "rb") as fh:
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ArtifactError(
                f"artifact at '{path}' exceeds cap {max_bytes}; refusing load"
            )
        # If `data` is a pointer file, resolve it transparently.
        # `resolve_artifact_pointer` enforces its own size cap on the resolved
        # artifact and raises ArtifactError if the target is missing.
        resolved_data, _resolved_path = resolve_artifact_pointer(
            data, fpath, fs, max_bytes
        )
        return resolved_data
    except ArtifactError:
        raise
    except Exception as exc:
        raise ArtifactError(f"cannot read artifact '{path}': {exc}") from exc


def _stat_marker(path: str, recipe_name: str = "<unknown>") -> Any:
    """Return an opaque change-marker for *path*.

    For local filesystem: (mtime, size) tuple.
    For object stores: ETag or VersionId from fsspec info.

    Returns ``None`` when the file does not exist (``FileNotFoundError``).
    For all other errors (S3 throttle, IAM revoke, DNS failure, fsspec
    import errors) logs a structured ``artifact_stat_failed`` warning,
    increments ``recotem_artifact_stat_failures_total``, and still returns
    ``None`` so the watcher loop can continue.  Repeated failures keep
    emitting WARN — no rate-limiting at this level.
    """
    try:
        fs, fpath = fsspec.core.url_to_fs(path)
        info = fs.info(fpath)
        etag = info.get("ETag") or info.get("etag") or info.get("VersionId")
        if etag:
            return etag
        mtime = info.get("mtime") or info.get("LastModified")
        size = info.get("size") or info.get("Size") or 0
        return (mtime, size)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning(
            "artifact_stat_failed",
            recipe=recipe_name,
            error_class=type(exc).__name__,
            error=str(exc),
        )
        _metrics.inc_artifact_stat_failure(recipe_name)
        return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Per-recipe watcher state
# ---------------------------------------------------------------------------


@dataclass
class _RecipeWatchState:
    """Internal state the watcher maintains per recipe."""

    recipe: Any  # Recipe
    artifact_path: str
    last_marker: Any = None
    last_sha256: str = ""


# ---------------------------------------------------------------------------
# ArtifactWatcher
# ---------------------------------------------------------------------------


class ArtifactWatcher(threading.Thread):
    """Background thread that hot-swaps models when artifacts change.

    Parameters
    ----------
    registry:
        The shared ModelRegistry.
    recipes_dir:
        Path to the directory containing *.yaml recipe files.
    serve_config:
        Loaded ServeConfig.
    key_ring:
        The KeyRing used to verify artifacts.  None when dev_allow_unsigned.
    initial_states:
        Mapping of recipe name to _RecipeWatchState capturing the marker
        and sha256 from the initial load.
    """

    #: Number of consecutive unhandled poll-loop exceptions before the watcher
    #: marks every known recipe as unhealthy.  Configurable for testing.
    _UNHEALTHY_THRESHOLD: int = 5

    def __init__(
        self,
        registry: ModelRegistry,
        recipes_dir: Path,
        serve_config: ServeConfig,
        key_ring: KeyRing | None,
        initial_states: dict[str, _RecipeWatchState] | None = None,
        *,
        unhealthy_threshold: int = 5,
    ) -> None:
        super().__init__(name="artifact-watcher", daemon=True)
        self._registry = registry
        self._recipes_dir = recipes_dir
        self._config = serve_config
        self._key_ring = key_ring
        self._stop_event = threading.Event()
        self._states: dict[str, _RecipeWatchState] = dict(initial_states or {})
        self._consecutive_errors: int = 0
        self._unhealthy_threshold: int = unhealthy_threshold

    def stop(self) -> None:
        """Request the watcher thread to exit on its next poll tick."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Thread main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("artifact_watcher_started", interval=self._config.watch_interval)
        while not self._stop_event.is_set():
            jitter = self._config.watch_interval * 0.1 * (random.random() * 2 - 1)
            sleep_secs = max(0.1, self._config.watch_interval + jitter)
            if self._stop_event.wait(sleep_secs):
                break

            try:
                self._scan_recipes_dir()
                self._poll_artifacts()
                # Successful poll — reset consecutive-error counter.
                self._consecutive_errors = 0
            except Exception:
                self._consecutive_errors += 1
                _metrics.inc_watcher_unhandled_error()
                logger.exception(
                    "artifact_watcher_unhandled_error",
                    consecutive_errors=self._consecutive_errors,
                    threshold=self._unhealthy_threshold,
                )
                if self._consecutive_errors >= self._unhealthy_threshold:
                    self._mark_all_unhealthy()

        logger.info("artifact_watcher_stopped")

    def _mark_all_unhealthy(self) -> None:
        """Mark every known recipe as unhealthy after repeated poll failures.

        Called when ``_consecutive_errors`` reaches ``_unhealthy_threshold``.
        Sets ``last_load_error`` on every registry entry so ``/health`` and
        callers can detect that the watcher itself is unable to poll.  The
        stale models are *not* dropped — they keep serving while degraded.
        """
        logger.error(
            "artifact_watcher_unhealthy",
            consecutive_errors=self._consecutive_errors,
            threshold=self._unhealthy_threshold,
            message="watcher has failed repeatedly; marking all recipes unhealthy",
        )
        for name in list(self._states.keys()):
            self._registry.set_load_error(name, "watcher unhealthy")

    # ------------------------------------------------------------------
    # Directory rescan
    # ------------------------------------------------------------------

    def _scan_recipes_dir(self) -> None:
        """Detect added/removed YAML files and update the registry."""
        try:
            yaml_files = sorted(
                f
                for f in self._recipes_dir.iterdir()
                if f.is_file() and f.suffix == ".yaml"
            )
        except Exception as exc:
            logger.warning("recipes_dir_scan_error", error=str(exc))
            return

        current_names = set(self._states.keys())
        found_names: set[str] = set()

        for yaml_file in yaml_files:
            try:
                from recotem.recipe.loader import load_recipe

                recipe: Recipe = load_recipe(yaml_file, recipes_root=self._recipes_dir)
            except Exception as exc:
                logger.warning(
                    "recipe_rescan_load_error",
                    file=yaml_file.name,
                    error=str(exc),
                )
                continue

            found_names.add(recipe.name)

            if recipe.name not in self._states:
                logger.info("recipe_discovered", name=recipe.name)
                artifact_path = recipe.output.path
                state = _RecipeWatchState(recipe=recipe, artifact_path=artifact_path)
                self._states[recipe.name] = state
                # Insert a stub entry BEFORE attempting the load so that if
                # the load fails, _record_load_failure → set_load_error finds
                # a registered entry and the failure is visible in /health.
                stub = ModelEntry(
                    name=recipe.name,
                    recommender=None,
                    header={},
                    kid="",
                    metadata_df=None,
                    last_load_error=None,
                    artifact_path=artifact_path,
                    loaded=False,
                )
                self._registry.replace(recipe.name, stub)
                self._load_recipe(recipe.name, state, force=True)

        for gone in current_names - found_names:
            logger.info("recipe_removed", name=gone)
            self._registry.remove(gone)
            _metrics.set_model_loaded(gone, False)
            del self._states[gone]

        if found_names != current_names:
            _metrics.set_active_recipes(
                sum(1 for e in self._registry.list() if e.loaded)
            )

    # ------------------------------------------------------------------
    # Artifact polling
    # ------------------------------------------------------------------

    def _poll_artifacts(self) -> None:
        """Check all known recipes for artifact changes (bounded at 16 concurrent)."""
        names = list(self._states.keys())
        if not names:
            return

        def _check(name: str) -> tuple[str, Any]:
            state = self._states[name]
            marker = _stat_marker(state.artifact_path, recipe_name=name)
            return name, marker

        with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_STATS) as pool:
            futures = {pool.submit(_check, n): n for n in names}
            for fut in as_completed(futures):
                try:
                    name, marker = fut.result()
                except Exception as exc:
                    logger.warning("artifact_stat_error", error=str(exc))
                    continue

                state = self._states.get(name)
                if state is None:
                    continue

                if marker is None:
                    # Artifact file is missing or stat failed.  Record the
                    # failure unconditionally so /health reflects the problem.
                    # The stale model (if any) stays in memory and keeps serving
                    # — we do NOT flip loaded=False — so hot-swap resumes when
                    # the file reappears.  Only emit the log on the first
                    # transition to avoid log spam on repeated missing polls.
                    entry = self._registry.get(name)
                    if entry is not None and entry.last_load_error is None:
                        logger.warning("artifact_disappeared", name=name)
                    self._registry.set_load_error(
                        name, "artifact missing or unreadable"
                    )
                    _metrics.inc_artifact_load_failure(name)
                    continue

                if marker == state.last_marker:
                    continue

                self._load_recipe(name, state, force=False, marker=marker)

    # ------------------------------------------------------------------
    # Load / verify / replace
    # ------------------------------------------------------------------

    def _load_recipe(
        self,
        name: str,
        state: _RecipeWatchState,
        *,
        force: bool,
        marker: Any = None,
    ) -> None:
        """Read, verify, deserialize, and atomically replace the entry for *name*.

        *marker* is the change-marker that triggered this load (from the
        polling pre-stat).  Reusing it avoids a second stat() round-trip
        per cycle — important on object stores where stat is a network call.
        """
        artifact_path = state.artifact_path
        max_bytes = self._config.max_artifact_bytes

        try:
            data = _read_artifact_bytes(artifact_path, max_bytes)
        except ArtifactError as exc:
            self._record_load_failure(name, f"read failed: {exc}")
            return
        except Exception as exc:
            self._record_load_failure(name, f"unexpected read error: {exc}")
            return

        sha256 = _sha256_bytes(data)

        if not force and sha256 == state.last_sha256:
            if marker is not None:
                state.last_marker = marker
            return

        try:
            entry = self._build_entry(name, state.recipe, data, artifact_path)
        except ArtifactError as exc:
            logger.error(
                "artifact_load_failed",
                name=name,
                kid=_extract_kid_safe(data),
                error=str(exc),
            )
            self._record_load_failure(name, str(exc))
            return
        except Exception as exc:
            logger.error("artifact_load_unexpected_error", name=name, error=str(exc))
            self._record_load_failure(name, str(exc))
            return

        new_marker = marker if marker is not None else _stat_marker(artifact_path)
        entry._loaded_marker = (new_marker, sha256)
        self._registry.replace(name, entry)
        state.last_sha256 = sha256
        state.last_marker = new_marker
        _metrics.set_model_loaded(name, True)
        _metrics.record_swap(name, ok=True)
        _metrics.set_active_recipes(sum(1 for e in self._registry.list() if e.loaded))
        logger.info(
            "artifact_hot_swapped",
            name=name,
            kid=entry.kid,
            trained_at=entry.trained_at,
        )

    def _build_entry(
        self, name: str, recipe: Any, data: bytes, artifact_path: str
    ) -> ModelEntry:
        """Parse, verify, deserialize data and return a fresh ModelEntry."""
        from recotem.artifact.format import parse_header_from_bytes
        from recotem.artifact.signing import unpickle_payload, verify_hmac

        max_bytes = self._config.max_artifact_bytes
        hdr = parse_header_from_bytes(data, max_bytes)

        payload_bytes = data[hdr.payload_offset :]

        if self._key_ring is not None:
            kid_bytes = hdr.kid.encode("utf-8")
            header_json_bytes = hdr.header_data
            verify_hmac(
                self._key_ring,
                hdr.kid,
                kid_bytes,
                header_json_bytes,
                payload_bytes,
                hdr.hmac_digest,
            )
        else:
            logger.warning(
                "artifact_hmac_skipped_dev_allow_unsigned",
                name=name,
                kid=hdr.kid,
            )

        header_dict: dict[str, Any] = json.loads(hdr.header_data.decode("utf-8"))
        recommender = unpickle_payload(payload_bytes)

        metadata_df = None
        if recipe.item_metadata is not None:
            metadata_df = _load_metadata(recipe, name)

        return ModelEntry(
            name=name,
            recommender=recommender,
            header=header_dict,
            kid=hdr.kid,
            metadata_df=metadata_df,
            last_load_error=None,
            artifact_path=artifact_path,
        )

    def _mark_error(self, name: str, error: str) -> None:
        """Mark last_load_error on the existing registry entry (if any).

        Goes through ``ModelRegistry.set_load_error`` so the mutation is
        serialised under the registry's lock — readers calling
        ``health_snapshot()`` see a consistent (loaded, error) pair.
        """
        self._registry.set_load_error(name, error)

    def _record_load_failure(self, name: str, error: str) -> None:
        """Mark the entry's load error and increment the failure metrics."""
        self._mark_error(name, error)
        _metrics.inc_artifact_load_failure(name)
        _metrics.record_swap(name, ok=False)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _extract_kid_safe(data: bytes) -> str:
    """Best-effort extraction of kid from raw artifact bytes.

    Catches only structural / encoding errors (``IndexError``,
    ``UnicodeDecodeError``, ``ValueError``) that indicate a corrupt or
    truncated artifact.  Programming bugs (``AttributeError``,
    ``ImportError``, etc.) are allowed to propagate so the caller's existing
    ``artifact_load_unexpected_error`` handler can surface them.
    """
    from recotem.artifact.format import FIXED_PREFIX_SIZE, MAX_KID_LEN

    try:
        if len(data) < FIXED_PREFIX_SIZE:
            return "<unknown>"
        kid_len = data[FIXED_PREFIX_SIZE - 1]
        if kid_len < 1 or kid_len > MAX_KID_LEN:
            return "<unknown>"
        if len(data) < FIXED_PREFIX_SIZE + kid_len:
            return "<unknown>"
        return data[FIXED_PREFIX_SIZE : FIXED_PREFIX_SIZE + kid_len].decode(
            "utf-8", errors="replace"
        )
    except (IndexError, UnicodeDecodeError, ValueError):
        return "<unknown>"


def _load_metadata(recipe: Any, recipe_name: str) -> Any:
    """Load item metadata; raises on any failure so the caller can mark the
    model as not-loaded.

    Silent fallback to ``None`` is intentionally avoided: a recipe that
    declares ``item_metadata`` but cannot load it is misconfigured, and
    masking the failure as ``loaded=True`` with no metadata leaves
    ``/health`` blind to the problem.
    """
    from recotem.metadata.loader import load_item_metadata

    return load_item_metadata(
        recipe.item_metadata,
        recipe.item_metadata.fields,
        on_field_missing=recipe.item_metadata.on_field_missing,
    )


# ---------------------------------------------------------------------------
# Factory helper used by app.py
# ---------------------------------------------------------------------------


def build_initial_states(
    recipes: list[Any],
    loaded_entries: dict[str, ModelEntry],
) -> dict[str, _RecipeWatchState]:
    """Build initial watcher states from entries already loaded at startup.

    Captures the current marker and sha256 so the watcher does not
    unnecessarily re-load artifacts that are already in the registry.

    Parameters
    ----------
    recipes:
        All recipes loaded from the recipes directory.
    loaded_entries:
        Mapping of recipe name to ModelEntry for recipes successfully loaded.
    """
    states: dict[str, _RecipeWatchState] = {}
    for recipe in recipes:
        artifact_path = recipe.output.path
        state = _RecipeWatchState(recipe=recipe, artifact_path=artifact_path)
        if recipe.name in loaded_entries:
            marker = _stat_marker(artifact_path, recipe_name=recipe.name)
            entry = loaded_entries[recipe.name]
            state.last_marker = marker
            loaded_marker = entry._loaded_marker
            state.last_sha256 = loaded_marker[1] if loaded_marker[1] else ""
        states[recipe.name] = state
    return states
