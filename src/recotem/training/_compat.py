"""IDMappedRecommender compatibility wrapper for recotem.training.

This class must be importable at *this exact path* (recotem.training._compat)
so that pickle can reconstruct instances serialised by the trainer.

recotem.serving._compat re-exports it so the allow-list can reference both
fully-qualified names (train time vs. serve time) without duplicating logic.

NOTE: This module applies a minimal IPython stub before importing irspack to
avoid a hard dependency on IPython (which fastprogress, a transitive dep of
irspack, imports at module level for Jupyter display support).
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from types import ModuleType

# Apply a minimal IPython stub so that fastprogress (transitive irspack dep)
# can be imported without a real IPython installation.  This is safe: the stub
# provides only the display symbols that fastprogress references at import time.
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
    Pickle-safe: reconstructs the transient ``_mapper`` on unpickle.
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
    # Pickle protocol
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
        """
        try:
            return self._mapper.recommend_for_known_user_id(
                self.recommender,
                str(user_id),
                cutoff=cutoff,
            )
        except RuntimeError as exc:
            raise KeyError(str(user_id)) from exc

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
