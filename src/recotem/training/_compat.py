"""IPython stub installer for recotem.training.

This module installs a minimal IPython stub *before* any irspack import so
that fastprogress (a transitive dependency of irspack) can be imported without
a real IPython installation.  The stub provides only the display symbols that
fastprogress references at import time.

``IDMappedRecommender`` has been moved to ``recotem._idmap`` (a neutral
package-level module that neither training nor serving imports from each
other's namespace).  ``IDMappedRecommender`` is re-exported here for backward
compatibility with internal callers that import from this path, but new
artifacts are pickled under the ``recotem._idmap`` FQCN.
"""

from __future__ import annotations

# Apply a minimal IPython stub so that fastprogress (transitive irspack dep)
# can be imported without a real IPython installation.  This is safe: the stub
# provides only the display symbols that fastprogress references at import time.
# Both "IPython" and "IPython.display" are checked independently so a partial
# real-IPython install (IPython present but IPython.display absent) is handled.
from recotem._ipython_stub import install as _install_ipython_stub

_install_ipython_stub()

# IDMappedRecommender is now defined in recotem._idmap (neutral location).
# Import it here for internal training-package callers that use this path.
from recotem._idmap import IDMappedRecommender  # noqa: E402, F401

__all__ = ["IDMappedRecommender"]
