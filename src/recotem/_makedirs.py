"""``makedirs(..., exist_ok=True)`` that tolerates one transient stat failure.

Neutral home so both ``recotem.artifact.io`` (the artifact write) and
``recotem.training.lock`` (the per-recipe lock directory) call the same rule.
Both create a directory on the artifacts volume, and on a network filesystem
both are exposed to the same CPython behaviour described below.
"""

from __future__ import annotations

import os


def makedirs_exist_ok(dest_dir: str) -> None:
    """``os.makedirs(dest_dir, exist_ok=True)`` that tolerates a stale stat.

    ``os.makedirs(..., exist_ok=True)`` swallows the ``FileExistsError`` from
    its ``mkdir`` only when the *single* ``os.path.isdir()`` call that follows
    returns True.  ``os.path.isdir`` reports False for any ``OSError``, so one
    ``stat`` that cannot answer is enough to re-raise — and on a network
    filesystem that is a routine state to be in, because a handle idled through
    the Optuna search can answer the next metadata call with ``ESTALE``.

    Being *rescued* by the re-check, however, is not routine, and the size of
    that gap is the reason this function is documented so carefully.  A rescue
    needs the two ``os.path.isdir`` calls — the one inside ``os.makedirs`` and
    the one below — to disagree, so the mount has to recover in the microseconds
    between them.  Measured against a real momentary window (an export
    unpublished while the server kept answering, then republished 55 s later),
    calling this function in a tight loop at ~360,000 attempts/second:

        89,729,290 attempts    148,007 re-raises    1 rescue

    The single rescue is the one attempt that straddled the recovery instant;
    it and the return to ``OK`` are 1 ms apart.  Inside the stale window the
    rescue rate is 7e-06.  A training run makes **one** such call, so it is
    rescued only if that call lands on the recovery boundary — the tolerance
    costs one ``stat`` and is worth having, but a run that survives an outage is
    not evidence that it fired.

    ``pathlib.Path.mkdir(parents=True, exist_ok=True)`` has the identical
    shape — ``except OSError: if not exist_ok or not self.is_dir(): raise`` —
    so a caller that used to spell it that way gets the same tolerance by
    calling this function instead.

    Measured on a 3-node cluster with an NFS-backed ``ReadWriteMany`` PVC after
    the file server was restarted: ``os.path.isdir('/artifacts')`` returned
    False, ``os.stat`` on the same path then succeeded with mode ``0o42777``,
    and the artifacts directory was readable throughout.  Because the artifact
    write is the first metadata access after minutes of pure-CPU tuning, the
    stale call lands on it every time: five consecutive training runs were
    discarded, each after a full data fetch, search and final refit, with

        FileExistsError: [Errno 17] File exists: '/artifacts'

    The per-recipe lock reaches the same directory one step earlier, before any
    data is fetched, so it is exposed to the same behaviour and calls the same
    rule.

    ``exist_ok=True`` already declares that an existing directory is the
    expected outcome, so re-checking once before giving up costs one ``stat``
    and keeps the failure semantics: a ``dest_dir`` that is genuinely not a
    directory still raises, because the second check fails too.

    **What this does not fix.**  The re-check rescues an is-a-directory answer
    that is False *momentarily*.  A mount whose handles have gone permanently
    stale — the export was rebuilt or failed over, so its ``fsid`` changed —
    gives no such window: measured on that cluster,
    ``os.path.isdir('/artifacts')`` answered False on 1,198 consecutive calls
    across the life of one pod and never once True, so this function re-raises
    there exactly as the plain spelling does.  Nor do the two spellings even
    fail alike in that state.  ``os.makedirs`` re-raises its own
    ``FileExistsError`` (``[Errno 17]``), because ``os.path.isdir`` swallows
    the ``ESTALE`` and reports False; ``Path.mkdir(parents=True,
    exist_ok=True)`` surfaces ``OSError [Errno 116] Stale file handle``,
    because ``Path.is_dir`` ignores only ENOENT / ENOTDIR / EBADF / ELOOP and
    re-raises everything else.  Both end the run at exit 1 (``_EXIT_UNKNOWN``).

    The tolerance is bounded to the mount *point* as well: when ``dest_dir``
    lies below the mount rather than being the mount point itself, the
    ``mkdir`` crosses into the export and raises ``OSError [Errno 116]``
    instead of ``FileExistsError``, which the ``except`` clause below does not
    catch at all.
    """
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except FileExistsError:
        if not os.path.isdir(dest_dir):
            raise
