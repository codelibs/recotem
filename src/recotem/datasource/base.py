"""DataSource Protocol, DataSourceError, FetchContext, and plugin contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    import pandas as pd
    from pydantic import BaseModel


class DataSourceError(Exception):
    """Raised by a DataSource for any external / transient failure.

    Corresponds to exit code 3 in the train error contract.

    Parameters
    ----------
    message:
        Human-readable description.  Must not contain cloud credentials,
        API keys, or signing keys.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class FetchContext:
    """Contextual information passed to ``DataSource.fetch()``.

    Attributes
    ----------
    recipe_name:
        The name of the recipe being trained.  Useful for logging.
    run_id:
        A per-train-run UUID string for structured log correlation.
    extra:
        Arbitrary key/value metadata; reserved for future use.
    """

    recipe_name: str
    run_id: str
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DataSource(Protocol):
    """Protocol that every DataSource plugin must satisfy.

    Class-level attributes
    ----------------------
    type_name:
        Short string discriminator (e.g. ``"csv"``, ``"bigquery"``).
        Must be unique across all registered plugins.
    Config:
        A ``pydantic.BaseModel`` subclass that describes the config schema
        for this source type.  Used to build the dynamic discriminated union.
    extras_required:
        List of pip extras to suggest when optional dependencies are missing
        (e.g. ``["bigquery"]``).

    Instance methods
    ----------------
    ``__init__(self, config)``
        Accepts an instance of ``Config``.  Optional dependencies must be
        imported here, not at module level.
    ``fetch(self, ctx: FetchContext) -> pd.DataFrame``
        Fetch and return the raw interactions DataFrame.  Must raise
        :class:`DataSourceError` for any external / transient failure.
    ``probe(self) -> None``  (optional)
        Lightweight connectivity / auth check invoked by ``recotem validate``
        when the method is defined.  Should not load full data — use
        ``LIMIT 1`` / dry-run / ``fs.exists`` style checks.  Must raise
        :class:`DataSourceError` on failure.  Sources without ``probe`` are
        still validated for extras and config schema only.
    """

    type_name: ClassVar[str]
    Config: ClassVar[type[BaseModel]]
    extras_required: ClassVar[list[str]]

    def fetch(self, ctx: FetchContext) -> pd.DataFrame: ...


def validate_plugin_contract(cls: type) -> None:
    """Assert that *cls* satisfies the DataSource plugin contract.

    Raises
    ------
    DataSourceError
        With a descriptive message naming the missing attribute.
    """
    required_class_attrs = ("type_name", "Config", "extras_required")
    for attr in required_class_attrs:
        if not hasattr(cls, attr):
            raise DataSourceError(
                f"DataSource plugin '{cls.__qualname__}' is missing required "
                f"class attribute '{attr}'. "
                "See docs/plugin-authoring.md for the plugin contract."
            )

    if not isinstance(cls.type_name, str) or not cls.type_name:  # type: ignore[union-attr]
        raise DataSourceError(
            f"DataSource plugin '{cls.__qualname__}' has an invalid 'type_name': "
            "must be a non-empty string."
        )

    if not isinstance(cls.extras_required, list):  # type: ignore[union-attr]
        raise DataSourceError(
            f"DataSource plugin '{cls.__qualname__}' has an invalid "
            "'extras_required': must be a list of strings."
        )

    if not hasattr(cls, "fetch") or not callable(getattr(cls, "fetch", None)):
        raise DataSourceError(
            f"DataSource plugin '{cls.__qualname__}' must define a 'fetch' method."
        )
