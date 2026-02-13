import json
import logging
import pickle  # noqa: S403
import tempfile
from pathlib import Path

import redis
from django.conf import settings
from django.core.files.storage import default_storage
from irspack import __version__ as irspack_version
from irspack.recommenders.base import get_recommender_class
from irspack.utils import df_to_sparse

from recotem.api.models import (
    ModelConfiguration,
    TrainedModel,
    TrainingData,
)
from recotem.api.services.id_mapper_compat import IDMappedRecommender
from recotem.api.services.pickle_signing import sign_pickle_bytes
from recotem.api.utils import read_dataframe

logger = logging.getLogger(__name__)

# NOTE: pickle is required for irspack model serialization.
# IDMappedRecommender contains scipy sparse matrices and numpy arrays.
# HMAC-SHA256 signing is applied to prevent tampering.


def train_and_save_model(model: TrainedModel) -> TrainedModel:
    """Train a recommender model and save it to storage.

    Returns the updated TrainedModel instance.
    """
    model_config: ModelConfiguration = model.configuration
    data: TrainingData = model.data_loc
    project = data.project

    user_column = project.user_column
    item_column = project.item_column
    recommender_class = get_recommender_class(model_config.recommender_class_name)

    # Skip column validation — already validated at upload time
    df = read_dataframe(Path(data.file.name), data.file)
    X, uids, iids = df_to_sparse(df, user_column, item_column)
    uids = [str(uid) for uid in uids]
    iids = [str(iid) for iid in iids]

    model.irspack_version = irspack_version

    param = model_config.parameters_json
    rec = recommender_class(X, **param).learn()
    with tempfile.TemporaryFile() as temp_fs:
        mapped_rec = IDMappedRecommender(rec, uids, iids)
        payload = pickle.dumps(  # noqa: S301
            dict(
                id_mapped_recommender=mapped_rec,
                irspack_version=irspack_version,
                recotem_trained_model_id=model.id,
            ),
        )
        signed = sign_pickle_bytes(payload)
        temp_fs.write(signed)
        temp_fs.seek(0)
        file_ = default_storage.save(f"trained_models/model-{model.id}.pkl", temp_fs)
        model.file = file_
        model.save()

    model.filesize = model.file.size
    model.save()

    _publish_model_event(model)

    return model


def _publish_model_event(model: TrainedModel) -> None:
    """Publish a model_trained event via Redis Pub/Sub for the inference service."""
    try:
        r = redis.from_url(settings.MODEL_EVENTS_REDIS_URL)
        event = json.dumps(
            {
                "event": "model_trained",
                "model_id": model.id,
                "project_id": model.data_loc.project_id,
            }
        )
        r.publish("recotem:model_events", event)
        r.close()
    except Exception:
        logger.warning("Failed to publish model event for model %d", model.id)
