import gzip
import pickle
from tempfile import NamedTemporaryFile
from time import sleep
from typing import IO, Optional

import pandas as pd
import pytest
from django.test import Client

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

    data_id = client.post(data_url, dict(project=project_id, file=pkl_file)).json()[
        "id"
    ]

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

    best_config: Optional[int] = None
    for _ in range(30):
        job = client.get(f"{parameter_tuning_job_url}{job_id}/").json()
        best_config = job["best_config"]
        if best_config is not None:
            assert job["irspack_version"] is not None
            break
        sleep(1.0)
    assert best_config is not None

    model_id = client.post(
        model_url,
        dict(
            configuration=best_config,
            data_loc=data_id,
        ),
    ).json()["id"]

    model_response = None
    for _ in range(30):
        model_response = client.get(f"{model_url}{model_id}/").json()
        filesize = model_response["filesize"]
        if filesize is not None:
            assert model_response["irspack_version"] is not None
            break
        sleep(1.0)
    assert model_response["filesize"] is not None

    download_response = client.get(f"{model_url}{model_id}/download_file/", stream=True)
    with NamedTemporaryFile() as temp_ofs:
        for chunk in download_response.streaming_content:
            temp_ofs.write(chunk)
        temp_ofs.seek(0)
        result = pickle.load(temp_ofs)
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

    tuned_model_id: Optional[int] = None
    for _ in range(20):
        job_ = client.get(
            f"{parameter_tuning_job_url}{job_with_train_afterward_id}/"
        ).json()
        tuned_model_id = job_["tuned_model"]
        if tuned_model_id is not None:
            assert job_["irspack_version"] is not None
            break
        sleep(1.0)
    assert tuned_model_id is not None

    download_response_tuned_model = client.get(
        f"{model_url}{tuned_model_id}/download_file/", stream=True
    )
    with NamedTemporaryFile() as temp_ofs:
        for chunk in download_response_tuned_model.streaming_content:
            temp_ofs.write(chunk)
        temp_ofs.seek(0)
        result = pickle.load(temp_ofs)
        assert "id_mapped_recommender" in result
        assert "irspack_version" in result
