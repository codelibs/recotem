from typing import Type

import pytest
from django.test import Client
from irspack.dataset import MovieLens100KDataManager

from recotem.api.models import TrainingData, User


@pytest.fixture
def ml100k():
    yield MovieLens100KDataManager().read_interaction()
    TrainingData.objects.all().delete()
