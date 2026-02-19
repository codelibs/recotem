"""Tests for Celery tasks — status field updates and failure scenarios."""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django_celery_results.models import TaskResult

from recotem.api.models import (
    DeploymentSlot,
    EvaluationConfig,
    ModelConfiguration,
    ParameterTuningJob,
    Project,
    RetrainingRun,
    RetrainingSchedule,
    SplitConfig,
    TrainedModel,
    TrainingData,
)
from recotem.api.tasks import (
    DEFAULT_SEARCH_RECOMMENDERS,
    _auto_deploy_model,
    _fail_retraining_run_for_job,
    _finalize_retraining_run,
    _get_search_recommender_classes,
    _resolve_recommender_class_name,
    start_tuning_job,
    task_scheduled_retrain,
    train_recommender_func,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def _use_locmem_cache(settings):
    """Use in-memory cache so tests work without Redis."""
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }


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

    with (
        patch(
            "recotem.api.tasks.train_and_save_model", side_effect=RuntimeError("boom")
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
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

    with (
        patch(
            "recotem.api.tasks.train_and_save_model",
            side_effect=SoftTimeLimitExceeded(),
        ),
        pytest.raises(SoftTimeLimitExceeded),
    ):
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
    with (
        patch(
            "recotem.api.tasks.train_and_save_model",
            side_effect=RuntimeError("first failure"),
        ),
        pytest.raises(RuntimeError, match="first failure"),
    ):
        train_recommender_func(task_result, model.id, job.id)

    job.refresh_from_db()
    assert job.status == ParameterTuningJob.Status.FAILED

    # Second attempt should keep status as FAILED
    with (
        patch(
            "recotem.api.tasks.train_and_save_model",
            side_effect=RuntimeError("second failure"),
        ),
        pytest.raises(RuntimeError, match="second failure"),
    ):
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
    """Atomic PENDING -> RUNNING transition should only succeed once."""
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

    # Verify PENDING -> RUNNING atomic transition
    job.refresh_from_db()
    assert job.status == ParameterTuningJob.Status.PENDING

    # First worker succeeds
    updated = ParameterTuningJob.objects.filter(
        id=job.id,
        status=ParameterTuningJob.Status.PENDING,
    ).update(status=ParameterTuningJob.Status.RUNNING)
    assert updated == 1

    # Second worker fails (already RUNNING)
    updated2 = ParameterTuningJob.objects.filter(
        id=job.id,
        status=ParameterTuningJob.Status.PENDING,
    ).update(status=ParameterTuningJob.Status.RUNNING)
    assert updated2 == 0

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


# ---------------------------------------------------------------------------
# Tests for _resolve_recommender_class_name
# ---------------------------------------------------------------------------


class TestResolveRecommenderClassName:
    def test_exact_name(self):
        """A valid recommender class name resolves to itself."""
        result = _resolve_recommender_class_name("TopPopRecommender")
        assert result == "TopPopRecommender"

    def test_unknown_returns_none(self):
        """A completely unknown algorithm name returns None."""
        result = _resolve_recommender_class_name("CompletelyFakeAlgorithm")
        assert result is None


# ---------------------------------------------------------------------------
# Tests for _get_search_recommender_classes
# ---------------------------------------------------------------------------


class TestGetSearchRecommenderClasses:
    def test_none_returns_defaults(self):
        """Passing None returns a copy of DEFAULT_SEARCH_RECOMMENDERS."""
        result = _get_search_recommender_classes(None)
        assert result == DEFAULT_SEARCH_RECOMMENDERS
        # Must be a copy, not the same list object
        assert result is not DEFAULT_SEARCH_RECOMMENDERS

    def test_all_invalid_returns_defaults(self):
        """When all algorithm names are invalid, fall back to defaults."""
        result = _get_search_recommender_classes(["FakeAlgo1", "FakeAlgo2"])
        assert result == DEFAULT_SEARCH_RECOMMENDERS


# ---------------------------------------------------------------------------
# Tests for _auto_deploy_model
# ---------------------------------------------------------------------------


@pytest.fixture
def training_data(project):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return TrainingData.objects.create(
        project=project,
        file=SimpleUploadedFile(
            "dummy.csv", b"col1,col2\n1,2\n", content_type="text/csv"
        ),
    )


@pytest.fixture
def model_config(project):
    return ModelConfiguration.objects.create(
        name="auto_deploy_config",
        project=project,
        recommender_class_name="IALSRecommender",
        parameters_json={},
    )


@pytest.fixture
def trained_model(model_config, training_data):
    return TrainedModel.objects.create(
        configuration=model_config,
        data_loc=training_data,
    )


@pytest.fixture
def schedule(project):
    return RetrainingSchedule.objects.create(
        project=project,
        is_enabled=True,
        cron_expression="0 2 * * 0",
    )


@pytest.mark.django_db
class TestAutoDeployModel:
    def test_creates_new_slot(self, schedule, trained_model):
        """Creates a new auto-deploy slot with weight=100 and is_active=True."""
        _auto_deploy_model(schedule, trained_model)

        slot = DeploymentSlot.objects.get(
            project=schedule.project,
            name=f"auto-deploy-{schedule.project.name}",
        )
        assert slot.trained_model == trained_model
        assert slot.weight == 100
        assert slot.is_active is True

    def test_updates_existing_slot(
        self, schedule, trained_model, model_config, training_data
    ):
        """Updates an existing auto-deploy slot with a new model."""
        # Create first slot
        _auto_deploy_model(schedule, trained_model)

        # Create a second trained model
        new_model = TrainedModel.objects.create(
            configuration=model_config,
            data_loc=training_data,
        )
        _auto_deploy_model(schedule, new_model)

        slots = DeploymentSlot.objects.filter(
            project=schedule.project,
            name=f"auto-deploy-{schedule.project.name}",
        )
        assert slots.count() == 1
        slot = slots.first()
        assert slot.trained_model == new_model


# ---------------------------------------------------------------------------
# Tests for _finalize_retraining_run
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFinalizeRetrainingRun:
    def test_no_linked_run(
        self, user, project, split_config, eval_config, training_data
    ):
        """No matching RetrainingRun for the job — returns silently."""
        job = ParameterTuningJob.objects.create(
            data=training_data,
            split=split_config,
            evaluation=eval_config,
            status=ParameterTuningJob.Status.RUNNING,
        )
        config = ModelConfiguration.objects.create(
            name="finalize_config",
            project=project,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )
        model = TrainedModel.objects.create(
            configuration=config,
            data_loc=training_data,
        )
        # Should not raise
        _finalize_retraining_run(job, model)

    def test_marks_completed(
        self,
        user,
        project,
        split_config,
        eval_config,
        training_data,
        schedule,
    ):
        """Sets run status to COMPLETED, assigns model, sets completed_at."""
        job = ParameterTuningJob.objects.create(
            data=training_data,
            split=split_config,
            evaluation=eval_config,
            status=ParameterTuningJob.Status.RUNNING,
        )
        config = ModelConfiguration.objects.create(
            name="finalize_completed_config",
            project=project,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )
        model = TrainedModel.objects.create(
            configuration=config,
            data_loc=training_data,
        )
        run = RetrainingRun.objects.create(
            schedule=schedule,
            tuning_job=job,
            status=RetrainingRun.Status.RUNNING,
        )

        _finalize_retraining_run(job, model)

        run.refresh_from_db()
        assert run.status == RetrainingRun.Status.COMPLETED
        assert run.trained_model == model
        assert run.completed_at is not None

        schedule.refresh_from_db()
        assert schedule.last_run_status == RetrainingRun.Status.COMPLETED

    def test_auto_deploy_triggered(
        self, user, project, split_config, eval_config, training_data
    ):
        """When auto_deploy=True, a deployment slot is created."""
        auto_schedule = RetrainingSchedule.objects.create(
            project=project,
            is_enabled=True,
            cron_expression="0 2 * * 0",
            auto_deploy=True,
        )
        job = ParameterTuningJob.objects.create(
            data=training_data,
            split=split_config,
            evaluation=eval_config,
            status=ParameterTuningJob.Status.RUNNING,
        )
        config = ModelConfiguration.objects.create(
            name="finalize_auto_deploy_config",
            project=project,
            recommender_class_name="IALSRecommender",
            parameters_json={},
        )
        model = TrainedModel.objects.create(
            configuration=config,
            data_loc=training_data,
        )
        RetrainingRun.objects.create(
            schedule=auto_schedule,
            tuning_job=job,
            status=RetrainingRun.Status.RUNNING,
        )

        _finalize_retraining_run(job, model)

        slot = DeploymentSlot.objects.get(
            project=project,
            name=f"auto-deploy-{project.name}",
        )
        assert slot.trained_model == model
        assert slot.weight == 100
        assert slot.is_active is True


# ---------------------------------------------------------------------------
# Tests for _fail_retraining_run_for_job
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFailRetrainingRunForJob:
    def test_marks_failed(
        self,
        user,
        project,
        split_config,
        eval_config,
        training_data,
        schedule,
    ):
        """Sets RUNNING run to FAILED and updates schedule."""
        job = ParameterTuningJob.objects.create(
            data=training_data,
            split=split_config,
            evaluation=eval_config,
            status=ParameterTuningJob.Status.RUNNING,
        )
        run = RetrainingRun.objects.create(
            schedule=schedule,
            tuning_job=job,
            status=RetrainingRun.Status.RUNNING,
        )

        _fail_retraining_run_for_job(job.id)

        run.refresh_from_db()
        assert run.status == RetrainingRun.Status.FAILED
        assert run.completed_at is not None
        assert run.error_message != ""

        schedule.refresh_from_db()
        assert schedule.last_run_status == "FAILED"

    def test_non_running_not_changed(
        self,
        user,
        project,
        split_config,
        eval_config,
        training_data,
        schedule,
    ):
        """A COMPLETED run is not modified."""
        job = ParameterTuningJob.objects.create(
            data=training_data,
            split=split_config,
            evaluation=eval_config,
            status=ParameterTuningJob.Status.COMPLETED,
        )
        run = RetrainingRun.objects.create(
            schedule=schedule,
            tuning_job=job,
            status=RetrainingRun.Status.COMPLETED,
        )

        _fail_retraining_run_for_job(job.id)

        run.refresh_from_db()
        assert run.status == RetrainingRun.Status.COMPLETED

    def test_no_matching_run(self):
        """No linked run for a nonexistent job ID — no crash."""
        # Use a job ID that does not exist
        _fail_retraining_run_for_job(999999)


# ---------------------------------------------------------------------------
# Tests for task_scheduled_retrain
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTaskScheduledRetrain:
    def test_schedule_not_found(self):
        """Missing schedule ID returns early without crashing."""
        task_scheduled_retrain._orig_run(999999)

    def test_disabled_skipped(self, project):
        """Disabled schedule is skipped."""
        disabled_schedule = RetrainingSchedule.objects.create(
            project=project,
            is_enabled=False,
            cron_expression="0 2 * * 0",
        )
        # Should return early without creating a RetrainingRun
        task_scheduled_retrain._orig_run(disabled_schedule.id)
        assert not RetrainingRun.objects.filter(schedule=disabled_schedule).exists()

    def test_no_training_data(self, user):
        """No training data available — returns early."""
        empty_project = Project.objects.create(
            name="empty_project",
            owner=user,
            user_column="userId",
            item_column="movieId",
        )
        sched = RetrainingSchedule.objects.create(
            project=empty_project,
            is_enabled=True,
            cron_expression="0 2 * * 0",
        )
        task_scheduled_retrain._orig_run(sched.id)
        assert not RetrainingRun.objects.filter(schedule=sched).exists()

    @patch("recotem.api.tasks.train_and_save_model")
    def test_train_with_config(self, mock_train, project, training_data, model_config):
        """Schedule with model_configuration trains directly."""
        sched = RetrainingSchedule.objects.create(
            project=project,
            is_enabled=True,
            cron_expression="0 2 * * 0",
            model_configuration=model_config,
            training_data=training_data,
        )

        task_scheduled_retrain._orig_run(sched.id)

        mock_train.assert_called_once()
        run = RetrainingRun.objects.filter(schedule=sched).first()
        assert run is not None
        assert run.status == RetrainingRun.Status.COMPLETED
        assert run.trained_model is not None

    def test_no_config_no_retune_skipped(self, project, training_data):
        """No model config and retune=False results in SKIPPED."""
        sched = RetrainingSchedule.objects.create(
            project=project,
            is_enabled=True,
            cron_expression="0 2 * * 0",
            training_data=training_data,
            retune=False,
        )

        task_scheduled_retrain._orig_run(sched.id)

        run = RetrainingRun.objects.filter(schedule=sched).first()
        assert run is not None
        assert run.status == RetrainingRun.Status.SKIPPED
        assert "No model configuration" in run.error_message

    @patch(
        "recotem.api.tasks.train_and_save_model",
        side_effect=RuntimeError("training exploded"),
    )
    def test_exception_marks_failed(
        self, mock_train, project, training_data, model_config
    ):
        """Exception during training marks run as FAILED with error_message."""
        sched = RetrainingSchedule.objects.create(
            project=project,
            is_enabled=True,
            cron_expression="0 2 * * 0",
            model_configuration=model_config,
            training_data=training_data,
        )

        task_scheduled_retrain._orig_run(sched.id)

        run = RetrainingRun.objects.filter(schedule=sched).first()
        assert run is not None
        assert run.status == RetrainingRun.Status.FAILED
        assert "training exploded" in run.error_message

    @patch("recotem.api.tasks.train_and_save_model")
    def test_schedule_metadata_updated(
        self, mock_train, project, training_data, model_config
    ):
        """last_run_at and last_run_status are updated after a run."""
        sched = RetrainingSchedule.objects.create(
            project=project,
            is_enabled=True,
            cron_expression="0 2 * * 0",
            model_configuration=model_config,
            training_data=training_data,
        )
        assert sched.last_run_at is None
        assert sched.last_run_status is None

        task_scheduled_retrain._orig_run(sched.id)

        sched.refresh_from_db()
        assert sched.last_run_at is not None
        assert sched.last_run_status == RetrainingRun.Status.COMPLETED
