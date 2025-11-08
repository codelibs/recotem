import os

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker
from irspack.dataset import MovieLens100KDataManager

# If we don't import them here, # test will now run
# as the tasks are not registered.
from recotem.api.tasks import run_search, start_tuning_job, task_train_recommender


@pytest.fixture
def ml100k():
    yield MovieLens100KDataManager().read_interaction()


@pytest.fixture
def ml100k_item():
    m = MovieLens100KDataManager()
    yield m.read_item_info()[0].reset_index()


@pytest.fixture(scope="session")
def celery_config():
    """Celery configuration for testing"""
    return {
        "broker_url": "amqp://user:bitnami@queue:5672",
        "result_backend": "django-db",
        "task_always_eager": False,
        "task_eager_propagates": True,
    }


@pytest.fixture(scope="session")
def celery_worker_parameters():
    """Override celery worker parameters to avoid Docker-in-Docker issues"""
    return {
        "queues": ("celery",),
        "pool": "solo",  # Use solo pool to avoid multiprocessing issues in Docker
    }


@pytest.fixture(scope="session")
def celery_enable_logging():
    """Enable celery logging for debugging"""
    return True


@pytest.fixture(scope="session")
def celery_includes():
    """Ensure tasks are imported"""
    return ["recotem.api.tasks"]


@pytest.fixture(scope="session")
def celery_worker_pool():
    """Use solo pool to avoid multiprocessing issues"""
    return "solo"


@pytest.fixture(scope="session")
def use_celery_app_trap():
    """Don't use docker for celery worker"""
    return False


@pytest.fixture(scope="session")
def celery_app():
    """Get the Django Celery app for testing"""
    # Setup Django settings before importing the Celery app
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "recotem.settings")
    import django

    django.setup()

    from recotem.celery import app

    return app


@pytest.fixture(scope="session")
def celery_worker(celery_app):
    """Start a Celery worker for testing (non-Docker)"""
    worker = start_worker(
        celery_app,
        pool="solo",
        loglevel="info",
        perform_ping_check=False,
    )
    worker.__enter__()
    yield worker
    worker.__exit__(None, None, None)
