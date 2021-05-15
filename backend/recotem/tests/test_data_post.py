import pytest
from django.urls import reverse
from irspack.dataset import MovieLens100KDataManager
from django.test import Client
import pandas as pd
from pandas import testing as pd_testing
from tempfile import NamedTemporaryFile

from recotem.api.models import TrainingData


@pytest.fixture
def ml100k() -> pd.DataFrame:
    return MovieLens100KDataManager().read_interaction()


@pytest.mark.django_db
def test_data_post(client: Client, ml100k: pd.DataFrame):
    project_url = reverse("project-list")
    data_url = reverse("training_data-list")
    resp_failing_project_creation = client.post(
        project_url, dict(name="ml_invalid", user_column="userId", item_column="itemid")
    )
    failing_project_id = resp_failing_project_creation.json()["id"]
    file = NamedTemporaryFile(suffix=".csv")
    ml100k.to_csv(file, index=False)
    file.seek(0)
    resp = client.post(data_url, dict(project=failing_project_id, upload_path=file))
    assert resp.status_code == 400

    resp_successfull_project_creation = client.post(
        project_url, dict(name="ml_valid", user_column="userId", item_column="movieId")
    )
    successfull_project_id = resp_successfull_project_creation.json()["id"]
    file.seek(0)
    resp = client.post(data_url, dict(project=successfull_project_id, upload_path=file))
    assert resp.status_code == 201
    data_created: TrainingData = TrainingData.objects.get(id=successfull_project_id)
    df_uploaded = pd.read_csv(data_created.upload_path, parse_dates=["timestamp"])
    pd_testing.assert_frame_equal(df_uploaded, ml100k)
