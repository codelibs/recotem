# tests/unit/test_v1_recommend.py
"""POST /v1/recipes/{name}:recommend — single user→items."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from recotem.serving.registry import ModelEntry, ModelRegistry
from tests.conftest import build_v1_app

_FAKE_SHA256_HEX = "a" * 64  # 64 lowercase hex chars for a valid Sha256Hex marker


def _entry_with_recommender(recommender) -> ModelEntry:
    """Build a loaded ModelEntry around the given recommender mock.

    The artifact SHA-256 lives on `_loaded_marker[1]`; pass it through
    that field rather than introducing a parallel attribute.
    """
    return ModelEntry(
        name="demo",
        recommender=recommender,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )


def _app_with_entry(entry: ModelEntry) -> TestClient:
    registry = ModelRegistry()
    registry.replace("demo", entry)
    return TestClient(build_v1_app(registry))


def test_recommend_returns_items_and_envelope():
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9), ("i2", 0.5)]
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recipe"] == "demo"
    assert body["model_version"] == f"sha256:{_FAKE_SHA256_HEX}"
    assert [i["item_id"] for i in body["items"]] == ["i1", "i2"]
    assert "request_id" in body
    rec.get_recommendation_for_known_user_id.assert_called_once_with("u1", 2)


def test_recommend_404_when_user_unknown():
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = KeyError("u1")
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 404
    body = r.json()
    # Flat error body: top-level "code" and "detail" (string).
    assert body["code"] == "UNKNOWN_USER"
    assert isinstance(body["detail"], str)


def test_recommend_503_when_recipe_not_loaded():
    stub = ModelEntry(
        name="demo",
        recommender=None,
        header={},
        kid="",
        loaded=False,
    )
    client = _app_with_entry(stub)
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1"})
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "RECIPE_UNAVAILABLE"
    assert isinstance(body["detail"], str)


def test_recommend_422_on_empty_user_id():
    rec = MagicMock()
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "", "limit": 5})
    assert r.status_code == 422


def test_recommend_404_when_recipe_missing_from_registry():
    rec = MagicMock()
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/unknown:recommend", json={"user_id": "u1"})
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "RECIPE_NOT_FOUND"
    assert isinstance(body["detail"], str)


# ---------------------------------------------------------------------------
# D. exclude_items + extra="forbid"
# ---------------------------------------------------------------------------


def test_recommend_excludes_items() -> None:
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [
        ("i1", 0.9),
        ("i2", 0.8),
        ("i3", 0.7),
        ("i4", 0.6),
        ("i5", 0.5),
    ]
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1", "limit": 5, "exclude_items": ["i2", "i4"]},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = [i["item_id"] for i in items]
    assert "i2" not in ids
    assert "i4" not in ids
    assert len(ids) == 3


def test_recommend_rejects_context_field() -> None:
    """context field has been removed; sending it must produce 422 (extra=forbid)."""
    rec = MagicMock()
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1", "limit": 1, "context": {"foo": "bar"}},
    )
    assert r.status_code == 422, r.text


def test_recommend_rejects_extra_field() -> None:
    rec = MagicMock()
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "u1", "limit": 1, "unknown_field": "x"},
    )
    assert r.status_code == 422


def test_recommend_rejects_oversized_user_id() -> None:
    rec = MagicMock()
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post(
        "/v1/recipes/demo:recommend",
        json={"user_id": "a" * 257, "limit": 1},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Finding 11: KeyError mis-attribution fix
# ---------------------------------------------------------------------------


def _entry_with_user_map(recommender, known_users: list[str]) -> ModelEntry:
    """Build a loaded entry whose _mapper.user_id_to_index knows *known_users*."""
    recommender._mapper.user_id_to_index = {u: i for i, u in enumerate(known_users)}
    return ModelEntry(
        name="demo",
        recommender=recommender,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )


def test_unknown_user_not_in_map_yields_unknown_user_error() -> None:
    """User NOT in _mapper.user_id_to_index must yield UNKNOWN_USER (404),
    not INTERNAL_ERROR — membership is checked before irspack is called."""
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = KeyError("u-ghost")
    # u-ghost is NOT in the known map
    entry = _entry_with_user_map(rec, known_users=["u-known"])
    client = _app_with_entry(entry)
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u-ghost"})
    assert r.status_code == 404
    assert r.json()["code"] == "UNKNOWN_USER", (
        "User absent from mapper must yield UNKNOWN_USER, not INTERNAL_ERROR"
    )


def test_known_user_unexpected_keyerror_yields_internal_error() -> None:
    """When the user IS in _mapper.user_id_to_index but irspack raises
    KeyError anyway, the response must be INTERNAL_ERROR (500)."""
    import structlog.testing

    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.side_effect = KeyError(
        "internal-irspack-bug"
    )
    # u-known IS in the user map
    entry = _entry_with_user_map(rec, known_users=["u-known"])
    client = _app_with_entry(entry)

    with structlog.testing.capture_logs() as cap:
        r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u-known"})

    assert r.status_code == 500, r.text
    body = r.json()
    assert body.get("code") == "INTERNAL_ERROR", (
        "Unexpected KeyError from irspack for a known user must yield INTERNAL_ERROR"
    )
    # The log event "recommender_unexpected_key_error" must be emitted
    log_events = [e.get("event") for e in cap]
    assert "recommender_unexpected_key_error" in log_events, (
        f"Expected recommender_unexpected_key_error log; got: {log_events!r}"
    )


def test_batch_known_user_unexpected_keyerror_yields_internal_error() -> None:
    """In :batch-recommend, a user in the id-map that triggers KeyError in irspack
    must produce status=error / code=INTERNAL_ERROR for that element (not UNKNOWN_USER).
    """
    import structlog.testing

    from tests.conftest import build_v1_app

    rec = MagicMock()
    # u-known is in the id-map, but irspack raises KeyError anyway
    rec._mapper.user_id_to_index = {"u-known": 0}
    rec.get_recommendation_for_known_user_id.side_effect = KeyError("irspack-internal")

    entry = ModelEntry(
        name="demo",
        recommender=rec,
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )
    registry = ModelRegistry()
    registry.replace("demo", entry)
    client = TestClient(build_v1_app(registry))

    with structlog.testing.capture_logs() as cap:
        r = client.post(
            "/v1/recipes/demo:batch-recommend",
            json={"requests": [{"user_id": "u-known"}]},
        )

    assert r.status_code == 200, r.text
    result = r.json()["results"][0]
    assert result["status"] == "error"
    assert result["error"]["code"] == "INTERNAL_ERROR", (
        f"Unexpected KeyError for known user must yield INTERNAL_ERROR; got {result!r}"
    )
    log_events = [e.get("event") for e in cap]
    assert "recommender_unexpected_key_error" in log_events, (
        f"Expected recommender_unexpected_key_error log; got {log_events!r}"
    )


def test_recommend_sets_model_version_response_header():
    rec = MagicMock()
    rec.get_recommendation_for_known_user_id.return_value = [("i1", 0.9)]
    client = _app_with_entry(_entry_with_recommender(rec))
    r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 1})
    assert r.status_code == 200, r.text
    header_val = r.headers.get("x-recotem-model-version")
    assert header_val, "X-Recotem-Model-Version header must be present and non-empty"
    assert header_val == r.json()["model_version"]


# ---------------------------------------------------------------------------
# F4: user_known AttributeError path (unexpected recommender layout)
# ---------------------------------------------------------------------------


def _entry_with_broken_user_mapper() -> ModelEntry:
    """Build a loaded entry whose recommender has no accessible _mapper
    (spec=[] ensures accessing any attribute raises AttributeError).
    This mimics an irspack API incompatibility for the user-id mapping path.
    """

    # Use a class with a descriptor _mapper that raises AttributeError on access,
    # while get_recommendation_for_known_user_id returns normally.
    class _BrokenMapper:
        """Descriptor that raises AttributeError on __get__."""

        def __get__(self, obj, objtype=None):
            raise AttributeError("_mapper not available")

    class _BrokenRec:
        _mapper = _BrokenMapper()

        def get_recommendation_for_known_user_id(self, user_id, limit):
            return [("i1", 0.9)]

    return ModelEntry(
        name="demo",
        recommender=_BrokenRec(),
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )


def test_recommend_user_known_attribute_error_logs_warning_and_still_serves() -> None:
    """When _mapper.user_id_to_index raises AttributeError (unexpected irspack layout),
    :recommend must log recommender_layout_unexpected, increment the metric counter,
    and still serve the result (irspack call succeeds despite broken mapper).
    """
    import structlog.testing

    entry = _entry_with_broken_user_mapper()
    client = _app_with_entry(entry)

    with structlog.testing.capture_logs() as cap:
        r = client.post(
            "/v1/recipes/demo:recommend", json={"user_id": "u1", "limit": 1}
        )

    # irspack call succeeds — the response is 200 with items
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"][0]["item_id"] == "i1"

    # recommender_layout_unexpected must be logged at WARNING
    log_events = [e.get("event") for e in cap]
    assert "recommender_layout_unexpected" in log_events, (
        f"Expected recommender_layout_unexpected warning log; got: {log_events!r}"
    )


def test_recommend_user_known_attribute_error_then_key_error_yields_internal_error() -> (
    None
):
    """When _mapper.user_id_to_index raises AttributeError (user_known=None)
    AND irspack subsequently raises KeyError, the response must be INTERNAL_ERROR (500),
    not UNKNOWN_USER — user membership is unknown, not confirmed-absent.
    """
    import structlog.testing

    class _BrokenMapperRec:
        @property
        def _mapper(self):
            raise AttributeError("_mapper not available")

        def get_recommendation_for_known_user_id(self, user_id, limit):
            raise KeyError(user_id)

    entry = ModelEntry(
        name="demo",
        recommender=_BrokenMapperRec(),
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )
    client = _app_with_entry(entry)

    with structlog.testing.capture_logs() as cap:
        r = client.post("/v1/recipes/demo:recommend", json={"user_id": "u-ghost"})

    assert r.status_code == 500, (
        "AttributeError then KeyError must yield INTERNAL_ERROR (500), not UNKNOWN_USER"
    )
    body = r.json()
    assert body.get("code") == "INTERNAL_ERROR", (
        f"Expected INTERNAL_ERROR; got {body!r}"
    )
    log_events = [e.get("event") for e in cap]
    assert "recommender_layout_unexpected" in log_events, (
        f"recommender_layout_unexpected must be logged; got: {log_events!r}"
    )


def test_batch_recommend_user_known_attribute_error_logs_warning() -> None:
    """In :batch-recommend, AttributeError on _mapper.user_id_to_index must log
    recommender_layout_unexpected and yield INTERNAL_ERROR for that element when
    irspack subsequently raises KeyError.
    """
    import structlog.testing

    class _BrokenMapperRec:
        @property
        def _mapper(self):
            raise AttributeError("_mapper not available")

        def get_recommendation_for_known_user_id(self, user_id, limit):
            raise KeyError(user_id)

    entry = ModelEntry(
        name="demo",
        recommender=_BrokenMapperRec(),
        header={},
        kid="test",
        metadata_df=None,
        metadata_index=None,
        loaded=True,
        _loaded_marker=(None, _FAKE_SHA256_HEX),
        loaded_at_unix=1747800000.0,
    )
    registry = ModelRegistry()
    registry.replace("demo", entry)
    client = TestClient(build_v1_app(registry))

    with structlog.testing.capture_logs() as cap:
        r = client.post(
            "/v1/recipes/demo:batch-recommend",
            json={"requests": [{"user_id": "u-ghost"}]},
        )

    assert r.status_code == 200, r.text  # batch always returns 200 for element errors
    result = r.json()["results"][0]
    assert result["status"] == "error"
    assert result["error"]["code"] == "INTERNAL_ERROR", (
        f"AttributeError then KeyError in batch must yield INTERNAL_ERROR; got {result!r}"
    )
    log_events = [e.get("event") for e in cap]
    assert "recommender_layout_unexpected" in log_events, (
        f"recommender_layout_unexpected must be logged in batch; got: {log_events!r}"
    )
