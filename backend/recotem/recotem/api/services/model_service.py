import io
import pickle  # noqa: S403
from pathlib import Path
from typing import Optional

import pandas as pd
from django.conf import settings
from django.core.cache import cache
from django.db.models.signals import post_delete
from django.dispatch import receiver

from recotem.api.exceptions import ModelLoadError, ResourceNotFoundError
from recotem.api.models import ItemMetaData, Project, TrainedModel
from recotem.api.services.id_mapper_compat import IDMappedRecommender
from recotem.api.services.pickle_signing import verify_and_extract
from recotem.api.utils import read_dataframe

# NOTE: pickle is required here because irspack IDMappedRecommender objects
# contain complex internal state (scipy sparse matrices, numpy arrays) that
# cannot be serialized with JSON or other safe formats.
# HMAC-SHA256 verification is applied before loading.

_CACHE_TIMEOUT = getattr(settings, "MODEL_CACHE_TIMEOUT", 3600)
_MODEL_KEY_PREFIX = "recotem:model:"
_METADATA_KEY_PREFIX = "recotem:metadata:"


class _ModelUnpickler(pickle.Unpickler):
    """Allow loading legacy models pickled with removed irspack class names."""

    def find_class(self, module, name):
        if module == "irspack.utils.id_mapping" and name == "IDMappedRecommender":
            return IDMappedRecommender
        return super().find_class(module, name)


def fetch_mapped_rec(pk: int) -> IDMappedRecommender:
    """Load and cache an IDMappedRecommender from a trained model."""
    cache_key = f"{_MODEL_KEY_PREFIX}{pk}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        model_record = TrainedModel.objects.get(pk=pk)
    except TrainedModel.DoesNotExist:
        raise ResourceNotFoundError(detail=f"Trained model {pk} not found.")
    try:
        with model_record.file.open("rb") as f:
            raw_data = f.read()
        payload = verify_and_extract(raw_data)
        rec = _ModelUnpickler(io.BytesIO(payload)).load()[  # noqa: S301
            "id_mapped_recommender"
        ]
        cache.set(cache_key, rec, _CACHE_TIMEOUT)
        return rec
    except ValueError as e:
        raise ModelLoadError(detail=f"Model {pk} integrity check failed: {e}")
    except (pickle.UnpicklingError, KeyError, EOFError, OSError) as e:
        raise ModelLoadError(detail=f"Could not load model {pk}: {e}")


def fetch_item_metadata(pk: int) -> Optional[pd.DataFrame]:
    """Load and cache item metadata as a DataFrame indexed by item column."""
    cache_key = f"{_METADATA_KEY_PREFIX}{pk}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        model_record: ItemMetaData = ItemMetaData.objects.get(pk=pk)
    except ItemMetaData.DoesNotExist:
        raise ResourceNotFoundError(detail=f"Item metadata {pk} not found.")
    try:
        project: Project = model_record.project
        item_column: str = project.item_column
        df: pd.DataFrame = read_dataframe(
            Path(model_record.file.name), model_record.file
        )
        df[item_column] = [str(x) for x in df[item_column]]
        result = df.drop_duplicates(item_column).set_index(
            model_record.project.item_column
        )
        cache.set(cache_key, result, _CACHE_TIMEOUT)
        return result
    except (TypeError, ValueError, pd.errors.ParserError) as e:
        raise ModelLoadError(detail=f"Could not load item metadata {pk}: {e}")


@receiver(post_delete, sender=TrainedModel)
def clear_model_cache(sender, instance, **kwargs):
    """Evict specific model from cache when deleted."""
    cache.delete(f"{_MODEL_KEY_PREFIX}{instance.pk}")


@receiver(post_delete, sender=ItemMetaData)
def clear_metadata_cache(sender, instance, **kwargs):
    """Evict specific metadata from cache when deleted."""
    cache.delete(f"{_METADATA_KEY_PREFIX}{instance.pk}")
