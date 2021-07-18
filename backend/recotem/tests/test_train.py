import gzip
import pickle
from tempfile import NamedTemporaryFile
from time import sleep
from typing import IO, Optional

import numpy as np
import pandas as pd
import pytest
from django.test import Client
from irspack.utils.id_mapping import IDMappedRecommender

from .test_data_post import login_client

RNS = np.random.RandomState(0)


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

    pkl_file.seek(0)
    deleted_data_id = client.post(
        data_url, dict(project=project_id, file=pkl_file)
    ).json()["id"]
    client.delete(f"{data_url}{deleted_data_id}/unlink_file/")

    assert client.post(split_config_url, dict(heldout_ratio=1.1)).status_code == 400
    assert client.post(split_config_url, dict(test_user_ratio=-0.1)).status_code == 400
    split_id = client.post(
        split_config_url, dict(test_user_ratio=1.0, hedldout_ratio=0.3)
    ).json()["id"]

    evaluation_id = client.post(
        evaluation_config_url, dict(cutoff=10, target_metric="map")
    ).json()["id"]

    job_response_unlinked_data = client.post(
        parameter_tuning_job_url,
        dict(
            data=deleted_data_id,
            split=split_id,
            evaluation=evaluation_id,
            n_tasks_parallel=1,
            n_trials=5,
            memory_budget=1,
            timeout_singlestep=5,
        ),
    )
    job_response_unlinked_data.status_code == 400
    assert "has been deleted" in job_response_unlinked_data.json()["data"][0]

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
    )
    job_response.status_code == 201
    job_id = job_response.json()["id"]

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
        download_model = pickle.load(temp_ofs)
        assert "id_mapped_recommender" in download_model
        assert "irspack_version" in download_model
    mapped_rec: IDMappedRecommender = download_model["id_mapped_recommender"]
    for _ in range(5):
        profile_ids = [str(x) for x in RNS.choice(mapped_rec.item_ids, size=10)]
        gt = mapped_rec.get_recommendation_for_new_user(profile_ids, cutoff=10)
        model_response = client.post(
            f"{model_url}{model_id}/recommend_using_profile_interaction/",
            data={"item_ids": profile_ids, "cutoff": 10},
        ).json()
        for i, rec in enumerate(model_response["recommendations"]):
            assert gt[i][0] == rec["item_id"]

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

    uid_set = {str(uid) for uid in ml100k["userId"]}
    iid_set = {str(iid) for iid in ml100k["movieId"]}
    recommendation_response = client.get(
        f"{model_url}{tuned_model_id}/sample_recommendation_raw/"
    )
    assert recommendation_response.status_code == 200
    sample_recommendation = recommendation_response.json()
    sample_user_id = sample_recommendation["user_id"]
    assert sample_user_id in uid_set

    profile = sample_recommendation["user_profile"]
    for iid in profile:
        assert iid in iid_set

    recommendations = sample_recommendation["recommendations"]
    for rec in recommendations:
        assert rec["item_id"] in iid_set
