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

from recotem._log_safe import format_kid_for_log as _format_kid_for_log
from recotem._metrics_watcher import inc_recipes_dir_scan_failure as _inc_scan_failure
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
    marker, _err = _stat_marker_with_error(path, recipe_name=recipe_name)
    return marker


def _stat_marker_with_error(
    path: str, recipe_name: str = "<unknown>"
) -> tuple[Any, str | None]:
    """Return ``(marker, error_class)`` for *path*.

    Identical to :func:`_stat_marker` but also returns the exception class
    name when a non-FileNotFoundError occurs, so callers can surface a more
    descriptive error in ``/health`` (distinguishing missing-file from
    network/IAM failure — M-9).

    Returns:
        ``(marker, None)``   — success; marker is the opaque change token.
        ``(None, None)``     — file not found (normal, no error to surface).
        ``(None, cls_name)`` — unexpected error; cls_name is the exc class.
    """
    try:
        fs, fpath = fsspec.core.url_to_fs(path)
        info = fs.info(fpath)
        etag = info.get("ETag") or info.get("etag") or info.get("VersionId")
        if etag:
            return etag, None
        mtime = info.get("mtime") or info.get("LastModified")
        size = info.get("size") or info.get("Size") or 0
        return (mtime, size), None
    except FileNotFoundError:
        return None, None
    except Exception as exc:
        error_class = type(exc).__name__
        logger.warning(
            "artifact_stat_failed",
            recipe=recipe_name,
            error_class=error_class,
            error=str(exc),
        )
        _metrics.inc_artifact_stat_failure(recipe_name)
        return None, error_class


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
    #: Last-known contents of the ``.sha256`` sidecar pointer file.
    #: ``None`` means we have not yet read the sidecar (or it does not exist).
    last_sidecar_contents: str | None = None
    #: Most recent stat-error class name, or ``None`` when the last stat
    #: succeeded.  Used by OBS-1 to demote repeated identical errors from
    #: WARNING to DEBUG so log aggregation is not flooded during an outage.
    _last_stat_error_class: str | None = None


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
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=_MAX_CONCURRENT_STATS,
            thread_name_prefix="artifact-stat",
        )
        # Maps each recipe YAML path to the recipe.name parsed from it.
        # recipe.name is authoritative and may differ from the file stem when
        # the YAML declares an explicit ``name:`` field.  Initialized empty;
        # populated by _scan_recipes_dir as YAML files are successfully parsed
        # (including pre-existing recipes from initial_states on the first
        # post-startup scan tick).  Until the first successful parse for a
        # given path, the parse-error fallback in _scan_recipes_dir falls back
        # to yaml_file.stem as a best-effort recipe name.
        self._yaml_path_to_name: dict[Path, str] = {}

    def stop(self) -> None:
        """Signal the watcher thread to exit on its next poll tick.

        The executor is intentionally NOT shut down here: an in-flight poll
        may still be submitting work.  The run() loop owns the executor and
        shuts it down in its finally block once the loop exits.  Callers
        should follow up with ``join(timeout=...)`` to wait for clean exit.
        """
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Thread main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("artifact_watcher_started", interval=self._config.watch_interval)
        try:
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
                except (MemoryError, RecursionError):
                    raise  # Daemon thread dying is preferable to silent OOM loops
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
        finally:
            self._executor.shutdown(wait=True)
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
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            logger.warning(
                "recipes_dir_scan_error",
                error=str(exc),
                error_class=type(exc).__name__,
            )
            # Surface to ops via the neutral scan-failure counter.
            _inc_scan_failure(f"dir_iter_{type(exc).__name__}")
            return

        current_names = set(self._states.keys())
        found_names: set[str] = set()

        for yaml_file in yaml_files:
            try:
                from recotem.recipe.loader import load_recipe

                recipe: Recipe = load_recipe(yaml_file, recipes_root=self._recipes_dir)
            except Exception as exc:
                # YAML parse/load failed on rescan.  Distinguish "transient
                # error on an existing recipe" from "brand-new YAML that has
                # never been loaded" to uphold the availability contract:
                # a transient parse error must NOT evict the currently-loaded
                # model (M-2).
                #
                # Strategy: use _yaml_path_to_name to look up the recipe name
                # that was previously parsed from this exact file (M-6).
                # Falling back to yaml_file.stem is retained for YAML files
                # that have never been successfully parsed (first-ever load
                # error), where the stem is the only available candidate.
                error_class = type(exc).__name__
                existing_name = self._yaml_path_to_name.get(yaml_file)
                if existing_name is None:
                    # Never successfully parsed before; use stem as fallback.
                    existing_name = (
                        yaml_file.stem if yaml_file.stem in current_names else None
                    )
                if existing_name is not None and existing_name in current_names:
                    # Keep the entry in found_names → it won't be deleted.
                    found_names.add(existing_name)
                    self._registry.set_load_error(
                        existing_name,
                        f"recipe YAML parse error on rescan: {exc}",
                    )
                    _metrics.inc_recipe_rescan_error(existing_name)
                    logger.warning(
                        "recipe_rescan_load_error",
                        file=yaml_file.name,
                        recipe=existing_name,
                        error=str(exc),
                    )
                else:
                    logger.warning(
                        "recipe_rescan_load_error",
                        file=yaml_file.name,
                        error=str(exc),
                    )
                # Always increment the neutral scan-failure counter regardless
                # of whether the recipe was previously registered.  This gives
                # operators a reliable signal that per-recipe load errors are
                # occurring, even for brand-new YAML files that have not yet
                # entered the registry (M-6).
                _inc_scan_failure(error_class)
                continue

            found_names.add(recipe.name)

            # Always keep the path→name map current so that a later parse
            # failure on this file uses the authoritative name rather than
            # the file stem (M-6).  This is a cheap dict update on each scan.
            self._yaml_path_to_name[yaml_file] = recipe.name

            if recipe.name not in self._states:
                logger.info("recipe_discovered", name=recipe.name)
                artifact_path = recipe.output.path
                state = _RecipeWatchState(recipe=recipe, artifact_path=artifact_path)
                self._states[recipe.name] = state
                # Track the yaml_path → recipe.name mapping so future rescan
                # error handling is not forced to rely on file stem (M-6).
                self._yaml_path_to_name[yaml_file] = recipe.name
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
                # Do NOT call _load_recipe here (M-1): loading is a blocking
                # network I/O + deserialization operation that stalls the
                # watcher loop for slow object-store artifacts.  The stub has
                # last_marker=None so the next _poll_artifacts tick detects
                # marker-change and loads via the normal bounded-thread-pool
                # path.
                # next poll tick picks up new recipes via the marker-change
                # path (was synchronous; deadlock-prone for slow
                # object-store loads)

        for gone in current_names - found_names:
            logger.info("recipe_removed", name=gone)
            self._registry.remove(gone)
            _metrics.set_model_loaded(gone, False)
            del self._states[gone]
            # Clean up the yaml_path → name mapping for removed recipes (M-6).
            stale_paths = [p for p, n in self._yaml_path_to_name.items() if n == gone]
            for p in stale_paths:
                del self._yaml_path_to_name[p]

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

        def _check(name: str) -> tuple[str, Any, str | None]:
            state = self._states[name]
            marker, error_class = _stat_marker_with_error(
                state.artifact_path, recipe_name=name
            )
            return name, marker, error_class

        futures = {self._executor.submit(_check, n): n for n in names}
        for fut in as_completed(futures):
            try:
                name, marker, stat_error_class = fut.result()
            except Exception as exc:
                _failed_recipe = futures[fut]
                logger.warning(
                    "artifact_stat_error",
                    recipe_name=_failed_recipe,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
                _metrics.inc_artifact_stat_failure(_failed_recipe)
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
                #
                # Distinguish a genuinely missing file from a network/IAM
                # failure (M-9): when stat_error_class is set, the error
                # message includes the class name so /health can surface
                # "stat failed: ClientError(403)" vs "artifact missing".
                #
                # OBS-1: the first occurrence of a given error class emits
                # WARNING; subsequent identical errors are demoted to DEBUG
                # to avoid flooding log aggregation during sustained outages.
                entry = self._registry.get(name)
                if stat_error_class is not None:
                    error_msg = f"stat failed: {stat_error_class}"
                    if state._last_stat_error_class == stat_error_class:
                        logger.debug(
                            "artifact_stat_failed_repeated",
                            recipe=name,
                            error_class=stat_error_class,
                        )
                    else:
                        # First occurrence (or changed error class) — already
                        # logged at WARNING inside _stat_marker_with_error;
                        # update state so the next occurrence is demoted.
                        state._last_stat_error_class = stat_error_class
                else:
                    # stat_error_class is None → file genuinely missing.
                    # Reset the error-class tracker (file was accessible before).
                    state._last_stat_error_class = None
                    error_msg = "artifact missing or unreadable"
                    if entry is not None and entry.last_load_error is None:
                        logger.warning("artifact_disappeared", name=name)
                self._registry.set_load_error(name, error_msg)
                _metrics.inc_artifact_load_failure(name)
                continue

            # Successful stat — clear the error-class tracker (OBS-1).
            state._last_stat_error_class = None

            if marker == state.last_marker:
                # Fast path: pointer/mtime unchanged.  For append_sha
                # artifacts we additionally check the cheap ``.sha256``
                # sidecar pointer file so the watcher can skip the full
                # artifact stat on the *resolved* target when neither the
                # pointer file nor the sidecar have changed (P-4).
                #
                # Always record the most-recently seen marker so that on
                # object stores with unstable ETags, a successful stat that
                # compares equal is still acknowledged.  Without this, a
                # watcher that receives a transiently different ETag on one
                # poll and then returns to the original ETag on the next
                # poll would re-trigger a full reload unnecessarily.
                state.last_marker = marker
                sidecar_changed = _check_sidecar_changed(state)
                if not sidecar_changed:
                    continue
                # Sidecar changed — fall through to full reload below.
                # If the full read subsequently fails, record it so the
                # operator can see the sidecar-stale condition in metrics.
                try:
                    self._load_recipe(name, state, force=False, marker=marker)
                except Exception:
                    _inc_scan_failure("sidecar_stale")
                    raise
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
            logger.error(
                "artifact_read_failed",
                name=name,
                path=str(artifact_path),
                error=str(exc),
                exc_type=type(exc).__name__,
            )
            self._record_load_failure(name, f"read failed: {exc}")
            return
        except Exception as exc:
            logger.error(
                "artifact_read_failed",
                name=name,
                path=str(artifact_path),
                error=str(exc),
                exc_type=type(exc).__name__,
            )
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
        except (MemoryError, RecursionError):
            # Never swallow OOM / stack-exhaustion in a long-running thread:
            # silently retrying every poll cycle drives the process to the
            # OOM killer with no observable symptom.  Re-raise so the
            # outer poll loop's exception handler logs and counts it.
            raise
        except Exception as exc:
            logger.exception(
                "artifact_load_unexpected_error",
                name=name,
                exc_type=type(exc).__name__,
                error=str(exc),
            )
            self._record_load_failure(name, f"{type(exc).__name__}: {exc}")
            return

        new_marker = (
            marker
            if marker is not None
            else _stat_marker(artifact_path, recipe_name=name)
        )
        # Use replace_with_marker to atomically insert the new entry AND set
        # its _loaded_marker in a single lock acquisition.  A two-step
        # replace() + update_loaded_marker() would allow readers iterating
        # list() between the two ops to see a fresh recommender with a stale
        # _loaded_marker (TOCTOU window).
        self._registry.replace_with_marker(name, entry, (new_marker, sha256))
        state.last_sha256 = sha256
        state.last_marker = new_marker
        _metrics.set_model_loaded(name, True)
        _metrics.record_swap(name, ok=True)
        _metrics.set_active_recipes(sum(1 for e in self._registry.list() if e.loaded))
        logger.info(
            "artifact_hot_swapped",
            name=name,
            kid=_format_kid_for_log(entry.kid),
            trained_at=entry.trained_at,
        )

    def _build_entry(
        self, name: str, recipe: Any, data: bytes, artifact_path: str
    ) -> ModelEntry:
        """Parse, verify, deserialize data and return a fresh ModelEntry."""
        from recotem.artifact.format import parse_header_from_bytes
        from recotem.artifact.signing import unpickle_payload, verify_hmac

        # Use the payload-specific cap for parse_header_from_bytes so
        # serve-side deserialization is bounded by max_payload_bytes (not
        # max_artifact_bytes). This separates the outer container size cap from
        # the deserialization cap.
        max_payload_bytes = self._config.max_payload_bytes
        hdr = parse_header_from_bytes(data, max_payload_bytes)

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
                kid=_format_kid_for_log(hdr.kid),
            )

        # Decode header JSON BEFORE deserializing the payload so a corrupt
        # header surfaces as a clear header-decode error rather than as a
        # downstream "ran out of input" on an empty/truncated payload.
        try:
            header_dict: dict[str, Any] = json.loads(hdr.header_data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ArtifactError(f"header JSON decode failed: {exc}") from exc

        recommender = unpickle_payload(payload_bytes)

        metadata_df = None
        metadata_index = None
        if recipe.item_metadata is not None:
            metadata_df = _load_metadata(recipe, name)
            from recotem.metadata.loader import build_metadata_index

            deny_set: frozenset[str] = frozenset(
                s.lower() for s in (self._config.metadata_field_deny or [])
            )
            metadata_index = build_metadata_index(metadata_df, deny_set)

        return ModelEntry(
            name=name,
            recommender=recommender,
            header=header_dict,
            kid=hdr.kid,
            metadata_df=metadata_df,
            metadata_index=metadata_index,
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

# _format_kid_for_log is imported from recotem._log_safe at the top of this
# module (OBS-3) so that both artifact/signing.py and serving/watcher.py use
# the same sanitisation logic without either sub-package importing the other.
# The _KID_LOG_MAX_LEN constant is kept here as a local alias for any tests
# or internal code that reference it directly.
_KID_LOG_MAX_LEN: int = 64


def _extract_kid_safe(data: bytes) -> str:
    """Best-effort extraction of kid from raw artifact bytes.

    Catches only structural / encoding errors (``IndexError``,
    ``UnicodeDecodeError``, ``ValueError``) that indicate a corrupt or
    truncated artifact.  Programming bugs (``AttributeError``,
    ``ImportError``, etc.) are allowed to propagate so the caller's existing
    ``artifact_load_unexpected_error`` handler can surface them.

    The returned string is already sanitized via ``_format_kid_for_log``
    so it is safe to embed in structured log fields without further processing
    (length-capped, non-printables rendered as ``\\xHH`` escapes).
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
        raw_kid = data[FIXED_PREFIX_SIZE : FIXED_PREFIX_SIZE + kid_len]
        return _format_kid_for_log(raw_kid)
    except (IndexError, UnicodeDecodeError, ValueError):
        return "<unknown>"


def _check_sidecar_changed(state: _RecipeWatchState) -> bool:
    """Return ``True`` when the ``.sha256`` sidecar pointer has changed.

    For ``versioning: append_sha`` artifacts the recipe's ``output.path`` is
    a small ASCII pointer file.  A ``.sha256`` sidecar (``<output_path>.sha256``)
    may be written alongside it by external tooling that manages artifact
    versions; the sidecar's contents summarise which sha-suffixed artifact is
    current.  When the sidecar exists and its contents are unchanged, the
    watcher can skip the full artifact stat/read entirely (P-4 pointer-only
    poll optimisation).

    Behaviour
    ---------
    - If no ``.sha256`` sidecar exists next to the artifact path, returns
      ``False`` (caller falls back to full-stat comparison).
    - If the sidecar exists and its contents are identical to
      ``state.last_sidecar_contents``, returns ``False`` (skip full read).
    - If the sidecar exists and its contents differ (or have not yet been
      read), updates ``state.last_sidecar_contents`` and returns ``True``
      (proceed with full reload).
    - On any I/O error reading the sidecar, returns ``False`` (conservative:
      let the full-stat path decide).

    Parameters
    ----------
    state:
        The per-recipe watcher state.  ``last_sidecar_contents`` is updated
        in-place when the sidecar changes.

    Returns
    -------
    bool
        ``True`` if the sidecar changed and a full reload should be triggered.
        ``False`` if unchanged or absent (let the outer marker comparison decide).
    """
    artifact_path = state.artifact_path
    # Only meaningful for local-FS paths where we can form a sibling sidecar.
    # For remote URIs (s3://, gs://) this is a no-op; the marker comparison
    # (ETag / mtime) is already cheap enough.
    try:
        sidecar_path = Path(artifact_path + ".sha256")
    except TypeError:
        return False

    if not sidecar_path.exists():
        return False

    try:
        sidecar_contents = sidecar_path.read_text(encoding="utf-8")
    except OSError:
        # Can't read the sidecar — be conservative and let the full stat run.
        return False

    if sidecar_contents == state.last_sidecar_contents:
        # Sidecar unchanged — the artifact itself has not changed.
        logger.debug(
            "pointer_unchanged_skip_read",
            recipe=state.recipe.name if hasattr(state.recipe, "name") else "<unknown>",
            sidecar=str(sidecar_path),
        )
        return False

    # Sidecar changed — signal to the caller that a reload is needed.
    state.last_sidecar_contents = sidecar_contents
    return True


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


# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------
#
# The functions below are intentionally referenced by ``serving/app.py`` for
# the startup load path.  Expose them under public names so the module
# boundary is no longer a leading-underscore handshake — leaving them
# private was a soft contract that broke quietly when the watcher internals
# moved during refactors.  The original ``_``-prefixed names remain for
# legacy callers (existing unit tests bind to them directly).

read_artifact_bytes = _read_artifact_bytes
stat_marker = _stat_marker
sha256_bytes = _sha256_bytes
load_metadata = _load_metadata


__all__ = [
    "ArtifactWatcher",
    "build_initial_states",
    "read_artifact_bytes",
    "stat_marker",
    "sha256_bytes",
    "load_metadata",
]


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
