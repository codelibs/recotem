# src/recotem/serving/schemas.py
"""Pydantic v2 request/response models for the recotem v1 HTTP API."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field

# Aggregate ``limit`` cap across all sub-requests in a single batch call.
# Documented in docs/api-reference.md. Bounds total candidate work per HTTP
# request so a 256-element batch cannot demand 256_000 items in one go.
BATCH_AGGREGATE_LIMIT = 5000

# Aggregate cold-seed cap across all sub-requests in a single
# ``:batch-recommend-related`` call. Documented in docs/api-reference.md and
# docs/operations.md.
#
# Why a SECOND cap rather than reusing BATCH_AGGREGATE_LIMIT: that one caps
# ``sum(limit)`` -- response volume -- which is a different dimension. Case C
# (a cold seed carrying ``item_features``) runs one irspack conjugate-gradient
# solve PER COLD SEED, so ``limit: 1`` x 256 elements x 100 cold seeds keeps
# the aggregate limit at 256 (2% of its cap) while demanding 25_600 solves.
# Every other path costs one solve per element at most.
#
# Why 512: measured ~0.25-0.45 ms/solve, so 512 bounds the worst case at
# ~230 ms of single-threaded CPU per HTTP request -- material on a
# single-process uvicorn but not an outage. The measurement is near-flat in
# ``n_components`` (0.27 ms at 8, 0.30 ms at 128, 0.45 ms at 256) and in the
# encoded feature dimension: the solve is call-overhead-dominated, not
# Cholesky-dominated, at every size a recipe can produce. So this bound does
# NOT need to shrink for a production-sized model.
#
# 512 also leaves the whole existing request space intact: a single
# ``:recommend-related`` tops out at 100 solves (``seed_items`` max_length),
# so the cap only ever binds on batch fan-out, and even then admits five
# maximal elements.
BATCH_COLD_SEED_SOLVE_LIMIT = 512

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
    "FEATURES_NOT_SUPPORTED",
    "FEATURE_VALUE_UNUSABLE",
]

# ---------------------------------------------------------------------------
# Single-request inputs
# ---------------------------------------------------------------------------

_ItemStr = Annotated[str, Field(min_length=1, max_length=256)]

# Per-string-value length cap for cold-start feature values. `Field(max_length=64)`
# on `_FeatureValues` caps only the KEY COUNT; without this a single string
# VALUE was unbounded -- the one request field with no length cap (user_id /
# _ItemStr are 256, seed_items 100, exclude_items 1000). `_tokens` does
# `str(raw).split(delimiter)` unbounded, so a large `multi_label` value
# amplifies (~8x) into a memory-DoS reachable with one API key and multiplied
# by batch/related fan-out. 8192 is generous for a real multi_label token list
# yet blocks MB-scale amplification, restoring parity with every other field.
_MAX_FEATURE_VALUE_CHARS = 8192


def _check_feature_value_lengths(
    values: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Reject any string feature value longer than ``_MAX_FEATURE_VALUE_CHARS``.

    Shared by every place a cold-start feature mapping appears: ``user_features``
    on both request models and each nested ``item_features`` mapping (this
    validator runs per ``_FeatureValues``, so a nested dict of values is checked
    too). Names the offending column key but never echoes the value, which is
    treated as personal data. Non-string scalars are unaffected.
    """
    if values is None:
        return values
    for key, val in values.items():
        if isinstance(val, str) and len(val) > _MAX_FEATURE_VALUE_CHARS:
            raise ValueError(
                f"feature value for column {key!r} exceeds the "
                f"{_MAX_FEATURE_VALUE_CHARS}-character limit"
            )
    return values


# Raw feature values for cold start, shared by the ``user_features`` field on
# both single-request models and the ``item_features`` values on
# ``RecommendRelatedRequest``.  Values are encoded server-side against the
# model's training vocabulary (``recotem._features.encode_one``) and are
# treated as personal data: never logged (see ``recotem.log_redaction`` for
# the key-based backstop, which this relies on as defence in depth only).
_FeatureValues = Annotated[
    dict[str, Any],
    Field(
        max_length=64,
        description=(
            "Raw feature values for cold start, keyed by the recipe's feature "
            "column names. Encoded server-side with the model's training "
            "vocabulary. Values are treated as personal data and are never "
            "logged."
        ),
    ),
    AfterValidator(_check_feature_value_lengths),
]


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
    # Cold-start profile (case A -- unknown user, features only). Ignored,
    # not rejected, for a KNOWN user_id: the learned embedding was fit to
    # their real interactions and strictly dominates a profile prior, so a
    # client that always sends the profile keeps working either way. See
    # ``routes.py``'s ``recommend`` handler and
    # ``docs/api-reference.md#feature-aware-cold-start`` ("A known `user_id`
    # with `user_features` supplied is not an error.").
    user_features: _FeatureValues | None = None


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
    # Case B -- profile prior added to the ad-hoc seed-history solve.
    user_features: _FeatureValues | None = None
    # Case C -- feature values for seed items absent from training, keyed by
    # seed item id. Takes precedence over ``user_features`` when a seed named
    # here is also cold: a cold seed has no row in the seed interaction
    # matrix, so the case-B solve would silently drop it.
    item_features: Annotated[
        dict[str, _FeatureValues] | None,
        Field(
            max_length=100,
            description=(
                "Raw feature values for seed items absent from training, keyed "
                "by seed item id."
            ),
        ),
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
