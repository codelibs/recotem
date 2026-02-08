from tempfile import NamedTemporaryFile

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django_celery_results.models import TaskResult

from recotem.api.models import (
    EvaluationConfig,
    ItemMetaData,
    ModelConfiguration,
    ParameterTuningJob,
    Project,
    SplitConfig,
    TaskAndParameterJobLink,
    TaskLog,
    TrainedModel,
    TrainingData,
)

User = get_user_model()


@pytest.mark.django_db
def test_split_and_evaluation_configs_are_isolated_per_user(client: Client):
    user_a = User.objects.create_user(username="split_user_a", password="pass")
    user_b = User.objects.create_user(username="split_user_b", password="pass")

    client.force_login(user_a)
    split_id = client.post(
        reverse("split_config-list"),
        {"name": "private_split_a", "scheme": "RG"},
        content_type="application/json",
    ).json()["id"]
    eval_id = client.post(
        reverse("evaluation_config-list"),
        {"name": "private_eval_a", "target_metric": "ndcg"},
        content_type="application/json",
    ).json()["id"]

    client.force_login(user_b)
    split_list = client.get(reverse("split_config-list"))
    eval_list = client.get(reverse("evaluation_config-list"))
    assert split_list.status_code == 200
    assert eval_list.status_code == 200
    assert split_id not in [item["id"] for item in split_list.json()["results"]]
    assert eval_id not in [item["id"] for item in eval_list.json()["results"]]

    assert (
        client.get(reverse("split_config-detail", args=[split_id])).status_code == 404
    )
    assert (
        client.get(reverse("evaluation_config-detail", args=[eval_id])).status_code
        == 404
    )


@pytest.mark.django_db
def test_project_summary_isolated_per_user(client: Client):
    user_a = User.objects.create_user(username="summary_user_a", password="pass")
    user_b = User.objects.create_user(username="summary_user_b", password="pass")

    client.force_login(user_a)
    project_id = client.post(
        reverse("project-list"),
        {
            "name": "private_summary_project",
            "user_column": "userId",
            "item_column": "movieId",
        },
    ).json()["id"]

    client.force_login(user_b)
    resp = client.get(f"/api/v1/project_summary/{project_id}/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_cannot_create_training_data_for_other_users_project(client: Client, ml100k):
    user_a = User.objects.create_user(username="data_user_a", password="pass")
    user_b = User.objects.create_user(username="data_user_b", password="pass")

    client.force_login(user_a)
    project_id = client.post(
        reverse("project-list"),
        {
            "name": "private_data_project",
            "user_column": "userId",
            "item_column": "movieId",
        },
    ).json()["id"]

    csv_file = NamedTemporaryFile(suffix=".csv")
    ml100k.to_csv(csv_file, index=False)
    csv_file.seek(0)

    client.force_login(user_b)
    resp = client.post(
        reverse("training_data-list"),
        {"project": project_id, "file": csv_file},
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_cannot_create_tuning_job_with_foreign_resources(client: Client):
    user_a = User.objects.create_user(username="job_user_a", password="pass")
    user_b = User.objects.create_user(username="job_user_b", password="pass")

    project = Project.objects.create(
        name="private_job_project",
        user_column="userId",
        item_column="movieId",
        owner=user_a,
    )
    data = TrainingData.objects.create(project=project, filesize=1)
    split = SplitConfig.objects.create(name="private_split", created_by=user_a)
    evaluation = EvaluationConfig.objects.create(
        name="private_eval", target_metric="ndcg", created_by=user_a
    )

    client.force_login(user_b)
    resp = client.post(
        reverse("parameter_tuning_job-list"),
        {
            "data": data.id,
            "split": split.id,
            "evaluation": evaluation.id,
            "n_tasks_parallel": 1,
            "n_trials": 1,
            "memory_budget": 512,
        },
        content_type="application/json",
    )
    assert resp.status_code == 400
    detail = resp.json()["error"]["detail"]
    assert "data" in detail
    assert "split" in detail
    assert "evaluation" in detail


@pytest.mark.django_db
def test_cannot_create_trained_model_with_foreign_resources(client: Client):
    user_a = User.objects.create_user(username="model_user_a", password="pass")
    user_b = User.objects.create_user(username="model_user_b", password="pass")

    project = Project.objects.create(
        name="private_model_project",
        user_column="userId",
        item_column="movieId",
        owner=user_a,
    )
    data = TrainingData.objects.create(project=project, filesize=1)
    config = ModelConfiguration.objects.create(
        name="private_config",
        project=project,
        recommender_class_name="TopPopRecommender",
        parameters_json={},
    )

    client.force_login(user_b)
    resp = client.post(
        reverse("trained_model-list"),
        {"configuration": config.id, "data_loc": data.id},
        content_type="application/json",
    )
    assert resp.status_code == 400
    detail = resp.json()["error"]["detail"]
    assert "configuration" in detail or "data_loc" in detail


@pytest.mark.django_db
def test_task_logs_are_isolated_per_user(client: Client):
    user_a = User.objects.create_user(username="log_user_a", password="pass")
    user_b = User.objects.create_user(username="log_user_b", password="pass")

    project = Project.objects.create(
        name="private_log_project",
        user_column="userId",
        item_column="movieId",
        owner=user_a,
    )
    data = TrainingData.objects.create(project=project, filesize=1)
    split = SplitConfig.objects.create(name="split_for_logs", created_by=user_a)
    evaluation = EvaluationConfig.objects.create(
        name="eval_for_logs", target_metric="ndcg", created_by=user_a
    )
    job = ParameterTuningJob.objects.create(
        data=data, split=split, evaluation=evaluation
    )

    task = TaskResult.objects.create(task_id="task-log-1", status="SUCCESS")
    TaskAndParameterJobLink.objects.create(job=job, task=task)
    log = TaskLog.objects.create(task=task, contents="private-log")

    client.force_login(user_a)
    owner_resp = client.get(
        reverse("task_log-list"),
        {"tuning_job_id": job.id},
    )
    assert owner_resp.status_code == 200
    assert log.id in [item["id"] for item in owner_resp.json()["results"]]

    client.force_login(user_b)
    other_resp = client.get(
        reverse("task_log-list"),
        {"tuning_job_id": job.id},
    )
    assert other_resp.status_code == 200
    assert log.id not in [item["id"] for item in other_resp.json()["results"]]
    assert client.get(reverse("task_log-detail", args=[log.id])).status_code == 404


@pytest.mark.django_db
def test_sample_recommendation_metadata_must_belong_to_same_project(client: Client):
    user = User.objects.create_user(username="metadata_user", password="pass")
    client.force_login(user)

    project_a = Project.objects.create(
        name="metadata_project_a",
        owner=user,
        user_column="userId",
        item_column="movieId",
    )
    project_b = Project.objects.create(
        name="metadata_project_b",
        owner=user,
        user_column="userId",
        item_column="movieId",
    )
    data_a = TrainingData.objects.create(project=project_a, filesize=1)
    config_a = ModelConfiguration.objects.create(
        name="metadata_config_a",
        project=project_a,
        recommender_class_name="TopPopRecommender",
        parameters_json={},
    )
    model = TrainedModel.objects.create(configuration=config_a, data_loc=data_a)
    metadata_other_project = ItemMetaData.objects.create(project=project_b, filesize=1)

    response = client.get(
        f"/api/v1/trained_model/{model.id}/sample_recommendation_metadata/{metadata_other_project.id}/"
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_recommendation_endpoint_rejects_invalid_cutoff(client: Client):
    user = User.objects.create_user(username="recommendation_user", password="pass")
    client.force_login(user)

    project = Project.objects.create(
        name="recommendation_project",
        owner=user,
        user_column="userId",
        item_column="movieId",
    )
    data = TrainingData.objects.create(project=project, filesize=1)
    config = ModelConfiguration.objects.create(
        name="recommendation_config",
        project=project,
        recommender_class_name="TopPopRecommender",
        parameters_json={},
    )
    model = TrainedModel.objects.create(configuration=config, data_loc=data)

    response_non_int = client.get(
        f"/api/v1/trained_model/{model.id}/recommendation/",
        {"user_id": "u1", "cutoff": "invalid"},
    )
    assert response_non_int.status_code == 400
    assert response_non_int.json()["detail"] == "cutoff must be an integer."

    response_non_positive = client.get(
        f"/api/v1/trained_model/{model.id}/recommendation/",
        {"user_id": "u1", "cutoff": 0},
    )
    assert response_non_positive.status_code == 400
    assert response_non_positive.json()["detail"] == "cutoff must be >= 1."
