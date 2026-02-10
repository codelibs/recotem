import pytest
from celery.contrib.testing.worker import start_worker
from irspack.dataset import MovieLens100KDataManager

from recotem.celery import app as _celery_app


@pytest.fixture(scope="session")
def celery_app():
    """Provide the recotem Celery app configured for in-process testing.

    Overrides pytest-celery's Docker-based worker with a thread-based one
    so that recotem tasks are available without a custom Docker image.
    """
    _celery_app.conf.update(
        broker_url="memory://",
        result_backend="cache+memory://",
    )
    return _celery_app


@pytest.fixture
def celery_worker(celery_app):
    """Thread-based Celery worker that has access to recotem tasks."""
    with start_worker(celery_app, perform_ping_check=False) as worker:
        yield worker


@pytest.fixture
def ml100k():
    yield MovieLens100KDataManager().read_interaction()


@pytest.fixture
def ml100k_item():
    m = MovieLens100KDataManager()
    yield m.read_item_info()[0].reset_index()
