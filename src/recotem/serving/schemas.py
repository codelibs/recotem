# src/recotem/serving/schemas.py
"""Pydantic v2 request/response models for the recotem v1 HTTP API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Single-request inputs
# ---------------------------------------------------------------------------

_ItemStr = Annotated[str, Field(min_length=1, max_length=256)]


class RecommendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: Annotated[
        str, Field(min_length=1, max_length=256, description="User identifier")
    ]
    limit: Annotated[
        int, Field(ge=1, le=1000, description="Maximum number of items to return")
    ] = 10
    exclude_items: Annotated[
        list[_ItemStr] | None,
        Field(max_length=1000, description="Item IDs to exclude from results"),
    ] = None
    context: Annotated[
        dict[str, Any] | None,
        Field(description="Reserved: arbitrary per-request context"),
    ] = None


class RecommendRelatedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed_items: Annotated[
        list[_ItemStr],
        Field(
            min_length=1,
            max_length=100,
            description="Item IDs to base recommendations on",
        ),
    ]
    limit: Annotated[
        int, Field(ge=1, le=1000, description="Maximum number of items to return")
    ] = 10
    exclude_items: Annotated[
        list[_ItemStr] | None,
        Field(max_length=1000, description="Item IDs to exclude from results"),
    ] = None
    context: Annotated[
        dict[str, Any] | None,
        Field(description="Reserved: arbitrary per-request context"),
    ] = None


# ---------------------------------------------------------------------------
# Batch-request inputs
# ---------------------------------------------------------------------------

_BATCH_AGGREGATE_LIMIT = 5000


class BatchRecommendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: Annotated[
        list[RecommendRequest],
        Field(
            min_length=1, max_length=256, description="Individual recommend requests"
        ),
    ]

    @model_validator(mode="after")
    def _check_aggregate_limit(self) -> BatchRecommendRequest:
        total = sum(r.limit for r in self.requests)
        if total > _BATCH_AGGREGATE_LIMIT:
            raise ValueError(f"aggregate limit cap exceeded: {_BATCH_AGGREGATE_LIMIT}")
        return self


class BatchRecommendRelatedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: Annotated[
        list[RecommendRelatedRequest],
        Field(
            min_length=1,
            max_length=256,
            description="Individual recommend-related requests",
        ),
    ]

    @model_validator(mode="after")
    def _check_aggregate_limit(self) -> BatchRecommendRelatedRequest:
        total = sum(r.limit for r in self.requests)
        if total > _BATCH_AGGREGATE_LIMIT:
            raise ValueError(f"aggregate limit cap exceeded: {_BATCH_AGGREGATE_LIMIT}")
        return self


# ---------------------------------------------------------------------------
# Common response building blocks
# ---------------------------------------------------------------------------


class RecommendItem(BaseModel):
    item_id: Annotated[str, Field(description="Item identifier")]
    score: Annotated[
        float, Field(allow_inf_nan=False, description="Recommendation score")
    ]
    model_config = ConfigDict(extra="allow")


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(min_length=1, description="Machine-readable error code")]
    message: Annotated[
        str, Field(min_length=1, description="Human-readable error message")
    ]


class RecommendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: Annotated[
        str, Field(min_length=1, description="Unique request identifier")
    ]
    recipe: Annotated[str, Field(min_length=1, description="Recipe name")]
    model_version: Annotated[
        str, Field(min_length=1, description="Artifact SHA-256 digest")
    ]
    items: Annotated[
        list[RecommendItem], Field(description="Recommended items in ranked order")
    ]


class BatchResultEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: Annotated[
        int, Field(description="Zero-based index of the original sub-request")
    ]
    status: Annotated[Literal["ok", "error"], Field(description="Sub-request outcome")]
    items: Annotated[
        list[RecommendItem] | None, Field(description="Recommended items (ok only)")
    ] = None
    error: Annotated[
        ErrorDetail | None, Field(description="Error detail (error only)")
    ] = None


class BatchRecommendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: Annotated[
        str, Field(min_length=1, description="Unique request identifier")
    ]
    recipe: Annotated[str, Field(min_length=1, description="Recipe name")]
    model_version: Annotated[
        str, Field(min_length=1, description="Artifact SHA-256 digest")
    ]
    results: Annotated[
        list[BatchResultEntry], Field(description="Per-request results in input order")
    ]


# ---------------------------------------------------------------------------
# Recipe discovery
# ---------------------------------------------------------------------------


def _parse_loaded_at(v: str) -> str:
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("loaded_at must include timezone info")
    utc = dt.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


class RecipeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, description="Recipe name")]
    model_version: Annotated[
        str, Field(min_length=1, description="Artifact SHA-256 digest")
    ]
    loaded_at: Annotated[
        str, Field(min_length=1, description="ISO-8601 UTC timestamp of last hot-swap")
    ]
    supported_verbs: Annotated[
        list[
            Literal[
                "recommend",
                "recommend-related",
                "batch-recommend",
                "batch-recommend-related",
            ]
        ],
        Field(description="HTTP verbs available for this recipe"),
    ]
    kind: Annotated[
        Literal["user-item", "item-item"], Field(description="Recommendation kind")
    ]

    @field_validator("loaded_at", mode="before")
    @classmethod
    def _validate_loaded_at(cls, v: str) -> str:
        return _parse_loaded_at(v)


class RecipesListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipes: Annotated[list[RecipeSummary], Field(description="All loaded recipes")]


class RecipeDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, description="Recipe name")]
    model_version: Annotated[
        str, Field(min_length=1, description="Artifact SHA-256 digest")
    ]
    loaded_at: Annotated[
        str, Field(min_length=1, description="ISO-8601 UTC timestamp of last hot-swap")
    ]
    supported_verbs: Annotated[
        list[
            Literal[
                "recommend",
                "recommend-related",
                "batch-recommend",
                "batch-recommend-related",
            ]
        ],
        Field(description="HTTP verbs available for this recipe"),
    ]
    kind: Annotated[
        Literal["user-item", "item-item"], Field(description="Recommendation kind")
    ]
    config_digest: Annotated[str, Field(description="SHA-256 digest of recipe config")]
    algorithms: Annotated[
        list[str], Field(description="Algorithms evaluated during training")
    ]
    best_algorithm: Annotated[str, Field(description="Algorithm selected by Optuna")]
    trained_at: str | None = None
    best_class: str | None = None
    best_params: dict[str, Any] | None = None
    best_score: float | None = None
    metric: str | None = None
    cutoff: int | None = None
    tuning: dict[str, Any] | None = None
    data_stats: dict[str, Any] | None = None
    recotem_version: str | None = None
    irspack_version: str | None = None
    recipe_hash: str | None = None

    @field_validator("loaded_at", mode="before")
    @classmethod
    def _validate_loaded_at(cls, v: str) -> str:
        return _parse_loaded_at(v)
