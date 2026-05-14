"""Unit tests for recotem.recipe.models pydantic strictness.

Tests:
- time_user without time_column raises
- heldout_ratio bounds
- n_trials zero rejected
- fields empty list rejected
- unknown extra fields rejected
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from recotem.recipe.models import (
    CleansingConfig,
    ItemMetadataConfig,
    OutputConfig,
    Recipe,
    SchemaConfig,
    SplitConfig,
    TrainingConfig,
)


def _minimal_recipe_dict(**overrides) -> dict:
    base = {
        "name": "test",
        "source": {"type": "csv", "path": "/tmp/data.csv"},
        "schema": {"user_column": "user_id", "item_column": "item_id"},
        "training": {
            "algorithms": ["TopPop"],
            "n_trials": 1,
        },
        "output": {"path": "/tmp/out.recotem"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# name regex
# ---------------------------------------------------------------------------


def test_recipe_name_with_slash_raises_validation_error() -> None:
    d = _minimal_recipe_dict(name="bad/name")
    with pytest.raises(ValidationError):
        Recipe.model_validate(d)


def test_recipe_name_over_64_chars_raises_validation_error() -> None:
    d = _minimal_recipe_dict(name="a" * 65)
    with pytest.raises(ValidationError):
        Recipe.model_validate(d)


def test_recipe_name_empty_raises_validation_error() -> None:
    d = _minimal_recipe_dict(name="")
    with pytest.raises(ValidationError):
        Recipe.model_validate(d)


def test_recipe_name_valid_alphanum_hyphens() -> None:
    d = _minimal_recipe_dict(name="valid-Name_1")
    r = Recipe.model_validate(d)
    assert r.name == "valid-Name_1"


# ---------------------------------------------------------------------------
# time_column requirement for time-based splits
# ---------------------------------------------------------------------------


def test_time_user_split_without_time_column_rejected() -> None:
    d = _minimal_recipe_dict()
    d["training"]["split"] = {"scheme": "time_user", "heldout_ratio": 0.1}
    with pytest.raises((ValidationError, ValueError)):
        Recipe.model_validate(d)


def test_time_global_split_without_time_column_rejected() -> None:
    d = _minimal_recipe_dict()
    d["training"]["split"] = {"scheme": "time_global", "heldout_ratio": 0.1}
    with pytest.raises((ValidationError, ValueError)):
        Recipe.model_validate(d)


def test_time_user_split_with_time_column_ok() -> None:
    d = _minimal_recipe_dict()
    d["schema"]["time_column"] = "ts"
    d["training"]["split"] = {"scheme": "time_user", "heldout_ratio": 0.1}
    r = Recipe.model_validate(d)
    assert r.training.split.scheme == "time_user"


# ---------------------------------------------------------------------------
# SplitConfig: heldout_ratio bounds
# ---------------------------------------------------------------------------


def test_heldout_ratio_above_one_rejected() -> None:
    with pytest.raises(ValidationError):
        SplitConfig(heldout_ratio=1.1)


def test_heldout_ratio_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        SplitConfig(heldout_ratio=0.0)


def test_heldout_ratio_one_rejected() -> None:
    """heldout_ratio=1.0 is rejected (lt=1.0 constraint)."""
    with pytest.raises(ValidationError):
        SplitConfig(heldout_ratio=1.0)


def test_heldout_ratio_valid() -> None:
    sc = SplitConfig(heldout_ratio=0.2)
    assert sc.heldout_ratio == 0.2


# ---------------------------------------------------------------------------
# TrainingConfig: n_trials
# ---------------------------------------------------------------------------


def test_n_trials_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(algorithms=["TopPop"], n_trials=0)


def test_n_trials_one_accepted() -> None:
    tc = TrainingConfig(algorithms=["TopPop"], n_trials=1)
    assert tc.n_trials == 1


def test_algorithms_empty_list_rejected() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(algorithms=[], n_trials=5)


# ---------------------------------------------------------------------------
# ItemMetadataConfig: fields empty
# ---------------------------------------------------------------------------


def test_item_metadata_fields_empty_list_rejected() -> None:
    with pytest.raises(ValidationError):
        ItemMetadataConfig(type="csv", path="/tmp/meta.csv", fields=[])


def test_item_metadata_fields_nonempty_ok() -> None:
    cfg = ItemMetadataConfig(type="csv", path="/tmp/meta.csv", fields=["title"])
    assert cfg.fields == ["title"]


# ---------------------------------------------------------------------------
# Extra fields rejected (extra="forbid")
# ---------------------------------------------------------------------------


def test_schema_config_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        SchemaConfig(user_column="u", item_column="i", unknown_field="x")


def test_cleansing_config_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        CleansingConfig(not_a_field=True)


# ---------------------------------------------------------------------------
# OutputConfig versioning enum
# ---------------------------------------------------------------------------


def test_output_config_invalid_versioning_rejected() -> None:
    with pytest.raises(ValidationError):
        OutputConfig(path="/tmp/x.recotem", versioning="invalid_mode")


def test_output_config_valid_versioning() -> None:
    oc = OutputConfig(path="/tmp/x.recotem", versioning="append_sha")
    assert oc.versioning == "append_sha"


# ---------------------------------------------------------------------------
# CleansingConfig: dedup field
# ---------------------------------------------------------------------------


def test_cleansing_config_invalid_dedup_rejected() -> None:
    with pytest.raises(ValidationError):
        CleansingConfig(dedup="destroy_all")


def test_cleansing_config_valid_dedup() -> None:
    for val in ("keep_first", "keep_last", "none"):
        cc = CleansingConfig(dedup=val)
        assert cc.dedup == val


def test_cleansing_config_rejects_unimplemented_sum_weight() -> None:
    """sum_weight was documented but never plumbed end-to-end; keep it
    rejected at the schema layer until the training pipeline can actually
    consume per-interaction weights."""
    with pytest.raises(ValidationError):
        CleansingConfig(dedup="sum_weight")


# ---------------------------------------------------------------------------
# sha256 integrity field on CSV / Parquet / ItemMetadata configs
# ---------------------------------------------------------------------------


def test_csvconfig_sha256_valid_lowercase_hex_accepted() -> None:
    from recotem.datasource.csv import CSVConfig

    cfg = CSVConfig(
        type="csv",
        path="/tmp/x.csv",
        sha256="945fc769205a5976d38c5783500ae473afbb04608043b703951a699993c8f8be",
    )
    assert cfg.sha256 == (
        "945fc769205a5976d38c5783500ae473afbb04608043b703951a699993c8f8be"
    )


def test_csvconfig_sha256_uppercase_rejected() -> None:
    import pydantic

    from recotem.datasource.csv import CSVConfig

    with pytest.raises(pydantic.ValidationError):
        CSVConfig(
            type="csv",
            path="/tmp/x.csv",
            sha256="945FC769205A5976D38C5783500AE473AFBB04608043B703951A699993C8F8BE",
        )


def test_csvconfig_sha256_wrong_length_rejected() -> None:
    import pydantic

    from recotem.datasource.csv import CSVConfig

    with pytest.raises(pydantic.ValidationError):
        CSVConfig(type="csv", path="/tmp/x.csv", sha256="abcd1234")


def test_csvconfig_sha256_optional_when_unset() -> None:
    from recotem.datasource.csv import CSVConfig

    cfg = CSVConfig(type="csv", path="/tmp/x.csv")
    assert cfg.sha256 is None


def test_parquetconfig_sha256_accepted() -> None:
    from recotem.datasource.csv import ParquetConfig

    cfg = ParquetConfig(
        type="parquet",
        path="/tmp/x.parquet",
        sha256="0" * 64,
    )
    assert cfg.sha256 == "0" * 64


def test_itemmetadata_sha256_accepted() -> None:
    from recotem.recipe.models import ItemMetadataConfig

    cfg = ItemMetadataConfig(
        type="csv",
        path="/tmp/items.csv",
        sha256="a" * 64,
        fields=["title"],
    )
    assert cfg.sha256 == "a" * 64


def test_itemmetadata_sha256_invalid_rejected() -> None:
    import pydantic

    from recotem.recipe.models import ItemMetadataConfig

    with pytest.raises(pydantic.ValidationError):
        ItemMetadataConfig(
            type="csv", path="/tmp/x.csv", sha256="not-hex", fields=["title"]
        )


# ---------------------------------------------------------------------------
# ItemMetadataConfig: item_id_column field
# ---------------------------------------------------------------------------


def test_item_metadata_item_id_column_default_is_item_id() -> None:
    cfg = ItemMetadataConfig(type="csv", path="/tmp/meta.csv", fields=["title"])
    assert cfg.item_id_column == "item_id"


def test_item_metadata_item_id_column_custom_value_round_trips() -> None:
    cfg = ItemMetadataConfig(
        type="csv",
        path="/tmp/meta.csv",
        fields=["title"],
        item_id_column="product_id",
    )
    assert cfg.item_id_column == "product_id"


def test_item_metadata_item_id_column_empty_string_rejected() -> None:
    with pytest.raises(ValidationError):
        ItemMetadataConfig(
            type="csv", path="/tmp/meta.csv", fields=["title"], item_id_column=""
        )


def test_item_metadata_item_id_column_whitespace_only_rejected() -> None:
    with pytest.raises(ValidationError):
        ItemMetadataConfig(
            type="csv", path="/tmp/meta.csv", fields=["title"], item_id_column="   "
        )


# ---------------------------------------------------------------------------
# Fix A3 — validate_assignment: re-validation on post-construction assignment
# ---------------------------------------------------------------------------


def test_recipe_name_assignment_revalidates_and_rejects_illegal() -> None:
    """Post-construction assignment of an invalid name must raise ValidationError.

    validate_assignment=True on the Recipe model_config ensures that
    `_validate_name` is re-run on every field set, not only at load time.
    """
    d = _minimal_recipe_dict(name="valid-name")
    recipe = Recipe.model_validate(d)
    original_name = recipe.name
    with pytest.raises(ValidationError):
        recipe.name = "../../etc/passwd"
    # Original name must be unchanged after the failed assignment.
    assert recipe.name == original_name


def test_recipe_name_assignment_accepts_valid_value() -> None:
    """Post-construction assignment of a valid name must succeed."""
    d = _minimal_recipe_dict(name="original-name")
    recipe = Recipe.model_validate(d)
    recipe.name = "new-valid-name"
    assert recipe.name == "new-valid-name"


# ---------------------------------------------------------------------------
# CLI-6: Recipe.source validation — unknown type raises, known Config passes
# ---------------------------------------------------------------------------


def test_recipe_source_unknown_type_raises_validation_error() -> None:
    """Recipe(source={"type": "unknown_xyz"}) must raise ValidationError.

    Library callers that pass an unknown source type directly must get an
    immediate ValidationError at construction time, not a cryptic runtime
    error during training.
    """
    from recotem.recipe.models import OutputConfig, SchemaConfig, TrainingConfig

    with pytest.raises(ValidationError):
        Recipe(
            name="test",
            source={"type": "unknown_xyz", "path": "/tmp/data.csv"},
            schema=SchemaConfig(user_column="user_id", item_column="item_id"),
            training=TrainingConfig(algorithms=["TopPop"], n_trials=1),
            output=OutputConfig(path="/tmp/out.recotem"),
        )


def test_recipe_source_known_csvconfig_succeeds() -> None:
    """Recipe(source=CSVConfig(...)) must construct successfully.

    A typed, registered Config instance must pass the _validate_source check.
    """
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import OutputConfig, SchemaConfig, TrainingConfig

    recipe = Recipe(
        name="test",
        source=CSVConfig(type="csv", path="/tmp/data.csv"),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(algorithms=["TopPop"], n_trials=1),
        output=OutputConfig(path="/tmp/out.recotem"),
    )
    assert recipe.source.type == "csv"


def test_recipe_source_dict_with_known_type_passes() -> None:
    """Recipe.model_validate(d) with source as a raw dict of known type must work.

    Backward compat: existing tests and load_recipe use this form.
    The dict is validated by load_recipe / datasource pipeline; models.py
    only rejects unknown types.
    """
    d = _minimal_recipe_dict()
    # source dict has type="csv" which is a known registered type → must pass
    recipe = Recipe.model_validate(d)
    assert isinstance(recipe.source, dict) or hasattr(recipe.source, "type")


# ---------------------------------------------------------------------------
# MF-4: non-dict, non-BaseModel sources must raise ValidationError
# ---------------------------------------------------------------------------


def test_recipe_source_int_raises_validation_error() -> None:
    """Recipe(source=42) must raise ValidationError.

    Integers are neither dicts nor pydantic BaseModels; the _validate_source
    model_validator must close this silent-pass path.
    """
    from recotem.recipe.models import OutputConfig, SchemaConfig, TrainingConfig

    with pytest.raises(ValidationError):
        Recipe(
            name="test",
            source=42,
            schema=SchemaConfig(user_column="user_id", item_column="item_id"),
            training=TrainingConfig(algorithms=["TopPop"], n_trials=1),
            output=OutputConfig(path="/tmp/out.recotem"),
        )


def test_recipe_source_plain_object_raises_validation_error() -> None:
    """Recipe(source=object()) must raise ValidationError.

    A plain Python object is not a registered DataSource Config; the validator
    must reject it with a clear error rather than silently passing.
    """
    from recotem.recipe.models import OutputConfig, SchemaConfig, TrainingConfig

    with pytest.raises(ValidationError):
        Recipe(
            name="test",
            source=object(),
            schema=SchemaConfig(user_column="user_id", item_column="item_id"),
            training=TrainingConfig(algorithms=["TopPop"], n_trials=1),
            output=OutputConfig(path="/tmp/out.recotem"),
        )


def test_recipe_source_string_raises_validation_error() -> None:
    """Recipe(source='csv') must raise ValidationError.

    A bare string is not a valid source; callers must pass a dict with 'type'
    or a typed Config instance.
    """
    from recotem.recipe.models import OutputConfig, SchemaConfig, TrainingConfig

    with pytest.raises(ValidationError):
        Recipe(
            name="test",
            source="csv",
            schema=SchemaConfig(user_column="user_id", item_column="item_id"),
            training=TrainingConfig(algorithms=["TopPop"], n_trials=1),
            output=OutputConfig(path="/tmp/out.recotem"),
        )


def test_recipe_source_csvconfig_instance_passes() -> None:
    """Recipe(source=CSVConfig(...)) must construct successfully.

    Explicit typed Config subclass must pass _validate_source.
    """
    from recotem.datasource.csv import CSVConfig
    from recotem.recipe.models import OutputConfig, SchemaConfig, TrainingConfig

    recipe = Recipe(
        name="test",
        source=CSVConfig(type="csv", path="/tmp/data.csv"),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(algorithms=["TopPop"], n_trials=1),
        output=OutputConfig(path="/tmp/out.recotem"),
    )
    assert recipe.source.type == "csv"


def test_recipe_source_dict_with_known_csv_type_passes_mf4() -> None:
    """Recipe(source={'type': 'csv', ...}) must construct successfully (dict path)."""
    from recotem.recipe.models import OutputConfig, SchemaConfig, TrainingConfig

    recipe = Recipe(
        name="test",
        source={"type": "csv", "path": "/tmp/data.csv"},
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(algorithms=["TopPop"], n_trials=1),
        output=OutputConfig(path="/tmp/out.recotem"),
    )
    assert isinstance(recipe.source, dict)


# ---------------------------------------------------------------------------
# I-27: validate_for_filesystem re-exported from recotem.recipe
# ---------------------------------------------------------------------------


def test_validate_for_filesystem_importable_from_recipe_package() -> None:
    """from recotem.recipe import validate_for_filesystem must succeed."""
    from recotem.recipe import (
        validate_for_filesystem,  # noqa: F401 — import is the test
    )

    assert callable(validate_for_filesystem)


def test_validate_for_filesystem_in_all() -> None:
    """validate_for_filesystem must appear in recotem.recipe.__all__."""
    import recotem.recipe as recipe_pkg

    assert "validate_for_filesystem" in recipe_pkg.__all__, (
        "validate_for_filesystem is missing from recotem.recipe.__all__; "
        f"current __all__: {recipe_pkg.__all__}"
    )


def test_validate_for_filesystem_accepts_valid_name() -> None:
    """validate_for_filesystem returns the name unchanged for a valid identifier."""
    from recotem.recipe import validate_for_filesystem

    assert validate_for_filesystem("my_recipe") == "my_recipe"
    assert validate_for_filesystem("Recipe-123") == "Recipe-123"


def test_validate_for_filesystem_rejects_invalid_name() -> None:
    """validate_for_filesystem raises ValueError for names with disallowed chars."""
    from recotem.recipe import validate_for_filesystem

    with pytest.raises(ValueError, match="not a valid filesystem identifier"):
        validate_for_filesystem("bad/name")

    with pytest.raises(ValueError, match="not a valid filesystem identifier"):
        validate_for_filesystem("a" * 65)  # too long


# ---------------------------------------------------------------------------
# I-19: plugin registry import failure emits structured warning log
# ---------------------------------------------------------------------------


def test_validate_source_emits_warning_on_registry_import_failure() -> None:
    """When the datasource registry import fails during _validate_source,
    a structured 'source_registry_unavailable_during_validation' warning log
    must be emitted before silently returning self.

    Before the I-19 fix, the exception was silently swallowed (``except Exception:
    return self``), making broken plugin debugging extremely difficult.  After
    the fix, the exception type and message are emitted as structured log fields.
    """
    from unittest.mock import patch

    import structlog.testing

    # Patch get_source_types to raise an ImportError simulating a broken plugin.
    with patch(
        "recotem.datasource.registry.get_source_types",
        side_effect=ImportError("simulated broken plugin import"),
    ):
        with structlog.testing.capture_logs() as captured:
            # Recipe.model_validate triggers _validate_source; the registry
            # import fails but the model is still returned (graceful degradation).
            recipe = Recipe.model_validate(_minimal_recipe_dict())

    assert recipe is not None, "Recipe must still be returned when registry fails"

    warnings = [
        e
        for e in captured
        if e.get("event") == "source_registry_unavailable_during_validation"
    ]
    assert warnings, (
        "A 'source_registry_unavailable_during_validation' warning must be emitted "
        "when the datasource registry import fails during source validation. "
        f"All captured log events: {[e.get('event') for e in captured]}"
    )
    warn = warnings[0]
    assert warn.get("log_level") == "warning"
    assert "ImportError" in warn.get("error_class", ""), (
        f"error_class must contain 'ImportError'; got {warn.get('error_class')!r}"
    )
    assert "simulated broken plugin" in warn.get("error", ""), (
        f"error field must contain the exception message; got {warn.get('error')!r}"
    )


def test_validate_source_warning_contains_error_class_and_message() -> None:
    """The 'source_registry_unavailable_during_validation' log event must include
    both error_class and error fields for observability.

    Operators need both the exception type and message to diagnose broken plugins
    without a full traceback.
    """
    from unittest.mock import patch

    import structlog.testing

    with patch(
        "recotem.datasource.registry.get_source_types",
        side_effect=RuntimeError("plugin entry_point collision"),
    ):
        with structlog.testing.capture_logs() as captured:
            Recipe.model_validate(_minimal_recipe_dict())

    warnings = [
        e
        for e in captured
        if e.get("event") == "source_registry_unavailable_during_validation"
    ]
    assert warnings, "Warning must be emitted for RuntimeError in registry"
    warn = warnings[0]
    assert warn.get("error_class") == "RuntimeError"
    assert "plugin entry_point collision" in warn.get("error", "")
