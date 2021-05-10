import pandas as pd
import pickle
import json
import tempfile
from django.core.files.storage import default_storage
from irspack.utils import df_to_sparse
from irspack.utils.id_mapping import IDMappedRecommender
from irspack import (
    Evaluator,
    split_dataframe_partial_user_holdout,
)
import scipy.sparse as sps
from irspack import autopilot
from .models import (
    EvaluationConfig,
    Project,
    SplitConfig,
    ParameterTuningJob,
    ModelConfiguration,
    TrainedModel,
    TrainingData,
)

from ..celery import app


@app.task(bind=True)
def execute_irspack(self, parameter_tuning_job_id: int) -> None:
    logs = [
        dict(message=f"Started the parameter tuning jog {parameter_tuning_job_id} ")
    ]
    self.update_state(state="STARTED", meta=logs)
    tl: ParameterTuningJob = ParameterTuningJob.objects.get(id=parameter_tuning_job_id)
    data: TrainingData = tl.data
    project: Project = data.project
    project_name: str = project.name
    split: SplitConfig = tl.split
    evaluation: EvaluationConfig = tl.evaluation
    df: pd.DataFrame = pd.read_csv(data.upload_path)
    user_column = project.user_column
    item_column = project.item_column

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

    def callback(i: int, df: pd.DataFrame) -> None:
        if df.shape[0] == 0:
            return
        logs.append(
            dict(
                message=f"Trial {i} finished.", result=json.loads(df.iloc[-1].to_json())
            )
        )
        self.update_state(state="STARTED", meta=logs)

    recommender_class, bp, _ = autopilot(
        X_tv_train,
        evaluator,
        n_trials=tl.n_trials,
        memory_budget=tl.memory_budget,
        timeout_overall=tl.timeout_overall,
        timeout_singlestep=tl.timeout_singlestep,
        random_seed=tl.random_seed,
        callback=callback,
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

    tl.best_config = model_config
    tl.tuned_model = model
    tl.save()
    return logs
