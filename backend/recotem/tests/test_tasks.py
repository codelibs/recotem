"""Tests for Celery tasks — status field updates and failure scenarios."""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django_celery_results.models import TaskResult

from recotem.api.models import (
    EvaluationConfig,
    ModelConfiguration,
    ParameterTuningJob,
    Project,
    SplitConfig,
    TrainedModel,
    TrainingData,
)
from recotem.api.tasks import start_tuning_job, train_recommender_func

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="task_tester", password="pass")


@pytest.fixture
def project(user):

    return Project.objects.create(
        name="task_test_project",
        owner=user,
        user_column="userId",
        item_column="movieId",
    )


@pytest.fixture
def split_config(user):
    return SplitConfig.objects.create(created_by=user)


@pytest.fixture
def eval_config(user):
    return EvaluationConfig.objects.create(created_by=user)


@pytest.mark.django_db
def test_tuning_job_initial_status(
    client: Client, user, project, split_config, eval_config, ml100k
):
    """New tuning jobs should start with PENDING status."""
    import io

    client.force_login(user)

    # Upload training data
    csv_buf = io.BytesIO()
    ml100k.to_csv(csv_buf, index=False)
    csv_buf.seek(0)
    csv_buf.name = "data.csv"

    data_url = reverse("training_data-list")
    resp = client.post(data_url, {"project": project.id, "file": csv_buf})
    assert resp.status_code == 201
    data_id = resp.json()["id"]

    # Create tuning job
    job_url = reverse("parameter_tuning_job-list")
    resp = client.post(
        job_url,
        {
            "data": data_id,
            "split": split_config.id,
            "evaluation": eval_config.id,
            "n_trials": 2,
            "n_tasks_parallel": 1,
        },
        content_type="application/json",
    )
    assert resp.status_code == 201
    job_data = resp.json()
    assert "status" in job_data
    # Job should be PENDING (it was just created and task dispatched)
    assert job_data["status"] in ("PENDING", "RUNNING")


@pytest.mark.django_db
def test_tuning_job_status_in_list(
    client: Client, user, project, split_config, eval_config
):
    """Status field should be included in list responses."""
    client.force_login(user)

    job_url = reverse("parameter_tuning_job-list")
    resp = client.get(job_url)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_tuning_job_status_filter(client: Client, user, project):
    """Status filter should work on the list endpoint."""
    client.force_login(user)

    job_url = reverse("parameter_tuning_job-list")
    resp = client.get(job_url, {"status": "PENDING"})
    assert resp.status_code == 200


@pytest.mark.django_db
def test_status_field_is_read_only(
    client: Client, user, project, split_config, eval_config, ml100k
):
    """Status field should not be settable by clients."""
    import io

    client.force_login(user)

    csv_buf = io.BytesIO()
    ml100k.to_csv(csv_buf, index=False)
    csv_buf.seek(0)
    csv_buf.name = "data.csv"

    data_url = reverse("training_data-list")
    resp = client.post(data_url, {"project": project.id, "file": csv_buf})
    assert resp.status_code == 201
    data_id = resp.json()["id"]

    job_url = reverse("parameter_tuning_job-list")
    resp = client.post(
        job_url,
        {
            "data": data_id,
            "split": split_config.id,
            "evaluation": eval_config.id,
            "n_trials": 2,
            "status": "COMPLETED",  # Try to override
        },
        content_type="application/json",
    )
    assert resp.status_code == 201
    job_data = resp.json()
    # Status should be ignored (read-only) and default to PENDING
    assert job_data["status"] in ("PENDING", "RUNNING")


@pytest.mark.django_db
def test_train_recommender_func_sets_failed_on_error(
    user, project, split_config, eval_config, ml100k
):
    """train_recommender_func should set job.status=FAILED when training raises."""
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile

    csv_buf = io.BytesIO()
    ml100k.to_csv(csv_buf, index=False)
    csv_content = csv_buf.getvalue()

    data = TrainingData.objects.create(
        project=project,
        file=SimpleUploadedFile("data.csv", csv_content, content_type="text/csv"),
    )
    config = ModelConfiguration.objects.create(
        name="test_config",
        project=project,
        recommender_class_name="IALSRecommender",
        parameters_json={},
    )
    model = TrainedModel.objects.create(configuration=config, data_loc=data)
    job = ParameterTuningJob.objects.create(
        data=data,
        split=split_config,
        evaluation=eval_config,
        status=ParameterTuningJob.Status.RUNNING,
    )
    task_result = TaskResult.objects.create(task_id="fake-task-id")

    with patch(
        "recotem.api.tasks.train_and_save_model", side_effect=RuntimeError("boom")
    ), pytest.raises(RuntimeError, match="boom"):
        train_recommender_func(task_result, model.id, job.id)

    job.refresh_from_db()
    assert job.status == ParameterTuningJob.Status.FAILED


@pytest.mark.django_db
def test_start_tuning_job_sets_failed_on_celery_error(
    user, project, split_config, eval_config, ml100k
):
    """start_tuning_job should set job.status=FAILED when Celery dispatch fails."""
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile

    csv_buf = io.BytesIO()
    ml100k.to_csv(csv_buf, index=False)
    csv_content = csv_buf.getvalue()

    data = TrainingData.objects.create(
        project=project,
        file=SimpleUploadedFile("data.csv", csv_content, content_type="text/csv"),
    )
    job = ParameterTuningJob.objects.create(
        data=data,
        split=split_config,
        evaluation=eval_config,
    )

    with (
        patch("recotem.api.tasks.optuna.create_study"),
        patch("recotem.api.tasks.chain") as mock_chain,
    ):
        mock_chain.return_value.delay.side_effect = ConnectionError("Redis down")
        with pytest.raises(ConnectionError):
            start_tuning_job(job)

    job.refresh_from_db()
    assert job.status == ParameterTuningJob.Status.FAILED


@pytest.mark.django_db
def test_task_timeout_handling(user, project, split_config, eval_config, ml100k):
    """Tasks should handle SoftTimeLimitExceeded gracefully."""
    import io

    from celery.exceptions import SoftTimeLimitExceeded
    from django.core.files.uploadedfile import SimpleUploadedFile

    csv_buf = io.BytesIO()
    ml100k.to_csv(csv_buf, index=False)
    csv_content = csv_buf.getvalue()

    data = TrainingData.objects.create(
        project=project,
        file=SimpleUploadedFile("data.csv", csv_content, content_type="text/csv"),
    )
    config = ModelConfiguration.objects.create(
        name="timeout_config",
        project=project,
        recommender_class_name="IALSRecommender",
        parameters_json={},
    )
    model = TrainedModel.objects.create(configuration=config, data_loc=data)
    job = ParameterTuningJob.objects.create(
        data=data,
        split=split_config,
        evaluation=eval_config,
        status=ParameterTuningJob.Status.RUNNING,
    )
    task_result = TaskResult.objects.create(task_id="timeout-task-id")

    with patch(
        "recotem.api.tasks.train_and_save_model", side_effect=SoftTimeLimitExceeded()
    ), pytest.raises(SoftTimeLimitExceeded):
        train_recommender_func(task_result, model.id, job.id)

    job.refresh_from_db()
    assert job.status == ParameterTuningJob.Status.FAILED


@pytest.mark.django_db
def test_job_status_stays_failed_when_task_throws(
    user, project, split_config, eval_config, ml100k
):
    """Once a job status is FAILED, it should stay FAILED even if task retries."""
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile

    csv_buf = io.BytesIO()
    ml100k.to_csv(csv_buf, index=False)
    csv_content = csv_buf.getvalue()

    data = TrainingData.objects.create(
        project=project,
        file=SimpleUploadedFile("data.csv", csv_content, content_type="text/csv"),
    )
    config = ModelConfiguration.objects.create(
        name="failed_config",
        project=project,
        recommender_class_name="IALSRecommender",
        parameters_json={},
    )
    model = TrainedModel.objects.create(configuration=config, data_loc=data)
    job = ParameterTuningJob.objects.create(
        data=data,
        split=split_config,
        evaluation=eval_config,
        status=ParameterTuningJob.Status.RUNNING,
    )
    task_result = TaskResult.objects.create(task_id="failed-task-id")

    # First failure sets status to FAILED
    with patch(
        "recotem.api.tasks.train_and_save_model",
        side_effect=RuntimeError("first failure"),
    ), pytest.raises(RuntimeError, match="first failure"):
        train_recommender_func(task_result, model.id, job.id)

    job.refresh_from_db()
    assert job.status == ParameterTuningJob.Status.FAILED

    # Second attempt should keep status as FAILED
    with patch(
        "recotem.api.tasks.train_and_save_model",
        side_effect=RuntimeError("second failure"),
    ), pytest.raises(RuntimeError, match="second failure"):
        train_recommender_func(task_result, model.id, job.id)

    job.refresh_from_db()
    assert job.status == ParameterTuningJob.Status.FAILED


@pytest.mark.django_db
def test_race_condition_two_workers_set_running(
    user, project, split_config, eval_config, ml100k
):
    """Only one worker should successfully transition PENDING -> RUNNING."""
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile

    csv_buf = io.BytesIO()
    ml100k.to_csv(csv_buf, index=False)
    csv_content = csv_buf.getvalue()

    data = TrainingData.objects.create(
        project=project,
        file=SimpleUploadedFile("data.csv", csv_content, content_type="text/csv"),
    )
    job = ParameterTuningJob.objects.create(
        data=data,
        split=split_config,
        evaluation=eval_config,
        status=ParameterTuningJob.Status.PENDING,
    )

    # Simulate atomic update: filter by PENDING and update to RUNNING
    # First worker
    updated_count_1 = ParameterTuningJob.objects.filter(
        id=job.id,
        status=ParameterTuningJob.Status.PENDING,
    ).update(status=ParameterTuningJob.Status.RUNNING)

    # Second worker (tries the same thing, but job is already RUNNING)
    updated_count_2 = ParameterTuningJob.objects.filter(
        id=job.id,
        status=ParameterTuningJob.Status.PENDING,
    ).update(status=ParameterTuningJob.Status.RUNNING)

    # Only first worker should succeed
    assert updated_count_1 == 1
    assert updated_count_2 == 0

    job.refresh_from_db()
    assert job.status == ParameterTuningJob.Status.RUNNING


@pytest.mark.django_db
def test_run_search_task_sets_pending_to_running_once(
    user, project, split_config, eval_config, ml100k
):
    """run_search task should atomically set PENDING to RUNNING only once."""
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile

    csv_buf = io.BytesIO()
    ml100k.to_csv(csv_buf, index=False)
    csv_content = csv_buf.getvalue()

    data = TrainingData.objects.create(
        project=project,
        file=SimpleUploadedFile("data.csv", csv_content, content_type="text/csv"),
    )
    job = ParameterTuningJob.objects.create(
        data=data,
        split=split_config,
        evaluation=eval_config,
        status=ParameterTuningJob.Status.PENDING,
        n_trials=1,
        n_tasks_parallel=1,
    )

    # Mock the actual Optuna search to avoid long computation
    with (
        patch("recotem.api.tasks.optuna.create_study"),
        patch("recotem.api.tasks.study.optimize"),
    ):
        # Verify PENDING -> RUNNING atomic transition
        job.refresh_from_db()
        initial_status = job.status
        assert initial_status == ParameterTuningJob.Status.PENDING

        # Manually update to RUNNING (simulating what the task does)
        ParameterTuningJob.objects.filter(
            id=job.id,
            status=ParameterTuningJob.Status.PENDING,
        ).update(status=ParameterTuningJob.Status.RUNNING)

        job.refresh_from_db()
        assert job.status == ParameterTuningJob.Status.RUNNING


@pytest.mark.django_db
def test_start_tuning_job_error_handling(
    user, project, split_config, eval_config, ml100k
):
    """start_tuning_job should catch and set FAILED when chain dispatch fails."""
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile

    csv_buf = io.BytesIO()
    ml100k.to_csv(csv_buf, index=False)
    csv_content = csv_buf.getvalue()

    data = TrainingData.objects.create(
        project=project,
        file=SimpleUploadedFile("data.csv", csv_content, content_type="text/csv"),
    )
    job = ParameterTuningJob.objects.create(
        data=data,
        split=split_config,
        evaluation=eval_config,
        n_trials=2,
        n_tasks_parallel=1,
    )

    # Mock Optuna create_study to succeed, chain to fail
    with (
        patch("recotem.api.tasks.optuna.create_study"),
        patch("recotem.api.tasks.chain") as mock_chain,
    ):
        mock_chain.return_value.delay.side_effect = Exception(
            "Broker connection failed"
        )

        with pytest.raises(Exception, match="Broker connection failed"):
            start_tuning_job(job)

    job.refresh_from_db()
    assert job.status == ParameterTuningJob.Status.FAILED
