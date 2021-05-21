import gzip
import pickle
from tempfile import NamedTemporaryFile
from time import sleep
from typing import IO, Optional

import pandas as pd
import pytest
from django.test import Client
from django.urls import reverse

from recotem.api.models import (
    EvaluationConfig,
    ModelConfiguration,
    ParameterTuningJob,
    SplitConfig,
    TrainedModel,
    TrainingData,
)
from recotem.api.tasks import start_tuning_job, train_recommender


@pytest.mark.django_db(transaction=True)
def test_tuning(client: Client, ml100k: pd.DataFrame, celery_worker) -> None:
    project_url = reverse("project-list")
    data_url = reverse("training_data-list")
    split_config_url = reverse("split_config-list")
    evaluation_config_url = reverse("evaluation_config-list")
    job_url = reverse("parameter_tuning_job-list")

    project_resp = client.post(
        project_url,
        dict(
            name=f"ml_project",
            user_column="userId",
            item_column="movieId",
            time_column="timestamp",
        ),
    )

    project_id = project_resp.json()["id"]
    pkl_file = NamedTemporaryFile(suffix=f".json.gz")
    pkl_gzip_file = gzip.open(pkl_file, mode="wb")
    ml100k.to_json(pkl_gzip_file)
    pkl_gzip_file.close()
    pkl_file.seek(0)

    data_id = client.post(
        data_url, dict(project=project_id, upload_path=pkl_file)
    ).json()["id"]

    assert client.post(split_config_url, dict(heldout_ratio=1.1)).status_code == 400
    assert client.post(split_config_url, dict(test_user_ratio=-0.1)).status_code == 400
    split_id = client.post(
        split_config_url, dict(test_user_ratio=1.0, hedldout_ratio=0.3)
    ).json()["id"]

    evaluation_id = client.post(
        evaluation_config_url, dict(cutoff=10, target_metric="map")
    ).json()["id"]

    job = ParameterTuningJob.objects.create(
        data=TrainingData.objects.get(id=data_id),
        split=SplitConfig.objects.get(id=split_id),
        evaluation=EvaluationConfig.objects.get(id=evaluation_id),
        n_tasks_parallel=1,
        n_trials=5,
        memory_budget=1,
        random_seed=0,
        timeout_singlestep=5,
    )
    job_id = job.id
    best_config: Optional[ModelConfiguration] = None
    for _ in range(100):
        job = ParameterTuningJob.objects.get(id=job_id)
        best_config = job.best_config
        if best_config is not None:
            break
        sleep(1.0)
    assert best_config is not None
    model_id: int = TrainedModel.objects.create(
        configuration=best_config, data_loc=TrainingData.objects.get(id=data_id)
    ).id
    train_model_path: Optional[IO] = None
    for _ in range(100):
        model = TrainedModel.objects.get(id=model_id)
        train_model_path = model.model_path
        if train_model_path.name:
            break
        sleep(1.0)
    assert train_model_path is not None
    model = TrainedModel.objects.get(id=model_id)
    result = pickle.load(model.model_path)
    assert "id_mapped_recommender" in result
