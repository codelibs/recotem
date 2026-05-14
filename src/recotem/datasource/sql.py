from __future__ import annotations

import os
from typing import ClassVar, Literal

import pandas as pd
import structlog
from pydantic import BaseModel, ConfigDict, Field

from recotem._http_fetch import HttpFetchError, assert_host_public
from recotem.config import get_max_sql_rows, sql_allow_private
from recotem.datasource.base import DataSourceError, FetchContext

_DIALECT_TO_EXTRA = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlite": "sqlite",
}

# keyed by extra name, not dialect
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
        # The path separator must be explicit so postgres "host:5432/mydb" does not
        # render as "host:5432mydb"; sqlite stays correct because host is empty
        # and database is ":memory:" → "sqlite:///:memory:".
        safe_netloc = url.host or ""
        if url.port is not None:
            safe_netloc = f"{safe_netloc}:{url.port}"
        safe_dsn = f"{url.drivername}://{safe_netloc}/{url.database or ''}"
        _log.debug(
            "sql_source_initialized",
            dialect=backend,
            host=url.host or "(local)",
            dsn=safe_dsn,
        )

    def probe(self) -> None:
        import sqlalchemy
        from sqlalchemy import text
        from sqlalchemy.pool import NullPool

        try:
            engine = sqlalchemy.create_engine(
                self._url,
                connect_args=self._connect_args(),
                poolclass=NullPool,
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            raise DataSourceError(
                f"probe failed for dialect {self._dialect!r}: {type(exc).__name__}"
            ) from exc

    def _connect_args(self) -> dict[str, object]:
        if self._dialect.startswith("postgres"):
            return {"connect_timeout": self._config.connect_timeout_seconds}
        if self._dialect in {"mysql", "mariadb"}:
            return {"connect_timeout": self._config.connect_timeout_seconds}
        if self._dialect == "sqlite":
            return {"timeout": self._config.connect_timeout_seconds}
        return {}

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        import sqlalchemy
        from sqlalchemy import text
        from sqlalchemy.pool import NullPool

        cap = get_max_sql_rows()
        engine = sqlalchemy.create_engine(
            self._url,
            connect_args=self._connect_args(),
            poolclass=NullPool,
        )
        try:
            with engine.connect() as conn:
                self._apply_read_only(conn)
                self._apply_statement_timeout(conn)
                stmt = text(self._config.query)
                if self._config.query_parameters:
                    stmt = stmt.bindparams(**self._config.query_parameters)
                chunks: list[pd.DataFrame] = []
                total = 0
                for chunk in pd.read_sql(stmt, conn, chunksize=100_000):
                    total += len(chunk)
                    if total > cap:
                        raise DataSourceError(
                            f"query result exceeds RECOTEM_MAX_SQL_ROWS={cap} rows; "
                            "tighten the query or raise the cap"
                        )
                    chunks.append(chunk)
                df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(
                f"query failed on dialect {self._dialect!r}: {type(exc).__name__}"
            ) from exc

        _log.info(
            "sql_fetch_complete",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            dialect=self._dialect,
            rows_loaded=len(df),
        )
        return df

    def _apply_read_only(self, conn) -> None:
        from sqlalchemy import text

        try:
            if self._dialect.startswith("postgres"):
                conn.execute(text("SET TRANSACTION READ ONLY"))
            elif self._dialect in {"mysql", "mariadb"}:
                conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
        except Exception as exc:
            _log.warning(
                "sql_read_only_set_failed",
                dialect=self._dialect,
                error=type(exc).__name__,
            )

    def _apply_statement_timeout(self, conn) -> None:
        from sqlalchemy import text

        ms = self._config.statement_timeout_seconds * 1000
        try:
            if self._dialect.startswith("postgres"):
                conn.execute(text(f"SET LOCAL statement_timeout = {ms}"))
            elif self._dialect in {"mysql", "mariadb"}:
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {ms}"))
        except Exception as exc:
            _log.warning(
                "sql_statement_timeout_set_failed",
                dialect=self._dialect,
                error=type(exc).__name__,
            )
