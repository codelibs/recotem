"""Entry-point discovery and dynamic discriminated-union builder.

On first call ``get_source_types()`` iterates the ``recotem.datasources``
entry-point group, validates the plugin contract, detects duplicate
``type_name`` values, and returns a ``dict[str, type]`` mapping.

``build_source_config_union()`` uses that mapping to build a
``typing.Annotated`` discriminated union suitable for use in pydantic models.
"""

from __future__ import annotations

import importlib
from functools import lru_cache
from importlib.metadata import entry_points
from typing import Annotated, Any, Union

import structlog
from pydantic import Field

from recotem.datasource.base import DataSourceError, validate_plugin_contract

logger = structlog.get_logger(__name__)

# Fallback builtin sources loaded when the package is not installed via pip
# (i.e. the entry points in pyproject.toml are not visible).  When the package
# IS installed the same classes are registered via entry_points(), so we guard
# against duplicates below.
_FALLBACK_BUILTINS: dict[str, str] = {
    "csv": "recotem.datasource.csv:CSVSource",
    "parquet": "recotem.datasource.csv:ParquetSource",
    "bigquery": "recotem.datasource.bigquery:BigQuerySource",
    "sql": "recotem.datasource.sql:SQLSource",
    "ga4": "recotem.datasource.ga4:GA4Source",
}


def _load_class(fqcn: str) -> type:
    """Import and return a class by fully-qualified class name (``mod:Class``)."""
    module_path, class_name = fqcn.rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


@lru_cache(maxsize=1)
def get_source_types() -> dict[str, type]:
    """Return ``{type_name: source_class}`` for all registered plugins.

    Iterates the ``recotem.datasources`` entry-point group.  When no entry
    points are found (e.g. editable install without ``pip install -e .``) the
    builtin sources are added as a fallback.

    Validates each class against the plugin contract.
    Raises :class:`DataSourceError` on duplicate ``type_name`` or contract
    violation.

    The result is cached after the first call.
    """
    registry: dict[str, type] = {}

    # -- Discover via entry points (works when package is installed) ----
    discovered = list(entry_points(group="recotem.datasources"))
    for ep in discovered:
        try:
            cls = ep.load()
        except Exception as exc:
            raise DataSourceError(
                f"Failed to load DataSource plugin '{ep.name}' from '{ep.value}': {exc}"
            ) from exc

        try:
            validate_plugin_contract(cls)
        except DataSourceError as exc:
            raise DataSourceError(
                f"DataSource plugin '{ep.name}' from '{ep.value}' "
                f"failed contract validation: {exc.message}"
            ) from exc

        tn: str = cls.type_name  # type: ignore[union-attr]
        if tn in registry:
            existing_cls = registry[tn]
            raise DataSourceError(
                f"Duplicate DataSource type_name '{tn}' detected. "
                f"Conflicting plugins: "
                f"'{existing_cls.__module__}.{existing_cls.__qualname__}' "
                f"and '{cls.__module__}.{cls.__qualname__}'. "
                "Each plugin must register a unique type_name."
            )
        registry[tn] = cls
        logger.debug(
            "datasource_plugin_registered",
            type_name=tn,
            cls=f"{cls.__module__}.{cls.__qualname__}",
            source="entry_point",
        )

    # -- Fallback: load builtins directly when not discovered via ep ---
    if not registry:
        logger.debug(
            "datasource_no_entry_points_found",
            note="falling back to builtin sources",
        )
        for type_name, fqcn in _FALLBACK_BUILTINS.items():
            try:
                cls = _load_class(fqcn)
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to load builtin DataSource '{type_name}' "
                    f"from '{fqcn}': {exc}"
                ) from exc
            validate_plugin_contract(cls)
            registry[type_name] = cls
            logger.debug(
                "datasource_builtin_registered",
                type_name=type_name,
                cls=fqcn,
                source="fallback",
            )

    return registry


def build_source_config_union() -> Any:
    """Build a pydantic-compatible discriminated union of all source configs.

    Returns
    -------
    Annotated type
        ``Annotated[Union[Config1, Config2, ...], Field(discriminator='type')]``
        where each ``ConfigN`` corresponds to a registered DataSource plugin.

    Raises
    ------
    DataSourceError
        If fewer than one source type is registered (should not happen in
        practice) or on any plugin contract / duplicate error.
    """
    types = get_source_types()

    config_classes = [cls.Config for cls in types.values()]  # type: ignore[union-attr]

    if not config_classes:
        raise DataSourceError("No DataSource plugins are registered.")

    if len(config_classes) == 1:
        union_type = config_classes[0]
    else:
        union_type = Union[tuple(config_classes)]  # type: ignore[assignment]  # noqa: UP007

    return Annotated[union_type, Field(discriminator="type")]  # type: ignore[valid-type]


def get_source_class(type_name: str) -> type:
    """Look up a source class by its ``type_name``.

    Raises
    ------
    DataSourceError
        If *type_name* is not registered.  The error lists known type names.
    """
    types = get_source_types()
    if type_name not in types:
        known = sorted(types.keys())
        raise DataSourceError(
            f"Unknown DataSource type '{type_name}'. Known types: {known}."
        )
    return types[type_name]
