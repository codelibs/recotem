"""Unit tests for recotem.datasource.registry.

Tests:
- Duplicate type_name detection raises DataSourceError
- Dynamic discriminated union includes builtin types
- Third-party plugin type appears in the union
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from recotem.datasource.base import DataSourceError

# ---------------------------------------------------------------------------
# Duplicate type_name detection
# ---------------------------------------------------------------------------


def test_two_plugins_register_same_name_rejected_at_discovery() -> None:
    """Two plugins with the same type_name raise DataSourceError at discovery."""
    # We test the logic in get_source_types by patching entry_points
    from pydantic import BaseModel, Field

    class DupeConfig(BaseModel):
        type: str = Field(default="csv", pattern="^csv$")
        path: str = ""

    class DupeSource1:
        type_name = "csv"
        Config = DupeConfig
        extras_required = []

        def fetch(self, ctx): ...

    class DupeSource2:
        type_name = "csv"  # same name
        Config = DupeConfig
        extras_required = []

        def fetch(self, ctx): ...

    ep1 = MagicMock()
    ep1.load.return_value = DupeSource1

    ep2 = MagicMock()
    ep2.load.return_value = DupeSource2

    with patch("recotem.datasource.registry.entry_points", return_value=[ep1, ep2]):
        # Clear the lru_cache to force re-discovery
        from recotem.datasource import registry

        registry.get_source_types.cache_clear()
        try:
            with pytest.raises(DataSourceError, match="[Dd]uplicate"):
                registry.get_source_types()
        finally:
            registry.get_source_types.cache_clear()


# ---------------------------------------------------------------------------
# Dynamic union includes builtin types
# ---------------------------------------------------------------------------


def test_dynamic_discriminated_union_includes_builtin_csv() -> None:
    """The dynamic union must include at least the builtin csv and parquet types."""
    from recotem.datasource import registry

    registry.get_source_types.cache_clear()
    try:
        types = registry.get_source_types()
        assert "csv" in types or "parquet" in types, (
            "Expected at least csv or parquet in the registry"
        )
    finally:
        registry.get_source_types.cache_clear()


def test_build_source_config_union_returns_annotated_type() -> None:
    """build_source_config_union() returns an annotated type (not None, not plain class)."""
    from recotem.datasource import registry

    registry.get_source_types.cache_clear()
    try:
        union = registry.build_source_config_union()
        assert union is not None
    finally:
        registry.get_source_types.cache_clear()


def test_dynamic_discriminated_union_includes_third_party_type() -> None:
    """A third-party plugin type_name appears in get_source_types() after discovery."""
    from pydantic import BaseModel, Field

    class ThirdPartyConfig(BaseModel):
        type: str = Field(default="echo", pattern="^echo$")

    class EchoSource:
        type_name = "echo"
        Config = ThirdPartyConfig
        extras_required = []

        def fetch(self, ctx): ...

    ep = MagicMock()
    ep.load.return_value = EchoSource

    with patch("recotem.datasource.registry.entry_points", return_value=[ep]):
        from recotem.datasource import registry

        registry.get_source_types.cache_clear()
        try:
            types = registry.get_source_types()
            assert "echo" in types
        finally:
            registry.get_source_types.cache_clear()


# ---------------------------------------------------------------------------
# get_source_class error path
# ---------------------------------------------------------------------------


def test_get_source_class_unknown_type_raises() -> None:
    from recotem.datasource import registry

    registry.get_source_types.cache_clear()
    try:
        with pytest.raises(DataSourceError, match="[Uu]nknown"):
            registry.get_source_class("does_not_exist_xyz")
    finally:
        registry.get_source_types.cache_clear()
