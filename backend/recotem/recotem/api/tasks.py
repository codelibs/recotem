import json
import pickle
import random
import re
import tempfile
from logging import Logger
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import scipy.sparse as sps
from billiard.connection import Pipe
from billiard.context import Process
from celery import chain, group
from django.conf import settings
from django.core.files.storage import default_storage
from django_celery_results.models import TaskResult
from irspack import Evaluator, IDMappedRecommender, InteractionMatrix
from irspack import __version__ as irspack_version
from irspack import autopilot, get_optimizer_class, split_dataframe_partial_user_holdout
from irspack.optimizers.autopilot import TaskBackend, search_one
from irspack.parameter_tuning import Suggestion
from irspack.parameter_tuning.parameter_range import is_valid_param_name
from irspack.recommenders.base import get_recommender_class
from irspack.utils import df_to_sparse
from optuna.storages import RDBStorage

from recotem.api.models import (
    EvaluationConfig,
    ModelConfiguration,
    ParameterTuningJob,
    Project,
    SplitConfig,
    TaskAndParameterJobLink,
    TaskAndTrainedModelLink,
    TaskLog,
    TrainedModel,
    TrainingData,
)
from recotem.api.utils import read_dataframe
from recotem.celery import app


class BilliardBackend(TaskBackend):
    def __init__(
        self,
        X: InteractionMatrix,
        evaluator: Evaluator,
        optimizer_names: List[str],
        suggest_overwrites: Dict[str, List[Suggestion]],
        db_url: str,
        study_name: str,
        random_seed: int,
        logger: Logger,
    ):
        self.pipe_parent, pipe_child = Pipe()
        self._p = Process(
            target=search_one,
            args=(
                pipe_child,
                X,
                evaluator,
                optimizer_names,
                suggest_overwrites,
                db_url,
                study_name,
                random_seed,
                logger,
            ),
        )

    def _exit_code(self) -> Optional[int]:
        return self._p.exitcode

    def receive_trial_number(self) -> int:
        result: int = self.pipe_parent.recv()
        return result

    def start(self) -> None:
        self._p.start()

    def join(self, timeout: Optional[int]) -> None:
        self._p.join(timeout=timeout)

    def terminate(self) -> None:
        self._p.terminate()


def train_recommender_func(
    task_result, model_id: int, parameter_tuning_job_id: Optional[int] = None
):
    model: TrainedModel = TrainedModel.objects.get(id=model_id)

    model_config: ModelConfiguration = model.configuration
    data: TrainingData = model.data_loc

    TaskAndTrainedModelLink.objects.create(model=model, task=task_result)
    assert model_config.project.id == data.project.id
    project: Project = data.project
    user_column = project.user_column
    item_column = project.item_column
    recommender_class = get_recommender_class(model_config.recommender_class_name)

    X, uid, iid = df_to_sparse(data.validate_return_df(), user_column, item_column)

    model.irspack_version = irspack_version

    param = json.loads(model_config.parameters_json)
    rec = recommender_class(X, **param).learn()
    with tempfile.TemporaryFile() as temp_fs:
        mapped_rec = IDMappedRecommender(rec, uid, iid)
        pickle.dump(
            dict(
                id_mapped_recommender=mapped_rec,
                irspack_version=irspack_version,
                recotem_trained_model_id=model_id,
            ),
            temp_fs,
        )
        temp_fs.seek(0)
        file_ = default_storage.save(f"models/model-{model.id}.pkl", temp_fs)
        model.file = file_
        model.save()

    model.filesize = model.file.size
    model.save()

    if parameter_tuning_job_id is not None:
        job: ParameterTuningJob = ParameterTuningJob.objects.get(
            id=parameter_tuning_job_id
        )
        job.tuned_model = model
        job.save()


@app.task(bind=True)
def task_train_recommender(self, model_id: int) -> None:
    task_result, _ = TaskResult.objects.get_or_create(task_id=self.request.id)
    self.update_state(state="STARTED", meta=[])
    train_recommender_func(task_result, model_id)


def create_best_config_fun(task_result, parameter_tuning_job_id: int) -> int:
    job: ParameterTuningJob = ParameterTuningJob.objects.get(id=parameter_tuning_job_id)

    TaskAndParameterJobLink.objects.create(job=job, task=task_result)

    data: TrainingData = job.data
    project: TrainingData = data.project

    optuna_storage = RDBStorage(settings.DATABASE_URL)
    study_name = job.study_name()
    study_id = optuna_storage.get_study_id_from_name(study_name)
    best_trial = optuna_storage.get_best_trial(study_id)
    best_params_with_prefix = dict(
        **best_trial.params,
        **{
            key: val
            for key, val in best_trial.user_attrs.items()
            if is_valid_param_name(key)
        },
    )
    best_params = {
        re.sub(r"^([^\.]*\.)", "", key): value
        for key, value in best_params_with_prefix.items()
    }
    optimizer_name: str = best_params.pop("optimizer_name")
    recommender_class_name = get_optimizer_class(
        optimizer_name
    ).recommender_class.__name__
    config_name = f"Tuning Result of job {job.id}"
    config = ModelConfiguration.objects.create(
        name=config_name,
        project=project,
        parameters_json=json.dumps(best_params),
        recommender_class_name=recommender_class_name,
    )

    job.best_config = config
    job.irspack_version = irspack_version
    job.save()

    TaskLog.objects.create(
        task=task_result,
        contents=f"""Job {parameter_tuning_job_id} complete.""",
    )
    TaskLog.objects.create(
        task=task_result,
        contents=f"""Found best configuration: {recommender_class_name} / {best_params}.""",
    )

    return config.id


@app.task(bind=True)
def task_create_best_config(self, parameter_tuning_job_id: int, *args) -> int:
    task_result, _ = TaskResult.objects.get_or_create(task_id=self.request.id)
    self.update_state(state="STARTED", meta=[])
    return create_best_config_fun(task_result, parameter_tuning_job_id)


@app.task(bind=True)
def task_create_best_config_train_rec(self, parameter_tuning_job_id: int, *args) -> int:
    task_result, _ = TaskResult.objects.get_or_create(task_id=self.request.id)
    self.update_state(state="STARTED", meta=[])
    config_id = create_best_config_fun(task_result, parameter_tuning_job_id)
    job: ParameterTuningJob = ParameterTuningJob.objects.get(id=parameter_tuning_job_id)
    config: ModelConfiguration = ModelConfiguration.objects.get(id=config_id)
    model = TrainedModel.objects.create(configuration=config, data_loc=job.data)

    train_recommender_func(task_result, model.id, parameter_tuning_job_id)


@app.task(bind=True)
def run_search(self, parameter_tuning_job_id: int, index: int) -> None:

    task_result, _ = TaskResult.objects.get_or_create(task_id=self.request.id)
    logs = [
        dict(message=f"Started the parameter tuning jog {parameter_tuning_job_id} ")
    ]
    self.update_state(state="STARTED", meta=logs)
    job: ParameterTuningJob = ParameterTuningJob.objects.get(id=parameter_tuning_job_id)

    n_trials: int = job.n_trials // job.n_tasks_parallel

    if index < (job.n_trials % job.n_tasks_parallel):
        n_trials += 1

    TaskAndParameterJobLink.objects.create(job=job, task=task_result)
    data: TrainingData = job.data
    project: Project = data.project
    split: SplitConfig = job.split
    evaluation: EvaluationConfig = job.evaluation
    df: pd.DataFrame = read_dataframe(Path(data.file.name), data.file)
    user_column = project.user_column
    item_column = project.item_column

    TaskLog.objects.create(
        task=task_result,
        contents=f"""Start job {parameter_tuning_job_id}.""",
    )

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

    optuna_storage = RDBStorage(settings.DATABASE_URL)
    study_name = job.study_name()
    study_id = optuna_storage.get_study_id_from_name(study_name)

    def callback(i: int, df: pd.DataFrame) -> None:
        trial_id = optuna_storage.get_trial_id_from_study_id_trial_number(study_id, i)
        trial = optuna_storage.get_trial(trial_id)
        params = trial.params.copy()
        algo: str = params.pop("optimizer_name")
        if trial.value is None or trial.value == 0.0:
            message = f"Trial {i} with {algo} / {params}: timeout."
        else:
            message = f"""Trial {i} with {algo} / {params}: {trial.state.name}.
{evaluator.target_metric.name}@{evaluator.cutoff}={-trial.value}"""
        TaskLog.objects.create(task=task_result, contents=message)

    if job.random_seed is None:
        random_seed = random.randint(0, 2 ** 16)
    else:
        random_seed: int = job.random_seed
    autopilot(
        X_tv_train,
        evaluator,
        n_trials=n_trials,
        memory_budget=job.memory_budget,
        timeout_overall=job.timeout_overall,
        timeout_singlestep=job.timeout_singlestep,
        random_seed=random_seed + index,
        callback=callback,
        storage=optuna_storage,
        study_name=study_name,
        task_resource_provider=BilliardBackend,
    )


def start_tuning_job(job: ParameterTuningJob) -> None:
    optuna_storage = RDBStorage(settings.DATABASE_URL)
    study_name = job.study_name()
    optuna_storage.create_new_study(study_name)

    if job.train_after_tuning:
        chain(
            group(
                run_search.si(job.id, i) for i in range(max(1, job.n_tasks_parallel))
            ),
            task_create_best_config_train_rec.si(job.id),
        ).delay()
    else:
        chain(
            group(
                run_search.si(job.id, i) for i in range(max(1, job.n_tasks_parallel))
            ),
            task_create_best_config.si(job.id),
        ).delay()
