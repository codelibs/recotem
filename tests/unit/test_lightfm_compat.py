"""LightFM import-time warning suppression.

``lightfm-next`` (the ``bprfm`` extra) emits a module-level ``UserWarning`` when
its extension was compiled without OpenMP -- which is every macOS wheel, because
``setup.py`` disables OpenMP on Darwin.  The warning fires from
``lightfm/_lightfm_fast.py`` at *import* time, and irspack imports ``lightfm``
unconditionally from ``irspack/recommenders/bpr.py``.  So the warning reaches
every recotem entry point that touches irspack, whether or not BPRFM is ever
trained: `recotem train` logs it through the ``py.warnings`` bridge, and the
warnings-as-error test suite fails at collection.

recotem silences that one warning at the same layer that installs the IPython
stub.  These tests run in a subprocess with ``-W error`` because warnings are
raised once per process and the parent pytest process has already imported
irspack by the time any test body runs.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

import pytest

# Probed with ``find_spec`` rather than ``import``: importing lightfm is the
# very thing that emits the warning under test, and this module is collected by
# the warnings-as-error suite.  ``find_spec`` locates the module without
# executing it.
_HAS_LIGHTFM = importlib.util.find_spec("lightfm") is not None

requires_lightfm = pytest.mark.skipif(
    not _HAS_LIGHTFM,
    reason="lightfm is not installed (recotem[bprfm] extra absent)",
)


_OPENMP_MESSAGE = "LightFM was compiled without OpenMP support"


def _run_with_fatal_warning(
    statement: str, message: str
) -> subprocess.CompletedProcess[str]:
    """Run *statement* with only warnings starting with *message* made fatal.

    A blanket ``-W error`` cannot be used: irspack's own import raises a
    ``PydanticDeprecatedSince20`` warning (class-based ``Config``) that has
    nothing to do with this shim, so it would make these tests fail for a
    reason they do not test.  ``-W ignore`` first, then a message-scoped
    ``error`` filter, narrows fatality to exactly the warning under test --
    later ``-W`` options take precedence over earlier ones.
    """
    return subprocess.run(
        [
            sys.executable,
            "-W",
            "ignore",
            "-W",
            f"error:{message}:UserWarning",
            "-c",
            statement,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _import_under_warning_errors(statement: str) -> subprocess.CompletedProcess[str]:
    """Run *statement* with LightFM's OpenMP warning made fatal."""
    return _run_with_fatal_warning(statement, _OPENMP_MESSAGE)


@requires_lightfm
def test_idmap_import_emits_no_lightfm_openmp_warning() -> None:
    """Importing ``recotem._idmap`` must not raise LightFM's OpenMP warning.

    ``recotem._idmap`` is the serving-side entry into irspack, so this is the
    path a `recotem serve` process takes.
    """
    result = _import_under_warning_errors("import recotem._idmap")

    assert result.returncode == 0, (
        f"importing recotem._idmap under -W error failed: stderr={result.stderr!r}"
    )


@requires_lightfm
def test_training_compat_import_emits_no_lightfm_openmp_warning() -> None:
    """Importing ``recotem.training._compat`` must not raise it either.

    This is the path a `recotem train` process takes, and it installs the
    IPython stub independently of ``recotem._idmap``.
    """
    result = _import_under_warning_errors("import recotem.training._compat")

    assert result.returncode == 0, (
        "importing recotem.training._compat under -W error failed: "
        f"stderr={result.stderr!r}"
    )


@requires_lightfm
def test_only_the_openmp_warning_is_silenced() -> None:
    """The filter must be message-scoped, not a blanket ``UserWarning`` mute.

    A blanket filter installed at import time would swallow every later
    ``UserWarning`` in the process -- including irspack's own
    ``n_components >= n_items`` clamp and recotem's runtime warnings -- turning
    a targeted compatibility shim into a silent-failure generator.
    """
    result = _run_with_fatal_warning(
        "import recotem._idmap; "
        "import warnings; "
        "warnings.warn('unrelated user warning', UserWarning)",
        "unrelated user warning",
    )

    assert result.returncode != 0, (
        "an unrelated UserWarning did not reach the interpreter's own filter, "
        "so the lightfm shim is too broad and is muting warnings it was never "
        "meant to touch"
    )
    assert "unrelated user warning" in result.stderr
