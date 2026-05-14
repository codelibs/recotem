"""Idempotent IPython stub installer for fastprogress -> IPython.display.

Both `recotem._idmap` (serving import path) and `recotem.training._compat`
(training import path) call this so neither sub-package depends on the other.
Both keys are checked independently so a partial real-IPython install does
not skip the .display stub.
"""

from __future__ import annotations

import sys
import types


def install() -> None:
    """Install minimal IPython and IPython.display stubs if not already present.

    Checks ``"IPython"`` and ``"IPython.display"`` independently so that a
    partial real-IPython installation (where ``IPython`` is importable but
    ``IPython.display`` is not) does not leave fastprogress unable to import.
    """
    if "IPython" not in sys.modules:
        sys.modules["IPython"] = types.ModuleType("IPython")
    if "IPython.display" not in sys.modules:
        display = types.ModuleType("IPython.display")
        display.display = lambda *a, **k: None  # type: ignore[attr-defined]
        display.HTML = str  # type: ignore[attr-defined]
        display.Markdown = str  # type: ignore[attr-defined]
        sys.modules["IPython.display"] = display
