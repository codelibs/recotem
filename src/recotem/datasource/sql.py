from __future__ import annotations

import ipaddress
import os
import socket
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

# PostgreSQL sslmode values that do NOT guarantee a TLS connection.  ``prefer``
# is psycopg's default — it attempts TLS but falls back to plaintext silently,
# which is exactly the failure mode operators forget about.  ``allow`` is
# similarly opportunistic.  Anything stricter (``require``, ``verify-ca``,
# ``verify-full``) is treated as TLS-configured.
_PG_PLAINTEXT_SSLMODES: frozenset[str] = frozenset({"disable", "allow", "prefer"})


def _warn_if_tls_not_configured(dialect: str, query: dict[str, str]) -> None:
    """Emit a structured warning when the DSN does not configure TLS.

    Heuristic check intended as an advisory, not an enforcement:

    * postgres / postgresql: warns if ``sslmode`` is absent or one of
      ``disable`` / ``allow`` / ``prefer`` (the modes that permit plaintext).
    * mysql / mariadb: warns if no ``ssl`` / ``ssl_*`` query parameter is
      present (driver default is plaintext).
    * sqlite: not network-bearing; no check.

    Driver-specific TLS flags vary; the heuristic deliberately under-detects
    rather than misclassify.  Operators can silence the warning by adding the
    explicit TLS query parameter to the DSN.
    """
    if dialect.startswith("postgres"):
        sslmode = (query.get("sslmode") or "").lower()
        if sslmode in _PG_PLAINTEXT_SSLMODES or sslmode == "":
            _log.warning(
                "sql_dsn_tls_not_configured",
                dialect=dialect,
                detected_sslmode=sslmode or "(absent)",
                hint=(
                    "Add ?sslmode=require (or verify-ca / verify-full) to the "
                    "DSN to force TLS.  Plaintext connections to postgres are "
                    "subject to credential interception on the wire."
                ),
            )
    elif dialect in {"mysql", "mariadb"}:
        # pymysql + drivers use one of these keys to indicate TLS.
        ssl_keys = {"ssl", "ssl_ca", "ssl_cert", "ssl_key", "ssl_verify_cert"}
        has_ssl = any(k in query for k in ssl_keys) and any(
            (query.get(k) or "").lower() not in {"false", "0", ""} for k in ssl_keys
        )
        if not has_ssl:
            _log.warning(
                "sql_dsn_tls_not_configured",
                dialect=dialect,
                hint=(
                    "Add ?ssl=true (or ssl_ca=...) to the DSN to force TLS.  "
                    "Plaintext connections to mysql/mariadb are subject to "
                    "credential interception on the wire."
                ),
            )


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
    # Extras correspond to pyproject.toml extra names (postgres, mysql, sqlite).
    extras_required: ClassVar[list[str]] = ["postgres", "mysql", "sqlite"]
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
        except (sqlalchemy.exc.ArgumentError, ValueError, TypeError) as exc:
            raise DataSourceError(
                f"env var {config.dsn_env} is not a valid SQLAlchemy URL"
            ) from exc

        backend = url.get_backend_name()
        extra = _DIALECT_TO_EXTRA.get(backend)
        if extra is None:
            raise DataSourceError(
                f"unsupported SQL dialect {backend!r}; "
                f"officially supported: {sorted(set(_DIALECT_TO_EXTRA.values()))}."
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

        # SSRF guard: reject private/loopback/link-local hosts unless opted in.
        # The full resolved IP set (IPv4 + IPv6) is pinned so that a DNS
        # rebinding attack between __init__ and the actual connect can be
        # detected in fetch()/probe().  Storing the full set rather than the
        # first address is critical for dual-stack hosts: getaddrinfo on the
        # re-check may legitimately return a different family, and a single-
        # IP pin would mis-classify that as a rebind.
        #
        # The guard inspects every routing form the libpq / PyMySQL drivers
        # honour, not just ``url.host``.  SQLAlchemy's ``make_url`` only
        # populates ``url.host`` from the netloc; when the destination is
        # supplied via a URL query parameter (e.g.
        # ``postgresql:///db?host=169.254.169.254``), ``url.host`` is empty
        # and the driver still routes the TCP connect to the query value.
        # The checks therefore cover:
        #
        # * Routing forms that *can* be resolved to a TCP IP and are
        #   validated against the public/private IP allow-list:
        #     - ``url.host``                      (netloc)
        #     - ``?host=name`` (postgres, mysql)  (libpq / PyMySQL routing)
        #     - ``?hostaddr=ip`` (postgres)       (libpq TCP target IP)
        # * Routing forms that are refused outright because they cannot be
        #   resolved to a TCP target the guard can validate and amount to
        #   local pivots:
        #     - ``?service=`` (postgres)          (pg_service.conf lookup)
        #     - ``?unix_socket=`` (mysql)         (local UDS)
        #     - ``?host=/abs/path`` (postgres)    (libpq Unix-socket dir)
        # * Network dialects whose DSN contains *no* host information at
        #   all are refused: libpq / PyMySQL default to the local socket
        #   or 127.0.0.1, which is exactly the local-pivot the guard
        #   exists to prevent.
        #
        # All of the above are reachable via the recipe-author-controlled
        # DSN env var, so each must be gated to honour the
        # ``RECOTEM_SQL_ALLOW_PRIVATE`` opt-in.  SQLite is exempt: there is
        # no network connect (``url.database`` is a filesystem path).
        self._pinned_ips: set[str] = set()
        self._rebinding_host: str | None = None
        if backend != "sqlite" and not sql_allow_private():
            q = url.query

            # Refuse routing forms that bypass the network guard by design.
            if backend.startswith("postgres") and q.get("service"):
                raise DataSourceError(
                    "DSN routes via libpq service file (?service=...); "
                    "this bypasses the network SSRF guard. "
                    "Set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in."
                )
            if backend in {"mysql", "mariadb"} and q.get("unix_socket"):
                raise DataSourceError(
                    "DSN routes via Unix socket (?unix_socket=...); "
                    "this bypasses the network SSRF guard. "
                    "Set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in."
                )

            # Collect every candidate TCP-target host the driver could use.
            candidates: list[str] = []
            if url.host:
                candidates.append(url.host)
            if backend.startswith("postgres"):
                for key in ("hostaddr", "host"):
                    v = q.get(key)
                    if v:
                        candidates.append(v)
            elif backend in {"mysql", "mariadb"}:
                v = q.get("host")
                if v:
                    candidates.append(v)

            # libpq treats an absolute-path ``host=`` value as a Unix-socket
            # directory.  Refuse it for the same reason as ?unix_socket=.
            for c in candidates:
                if c.startswith("/"):
                    raise DataSourceError(
                        "DSN host is an absolute path (libpq Unix-socket "
                        "form); this bypasses the network SSRF guard. "
                        "Set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in."
                    )

            # No host info at all → driver-default localhost / local socket.
            if not candidates:
                raise DataSourceError(
                    f"DSN for dialect {backend!r} does not specify a host; "
                    "the driver would default to the local socket / 127.0.0.1 "
                    "which is rejected by the SSRF guard. Specify a host "
                    "explicitly or set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in."
                )

            # Deduplicate while preserving order.  A DSN like
            # ``postgresql:///db?host=foo`` produces a single candidate;
            # a DSN like ``postgresql://x:y@h/db?host=h`` produces two
            # copies of the same host and only needs one SSRF lookup.
            seen: set[str] = set()
            deduped_candidates: list[str] = []
            for c in candidates:
                if c not in seen:
                    seen.add(c)
                    deduped_candidates.append(c)

            # Run the SSRF check on every candidate.  Pin the union of
            # resolved public IPs so the rebinding re-check has the full
            # dual-stack set to intersect against.
            for host in deduped_candidates:
                host_for_check = host
                # Wrap IPv6 literals in brackets so urlparse inside
                # assert_host_public identifies the full address (e.g.
                # "fe80::1" not just "fe80").  SQLAlchemy's make_url
                # strips the brackets from "[::1]" and returns "::1".
                if ":" in host_for_check and not host_for_check.startswith("["):
                    host_for_check = f"[{host_for_check}]"
                try:
                    pinned_ips = assert_host_public(
                        f"db://{host_for_check}", allow_private=False
                    )
                except HttpFetchError as exc:
                    msg = str(exc)
                    if "does not resolve" in msg:
                        raise DataSourceError(
                            f"hostname {host!r} does not resolve; "
                            "verify the DSN host or set RECOTEM_SQL_ALLOW_PRIVATE=1 "
                            "to bypass for offline tests"
                        ) from exc
                    raise DataSourceError(
                        f"refusing to connect to private/loopback host {host!r}; "
                        "set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in (intended for "
                        "in-cluster or compose service-name destinations)"
                    ) from exc
                if pinned_ips:
                    self._pinned_ips.update(pinned_ips)

            # The TCP target that the driver actually connects to is the
            # one we re-resolve in _check_rebinding.  libpq's precedence
            # is ``hostaddr`` > ``host`` (query) > netloc; PyMySQL uses
            # the query ``host`` if set, otherwise the netloc.
            if backend.startswith("postgres"):
                self._rebinding_host = q.get("hostaddr") or q.get("host") or url.host
            elif backend in {"mysql", "mariadb"}:
                self._rebinding_host = q.get("host") or url.host

        self._config = config
        self._url = url
        self._dialect = backend

        # Advisory TLS check: warn (do not refuse) when the DSN points at a
        # plaintext connection.  We do not enforce TLS by default because
        # operators frequently use service-mesh or in-cluster destinations
        # where TLS is layered below the SQL driver; refusing plaintext
        # outright would break those deployments.  The warning is opt-out by
        # configuring sslmode (PG) / ssl (MySQL/MariaDB) in the DSN.
        _warn_if_tls_not_configured(backend, dict(url.query))

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
        except (AttributeError, TypeError):
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

    def _check_rebinding(self) -> None:
        """Re-resolve the DSN host and raise DataSourceError if the IP changed.

        This is a TOCTOU mitigation: an attacker who controls DNS could change
        a public IP to a private one between __init__ (where SSRF is checked)
        and the actual TCP connect in fetch()/probe().  We re-verify that the
        current resolution still intersects the pinned set.

        The host re-checked is :attr:`_rebinding_host`, which reflects the
        driver's connect-routing precedence (libpq: ``hostaddr`` > query
        ``host`` > netloc; PyMySQL: query ``host`` > netloc).  ``url.host``
        alone is not authoritative when the DSN uses query-parameter routing
        (e.g. ``postgresql:///db?host=...``).

        Skipped when:
        - no pinned IPs were recorded (allow_private mode or SQLite)
        - the rebinding host is unset (allow_private mode or SQLite)
        - the rebinding host is already a numeric IP literal (no DNS involved)
        """
        if not self._pinned_ips:
            return
        host = self._rebinding_host
        if not host:
            return
        # Skip re-check for numeric IP literals — there is no DNS rebinding risk.
        try:
            ipaddress.ip_address(host)
            return
        except ValueError:
            pass  # hostname, not a literal IP

        # Use getaddrinfo (not gethostbyname_ex, which is IPv4-only) so the
        # re-check resolves both IPv4 and IPv6 records — matching the family
        # coverage of the original pin in ``__init__`` and avoiding false-
        # positive "DNS rebinding detected" errors on legitimate dual-stack
        # or IPv6-only hosts.
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as exc:
            # DNS resolution failed on re-check; treat as a changed/gone address.
            raise DataSourceError(
                f"DNS re-resolution of {host!r} failed before connect; "
                "aborting to prevent SSRF via DNS rebinding"
            ) from exc
        current_ips: set[str] = set()
        for fam, _socktype, _proto, _canon, sockaddr in infos:
            if fam not in (socket.AF_INET, socket.AF_INET6):
                continue
            current_ips.add(sockaddr[0])
        if not current_ips.intersection(self._pinned_ips):
            raise DataSourceError(
                f"DNS rebinding detected for host {host!r}: "
                f"pinned={self._pinned_ips}, current={current_ips}; "
                "aborting to prevent SSRF"
            )

    def probe(self) -> None:
        import sqlalchemy
        from sqlalchemy import text
        from sqlalchemy.pool import NullPool

        self._check_rebinding()
        engine = None
        try:
            engine = sqlalchemy.create_engine(
                self._url,
                connect_args=self._connect_args(),
                poolclass=NullPool,
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except DataSourceError:
            raise
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

        self._check_rebinding()
        cap = get_max_sql_rows()
        engine = None
        try:
            engine = sqlalchemy.create_engine(
                self._url,
                connect_args=self._connect_args(),
                poolclass=NullPool,
            )
            # stream_results=True enables server-side cursors where the driver
            # supports them (PostgreSQL: named server-side cursor via psycopg;
            # MySQL/MariaDB: SSCursor when pymysql is used with the appropriate
            # connect_args).  For SQLite this option is accepted but has no
            # effect — SQLite always materialises the full result in the client.
            # True streaming (avoiding full materialisation) is therefore only
            # guaranteed on PostgreSQL with psycopg, and on MySQL/MariaDB when
            # SSCursor is active.
            with engine.connect().execution_options(stream_results=True) as conn:
                self._apply_read_only(conn)
                self._apply_statement_timeout(conn)
                stmt = text(self._config.query)
                if self._config.query_parameters:
                    stmt = stmt.bindparams(**self._config.query_parameters)
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
            # SQLite has no transactional READ ONLY, but `PRAGMA query_only=ON`
            # rejects writes for the rest of the connection session.  Fail
            # closed if the pragma cannot be issued — silently degrading to a
            # writable session for an SSRF-trusted recipe is exactly the
            # surprise we want to avoid for users following the SQLite tutorial
            # examples.
            try:
                conn.execute(text("PRAGMA query_only = ON"))
            except Exception as exc:
                raise DataSourceError(
                    "failed to enforce read-only mode on sqlite "
                    f"({type(exc).__name__}); refusing to run the query"
                ) from exc
            return
        try:
            if self._dialect.startswith("postgres"):
                conn.execute(text("SET TRANSACTION READ ONLY"))
            elif self._dialect in {"mysql", "mariadb"}:
                conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
        except Exception as exc:
            # Do not interpolate ``str(exc)`` — driver exceptions can embed
            # DSN userinfo / hostnames in their ``__str__``.  The class name
            # plus the chained ``__cause__`` give operators enough context.
            raise DataSourceError(
                f"failed to enforce READ ONLY transaction on {self._dialect!r}: "
                f"{type(exc).__name__}; refusing to run the query"
            ) from exc

    def _apply_statement_timeout(self, conn) -> None:
        from sqlalchemy import text

        if self._dialect == "sqlite":
            # SQLite has no server-side statement timeout.  Surface this as a
            # warning rather than a silent no-op so operators understand that
            # the documented safety control is not in effect on this dialect.
            _log.warning(
                "sql_statement_timeout_unsupported_on_sqlite",
                requested_seconds=self._config.statement_timeout_seconds,
            )
            return
        ms = self._config.statement_timeout_seconds * 1000
        try:
            if self._dialect.startswith("postgres"):
                conn.execute(text(f"SET LOCAL statement_timeout = {ms}"))
            elif self._dialect == "mariadb":
                # MariaDB uses max_statement_time in seconds (DOUBLE), not ms.
                seconds = self._config.statement_timeout_seconds
                conn.execute(text(f"SET SESSION max_statement_time = {seconds}"))
            elif self._dialect == "mysql":
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {ms}"))
        except Exception as exc:
            # Drop ``str(exc)`` — driver error messages can include DSN
            # userinfo / hostnames.  ``from exc`` preserves the chain for
            # debug-mode tracebacks.
            raise DataSourceError(
                f"failed to enforce statement_timeout on {self._dialect!r}: "
                f"{type(exc).__name__}; refusing to run the query"
            ) from exc
