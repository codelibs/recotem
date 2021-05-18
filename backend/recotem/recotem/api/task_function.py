from logging import Logger
from billiard.context import Process
from billiard.connection import Pipe
from typing import List, Dict, Optional
from irspack import InteractionMatrix, Evaluator
from irspack.parameter_tuning import Suggestion
from irspack.optimizers.autopilot import TaskBackend, search_one
from irspack.utils import df_to_sparse
from irspack.recommenders import get_recommender_class

from .models import TrainingData, ModelConfiguration


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


def learn_model(data: TrainingData, model_config: ModelConfiguration):
    model_config.recommender_class_name
