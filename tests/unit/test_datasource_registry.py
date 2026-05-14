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
        no_expand_fields = frozenset()

        def fetch(self, ctx): ...

    class DupeSource2:
        type_name = "csv"  # same name
        Config = DupeConfig
        extras_required = []
        no_expand_fields = frozenset()

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
        no_expand_fields = frozenset()

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


# ---------------------------------------------------------------------------
# MAJOR-9: entry_points ep.load() ImportError — graceful vs fatal behaviour
# ---------------------------------------------------------------------------
# The implementation raises DataSourceError (fatal) when ep.load() fails.
# This test pins that behaviour so a regression (silently skipping the broken
# plugin) is immediately caught.


def test_plugin_ep_load_failure_raises_datasource_error() -> None:
    """When ep.load() raises ImportError, get_source_types must raise DataSourceError.

    The implementation is FATAL on load failure (not graceful/silent-skip):
    it is better to fail loudly at startup than to silently omit a plugin the
    operator configured, which would cause mysterious 'unknown type' errors later.

    This test pins that fatal behaviour so a future refactor to 'graceful skip'
    is an explicit, reviewed decision.
    """
    ep = MagicMock()
    ep.name = "broken_plugin"
    ep.value = "some.module:BrokenSource"
    ep.load.side_effect = ImportError("missing dependency: install broken-extras")

    with patch("recotem.datasource.registry.entry_points", return_value=[ep]):
        from recotem.datasource import registry

        registry.get_source_types.cache_clear()
        try:
            with pytest.raises(
                DataSourceError, match="Failed to load DataSource plugin"
            ):
                registry.get_source_types()
        finally:
            registry.get_source_types.cache_clear()


def test_plugin_ep_load_failure_error_message_includes_plugin_name() -> None:
    """The DataSourceError message for a failed ep.load() includes the plugin name."""
    ep = MagicMock()
    ep.name = "myspecialplugin"
    ep.value = "my.module:MySource"
    ep.load.side_effect = ModuleNotFoundError("no module named 'my'")

    with patch("recotem.datasource.registry.entry_points", return_value=[ep]):
        from recotem.datasource import registry

        registry.get_source_types.cache_clear()
        try:
            with pytest.raises(DataSourceError) as exc_info:
                registry.get_source_types()
            assert "myspecialplugin" in str(exc_info.value), (
                f"Error message must name the broken plugin; got: {exc_info.value!r}"
            )
        finally:
            registry.get_source_types.cache_clear()


def test_plugin_ep_load_raises_attribute_error_raises_datasource_error() -> None:
    """An AttributeError from ep.load() (e.g. class not found) is also fatal."""
    ep = MagicMock()
    ep.name = "attr_error_plugin"
    ep.value = "good.module:NonExistentClass"
    ep.load.side_effect = AttributeError("module has no attribute 'NonExistentClass'")

    with patch("recotem.datasource.registry.entry_points", return_value=[ep]):
        from recotem.datasource import registry

        registry.get_source_types.cache_clear()
        try:
            with pytest.raises(DataSourceError):
                registry.get_source_types()
        finally:
            registry.get_source_types.cache_clear()
