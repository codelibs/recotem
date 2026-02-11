"""Model loading and LRU caching."""

import io
import logging
import pickle  # noqa: S403
import threading
from collections import OrderedDict
from pathlib import Path

from .config import settings
from .id_mapper_compat import IDMappedRecommender
from .signing import verify_and_extract

logger = logging.getLogger(__name__)


class _ModelUnpickler(pickle.Unpickler):
    """Allow loading models pickled with different module paths."""

    def find_class(self, module, name):
        if name == "IDMappedRecommender":
            return IDMappedRecommender
        return super().find_class(module, name)


class ModelCache:
    """Thread-safe LRU cache for loaded recommendation models."""

    def __init__(self, max_size: int = 10):
        self._cache: OrderedDict[int, IDMappedRecommender] = OrderedDict()
        self._lock = threading.Lock()
        self._max_size = max_size

    def get(self, model_id: int) -> IDMappedRecommender | None:
        with self._lock:
            if model_id in self._cache:
                self._cache.move_to_end(model_id)
                return self._cache[model_id]
        return None

    def put(self, model_id: int, model: IDMappedRecommender) -> None:
        with self._lock:
            if model_id in self._cache:
                self._cache.move_to_end(model_id)
                self._cache[model_id] = model
            else:
                self._cache[model_id] = model
                if len(self._cache) > self._max_size:
                    evicted_id, _ = self._cache.popitem(last=False)
                    logger.info("Evicted model %d from cache (LRU)", evicted_id)

    def remove(self, model_id: int) -> None:
        with self._lock:
            self._cache.pop(model_id, None)

    def loaded_models(self) -> list[int]:
        with self._lock:
            return list(self._cache.keys())

    def size(self) -> int:
        with self._lock:
            return len(self._cache)


model_cache = ModelCache(max_size=settings.inference_max_loaded_models)


def _get_hmac_key() -> bytes:
    return settings.secret_key.encode("utf-8")


def load_model_from_file(file_path: str) -> IDMappedRecommender:
    """Load and verify a model from a file path."""
    path = Path(settings.media_root) / file_path
    if not path.exists():
        raise FileNotFoundError(f"Model file not found: {path}")

    raw_data = path.read_bytes()
    payload = verify_and_extract(
        _get_hmac_key(), raw_data, settings.pickle_allow_legacy_unsigned
    )
    data = _ModelUnpickler(io.BytesIO(payload)).load()  # noqa: S301
    return data["id_mapped_recommender"]


def get_or_load_model(model_id: int, file_path: str) -> IDMappedRecommender:
    """Get a model from cache or load it from disk."""
    cached = model_cache.get(model_id)
    if cached is not None:
        return cached

    model = load_model_from_file(file_path)
    model_cache.put(model_id, model)
    logger.info("Loaded model %d into cache", model_id)
    return model
