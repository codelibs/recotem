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
        description=(
            "Name of the environment variable holding the DSN. Must match "
            "^RECOTEM_RECIPE_[A-Z0-9_]+$ (set RECOTEM_RECIPE_DB_DSN, etc.)."
        ),
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
            # Wrap IPv6 literals in brackets so urlparse inside assert_host_public
            # correctly identifies the full address (e.g. "fe80::1" not just "fe80").
            # SQLAlchemy's make_url strips the brackets from "[::1]" and returns "::1".
            host_for_check = url.host
            if ":" in host_for_check and not host_for_check.startswith("["):
                host_for_check = f"[{host_for_check}]"
            try:
                assert_host_public(f"db://{host_for_check}", allow_private=False)
            except HttpFetchError as exc:
                raise DataSourceError(
                    f"refusing to connect to private/loopback host {url.host!r}; "
                    "set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in (intended for "
                    "in-cluster or compose service-name destinations)"
                ) from exc

        self._config = config
        self._url = url
        self._dialect = backend

        # Redact userinfo from DSN before logging.  Build a credential-free
        # URL using URL.create (SQLAlchemy 2.x) so username and password are
        # fully omitted from the rendered string.  Query parameters (e.g.
        # sslmode, connect_timeout) and the driver suffix (+psycopg) are
        # preserved.  The try/except guards against future SQLAlchemy API
        # changes.
        try:
            from sqlalchemy.engine.url import URL as _SAUrl

            safe_dsn = _SAUrl.create(
                drivername=url.drivername,
                username=None,
                password=None,
                host=url.host,
                port=url.port,
                database=url.database,
                query=url.query,
            ).render_as_string(hide_password=True)
        except (AttributeError, TypeError, Exception):
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

        engine = None
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
        finally:
            if engine is not None:
                engine.dispose()

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
        engine = None
        try:
            engine = sqlalchemy.create_engine(
                self._url,
                connect_args=self._connect_args(),
                poolclass=NullPool,
            )
            with engine.connect() as conn:
                self._apply_read_only(conn)
                self._apply_statement_timeout(conn)
                stmt = text(self._config.query)
                if self._config.query_parameters:
                    stmt = stmt.bindparams(**self._config.query_parameters)
                # Use chunksize = min(100_000, cap) so that the first chunk
                # never physically loads more rows than the cap allows.
                chunksize = min(100_000, max(1, cap))
                chunks: list[pd.DataFrame] = []
                total = 0
                for chunk in pd.read_sql(stmt, conn, chunksize=chunksize):
                    if total + len(chunk) > cap:
                        raise DataSourceError(
                            f"query result exceeds RECOTEM_MAX_SQL_ROWS={cap} rows; "
                            "tighten the query or raise the cap"
                        )
                    total += len(chunk)
                    chunks.append(chunk)
                df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(
                f"query failed on dialect {self._dialect!r}: {type(exc).__name__}"
            ) from exc
        finally:
            if engine is not None:
                engine.dispose()

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

        if self._dialect == "sqlite":
            # SQLite has no transactional READ ONLY mode; intentional no-op.
            return
        try:
            if self._dialect.startswith("postgres"):
                conn.execute(text("SET TRANSACTION READ ONLY"))
            elif self._dialect in {"mysql", "mariadb"}:
                conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
        except Exception as exc:
            raise DataSourceError(
                f"failed to enforce READ ONLY transaction on {self._dialect!r}: "
                f"{type(exc).__name__}: {exc}; refusing to run the query"
            ) from exc

    def _apply_statement_timeout(self, conn) -> None:
        from sqlalchemy import text

        if self._dialect == "sqlite":
            # SQLite has no statement_timeout; intentional no-op.
            return
        ms = self._config.statement_timeout_seconds * 1000
        try:
            if self._dialect.startswith("postgres"):
                conn.execute(text(f"SET LOCAL statement_timeout = {ms}"))
            elif self._dialect in {"mysql", "mariadb"}:
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {ms}"))
        except Exception as exc:
            raise DataSourceError(
                f"failed to enforce statement_timeout on {self._dialect!r}: "
                f"{type(exc).__name__}: {exc}; refusing to run the query"
            ) from exc
