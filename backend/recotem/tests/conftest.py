import pytest
from irspack.dataset import MovieLens100KDataManager

from recotem.api.models import TrainingData


@pytest.fixture
def ml100k():
    yield MovieLens100KDataManager().read_interaction()
    TrainingData.objects.all().delete()
