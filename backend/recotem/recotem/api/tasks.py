import logging
import random
import re
from pathlib import Path
from typing import Any

import optuna
import pandas as pd
import scipy.sparse as sps
from asgiref.sync import async_to_sync
from celery import chain, group
from celery.exceptions import SoftTimeLimitExceeded
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_celery_results.models import TaskResult
from irspack import Evaluator, split_dataframe_partial_user_holdout
from irspack import __version__ as irspack_version
from irspack.optimization.parameter_range import is_valid_param_name
from irspack.recommenders.base import get_recommender_class
from optuna.samplers import TPESampler
from optuna.trial import FrozenTrial

from recotem.api.models import (
    EvaluationConfig,
    ModelConfiguration,
    ParameterTuningJob,
    Project,
    RetrainingRun,
    RetrainingSchedule,
    SplitConfig,
    TaskAndParameterJobLink,
    TaskAndTrainedModelLink,
    TaskLog,
    TrainedModel,
    TrainingData,
)
from recotem.api.services.training_service import train_and_save_model
from recotem.api.services.tuning_service import get_optuna_storage
from recotem.api.utils import read_dataframe
from recotem.celery import app

logger = logging.getLogger(__name__)


def _send_ws_log(job_id: int, message: str) -> None:
    """Send a log message to the WebSocket group for the given job."""
    try:
        channel_layer = get_channel_layer()
        if channel_layer is not None:
            async_to_sync(channel_layer.group_send)(
                f"job_{job_id}_logs",
                {"type": "task_log_message", "message": message},
            )
    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.warning("Failed to send WebSocket log for job %d: %s", job_id, exc)


def _send_ws_status(job_id: int, status: str, data: dict = None) -> None:
    """Send a status update to the WebSocket group for the given job."""
    try:
        channel_layer = get_channel_layer()
        if channel_layer is not None:
            async_to_sync(channel_layer.group_send)(
                f"job_{job_id}_status",
                {"type": "job_status_update", "status": status, "data": data or {}},
            )
    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.warning("Failed to send WebSocket status for job %d: %s", job_id, exc)


DEFAULT_SEARCH_RECOMMENDERS: list[str] = [
    "IALSRecommender",
    "CosineKNNRecommender",
    "TopPopRecommender",
]


def _resolve_recommender_class_name(algorithm_name: str) -> str | None:
    candidates = [algorithm_name]
    if algorithm_name.endswith("Optimizer"):
        candidates.append(algorithm_name.replace("Optimizer", "Recommender"))
    if not algorithm_name.endswith("Recommender"):
        candidates.append(f"{algorithm_name}Recommender")
    for candidate in candidates:
        try:
            get_recommender_class(candidate)
        except (ImportError, AttributeError, ValueError, KeyError):
            continue
        return candidate
    return None


def _get_search_recommender_classes(raw_algorithms_json: list[str] | None) -> list[str]:
    if raw_algorithms_json is None:
        return DEFAULT_SEARCH_RECOMMENDERS.copy()

    resolved = []
    for algorithm_name in raw_algorithms_json:
        recommender_class_name = _resolve_recommender_class_name(algorithm_name)
        if recommender_class_name is not None:
            resolved.append(recommender_class_name)
    if resolved:
        return resolved
    return DEFAULT_SEARCH_RECOMMENDERS.copy()


def train_recommender_func(
    task_result, model_id: int, parameter_tuning_job_id: int | None = None
):
    model: TrainedModel = TrainedModel.objects.get(id=model_id)
    TaskAndTrainedModelLink.objects.get_or_create(
        task=task_result, defaults={"model": model}
    )

    try:
        train_and_save_model(model)
    except Exception:
        if parameter_tuning_job_id is not None:
            ParameterTuningJob.objects.filter(id=parameter_tuning_job_id).update(
                status=ParameterTuningJob.Status.FAILED
            )
            _send_ws_status(
                parameter_tuning_job_id, "error", {"error": "training failed"}
            )
        raise

    if parameter_tuning_job_id is not None:
        job: ParameterTuningJob = ParameterTuningJob.objects.get(
            id=parameter_tuning_job_id
        )
        job.tuned_model = model
        job.save()


@app.task(
    bind=True,
    time_limit=settings.CELERY_TASK_TIME_LIMIT,
    soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    autoretry_for=(ConnectionError, OSError),
    retry_backoff=True,
    max_retries=3,
)
def task_train_recommender(self, model_id: int) -> None:
    task_result, _ = TaskResult.objects.get_or_create(task_id=self.request.id)
    self.update_state(state="STARTED", meta=[])
    try:
        train_recommender_func(task_result, model_id)
    except SoftTimeLimitExceeded:
        logger.error("Training timed out for model %d", model_id)
        TaskLog.objects.create(
            task=task_result, contents=f"Training timed out for model {model_id}"
        )
        raise
    except Exception as e:
        logger.exception("Training failed for model %d", model_id)
        TaskLog.objects.create(task=task_result, contents=f"Training failed: {e}")
        raise


def create_best_config_fun(task_result, parameter_tuning_job_id: int) -> int:
    with transaction.atomic():
        job: ParameterTuningJob = ParameterTuningJob.objects.select_for_update().get(
            id=parameter_tuning_job_id
        )
        evaluation: EvaluationConfig = job.evaluation

        TaskAndParameterJobLink.objects.get_or_create(
            task=task_result, defaults={"job": job}
        )

        data: TrainingData = job.data
        project: Project = data.project

        optuna_storage = get_optuna_storage()
        study_name = job.study_name()
        study_id = optuna_storage.get_study_id_from_name(study_name)
        best_trial = optuna_storage.get_best_trial(study_id)
        best_params_with_prefix = dict(best_trial.params)
        best_params_with_prefix.update(
            {
                key: val
                for key, val in best_trial.user_attrs.items()
                if is_valid_param_name(key)
            }
        )
        best_params = {
            re.sub(r"^([^\.]*\.)", "", key): value
            for key, value in best_params_with_prefix.items()
        }
        recommender_class_name = best_trial.user_attrs.get("recommender_class_name")
        if recommender_class_name is None:
            recommender_class_name = best_params.pop("recommender_class_name", None)
        if recommender_class_name is None and "optimizer_name" in best_params:
            recommender_class_name = _resolve_recommender_class_name(
                str(best_params.pop("optimizer_name"))
            )
        best_params.pop("optimizer_name", None)
        best_params.pop("recommender_class_name", None)
        if recommender_class_name is None:
            raise RuntimeError("Could not determine recommender class from best trial.")
        # Validate the class exists in current irspack version.
        get_recommender_class(recommender_class_name)

        if best_trial.value is None:
            raise RuntimeError("Best trial has no objective value.")
        best_score = -best_trial.value

        config_name = f"Tuning Result of job {job.id}"
        config = ModelConfiguration.objects.create(
            name=config_name,
            project=project,
            parameters_json=best_params,
            recommender_class_name=recommender_class_name,
        )

        job.best_config = config
        job.irspack_version = irspack_version
        job.best_score = best_score
        job.save()

        if best_score == 0.0:
            job.status = ParameterTuningJob.Status.FAILED
            job.save(update_fields=["status"])
            _send_ws_status(parameter_tuning_job_id, "error", {"error": "zero score"})
            raise RuntimeError(
                f"This settings resulted in {evaluation.target_metric} == 0.0.\n"
                "This might be caused by too short timeout or too small validation set."
            )

        job.status = ParameterTuningJob.Status.COMPLETED
        job.save(update_fields=["status"])

    msg_complete = f"Job {parameter_tuning_job_id} complete."
    TaskLog.objects.create(task=task_result, contents=msg_complete)
    _send_ws_log(parameter_tuning_job_id, msg_complete)

    msg_best = (
        f"Found best configuration: {recommender_class_name}"
        f" / {best_params} with"
        f" {evaluation.target_metric}@{evaluation.cutoff}"
        f" = {best_score}"
    )
    TaskLog.objects.create(task=task_result, contents=msg_best)
    _send_ws_log(parameter_tuning_job_id, msg_best)
    _send_ws_status(parameter_tuning_job_id, "completed", {"best_score": best_score})

    return config.id


@app.task(
    bind=True,
    time_limit=settings.CELERY_TASK_TIME_LIMIT,
    soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    autoretry_for=(ConnectionError, OSError),
    retry_backoff=True,
    max_retries=3,
)
def task_create_best_config(self, parameter_tuning_job_id: int, *args) -> int:
    task_result, _ = TaskResult.objects.get_or_create(task_id=self.request.id)
    self.update_state(state="STARTED", meta=[])
    return create_best_config_fun(task_result, parameter_tuning_job_id)


@app.task(
    bind=True,
    time_limit=settings.CELERY_TASK_TIME_LIMIT,
    soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    autoretry_for=(ConnectionError, OSError),
    retry_backoff=True,
    max_retries=3,
)
def task_create_best_config_train_rec(self, parameter_tuning_job_id: int, *args) -> int:
    task_result, _ = TaskResult.objects.get_or_create(task_id=self.request.id)
    self.update_state(state="STARTED", meta=[])
    config_id = create_best_config_fun(task_result, parameter_tuning_job_id)
    job: ParameterTuningJob = ParameterTuningJob.objects.get(id=parameter_tuning_job_id)
    config: ModelConfiguration = ModelConfiguration.objects.get(id=config_id)
    model = TrainedModel.objects.create(configuration=config, data_loc=job.data)

    train_recommender_func(task_result, model.id, parameter_tuning_job_id)


@app.task(
    bind=True,
    time_limit=settings.CELERY_TASK_TIME_LIMIT,
    soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    autoretry_for=(ConnectionError, OSError),
    retry_backoff=True,
    max_retries=3,
)
def run_search(self, parameter_tuning_job_id: int, index: int) -> None:
    task_result, _ = TaskResult.objects.get_or_create(task_id=self.request.id)
    logs = [
        dict(message=f"Started the parameter tuning job {parameter_tuning_job_id} ")
    ]
    self.update_state(state="STARTED", meta=logs)
    job: ParameterTuningJob = ParameterTuningJob.objects.get(id=parameter_tuning_job_id)

    n_trials: int = job.n_trials // job.n_tasks_parallel

    if index < (job.n_trials % job.n_tasks_parallel):
        n_trials += 1

    TaskAndParameterJobLink.objects.get_or_create(
        task=task_result, defaults={"job": job}
    )
    data: TrainingData = job.data
    project: Project = data.project
    split: SplitConfig = job.split
    evaluation: EvaluationConfig = job.evaluation
    df: pd.DataFrame = read_dataframe(Path(data.file.name), data.file)
    tried_recommenders = _get_search_recommender_classes(job.tried_algorithms_json)
    user_column = project.user_column
    item_column = project.item_column

    # Atomically set RUNNING only if still PENDING (first worker wins)
    ParameterTuningJob.objects.filter(
        id=parameter_tuning_job_id,
        status=ParameterTuningJob.Status.PENDING,
    ).update(status=ParameterTuningJob.Status.RUNNING)

    msg_start = f"Start job {parameter_tuning_job_id} / worker {index}."
    TaskLog.objects.create(task=task_result, contents=msg_start)
    _send_ws_log(parameter_tuning_job_id, msg_start)
    _send_ws_status(parameter_tuning_job_id, "running")

    dataset, _ = split_dataframe_partial_user_holdout(
        df,
        user_column=user_column,
        item_column=item_column,
        time_column=project.time_column,
        n_val_user=split.n_test_users,
        val_user_ratio=split.test_user_ratio,
        test_user_ratio=0.0,
        heldout_ratio_val=split.heldout_ratio,
        n_heldout_val=split.n_heldout,
    )
    train = dataset["train"]
    val = dataset["val"]
    X_tv_train = sps.vstack([train.X_train, val.X_train])
    evaluator = Evaluator(
        val.X_test,
        offset=train.n_users,
        target_metric=evaluation.target_metric.lower(),
        cutoff=evaluation.cutoff,
    )

    study_name = job.study_name()
    optuna_storage = get_optuna_storage()

    log_buffer: list[TaskLog] = []
    BULK_FLUSH_SIZE = 10

    def callback(study: optuna.Study, trial: FrozenTrial) -> None:
        params = trial.params.copy()
        algo: str = str(
            trial.user_attrs.get(
                "recommender_class_name",
                params.pop("recommender_class_name", "unknown"),
            )
        )
        params.pop("optimizer_name", None)
        if trial.value is None:
            message = (
                f"Trial {trial.number} with {algo} / {params}: {trial.state.name}."
            )
        else:
            message = (
                f"Trial {trial.number} with {algo}"
                f" / {params}: {trial.state.name}.\n"
                f"{evaluator.target_metric.name}"
                f"@{evaluator.cutoff}={-trial.value}"
            )
        log_buffer.append(TaskLog(task=task_result, contents=message))
        if len(log_buffer) >= BULK_FLUSH_SIZE:
            TaskLog.objects.bulk_create(log_buffer)
            log_buffer.clear()
        _send_ws_log(parameter_tuning_job_id, message)

    if job.random_seed is None:
        random_seed = random.randint(0, 2**16)
    else:
        random_seed: int = job.random_seed

    study = optuna.create_study(
        storage=optuna_storage,
        study_name=study_name,
        direction="minimize",
        sampler=TPESampler(seed=random_seed + index),
        load_if_exists=True,
    )

    def objective(trial: optuna.Trial) -> float:
        recommender_class_name = trial.suggest_categorical(
            "recommender_class_name",
            tried_recommenders,
        )
        recommender_class = get_recommender_class(recommender_class_name)
        params: dict[str, Any] = recommender_class.default_suggest_parameter(trial, {})
        recommender = recommender_class(X_tv_train, **params)
        recommender.learn_with_optimizer(evaluator, trial)
        score = evaluator.get_score(recommender)
        val_score = score[evaluator.target_metric.name]
        trial.set_user_attr("recommender_class_name", recommender_class_name)
        for param_name, param_val in recommender.learnt_config.items():
            trial.set_user_attr(param_name, param_val)
        return -val_score

    try:
        study.optimize(
            objective,
            n_trials=n_trials,
            timeout=job.timeout_overall,
            callbacks=[callback],
        )
    except SoftTimeLimitExceeded:
        logger.error(
            "Search worker %d for job %d timed out", index, parameter_tuning_job_id
        )
        ParameterTuningJob.objects.filter(id=parameter_tuning_job_id).update(
            status=ParameterTuningJob.Status.FAILED
        )
        msg_timeout = (
            f"Search worker {index} for job {parameter_tuning_job_id} timed out"
        )
        TaskLog.objects.create(task=task_result, contents=msg_timeout)
        _send_ws_log(parameter_tuning_job_id, msg_timeout)
        _send_ws_status(parameter_tuning_job_id, "error", {"error": "Task timed out"})
        raise
    except Exception as e:
        logger.exception(
            "Search worker %d for job %d failed", index, parameter_tuning_job_id
        )
        ParameterTuningJob.objects.filter(id=parameter_tuning_job_id).update(
            status=ParameterTuningJob.Status.FAILED
        )
        msg_error = (
            f"Search worker {index} for job {parameter_tuning_job_id} failed: {e}"
        )
        TaskLog.objects.create(task=task_result, contents=msg_error)
        _send_ws_log(parameter_tuning_job_id, msg_error)
        _send_ws_status(parameter_tuning_job_id, "error", {"error": str(e)})
        raise
    finally:
        # Flush any remaining buffered log entries so they are never lost,
        # regardless of whether the task succeeded, failed, or was interrupted.
        if log_buffer:
            try:
                TaskLog.objects.bulk_create(log_buffer)
            except Exception:
                logger.exception(
                    "Failed to flush %d buffered TaskLog entries for job %d",
                    len(log_buffer),
                    parameter_tuning_job_id,
                )
            log_buffer.clear()


def start_tuning_job(job: ParameterTuningJob) -> None:
    job.status = ParameterTuningJob.Status.PENDING
    job.save(update_fields=["status"])

    optuna_storage = get_optuna_storage()
    study_name = job.study_name()
    optuna.create_study(
        storage=optuna_storage,
        study_name=study_name,
        direction="minimize",
        load_if_exists=True,
    )

    try:
        if job.train_after_tuning:
            chain(
                group(
                    run_search.si(job.id, i)
                    for i in range(max(1, job.n_tasks_parallel))
                ),
                task_create_best_config_train_rec.si(job.id),
            ).delay()
        else:
            chain(
                group(
                    run_search.si(job.id, i)
                    for i in range(max(1, job.n_tasks_parallel))
                ),
                task_create_best_config.si(job.id),
            ).delay()
    except Exception:
        logger.exception("Failed to enqueue Celery tasks for job %d", job.id)
        job.status = ParameterTuningJob.Status.FAILED
        job.save(update_fields=["status"])
        raise


@app.task(
    bind=True,
    time_limit=settings.CELERY_TASK_TIME_LIMIT,
    soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    autoretry_for=(ConnectionError, OSError),
    retry_backoff=True,
    max_retries=3,
)
def task_scheduled_retrain(self, schedule_id: int) -> None:
    """Execute a scheduled retraining run."""
    try:
        schedule = RetrainingSchedule.objects.select_related(
            "project",
            "training_data",
            "model_configuration",
            "split_config",
            "evaluation_config",
        ).get(id=schedule_id)
    except RetrainingSchedule.DoesNotExist:
        logger.error("Retraining schedule %d not found", schedule_id)
        return

    if not schedule.is_enabled:
        logger.info("Schedule %d is disabled, skipping", schedule_id)
        return

    # Determine training data
    training_data = schedule.training_data
    if training_data is None:
        training_data = (
            TrainingData.objects.filter(project=schedule.project)
            .order_by("-ins_datetime")
            .first()
        )
    if training_data is None:
        logger.warning("No training data for schedule %d", schedule_id)
        return

    # Create run record
    run = RetrainingRun.objects.create(
        schedule=schedule,
        status=RetrainingRun.Status.RUNNING,
        data_rows_at_trigger=training_data.filesize,
    )

    try:
        if schedule.retune and schedule.split_config and schedule.evaluation_config:
            # Re-tune: create and start a tuning job
            job = ParameterTuningJob.objects.create(
                data=training_data,
                split=schedule.split_config,
                evaluation=schedule.evaluation_config,
                train_after_tuning=True,
            )
            start_tuning_job(job)
            run.tuning_job = job
            run.status = RetrainingRun.Status.COMPLETED
        elif schedule.model_configuration:
            # Train with existing config
            model = TrainedModel.objects.create(
                configuration=schedule.model_configuration,
                data_loc=training_data,
            )
            train_and_save_model(model)
            run.trained_model = model
            run.status = RetrainingRun.Status.COMPLETED
        else:
            run.status = RetrainingRun.Status.SKIPPED
            run.error_message = "No model configuration or retune settings"

    except Exception as e:
        logger.exception("Scheduled retraining failed for schedule %d", schedule_id)
        run.status = RetrainingRun.Status.FAILED
        run.error_message = str(e)

    run.completed_at = timezone.now()
    run.save()

    # Update schedule metadata
    schedule.last_run_at = timezone.now()
    schedule.last_run_status = run.status
    schedule.save(update_fields=["last_run_at", "last_run_status", "updated_at"])
