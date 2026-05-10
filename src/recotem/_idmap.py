"""Neutral package-level home for IDMappedRecommender.

This module is the canonical location for ``IDMappedRecommender`` so that
pickled artifacts record a FQCN (``recotem._idmap.IDMappedRecommender``) that
is independent of whether the class was instantiated by the training or serving
sub-package.

Why this module exists
-----------------------
``recotem.training`` and ``recotem.serving`` must never import each other
(CLAUDE.md architecture constraint).  Previously ``IDMappedRecommender`` was
defined in ``recotem.training._compat`` and re-exported from
``recotem.serving._compat``, causing a cross-package import violation when the
serving package imported the training package.

By defining the class here (under ``recotem.*`` -- no sub-package), both
training and serving can import from this neutral location without violating
the boundary.  The IPython stub that must run *before* the first irspack
import is still installed in ``recotem.training._compat``, which is imported
first by the training sub-package.

FQCN allow-list note
--------------------
``recotem.artifact.signing._ALLOWED_CLASSES`` contains
``("recotem._idmap", "IDMappedRecommender")``.  Artifacts pickled before this
commit (which recorded ``recotem.training._compat``) cannot be loaded after
this change -- this is intentional for the 2.0.0a0 pre-release.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from types import ModuleType

# IPython stub: install before any irspack import.  Irspack pulls in fastprogress
# at import time, which in turn imports IPython.display.  The stub provides only
# the display symbols that fastprogress references and is idempotent.
# `recotem.training._compat` installs the same stub for callers that go through
# the training sub-package, but importing `_idmap` directly (e.g. from serving)
# must also work, so we self-bootstrap here.
if "IPython" not in sys.modules:
    _ipython = ModuleType("IPython")
    _display = ModuleType("IPython.display")
    _display.display = lambda *a, **kw: None  # type: ignore[attr-defined]
    _display.HTML = str  # type: ignore[attr-defined]
    _display.Markdown = str  # type: ignore[attr-defined]
    _ipython.display = _display  # type: ignore[attr-defined]
    sys.modules["IPython"] = _ipython
    sys.modules["IPython.display"] = _display

from irspack.utils.id_mapping import IDMapper  # noqa: E402


class IDMappedRecommender:
    """String-keyed recommender wrapper around irspack IDMapper.

    Wraps any trained irspack recommender and exposes user/item IDs as strings.
    Reconstructs the transient ``_mapper`` on unpickle via __setstate__.

    The class is defined here (``recotem._idmap``) rather than in
    ``recotem.training._compat`` so that the recorded FQCN is not tied
    to either the training or the serving sub-package.
    """

    def __init__(
        self,
        recommender: object,
        user_ids: Iterable[str],
        item_ids: Iterable[str],
    ) -> None:
        self.recommender = recommender
        self.user_ids: list[str] = [str(u) for u in user_ids]
        self.item_ids: list[str] = [str(i) for i in item_ids]
        self._mapper: IDMapper = IDMapper(self.user_ids, self.item_ids)

    # ------------------------------------------------------------------
    # State protocol (used by the artifact serializer)
    # ------------------------------------------------------------------

    def __getstate__(self) -> dict:
        state = dict(self.__dict__)
        state.pop("_mapper", None)
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self.user_ids = [str(u) for u in self.user_ids]
        self.item_ids = [str(i) for i in self.item_ids]
        self._mapper = IDMapper(self.user_ids, self.item_ids)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_recommendation_for_known_user_id(
        self,
        user_id: str,
        cutoff: int = 20,
    ) -> list[tuple[str, float]]:
        """Return top-*cutoff* (item_id, score) pairs for a known user.

        Raises
        ------
        KeyError
            If *user_id* was not in the training set.
        RuntimeError
            If the underlying recommender raises an internal error (propagated
            so it surfaces as a 500 rather than being masked as a 404).
        """
        uid = str(user_id)
        if uid not in self._mapper.user_id_to_index:
            raise KeyError(uid)
        return self._mapper.recommend_for_known_user_id(
            self.recommender,
            uid,
            cutoff=cutoff,
        )

    def get_recommendation_for_new_user(
        self,
        item_ids: Iterable[str],
        cutoff: int = 20,
    ) -> list[tuple[str, float]]:
        """Return top-*cutoff* (item_id, score) pairs for a cold-start user."""
        return self._mapper.recommend_for_new_user(
            self.recommender,
            [str(iid) for iid in item_ids],
            cutoff=cutoff,
        )
