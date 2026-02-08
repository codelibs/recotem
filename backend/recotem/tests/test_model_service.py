"""Unit tests for model_service.py — caching, signing, and loading.

NOTE: pickle is required for irspack model serialization (scipy sparse matrices,
numpy arrays). HMAC-SHA256 verification prevents tampering. This test file validates
the security mechanisms.
"""

import pickle  # noqa: S403
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile

from recotem.api.exceptions import ModelLoadError, ResourceNotFoundError
from recotem.api.models import (
    ItemMetaData,
    ModelConfiguration,
    Project,
    TrainedModel,
    TrainingData,
)
from recotem.api.services.id_mapper_compat import IDMappedRecommender
from recotem.api.services.model_service import (
    _METADATA_KEY_PREFIX,
    _MODEL_KEY_PREFIX,
    fetch_item_metadata,
    fetch_mapped_rec,
)
from recotem.api.services.pickle_signing import sign_pickle_bytes

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="testuser", password="testpass123")


@pytest.fixture
def project(user):
    return Project.objects.create(
        name="TestProject",
        owner=user,
        user_column="user_id",
        item_column="item_id",
    )


@pytest.fixture
def training_data(project):
    csv_content = b"user_id,item_id\n1,101\n1,102\n2,101\n"
    return TrainingData.objects.create(
        project=project,
        file=SimpleUploadedFile("data.csv", csv_content, content_type="text/csv"),
    )


@pytest.fixture
def model_config(project):
    return ModelConfiguration.objects.create(
        name="test_config",
        project=project,
        recommender_class_name="IALSRecommender",
        parameters_json={"n_components": 10},
    )


@pytest.fixture
def mock_recommender():
    """Create a mock IDMappedRecommender for testing."""
    mock_rec = Mock(spec=IDMappedRecommender)
    mock_rec.n_users = 10
    mock_rec.n_items = 20
    return mock_rec


@pytest.mark.django_db
class TestFetchMappedRec:
    @pytest.fixture(autouse=True)
    def _override_settings(self, settings):
        settings.SECRET_KEY = "test-secret-key-for-model-service"

    def test_load_model_from_file(self, model_config, training_data, mock_recommender):
        """fetch_mapped_rec should load and return a model from storage."""
        cache.clear()

        # Create a signed pickle payload
        payload = pickle.dumps({"id_mapped_recommender": mock_recommender})  # noqa: S301
        signed = sign_pickle_bytes(payload)

        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
            file=SimpleUploadedFile(
                "model.pkl", signed, content_type="application/octet-stream"
            ),
        )

        rec = fetch_mapped_rec(model.pk)
        assert rec is not None
        assert rec.n_users == 10
        assert rec.n_items == 20

    def test_load_model_returns_cached_on_second_call(
        self, model_config, training_data, mock_recommender
    ):
        """Second call should return cached model (no file I/O)."""
        cache.clear()

        payload = pickle.dumps({"id_mapped_recommender": mock_recommender})  # noqa: S301
        signed = sign_pickle_bytes(payload)

        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
            file=SimpleUploadedFile(
                "model.pkl", signed, content_type="application/octet-stream"
            ),
        )

        # First call loads from file
        rec1 = fetch_mapped_rec(model.pk)

        # Mock file open to ensure it's not called again
        with patch.object(model.file, "open") as mock_open:
            rec2 = fetch_mapped_rec(model.pk)
            mock_open.assert_not_called()

        # Should return the same object from cache
        assert rec2 is rec1

    def test_load_model_raises_on_tampered_signature(
        self, model_config, training_data, mock_recommender
    ):
        """fetch_mapped_rec should raise ModelLoadError when signature is invalid."""
        cache.clear()

        payload = pickle.dumps({"id_mapped_recommender": mock_recommender})  # noqa: S301
        signed = sign_pickle_bytes(payload)
        # Tamper with the payload (keep signature intact but change data)
        tampered = signed[:32] + b"TAMPERED" + signed[40:]

        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
            file=SimpleUploadedFile(
                "model.pkl", tampered, content_type="application/octet-stream"
            ),
        )

        with pytest.raises(ModelLoadError, match="integrity check failed"):
            fetch_mapped_rec(model.pk)

    def test_load_model_raises_on_missing_model(self):
        """Raises ResourceNotFoundError for non-existent model."""
        cache.clear()

        with pytest.raises(
            ResourceNotFoundError, match="Trained model 99999 not found"
        ):
            fetch_mapped_rec(99999)

    def test_load_model_raises_on_corrupted_pickle(self, model_config, training_data):
        """fetch_mapped_rec should raise ModelLoadError on corrupted pickle data."""
        cache.clear()

        # Create a file with valid signature but corrupted pickle payload
        corrupted_pickle = b"not a valid pickle at all"
        signed = sign_pickle_bytes(corrupted_pickle)

        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
            file=SimpleUploadedFile(
                "model.pkl", signed, content_type="application/octet-stream"
            ),
        )

        with pytest.raises(ModelLoadError, match="Could not load model"):
            fetch_mapped_rec(model.pk)

    def test_load_model_raises_on_missing_key(self, model_config, training_data):
        """Raises ModelLoadError if pickle misses expected key."""
        cache.clear()

        # Valid pickle but wrong structure
        payload = pickle.dumps({"wrong_key": "wrong_value"})  # noqa: S301
        signed = sign_pickle_bytes(payload)

        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
            file=SimpleUploadedFile(
                "model.pkl", signed, content_type="application/octet-stream"
            ),
        )

        with pytest.raises(ModelLoadError, match="Could not load model"):
            fetch_mapped_rec(model.pk)


@pytest.mark.django_db
class TestCacheInvalidation:
    def test_delete_model_clears_cache(
        self, model_config, training_data, mock_recommender
    ):
        """Deleting a TrainedModel should evict it from cache."""
        cache.clear()

        payload = pickle.dumps({"id_mapped_recommender": mock_recommender})  # noqa: S301
        signed = sign_pickle_bytes(payload)

        model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
            file=SimpleUploadedFile(
                "model.pkl", signed, content_type="application/octet-stream"
            ),
        )

        # Load into cache
        fetch_mapped_rec(model.pk)
        cache_key = f"{_MODEL_KEY_PREFIX}{model.pk}"
        assert cache.get(cache_key) is not None

        # Delete model
        model_pk = model.pk
        model.delete()

        # Cache should be cleared
        assert cache.get(f"{_MODEL_KEY_PREFIX}{model_pk}") is None


@pytest.mark.django_db
class TestFetchItemMetadata:
    def test_load_item_metadata(self, project, user):
        """fetch_item_metadata should load and cache item metadata CSV."""
        cache.clear()

        csv_content = b"item_id,title,genre\n101,Movie A,Action\n102,Movie B,Drama\n"
        meta = ItemMetaData.objects.create(
            project=project,
            file=SimpleUploadedFile("items.csv", csv_content, content_type="text/csv"),
        )

        df = fetch_item_metadata(meta.pk)
        assert df is not None
        assert len(df) == 2
        assert "title" in df.columns
        assert "genre" in df.columns
        assert df.index.name == "item_id"

    def test_load_item_metadata_returns_cached_on_second_call(self, project, user):
        """Second call to fetch_item_metadata should return cached DataFrame."""
        cache.clear()

        csv_content = b"item_id,title\n101,Movie A\n102,Movie B\n"
        meta = ItemMetaData.objects.create(
            project=project,
            file=SimpleUploadedFile("items.csv", csv_content, content_type="text/csv"),
        )

        # First call loads from file
        df1 = fetch_item_metadata(meta.pk)

        # Second call should return from cache
        with patch("recotem.api.services.model_service.read_dataframe") as mock_read:
            df2 = fetch_item_metadata(meta.pk)
            mock_read.assert_not_called()

        # Should be the same cached object
        assert df2 is df1

    def test_load_item_metadata_raises_on_missing(self):
        """Raises ResourceNotFoundError for non-existent metadata."""
        cache.clear()

        with pytest.raises(
            ResourceNotFoundError, match="Item metadata 99999 not found"
        ):
            fetch_item_metadata(99999)

    def test_load_item_metadata_raises_on_corrupted_csv(self, project):
        """fetch_item_metadata should raise ModelLoadError on corrupted CSV."""
        cache.clear()

        corrupted_csv = b"\xff\xfe invalid csv data \x00\x00"
        meta = ItemMetaData.objects.create(
            project=project,
            file=SimpleUploadedFile(
                "items.csv", corrupted_csv, content_type="text/csv"
            ),
        )

        with pytest.raises(ModelLoadError, match="Could not load item metadata"):
            fetch_item_metadata(meta.pk)

    def test_delete_metadata_clears_cache(self, project):
        """Deleting ItemMetaData should evict it from cache."""
        cache.clear()

        csv_content = b"item_id,title\n101,Movie A\n"
        meta = ItemMetaData.objects.create(
            project=project,
            file=SimpleUploadedFile("items.csv", csv_content, content_type="text/csv"),
        )

        # Load into cache
        fetch_item_metadata(meta.pk)
        cache_key = f"{_METADATA_KEY_PREFIX}{meta.pk}"
        assert cache.get(cache_key) is not None

        # Delete metadata
        meta_pk = meta.pk
        meta.delete()

        # Cache should be cleared
        assert cache.get(f"{_METADATA_KEY_PREFIX}{meta_pk}") is None
