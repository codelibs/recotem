from django.conf import settings
from optuna.storages import RDBStorage

from recotem.api.models import ParameterTuningJob


def create_tuning_study(job: ParameterTuningJob) -> str:
    """Create an Optuna study for a parameter tuning job.

    Returns the study name.
    """
    optuna_storage = RDBStorage(settings.DATABASE_URL)
    study_name = job.study_name()
    optuna_storage.create_new_study(study_name)
    return study_name


def get_optuna_storage() -> RDBStorage:
    """Get an Optuna RDB storage instance connected to the project database."""
    return RDBStorage(settings.DATABASE_URL)
