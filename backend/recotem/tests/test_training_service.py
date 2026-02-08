"""Unit tests for training_service.py — model training and signing.

NOTE: pickle is required for irspack model serialization. HMAC-SHA256 verification
ensures models cannot be tampered with after training.
"""

import pickle  # noqa: S403

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from recotem.api.models import (
    ModelConfiguration,
    Project,
    TrainedModel,
    TrainingData,
)
from recotem.api.services.pickle_signing import verify_and_extract
from recotem.api.services.training_service import train_and_save_model

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="trainuser", password="testpass123")


@pytest.fixture
def project(user):
    return Project.objects.create(
        name="TrainingProject",
        owner=user,
        user_column="userId",
        item_column="movieId",
    )


@pytest.fixture
def training_data(project):
    """Create minimal training data for recommender training."""
    # Small dataset with explicit interactions
    csv_content = b"userId,movieId\n1,101\n1,102\n1,103\n2,101\n2,104\n3,102\n3,103\n"
    return TrainingData.objects.create(
        project=project,
        file=SimpleUploadedFile("train.csv", csv_content, content_type="text/csv"),
    )


@pytest.fixture
def model_config(project):
    return ModelConfiguration.objects.create(
        name="training_test_config",
        project=project,
        recommender_class_name="TopPopRecommender",
        parameters_json={},
    )


@pytest.mark.django_db
@override_settings(SECRET_KEY="test-secret-key-for-training")
class TestTrainAndSaveModel:
    def test_train_and_save_model_creates_signed_file(
        self, model_config, training_data
    ):
        """train_and_save_model should create a TrainedModel with HMAC-signed file."""
        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
        )

        result = train_and_save_model(model)

        assert result.file is not None
        assert result.file.name.endswith(".pkl")
        assert result.filesize is not None
        assert result.filesize > 0
        assert result.irspack_version is not None

        # Verify file is signed and can be extracted
        with result.file.open("rb") as f:
            raw_data = f.read()

        payload = verify_and_extract(raw_data)
        data = pickle.loads(payload)  # noqa: S301
        assert "id_mapped_recommender" in data
        assert "irspack_version" in data
        assert "recotem_trained_model_id" in data

    def test_train_and_save_model_with_ials(self, project, training_data):
        """train_and_save_model should work with IALSRecommender."""
        config = ModelConfiguration.objects.create(
            name="ials_config",
            project=project,
            recommender_class_name="IALSRecommender",
            parameters_json={"n_components": 2},
        )
        model = TrainedModel.objects.create(
            configuration=config,
            data_loc=training_data,
        )

        result = train_and_save_model(model)

        assert result.file is not None
        assert result.filesize > 0

    def test_train_and_save_model_raises_on_invalid_recommender_class(
        self, project, training_data
    ):
        """train_and_save_model should raise when recommender_class_name is invalid."""
        config = ModelConfiguration.objects.create(
            name="invalid_config",
            project=project,
            recommender_class_name="NonExistentRecommender",
            parameters_json={},
        )
        model = TrainedModel.objects.create(
            configuration=config,
            data_loc=training_data,
        )

        with pytest.raises((ImportError, AttributeError, ValueError)):
            train_and_save_model(model)

    def test_train_and_save_model_raises_on_invalid_params_type(
        self, model_config, training_data
    ):
        """train_and_save_model should raise when parameters_json is not a dict."""
        model_config.parameters_json = ["not", "a", "dict"]
        model_config.save()

        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
        )

        with pytest.raises(TypeError):
            train_and_save_model(model)

    def test_model_file_is_signed_with_hmac(self, model_config, training_data):
        """Verify that the saved model file has HMAC signature prepended."""
        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
        )

        train_and_save_model(model)

        with model.file.open("rb") as f:
            raw_data = f.read()

        # File should be longer than 32 bytes (HMAC signature size)
        assert len(raw_data) > 32

        # verify_and_extract should succeed (no exception)
        payload = verify_and_extract(raw_data)
        assert payload is not None

    def test_model_stores_irspack_version(self, model_config, training_data):
        """train_and_save_model should store the irspack version."""
        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
        )

        result = train_and_save_model(model)

        assert result.irspack_version is not None
        assert isinstance(result.irspack_version, str)
        assert len(result.irspack_version) > 0

    def test_model_stores_correct_filesize(self, model_config, training_data):
        """train_and_save_model should update filesize field after saving."""
        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
        )

        result = train_and_save_model(model)

        # Filesize should match actual file size
        assert result.filesize == result.file.size

    def test_model_id_embedded_in_pickle(self, model_config, training_data):
        """Pickled model should contain the recotem_trained_model_id."""
        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
        )

        result = train_and_save_model(model)

        with result.file.open("rb") as f:
            raw_data = f.read()

        payload = verify_and_extract(raw_data)
        data = pickle.loads(payload)  # noqa: S301

        assert data["recotem_trained_model_id"] == result.id
