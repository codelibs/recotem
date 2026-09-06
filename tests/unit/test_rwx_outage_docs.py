"""An RWX outage stalls ``train``; how the run *ends* is not deterministic.

The stall is robust and reproduces every time: the artifact write is a plain
``makedirs`` -> ``mkstemp`` -> ``write`` -> ``fsync`` -> ``os.replace``, and on
a hard NFS mount whose server is gone each of those parks in the kernel,
uninterruptibly, emitting nothing after ``final_model_trained``.

What happens when the server comes back is not.  Two endings have been measured
on the same setup:

* the blocked ``os.makedirs(dest_dir, exist_ok=True)`` returns by raising
  ``[Errno 17] File exists``, which is unmapped and lands on ``exit 1``; or
* it simply returns, the write finishes, and the run exits 0 with the Job
  marked ``SuccessCriteriaMet,Complete`` -- measured at 374 s inside the write,
  94 s of that after the file server returned.

Which one you get turns on whether the NFS client's file handles survived; the
``isdir`` check that ``exist_ok=True`` depends on succeeds in the second case
and not in the first.  The same discriminator shows up on the ``serve`` side,
where the failing run reported ``Stale file handle`` and the recovering one
never got past ``artifact_stat_timeout``.

``docs/deployment/k8s.md`` stated the failing ending as *the* ending -- "On
recovery, ``exit 1``" in the summary table, and a paragraph opening "the run
does not simply resume when storage comes back".  An operator who builds on
that gets a rule that is wrong half the time, and worse, is steered toward
alerting on Job status when neither ending is a reliable signal.

These checks pin the corrected text so it cannot drift back to a single
ending.  They are string assertions on the doc, in the manner of
``test_cronjob_lock_skip_docs.py``: no helm, no cluster, so they run on every
source PR.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_K8S_DOC = _ROOT / "docs" / "deployment" / "k8s.md"
_IO = _ROOT / "src" / "recotem" / "artifact" / "io.py"

# The framing the section was corrected away from: a single, certain ending.
_OLD_TABLE_CLAIM = "On recovery, `exit 1`"
_OLD_PARA_CLAIM = "the run does not simply resume when storage comes back"

# The heading of the section under test, so a failure points at the right place.
_SECTION = "A network-filesystem outage stalls `train`, and says nothing"


def _doc() -> str:
    return _K8S_DOC.read_text(encoding="utf-8")


def test_section_still_exists() -> None:
    """Guard the guards: every assertion below is scoped to this section."""
    assert _SECTION in _doc(), (
        f"{_K8S_DOC.relative_to(_ROOT)} no longer contains the section "
        f"{_SECTION!r}; the checks in this module are asserting against text "
        "that has moved or been deleted and would pass vacuously."
    )


def test_doc_does_not_state_a_single_recovery_ending() -> None:
    """Neither of the two old "it fails on recovery" claims may return."""
    doc = _doc()
    assert _OLD_TABLE_CLAIM not in doc, (
        f"{_K8S_DOC.relative_to(_ROOT)} again states the post-outage ending as "
        f"{_OLD_TABLE_CLAIM!r}.  A run whose mount comes back with its file "
        "handles intact completes and exits 0 -- measured, with the artifact "
        "written and the Job marked Complete.  State both endings."
    )
    assert _OLD_PARA_CLAIM not in doc.lower(), (
        f"{_K8S_DOC.relative_to(_ROOT)} again asserts that the run does not "
        "resume when storage returns.  It sometimes does; that is the whole "
        "point of the correction."
    )


def test_doc_names_both_endings() -> None:
    """Both measured outcomes have to be present, not just the failure."""
    doc = _doc()
    for needle, why in (
        (
            "not deterministic",
            "the section must say outright that the ending is not fixed",
        ),
        (
            "[Errno 17] File exists",
            "the failing ending must keep its verbatim error, which is what an "
            "operator greps for",
        ),
        (
            "RECOTEM_EXIT=1",
            "the failing ending must keep its exit code",
        ),
        (
            "exit 0",
            "the completing ending must be stated, or the doc is back to one outcome",
        ),
    ):
        assert needle in doc, (
            f"{_K8S_DOC.relative_to(_ROOT)}: {why} (missing {needle!r})"
        )


def test_doc_explains_which_ending_you_get() -> None:
    """A reader must be able to tell the two apart, not just know there are two.

    The discriminator is whether the mount's file handles survived, which the
    doc surfaces through the ``serve``-side symptom an operator can actually
    observe.
    """
    doc = _doc()
    assert "handles" in doc, (
        f"{_K8S_DOC.relative_to(_ROOT)}: the section must name what decides "
        "the ending -- whether the NFS client's file handles survived the "
        "outage -- or 'not deterministic' is just a shrug."
    )
    assert "Stale file handle" in doc and "artifact_stat_timeout" in doc, (
        f"{_K8S_DOC.relative_to(_ROOT)}: the section must keep both "
        "`serve`-side symptoms.  Which one appears is the same discriminator "
        "showing up where an operator can see it."
    )


def test_doc_does_not_send_operators_to_alert_on_job_status() -> None:
    """Neither ending is a usable signal; the stall is.

    A completed Job proves nothing about whether the outage happened, and a
    failed one blames a directory rather than the file server.  The doc has to
    point at duration or artifact age instead.
    """
    doc = _doc()
    assert "Do not build an alert on either ending" in doc, (
        f"{_K8S_DOC.relative_to(_ROOT)}: the section must warn against "
        "alerting on the run's outcome.  With two endings, Job status is "
        "actively misleading in both directions."
    )
    assert "trained_at" in doc, (
        f"{_K8S_DOC.relative_to(_ROOT)}: the section must name the signal that "
        "does work -- training-run duration, or artifact `trained_at` age."
    )


def test_makedirs_call_the_doc_blames_still_exists() -> None:
    """The doc names a specific line of product code; keep them in step.

    If the write path stops calling ``os.makedirs(..., exist_ok=True)``, the
    explanation above is stale regardless of which ending it describes.
    """
    doc = _doc()
    assert "os.makedirs(dest_dir, exist_ok=True)" in doc, (
        f"{_K8S_DOC.relative_to(_ROOT)} no longer names the call it explains."
    )
    io_src = _IO.read_text(encoding="utf-8")
    assert "makedirs(dest_dir, exist_ok=True)" in io_src, (
        f"{_IO.relative_to(_ROOT)} no longer contains "
        "`makedirs(dest_dir, exist_ok=True)`, but "
        f"{_K8S_DOC.relative_to(_ROOT)} still explains the outage in terms of "
        "it.  Update the doc, or it describes a code path that is gone."
    )
