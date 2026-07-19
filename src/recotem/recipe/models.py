"""Pydantic v2 models for the Recotem recipe schema (Section 5)."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

import structlog
from pydantic import BaseModel, Field, field_validator, model_validator

logger = structlog.get_logger(__name__)

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
# Feature-aware iALS side features
# ---------------------------------------------------------------------------

FeatureEncoding = Literal["categorical", "numerical", "multi_label"]


class FeatureColumn(BaseModel, extra="forbid"):
    """One source column and how to turn it into numeric feature columns.

    ``delimiter`` applies only to ``multi_label`` (defaulted to ``"|"``).
    ``min_frequency`` applies only to the vocabulary-based encodings; it is the
    operator's only lever against RECOTEM_MAX_FEATURE_DIM, because the
    vocabulary is built from the whole feature table and therefore scales with
    catalog size rather than interaction size.
    """

    name: str = Field(min_length=1)
    encoding: FeatureEncoding
    delimiter: str | None = None
    min_frequency: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _validate_encoding_options(self) -> FeatureColumn:
        if self.encoding == "multi_label":
            if self.delimiter is None:
                object.__setattr__(self, "delimiter", "|")
            elif not self.delimiter:
                raise ValueError("delimiter must not be empty for multi_label")
        elif self.delimiter is not None:
            raise ValueError(
                f"delimiter is only valid for encoding='multi_label', "
                f"not {self.encoding!r} (column {self.name!r})"
            )
        if self.encoding == "numerical" and self.min_frequency != 1:
            raise ValueError(
                f"min_frequency is only valid for vocabulary-based encodings "
                f"(categorical, multi_label), not 'numerical' "
                f"(column {self.name!r})"
            )
        return self


class FeatureSideConfig(BaseModel, extra="forbid"):
    """Feature table for one side (item or user) plus its encoding plan."""

    # ``source`` is ``Any`` for the same reason as ``Recipe.source``: the
    # discriminated union is built dynamically from entry points and importing
    # datasource/registry.py here would be circular.  recipe/loader.py performs
    # the typed construction; ``Recipe._validate_features_sources`` is the
    # fallback check for direct construction.
    source: Any
    id_column: str = Field(min_length=1)
    columns: list[FeatureColumn] = Field(min_length=1)

    @field_validator("id_column")
    @classmethod
    def _validate_id_column(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("id_column must not be empty or whitespace-only")
        return v

    @field_validator("columns")
    @classmethod
    def _validate_unique_columns(cls, v: list[FeatureColumn]) -> list[FeatureColumn]:
        names = [c.name for c in v]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate feature column names: {dupes}")
        return v

    @model_validator(mode="after")
    def _validate_id_column_not_a_feature(self) -> FeatureSideConfig:
        """Reject an id_column that also names a feature column.

        ``_fetch_side`` does ``set_index(id_column)``, which consumes that
        column, so ``build_encoder_state`` would then raise a misleading
        "feature column '...' is not present" at train time (exit 4). The
        failure is guaranteed, so surface it early at recipe load instead.
        """
        if any(c.name == self.id_column for c in self.columns):
            raise ValueError(
                f"id_column {self.id_column!r} collides with a feature column "
                f"of the same name in columns; the id column is consumed as the "
                f"index and cannot also be a feature. Rename one of them."
            )
        return self


class FeaturesConfig(BaseModel, extra="forbid"):
    """Side features for feature-aware iALS.

    The mere presence of this block on a recipe enables feature-aware training
    for every feature-capable algorithm in ``training.algorithms``.  There is
    no separate flag. ``lambda_*_feature`` is a ridge on the feature-weight
    matrices ``A``/``B``, not the strength of the feature prior itself; a
    large enough value shrinks ``A``/``B`` toward zero and was measured
    bit-identical to plain iALS at ``λ=1e8``. The tuned range's upper bound
    (``1e6``) is only what upstream's own example exercises, not a verified
    "features off" point, so the search space is not guaranteed to contain a
    features-off model. Run two recipes if you need a true on/off comparison.
    """

    item: FeatureSideConfig | None = None
    user: FeatureSideConfig | None = None

    @model_validator(mode="after")
    def _validate_at_least_one_side(self) -> FeaturesConfig:
        if self.item is None and self.user is None:
            raise ValueError(
                "features requires at least one of features.item or features.user"
            )
        return self


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------


def _check_source_value(src: Any, where: str) -> None:
    """Reject a source whose ``type`` is not a registered DataSource.

    *where* names the field for the error message ("source",
    "features.item.source").  Shared by ``Recipe._validate_source`` and
    ``Recipe._validate_features_sources``.

    Behaviour by source kind
    ------------------------
    * ``dict`` with a ``type`` key in the registry → allowed (backward
      compat for ``Recipe.model_validate(d)`` and test helpers that pass
      raw dicts).
    * ``dict`` with a ``type`` key NOT in the registry → rejected.
    * ``dict`` with no ``type`` key → rejected (missing discriminator).
    * ``pydantic.BaseModel`` subclass that is a known Config class →
      allowed.
    * ``pydantic.BaseModel`` subclass that is NOT a known Config class →
      rejected.
    * Any other object (e.g. test mock, legacy opaque object) → allowed
      silently (registry check skipped to avoid false positives).

    The registry import stays deferred and a registry failure stays
    non-fatal (warn and skip) -- both are load-bearing: a module-level
    import would trigger plugin-loading side effects at recipe-model import
    time, and a broken third-party plugin must not make every recipe
    unloadable.
    """
    if src is None:
        raise ValueError(f"{where} must not be None")

    try:
        from pydantic import BaseModel as _BaseModel  # noqa: PLC0415

        from recotem.datasource.registry import get_source_types  # noqa: PLC0415

        types = get_source_types()
        known_types_set: set[str] = {cls.type_name for cls in types.values()}  # type: ignore[union-attr]
        known_config_classes = tuple(cls.Config for cls in types.values())  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        # Registry unavailable (import failure, broken plugin, etc.) —
        # emit a structured warning so operators can diagnose broken plugins
        # without a traceback, then let the datasource path surface the error
        # at training time.
        logger.warning(
            "source_registry_unavailable_during_validation",
            error_class=type(exc).__name__,
            error=str(exc),
        )
        return

    if isinstance(src, dict):
        type_val = src.get("type")
        if type_val is None:
            raise ValueError(
                f"{where} dict is missing the 'type' discriminator field. "
                f"Known types: {sorted(known_types_set)}."
            )
        if str(type_val) not in known_types_set:
            raise ValueError(
                f"{where} type {type_val!r} is not a registered DataSource type. "
                f"Known types: {sorted(known_types_set)}."
            )
        # Known dict type — allow through (will be validated by load_recipe
        # or the datasource pipeline).
        return

    if isinstance(src, _BaseModel):
        # Pydantic model: check it is one of the known Config classes.
        if known_config_classes and not isinstance(src, known_config_classes):
            type_hint = getattr(src, "type", type(src).__name__)
            raise ValueError(
                f"{where} type {type_hint!r} is not a known DataSource Config. "
                f"Known types: {sorted(known_types_set)}. "
                "Pass a typed Config (e.g. CSVConfig, BigQueryConfig) or use "
                "load_recipe() to validate from YAML."
            )
        return

    # Non-pydantic, non-dict source: reject immediately so library callers
    # get a clear ValidationError rather than a cryptic runtime error deep
    # inside the training pipeline.  Test code that needs to pass a mock
    # must use a pydantic BaseModel (e.g. a MagicMock(spec=CSVConfig)) or
    # a valid dict with a recognised 'type' key.
    raise ValueError(
        f"{where} must be a dict with a 'type' key or a registered "
        "DataSource Config subclass (pydantic BaseModel).  "
        f"Got {type(src).__name__!r} which is neither."
    )


class Recipe(BaseModel, extra="forbid"):
    """Top-level recipe model.

    Represents a single training + serving unit.  One recipe → one model →
    one ``/v1/recipes/{name}:*`` set of endpoints.
    """

    name: Annotated[
        str,
        Field(pattern=r"^[A-Za-z0-9_-]{1,64}$"),
    ]
    # ``source`` is typed as ``Any`` here because the discriminated union is
    # built dynamically from entry points (see datasource/registry.py).  The
    # ``_validate_source`` model_validator below checks that the value is an
    # instance of a known DataSource Config class when possible.  When the
    # registry is unavailable (import failure), the check is skipped.
    # NOTE: the ``load_recipe`` function in recipe/loader.py performs full
    # validated construction; direct ``Recipe(...)`` calls bypass the YAML
    # pipeline but are still guarded by ``_validate_source``.
    source: Any
    schema_: SchemaConfig = Field(alias="schema")
    cleansing: CleansingConfig = Field(default_factory=CleansingConfig)
    item_metadata: ItemMetadataConfig | None = None
    features: FeaturesConfig | None = None
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
    def _validate_source(self) -> Recipe:
        """Reject a ``source`` with a ``type`` that is not registered.

        When a library caller passes ``source={"type": "unknown_xyz"}`` or a
        pydantic BaseModel whose type_name is not in the registry, raise
        immediately rather than allowing the error to surface obscurely at
        training time.  See ``_check_source_value`` for the full behaviour
        table.
        """
        _check_source_value(self.source, "source")
        return self

    @model_validator(mode="after")
    def _validate_features_sources(self) -> Recipe:
        """Reject feature sources with an unregistered ``type``.

        ``load_recipe`` performs the full typed construction; direct
        ``Recipe(...)`` calls bypass the YAML pipeline, so this mirrors
        ``_validate_source`` for the feature subtrees.
        """
        if self.features is None:
            return self
        for side_name in ("item", "user"):
            side = getattr(self.features, side_name)
            if side is not None:
                _check_source_value(side.source, f"features.{side_name}.source")
        return self

    @model_validator(mode="after")
    def _validate_features_algorithms(self) -> Recipe:
        """Reject a features block that no listed algorithm can consume.

        Deliberately tolerant of unresolvable algorithm names: those stay
        deferred to train time, matching ``_validate_per_algorithm_trials_keys``.
        """
        if self.features is None:
            return self
        try:
            from recotem.training.algorithms import is_feature_capable  # noqa: PLC0415
        except ImportError:  # pragma: no cover - training extras absent
            return self
        if not any(is_feature_capable(a) for a in self.training.algorithms):
            raise ValueError(
                "features is configured but training.algorithms contains no "
                "feature-capable algorithm. Feature-aware training requires "
                "IALS. Either add IALS to training.algorithms or remove the "
                "features block."
            )
        return self

    @model_validator(mode="after")
    def _validate_time_split(self) -> Recipe:
        scheme = self.training.split.scheme
        if scheme in ("time_user", "time_global") and self.schema_.time_column is None:
            raise ValueError(
                f"training.split.scheme='{scheme}' requires schema.time_column to be set."
            )
        return self
