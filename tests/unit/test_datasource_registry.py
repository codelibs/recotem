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
# Third-party plugin load failures remain fatal (DataSourceError).
# Builtin sources (recotem.datasource.*) that raise ImportError are skipped
# gracefully so that optional extras (sql, ga4, bigquery) can be absent
# without aborting startup for unrelated sources.


def test_plugin_ep_load_failure_raises_datasource_error() -> None:
    """When a THIRD-PARTY ep.load() raises ImportError, get_source_types must raise
    DataSourceError.

    Third-party plugins are fatal on load failure: it is better to fail loudly
    at startup than to silently omit a plugin the operator explicitly configured,
    which would cause mysterious 'unknown type' errors later.
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


# ---------------------------------------------------------------------------
# CRITICAL-1: graceful skip for builtin optional sources on ImportError
# ---------------------------------------------------------------------------


def test_builtin_ep_import_error_is_skipped_with_warning() -> None:
    """A builtin EP (recotem.datasource.*) raising ImportError must be skipped
    with a 'datasource_builtin_skipped' warning rather than raising DataSourceError.

    Note: get_source_types is lru_cache'd; tests must call cache_clear() before
    and after to avoid state leakage between test runs.
    """
    import structlog.testing

    ep = MagicMock()
    ep.name = "foo"
    ep.value = "recotem.datasource.foo:FooSource"
    ep.load.side_effect = ImportError("no module named 'recotem.datasource.foo'")

    with patch("recotem.datasource.registry.entry_points", return_value=[ep]):
        from recotem.datasource import registry

        registry.get_source_types.cache_clear()
        try:
            with structlog.testing.capture_logs() as captured:
                # Must not raise — the builtin EP is gracefully skipped.
                types = registry.get_source_types()

            # The skipped source must not appear in the registry.
            assert "foo" not in types, (
                "Skipped builtin EP must not appear in the registry"
            )

            # A warning must have been emitted.
            skip_events = [
                e for e in captured if e.get("event") == "datasource_builtin_skipped"
            ]
            assert skip_events, (
                "A 'datasource_builtin_skipped' warning must be logged when a "
                "builtin EP raises ImportError"
            )
            assert skip_events[0].get("log_level") == "warning"
            assert skip_events[0].get("type_name") == "foo"
        finally:
            registry.get_source_types.cache_clear()


def test_third_party_ep_import_error_is_fatal() -> None:
    """A third-party EP (not starting with 'recotem.datasource.') raising
    ImportError must still raise DataSourceError.
    """
    ep = MagicMock()
    ep.name = "evil"
    ep.value = "mypkg.evil:EvilSource"
    ep.load.side_effect = ImportError("mypkg not installed")

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


def test_builtin_ep_attribute_error_is_fatal() -> None:
    """A builtin EP raising AttributeError (not ImportError) must still raise
    DataSourceError — only ImportError gets the graceful-skip treatment.
    """
    ep = MagicMock()
    ep.name = "sql"
    ep.value = "recotem.datasource.sql:SQLSource"
    ep.load.side_effect = AttributeError("module has no attribute 'SQLSource'")

    with patch("recotem.datasource.registry.entry_points", return_value=[ep]):
        from recotem.datasource import registry

        registry.get_source_types.cache_clear()
        try:
            with pytest.raises(DataSourceError):
                registry.get_source_types()
        finally:
            registry.get_source_types.cache_clear()


def test_builtin_ep_skipped_other_eps_still_registered() -> None:
    """When a builtin EP is skipped, other successfully-loaded EPs are still
    registered in the result dict.

    Note: get_source_types is lru_cache'd; cache_clear() is called before and
    after to prevent state leakage between test runs.
    """
    from pydantic import BaseModel, Field

    class GoodConfig(BaseModel):
        type: str = Field(default="good", pattern="^good$")

    class GoodSource:
        type_name = "good"
        Config = GoodConfig
        extras_required = []
        no_expand_fields = frozenset()

        def fetch(self, ctx): ...

    ep_bad = MagicMock()
    ep_bad.name = "missing_extra"
    ep_bad.value = "recotem.datasource.missing_extra:MissingSource"
    ep_bad.load.side_effect = ImportError("optional extra not installed")

    ep_good = MagicMock()
    ep_good.load.return_value = GoodSource

    with patch(
        "recotem.datasource.registry.entry_points", return_value=[ep_bad, ep_good]
    ):
        from recotem.datasource import registry

        registry.get_source_types.cache_clear()
        try:
            types = registry.get_source_types()
            assert "good" in types, (
                "Successfully-loaded EP must still appear after a builtin skip"
            )
            assert "missing_extra" not in types, (
                "Skipped builtin EP must not appear in the registry"
            )
        finally:
            registry.get_source_types.cache_clear()
