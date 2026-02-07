import json
import pickle
import tempfile

from django.core.files.storage import default_storage
from irspack import IDMappedRecommender
from irspack import __version__ as irspack_version
from irspack.recommenders.base import get_recommender_class
from irspack.utils import df_to_sparse

from recotem.api.models import (
    ModelConfiguration,
    TrainedModel,
    TrainingData,
)

# NOTE: pickle is required for irspack model serialization.
# IDMappedRecommender contains scipy sparse matrices and numpy arrays.


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

    X, uids, iids = df_to_sparse(data.validate_return_df(), user_column, item_column)
    uids = [str(uid) for uid in uids]
    iids = [str(iid) for iid in iids]

    model.irspack_version = irspack_version

    param = json.loads(model_config.parameters_json)
    rec = recommender_class(X, **param).learn()
    with tempfile.TemporaryFile() as temp_fs:
        mapped_rec = IDMappedRecommender(rec, uids, iids)
        pickle.dump(  # noqa: S301
            dict(
                id_mapped_recommender=mapped_rec,
                irspack_version=irspack_version,
                recotem_trained_model_id=model.id,
            ),
            temp_fs,
        )
        temp_fs.seek(0)
        file_ = default_storage.save(f"trained_models/model-{model.id}.pkl", temp_fs)
        model.file = file_
        model.save()

    model.filesize = model.file.size
    model.save()

    return model
