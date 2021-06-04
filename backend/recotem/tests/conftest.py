from typing import Type

import pytest
from django.test import Client
from irspack.dataset import MovieLens100KDataManager

from recotem.api.models import TrainingData, User

# If we don't import them here, # test will now run
# as the tasks are not registered.
from recotem.api.tasks import run_search, start_tuning_job, train_recommender


@pytest.fixture
def ml100k():
    yield MovieLens100KDataManager().read_interaction()
