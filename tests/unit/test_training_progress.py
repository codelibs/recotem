"""Unit tests for recotem.training.progress.ProgressReporter.

Tests:
- non-TTY on_trial_done emits 'trial_done' structured events
- quiet=True suppresses per-trial 'trial_done' events
- __exit__ always emits 'tuning_complete' summary
- best score updated correctly across multiple trials
- None score (failed trial) does not corrupt best_score or best_algorithm
- early trial with algorithm='unknown' does not corrupt summary
- verbose=True adds params field to 'trial_done' events
- completed counter increments on every call including failed trials
- trial_done event carries expected fields: trial, score, algorithm, recipe, run_id
"""

from __future__ import annotations

import structlog.testing

from recotem.training.errors import SearchError
from recotem.training.progress import ProgressReporter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reporter(**kwargs) -> ProgressReporter:
    """Construct a ProgressReporter with force_log=True (no rich bar in tests)."""
    defaults = {
        "n_trials": 3,
        "recipe_name": "test_recipe",
        "run_id": "run-test",
        "force_log": True,
    }
    defaults.update(kwargs)
    return ProgressReporter(**defaults)


# ---------------------------------------------------------------------------
# Non-TTY path emits trial_done events
# ---------------------------------------------------------------------------


def test_progress_reporter_emits_trial_done_event_non_tty() -> None:
    """on_trial_done emits a 'trial_done' structured event in non-TTY mode.

    force_log=True bypasses the TTY check, guaranteeing the structured log
    path is taken even when tests run in a terminal.
    """
    with structlog.testing.capture_logs() as cap:
        with _make_reporter() as rep:
            rep.on_trial_done(
                trial_number=0,
                algorithm="TopPop",
                score=0.25,
                params={},
            )

    trial_events = [e for e in cap if e.get("event") == "trial_done"]
    assert len(trial_events) == 1, (
        f"Expected exactly 1 'trial_done' event; got {[e.get('event') for e in cap]}"
    )
    ev = trial_events[0]
    assert ev["trial"] == 0
    assert ev["score"] == 0.25
    assert ev["algorithm"] == "TopPop"
    assert ev["recipe"] == "test_recipe"
    assert ev["run_id"] == "run-test"


def test_progress_reporter_trial_done_event_fields_are_complete() -> None:
    """trial_done events must carry all required structured fields."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(recipe_name="news_articles", run_id="abc123") as rep:
            rep.on_trial_done(
                trial_number=7,
                algorithm="IALS",
                score=0.42,
                params={"n_components": 16},
            )

    ev = next(e for e in cap if e.get("event") == "trial_done")
    assert ev["trial"] == 7
    assert ev["score"] == 0.42
    assert ev["algorithm"] == "IALS"
    assert ev["recipe"] == "news_articles"
    assert ev["run_id"] == "abc123"
    # Without verbose=True, params must not appear in the event.
    assert "params" not in ev, (
        f"Non-verbose mode must not include params field; got {ev!r}"
    )


# ---------------------------------------------------------------------------
# quiet=True suppresses per-trial events
# ---------------------------------------------------------------------------


def test_progress_reporter_quiet_suppresses_trial_done_events() -> None:
    """With quiet=True, no 'trial_done' events are emitted during trials."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(quiet=True) as rep:
            rep.on_trial_done(0, "TopPop", 0.1, {})
            rep.on_trial_done(1, "IALS", 0.2, {})
            rep.on_trial_done(2, "RP3beta", 0.3, {})

    trial_events = [e for e in cap if e.get("event") == "trial_done"]
    assert trial_events == [], (
        f"quiet=True must suppress all 'trial_done' events; got {trial_events!r}"
    )


# ---------------------------------------------------------------------------
# __exit__ always emits tuning_complete summary
# ---------------------------------------------------------------------------


def test_progress_reporter_summary_on_exit() -> None:
    """__exit__ must emit a 'tuning_complete' event regardless of quiet setting."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter() as rep:
            rep.on_trial_done(0, "TopPop", 0.5, {})

    summary_events = [e for e in cap if e.get("event") == "tuning_complete"]
    assert len(summary_events) == 1, (
        f"Expected exactly 1 'tuning_complete' event on __exit__; "
        f"got {[e.get('event') for e in cap]}"
    )


def test_progress_reporter_quiet_still_emits_summary_on_exit() -> None:
    """Even with quiet=True, __exit__ must emit 'tuning_complete'."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(quiet=True) as rep:
            rep.on_trial_done(0, "TopPop", 0.5, {})

    summary_events = [e for e in cap if e.get("event") == "tuning_complete"]
    assert len(summary_events) == 1, (
        f"quiet=True must still emit 'tuning_complete' on exit; "
        f"got {[e.get('event') for e in cap]}"
    )


def test_progress_reporter_summary_fields_are_complete() -> None:
    """tuning_complete event must carry recipe, run_id, n_completed, best_score,
    and best_algorithm fields."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(
            n_trials=2, recipe_name="purchase_log", run_id="run-42"
        ) as rep:
            rep.on_trial_done(0, "IALS", 0.35, {})
            rep.on_trial_done(1, "TopPop", 0.20, {})

    summary = next(e for e in cap if e.get("event") == "tuning_complete")
    assert summary["recipe"] == "purchase_log"
    assert summary["run_id"] == "run-42"
    assert summary["n_completed"] == 2
    assert summary["best_score"] == 0.35
    assert summary["best_algorithm"] == "IALS"


# ---------------------------------------------------------------------------
# Best score tracking
# ---------------------------------------------------------------------------


def test_progress_reporter_tracks_best_score_across_trials() -> None:
    """best_score and best_algorithm reflect the highest score seen."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(n_trials=4) as rep:
            rep.on_trial_done(0, "TopPop", 0.10, {})
            rep.on_trial_done(1, "IALS", 0.50, {})  # best so far
            rep.on_trial_done(2, "RP3beta", 0.30, {})
            rep.on_trial_done(3, "IALS", 0.45, {})  # worse than best

    summary = next(e for e in cap if e.get("event") == "tuning_complete")
    assert summary["best_score"] == 0.50, (
        f"Expected best_score=0.50, got {summary['best_score']!r}"
    )
    assert summary["best_algorithm"] == "IALS", (
        f"Expected best_algorithm='IALS', got {summary['best_algorithm']!r}"
    )


def test_progress_reporter_best_score_monotonically_increases() -> None:
    """Each higher score must replace the previous best; lower scores must not."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(n_trials=3) as rep:
            rep.on_trial_done(0, "A", 0.1, {})
            rep.on_trial_done(1, "B", 0.9, {})
            rep.on_trial_done(2, "C", 0.5, {})  # lower than best; must not replace

    summary = next(e for e in cap if e.get("event") == "tuning_complete")
    assert summary["best_score"] == 0.9
    assert summary["best_algorithm"] == "B"


# ---------------------------------------------------------------------------
# None score (failed trial) does not corrupt state
# ---------------------------------------------------------------------------


def test_progress_reporter_none_score_does_not_update_best() -> None:
    """A trial with score=None (failed trial) must not affect best_score."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(n_trials=3) as rep:
            rep.on_trial_done(0, "TopPop", 0.3, {})
            rep.on_trial_done(1, "IALS", None, {})  # failed
            rep.on_trial_done(2, "RP3beta", 0.2, {})

    summary = next(e for e in cap if e.get("event") == "tuning_complete")
    assert summary["best_score"] == 0.3
    assert summary["best_algorithm"] == "TopPop"
    assert summary["n_completed"] == 3


def test_progress_reporter_all_none_scores_leaves_best_score_none() -> None:
    """When every trial has score=None, best_score must remain None in the summary."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(n_trials=2) as rep:
            rep.on_trial_done(0, "TopPop", None, {})
            rep.on_trial_done(1, "IALS", None, {})

    summary = next(e for e in cap if e.get("event") == "tuning_complete")
    assert summary["best_score"] is None
    assert summary["n_completed"] == 2


# ---------------------------------------------------------------------------
# Unknown default class does not corrupt summary
# ---------------------------------------------------------------------------


def test_progress_reporter_unknown_default_class_does_not_corrupt_summary() -> None:
    """Early trial with algorithm='unknown' and None score must not corrupt best state.

    When make_trial_callback is used without a default_class, early trials whose
    recommender_class_name has not been written to user_attrs surface as
    algorithm='unknown'.  The reporter must handle this gracefully.
    """
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(n_trials=3) as rep:
            # Simulate an early trial before recommender_class_name is set.
            rep.on_trial_done(0, "unknown", None, {})
            rep.on_trial_done(1, "TopPop", 0.4, {})
            rep.on_trial_done(2, "IALS", 0.8, {})

    summary = next(e for e in cap if e.get("event") == "tuning_complete")
    assert summary["best_score"] == 0.8, (
        f"'unknown' early trial must not corrupt best_score; got {summary['best_score']!r}"
    )
    assert summary["best_algorithm"] == "IALS"
    assert summary["n_completed"] == 3


def test_progress_reporter_unknown_algorithm_with_score_can_win() -> None:
    """If 'unknown' has the highest score it must still become best_algorithm."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(n_trials=2) as rep:
            rep.on_trial_done(0, "unknown", 0.99, {})
            rep.on_trial_done(1, "TopPop", 0.50, {})

    summary = next(e for e in cap if e.get("event") == "tuning_complete")
    assert summary["best_score"] == 0.99
    assert summary["best_algorithm"] == "unknown"


# ---------------------------------------------------------------------------
# verbose=True adds params field
# ---------------------------------------------------------------------------


def test_progress_reporter_verbose_includes_params_in_trial_done() -> None:
    """With verbose=True, trial_done events must include the params field."""
    params = {"n_components": 32, "alpha": 0.01}

    with structlog.testing.capture_logs() as cap:
        with _make_reporter(verbose=True) as rep:
            rep.on_trial_done(0, "IALS", 0.4, params)

    ev = next(e for e in cap if e.get("event") == "trial_done")
    assert "params" in ev, f"verbose=True must include 'params' field; got {ev!r}"
    assert ev["params"] == params


def test_progress_reporter_non_verbose_excludes_params_from_trial_done() -> None:
    """Without verbose=True, the params field must be absent from trial_done events."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(verbose=False) as rep:
            rep.on_trial_done(0, "IALS", 0.4, {"n_components": 16})

    ev = next(e for e in cap if e.get("event") == "trial_done")
    assert "params" not in ev, (
        f"Non-verbose mode must exclude 'params' from trial_done; got {ev!r}"
    )


# ---------------------------------------------------------------------------
# Completed counter
# ---------------------------------------------------------------------------


def test_progress_reporter_completed_counter_increments_on_every_trial() -> None:
    """n_completed in the summary must equal the number of on_trial_done calls,
    including calls with score=None."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(n_trials=5) as rep:
            for i in range(5):
                score = float(i) * 0.1 if i % 2 == 0 else None
                rep.on_trial_done(i, "TopPop", score, {})

    summary = next(e for e in cap if e.get("event") == "tuning_complete")
    assert summary["n_completed"] == 5


def test_progress_reporter_zero_trials_emits_empty_summary() -> None:
    """Reporter used as a context manager with no trials must emit a valid summary."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(n_trials=0):
            pass  # no trials

    summary = next(e for e in cap if e.get("event") == "tuning_complete")
    assert summary["n_completed"] == 0
    assert summary["best_score"] is None
    assert summary["best_algorithm"] == ""


# ---------------------------------------------------------------------------
# Multiple trial_done events in correct order
# ---------------------------------------------------------------------------


def test_progress_reporter_emits_one_event_per_trial() -> None:
    """Exactly N trial_done events must be emitted for N on_trial_done calls."""
    n = 4

    with structlog.testing.capture_logs() as cap:
        with _make_reporter(n_trials=n) as rep:
            for i in range(n):
                rep.on_trial_done(i, "TopPop", 0.1 * (i + 1), {})

    trial_events = [e for e in cap if e.get("event") == "trial_done"]
    assert len(trial_events) == n, (
        f"Expected {n} trial_done events, got {len(trial_events)}"
    )
    # Verify trial numbers are in order.
    assert [e["trial"] for e in trial_events] == list(range(n))


# ---------------------------------------------------------------------------
# sil m-7: tuning_aborted on error exit; tuning_complete only on success
# ---------------------------------------------------------------------------


def test_progress_reporter_error_exit_emits_tuning_aborted() -> None:
    """When __exit__ is called with an exception, 'tuning_aborted' must be emitted
    instead of 'tuning_complete'."""
    reporter = _make_reporter(n_trials=3)
    with structlog.testing.capture_logs() as cap:
        try:
            with reporter as rep:
                rep.on_trial_done(0, "TopPop", 0.4, {})
                raise RuntimeError("search failed")
        except RuntimeError:
            pass

    complete_events = [e for e in cap if e.get("event") == "tuning_complete"]
    aborted_events = [e for e in cap if e.get("event") == "tuning_aborted"]

    assert not complete_events, (
        f"'tuning_complete' must NOT be emitted on error exit; got {complete_events!r}"
    )
    assert len(aborted_events) == 1, (
        f"Expected exactly 1 'tuning_aborted' event; got {aborted_events!r}"
    )


def test_progress_reporter_error_exit_aborted_has_recipe_run_id() -> None:
    """tuning_aborted must carry recipe and run_id for correlation."""
    reporter = _make_reporter(n_trials=2, recipe_name="news_recs", run_id="err-run-1")
    with structlog.testing.capture_logs() as cap:
        try:
            with reporter:
                raise ValueError("unexpected")
        except ValueError:
            pass

    aborted = next(e for e in cap if e.get("event") == "tuning_aborted")
    assert aborted["recipe"] == "news_recs"
    assert aborted["run_id"] == "err-run-1"
    assert aborted["best_score"] is None


def test_progress_reporter_error_exit_aborted_has_trials_done() -> None:
    """tuning_aborted.trials_done must equal the number of completed trials."""
    reporter = _make_reporter(n_trials=5)
    with structlog.testing.capture_logs() as cap:
        try:
            with reporter as rep:
                rep.on_trial_done(0, "TopPop", 0.1, {})
                rep.on_trial_done(1, "IALS", 0.3, {})
                raise SearchError("no budget")
        except Exception:
            pass

    aborted = next(e for e in cap if e.get("event") == "tuning_aborted")
    assert aborted["trials_done"] == 2, (
        f"Expected trials_done=2; got {aborted['trials_done']!r}"
    )


def test_progress_reporter_success_exit_emits_tuning_complete_not_aborted() -> None:
    """Normal (no-exception) exit must emit 'tuning_complete' and NOT 'tuning_aborted'."""
    with structlog.testing.capture_logs() as cap:
        with _make_reporter(n_trials=1) as rep:
            rep.on_trial_done(0, "TopPop", 0.5, {})

    complete_events = [e for e in cap if e.get("event") == "tuning_complete"]
    aborted_events = [e for e in cap if e.get("event") == "tuning_aborted"]

    assert len(complete_events) == 1, (
        f"Expected exactly 1 'tuning_complete' on success; got {complete_events!r}"
    )
    assert not aborted_events, (
        f"'tuning_aborted' must NOT appear on success exit; got {aborted_events!r}"
    )
