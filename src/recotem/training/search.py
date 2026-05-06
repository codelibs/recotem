"""Optuna-based hyperparameter search driver.

Implements the spec's Section 6 step 6 requirements:
- Single or multi-algorithm categorical search.
- Per-algorithm trial budget partitioning.
- Per-trial soft timeout via Optuna callback.
- Deterministic seeding from recipe split seed (or explicit random_seed).
- In-memory or persistent (SQLite / Postgres URL) storage.
- Parallelism via optuna n_jobs.
- Raises ZeroScoreError if all completed trials score 0.0.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import optuna
import scipy.sparse as sps

# _compat applies IPython stub before irspack imports (see _compat.py).
import recotem.training._compat  # noqa: F401
from irspack import Evaluator
from optuna.samplers import TPESampler

from recotem.training.algorithms import get_recommender_cls, resolve_algorithm_name
from recotem.training.errors import SearchError, ZeroScoreError
from recotem.training.evaluate import get_score
from recotem.training.progress import ProgressReporter, make_trial_callback

# Suppress Optuna's noisy logging (we emit our own structured events).
optuna.logging.set_verbosity(optuna.logging.WARNING)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


class SearchResult:
    """Outcome of a completed search."""

    __slots__ = (
        "best_class_name",
        "best_params",
        "best_score",
        "best_trial_number",
        "tried_algorithms",
        "n_trials",
        "n_completed",
        "search_seed",
    )

    def __init__(
        self,
        best_class_name: str,
        best_params: dict[str, Any],
        best_score: float,
        best_trial_number: int,
        tried_algorithms: list[str],
        n_trials: int,
        n_completed: int,
        search_seed: int,
    ) -> None:
        self.best_class_name = best_class_name
        self.best_params = best_params
        self.best_score = best_score
        self.best_trial_number = best_trial_number
        self.tried_algorithms = tried_algorithms
        self.n_trials = n_trials
        self.n_completed = n_completed
        self.search_seed = search_seed


# ---------------------------------------------------------------------------
# Per-trial timeout via Optuna callback
# ---------------------------------------------------------------------------


class _PerTrialTimeoutCallback:
    """Optuna callback that prunes a trial that has run too long.

    Implemented as a wall-clock tracker: ``on_trial_complete`` is never called
    for pruned trials in older Optuna versions, so we track the trial start
    time via ``study._storage`` hooks indirectly.  The simpler approach is to
    use the ``trial.should_prune()`` path: a separate thread raises
    ``TrialPruned`` inside the objective by setting a per-trial flag.

    Actually the cleanest portable approach for Optuna 3.x is to check the
    elapsed time from inside the objective function, but we cannot modify the
    objective easily from the callback.  Instead, we store trial start times
    when the callback is invoked and, if the trial time exceeds the budget,
    we report pruned on the *next* callback invocation.

    For simplicity and Optuna 3.x compatibility: the per-trial timeout is
    enforced by wrapping the objective at objective-call time with a thread
    that raises ``TrialPruned`` via ``trial.report(nan, 0)`` followed by
    ``raise TrialPruned()``.  This is handled in ``run_search`` where we
    wrap the per-class objective.
    """

    def __init__(self, per_trial_timeout_seconds: float) -> None:
        self._timeout = per_trial_timeout_seconds
        self._start_times: dict[int, float] = {}
        self._lock = threading.Lock()

    def record_start(self, trial_number: int) -> None:
        with self._lock:
            self._start_times[trial_number] = time.monotonic()

    def elapsed(self, trial_number: int) -> float:
        with self._lock:
            start = self._start_times.get(trial_number)
        if start is None:
            return 0.0
        return time.monotonic() - start

    def is_over_budget(self, trial_number: int) -> bool:
        return self.elapsed(trial_number) > self._timeout


# ---------------------------------------------------------------------------
# Optuna storage factory
# ---------------------------------------------------------------------------


def _make_storage(storage_path: str) -> optuna.storages.BaseStorage | None:
    """Return an Optuna storage instance for *storage_path*, or ``None`` for
    in-memory storage (empty string or whitespace-only)."""
    path = storage_path.strip()
    if not path:
        return None

    # Postgres or other SQLAlchemy-backed URL
    if re.match(r"^(postgresql|postgres|mysql|sqlite)\b", path):
        return optuna.storages.RDBStorage(path)

    # Bare file path -> SQLite
    if not path.startswith(("sqlite:///", "sqlite://")):
        path = f"sqlite:///{path}"
    return optuna.storages.RDBStorage(path)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_search(
    *,
    algorithms: list[str],
    X_tv_train: sps.spmatrix,
    evaluator: Evaluator,
    n_trials: int,
    per_algorithm_trials: dict[str, int] | None,
    per_trial_timeout_seconds: int | None,
    timeout_seconds: int | None,
    parallelism: int,
    storage_path: str,
    random_seed: int,
    reporter: ProgressReporter,
    recipe_name: str,
    run_id: str,
) -> SearchResult:
    """Run an Optuna hyperparameter search and return the best result.

    Parameters
    ----------
    algorithms:
        List of resolved canonical class names (e.g. ``["IALSRecommender"]``).
    X_tv_train:
        Combined train+val training interaction matrix.
    evaluator:
        irspack ``Evaluator`` for scoring.
    n_trials:
        Global trial budget.
    per_algorithm_trials:
        Optional per-algorithm trial override (keyed by alias or class name).
    per_trial_timeout_seconds:
        Soft per-trial wall-clock budget (pruning via thread).
    timeout_seconds:
        Overall wall-clock cap passed to ``study.optimize``.
    parallelism:
        Number of parallel Optuna worker threads.
    storage_path:
        Path/URL for persistent Optuna storage; empty string = in-memory.
    random_seed:
        Seed for TPESampler.
    reporter:
        ``ProgressReporter`` instance for trial notifications.
    recipe_name, run_id:
        Carried in log events.

    Returns
    -------
    SearchResult

    Raises
    ------
    SearchError
        If no trials completed successfully.
    ZeroScoreError
        If all completed trials scored 0.0.
    """
    # Resolve all aliases up front so we can report canonical names.
    class_names: list[str] = [resolve_algorithm_name(a) for a in algorithms]

    # Compute per-algorithm trial budgets.
    budgets: dict[str, int] = _compute_budgets(
        class_names=class_names,
        n_trials=n_trials,
        per_algorithm_trials=per_algorithm_trials,
    )

    storage = _make_storage(storage_path)
    study_name = f"recotem_{recipe_name}_{run_id}"

    sampler = TPESampler(seed=random_seed)
    study = optuna.create_study(
        storage=storage,
        study_name=study_name,
        direction="minimize",
        sampler=sampler,
        load_if_exists=True,
    )

    per_trial_timeout_cb = (
        _PerTrialTimeoutCallback(per_trial_timeout_seconds)
        if per_trial_timeout_seconds is not None
        else None
    )

    trial_progress_cb = make_trial_callback(reporter)

    def objective(trial: optuna.Trial) -> float:
        if len(class_names) == 1:
            class_name = class_names[0]
        else:
            class_name = trial.suggest_categorical(
                "recommender_class_name", class_names
            )

        # Check per-algorithm budget: prune if this class is over budget.
        completed_for_class = sum(
            1
            for t in study.trials
            if t.state == optuna.trial.TrialState.COMPLETE
            and _trial_class(t, len(class_names) > 1) == class_name
        )
        if completed_for_class >= budgets.get(class_name, n_trials):
            raise optuna.TrialPruned(
                f"Per-algorithm budget for {class_name} exhausted."
            )

        rec_cls = get_recommender_cls(class_name)

        # Record start time for per-trial timeout.
        if per_trial_timeout_cb is not None:
            per_trial_timeout_cb.record_start(trial.number)

        params: dict[str, Any] = rec_cls.default_suggest_parameter(trial, {})

        # Per-trial timeout: run the learn in a thread so we can interrupt.
        if per_trial_timeout_cb is not None:
            result_holder: list[Any] = []
            exc_holder: list[BaseException] = []

            def _learn() -> None:
                try:
                    rec = rec_cls(X_tv_train, **params)
                    rec.learn_with_optimizer(evaluator, trial)
                    result_holder.append(rec)
                except Exception as exc:  # noqa: BLE001
                    exc_holder.append(exc)

            thread = threading.Thread(target=_learn, daemon=True)
            thread.start()

            timeout_val = per_trial_timeout_seconds or 0
            thread.join(timeout=float(timeout_val))

            if thread.is_alive():
                # Thread still running; prune the trial.
                raise optuna.TrialPruned(
                    f"Trial {trial.number} exceeded per_trial_timeout_seconds "
                    f"({per_trial_timeout_seconds}s)."
                )
            if exc_holder:
                raise exc_holder[0]
            recommender = result_holder[0]
        else:
            recommender = rec_cls(X_tv_train, **params)
            recommender.learn_with_optimizer(evaluator, trial)

        score = get_score(evaluator, recommender)

        trial.set_user_attr("recommender_class_name", class_name)
        for param_name, param_val in recommender.learnt_config.items():
            trial.set_user_attr(param_name, param_val)

        return -score  # Optuna minimises; negate the metric

    study.optimize(
        objective,
        n_trials=n_trials,
        timeout=timeout_seconds,
        n_jobs=parallelism,
        callbacks=[trial_progress_cb],
    )

    # Post-search analysis.
    completed = [
        t
        for t in study.trials
        if t.state == optuna.trial.TrialState.COMPLETE
    ]
    n_completed = len(completed)

    if n_completed == 0:
        raise SearchError(
            "No trials completed successfully. "
            "Check algorithm compatibility with the dataset and increase "
            "per_trial_timeout_seconds or n_trials.",
            code="no_completed_trials",
        )

    best_trial = study.best_trial

    # Extract best class name and params.
    best_params_raw = dict(best_trial.params)
    best_class_name: str = str(
        best_trial.user_attrs.get(
            "recommender_class_name",
            best_params_raw.pop("recommender_class_name", class_names[0]),
        )
    )

    # Clean up Optuna bookkeeping keys from params.
    best_params_raw.pop("recommender_class_name", None)
    best_params_raw.pop("optimizer_name", None)

    # Merge learnt_config attributes (prefixed params stored as user_attrs).
    best_params: dict[str, Any] = {}
    for key, val in best_trial.user_attrs.items():
        if key not in ("recommender_class_name",):
            best_params[key] = val
    # Optuna-suggested params take precedence (they are the ones passed to __init__).
    best_params.update(best_params_raw)

    best_score = -best_trial.value  # un-negate

    if best_score == 0.0:
        raise ZeroScoreError(
            f"Best score across {n_completed} completed trials is 0.0. "
            "This may indicate too short a timeout or too small a validation set."
        )

    return SearchResult(
        best_class_name=best_class_name,
        best_params=best_params,
        best_score=best_score,
        best_trial_number=best_trial.number,
        tried_algorithms=class_names,
        n_trials=n_trials,
        n_completed=n_completed,
        search_seed=random_seed,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_budgets(
    class_names: list[str],
    n_trials: int,
    per_algorithm_trials: dict[str, int] | None,
) -> dict[str, int]:
    """Return a per-class trial budget dictionary.

    If *per_algorithm_trials* is provided, use those values (scaled down if
    their sum exceeds *n_trials*).  Otherwise split proportionally.
    """
    if not per_algorithm_trials:
        # Equal split; last class absorbs the remainder.
        base = n_trials // len(class_names)
        remainder = n_trials % len(class_names)
        budgets: dict[str, int] = {}
        for idx, name in enumerate(class_names):
            budgets[name] = base + (1 if idx < remainder else 0)
        return budgets

    # Resolve aliases in per_algorithm_trials keys.
    resolved: dict[str, int] = {}
    for alias, count in per_algorithm_trials.items():
        try:
            cname = resolve_algorithm_name(alias)
        except Exception:  # noqa: BLE001
            cname = alias
        resolved[cname] = count

    total_requested = sum(resolved.get(c, 0) for c in class_names)

    if total_requested == 0:
        # Fall back to proportional.
        return _compute_budgets(class_names, n_trials, None)

    # Scale down if over budget.
    scale = min(1.0, n_trials / total_requested)
    budgets = {}
    allocated = 0
    for idx, name in enumerate(class_names):
        raw = resolved.get(name, 0)
        if idx == len(class_names) - 1:
            # Last class gets the remainder to avoid rounding losses.
            budgets[name] = max(1, n_trials - allocated)
        else:
            b = max(1, round(raw * scale))
            budgets[name] = b
            allocated += b

    return budgets


def _trial_class(trial: optuna.trial.FrozenTrial, multi_algo: bool) -> str:
    """Extract the recommender class name from a completed trial."""
    return str(
        trial.user_attrs.get(
            "recommender_class_name",
            trial.params.get("recommender_class_name", "unknown"),
        )
    )
