"""Progress reporting for Optuna trials.

Auto-detects the terminal environment and renders accordingly:
- TTY (sys.stderr.isatty()) + not --quiet: rich progress bar.
- Non-TTY or --quiet: one structlog event per trial.
- --verbose: adds full param dump per trial.
- --quiet: suppresses per-trial lines (final summary still emitted).
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ProgressReporter:
    """Manage progress output during an Optuna search.

    Lifecycle::

        with ProgressReporter(n_trials, recipe_name, ...) as reporter:
            reporter.on_trial_done(trial_number, algorithm, score, params)
        # summary emitted on __exit__

    Parameters
    ----------
    n_trials:
        Total planned trial budget (for progress bar denominator).
    recipe_name:
        Recipe name; carried in all log events.
    run_id:
        Opaque run identifier; carried in all log events.
    quiet:
        Suppress per-trial lines.  Final summary is always emitted.
    verbose:
        Emit full parameter dict per trial (only meaningful in non-TTY mode;
        rich progress shows params anyway).
    force_log:
        If ``True``, always use structured log output even on a TTY.
        Useful for testing.
    """

    def __init__(
        self,
        n_trials: int,
        recipe_name: str,
        run_id: str,
        *,
        quiet: bool = False,
        verbose: bool = False,
        force_log: bool = False,
    ) -> None:
        self._n_trials = n_trials
        self._recipe_name = recipe_name
        self._run_id = run_id
        self._quiet = quiet
        self._verbose = verbose
        self._use_rich = (
            not force_log and not quiet and sys.stderr.isatty() and _rich_available()
        )
        self._lock = threading.Lock()
        self._completed = 0
        self._best_score: float | None = None
        self._best_algorithm: str = ""
        self._progress: Any = None  # rich.progress.Progress instance
        self._task_id: Any = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> ProgressReporter:
        if self._use_rich:
            self._start_rich()
        return self

    def __exit__(self, *_: object) -> None:
        if self._use_rich and self._progress is not None:
            self._progress.__exit__(None, None, None)
        self._emit_summary()

    # ------------------------------------------------------------------
    # Trial callback
    # ------------------------------------------------------------------

    def on_trial_done(
        self,
        trial_number: int,
        algorithm: str,
        score: float | None,
        params: dict[str, Any],
    ) -> None:
        """Called once per completed Optuna trial."""
        with self._lock:
            self._completed += 1
            if score is not None and (
                self._best_score is None or score > self._best_score
            ):
                self._best_score = score
                self._best_algorithm = algorithm

        if self._use_rich and self._progress is not None:
            desc = (
                f"[cyan]{algorithm}[/cyan]  score={score:.4f}" if score else algorithm
            )
            self._progress.update(
                self._task_id,
                advance=1,
                description=desc,
            )
            return

        if self._quiet:
            return

        # Structured log path
        event_fields: dict[str, Any] = {
            "event": "trial_done",
            "trial": trial_number,
            "score": score,
            "algorithm": algorithm,
            "recipe": self._recipe_name,
            "run_id": self._run_id,
        }
        if self._verbose:
            event_fields["params"] = params

        logger.info(**event_fields)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_rich(self) -> None:
        from rich.progress import (  # type: ignore[import]
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TaskProgressColumn,
            TextColumn,
            TimeElapsedColumn,
            TimeRemainingColumn,
        )

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=None,  # defaults to stderr
            redirect_stderr=False,
        )
        self._task_id = self._progress.add_task(
            f"Tuning {self._recipe_name}", total=self._n_trials
        )
        self._progress.__enter__()

    def _emit_summary(self) -> None:
        logger.info(
            "tuning_complete",
            recipe=self._recipe_name,
            run_id=self._run_id,
            n_completed=self._completed,
            best_score=self._best_score,
            best_algorithm=self._best_algorithm,
        )


def _rich_available() -> bool:
    """Return ``True`` if ``rich`` is importable."""
    try:
        import rich  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Convenience factory for the trial callback signature Optuna expects
# ---------------------------------------------------------------------------


def make_trial_callback(
    reporter: ProgressReporter,
    default_class: str = "unknown",
) -> Callable:
    """Return an Optuna study callback that feeds *reporter*.

    Compatible with ``optuna.study.Study.optimize(callbacks=[...])``.
    The callback is called with ``(study, trial)`` after each trial.

    ``default_class`` is forwarded to
    :func:`~recotem.training.search.extract_class_and_clean_params` for
    early trials whose ``recommender_class_name`` has not yet been written
    to ``trial.user_attrs`` / ``trial.params``.  Pass ``algorithms[0]`` so
    the structured ``trial_done`` events surface a real candidate name to
    the SIEM rather than a ``"unknown"`` literal that pollutes class-name
    aggregations.
    """

    from recotem.training.search import extract_class_and_clean_params  # noqa: PLC0415

    def _callback(study, trial) -> None:  # type: ignore[no-untyped-def]
        algorithm, params = extract_class_and_clean_params(
            trial, default_class=default_class
        )

        score: float | None = None
        if trial.value is not None:
            score = -trial.value  # Optuna minimises; we negate back to metric

        reporter.on_trial_done(
            trial_number=trial.number,
            algorithm=algorithm,
            score=score,
            params=params,
        )

    return _callback
