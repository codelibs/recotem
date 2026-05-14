from __future__ import annotations

import os
from typing import ClassVar, Literal

import pandas as pd
import structlog
from pydantic import BaseModel, ConfigDict, Field

from recotem._http_fetch import HttpFetchError, assert_host_public
from recotem.config import sql_allow_private
from recotem.datasource.base import DataSourceError, FetchContext

_DIALECT_TO_EXTRA = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlite": "sqlite",
}

_DIALECT_DRIVER_PROBE = {
    "postgres": "psycopg",
    "mysql": "pymysql",
    "sqlite": None,
}

_log = structlog.get_logger(__name__)


class SQLConfig(BaseModel):
    type: Literal["sql"]
    dsn_env: str = Field(
        ...,
        min_length=1,
        pattern=r"^RECOTEM_RECIPE_[A-Z0-9_]+$",
    )
    query: str = Field(..., min_length=1)
    query_parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    connect_timeout_seconds: int = Field(10, ge=1, le=60)
    statement_timeout_seconds: int = Field(300, ge=1, le=1800)

    model_config = ConfigDict(extra="forbid")


class SQLSource:
    type_name: ClassVar[str] = "sql"
    Config: ClassVar[type[BaseModel]] = SQLConfig
    extras_required: ClassVar[list[str]] = ["sqlalchemy"]
    no_expand_fields: ClassVar[frozenset[str]] = frozenset({"query", "dsn_env"})

    def __init__(self, config: SQLConfig) -> None:
        try:
            import sqlalchemy
            from sqlalchemy.engine.url import make_url
        except ImportError as exc:
            raise DataSourceError(
                "sqlalchemy is required for SQLSource. Install one of: "
                "recotem[postgres], recotem[mysql], recotem[sqlite]."
            ) from exc

        dsn = os.environ.get(config.dsn_env, "").strip()
        if not dsn:
            raise DataSourceError(
                f"env var {config.dsn_env} is not set or is empty; "
                f"set it to the database DSN (e.g. postgresql://user:pass@host/db)"
            )

        try:
            url = make_url(dsn)
        except sqlalchemy.exc.ArgumentError as exc:
            raise DataSourceError(
                f"env var {config.dsn_env} is not a valid SQLAlchemy URL"
            ) from exc

        backend = url.get_backend_name()
        extra = _DIALECT_TO_EXTRA.get(backend)
        if extra is None:
            raise DataSourceError(
                f"unsupported SQL dialect {backend!r}; "
                f"officially supported: {sorted(set(_DIALECT_TO_EXTRA.values()))}. "
                "Other SQLAlchemy dialects work if you install the driver yourself."
            )

        driver_mod = _DIALECT_DRIVER_PROBE.get(extra)
        if driver_mod is not None:
            try:
                __import__(driver_mod)
            except ImportError as exc:
                raise DataSourceError(
                    f"{driver_mod} driver is required for dialect {backend!r}. "
                    f"Install it with: pip install 'recotem[{extra}]'"
                ) from exc

        if url.host and not sql_allow_private():
            try:
                assert_host_public(f"db://{url.host}", allow_private=False)
            except HttpFetchError as exc:
                raise DataSourceError(
                    f"refusing to connect to private/loopback host {url.host!r}; "
                    "set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in (intended for "
                    "in-cluster or compose service-name destinations)"
                ) from exc

        self._config = config
        self._url = url
        self._dialect = backend

        # Redact userinfo from DSN before logging; redact_url_userinfo only covers
        # HTTP(S)/FTP schemes so we strip credentials directly from the URL object.
        safe_netloc = url.host or ""
        if url.port is not None:
            safe_netloc = f"{safe_netloc}:{url.port}"
        safe_dsn = f"{url.drivername}://{safe_netloc}{url.database or ''}"
        _log.debug(
            "sql_source_initialized",
            dialect=backend,
            host=url.host or "(local)",
            dsn=safe_dsn,
        )

    def probe(self) -> None:
        raise NotImplementedError  # filled in Task 2.5

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        raise NotImplementedError  # filled in Task 2.6
