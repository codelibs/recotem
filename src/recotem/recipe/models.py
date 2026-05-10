"""Pydantic v2 models for the Recotem recipe schema (Section 5)."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_for_filesystem(name: str) -> str:
    """Re-assert the name regex immediately before any path or URL use.

    This is a defence-in-depth check. pydantic validates at load time; this
    function must be called again just before the name is embedded in a path.

    Raises
    ------
    ValueError
        If *name* does not match ``^[A-Za-z0-9_-]{1,64}$``.
    """
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Recipe name {name!r} is not a valid filesystem identifier. "
            "Must match ^[A-Za-z0-9_-]{1,64}$."
        )
    return name


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class SchemaConfig(BaseModel, extra="forbid"):
    """Column name mappings for the interaction DataFrame."""

    user_column: str
    item_column: str
    time_column: str | None = None
    time_unit: str | None = Field(
        default=None,
        pattern=r"^(s|ms|us|ns)$",
        description=(
            "Unit for numeric time_column values. Required when time_column "
            "contains integers (Unix timestamps). One of 's', 'ms', 'us', 'ns'. "
            "String and datetime columns are unaffected. "
            "Omitting this field for a numeric time_column raises a TrainingError."
        ),
    )


class CleansingConfig(BaseModel, extra="forbid"):
    """Optional cleansing rules applied after data fetch."""

    drop_null_ids: bool = True
    dedup: str = Field(
        default="keep_last",
        pattern=r"^(keep_first|keep_last|none)$",
    )
    min_rows: int | None = Field(default=None, ge=0)
    min_users: int | None = Field(default=None, ge=0)
    min_items: int | None = Field(default=None, ge=0)


class SplitConfig(BaseModel, extra="forbid"):
    """Train/test split parameters."""

    scheme: str = Field(
        default="random",
        pattern=r"^(random|time_global|time_user)$",
    )
    heldout_ratio: float = Field(default=0.1, gt=0.0, lt=1.0)
    test_user_ratio: float = Field(default=1.0, gt=0.0, le=1.0)
    seed: int = 42


class TrainingConfig(BaseModel, extra="forbid"):
    """Hyperparameter search and training parameters."""

    algorithms: list[str] = Field(min_length=1)
    metric: str = Field(
        default="ndcg",
        pattern=r"^(ndcg|map|recall|hit)$",
    )
    cutoff: int = Field(default=20, ge=1)
    n_trials: int = Field(default=40, ge=1)
    per_algorithm_trials: dict[str, int] | None = None
    per_trial_timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Soft per-trial wall-clock cap. The trial runs in a daemon "
            "thread; on timeout Optuna prunes the trial but the underlying "
            "training thread keeps running until it finishes naturally "
            "(CPU/memory remain spent). Use parallelism=1 and a generous "
            "timeout, or rely on TrainingConfig.timeout_seconds for a hard "
            "overall cap. See docs/recipe-reference.md."
        ),
    )
    timeout_seconds: int | None = Field(default=None, ge=1)
    parallelism: int = Field(default=1, ge=1)
    storage_path: str = ""
    split: SplitConfig = Field(default_factory=SplitConfig)

    @model_validator(mode="after")
    def _validate_per_algorithm_trials_keys(self) -> TrainingConfig:
        """Reject per_algorithm_trials keys that are not in algorithms.

        Each key must be resolvable as an alias for one of the algorithms
        listed in ``self.algorithms``.  Unknown keys (e.g. typos) are
        rejected with a ValidationError at recipe-load time.
        """
        if not self.per_algorithm_trials:
            return self

        # Import lazily to avoid a circular dependency at module load.
        from recotem.training.algorithms import (  # noqa: PLC0415
            UnknownAlgorithmError,
            resolve_algorithm_name,
        )

        # Build the set of resolved canonical class names from algorithms.
        resolved_algorithms: set[str] = set()
        for alias in self.algorithms:
            try:
                resolved_algorithms.add(resolve_algorithm_name(alias))
            except UnknownAlgorithmError:
                # The algorithms list itself may contain unresolvable names;
                # those are caught elsewhere (at search time).  Don't block
                # per_algorithm_trials validation on them.
                pass

        unknown_keys: list[str] = []
        for key in self.per_algorithm_trials:
            # Accept keys that are already canonical class names in algorithms.
            if key in resolved_algorithms:
                continue
            # Try resolving the key as an alias.
            try:
                canonical = resolve_algorithm_name(key)
            except UnknownAlgorithmError:
                unknown_keys.append(key)
                continue
            if canonical not in resolved_algorithms:
                unknown_keys.append(key)

        if unknown_keys:
            raise ValueError(
                f"per_algorithm_trials contains keys that are not in algorithms: "
                f"{unknown_keys!r}.  Keys must match (or be aliases for) entries "
                f"in training.algorithms."
            )
        return self


class OutputConfig(BaseModel, extra="forbid"):
    """Artifact output path and versioning policy."""

    path: str
    versioning: str = Field(
        default="append_sha",
        pattern=r"^(always_overwrite|append_sha)$",
    )


class ItemMetadataConfig(BaseModel, extra="forbid"):
    """Optional item metadata join configuration."""

    type: str = Field(pattern=r"^(csv|parquet)$")
    path: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    fields: list[str] = Field(min_length=1)
    on_field_missing: str = Field(
        default="error",
        pattern=r"^(error|null)$",
    )
    item_id_column: str = Field(
        default="item_id",
        description="Column name in the metadata source that holds the item id",
    )

    @field_validator("item_id_column")
    @classmethod
    def _validate_item_id_column(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("item_id_column must not be empty or whitespace-only")
        return v


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------


class Recipe(BaseModel, extra="forbid"):
    """Top-level recipe model.

    Represents a single training + serving unit.  One recipe → one model →
    one ``/predict/{name}`` endpoint.
    """

    name: Annotated[
        str,
        Field(pattern=r"^[A-Za-z0-9_-]{1,64}$"),
    ]
    # ``source`` is typed as ``Any`` here because the discriminated union is
    # built dynamically from entry points (see datasource/registry.py).  The
    # CLI / loader validates the source config using the dynamic union after
    # assembly; models.py itself cannot import datasource without creating a
    # circular dependency.
    source: Any
    schema_: SchemaConfig = Field(alias="schema")
    cleansing: CleansingConfig = Field(default_factory=CleansingConfig)
    item_metadata: ItemMetadataConfig | None = None
    training: TrainingConfig = Field(default_factory=TrainingConfig)
    output: OutputConfig

    model_config = {"populate_by_name": True, "validate_assignment": True}

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"Recipe name {v!r} must match ^[A-Za-z0-9_-]{{1,64}}$")
        return v

    @model_validator(mode="after")
    def _validate_time_split(self) -> Recipe:
        scheme = self.training.split.scheme
        if scheme in ("time_user", "time_global") and self.schema_.time_column is None:
            raise ValueError(
                f"training.split.scheme='{scheme}' requires schema.time_column to be set."
            )
        return self
