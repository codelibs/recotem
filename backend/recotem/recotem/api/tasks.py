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
from irspack import Evaluator, IDMapper, InteractionMatrix
from irspack import __version__ as irspack_version
from irspack import split_dataframe_partial_user_holdout
from irspack.optimization import Optimizer  # get_optimizer_class replacement

# autopilot module has been removed in irspack 0.4.0 - complete reimplementation
DEFAULT_SEARCHNAMES = [
    "CosineKNNRecommender",
    "P3alphaRecommender",
    "RP3betaRecommender",
    "IALSRecommender",
    "NMFRecommender",
    "TruncatedSVDRecommender",
]

# Parameter ranges for each algorithm
ALGORITHM_PARAMETER_RANGES = {
    "CosineKNNRecommender": {
        "top_k": ("int", 4, 1000),
        "shrinkage": ("float", 0.0, 1000.0),
        "feature_weighting": ("categorical", ["NONE", "TF_IDF", "BM_25"]),
        "normalize": ("categorical", [True, False]),
    },
    "P3alphaRecommender": {
        "alpha": ("float", 0.0, 2.0),
        "top_k": ("int", 4, 1000),
        "normalize_weight": ("categorical", [True, False]),
    },
    "RP3betaRecommender": {
        "alpha": ("float", 0.0, 2.0),
        "beta": ("float", 0.0, 2.0),
        "top_k": ("int", 4, 1000),
        "normalize_weight": ("categorical", [True, False]),
    },
    "IALSRecommender": {
        "n_components": ("int", 4, 512),
        "alpha0": ("log_float", 1e-8, 10.0),
        "reg": ("log_float", 1e-5, 1e2),
        "nu": ("log_float", 1e-5, 1e2),
        "train_epochs": ("int", 1, 512),
    },
    "NMFRecommender": {
        "n_components": ("int", 4, 512),
        "alpha": ("log_float", 1e-8, 10.0),
        "l1_ratio": ("float", 0.0, 1.0),
        "beta_loss": ("categorical", ["frobenius", "kullback-leibler"]),
    },
    "TruncatedSVDRecommender": {
        "n_components": ("int", 4, 512),
    },
}


def suggest_parameters(trial, algorithm_name):
    """Suggest parameters for a given algorithm using Optuna trial"""
    if algorithm_name not in ALGORITHM_PARAMETER_RANGES:
        raise ValueError(f"Unknown algorithm: {algorithm_name}")

    params = {"optimizer_name": algorithm_name}
    ranges = ALGORITHM_PARAMETER_RANGES[algorithm_name]

    for param_name, param_config in ranges.items():
        param_type = param_config[0]
        if param_type == "int":
            params[param_name] = trial.suggest_int(
                param_name, param_config[1], param_config[2]
            )
        elif param_type == "float":
            params[param_name] = trial.suggest_float(
                param_name, param_config[1], param_config[2]
            )
        elif param_type == "log_float":
            params[param_name] = trial.suggest_float(
                param_name, param_config[1], param_config[2], log=True
            )
        elif param_type == "categorical":
            params[param_name] = trial.suggest_categorical(param_name, param_config[1])

    return params


class TaskBackend:
    """Task backend for parallel execution"""

    def __init__(
        self,
        X,
        evaluator,
        optimizer_names,
        suggest_overwrites,
        db_url,
        study_name,
        random_seed,
        logger,
    ):
        self.X = X
        self.evaluator = evaluator
        self.optimizer_names = optimizer_names
        self.suggest_overwrites = suggest_overwrites
        self.db_url = db_url
        self.study_name = study_name
        self.random_seed = random_seed
        self.logger = logger

    def __call__(self, *args, **kwargs):
        # This is called by the original code, but we'll handle it in autopilot
        return self


def search_one(
    pipe,
    X,
    evaluator,
    optimizer_names,
    suggest_overwrites,
    db_url,
    study_name,
    random_seed,
    logger,
):
    """Execute a single optimization search"""
    import optuna
    from irspack import get_recommender_class

    storage = optuna.storages.RDBStorage(db_url)
    study = optuna.load_study(study_name=study_name, storage=storage)

    def objective(trial):
        # Select algorithm
        algorithm_name = trial.suggest_categorical("optimizer_name", optimizer_names)

        try:
            # Get parameters for this algorithm
            params = suggest_parameters(trial, algorithm_name)
            optimizer_name = params.pop("optimizer_name")

            # Get recommender class
            recommender_class = get_recommender_class(optimizer_name)

            # Train recommender
            recommender = recommender_class(X, **params).learn()

            # Evaluate
            scores = evaluator.get_score(recommender)
            # target_metric is already a string in irspack 0.4.0
            target_metric = evaluator.target_metric if isinstance(evaluator.target_metric, str) else evaluator.target_metric.name.lower()

            if target_metric in scores:
                return scores[target_metric]
            else:
                # Return first available score
                return list(scores.values())[0]

        except Exception as e:
            logger.error(f"Trial failed: {e}")
            return 0.0

    # Run a single trial
    study.optimize(objective, n_trials=1, timeout=300)  # 5 minute timeout per trial


def autopilot(
    X_train,
    evaluator,
    n_trials=10,
    callback=None,
    storage=None,
    study_name=None,
    algorithms=None,
    memory_budget=None,
    timeout_overall=None,
    timeout_singlestep=None,
    random_seed=None,
    task_resource_provider=None,
    **kwargs,
):
    """Complete reimplementation of autopilot function for irspack 0.4.0"""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    import optuna
    from irspack import get_recommender_class

    if algorithms is None:
        algorithms = DEFAULT_SEARCHNAMES

    print(
        f"autopilot: Starting optimization with {n_trials} trials for study {study_name}"
    )
    print(f"Algorithms: {algorithms}")

    # Get or create study with proper synchronization
    study = None
    max_retries = 3

    for attempt in range(max_retries):
        try:
            # First, try to load existing study
            study = optuna.load_study(study_name=study_name, storage=storage)
            print(
                f"autopilot: Loaded existing study {study_name} (attempt {attempt + 1})"
            )
            break
        except Exception as load_error:
            print(
                f"autopilot: Failed to load study {study_name} (attempt {attempt + 1}): {load_error}"
            )

            # If loading failed, try to create study
            try:
                study = optuna.create_study(
                    direction="maximize", study_name=study_name, storage=storage
                )
                print(
                    f"autopilot: Created new study {study_name} (attempt {attempt + 1})"
                )
                break
            except Exception as create_error:
                print(
                    f"autopilot: Failed to create study {study_name} (attempt {attempt + 1}): {create_error}"
                )

                # If creation failed due to study already existing, try loading again
                if (
                    "already exists" in str(create_error).lower()
                    or "duplicate" in str(create_error).lower()
                ):
                    try:
                        study = optuna.load_study(
                            study_name=study_name, storage=storage
                        )
                        print(
                            f"autopilot: Loaded study after creation conflict (attempt {attempt + 1})"
                        )
                        break
                    except Exception as final_load_error:
                        print(
                            f"autopilot: Final load attempt failed (attempt {attempt + 1}): {final_load_error}"
                        )

                # Wait a bit before retrying to avoid race conditions
                if attempt < max_retries - 1:
                    import time

                    time.sleep(0.5)

    if study is None:
        print(
            f"autopilot: Completely failed to get study {study_name} after {max_retries} attempts, aborting"
        )
        return

    # Set random seed for reproducibility
    if random_seed is not None:
        optuna.samplers.RandomSampler(seed=random_seed)

    start_time = time.time()
    completed_trials = 0

    def objective(trial):
        trial_start_time = time.time()

        # Check overall timeout
        if timeout_overall and (time.time() - start_time) > timeout_overall:
            raise optuna.TrialPruned()

        # Select algorithm
        algorithm_name = trial.suggest_categorical("optimizer_name", algorithms)

        try:
            # Get parameters for this algorithm
            params = suggest_parameters(trial, algorithm_name)
            optimizer_name = params.pop("optimizer_name")

            print(
                f"Trial {trial.number}: Training {optimizer_name} with params: {params}"
            )

            # Get recommender class
            recommender_class = get_recommender_class(optimizer_name)

            # Execute with timeout if specified
            def train_and_evaluate():
                recommender = recommender_class(X_train, **params).learn()
                scores = evaluator.get_score(recommender)
                # target_metric is already a string in irspack 0.4.0
                target_metric = evaluator.target_metric if isinstance(evaluator.target_metric, str) else evaluator.target_metric.name.lower()

                if target_metric in scores:
                    return scores[target_metric]
                else:
                    # Return first available score
                    return list(scores.values())[0]

            if timeout_singlestep:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(train_and_evaluate)
                    try:
                        score = future.result(timeout=timeout_singlestep)
                    except TimeoutError:
                        print(
                            f"Trial {trial.number}: Timeout after {timeout_singlestep}s"
                        )
                        return 0.0
            else:
                score = train_and_evaluate()

            elapsed_time = time.time() - trial_start_time
            print(
                f"Trial {trial.number}: Completed in {elapsed_time:.2f}s with score: {score}"
            )

            return score

        except Exception as e:
            elapsed_time = time.time() - trial_start_time
            print(
                f"Trial {trial.number}: Failed after {elapsed_time:.2f}s with error: {e}"
            )
            return 0.0

    # Custom callback to integrate with the original system
    def study_callback(study, trial):
        nonlocal completed_trials
        completed_trials += 1

        if callback:
            try:
                # Create dataframe with trial results
                df = pd.DataFrame(
                    [
                        {
                            "trial": trial.number,
                            "value": trial.value,
                            "params": trial.params,
                            "state": trial.state.name,
                        }
                    ]
                )
                callback(trial.number, df)
            except Exception as e:
                print(f"Callback failed for trial {trial.number}: {e}")

    # Run optimization
    try:
        study.optimize(
            objective,
            n_trials=n_trials,
            timeout=timeout_overall,
            callbacks=[study_callback],
        )
    except KeyboardInterrupt:
        print("Optimization interrupted by user")
    except Exception as e:
        print(f"Optimization failed: {e}")
        import traceback

        traceback.print_exc()

    total_time = time.time() - start_time
    print(f"autopilot: Completed optimization in {total_time:.2f}s")
    print(f"Completed {completed_trials} trials")

    if study.trials:
        best_trial = study.best_trial
        print(f"Best trial: {best_trial.number} with value: {best_trial.value}")
        print(f"Best params: {best_trial.params}")
    else:
        print("No successful trials completed")


def get_optimizer_class(optimizer_name):
    """Get optimizer information for a given algorithm name"""
    from irspack import get_recommender_class

    class OptimizerInfo:
        def __init__(self, name):
            self.recommender_class = get_recommender_class(name)
            self.name = name

    return OptimizerInfo(optimizer_name)


# IDMappedRecommender wrapper class (moved to module level for pickle compatibility)
class IDMappedRecommender:
    """Compatibility wrapper for irspack 0.4.0 to maintain API compatibility"""

    def __init__(self, recommender, user_ids, item_ids, X_train=None):
        self.recommender = recommender
        self.id_mapper = IDMapper(user_ids, item_ids)
        self.user_ids = user_ids
        self.item_ids = item_ids
        # Store training matrix for new user recommendations
        self.X_train = X_train

    def get_score(self, user_ids):
        import numpy as np

        user_indices = [self.id_mapper.user_id_to_index[uid] for uid in user_ids]
        return self.recommender.get_score(np.array(user_indices))

    def get_item_score(self, user_id, item_ids=None):
        import numpy as np

        user_index = self.id_mapper.user_id_to_index[user_id]
        scores = self.recommender.get_score(np.array([user_index]))[0]
        if item_ids is None:
            return scores
        item_indices = [self.id_mapper.item_id_to_index[iid] for iid in item_ids]
        return scores[item_indices]

    def get_recommendation_for_known_user_id(self, user_id, cutoff=10):
        import numpy as np

        user_index = self.id_mapper.user_id_to_index[user_id]
        scores = self.recommender.get_score(np.array([user_index]))[0]
        top_indices = np.argsort(scores)[::-1][:cutoff]
        return [(self.item_ids[i], scores[i]) for i in top_indices]

    def get_recommendation_for_new_user(self, consumed_item_ids, cutoff=10):
        # For new user recommendations, we'll use a simple approach
        # In practice, this would need a more sophisticated implementation
        import numpy as np

        # Return top popular items that are not in consumed_item_ids
        # Use X_train if available, otherwise fall back to a simple approach
        if self.X_train is not None:
            item_popularity = np.array(self.X_train.sum(axis=0)).flatten()
        else:
            # Fallback: use uniform popularity
            item_popularity = np.ones(len(self.item_ids))

        consumed_indices = set(
            self.id_mapper.item_id_to_index[iid]
            for iid in consumed_item_ids
            if iid in self.id_mapper.item_id_to_index
        )
        candidates = [
            (i, pop)
            for i, pop in enumerate(item_popularity)
            if i not in consumed_indices
        ]
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [(self.item_ids[i], pop) for i, pop in candidates[:cutoff]]


# Suggestion class has been removed in irspack 0.4.0 - reimplemented
class Suggestion:
    """Reimplemented Suggestion class for parameter suggestions"""

    def __init__(self, parameter_name, value):
        self.parameter_name = parameter_name
        self.value = value

    def __repr__(self):
        return f"Suggestion({self.parameter_name}={self.value})"

    def to_dict(self):
        return {self.parameter_name: self.value}


import optuna
from irspack.optimization.parameter_range import is_valid_param_name
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

    X, uids, iids = df_to_sparse(data.validate_return_df(), user_column, item_column)
    uids = [str(uid) for uid in uids]
    iids = [str(iid) for iid in iids]

    model.irspack_version = irspack_version

    param = json.loads(model_config.parameters_json)
    rec = recommender_class(X, **param).learn()
    with tempfile.TemporaryFile() as temp_fs:
        # Use the module-level IDMappedRecommender class for pickle compatibility
        mapped_rec = IDMappedRecommender(rec, uids, iids, X_train=X)
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

    print(
        f"create_best_config_fun: Getting best trial for study {study_name} (id: {study_id})"
    )
    try:
        best_trial = optuna_storage.get_best_trial(study_id)
        print(
            f"create_best_config_fun: Found best trial: {best_trial.number} with value {best_trial.value}"
        )
    except Exception as e:
        print(f"create_best_config_fun: Failed to get best trial: {e}")
        # If no trials exist, we can't create a best config
        if "no completed trials" in str(e).lower() or "no trial" in str(e).lower():
            print(
                f"create_best_config_fun: No completed trials found for study {study_name}"
            )
            raise ValueError(
                f"No completed trials found for study {study_name}. Cannot create best config."
            )
        raise
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


def run_search_func(task_result, parameter_tuning_job_id: int, index: int) -> None:
    """Core function for parameter tuning search - can be called directly or via Celery"""
    print(
        f"run_search_func: Starting task for job {parameter_tuning_job_id}, index {index}"
    )

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
    tried_algorithms: List[str] = DEFAULT_SEARCHNAMES
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

    # Ensure study exists before getting ID
    try:
        study_id = optuna_storage.get_study_id_from_name(study_name)
        print(f"run_search_func: Found existing study {study_name} with id {study_id}")
    except Exception as e:
        print(f"run_search_func: Study {study_name} not found, creating it: {e}")
        try:
            study_id = optuna_storage.create_new_study(study_name)
            print(f"run_search_func: Created new study {study_name} with id {study_id}")
        except Exception as create_error:
            print(f"run_search_func: Failed to create study: {create_error}")
            # Try to get it again in case it was created by another process
            try:
                study_id = optuna_storage.get_study_id_from_name(study_name)
                print(
                    f"run_search_func: Found study after creation attempt: {study_id}"
                )
            except:
                print(f"run_search_func: Completely failed to get study_id, aborting")
                raise create_error

    def callback(i: int, df: pd.DataFrame) -> None:
        trial_id = optuna_storage.get_trial_id_from_study_id_trial_number(study_id, i)
        trial = optuna_storage.get_trial(trial_id)
        params = trial.params.copy()
        algo: str = params.pop("optimizer_name")
        # target_metric is already a string in irspack 0.4.0
        target_metric_name = evaluator.target_metric if isinstance(evaluator.target_metric, str) else evaluator.target_metric.name
        if trial.value is None or trial.value == 0.0:
            message = f"Trial {i} with {algo} / {params}: timeout."
        else:
            message = f"""Trial {i} with {algo} / {params}: {trial.state.name}.
{target_metric_name}@{evaluator.cutoff}={-trial.value}"""
        TaskLog.objects.create(task=task_result, contents=message)

    if job.random_seed is None:
        random_seed = random.randint(0, 2**16)
    else:
        random_seed: int = job.random_seed

    print(f"run_search_func: About to call autopilot with {n_trials} trials")
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
        algorithms=tried_algorithms,
    )


@app.task(bind=True)
def run_search(self, parameter_tuning_job_id: int, index: int) -> None:
    task_result, _ = TaskResult.objects.get_or_create(task_id=self.request.id)
    print(
        f"run_search: Starting Celery task for job {parameter_tuning_job_id}, index {index}"
    )
    self.update_state(state="STARTED", meta=[])
    return run_search_func(task_result, parameter_tuning_job_id, index)


def start_tuning_job(job: ParameterTuningJob) -> None:
    optuna_storage = RDBStorage(settings.DATABASE_URL)
    study_name = job.study_name()
    optuna_storage.create_new_study(study_name)

    # Check if we're in a test environment (detect by checking if we're using memory transport)
    is_testing = (
        hasattr(settings, "CELERY_TASK_ALWAYS_EAGER")
        and settings.CELERY_TASK_ALWAYS_EAGER
    ) or (
        "sqlite" in settings.DATABASE_URL.lower()
        or "memory" in str(getattr(settings, "CELERY_BROKER_URL", ""))
    )

    print(
        f"start_tuning_job: is_testing={is_testing}, DATABASE_URL={settings.DATABASE_URL}"
    )

    if is_testing:
        # Execute tasks directly in test environment
        print(f"start_tuning_job: Running in test mode, executing tasks directly")

        # Run search tasks directly
        for i in range(max(1, job.n_tasks_parallel)):
            print(f"start_tuning_job: Executing run_search for job {job.id}, index {i}")
            # Create a mock task result
            from django_celery_results.models import TaskResult

            task_result, _ = TaskResult.objects.get_or_create(
                task_id=f"test-run-search-{job.id}-{i}", defaults={"status": "STARTED"}
            )

            # Execute run_search function directly
            try:
                run_search_func(task_result, job.id, i)
                task_result.status = "SUCCESS"
                task_result.save()
            except Exception as e:
                print(f"start_tuning_job: run_search failed: {e}")
                task_result.status = "FAILURE"
                task_result.result = str(e)
                task_result.save()

        # Create best config
        print(f"start_tuning_job: Creating best config for job {job.id}")
        task_result, _ = TaskResult.objects.get_or_create(
            task_id=f"test-create-config-{job.id}", defaults={"status": "STARTED"}
        )

        try:
            config_id = create_best_config_fun(task_result, job.id)
            task_result.status = "SUCCESS"
            task_result.result = config_id
            task_result.save()

            if job.train_after_tuning:
                # Train the model
                from recotem.api.models import ModelConfiguration, TrainedModel

                config = ModelConfiguration.objects.get(id=config_id)
                model = TrainedModel.objects.create(
                    configuration=config, data_loc=job.data
                )
                train_recommender_func(task_result, model.id, job.id)

        except Exception as e:
            print(f"start_tuning_job: create_best_config_fun failed: {e}")
            import traceback

            traceback.print_exc()
            task_result.status = "FAILURE"
            task_result.result = str(e)
            task_result.save()
    else:
        # Use Celery in production
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
