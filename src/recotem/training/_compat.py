"""fastprogress / IPython compatibility shims for recotem.training.

This module owns everything recotem has to do to irspack's transitive
fastprogress dependency.  Two things live here:

1. A minimal IPython stub installed *before* any irspack import, so that
   fastprogress can be imported without a real IPython installation.  The
   stub provides only the display symbols that fastprogress references at
   import time.
2. The console progress-bar policy (:func:`suppress_progress_bars`), which
   keeps fastprogress's block-drawing bars off a non-interactive stdout.

``IDMappedRecommender`` has been moved to ``recotem._idmap`` (a neutral
package-level module that neither training nor serving imports from each
other's namespace).  ``IDMappedRecommender`` is re-exported here for backward
compatibility with internal callers that import from this path, but new
artifacts are pickled under the ``recotem._idmap`` FQCN.
"""

from __future__ import annotations

import os
import sys

# Apply a minimal IPython stub so that fastprogress (transitive irspack dep)
# can be imported without a real IPython installation.  This is safe: the stub
# provides only the display symbols that fastprogress references at import time.
# Both "IPython" and "IPython.display" are checked independently so a partial
# real-IPython install (IPython present but IPython.display absent) is handled.
from recotem._ipython_stub import install as _install_ipython_stub

_install_ipython_stub()

# LightFM (optional, via the `bprfm` extra) warns at import time when its
# extension was built without OpenMP, and irspack imports lightfm from
# recommenders/bpr.py.  Installed here as well as in `recotem._idmap` so the
# filter is in place no matter which module reaches irspack first.
from recotem._lightfm_compat import install as _install_lightfm_filter  # noqa: E402

_install_lightfm_filter()

# IDMappedRecommender is now defined in recotem._idmap (neutral location).
# Import it here for internal training-package callers that use this path.
from recotem._idmap import IDMappedRecommender  # noqa: E402, F401

__all__ = [
    "IDMappedRecommender",
    "should_suppress_progress_bars",
    "suppress_progress_bars",
]


def _stdout_is_tty() -> bool:
    """Return ``True`` only when ``sys.stdout`` is an interactive terminal.

    ``sys.stdout`` can be ``None`` (a GUI host with no console) or already
    closed by an embedding process.  Both mean "no terminal is watching",
    which is an answer rather than an error, so they resolve to ``False``.
    """
    stream = sys.stdout
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, ValueError):
        return False


def should_suppress_progress_bars(*, quiet: bool = False) -> bool:
    """Return ``True`` when fastprogress must not draw bars on stdout.

    Any one of three conditions means no human is watching a terminal for a
    carriage-return-overwriting bar:

    * *quiet* — ``--quiet`` asks for per-trial output to be suppressed, and
      irspack draws one bar per early-stopping trial.
    * ``sys.stdout`` is not a TTY — the redirected/piped case, where the bar
      frames are the only thing a ``> train.log`` capture would contain.
    * ``RECOTEM_LOG_FORMAT=json`` was set explicitly — the operator asked for
      a machine-readable run (``compose.yaml`` sets it in the container).
      ``auto`` deliberately does *not* count: it resolves off stderr, and the
      stdout check above already covers every redirect it would imply.
    """
    if quiet:
        return True
    if os.environ.get("RECOTEM_LOG_FORMAT", "").strip().lower() == "json":
        return True
    return not _stdout_is_tty()


def _discard(*_args: object, **_kwargs: object) -> None:
    """Swallow one fastprogress console write.

    Accepts ``print``'s signature because it stands in for both the module's
    bare ``print`` calls and its ``WRITER_FN`` hook.
    """


def suppress_progress_bars(*, quiet: bool = False) -> bool:
    """Silence irspack's fastprogress bars when nobody is watching a terminal.

    Returns ``True`` when suppression was applied, ``False`` when the run is
    interactive and fastprogress was left alone.

    fastprogress cannot be asked politely.  Its own gate,
    ``fastprogress.fastprogress.printing()``, reads
    ``getattr(stdout, 'isatty', False)`` — which yields the *bound method*
    instead of calling it, so the check is truthy on every stream and the
    bars render into pipes and files exactly as they do into a terminal.
    Three module globals have to be set because the writes take three
    different routes out:

    * ``NO_BAR`` is fastprogress's documented off switch and makes
      ``printing()`` return ``False``, which covers the bar frames in
      ``ConsoleProgressBar.on_update`` and the erase line in ``on_iter_end``.
      It is the mechanism most likely to survive an upstream refactor, so it
      is set first and the two below are mop-up for what it does not reach.
    * ``WRITER_FN`` is bound to the builtin ``print`` at fastprogress import
      time, so ``print_and_maybe_save`` (via ``ConsoleMasterBar.write``)
      never consults ``NO_BAR`` at all.
    * ``ConsoleProgressBar.__init__`` probes the terminal encoding with a
      bare ``print(self.fill, end='\\r')`` before any gate is consulted —
      one ``block-char + CR`` per bar constructed, i.e. per trial.  Shadowing
      the module-global ``print`` is the only way to reach it.

    Those four call sites are the whole of what the module writes, so nothing
    but progress-bar output is swallowed.  Idempotent: re-applying the same
    globals is a no-op, mirroring ``recotem._ipython_stub.install``.
    """
    if not should_suppress_progress_bars(quiet=quiet):
        return False

    try:
        from fastprogress import fastprogress as _fp  # noqa: PLC0415
    except ImportError:
        # fastprogress only arrives transitively via irspack; an install
        # without it has no bars to silence.
        return False

    _fp.NO_BAR = True
    _fp.WRITER_FN = _discard
    _fp.print = _discard  # type: ignore[attr-defined]
    return True
