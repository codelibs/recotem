"""Unit tests for recotem.datasource.base.validate_plugin_contract.

Tests:
- Missing no_expand_fields raises DataSourceError with message + doc pointer
- Non-frozenset no_expand_fields (list, set, str) raises DataSourceError
- Empty frozenset() is accepted
- frozenset of strings is accepted
- Builtin CSVSource, ParquetSource, BigQuerySource each have no_expand_fields as frozenset
- BigQuerySource.no_expand_fields contains 'query' and 'query_parameters'
"""

from __future__ import annotations

import pytest

from recotem.datasource.base import DataSourceError, validate_plugin_contract

# ---------------------------------------------------------------------------
# Helpers — minimal valid source class (all required attrs present)
# ---------------------------------------------------------------------------


def _make_valid_cls(**overrides):
    """Return a minimal source class satisfying the full contract.

    Caller can override individual class-level attributes to test specific
    failure modes.
    """
    from pydantic import BaseModel

    class _Config(BaseModel):
        type: str = "test"

    attrs = {
        "type_name": "test",
        "Config": _Config,
        "extras_required": [],
        "no_expand_fields": frozenset(),
        "fetch": lambda self, ctx: None,
    }
    attrs.update(overrides)
    return type("_TestSource", (), attrs)


# ---------------------------------------------------------------------------
# missing no_expand_fields raises DataSourceError
# ---------------------------------------------------------------------------


def test_missing_no_expand_fields_raises_datasource_error() -> None:
    """A plugin class without no_expand_fields must raise DataSourceError."""
    cls = _make_valid_cls()
    # Remove the attribute after construction
    del cls.no_expand_fields
    with pytest.raises(DataSourceError) as exc_info:
        validate_plugin_contract(cls)
    msg = str(exc_info.value)
    assert "no_expand_fields" in msg
    assert "plugin-authoring.md" in msg


def test_missing_no_expand_fields_error_names_the_class() -> None:
    """The DataSourceError for missing no_expand_fields must include the class name."""
    cls = _make_valid_cls()
    del cls.no_expand_fields
    with pytest.raises(DataSourceError) as exc_info:
        validate_plugin_contract(cls)
    assert "_TestSource" in str(exc_info.value)


# ---------------------------------------------------------------------------
# non-frozenset no_expand_fields raises DataSourceError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [
        [],  # list (even if empty)
        ["query"],  # list of strings
        set(),  # plain set
        {"query"},  # set of strings
        "query",  # bare string
        ("query",),  # tuple
        None,  # None
    ],
)
def test_non_frozenset_no_expand_fields_raises_datasource_error(bad_value) -> None:
    """no_expand_fields with any type other than frozenset must raise DataSourceError."""
    cls = _make_valid_cls(no_expand_fields=bad_value)
    with pytest.raises(DataSourceError) as exc_info:
        validate_plugin_contract(cls)
    msg = str(exc_info.value)
    assert "no_expand_fields" in msg
    assert "frozenset" in msg
    assert "plugin-authoring.md" in msg


def test_non_frozenset_error_includes_actual_type_name() -> None:
    """The error message for wrong-type no_expand_fields must name the actual type."""
    cls = _make_valid_cls(no_expand_fields=["query"])
    with pytest.raises(DataSourceError) as exc_info:
        validate_plugin_contract(cls)
    # The actual type (list) should be mentioned
    assert "list" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Valid no_expand_fields values are accepted
# ---------------------------------------------------------------------------


def test_empty_frozenset_no_expand_fields_accepted() -> None:
    """frozenset() (empty) must pass validate_plugin_contract without error."""
    cls = _make_valid_cls(no_expand_fields=frozenset())
    # Must not raise
    validate_plugin_contract(cls)


def test_frozenset_of_strings_no_expand_fields_accepted() -> None:
    """frozenset({'query', 'query_parameters'}) must pass without error."""
    cls = _make_valid_cls(no_expand_fields=frozenset({"query", "query_parameters"}))
    validate_plugin_contract(cls)


def test_frozenset_with_custom_field_names_accepted() -> None:
    """A frozenset with arbitrary field names must be accepted."""
    cls = _make_valid_cls(
        no_expand_fields=frozenset({"sql", "bind_params", "template"})
    )
    validate_plugin_contract(cls)


# ---------------------------------------------------------------------------
# Builtin sources declare no_expand_fields correctly
# ---------------------------------------------------------------------------


def test_csv_source_has_no_expand_fields_frozenset() -> None:
    """CSVSource must declare no_expand_fields as a frozenset."""
    from recotem.datasource.csv import CSVSource

    assert hasattr(CSVSource, "no_expand_fields"), (
        "CSVSource is missing the required 'no_expand_fields' class attribute"
    )
    assert isinstance(CSVSource.no_expand_fields, frozenset), (
        f"CSVSource.no_expand_fields must be a frozenset, "
        f"got {type(CSVSource.no_expand_fields).__name__!r}"
    )


def test_parquet_source_has_no_expand_fields_frozenset() -> None:
    """ParquetSource must declare no_expand_fields as a frozenset."""
    from recotem.datasource.csv import ParquetSource

    assert hasattr(ParquetSource, "no_expand_fields"), (
        "ParquetSource is missing the required 'no_expand_fields' class attribute"
    )
    assert isinstance(ParquetSource.no_expand_fields, frozenset), (
        f"ParquetSource.no_expand_fields must be a frozenset, "
        f"got {type(ParquetSource.no_expand_fields).__name__!r}"
    )


def test_bigquery_source_has_no_expand_fields_frozenset() -> None:
    """BigQuerySource must declare no_expand_fields as a frozenset."""
    from recotem.datasource.bigquery import BigQuerySource

    assert hasattr(BigQuerySource, "no_expand_fields"), (
        "BigQuerySource is missing the required 'no_expand_fields' class attribute"
    )
    assert isinstance(BigQuerySource.no_expand_fields, frozenset), (
        f"BigQuerySource.no_expand_fields must be a frozenset, "
        f"got {type(BigQuerySource.no_expand_fields).__name__!r}"
    )


def test_bigquery_source_no_expand_fields_contains_query_fields() -> None:
    """BigQuerySource.no_expand_fields must contain 'query' and 'query_parameters'."""
    from recotem.datasource.bigquery import BigQuerySource

    assert "query" in BigQuerySource.no_expand_fields, (
        "BigQuerySource.no_expand_fields must contain 'query' for defence-in-depth"
    )
    assert "query_parameters" in BigQuerySource.no_expand_fields, (
        "BigQuerySource.no_expand_fields must contain 'query_parameters' "
        "for defence-in-depth"
    )


def test_csv_source_no_expand_fields_is_empty() -> None:
    """CSVSource has no SQL/query fields — no_expand_fields must be frozenset()."""
    from recotem.datasource.csv import CSVSource

    assert CSVSource.no_expand_fields == frozenset(), (
        f"CSVSource.no_expand_fields should be empty frozenset(); "
        f"got {CSVSource.no_expand_fields!r}"
    )


def test_parquet_source_no_expand_fields_is_empty() -> None:
    """ParquetSource has no SQL/query fields — no_expand_fields must be frozenset()."""
    from recotem.datasource.csv import ParquetSource

    assert ParquetSource.no_expand_fields == frozenset(), (
        f"ParquetSource.no_expand_fields should be empty frozenset(); "
        f"got {ParquetSource.no_expand_fields!r}"
    )


# ---------------------------------------------------------------------------
# validate_plugin_contract passes for all builtins
# ---------------------------------------------------------------------------


def test_validate_plugin_contract_passes_for_csv_source() -> None:
    """validate_plugin_contract must not raise for the builtin CSVSource."""
    from recotem.datasource.csv import CSVSource

    validate_plugin_contract(CSVSource)  # must not raise


def test_validate_plugin_contract_passes_for_parquet_source() -> None:
    """validate_plugin_contract must not raise for the builtin ParquetSource."""
    from recotem.datasource.csv import ParquetSource

    validate_plugin_contract(ParquetSource)  # must not raise


def test_validate_plugin_contract_passes_for_bigquery_source() -> None:
    """validate_plugin_contract must not raise for the builtin BigQuerySource."""
    from recotem.datasource.bigquery import BigQuerySource

    validate_plugin_contract(BigQuerySource)  # must not raise
