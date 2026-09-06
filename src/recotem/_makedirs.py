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
    transient ``stat`` failure is enough to re-raise — and on a network
    filesystem that happens routinely: an NFS handle that has been idle while
    the Optuna search ran can answer the first metadata call with ``ESTALE``
    and the next one, microseconds later, correctly.

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
    data is fetched, and used to answer a single stale ``stat`` there with the
    same message mapped to exit 1 (``_EXIT_UNKNOWN``) — a permanent-looking
    crash of recotem on a directory that is present and readable.

    ``exist_ok=True`` already declares that an existing directory is the
    expected outcome, so re-checking once before giving up costs one ``stat``
    and keeps the failure semantics: a ``dest_dir`` that is genuinely not a
    directory still raises, because the second check fails too.
    """
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except FileExistsError:
        if not os.path.isdir(dest_dir):
            raise
