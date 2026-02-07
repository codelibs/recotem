import pickle
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd
from django.conf import settings
from django.db.models.signals import post_delete
from django.dispatch import receiver
from irspack import IDMappedRecommender

from recotem.api.exceptions import ModelLoadError, ResourceNotFoundError
from recotem.api.models import ItemMetaData, Project, TrainedModel
from recotem.api.services.data_service import read_dataframe

# NOTE: pickle is required here because irspack IDMappedRecommender objects
# contain complex internal state (scipy sparse matrices, numpy arrays) that
# cannot be serialized with JSON or other safe formats.
# Only models uploaded by authenticated admin users are loaded.

_cache_size = getattr(settings, "MODEL_CACHE_SIZE", 8)


@lru_cache(maxsize=_cache_size)
def fetch_mapped_rec(pk: int) -> IDMappedRecommender:
    """Load and cache an IDMappedRecommender from a trained model."""
    try:
        model_record = TrainedModel.objects.get(pk=pk)
    except TrainedModel.DoesNotExist:
        raise ResourceNotFoundError(detail=f"Trained model {pk} not found.")
    try:
        return pickle.load(model_record.file)["id_mapped_recommender"]  # noqa: S301
    except (pickle.UnpicklingError, KeyError, EOFError, OSError) as e:
        raise ModelLoadError(detail=f"Could not load model {pk}: {e}")


@lru_cache(maxsize=_cache_size)
def fetch_item_metadata(pk: int) -> Optional[pd.DataFrame]:
    """Load and cache item metadata as a DataFrame indexed by item column."""
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
        return df.drop_duplicates(item_column).set_index(
            model_record.project.item_column
        )
    except (TypeError, ValueError, pd.errors.ParserError) as e:
        raise ModelLoadError(detail=f"Could not load item metadata {pk}: {e}")


@receiver(post_delete, sender=TrainedModel)
def clear_model_cache(sender, instance, **kwargs):
    """Clear cached model when a TrainedModel is deleted."""
    fetch_mapped_rec.cache_clear()


@receiver(post_delete, sender=ItemMetaData)
def clear_metadata_cache(sender, instance, **kwargs):
    """Clear cached metadata when ItemMetaData is deleted."""
    fetch_item_metadata.cache_clear()
