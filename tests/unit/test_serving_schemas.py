# tests/unit/test_serving_schemas.py
"""Unit tests for recotem.serving.schemas (v1)."""

import pytest
from pydantic import ValidationError

from recotem.serving.schemas import (
    BatchRecommendRelatedRequest,
    BatchRecommendRequest,
    BatchRecommendResponse,
    BatchResultEntry,
    ErrorDetail,
    RecommendItem,
    RecommendRelatedRequest,
    RecommendRequest,
    RecommendResponse,
    RecipeDetailResponse,
    RecipesListResponse,
    RecipeSummary,
)


def test_recommend_request_defaults_limit_10():
    req = RecommendRequest(user_id="u1")
    assert req.limit == 10
    assert req.exclude_items is None
    assert req.context is None


def test_recommend_request_rejects_empty_user_id():
    with pytest.raises(ValidationError):
        RecommendRequest(user_id="")


def test_recommend_request_limit_bounds():
    with pytest.raises(ValidationError):
        RecommendRequest(user_id="u1", limit=0)
    with pytest.raises(ValidationError):
        RecommendRequest(user_id="u1", limit=1001)


def test_recommend_related_request_requires_non_empty_seed():
    with pytest.raises(ValidationError):
        RecommendRelatedRequest(seed_items=[])


def test_recommend_related_request_caps_seed_at_100():
    RecommendRelatedRequest(seed_items=[f"i{i}" for i in range(100)])
    with pytest.raises(ValidationError):
        RecommendRelatedRequest(seed_items=[f"i{i}" for i in range(101)])


def test_recommend_item_allows_extra_metadata_fields():
    item = RecommendItem(item_id="i1", score=0.5, title="Hello")
    dumped = item.model_dump()
    assert dumped["title"] == "Hello"
    assert dumped["item_id"] == "i1"


def test_batch_recommend_request_requires_at_least_one():
    with pytest.raises(ValidationError):
        BatchRecommendRequest(requests=[])


def test_batch_recommend_request_caps_at_256():
    BatchRecommendRequest(requests=[RecommendRequest(user_id=f"u{i}") for i in range(256)])
    with pytest.raises(ValidationError):
        BatchRecommendRequest(requests=[RecommendRequest(user_id=f"u{i}") for i in range(257)])


def test_batch_recommend_related_request_caps_at_256():
    seeds = [RecommendRelatedRequest(seed_items=[f"i{i}"]) for i in range(256)]
    BatchRecommendRelatedRequest(requests=seeds)
    with pytest.raises(ValidationError):
        BatchRecommendRelatedRequest(requests=seeds + [seeds[0]])


def test_batch_result_entry_status_enum():
    BatchResultEntry(index=0, status="ok", items=[])
    BatchResultEntry(index=0, status="error", error=ErrorDetail(code="X", message="m"))
    with pytest.raises(ValidationError):
        BatchResultEntry(index=0, status="invalid")  # type: ignore[arg-type]


def test_recommend_response_round_trip():
    r = RecommendResponse(
        request_id="req_1",
        recipe="r",
        model_version="sha256:abc",
        items=[RecommendItem(item_id="i1", score=0.9)],
    )
    assert r.model_dump()["items"][0]["item_id"] == "i1"


def test_recipe_summary_supports_verb_list():
    s = RecipeSummary(
        name="r",
        model_version="sha256:abc",
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=["recommend", "recommend-related"],
        kind="user-item",
    )
    assert "recommend" in s.supported_verbs


def test_recipes_list_response_is_serialisable():
    s = RecipeSummary(
        name="r",
        model_version="v1",
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=[],
        kind="user-item",
    )
    payload = RecipesListResponse(recipes=[s]).model_dump()
    assert payload["recipes"][0]["name"] == "r"


def test_recipe_detail_response_includes_config_digest():
    d = RecipeDetailResponse(
        name="r",
        model_version="v1",
        loaded_at="2026-05-21T00:00:00Z",
        supported_verbs=[],
        kind="user-item",
        config_digest="sha256:cfg",
        algorithms=["TopPop"],
        best_algorithm="TopPop",
    )
    assert d.config_digest == "sha256:cfg"
