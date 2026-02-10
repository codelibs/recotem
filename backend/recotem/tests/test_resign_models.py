"""Tests for the resign_models management command."""

import io

import pytest
from django.core.management import call_command

from recotem.api.services.pickle_signing import (
    sign_pickle_bytes,
    verify_and_extract,
)


@pytest.fixture()
def _override_secret(settings):
    settings.SECRET_KEY = "test-resign-secret-key-for-hmac"
    settings.PICKLE_ALLOW_LEGACY_UNSIGNED = True


@pytest.mark.django_db
@pytest.mark.usefixtures("_override_secret")
class TestResignModels:
    def _create_model_with_file(self, data: bytes):
        """Create a TrainedModel with the given raw file data."""
        from django.core.files.base import ContentFile

        from recotem.api.models import (
            ModelConfiguration,
            Project,
            TrainedModel,
            TrainingData,
        )

        project = Project.objects.create(
            name=f"resign_test_{TrainedModel.objects.count()}",
            user_column="user",
            item_column="item",
        )
        td = TrainingData.objects.create(project=project)
        mc = ModelConfiguration.objects.create(
            name="cfg",
            project=project,
            recommender_class_name="IALSRecommender",
        )
        model = TrainedModel.objects.create(configuration=mc, data_loc=td)
        model.file.save("model.bin", ContentFile(data))
        return model

    def test_signs_unsigned_file(self):
        unsigned_data = b"\x80\x04\x95" + b"x" * 100
        model = self._create_model_with_file(unsigned_data)

        out = io.StringIO()
        call_command("resign_models", stdout=out)

        model.refresh_from_db()
        with model.file.open("rb") as f:
            signed_data = f.read()

        # Should now have valid signature
        payload = verify_and_extract(signed_data)
        assert payload == unsigned_data
        assert "Newly signed:   1" in out.getvalue()

    def test_skips_already_signed(self):
        raw = b"some payload data"
        signed = sign_pickle_bytes(raw)
        model = self._create_model_with_file(signed)

        out = io.StringIO()
        call_command("resign_models", stdout=out)

        assert "Already signed: 1" in out.getvalue()
        assert "Newly signed:   0" in out.getvalue()

        # File should be unchanged
        model.refresh_from_db()
        with model.file.open("rb") as f:
            assert f.read() == signed

    def test_dry_run_does_not_modify(self):
        unsigned_data = b"\x80\x04\x95" + b"y" * 50
        model = self._create_model_with_file(unsigned_data)

        out = io.StringIO()
        call_command("resign_models", "--dry-run", stdout=out)

        assert "would be signed" in out.getvalue()
        assert "Dry run complete" in out.getvalue()

        # File should be unchanged
        model.refresh_from_db()
        with model.file.open("rb") as f:
            assert f.read() == unsigned_data

    def test_empty_file_excluded(self):
        """Models with empty file field are excluded."""
        from recotem.api.models import (
            ModelConfiguration,
            Project,
            TrainedModel,
            TrainingData,
        )

        project = Project.objects.create(
            name="resign_empty", user_column="user", item_column="item"
        )
        td = TrainingData.objects.create(project=project)
        mc = ModelConfiguration.objects.create(
            name="cfg_empty",
            project=project,
            recommender_class_name="IALSRecommender",
        )
        TrainedModel.objects.create(configuration=mc, data_loc=td)  # no file

        out = io.StringIO()
        call_command("resign_models", stdout=out)
        assert "Checking 0 trained model(s)" in out.getvalue()
