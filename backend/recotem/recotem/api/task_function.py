import re
import time
from logging import Logger
from billiard.context import Process
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type
from uuid import uuid1

import numpy as np
import optuna
import pandas as pd
from django.conf import settings
from optuna.samplers import TPESampler
from optuna.storages import RDBStorage
from optuna.trial import TrialState

from irspack.definitions import InteractionMatrix
from irspack.evaluator import Evaluator
from irspack.optimizers.base_optimizer import (
    LowMemoryError,
    get_optimizer_class,
    study_to_dataframe,
)
from irspack.parameter_tuning.parameter_range import Suggestion, is_valid_param_name
from irspack.recommenders import BaseRecommender
from irspack.utils.default_logger import get_default_logger

DEFAULT_SEARCHNAMES = ["RP3beta", "IALS", "DenseSLIM", "AsymmetricCosineKNN", "SLIM"]


def search_one(
    X: InteractionMatrix,
    evaluator: Evaluator,
    optimizer_names: List[str],
    suggest_overwrites: Dict[str, List[Suggestion]],
    intermediate_result_path: Path,
    random_seed: int,
    logger: Logger,
    **kwargs: Any,
) -> None:
    study = optuna.load_study(
        storage=f"sqlite:///{intermediate_result_path.name}",
        study_name="autopilot",
        sampler=TPESampler(seed=random_seed),
    )

    def objective(trial: optuna.Trial) -> float:
        optimizer_name = trial.suggest_categorical("optimizer_name", optimizer_names)
        assert isinstance(optimizer_name, str)
        optimizer = get_optimizer_class(optimizer_name)(
            X,
            evaluator,
            suggest_overwrite=suggest_overwrites[optimizer_name],
            logger=logger,
        )
        result = optimizer.objective_function(optimizer_name + ".")(trial)
        return result

    study.optimize(objective, n_trials=1)


def autopilot(
    X: InteractionMatrix,
    evaluator: Evaluator,
    n_trials: int = 20,
    memory_budget: int = 4000,  # 4GB
    timeout_overall: Optional[int] = None,
    timeout_singlestep: Optional[int] = None,
    algorithms: List[str] = DEFAULT_SEARCHNAMES,
    random_seed: Optional[int] = None,
    logger: Optional[Logger] = None,
    callback: Optional[Callable[[int, pd.DataFrame], None]] = None,
) -> Tuple[Type[BaseRecommender], Dict[str, Any], pd.DataFrame]:
    RNS = np.random.RandomState(random_seed)
    suggest_overwrites: Dict[str, List[Suggestion]] = {}
    optimizer_names: List[str] = []
    db_path = Path(f".autopilot-{uuid1()}.db")
    for rec_name in algorithms:
        optimizer_class_name = rec_name + "Optimizer"
        optimizer_class = get_optimizer_class(optimizer_class_name)
        try:
            suggest_overwrites[
                optimizer_class_name
            ] = optimizer_class.tune_range_given_memory_budget(X, memory_budget)
            optimizer_names.append(optimizer_class_name)
        except LowMemoryError:
            continue

    if not optimizer_names:
        raise RuntimeError("No available algorithm with given memory.")

    if logger is None:
        logger = get_default_logger()

    logger.info("Trying the following algorithms: %s", optimizer_names)
    storage = RDBStorage(
        url=f"sqlite:///{db_path.name}",
    )
    study_id = storage.create_new_study("autopilot")
    start = time.time()
    study = optuna.load_study(
        storage=f"sqlite:///{db_path.name}",
        study_name="autopilot",
    )
    for _ in range(n_trials):

        p = Process(
            target=search_one,
            args=(
                X,
                evaluator,
                optimizer_names,
                suggest_overwrites,
                db_path,
                RNS.randint(0, 2 ** 31),
                logger,
            ),
        )
        p.start()

        process_start = time.time()

        elapsed_at_start = process_start - start

        timeout_for_this_process: Optional[int] = None
        if timeout_overall is None:
            timeout_for_this_process = timeout_singlestep
        else:
            timeouf_for_this_process = int(timeout_overall - elapsed_at_start)
            if timeout_singlestep is not None:
                timeout_for_this_process = min(
                    timeouf_for_this_process, timeout_singlestep
                )
        p.join(timeout=timeout_for_this_process)

        trial_number = study.trials[-1].number
        if p.exitcode is None:
            logger.info(f"Trial {trial_number} timeout.")
            p.terminate()
            trial_id = storage.get_trial_id_from_study_id_trial_number(
                study_id, trial_number
            )
            try:
                storage.set_trial_values(trial_id, [0.0])
                storage.set_trial_state(trial_id, TrialState.COMPLETE)
            except RuntimeError:  # pragma: no cover
                # this happens if the trial completes before accepting the SIGTERM?
                pass  # pragma: no cover

        if callback is not None:
            callback(trial_number, study_to_dataframe(study))

        now = time.time()
        elapsed = now - start
        if timeout_overall is not None:
            if elapsed > timeout_overall:
                break
    best_params_with_prefix = dict(
        **study.best_trial.params,
        **{
            key: val
            for key, val in study.best_trial.user_attrs.items()
            if is_valid_param_name(key)
        },
    )
    best_params = {
        re.sub(r"^([^\.]*\.)", "", key): value
        for key, value in best_params_with_prefix.items()
    }
    optimizer_name: str = best_params.pop("optimizer_name")
    result_df = study_to_dataframe(study)
    db_path.unlink()
    recommender_class = get_optimizer_class(optimizer_name).recommender_class

    return (recommender_class, best_params, result_df)

settings.OP