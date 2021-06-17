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

from .test_data_post import login_client


@pytest.mark.django_db(transaction=True)
def test_tuning(client: Client, ml100k: pd.DataFrame, celery_worker) -> None:
    login_client(client)
    project_url = "/api/project/"
    data_url = "/api/training_data/"
    split_config_url = "/api/split_config/"
    evaluation_config_url = "/api/evaluation_config/"

    parameter_tuning_job_url = "/api/parameter_tuning_job/"
    model_url = "/api/trained_model/"

    project_resp = client.post(
        project_url,
        dict(
            name=f"ml_project",
            user_column="userId",
            item_column="movieId",
            time_column="timestamp",
        ),
    )
    if project_resp.status_code != 201:
        raise RuntimeError(project_resp.json())

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

    job_response = client.post(
        parameter_tuning_job_url,
        dict(
            data=data_id,
            split=split_id,
            evaluation=evaluation_id,
            n_tasks_parallel=1,
            n_trials=5,
            memory_budget=1,
            timeout_singlestep=5,
        ),
    ).json()
    job_id = job_response["id"]

    best_config: Optional[ModelConfiguration] = None
    for _ in range(100):
        job = ParameterTuningJob.objects.get(id=job_id)
        best_config = job.best_config
        if best_config is not None:
            assert job.irspack_version is not None
            break
        sleep(1.0)
    assert best_config is not None

    model_id = client.post(
        model_url,
        dict(
            configuration=best_config.id,
            data_loc=data_id,
        ),
    ).json()["id"]
    train_model_path: Optional[IO] = None
    for _ in range(100):
        model = TrainedModel.objects.get(id=model_id)
        train_model_path = model.model_path
        if train_model_path.name:
            assert model.irspack_version is not None
            break
        sleep(1.0)
    assert train_model_path is not None
    model = TrainedModel.objects.get(id=model_id)
    result = pickle.load(model.model_path)
    assert "id_mapped_recommender" in result
    assert "irspack_version" in result

    job_with_train_afterward_id = client.post(
        parameter_tuning_job_url,
        dict(
            data=data_id,
            split=split_id,
            evaluation=evaluation_id,
            n_tasks_parallel=1,
            n_trials=3,
            memory_budget=1,
            train_after_tuning=True,
        ),
    ).json()["id"]

    model_after_tuning_job = None
    for _ in range(20):
        job_ = ParameterTuningJob.objects.get(id=job_with_train_afterward_id)
        model_after_tuning_job = job_.tuned_model
        if model_after_tuning_job is not None:
            assert job_.irspack_version is not None
            assert model_after_tuning_job.irspack_version is not None
            break
        sleep(1.0)
    assert model_after_tuning_job is not None

    result = pickle.load(model_after_tuning_job.model_path)
    assert "id_mapped_recommender" in result
    assert "irspack_version" in result
    assert result["recotem_trained_model_id"] == model_after_tuning_job.id
