import gzip
import typing
from tempfile import NamedTemporaryFile

from django.urls import reverse
from irspack.dataset import MovieLens100KDataManager
from django.test import Client
import pandas as pd
from pandas import testing as pd_testing

import pytest

from recotem.api.models import TrainingData


@pytest.fixture
def ml100k():
    yield MovieLens100KDataManager().read_interaction()


I_O_functions: typing.List[
    typing.Tuple[
        str,
        typing.Callable[[pd.DataFrame, typing.IO], None],
        typing.Callable[[typing.IO], pd.DataFrame],
    ],
] = [
    (
        ".csv",
        lambda df, file: df.to_csv(file, index=False),
        lambda file: pd.read_csv(file, parse_dates=["timestamp"]),
    ),
    (
        ".tsv",
        lambda df, file: df.to_csv(file, sep="\t", index=False),
        lambda file: pd.read_csv(file, sep="\t", parse_dates=["timestamp"]),
    ),
    (
        ".json",
        lambda df, file: df.to_json(file),
        lambda file: pd.read_json(file),
    ),
    (
        ".ndjson",
        lambda df, file: df.to_json(file, lines=True, orient="records"),
        lambda file: pd.read_json(file, lines=True, orient="records"),
    ),
    (
        ".jsonl",
        lambda df, file: df.to_json(file, lines=True, orient="records"),
        lambda file: pd.read_json(file, lines=True, orient="records"),
    ),
    (
        ".pickle",
        lambda df, file: df.to_pickle(file),
        lambda file: pd.read_pickle(file),
    ),
    (
        ".pkl",
        lambda df, file: df.to_pickle(file),
        lambda file: pd.read_pickle(file),
    ),
]


@pytest.mark.django_db
def test_invalid_compression(client: Client, ml100k: pd.DataFrame):
    project_url = reverse("project-list")
    data_url = reverse("training_data-list")

    resp_failing_project_creation_invalid_compression = client.post(
        project_url,
        dict(name="invalid_compression", user_column="userId", item_column="movieId"),
    )
    failing_project_id_item = resp_failing_project_creation_invalid_compression.json()[
        "id"
    ]

    unk_compression_file = NamedTemporaryFile(suffix=".csv.unknown")
    ml100k.to_csv(unk_compression_file, index=False)
    unk_compression_file.seek(0)

    resp = client.post(
        data_url,
        dict(project=failing_project_id_item, upload_path=unk_compression_file),
    )
    assert resp.status_code == 400
    assert resp.json()[0] == "Only .gzip or .gz compression are supported."


@pytest.mark.django_db
def test_invalid_file_format(client: Client, ml100k: pd.DataFrame):
    project_url = reverse("project-list")
    data_url = reverse("training_data-list")

    resp_failing_project_creation_invalid_item = client.post(
        project_url,
        dict(name="invalid_suffix", user_column="userId", item_column="movieid"),
    )
    failing_project_id_item = resp_failing_project_creation_invalid_item.json()["id"]

    no_ext_file = NamedTemporaryFile()
    ml100k.to_csv(no_ext_file, index=False)
    no_ext_file.seek(0)

    resp = client.post(
        data_url, dict(project=failing_project_id_item, upload_path=no_ext_file)
    )
    assert resp.status_code == 400
    assert resp.json()[0] == "Suffix like .csv or .json.gzip or pickle.gz required."

    unknown_ext_file = NamedTemporaryFile(suffix=".unknown")
    ml100k.to_csv(unknown_ext_file, index=False)
    unknown_ext_file.seek(0)

    resp = client.post(
        data_url, dict(project=failing_project_id_item, upload_path=unknown_ext_file)
    )
    assert resp.status_code == 400

    message: str = resp.json()[0]
    assert message.startswith(".unknown file not supported.")

    toomany_prefix_file = NamedTemporaryFile(suffix=".csv.json.tgz")
    ml100k.to_csv(toomany_prefix_file, index=False)
    toomany_prefix_file.seek(0)
    resp = client.post(
        data_url, dict(project=failing_project_id_item, upload_path=toomany_prefix_file)
    )
    assert resp.status_code == 400

    wrong_ext_file = NamedTemporaryFile(suffix=".csv.gzip")
    wrong_ext_file_gzip = gzip.open(wrong_ext_file, mode="wb")
    ml100k.to_pickle(wrong_ext_file_gzip)
    wrong_ext_file_gzip.close()
    wrong_ext_file.seek(0)
    resp = client.post(
        data_url, dict(project=failing_project_id_item, upload_path=wrong_ext_file)
    )
    assert resp.status_code == 400
    assert resp.json()[0].startswith("Failed to parse")


@pytest.mark.django_db
def test_data_post(client: Client, ml100k: pd.DataFrame):
    project_url = reverse("project-list")
    data_url = reverse("training_data-list")
    resp_failing_project_creation_invalid_item = client.post(
        project_url,
        dict(name="ml_invalid_item", user_column="userId", item_column="movieid"),
    )
    failing_project_id_item = resp_failing_project_creation_invalid_item.json()["id"]

    csv_file = NamedTemporaryFile(suffix=".csv")
    ml100k.to_csv(csv_file, index=False)
    csv_file.seek(0)

    resp = client.post(
        data_url, dict(project=failing_project_id_item, upload_path=csv_file)
    )
    assert resp.status_code == 400

    resp_failing_project_creation_invalid_user = client.post(
        project_url,
        dict(name="ml_invalid_user", user_column="userid", item_column="movieId"),
    )
    failing_project_id_user = resp_failing_project_creation_invalid_user.json()["id"]

    csv_file.seek(0)
    resp = client.post(
        data_url, dict(project=failing_project_id_user, upload_path=csv_file)
    )
    assert resp.status_code == 400

    resp_successfull_project_creation = client.post(
        project_url,
        dict(
            name="ml_valid",
            user_column="userId",
            item_column="movieId",
            time_column="timestamp",
        ),
    )
    successfull_project_id = resp_successfull_project_creation.json()["id"]
    csv_file.seek(0)

    csv_file_with_invalid_time = NamedTemporaryFile(suffix=".csv")
    ml100k.rename(columns={"timestamp": "timestamp_"}).to_csv(
        csv_file_with_invalid_time, index=False
    )
    csv_file_with_invalid_time.seek(0)

    resp_no_timestamp = client.post(
        data_url,
        dict(project=successfull_project_id, upload_path=csv_file_with_invalid_time),
    )
    assert resp_no_timestamp.status_code == 400
    assert (
        resp_no_timestamp.json()[0]
        == 'Column "timestamp" not found in the upload file.'
    )

    resp = client.post(
        data_url, dict(project=successfull_project_id, upload_path=csv_file)
    )
    assert resp.status_code == 201
    data_created: TrainingData = TrainingData.objects.get(id=resp.json()["id"])
    df_uploaded = pd.read_csv(data_created.upload_path, parse_dates=["timestamp"])
    pd_testing.assert_frame_equal(df_uploaded, ml100k)


@pytest.mark.django_db
@pytest.mark.parametrize("ext, dump_function, load_function", I_O_functions)
def test_data_post_with_pkl_compression(
    client: Client,
    ml100k: pd.DataFrame,
    ext: str,
    dump_function: typing.Callable[[pd.DataFrame, typing.IO], None],
    load_function: typing.Callable[[typing.IO], pd.DataFrame],
):
    project_url = reverse("project-list")
    data_url = reverse("training_data-list")
    project_resp = client.post(
        project_url,
        dict(
            name=f"ml_gzip_pkl_test{ext}", user_column="userId", item_column="movieId"
        ),
    )

    project_id = project_resp.json()["id"]
    dump_file = NamedTemporaryFile(suffix=f"{ext}.gz")
    pkl_gzip_file = gzip.open(dump_file, mode="wb")
    dump_function(ml100k, pkl_gzip_file)
    pkl_gzip_file.close()
    dump_file.seek(0)

    response = client.post(data_url, dict(project=project_id, upload_path=dump_file))
    assert response.status_code == 201
    data_id = response.json()["id"]
    dump_file.close()

    data_created: TrainingData = TrainingData.objects.get(id=data_id)
    df_uploaded = load_function(gzip.open(data_created.upload_path))
    pd_testing.assert_frame_equal(df_uploaded, ml100k)


@pytest.mark.django_db
def test_datetime(
    client: Client,
    ml100k: pd.DataFrame,
):
    project_url = reverse("project-list")
    data_url = reverse("training_data-list")
    project_resp = client.post(
        project_url,
        dict(
            name=f"ml_project_wrong_timecolumn",
            user_column="userId",
            item_column="movieId",
            time_column="timestamp",
        ),
    )

    project_id = project_resp.json()["id"]
    pkl_file = NamedTemporaryFile(suffix=f".json.gz")
    pkl_gzip_file = gzip.open(pkl_file, mode="wb")
    ml100k_dummy_ts = ml100k.copy()
    ml100k_dummy_ts["timestamp"] = "This is not a time!"
    ml100k_dummy_ts.to_json(pkl_gzip_file)
    pkl_gzip_file.close()
    pkl_file.seek(0)

    response = client.post(data_url, dict(project=project_id, upload_path=pkl_file))
    assert response.status_code == 400
    assert 'Could not interpret "timestamp" as datetime.' == response.json()[0]
