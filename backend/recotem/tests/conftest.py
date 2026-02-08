import pytest
from irspack.dataset import MovieLens100KDataManager

# If we don't import them here, # test will now run
# as the tasks are not registered.


@pytest.fixture
def ml100k():
    yield MovieLens100KDataManager().read_interaction()


@pytest.fixture
def ml100k_item():
    m = MovieLens100KDataManager()
    yield m.read_item_info()[0].reset_index()
