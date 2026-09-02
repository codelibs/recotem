"""DataSource Protocol, DataSourceError, FetchContext, and plugin contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Literal,
    Protocol,
    get_args,
    get_origin,
    runtime_checkable,
)

if TYPE_CHECKING:
    import pandas as pd
    from pydantic import BaseModel


class DataSourceError(Exception):
    """Raised by a DataSource for any external / transient failure.

    Corresponds to exit code 3 in the train error contract.

    The ``code`` class attribute classifies this as a domain error, so
    ``recotem.training.pipeline`` records ``error_code="datasource_error"`` and
    suppresses ``exc_info`` in the ``train_error`` log event.  This prevents
    chained driver exceptions (psycopg ``OperationalError`` etc., which can
    embed DSN userinfo or hostnames in their ``__str__``) from being rendered
    into the structlog ``exception`` field by ``format_exc_info`` — that
    processor runs AFTER ``redact_sensitive_keys`` and would otherwise emit a
    non-redacted traceback string.

    Parameters
    ----------
    message:
        Human-readable description.  Must not contain cloud credentials,
        API keys, or signing keys.
    """

    code: ClassVar[str] = "datasource_error"

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
    no_expand_fields:
        A ``frozenset[str]`` of field names inside the source config whose
        string values must **never** receive ``${RECOTEM_RECIPE_*}``
        environment-variable expansion.  Plugin authors should list any fields
        that carry raw SQL, parameterised queries, or other content where
        ``${}`` should be treated as literals (e.g. ``{"sql",
        "query_parameters"}``).

        The built-in ``query`` and ``query_parameters`` keys are already
        protected globally via ``_NO_EXPAND_KEYS`` in ``loader.py``, but
        declaring them here provides defence-in-depth and documents the
        contract for future maintainers who might remove the global set.

        Default: ``frozenset()`` — no additional protected fields beyond the
        global ``_NO_EXPAND_KEYS``.

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
    no_expand_fields: ClassVar[frozenset[str]]

    def fetch(self, ctx: FetchContext) -> pd.DataFrame: ...


def _validate_config_discriminator(cls: type) -> None:
    """Assert that ``cls.Config`` carries the ``type`` discriminator field.

    ``recotem.datasource.registry.build_source_config_union`` assembles every
    registered ``Config`` into a pydantic discriminated union keyed on ``type``,
    and ``recotem.training.pipeline`` reads that field back to resolve the source
    class.  A ``Config`` without it still *loads* (pydantic's default
    ``extra="ignore"`` silently drops the YAML ``type:`` key) but fails at train
    time with "Recipe source has no discriminator 'type' field.", so the check
    belongs at plugin-discovery time where the message can name the culprit.
    """
    type_name = cls.type_name  # type: ignore[attr-defined]
    declaration = f'type: Literal["{type_name}"] = "{type_name}"'
    hint = (
        f"Declare '{declaration}' on the Config. "
        "See docs/plugin-authoring.md for the plugin contract."
    )

    model_fields = getattr(cls.Config, "model_fields", None)  # type: ignore[attr-defined]
    if not isinstance(model_fields, dict):
        raise DataSourceError(
            f"DataSource plugin '{cls.__qualname__}' has a 'Config' that is not a "
            f"pydantic BaseModel subclass. {hint}"
        )

    field_info = model_fields.get("type")
    if field_info is None:
        raise DataSourceError(
            f"DataSource plugin '{cls.__qualname__}' has a Config without the "
            f"required 'type' discriminator field. {hint}"
        )

    annotation = field_info.annotation
    if get_origin(annotation) is not Literal:
        raise DataSourceError(
            f"DataSource plugin '{cls.__qualname__}' declares Config.type as "
            f"{annotation!r}, which pydantic cannot discriminate on. "
            f"It must be a typing.Literal. {hint}"
        )

    literal_values = get_args(annotation)
    if literal_values != (type_name,):
        raise DataSourceError(
            f"DataSource plugin '{cls.__qualname__}' declares Config.type as "
            f"Literal{list(literal_values)!r}, which does not match its "
            f"type_name {type_name!r}. The registry resolves sources by "
            f"type_name, so the two must agree exactly. {hint}"
        )


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

    _validate_config_discriminator(cls)

    if not isinstance(cls.extras_required, list):  # type: ignore[union-attr]
        raise DataSourceError(
            f"DataSource plugin '{cls.__qualname__}' has an invalid "
            "'extras_required': must be a list of strings."
        )

    if not hasattr(cls, "no_expand_fields"):
        raise DataSourceError(
            f"DataSource plugin '{cls.__qualname__}' is missing required "
            "class attribute 'no_expand_fields'. "
            "Declare 'no_expand_fields: ClassVar[frozenset[str]] = frozenset()' "
            "(or list field names whose values must not receive env-var expansion). "
            "See docs/plugin-authoring.md for the plugin contract."
        )

    if not isinstance(cls.no_expand_fields, frozenset):  # type: ignore[union-attr]
        raise DataSourceError(
            f"DataSource plugin '{cls.__qualname__}' has an invalid "
            f"'no_expand_fields': must be a frozenset[str], "
            f"got {type(cls.no_expand_fields).__name__!r}. "  # type: ignore[union-attr]
            "See docs/plugin-authoring.md for the plugin contract."
        )

    if not hasattr(cls, "fetch") or not callable(getattr(cls, "fetch", None)):
        raise DataSourceError(
            f"DataSource plugin '{cls.__qualname__}' must define a 'fetch' method."
        )
