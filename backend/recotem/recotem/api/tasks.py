import pandas as pd
import pickle
from optuna.storages import RDBStorage
import json
import tempfile
from django.core.files.storage import default_storage
from irspack.utils import df_to_sparse
from irspack import (
    IDMappedRecommender,
    Evaluator,
    split_dataframe_partial_user_holdout,
    autopilot,
)
import scipy.sparse as sps
from .models import (
    EvaluationConfig,
    ParameterTuningLog,
    Project,
    SplitConfig,
    ParameterTuningJob,
    ModelConfiguration,
    TrainedModel,
    TrainingData,
)
from .task_function import BilliardBackend

from ..celery import app


@app.task(bind=True)
def execute_irspack(self, parameter_tuning_job_id: int) -> None:
    logs = [
        dict(message=f"Started the parameter tuning jog {parameter_tuning_job_id} ")
    ]
    self.update_state(state="STARTED", meta=logs)
    job: ParameterTuningJob = ParameterTuningJob.objects.get(id=parameter_tuning_job_id)
    data: TrainingData = job.data
    project: Project = data.project
    project_name: str = project.name
    split: SplitConfig = job.split
    evaluation: EvaluationConfig = job.evaluation
    df: pd.DataFrame = pd.read_csv(data.upload_path)
    user_column = project.user_column
    item_column = project.item_column

    ParameterTuningLog.objects.create(
        job=job,
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

    optuna_storage_file = tempfile.NamedTemporaryFile()
    optuna_storage = RDBStorage(f"sqlite:///{optuna_storage_file.name}")
    study_name = f"recotem_tune_job_{parameter_tuning_job_id}"
    study_id = optuna_storage.create_new_study(study_name)

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
        ParameterTuningLog.objects.create(job=job, contents=message)

    recommender_class, bp, _ = autopilot(
        X_tv_train,
        evaluator,
        n_trials=job.n_trials,
        memory_budget=job.memory_budget,
        timeout_overall=job.timeout_overall,
        timeout_singlestep=job.timeout_singlestep,
        random_seed=job.random_seed,
        callback=callback,
        storage=optuna_storage,
        study_name=study_name,
        task_resource_provider=BilliardBackend,
    )

    ParameterTuningLog.objects.create(
        job=job,
        contents=f"""Found best configuration: {recommender_class.__name__} / {bp}.
Start fitting the entire data using this config.
""",
    )

    X, uid, iid = df_to_sparse(df, user_column, item_column)

    rec = recommender_class(X, **bp).learn()
    with tempfile.TemporaryFile() as temp_fs:
        mapped_rec = IDMappedRecommender(rec, uid, iid)
        pickle.dump(mapped_rec, temp_fs)
        temp_fs.seek(0)
        file_ = default_storage.save(f"models/{project_name}.pkl", temp_fs)

    model_config = ModelConfiguration.objects.create(
        name=None,
        project=project,
        parameters_json=json.dumps(bp),
        recommender_class_name=recommender_class.__name__,
    )
    model = TrainedModel.objects.create(
        configuration=model_config, data_loc=data, model_path=file_
    )

    job.best_config = model_config
    job.tuned_model = model
    job.save()
    ParameterTuningLog.objects.create(
        job=job,
        contents=f"""Job {parameter_tuning_job_id} complete.""",
    )

    return ["complete"]
