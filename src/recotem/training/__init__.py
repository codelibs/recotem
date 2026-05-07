# ruff: noqa: I001
# _compat MUST be imported before irspack to apply the IPython stub
# (irspack -> fastprogress -> IPython.display, but IPython is not a real
# dependency).  Importing this package eagerly resolves the stub.
from recotem.training import _compat as _compat  # noqa: F401
from recotem.training.pipeline import TrainingError, TrainResult, run_training

__all__ = ["TrainResult", "TrainingError", "run_training"]
