"""Canonical exit-code constants and exception-to-exit-code mapping.

Single source of truth for both ``recotem.cli`` and
``recotem.training.pipeline`` so the two paths cannot diverge silently.

Exit code table
---------------
| Code | Constant            | Meaning                                    |
|------|---------------------|--------------------------------------------|
|    0 | _EXIT_SUCCESS       | success                                    |
|    1 | _EXIT_UNKNOWN       | unhandled / unmapped exception             |
|    2 | _EXIT_RECIPE        | RecipeError                                |
|    3 | _EXIT_DATASOURCE    | DataSourceError                            |
|    4 | _EXIT_TRAINING      | TrainingError                              |
|    5 | _EXIT_ARTIFACT      | ArtifactError                              |
|    6 | _EXIT_LOCK_CONTESTED| LockContestedError                         |
|    7 | _EXIT_HTTP_FETCH    | HttpFetchError                             |
|    8 | _EXIT_CONFIG        | ConfigError / missing signing keys         |

Design note on imports
----------------------
All exception-class imports inside ``_map_exception_to_exit`` are deferred to
function-call time to prevent circular imports.  The ``recotem.training``
package ``__init__.py`` imports ``pipeline``, which imports this module, so
any top-level import of ``recotem.training.*`` here would cause a circular
import.  Deferring the imports to inside the function is safe because the
function is only called after all modules are fully initialised.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Exit-code constants (no imports needed — just ints)
# ---------------------------------------------------------------------------

_EXIT_SUCCESS = 0
_EXIT_UNKNOWN = 1
_EXIT_RECIPE = 2
_EXIT_DATASOURCE = 3
_EXIT_TRAINING = 4
_EXIT_ARTIFACT = 5
_EXIT_LOCK_CONTESTED = 6
_EXIT_HTTP_FETCH = 7
_EXIT_CONFIG = 8


def _map_exception_to_exit(exc: BaseException) -> int:  # noqa: C901
    """Map a known exception type to its canonical exit code.

    Checked in priority order so that the most-specific mapping wins when
    exception hierarchies overlap (e.g. a subclass of TrainingError that
    signals a configuration problem maps to _EXIT_CONFIG, not _EXIT_TRAINING).

    All exception-class imports are deferred (inside this function) to avoid
    circular imports at module level — see module docstring for details.

    The ``__cause__`` chain is walked for HttpFetchError so that a
    DataSourceError wrapping an HttpFetchError maps to exit 7 (transient
    network) rather than exit 3 (structural datasource failure).  CronJob
    retry logic distinguishes the two.
    """
    # --- configuration errors (signing_key_missing TrainingError subclass) ---
    try:
        from recotem.training.errors import (
            TrainingError as _TrainingError,  # noqa: PLC0415
        )

        if isinstance(exc, _TrainingError) and getattr(exc, "code", "") in (
            "signing_key_missing",
        ):
            return _EXIT_CONFIG
    except (ImportError, AttributeError):
        pass

    # --- recipe errors ---
    try:
        from recotem.recipe.errors import RecipeError as _RecipeError  # noqa: PLC0415

        if isinstance(exc, _RecipeError):
            return _EXIT_RECIPE
    except ImportError:
        pass

    # --- HTTP fetch errors (checked BEFORE DataSourceError so that a
    # DataSourceError wrapping an HttpFetchError still maps to exit 7).
    # CronJob retry semantics distinguish transient HTTP/SSRF failures (7)
    # from structural data-source failures (3).
    try:
        from recotem._http_fetch import (
            HttpFetchError as _HttpFetchError,  # noqa: PLC0415
        )

        # Walk the __cause__ chain — datasource layers wrap HttpFetchError
        # into DataSourceError via ``raise DataSourceError(...) from exc``.
        cur: BaseException | None = exc
        while cur is not None:
            if isinstance(cur, _HttpFetchError):
                return _EXIT_HTTP_FETCH
            cur = cur.__cause__
    except (ImportError, AttributeError):
        pass

    # --- datasource errors ---
    try:
        from recotem.datasource.base import (
            DataSourceError as _DataSourceError,  # noqa: PLC0415
        )

        if isinstance(exc, _DataSourceError):
            return _EXIT_DATASOURCE
    except ImportError:
        pass

    # --- config errors (must come before ArtifactError so that a signing-key
    # misconfiguration on the serve path exits 8, not 5) ---
    try:
        from recotem.config import ConfigError as _ConfigError  # noqa: PLC0415

        if isinstance(exc, _ConfigError):
            return _EXIT_CONFIG
    except ImportError:
        pass

    # --- artifact errors ---
    try:
        from recotem.artifact.format import (
            ArtifactError as _ArtifactError,  # noqa: PLC0415
        )

        if isinstance(exc, _ArtifactError):
            return _EXIT_ARTIFACT
    except ImportError:
        pass

    # --- lock contested ---
    try:
        from recotem.training.lock import (
            LockContestedError as _LockContestedError,  # noqa: PLC0415
        )

        if isinstance(exc, _LockContestedError):
            return _EXIT_LOCK_CONTESTED
    except (ImportError, AttributeError):
        pass

    # --- general training errors ---
    try:
        from recotem.training.errors import (
            TrainingError as _TrainingError2,  # noqa: PLC0415
        )

        if isinstance(exc, _TrainingError2):
            return _EXIT_TRAINING
    except (ImportError, AttributeError):
        pass

    return _EXIT_UNKNOWN
