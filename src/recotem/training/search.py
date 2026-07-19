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
from typing import Any

import optuna
import scipy.sparse as sps
import structlog
from irspack import Evaluator
from optuna.samplers import TPESampler

# _compat applies IPython stub before irspack imports (see _compat.py).
import recotem.training._compat  # noqa: F401
from recotem.training.algorithms import (
    get_recommender_cls,
    is_feature_capable,
    resolve_algorithm_name,
)
from recotem.training.errors import SearchError, TrainingError, ZeroScoreError
from recotem.training.evaluate import get_score
from recotem.training.progress import ProgressReporter, make_trial_callback

logger = structlog.get_logger(__name__)

# Suppress Optuna's noisy logging (we emit our own structured events).
optuna.logging.set_verbosity(optuna.logging.WARNING)

# Maximum number of simultaneously-live orphaned threads before we abort the
# study entirely.  Each orphaned thread consumes a C-extension worker that
# cannot be killed; accumulating too many stalls the process and eventually
# causes OOM.  32 is a conservative ceiling — operators whose dataset+algorithm
# combination genuinely needs longer training should raise
# per_trial_timeout_seconds rather than bumping this constant.
#
# "Live" means the orphaned thread has not yet returned from its underlying
# learn step.  The live count is decremented when the orphaned thread
# eventually finishes (see the ``_learn`` finally clause in ``run_search``).
# A separate cumulative counter tracks the total number of orphaned threads
# ever created (surfaced as ``SearchResult.orphaned_count``).
_MAX_LIVE_ORPHANED_THREADS: int = 32


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
        "orphaned_count",
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
        orphaned_count: int,
        search_seed: int,
    ) -> None:
        self.best_class_name = best_class_name
        self.best_params = best_params
        self.best_score = best_score
        self.best_trial_number = best_trial_number
        self.tried_algorithms = tried_algorithms
        self.n_trials = n_trials
        self.n_completed = n_completed
        self.orphaned_count = orphaned_count
        self.search_seed = search_seed


# ---------------------------------------------------------------------------
# Optuna storage factory
# ---------------------------------------------------------------------------


def _make_storage(storage_path: str) -> optuna.storages.BaseStorage | None:
    """Return an Optuna storage instance for *storage_path*, or ``None`` for
    in-memory storage (empty string or whitespace-only).

    Raises
    ------
    SearchError
        If *storage_path* is a SQLAlchemy URL embedding userinfo
        (``user:pass@host``).  Embedded credentials end up in SQLAlchemy
        exception traces and the redaction processor only redacts by
        dict key, so URL-shaped values would slip past it.  Operators
        should use environment-driven credentials (e.g. ``PGPASSFILE``)
        instead.
    """
    from urllib.parse import urlparse  # noqa: PLC0415

    path = storage_path.strip()
    if not path:
        return None

    # Postgres or other SQLAlchemy-backed URL
    if re.match(r"^(postgresql|postgres|mysql|sqlite)\b", path):
        parsed = urlparse(path)
        if parsed.username or parsed.password:
            raise SearchError(
                "training.storage_path must not embed credentials "
                "(user:pass@host).  Use a credential file or env-driven "
                "auth (PGPASSFILE, ~/.pgpass, sqlalchemy.url env) instead."
            )
        return optuna.storages.RDBStorage(path)

    # Bare file path -> SQLite
    if not path.startswith(("sqlite:///", "sqlite://")):
        path = f"sqlite:///{path}"
    return optuna.storages.RDBStorage(path)


# ---------------------------------------------------------------------------
# Feature-aware construction helper
# ---------------------------------------------------------------------------


def _construct(
    rec_cls: type,
    X: sps.spmatrix,
    params: dict[str, Any],
    feature_kwargs: dict[str, sps.csr_matrix] | None,
) -> Any:
    """Construct a recommender, injecting feature matrices exactly once.

    THE single construction point for feature-aware training: both search
    paths here (the per-trial-timeout thread path and the default path) and
    the final refit in ``pipeline._train_final``. It exists because irspack
    fails asymmetrically: it raises for a feature matrix with lambda=0, but
    silently trains PLAIN iALS for a lambda with no feature matrix. Routing
    every site through here turns that one silent mistake into a raise.

    The checks below are an explicit ``raise AssertionError`` rather than
    ``assert`` because ``assert`` is stripped under ``-O`` / ``PYTHONOPTIMIZE``
    -- which would restore exactly the silent plain-iALS training this helper
    exists to prevent. ``AssertionError`` (not ``TrainingError``) is
    deliberate: reaching here means recotem suggested a lambda without
    plumbing through the matching matrix, which is a bug in this package, not
    a bad recipe or bad data, so it must not claim one of the semantic exit
    codes (2-8). It is left unmapped by ``_map_exception_to_exit`` and so
    surfaces as ``_EXIT_UNKNOWN`` (exit 1, "unhandled / unmapped exception"),
    which is the code reserved for bugs -- exit 4 would send the operator
    looking at a recipe and data that are not at fault.

    Scope of the guarantee: this catches only lambda-without-matrix. It does
    not check that a supplied matrix has the right shape, dtype, or row order
    -- axis alignment is covered separately by the alignment tests in
    tests/unit/test_training_pipeline.py.
    """
    kwargs = dict(feature_kwargs or {})
    if "lambda_item_feature" in params and kwargs.get("item_features") is None:
        raise AssertionError(
            "lambda_item_feature was suggested but no item_features matrix "
            "was supplied; irspack would silently train plain iALS"
        )
    if "lambda_user_feature" in params and kwargs.get("user_features") is None:
        raise AssertionError(
            "lambda_user_feature was suggested but no user_features matrix "
            "was supplied; irspack would silently train plain iALS"
        )
    return rec_cls(X, **params, **kwargs)


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
    metric: str = "ndcg",
    feature_kwargs: dict[str, sps.csr_matrix] | None = None,
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
    feature_kwargs:
        Optional ``{"item_features": csr_matrix, "user_features": csr_matrix}``
        produced by ``training.features.encode_for_axis`` (only present keys
        for configured sides). Defaults to ``None`` so existing callers are
        unaffected. Only forwarded to trials whose sampled algorithm is
        feature-capable (``is_feature_capable``); every other trial gets
        irspack's own ``(n, 0)`` auto-fill.  When present for a
        feature-capable trial, a ``lambda_*_feature`` search dimension is
        added per configured side.

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
    # Guard against SQLite + parallelism>1 which causes DB corruption.
    # SQLite's WAL mode cannot be safely used across threads within a single
    # process when multiple threads simultaneously call ``study.optimize``.
    # Detect SQLite (bare path → sqlite:///, explicit sqlite:// prefix, or any
    # non-Postgres/MySQL URL-shaped path) and downgrade parallelism to 1.
    # In-memory storage (storage_path=None or "") is not affected.
    if storage_path and parallelism > 1:
        _sp = storage_path.strip()
        _is_pg_mysql = bool(re.match(r"^(postgresql|postgres|mysql)\b", _sp))
        _is_sqlite = not _is_pg_mysql  # everything else is SQLite or file-based
        if _is_sqlite:
            logger.warning(
                "env_var_clamped",
                var="parallelism",
                original=parallelism,
                clamped=1,
                reason=(
                    "SQLite Optuna storage does not support multi-threaded access; "
                    "parallelism downgraded to 1 to prevent study DB corruption. "
                    "Use a PostgreSQL URL for parallelism > 1."
                ),
                recipe=recipe_name,
                run_id=run_id,
            )
            parallelism = 1

    # Resolve all aliases up front so we can report canonical names.
    class_names: list[str] = [resolve_algorithm_name(a) for a in algorithms]

    # Compute per-algorithm trial budgets.
    budgets: dict[str, int] = _compute_budgets(
        class_names=class_names,
        n_trials=n_trials,
        per_algorithm_trials=per_algorithm_trials,
    )

    # Drop algorithms that the caller explicitly disabled (budget == 0) so
    # Optuna does not waste sampler calls on classes whose every trial would
    # immediately be pruned.
    active_classes = [c for c in class_names if budgets.get(c, 0) > 0]
    if not active_classes:
        raise SearchError(
            "All algorithms are disabled by per_algorithm_trials.",
            code="no_active_algorithms",
        )
    class_names = active_classes

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

    # Pre-enqueue trials per algorithm so each algorithm receives exactly
    # its budgeted slot count. Without this, TPESampler can keep picking a
    # saturated class once its budget is hit; the in-objective budget
    # check then prunes those trials, but the global ``n_trials`` budget
    # is consumed regardless, leaving other algorithms underused. Enqueued
    # trials skip sampling for ``recommender_class_name`` and run in
    # queued order. For resumed studies (load_if_exists), subtract any
    # already-completed trials per class so we don't double-allocate.
    if len(class_names) > 1:
        already_completed: dict[str, int] = dict.fromkeys(class_names, 0)
        for t in study.trials:
            if t.state == optuna.trial.TrialState.COMPLETE:
                cls = _trial_class(t)
                if cls in already_completed:
                    already_completed[cls] += 1
        for cname in class_names:
            remaining = max(0, budgets.get(cname, 0) - already_completed[cname])
            for _ in range(remaining):
                study.enqueue_trial(
                    {"recommender_class_name": cname},
                    skip_if_exists=False,
                )

    has_per_trial_timeout = per_trial_timeout_seconds is not None

    # Thread-safe counters for per-trial-timeout orphaned threads.
    # _orphaned_live: currently outstanding orphan threads (decremented when the
    #   thread eventually returns from its learn step); used for ceiling abort.
    # _orphaned_total: cumulative count, never decremented; surfaced as
    #   SearchResult.orphaned_count and used for the post-search heuristic.
    _orphan_lock = threading.Lock()
    _orphaned_live: list[int] = [0]
    _orphaned_total: list[int] = [0]

    # Per-algorithm completed-trial counters and a shared lock.
    #
    # Replacing the O(N²) ``sum(1 for t in study.trials …)`` scan inside the
    # objective with O(1) in-memory counters eliminates two problems:
    #
    # 1. **Performance**: scanning study.trials on every objective call is O(N²)
    #    in total trial count, which is measurable at N > 50 even for cheap
    #    recommenders.
    # 2. **Race**: with n_jobs > 1 two concurrent objective calls can each read
    #    budget-1 from study.trials, both proceed, and the per-algorithm count
    #    overshoots by up to parallelism-1.  A single lock around the
    #    read-increment-check sequence makes the check-and-increment atomic,
    #    reducing the overshoot window to zero for single-process threaded
    #    parallelism (n_jobs > 1, same process).  Across-process parallelism
    #    (RDBStorage + separate worker processes) is out of scope for this guard;
    #    in that regime, per-algo enqueuing (the pre-study block above) is the
    #    primary budget enforcer.
    _budget_lock = threading.Lock()
    _algo_completed: dict[str, int] = dict.fromkeys(class_names, 0)

    # Seed in-process counters from any already-completed trials so that
    # resumed studies (load_if_exists=True) do not get double credit.
    for _t in study.trials:
        if _t.state == optuna.trial.TrialState.COMPLETE:
            _cls = _trial_class(_t)
            if _cls in _algo_completed:
                _algo_completed[_cls] += 1

    # Pass class_names[0] as the default so early-trial structured logs surface
    # a real candidate name rather than the literal "unknown" sentinel that
    # would otherwise pollute SIEM aggregations of ``trial_done.algorithm``.
    trial_progress_cb = make_trial_callback(reporter, default_class=class_names[0])

    def objective(trial: optuna.Trial) -> float:
        if len(class_names) == 1:
            class_name = class_names[0]
        else:
            class_name = trial.suggest_categorical(
                "recommender_class_name", class_names
            )

        # Atomically check and reserve a budget slot for this algorithm.
        # Using a lock makes the read-compare-increment sequence atomic so that
        # concurrent objective calls (n_jobs > 1) cannot both see budget-1 and
        # both proceed, which was the source of the per-algorithm overshoot.
        with _budget_lock:
            if _algo_completed[class_name] >= budgets.get(class_name, n_trials):
                raise optuna.TrialPruned(
                    f"Per-algorithm budget for {class_name} exhausted."
                )

        rec_cls = get_recommender_cls(class_name)

        # Feature-aware iALS: only inject matrices when the class sampled
        # for THIS trial actually supports them. In a multi-algorithm search
        # a non-capable class (e.g. TopPop) may be sampled in the same study
        # as IALS; it must never receive item_features/user_features kwargs
        # it does not accept.
        trial_features: dict[str, sps.csr_matrix] = (
            feature_kwargs
            if (feature_kwargs and is_feature_capable(class_name))
            else {}
        )
        # NB: this reuses the ONE feature-matrix object built per phase, so
        # under parallelism>1 (Optuna n_jobs = threads, one process) every
        # concurrent trial shares it. Safe because irspack 0.5.0 reads the
        # feature matrices read-only -- unlike the interaction matrix X, which
        # it defensively copies via .astype(). Do NOT add a per-trial .copy()
        # (pure cost, no correctness gain today); if a future irspack ever
        # mutates the feature buffers in place, that reliance breaks here.

        params: dict[str, Any] = rec_cls.default_suggest_parameter(trial, {})

        # irspack ships no default search space for the feature ridge, and
        # the constructor default (0.0) is a hard error whenever a feature
        # matrix is present. Recotem defines the range, mirroring upstream's
        # own ML-1M example (the only range upstream has exercised). This is
        # a ridge on A/B, not the feature-prior strength itself (that's
        # reg/nu); a large enough value shrinks A/B toward zero and was
        # measured bit-identical to plain iALS at lambda=1e8. 1e6 is just
        # upstream's exercised value, NOT a verified features-off bound --
        # the search space is not guaranteed to contain a features-off model.
        # Run two recipes for a true on/off comparison.
        if trial_features:
            if "item_features" in trial_features:
                params["lambda_item_feature"] = trial.suggest_float(
                    "lambda_item_feature", 5e-2, 1e6, log=True
                )
            if "user_features" in trial_features:
                params["lambda_user_feature"] = trial.suggest_float(
                    "lambda_user_feature", 5e-2, 1e6, log=True
                )

        # Per-trial timeout: run the learn in a thread so we can interrupt.
        if has_per_trial_timeout:
            result_holder: list[Any] = []
            exc_holder: list[BaseException] = []
            trial_state = {"completed": False, "orphaned": False}

            def _learn() -> None:
                try:
                    try:
                        rec = _construct(rec_cls, X_tv_train, params, trial_features)
                        try:
                            rec.learn_with_optimizer(evaluator, trial)
                        except RuntimeError as exc:
                            # Rank-deficient features make the feature-ridge
                            # Cholesky fail. This is data-dependent, so prune
                            # the trial rather than failing the run --
                            # matching upstream's own feature-aware example.
                            if "Feature ridge Cholesky decomposition failed" in str(
                                exc
                            ):
                                raise optuna.TrialPruned(str(exc)) from exc
                            raise
                        result_holder.append(rec)
                    except (MemoryError, RecursionError):
                        # Re-raise without capture so the worker thread dies
                        # loudly and the parent process can surface OOM via
                        # exit code instead of bookkeeping it as a trial
                        # failure that hints at per-trial timeout.
                        raise
                    except optuna.TrialPruned as exc:
                        # A pruned trial (e.g. the rank-deficient-feature-Gram
                        # Cholesky failure above, or any other by-design prune
                        # raised from inside learn_with_optimizer) is a normal,
                        # expected outcome -- not a failure. TrialPruned
                        # subclasses Exception, so without this clause the
                        # generic handler below would catch it and log a
                        # spurious trial_learn_failed WARNING, making an
                        # expected prune look like an error under the
                        # per-trial-timeout thread path while the
                        # non-threaded path (which has no such generic catch)
                        # stays silent for the identical prune. Still route
                        # through exc_holder so it re-raises in the main
                        # thread and Optuna records the trial as PRUNED.
                        exc_holder.append(exc)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "trial_learn_failed",
                            recipe=recipe_name,
                            run_id=run_id,
                            trial=trial.number,
                            class_name=class_name,
                            error_class=type(exc).__name__,
                            error=str(exc),
                        )
                        exc_holder.append(exc)
                finally:
                    with _orphan_lock:
                        trial_state["completed"] = True
                        if trial_state["orphaned"]:
                            _orphaned_live[0] -= 1

            thread = threading.Thread(target=_learn, daemon=True)
            thread.start()

            timeout_val = per_trial_timeout_seconds or 0
            thread.join(timeout=float(timeout_val))

            if thread.is_alive():
                # Re-check completion under the lock: the thread may have finished
                # between `thread.is_alive()` returning True and our acquiring the
                # lock.  If so, the orphan path does not apply — fall through to
                # the normal completion handling below.
                was_orphaned = False
                current_live = 0
                current_total = 0
                with _orphan_lock:
                    if not trial_state["completed"]:
                        trial_state["orphaned"] = True
                        _orphaned_live[0] += 1
                        _orphaned_total[0] += 1
                        current_live = _orphaned_live[0]
                        current_total = _orphaned_total[0]
                        was_orphaned = True
                if was_orphaned:
                    # The learn thread is still running and CANNOT be killed —
                    # irspack's recommenders execute in C extensions that don't
                    # honour Python's interpreter-level interrupt.  We prune the
                    # Optuna trial so the search keeps making progress, but the
                    # orphaned thread continues to consume memory / CPU until it
                    # finishes its current learn step.  When that happens, the
                    # `_learn` finally clause decrements `_orphaned_live` so the
                    # ceiling check correctly tracks concurrent (not cumulative)
                    # orphans.  Operators should treat repeated occurrences as a
                    # sign that ``per_trial_timeout_seconds`` is too aggressive
                    # for this dataset+algorithm combination.
                    logger.warning(
                        "per_trial_timeout_thread_orphaned",
                        recipe=recipe_name,
                        run_id=run_id,
                        trial=trial.number,
                        class_name=class_name,
                        timeout_seconds=per_trial_timeout_seconds,
                    )
                    # Emit a periodic aggregate warning every 16 cumulative
                    # orphan events so operators can detect systematic timeout
                    # misconfiguration without being spammed every trial.
                    if current_total % 16 == 0:
                        logger.warning(
                            "training_orphaned_threads",
                            live=current_live,
                            total=current_total,
                            ceiling=_MAX_LIVE_ORPHANED_THREADS,
                            recipe=recipe_name,
                            run_id=run_id,
                        )
                    # Runtime ceiling: abort the study if the number of live orphaned
                    # threads reaches the configured maximum.  Beyond this point the
                    # process is at risk of OOM and the search is not making useful
                    # progress — raising SearchError here propagates out of
                    # study.optimize and surfaces as a fatal training failure.
                    if current_live >= _MAX_LIVE_ORPHANED_THREADS:
                        raise SearchError(
                            f"orphaned thread ceiling exceeded ({current_live} >= "
                            f"{_MAX_LIVE_ORPHANED_THREADS}); "
                            "check per_trial_timeout vs algorithm runtime"
                        )
                    raise optuna.TrialPruned(
                        f"Trial {trial.number} exceeded per_trial_timeout_seconds "
                        f"({per_trial_timeout_seconds}s)."
                    )
            if exc_holder:
                raise exc_holder[0]
            recommender = result_holder[0]
        else:
            recommender = _construct(rec_cls, X_tv_train, params, trial_features)
            try:
                recommender.learn_with_optimizer(evaluator, trial)
            except RuntimeError as exc:
                # Rank-deficient features make the feature-ridge Cholesky
                # fail. This is data-dependent, so prune the trial rather
                # than failing the run -- matching upstream's own
                # feature-aware example.
                if "Feature ridge Cholesky decomposition failed" in str(exc):
                    raise optuna.TrialPruned(str(exc)) from exc
                raise

        score = get_score(evaluator, recommender)

        # Increment the in-memory per-algorithm completed counter so the next
        # call to this objective sees an up-to-date budget snapshot.  The
        # increment is done under _budget_lock to stay consistent with the
        # check-and-reserve block at the top of the objective.
        with _budget_lock:
            _algo_completed[class_name] = _algo_completed.get(class_name, 0) + 1

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
    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    n_completed = len(completed)
    orphaned_count = _orphaned_total[0]

    if n_completed == 0:
        raise SearchError(
            "No trials completed successfully. "
            "Check algorithm compatibility with the dataset and increase "
            "per_trial_timeout_seconds or n_trials.",
            code="no_completed_trials",
        )

    # Fail loudly if more than half of completed trials were orphaned — this
    # indicates per_trial_timeout_seconds is far too aggressive.
    if orphaned_count > n_completed // 2:
        raise TrainingError(
            f"Excessive per-trial timeouts: {orphaned_count} threads orphaned vs "
            f"{n_completed} trials completed. Increase per_trial_timeout_seconds "
            "or reduce dataset/algorithm complexity.",
            code="excessive_per_trial_timeouts",
        )

    best_trial = study.best_trial

    best_class_name, best_params_raw = extract_class_and_clean_params(
        best_trial, default_class=class_names[0]
    )

    # Merge learnt_config attributes (prefixed params stored as user_attrs).
    best_params: dict[str, Any] = {}
    for key, val in best_trial.user_attrs.items():
        if key not in ("recommender_class_name",):
            best_params[key] = val
    # Optuna-suggested params take precedence (they are the ones passed to __init__).
    best_params.update(best_params_raw)

    best_score = -best_trial.value  # un-negate

    if best_score == 0.0:
        if metric == "hit":
            # hit@k is legitimately 0 on small validation sets; warn but continue.
            logger.warning(
                "zero_score_hit_metric",
                recipe=recipe_name,
                run_id=run_id,
                metric=metric,
                n_completed=n_completed,
                message=(
                    "Best score is 0.0 for metric='hit'. This may be legitimate "
                    "on small validation sets. The artifact will still be written. "
                    "Consider increasing cutoff or validation set size."
                ),
            )
        else:
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
        orphaned_count=orphaned_count,
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

    Semantics:

    * No ``per_algorithm_trials`` (or empty): even split over ``class_names``;
      the leading classes absorb the rounding remainder.
    * Explicit ``0`` for a class: that class is **skipped** (budget 0).
    * Explicit positive value: respected literally if it fits; if the sum of
      explicit values exceeds ``n_trials`` the positive values are scaled
      down proportionally (each remains ≥ 1 *when at least n_trials slots
      exist* — see the next bullet for the n_trials-too-small case).
    * If ``n_trials`` is smaller than the number of explicit-positive
      classes, the first ``n_trials`` of those classes get one trial each
      and the rest get 0.  This preserves ``sum(budgets) == n_trials`` and
      gives every trial slot to a real algorithm rather than dropping any.
    * Class in ``class_names`` but absent from ``per_algorithm_trials``:
      shares whatever budget is left after honouring explicit values.
    * If every class is explicitly 0 *and* nothing is unspecified, the
      configuration is treated as "no override" and falls back to the even
      split (so the run still produces trials).

    The returned mapping always sums to ``n_trials`` unless every entry is 0
    (which only happens when the caller asked for no trials).
    """
    if not per_algorithm_trials:
        base = n_trials // len(class_names)
        remainder = n_trials % len(class_names)
        budgets: dict[str, int] = {}
        for idx, name in enumerate(class_names):
            budgets[name] = base + (1 if idx < remainder else 0)
        return budgets

    resolved: dict[str, int] = {}
    for alias, count in per_algorithm_trials.items():
        # If the key is already a resolved class name (present in class_names),
        # use it directly without going through resolve_algorithm_name so that
        # callers passing already-canonical names (e.g. "IALSRecommender") are
        # accepted without a redundant round-trip through the alias table.
        if alias in class_names:
            cname = alias
        else:
            try:
                cname = resolve_algorithm_name(alias)
            except Exception as exc:
                raise TrainingError(
                    f"Unknown algorithm alias {alias!r} in per_algorithm_trials. "
                    "Check the algorithm name against the supported algorithms list.",
                    code="unknown_algorithm_in_budget",
                ) from exc
        resolved[cname] = max(0, count)

    explicit_classes = [c for c in class_names if c in resolved]
    unspecified_classes = [c for c in class_names if c not in resolved]
    explicit_sum = sum(resolved[c] for c in explicit_classes)

    if explicit_sum == 0 and not unspecified_classes:
        return _compute_budgets(class_names, n_trials, None)

    budgets = dict.fromkeys(class_names, 0)

    if explicit_sum > n_trials:
        nonzero_explicit = [c for c in explicit_classes if resolved[c] > 0]

        # Edge case: fewer slots than non-zero classes.  We cannot give every
        # class ≥ 1 *and* keep the total at n_trials.  Prefer "total ==
        # n_trials" (Optuna stops there anyway) — give 1 trial each to the
        # first n_trials non-zero classes and 0 to the remainder.  This keeps
        # the contract sum(budgets) == n_trials intact.
        if n_trials < len(nonzero_explicit):
            for idx, c in enumerate(nonzero_explicit):
                budgets[c] = 1 if idx < n_trials else 0
            return budgets

        scale = n_trials / explicit_sum
        allocated = 0
        for c in explicit_classes:
            v = resolved[c]
            if v == 0:
                budgets[c] = 0
            elif c == nonzero_explicit[-1]:
                budgets[c] = max(1, n_trials - allocated)
            else:
                b = max(1, round(v * scale))
                budgets[c] = b
                allocated += b
        return budgets

    for c in explicit_classes:
        budgets[c] = resolved[c]
    leftover = n_trials - explicit_sum
    if leftover <= 0:
        return budgets

    if unspecified_classes:
        base = leftover // len(unspecified_classes)
        rem = leftover % len(unspecified_classes)
        for idx, c in enumerate(unspecified_classes):
            budgets[c] = base + (1 if idx < rem else 0)
    else:
        nonzero = [c for c in explicit_classes if resolved[c] > 0]
        if nonzero:
            budgets[nonzero[-1]] += leftover

    return budgets


def _trial_class(trial: optuna.trial.FrozenTrial) -> str:
    """Extract the recommender class name from a completed trial."""
    return str(
        trial.user_attrs.get(
            "recommender_class_name",
            trial.params.get("recommender_class_name", "unknown"),
        )
    )


# Keys Optuna may inject into trial.params that should not be forwarded as
# recommender constructor arguments.
_TRIAL_BOOKKEEPING_KEYS = ("recommender_class_name", "optimizer_name")


def extract_class_and_clean_params(
    trial: Any,
    default_class: str,
) -> tuple[str, dict[str, Any]]:
    """Return ``(class_name, params_without_bookkeeping)`` for *trial*.

    Reads ``recommender_class_name`` from ``trial.user_attrs`` first, falling
    back to ``trial.params`` and finally to *default_class*.  Returns a fresh
    dict copy of ``trial.params`` with the Optuna bookkeeping keys removed.
    """
    params = dict(trial.params)
    class_name = str(
        trial.user_attrs.get(
            "recommender_class_name",
            params.get("recommender_class_name", default_class),
        )
    )
    for key in _TRIAL_BOOKKEEPING_KEYS:
        params.pop(key, None)
    return class_name, params
