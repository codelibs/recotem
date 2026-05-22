# src/recotem/serving/schemas.py
"""Pydantic v2 request/response models for the recotem v1 HTTP API."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Aggregate ``limit`` cap across all sub-requests in a single batch call.
# Documented in docs/api-reference.md. Bounds total candidate work per HTTP
# request so a 256-element batch cannot demand 256_000 items in one go.
BATCH_AGGREGATE_LIMIT = 5000

# Machine-readable error codes emitted by the v1 API. Kept as a Literal
# union so OpenAPI / SDK generation produces an exhaustive enum and any
# new code added in routes/auth/app fails type-check until listed here.
ErrorCode = Literal[
    "RECIPE_NOT_FOUND",
    "RECIPE_UNAVAILABLE",
    "UNKNOWN_USER",
    "UNKNOWN_SEED_ITEMS",
    "NO_CANDIDATES",
    "VALIDATION_ERROR",
    "MISSING_API_KEY",
    "INVALID_API_KEY",
    "INTERNAL_ERROR",
]

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


# ---------------------------------------------------------------------------
# Batch-request inputs
# ---------------------------------------------------------------------------
#
# Per-element schema validation is deferred to the handler so a single bad
# entry does not 422 the whole batch — instead the bad entry surfaces as
# ``BatchResultEntry(status="error", code="VALIDATION_ERROR")``. The
# ``list[dict]`` typing here only enforces the list-level invariants
# (1..256 elements). Aggregate ``limit`` is checked in the handler after
# per-element parsing.


class BatchRecommendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: Annotated[
        list[dict[str, Any]],
        Field(
            min_length=1, max_length=256, description="Individual recommend requests"
        ),
    ]
    include_metadata: Annotated[
        bool,
        Field(
            description=(
                "When True, include per-item metadata fields in each result "
                "(same as single-recommend enrichment). Default False preserves "
                "performance for large batches."
            )
        ),
    ] = False


class BatchRecommendRelatedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: Annotated[
        list[dict[str, Any]],
        Field(
            min_length=1,
            max_length=256,
            description="Individual recommend-related requests",
        ),
    ]
    include_metadata: Annotated[
        bool,
        Field(
            description=(
                "When True, include per-item metadata fields in each result "
                "(same as single-recommend enrichment). Default False preserves "
                "performance for large batches."
            )
        ),
    ] = False


# ---------------------------------------------------------------------------
# Branded string types for artifact digests
# ---------------------------------------------------------------------------

# ``sha256:<64 hex chars>`` — used for model_version in responses.
Sha256Hex = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

# Plain 64-char hex string — used for recipe_hash in artifact headers.
HexHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


# ---------------------------------------------------------------------------
# Common response building blocks
# ---------------------------------------------------------------------------


class RecommendItem(BaseModel):
    item_id: Annotated[
        str, Field(min_length=1, max_length=256, description="Item identifier")
    ]
    score: Annotated[
        float, Field(allow_inf_nan=False, description="Recommendation score")
    ]
    model_config = ConfigDict(extra="allow")


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[ErrorCode, Field(description="Machine-readable error code")]
    message: Annotated[
        str, Field(min_length=1, description="Human-readable error message")
    ]


class RecommendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: Annotated[
        str, Field(min_length=1, description="Unique request identifier")
    ]
    recipe: Annotated[str, Field(min_length=1, description="Recipe name")]
    model_version: Annotated[Sha256Hex, Field(description="Artifact SHA-256 digest")]
    items: Annotated[
        list[RecommendItem], Field(description="Recommended items in ranked order")
    ]


class BatchResultOk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: Annotated[
        int,
        Field(ge=0, description="Zero-based index of the original sub-request"),
    ]
    status: Literal["ok"]
    items: Annotated[
        list[RecommendItem], Field(description="Recommended items in ranked order")
    ]


class BatchResultErr(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: Annotated[
        int,
        Field(ge=0, description="Zero-based index of the original sub-request"),
    ]
    status: Literal["error"]
    error: Annotated[ErrorDetail, Field(description="Error detail")]


# Discriminated union: ``status`` field selects the concrete class at
# parse/serialise time so the ok/error invariant is enforced by the type
# system rather than a ``@model_validator``.
BatchResultEntry = Annotated[
    BatchResultOk | BatchResultErr, Field(discriminator="status")
]


class BatchRecommendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: Annotated[
        str, Field(min_length=1, description="Unique request identifier")
    ]
    recipe: Annotated[str, Field(min_length=1, description="Recipe name")]
    model_version: Annotated[Sha256Hex, Field(description="Artifact SHA-256 digest")]
    results: Annotated[
        list[BatchResultEntry], Field(description="Per-request results in input order")
    ]


# ---------------------------------------------------------------------------
# Recipe discovery
# ---------------------------------------------------------------------------


class RecipeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, description="Recipe name")]
    model_version: Annotated[
        Sha256Hex | None,
        Field(description="Artifact SHA-256 digest, or null for stub entries"),
    ]
    loaded_at: Annotated[
        AwareDatetime,
        Field(description="UTC timestamp of last successful hot-swap"),
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
        Field(min_length=1, description="HTTP verbs available for this recipe"),
    ]
    kind: Annotated[
        Literal["user-item", "item-item"], Field(description="Recommendation kind")
    ]


class RecipesListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipes: Annotated[list[RecipeSummary], Field(description="All loaded recipes")]


class RecipeDetailResponse(RecipeSummary):
    config_digest: Annotated[
        Sha256Hex | None,
        Field(description="SHA-256 digest of recipe config, or null when unavailable"),
    ] = None
    algorithms: Annotated[
        list[str], Field(description="Algorithms evaluated during training")
    ]
    best_algorithm: Annotated[str, Field(description="Algorithm selected by Optuna")]
    trained_at: AwareDatetime | None = None
    best_class: str | None = None
    best_params: dict[str, Any] | None = None
    best_score: float | None = None
    metric: Literal["ndcg", "map", "recall", "hit"] | None = None
    cutoff: Annotated[int, Field(ge=1)] | None = None
    tuning: dict[str, Any] | None = None
    data_stats: dict[str, Any] | None = None
    recotem_version: Annotated[str, Field(pattern=r"^\d+\.\d+")] | None = None
    irspack_version: Annotated[str, Field(pattern=r"^\d+\.\d+")] | None = None
    recipe_hash: HexHash | None = None
