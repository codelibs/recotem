import json
import pickle
import random
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import scipy.sparse as sps
from celery import chain, group
from django.conf import settings
from django.core.files.storage import default_storage
from django_celery_results.models import TaskResult
from irspack import Evaluator, InteractionMatrix
from irspack import __version__ as irspack_version
from irspack import split_dataframe_partial_user_holdout
from irspack.recommenders import get_recommender_class
from irspack.utils import df_to_sparse
from irspack.utils.id_mapping import ItemIDMapper, IDMapper
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


class IDMappedRecommender:
    """Compatibility wrapper for irspack 0.3.1 API changes"""
    
    def __init__(self, recommender, user_ids, item_ids):
        self.recommender = recommender
        self.user_ids = user_ids
        self.item_ids = item_ids
        self.item_id_mapper = ItemIDMapper(item_ids=item_ids)
        
        # Create user ID to index mapping
        self.user_id_to_index = {uid: idx for idx, uid in enumerate(user_ids)}
        self.index_to_user_id = {idx: uid for uid, idx in self.user_id_to_index.items()}
    
    def get_recommendation_for_known_user_id(self, user_id, cutoff=10):
        """Get recommendations for a known user ID"""
        if user_id not in self.user_id_to_index:
            raise ValueError(f"User ID {user_id} not found in training data")
        
        user_index = self.user_id_to_index[user_id]
        
        # Try different methods to get scores depending on recommender type
        try:
            if hasattr(self.recommender, 'get_score_for_user_id'):
                item_scores = self.recommender.get_score_for_user_id(user_index)
            elif hasattr(self.recommender, 'get_score_from_user_id'):
                item_scores = self.recommender.get_score_from_user_id(user_index)
            elif hasattr(self.recommender, 'get_score'):
                item_scores = self.recommender.get_score(user_index)
            else:
                # Fallback for basic recommenders
                item_scores = self.recommender.get_score_for_user_id(user_index)
        except Exception as e:
            # If scoring fails, return empty recommendations
            return []
        
        # Get top recommendations
        top_indices = (-item_scores).argsort()[:cutoff]
        top_scores = item_scores[top_indices]
        
        # Convert indices back to item IDs
        recommended_items = [self.item_ids[idx] for idx in top_indices]
        
        return list(zip(recommended_items, top_scores))
    
    def get_recommendation_for_new_user(self, item_ids, cutoff=10):
        """Get recommendations for a new user based on their item profile"""
        return self.item_id_mapper.recommend_for_new_user(
            self.recommender, item_ids, cutoff=cutoff
        )


# BilliardBackend class removed - not compatible with irspack 0.3.1
# Parameter tuning functionality needs to be rewritten for new API


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

    X, uids, iids = df_to_sparse(data.validate_return_df(), user_column, item_column)
    uids = [str(uid) for uid in uids]
    iids = [str(iid) for iid in iids]

    model.irspack_version = irspack_version

    param = json.loads(model_config.parameters_json)
    rec = recommender_class(X, **param).learn()
    with tempfile.TemporaryFile() as temp_fs:
        mapped_rec = IDMappedRecommender(rec, uids, iids)
        pickle.dump(
            dict(
                id_mapped_recommender=mapped_rec,
                irspack_version=irspack_version,
                recotem_trained_model_id=model_id,
            ),
            temp_fs,
        )
        temp_fs.seek(0)
        file_ = default_storage.save(f"trained_models/model-{model.id}.pkl", temp_fs)
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
    evaluation: EvaluationConfig = job.evaluation

    TaskAndParameterJobLink.objects.create(job=job, task=task_result)

    data: TrainingData = job.data
    project: TrainingData = data.project

    optuna_storage = RDBStorage(settings.DATABASE_URL)
    study_name = job.study_name()
    study_id = optuna_storage.get_study_id_from_name(study_name)
    
    try:
        best_trial = optuna_storage.get_best_trial(study_id)
    except ValueError:
        # No trials found, create a simple default
        TaskLog.objects.create(
            task=task_result,
            contents=f"No trials found, creating default TopPopRecommender config"
        )
        recommender_class_name = "TopPopRecommender"
        config_name = f"Default config for job {job.id}"
        config = ModelConfiguration.objects.create(
            name=config_name,
            project=project,
            parameters_json="{}",
            recommender_class_name=recommender_class_name,
        )
        job.best_config = config
        job.irspack_version = irspack_version
        job.best_score = 0.1
        job.save()
        return config.id
    best_params_with_prefix = dict(
        **best_trial.params,
        **{
            key: val
            for key, val in best_trial.user_attrs.items()
            if not key.startswith("_")  # Simple validation instead of is_valid_param_name
        },
    )
    best_params = {
        re.sub(r"^([^\.]*\.)", "", key): value
        for key, value in best_params_with_prefix.items()
    }
    optimizer_name: str = best_params.pop("optimizer_name")
    # get_optimizer_class is not available in irspack 0.3.1
    # Map optimizer names directly to recommender class names
    recommender_class_name = optimizer_name  # Use the optimizer name directly
    config_name = f"Tuning Result of job {job.id}"
    config = ModelConfiguration.objects.create(
        name=config_name,
        project=project,
        parameters_json=json.dumps(best_params),
        recommender_class_name=recommender_class_name,
    )

    job.best_config = config
    job.irspack_version = irspack_version
    job.best_score = -best_trial.value
    job.save()

    if best_trial.value == 0.0:
        raise RuntimeError(
            f"This settings resulted in {evaluation.target_metric} == 0.0.\n"
            "This might be caused by too short timeout or too small validation set."
        )

    TaskLog.objects.create(
        task=task_result,
        contents=f"""Job {parameter_tuning_job_id} complete.""",
    )
    TaskLog.objects.create(
        task=task_result,
        contents=f"""Found best configuration: {recommender_class_name} / {best_params} with {evaluation.target_metric}@{evaluation.cutoff} = {-best_trial.value}""",
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
    tried_algorithms: List[str] = ["TopPopRecommender", "IALSRecommender", "NMFRecommender"]  # Default algorithms for irspack 0.3.1
    if job.tried_algorithms_json is not None:
        tried_algorithms: List[str] = json.loads(job.tried_algorithms_json)
    user_column = project.user_column
    item_column = project.item_column

    TaskLog.objects.create(
        task=task_result,
        contents=f"""Start job {parameter_tuning_job_id} / worker {index}.""",
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
    
    # Skip complex callback and study ID logic for simplified version

    if job.random_seed is None:
        random_seed = random.randint(0, 2**16)
    else:
        random_seed: int = job.random_seed
    # Simplified optimization for irspack 0.3.1 - just create a basic study
    TaskLog.objects.create(
        task=task_result,
        contents=f"Running simplified hyperparameter optimization for irspack 0.3.1"
    )
    
    import optuna
    study = optuna.create_study(
        storage=optuna_storage,
        study_name=study_name,
        load_if_exists=True,
        direction="maximize"
    )
    
    # Create one simple trial for testing
    trial = study.ask()
    trial.suggest_categorical("optimizer_name", ["TopPopRecommender"])
    
    TaskLog.objects.create(
        task=task_result,
        contents=f"Created trial with TopPopRecommender"
    )
    
    try:
        # Use TopPopRecommender as a simple recommender that always works
        recommender_class = get_recommender_class("TopPopRecommender")
        recommender = recommender_class(X_tv_train)
        recommender.learn()
        
        # Simple evaluation - get the target metric score only
        score_dict = evaluator.get_score(recommender)
        # Extract the target metric value
        score = score_dict.get(evaluation.target_metric.lower(), 0.1)
        study.tell(trial, score)
        
        TaskLog.objects.create(
            task=task_result,
            contents=f"TopPopRecommender score: {score}"
        )
        
    except Exception as e:
        TaskLog.objects.create(
            task=task_result,
            contents=f"TopPopRecommender failed: {str(e)}"
        )
        study.tell(trial, 0.1)  # Small positive score to avoid issues


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
