# src/recotem/serving/schemas.py
"""Pydantic v2 request/response models for the recotem v1 HTTP API."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Single-request inputs
# ---------------------------------------------------------------------------

class RecommendRequest(BaseModel):
    user_id: Annotated[str, Field(min_length=1, max_length=256)]
    limit: Annotated[int, Field(ge=1, le=1000)] = 10
    exclude_items: Annotated[list[str] | None, Field(max_length=1000)] = None
    context: dict[str, Any] | None = None


class RecommendRelatedRequest(BaseModel):
    seed_items: Annotated[list[str], Field(min_length=1, max_length=100)]
    limit: Annotated[int, Field(ge=1, le=1000)] = 10
    exclude_items: Annotated[list[str] | None, Field(max_length=1000)] = None
    context: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Batch-request inputs
# ---------------------------------------------------------------------------

class BatchRecommendRequest(BaseModel):
    requests: Annotated[list[RecommendRequest], Field(min_length=1, max_length=256)]


class BatchRecommendRelatedRequest(BaseModel):
    requests: Annotated[list[RecommendRelatedRequest], Field(min_length=1, max_length=256)]


# ---------------------------------------------------------------------------
# Common response building blocks
# ---------------------------------------------------------------------------

class RecommendItem(BaseModel):
    item_id: str
    score: float
    # Extra metadata fields are passed through (join result from registry).
    model_config = ConfigDict(extra="allow")


class ErrorDetail(BaseModel):
    code: str
    message: str


class RecommendResponse(BaseModel):
    request_id: str
    recipe: str
    model_version: str
    items: list[RecommendItem]


class BatchResultEntry(BaseModel):
    index: int
    status: Literal["ok", "error"]
    items: list[RecommendItem] | None = None
    error: ErrorDetail | None = None


class BatchRecommendResponse(BaseModel):
    request_id: str
    recipe: str
    model_version: str
    results: list[BatchResultEntry]


# ---------------------------------------------------------------------------
# Recipe discovery
# ---------------------------------------------------------------------------

class RecipeSummary(BaseModel):
    name: str
    model_version: str
    loaded_at: str  # ISO-8601 UTC timestamp at last hot-swap
    supported_verbs: list[str]
    kind: str  # "user-item" | "item-item" | future kinds


class RecipesListResponse(BaseModel):
    recipes: list[RecipeSummary]


class RecipeDetailResponse(RecipeSummary):
    config_digest: str
    algorithms: list[str]
    best_algorithm: str
