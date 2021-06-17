import pytest
from irspack.dataset import MovieLens100KDataManager

# If we don't import them here, # test will now run
# as the tasks are not registered.
from recotem.api.tasks import run_search, start_tuning_job, task_train_recommender


@pytest.fixture
def ml100k():
    yield MovieLens100KDataManager().read_interaction()
