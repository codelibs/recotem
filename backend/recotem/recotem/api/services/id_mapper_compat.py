from collections.abc import Iterable

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
