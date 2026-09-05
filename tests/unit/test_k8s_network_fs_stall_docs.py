"""``docs/deployment/k8s.md`` recommends an RWX PVC; say what an outage costs.

Serve and train do not degrade the same way when the file server behind that
PVC stops answering.  The watcher stats artifacts on a worker thread under a
wall-clock timeout, so a wedged mount costs it a timeout and the process keeps
serving.  ``write_artifact`` has no equivalent: it is a plain ``mkstemp`` ->
``write`` -> ``fsync`` -> ``os.replace``, and on a hard NFS mount whose server
is gone each of those blocks in the kernel for as long as the server stays
away.  Measured on a live cluster: 16 min 50 s at one millicore, last log line
``final_model_trained``, no error and no exit code.

The doc paragraph quotes four product facts to explain what bounds that stall
and what the operator sees instead of a storage error.  The checks below pin
each of them to the file that actually carries it, so a change to the chart or
to the watcher fails here rather than leaving the paragraph describing a
release that no longer exists.

Deliberately text-scans the chart instead of rendering it.  ``helm`` is
provided by the ``ubuntu-24.04`` runner image rather than by anything this
repository installs, so a helm-gated assertion would turn into a silent skip
the day that image drops it -- in the job that runs on every source PR.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

_K8S_DOC = _ROOT / "docs" / "deployment" / "k8s.md"
_CRONJOB = _ROOT / "helm" / "recotem" / "templates" / "cronjob-train.yaml"
_VALUES = _ROOT / "helm" / "recotem" / "values.yaml"
_WATCHER = _ROOT / "src" / "recotem" / "serving" / "watcher.py"

# The heading the paragraph lives under.  Anything below is only meaningful
# while this section is present.
_SECTION = "#### A network-filesystem outage stalls `train`, and says nothing"

# The train Job's hard kill.  Nothing inside the process ends the stall, so
# this is the only bound on it -- and the doc quotes the number.
_DEADLINE = "activeDeadlineSeconds: 3600"

# What the operator is shown when that deadline fires: the deadline, never the
# file server.
_DEADLINE_REASON = "DeadlineExceeded"

# Why the runs queued behind a stalled one disappear too.
_FORBID_EVENT = "JobAlreadyActive"

# The watcher's escape hatch, and the reason serve survives what train does
# not.  If this event stops being emitted, the "serve keeps answering" half of
# the paragraph is no longer true.
_STAT_TIMEOUT_EVENT = "artifact_stat_timeout"


def _doc() -> str:
    return _K8S_DOC.read_text(encoding="utf-8")


def test_k8s_doc_warns_that_a_network_fs_outage_stalls_train() -> None:
    """The section exists, and names the stall rather than only the recovery."""
    doc = _doc()
    assert _SECTION in doc, (
        f"{_K8S_DOC.relative_to(_ROOT)} recommends a ReadWriteMany PVC for "
        "artifacts but no longer carries the section explaining that a file "
        "server outage blocks `recotem train` in the artifact write with no "
        "error and no exit code."
    )
    for phrase in ("final_model_trained", "Stale file handle", "FailedMount"):
        assert phrase in doc, (
            f"{_K8S_DOC.relative_to(_ROOT)}: the outage section no longer "
            f"quotes {phrase!r}, which is what an operator actually greps for "
            "when a train run goes quiet."
        )


def test_doc_quotes_the_deadline_the_chart_actually_ships() -> None:
    """The only bound on the stall, pinned to the template that sets it."""
    cronjob = _CRONJOB.read_text(encoding="utf-8")
    assert _DEADLINE in cronjob, (
        f"{_CRONJOB.relative_to(_ROOT)} no longer sets {_DEADLINE!r}.  "
        f"{_K8S_DOC.relative_to(_ROOT)} tells operators that value is the only "
        "thing that ends a train run stalled on a dead network filesystem; "
        "update both together."
    )
    doc = _doc()
    assert _DEADLINE in doc and _DEADLINE_REASON in doc, (
        f"{_K8S_DOC.relative_to(_ROOT)}: the outage section must quote both "
        f"{_DEADLINE!r} and the {_DEADLINE_REASON!r} the operator is shown in "
        "its place."
    )


def test_doc_forbid_claim_matches_the_shipped_default() -> None:
    """The suppressed-runs consequence only holds while Forbid is the default."""
    values = _VALUES.read_text(encoding="utf-8")
    assert "concurrencyPolicy: Forbid" in values, (
        f"{_VALUES.relative_to(_ROOT)} no longer defaults concurrencyPolicy to "
        f"Forbid.  {_K8S_DOC.relative_to(_ROOT)} says a single stalled train "
        "run suppresses every scheduled run behind it, which is true only "
        "under Forbid."
    )
    assert _FORBID_EVENT in _doc(), (
        f"{_K8S_DOC.relative_to(_ROOT)}: the outage section no longer names "
        f"the {_FORBID_EVENT!r} event those suppressed runs are logged with."
    )


def test_serve_still_has_the_timeout_that_train_lacks() -> None:
    """The asymmetry the section is built on."""
    watcher = _WATCHER.read_text(encoding="utf-8")
    assert _STAT_TIMEOUT_EVENT in watcher, (
        f"{_WATCHER.relative_to(_ROOT)} no longer emits "
        f"{_STAT_TIMEOUT_EVENT!r}.  {_K8S_DOC.relative_to(_ROOT)} contrasts a "
        "serve process that survives a wedged mount with a train process that "
        "does not; without this timeout both sides block and the section is "
        "wrong about serve."
    )
    assert _STAT_TIMEOUT_EVENT in _doc(), (
        f"{_K8S_DOC.relative_to(_ROOT)}: the outage section must name the "
        "event an operator sees while serve rides out the outage."
    )
