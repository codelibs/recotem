"""IDMappedRecommender for unpickling models trained by the backend.

This is a copy of the backend's id_mapper_compat module, needed because
pickle deserialization requires the class to be importable at the same path.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from types import ModuleType

# Provide a minimal IPython stub for fastprogress (transitive dep of irspack).
# fastprogress imports IPython.display at module level, but IPython is not
# needed for the inference service's use of irspack's IDMapper.
if "IPython" not in sys.modules:
    _ipython = ModuleType("IPython")
    _display = ModuleType("IPython.display")
    _display.display = lambda *a, **kw: None  # type: ignore[attr-defined]
    _display.HTML = str  # type: ignore[attr-defined]
    _display.Markdown = str  # type: ignore[attr-defined]
    _ipython.display = _display  # type: ignore[attr-defined]
    sys.modules["IPython"] = _ipython
    sys.modules["IPython.display"] = _display

from irspack.utils.id_mapping import IDMapper


class IDMappedRecommender:
    """Backward-compatible wrapper around irspack 0.4 IDMapper API."""

    def __init__(self, recommender, user_ids: Iterable[str], item_ids: Iterable[str]):
        self.recommender = recommender
        self.user_ids: list[str] = [str(u) for u in user_ids]
        self.item_ids: list[str] = [str(i) for i in item_ids]
        self._mapper = IDMapper(self.user_ids, self.item_ids)

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.user_ids = [str(u) for u in self.user_ids]
        self.item_ids = [str(i) for i in self.item_ids]
        self._mapper = IDMapper(self.user_ids, self.item_ids)

    def __getstate__(self):
        state = dict(self.__dict__)
        state.pop("_mapper", None)
        return state

    def get_recommendation_for_known_user_id(
        self, user_id: str, cutoff: int = 20
    ) -> list[tuple[str, float]]:
        try:
            return self._mapper.recommend_for_known_user_id(
                self.recommender,
                str(user_id),
                cutoff=cutoff,
            )
        except RuntimeError as e:
            raise KeyError(str(user_id)) from e

    def get_recommendation_for_new_user(
        self, item_ids: Iterable[str], cutoff: int = 20
    ) -> list[tuple[str, float]]:
        return self._mapper.recommend_for_new_user(
            self.recommender,
            [str(item_id) for item_id in item_ids],
            cutoff=cutoff,
        )
