from functools import lru_cache

from django.conf import settings
from optuna.storages import RDBStorage

from recotem.api.models import ParameterTuningJob


@lru_cache(maxsize=1)
def get_optuna_storage() -> RDBStorage:
    """Get a cached Optuna RDB storage instance with connection pooling."""
    db_url = settings.DATABASE_URL
    # Use psycopg3 dialect for SQLAlchemy (Optuna uses SQLAlchemy internally)
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine_kwargs: dict = {}
    # SQLite doesn't support connection pooling parameters
    if not db_url.startswith("sqlite"):
        engine_kwargs = {"pool_size": 5, "max_overflow": 10}
    return RDBStorage(db_url, engine_kwargs=engine_kwargs)


def create_tuning_study(job: ParameterTuningJob) -> str:
    """Create an Optuna study for a parameter tuning job.

    Returns the study name.
    """
    optuna_storage = get_optuna_storage()
    study_name = job.study_name()
    optuna_storage.create_new_study(study_name)
    return study_name
