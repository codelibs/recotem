"""Idempotent suppression of LightFM's import-time OpenMP warning.

Both `recotem._idmap` (serving import path) and `recotem.training._compat`
(training import path) call this before their first irspack import, so neither
sub-package depends on the other -- the same arrangement as
`recotem._ipython_stub`.

Why this exists
---------------
The `bprfm` extra installs `lightfm-next`, whose ``setup.py`` compiles the
OpenMP extension only on non-Darwin platforms.  On macOS the package therefore
ships only ``_lightfm_fast_no_openmp``, and ``lightfm/_lightfm_fast.py`` emits

    UserWarning: LightFM was compiled without OpenMP support.
    Only a single thread will be used.

at *module import* time.  irspack imports ``lightfm`` unconditionally from
``irspack/recommenders/bpr.py``, so the warning reaches every recotem entry
point that touches irspack -- ``recotem serve``, ``recotem inspect``, and
``recotem train`` for a TopPop recipe alike -- whether or not BPRFM is ever
used.  Left alone it is logged once per process through structlog's
``py.warnings`` bridge, which makes every run look like it has a problem.

The warning is accurate but not actionable: on macOS there is no OpenMP build
to switch to, and single-threaded BPRFM training is the documented macOS
behaviour (see docs/recipe-reference.md#algorithms).  The Linux images build
lightfm from source *with* OpenMP, so the warning never fires there.

The filter is scoped to this one message so that every other ``UserWarning``
-- irspack's ``n_components >= n_items`` clamp, recotem's own runtime warnings
-- still reaches the operator.  ``tests/unit/test_lightfm_compat.py`` pins
that scoping.
"""

from __future__ import annotations

import warnings

# Matched against the start of the warning message.  ``filterwarnings``
# treats this as a regex, so the string must stay free of metacharacters.
_OPENMP_MESSAGE = "LightFM was compiled without OpenMP support"


def install() -> None:
    """Silence LightFM's no-OpenMP import warning.

    Idempotent: ``filterwarnings`` prepends to the filter list, so calling this
    twice leaves a duplicate but harmless entry, mirroring the tolerance of
    ``recotem._ipython_stub.install``.
    """
    warnings.filterwarnings(
        "ignore",
        message=_OPENMP_MESSAGE,
        category=UserWarning,
    )
