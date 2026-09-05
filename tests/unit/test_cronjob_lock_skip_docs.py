"""``concurrencyPolicy: Forbid`` bounds the CronJob, not the recipe lock.

Forbid stops the CronJob overlapping itself.  Any *other* trainer on the same
recipe -- the bootstrap Job ``helm/recotem/values.yaml`` tells operators to
create, an out-of-cluster cron, a manual ``recotem train`` -- still collides on
``<output.path>.lock``, and with the chart default ``failOnBusy: false`` the
losing run logs ``recipe_lock_contended_skipping`` at INFO and exits 0.  The
Job is then marked ``Complete`` with ``succeeded: 1`` while no artifact was
written, so alerting on Job success cannot see a model going stale.

``docs/deployment/k8s.md`` used to scope that risk to ``concurrencyPolicy:
Allow``, which reads as "the default is safe".  The checks below pin the
corrected paragraph to the product strings it quotes, so the doc cannot drift
away from the behaviour or quietly regain the old framing.

Text-scans the chart rather than rendering it: ``helm`` comes from the
``ubuntu-24.04`` runner image, not from anything this repository installs, so a
helm-gated assertion would become a silent skip in the job that runs on every
source PR the day that image drops it.
"""

from __future__ import annotations

from pathlib import Path

from recotem._exit_codes import _EXIT_LOCK_CONTESTED

_ROOT = Path(__file__).resolve().parents[2]

_K8S_DOC = _ROOT / "docs" / "deployment" / "k8s.md"
_VALUES = _ROOT / "helm" / "recotem" / "values.yaml"
_PIPELINE = _ROOT / "src" / "recotem" / "training" / "pipeline.py"

# What the losing run logs, and the only string in the output that says the run
# did nothing.  INFO, not WARNING -- the doc has to name it or an operator has
# nothing to grep for.
_SKIP_EVENT = "recipe_lock_contended_skipping"

# The framing the paragraph was corrected away from.  A collision is not
# "impossible" under Forbid; Forbid only excludes one of its sources.
_OLD_CLAIM = "impossible at the K8s layer"

# The procedure that creates a second trainer on the same lock, straight out of
# the chart's own first-install instructions.
_BOOTSTRAP = "--from=cronjob/"


def _doc() -> str:
    return _K8S_DOC.read_text(encoding="utf-8")


def test_doc_does_not_call_a_lock_collision_impossible_under_forbid() -> None:
    """Forbid excludes one source of collision, not the collision."""
    assert _OLD_CLAIM not in _doc(), (
        f"{_K8S_DOC.relative_to(_ROOT)} again says a lock collision is "
        f"{_OLD_CLAIM!r} with concurrencyPolicy: Forbid.  Forbid only stops "
        "the CronJob overlapping itself; the bootstrap Job, an out-of-cluster "
        "cron and a manual `recotem train` all still take the same lock, and "
        "the losing run exits 0 with the Job marked Complete."
    )


def test_doc_names_the_event_a_silently_skipped_run_logs() -> None:
    """The doc's grep target has to be the string the product emits."""
    assert _SKIP_EVENT in _PIPELINE.read_text(encoding="utf-8"), (
        f"{_PIPELINE.relative_to(_ROOT)} no longer logs {_SKIP_EVENT!r}; "
        f"{_K8S_DOC.relative_to(_ROOT)} tells operators to look for it as the "
        "only sign that a `Complete` train Job produced nothing."
    )
    doc = _doc()
    assert _SKIP_EVENT in doc, (
        f"{_K8S_DOC.relative_to(_ROOT)}: the CronJob section must name "
        f"{_SKIP_EVENT!r}, the one line distinguishing a skipped run from a "
        "successful one."
    )
    assert "Complete" in doc and "succeeded" in doc, (
        f"{_K8S_DOC.relative_to(_ROOT)}: the section must say the Job is "
        "marked Complete/succeeded, which is why Job-status alerting misses "
        "this entirely."
    )


def test_doc_points_at_the_bootstrap_job_as_a_real_collision_source() -> None:
    """The chart ships the procedure that produces the second trainer."""
    assert _BOOTSTRAP in _VALUES.read_text(encoding="utf-8"), (
        f"{_VALUES.relative_to(_ROOT)} no longer documents the "
        f"`kubectl create job … {_BOOTSTRAP}<release>-train` bootstrap.  "
        f"{_K8S_DOC.relative_to(_ROOT)} cites it as the collision source an "
        "operator is most likely to create by following the instructions."
    )
    assert "bootstrap" in _doc(), (
        f"{_K8S_DOC.relative_to(_ROOT)}: the section must point at the "
        "bootstrap Job, so the risk reads as something the shipped "
        "instructions produce rather than a hypothetical."
    )


def test_doc_quotes_the_exit_code_fail_on_busy_actually_produces() -> None:
    """The remedy is only useful if the number matches the CLI."""
    assert _EXIT_LOCK_CONTESTED == 6, (
        "the lock-contested exit code moved; "
        f"{_K8S_DOC.relative_to(_ROOT)} quotes 6 as what `--fail-on-busy` "
        "returns so a failing Job is distinguishable from a data error."
    )
    doc = _doc()
    assert "failOnBusy: true" in doc and f"exits {_EXIT_LOCK_CONTESTED}" in doc, (
        f"{_K8S_DOC.relative_to(_ROOT)}: the section must give "
        "`failOnBusy: true` as the remedy and say it exits "
        f"{_EXIT_LOCK_CONTESTED}."
    )
