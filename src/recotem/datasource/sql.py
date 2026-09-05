from __future__ import annotations

import ipaddress
import os
import re
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
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlite": "sqlite",
}

# `postgres://` is NOT in the table above, deliberately. SQLAlchemy 2.x removed
# the dialect alias outright, so it cannot be rescued by any `+driver` suffix --
# `make_url` parses it happily and `get_dialect()` then raises NoSuchModuleError.
# Listing it as supported (which recotem did) advertised the one PostgreSQL
# spelling that can never work, so it gets its own message instead.
_REMOVED_DIALECT_ALIASES = {
    "postgres": "postgresql",
}

# The DSN form recotem's extras actually provide a driver for, per backend.
# This is what an operator has to type; the extra name alone is not enough,
# because installing the extra does NOT make the bare scheme work.
_BACKEND_RECOMMENDED_DSN = {
    "postgresql": "postgresql+psycopg://",
    "mysql": "mysql+pymysql://",
    "mariadb": "mariadb+pymysql://",
    "sqlite": "sqlite:///",
}

# SQLAlchemy driver name -> the module that must be importable for it.  The two
# differ often enough to matter: SQLAlchemy's `mysqldb` driver imports
# `MySQLdb`, and `pysqlite` is the stdlib `sqlite3`.
#
# Probing the driver the URL ACTUALLY routes to is the whole point.  Keying the
# probe off the extra instead (which recotem did) validated a driver the DSN was
# never going to load: `recotem[postgres]` installs psycopg v3, the probe
# imported `psycopg` and passed, and SQLAlchemy then loaded its DEFAULT
# PostgreSQL DBAPI -- psycopg2, which recotem never installs -- so every bare
# `postgresql://` DSN died at DBAPI-import time with a bare ModuleNotFoundError
# naming neither the module nor the fix.  The same held for `mysql://` and
# `mariadb://`, which default to `mysqldb`, not the `pymysql` the extra ships.
#
# This table is a CLOSED ALLOW-LIST, and membership is tested before the value
# is used.  ``get_driver_name()`` returns the ``+suffix`` from the DSN verbatim
# and ``make_url`` accepts any suffix at all, so a DSN of
# ``postgresql+<name>://`` would otherwise reach ``__import__(<name>)`` and run
# that module's top-level code.  Nothing untrusted supplies a DSN today -- it
# comes from an operator-set ``RECOTEM_RECIPE_*`` variable -- so this is a
# fail-closed convention rather than a patched vulnerability, matching the FQCN
# allow-list, the path-scheme allow-list and the SSRF guards, which all
# enumerate what is permitted and refuse the rest.  A lookup whose fallback is
# "import whatever string the DSN supplied" is the opposite shape.
#
# ``None`` means "known driver, nothing to import" (the stdlib sqlite3 module).
# It must NOT be reachable for an unknown driver: collapsing "known, no probe
# needed" into "unrecognised" would silently skip the preflight, which is the
# same silent-no-op shape as a statement timeout that sets no variable.  The
# membership test at the call site is what keeps the two apart.
_DRIVER_MODULE = {
    "psycopg": "psycopg",
    "psycopg2": "psycopg2",
    "pymysql": "pymysql",
    "mysqldb": "MySQLdb",
    "pysqlite": None,  # stdlib sqlite3, always importable
    "pysqlite_numeric": None,
}

_log = structlog.get_logger(__name__)

# PostgreSQL sslmode values that do NOT guarantee a TLS connection.  ``prefer``
# is psycopg's default — it attempts TLS but falls back to plaintext silently,
# which is exactly the failure mode operators forget about.  ``allow`` is
# similarly opportunistic.  Anything stricter (``require``, ``verify-ca``,
# ``verify-full``) is treated as TLS-configured.
_PG_PLAINTEXT_SSLMODES: frozenset[str] = frozenset({"disable", "allow", "prefer"})

# Bound on the ``__cause__`` walk in ``_dbapi_error``.  pandas -> SQLAlchemy ->
# driver is two links; the bound only stops a pathological self-referential
# chain from spinning.
_MAX_CAUSE_DEPTH = 6


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


def _dbapi_error(exc: BaseException) -> BaseException | None:
    """Return the driver (DBAPI) exception under a wrapper, if there is one.

    SQLAlchemy exposes it directly as ``orig``.  ``pandas.read_sql`` re-wraps
    the SQLAlchemy error in ``pandas.errors.DatabaseError``, which carries no
    ``orig`` of its own -- the one that does is a link further down the
    ``__cause__`` chain.  Walking the chain is what lets the query path name
    the driver class, not just the outermost wrapper.
    """
    cur: BaseException | None = exc
    for _ in range(_MAX_CAUSE_DEPTH):
        if cur is None:
            break
        orig = getattr(cur, "orig", None)
        if orig is not None:
            return orig
        cur = cur.__cause__
    return None


def _server_is_mariadb(conn, dialect: str) -> bool:
    """Return True when the server on the other end of *conn* is MariaDB.

    The DSN scheme is not authoritative about which server answers.
    ``mysql+pymysql://`` is the DSN form PyMySQL documents and the only
    PyMySQL row in ``docs/data-sources/sql.md``, and it connects to a MariaDB
    server just as happily as to MySQL — so ``url.get_backend_name()`` reports
    ``"mysql"`` for a large share of real MariaDB deployments.

    That distinction is load-bearing here because the two servers have
    disjoint statement-timeout variables: MariaDB has ``max_statement_time``
    (seconds) and rejects ``MAX_EXECUTION_TIME``; MySQL has
    ``MAX_EXECUTION_TIME`` (milliseconds) and rejects ``max_statement_time``.
    Both rejections are ``ERROR 1193 (HY000) Unknown system variable``, which
    ``_apply_statement_timeout`` turns into a refusal to run the query — so
    picking the variable off the scheme fails the whole fetch.

    SQLAlchemy already resolves this: its MySQL dialect reads the server
    banner on connect and records the verdict on ``dialect._is_mariadb``,
    regardless of which scheme the DSN used.  Prefer that answer and fall back
    to the scheme when the attribute is absent or is not a real ``bool`` (a
    test double, or a future SQLAlchemy that drops the attribute).
    """
    is_mariadb = getattr(conn.dialect, "_is_mariadb", None)
    if isinstance(is_mariadb, bool):
        return is_mariadb
    return dialect == "mariadb"


# Userinfo inside a URL that appears in free text: ``scheme://user:pass@host``.
# Bounded character classes (no ``/``, ``@`` or whitespace on either side of
# the colon) so the match cannot run past the authority section.
_USERINFO_IN_TEXT = re.compile(r"(?<=://)[^\s/@]*:[^\s/@]*@")

# Upper bound on the SQLAlchemy diagnostic appended by ``_error_label``.  Long
# enough for the sentences SQLAlchemy actually writes and short enough that a
# message quoting a large statement cannot flood the log line.
_MAX_SA_DETAIL = 200


def _sqlalchemy_detail(exc: Exception) -> str | None:
    """Return SQLAlchemy's own message for *exc*, redacted, or ``None``.

    Only ``sqlalchemy.exc.SQLAlchemyError`` instances qualify, and only those
    with no DBAPI error beneath them.  That combination is precisely the set
    whose ``__str__`` is text SQLAlchemy wrote itself rather than text a driver
    handed it, which is what makes it safe to surface: the reason
    ``_error_label`` refuses to interpolate ``str(exc)`` in general is that a
    *driver* exception can embed DSN userinfo, and a driver exception always
    arrives with an ``orig``.

    Userinfo is stripped anyway.  Three samples is not a proof about every
    SQLAlchemy exception type, and the redaction costs nothing.
    """
    try:
        from sqlalchemy.exc import SQLAlchemyError  # noqa: PLC0415
    except ImportError:  # pragma: no cover - sqlalchemy is a hard dep here
        return None
    if not isinstance(exc, SQLAlchemyError):
        return None
    detail = " ".join(str(exc).split())
    if not detail:
        return None
    detail = _USERINFO_IN_TEXT.sub("***@", detail)
    if len(detail) > _MAX_SA_DETAIL:
        detail = detail[:_MAX_SA_DETAIL] + "…"
    return detail


def _error_label(exc: Exception) -> str:
    """Name *exc* and, when present, the DBAPI error underneath it.

    When a DBAPI error is present, only class names and the SQLSTATE are used.
    Driver exception ``__str__`` can embed DSN userinfo and hostnames; a class
    name cannot, and SQLSTATE is a fixed five-character code from the SQL
    standard (``42P01`` undefined table, ``42501`` insufficient privilege,
    ...), so both stay safe to put in an operator-visible message.  The code is
    length- and charset-checked before use so a driver that puts something else
    in that attribute cannot smuggle free text into the message.

    When there is **no** DBAPI error, the class name alone is usually not
    actionable, and SQLAlchemy's own message is.  The case that made this worth
    fixing: a ``mariadb+pymysql://`` DSN pointed at a MySQL server reported

        probe failed for dialect 'mariadb': InvalidRequestError

    and nothing else, while SQLAlchemy's message underneath said "MySQL version
    8.4.11 is not a MariaDB variant" — which tells the operator exactly what to
    change.  ``docs/data-sources/sql.md`` recommends ``mysql+pymysql://`` for
    MariaDB servers, so assuming the mirror image works is an ordinary mistake
    to make, and the operator was left with a bare class name for it.
    """
    orig = _dbapi_error(exc)
    if orig is None:
        detail = _sqlalchemy_detail(exc)
        if detail is None:
            return type(exc).__name__
        return f"{type(exc).__name__}: {detail}"
    label = f"{type(exc).__name__} ({type(orig).__module__}.{type(orig).__name__})"
    sqlstate = getattr(orig, "sqlstate", None)
    if isinstance(sqlstate, str) and len(sqlstate) == 5 and sqlstate.isalnum():
        label = f"{label} [SQLSTATE {sqlstate}]"
    return label


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
                f"set it to the database DSN (e.g. "
                f"postgresql+psycopg://user:pass@host/db). The +driver suffix "
                f"is required: a bare postgresql:// or mysql:// DSN routes to "
                f"a driver recotem does not install"
            )

        try:
            url = make_url(dsn)
        except (sqlalchemy.exc.ArgumentError, ValueError, TypeError) as exc:
            raise DataSourceError(
                f"env var {config.dsn_env} is not a valid SQLAlchemy URL"
            ) from exc

        backend = url.get_backend_name()

        replacement = _REMOVED_DIALECT_ALIASES.get(backend)
        if replacement is not None:
            raise DataSourceError(
                f"SQL dialect {backend!r} was removed in SQLAlchemy 2.x and "
                f"cannot be loaded by any driver. Use "
                f"{_BACKEND_RECOMMENDED_DSN[replacement]} instead."
            )

        extra = _DIALECT_TO_EXTRA.get(backend)
        if extra is None:
            raise DataSourceError(
                f"unsupported SQL dialect {backend!r}; supported DSN forms: "
                f"{sorted(_BACKEND_RECOMMENDED_DSN.values())}."
            )

        # Probe the driver THIS URL routes to, not the extra's canonical one.
        # A bare scheme picks SQLAlchemy's default DBAPI, which for every
        # backend recotem supports except sqlite is a driver the extras do not
        # install -- see _DRIVER_MODULE.
        #
        # Runs BEFORE the SSRF guard on purpose. This is a pure local import
        # check: it does no I/O, cannot be influenced by the DSN's host, and
        # raises DataSourceError, so no connection is attempted either way --
        # the SSRF guard is not weakened by losing the race. Ordering it after
        # the guard instead means the DNS lookup fails first whenever the host
        # does not resolve, and a missing extra is then reported as "hostname
        # does not resolve" -- in CI and offline dev, which is exactly where a
        # missing extra is met.
        driver = url.get_driver_name()
        if driver not in _DRIVER_MODULE:
            # Refuse rather than import a DSN-supplied name.  Membership is
            # tested first so an unrecognised driver can never reach the
            # ``None`` ("stdlib, no probe") branch below.
            raise DataSourceError(
                f"unknown SQL driver {driver!r} in the DSN for dialect "
                f"{backend!r}. recotem probes a fixed set of drivers and will "
                f"not import a name supplied by the DSN. Known drivers: "
                f"{sorted(_DRIVER_MODULE)}. Write the DSN as "
                f"{_BACKEND_RECOMMENDED_DSN[backend]} instead."
            )
        driver_mod = _DRIVER_MODULE[driver]
        if driver_mod is not None:
            try:
                __import__(driver_mod)
            except ImportError as exc:
                recommended = _BACKEND_RECOMMENDED_DSN[backend]
                explicit = "+" in url.drivername
                detail = (
                    f"the DSN names driver {driver!r} explicitly"
                    if explicit
                    else (
                        f"{backend}:// with no +driver suffix defaults to "
                        f"{driver!r}, which recotem does not install"
                    )
                )
                raise DataSourceError(
                    f"cannot load the {driver!r} driver for dialect "
                    f"{backend!r}: {detail}. Write the DSN as "
                    f"{recommended} to use the driver "
                    f"pip install 'recotem[{extra}]' provides, or install "
                    f"{driver_mod!r} yourself."
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
                    # ``from None`` is deliberate.  ``assert_host_public`` is
                    # shared with the HTTP fetcher and reports its verdict as
                    # an HttpFetchError, but a SQL DSN is not an HTTP fetch —
                    # nothing here speaks HTTP and none of the RECOTEM_HTTP_*
                    # knobs apply.  ``_map_exception_to_exit`` walks
                    # ``__cause__`` for HttpFetchError, so chaining it would
                    # report exit 7 for these two refusals while the four
                    # sibling refusals above (service file, Unix socket,
                    # absolute-path host, no host) — the same guard reaching
                    # the same verdict through a different routing form —
                    # report exit 3.  Suppressing the cause keeps every
                    # routing form on the exit code that the ``code``
                    # attribute of this exception already advertises,
                    # ``datasource_error``.
                    msg = str(exc)
                    if "does not resolve" in msg:
                        raise DataSourceError(
                            f"hostname {host!r} does not resolve; "
                            "verify the DSN host or set RECOTEM_SQL_ALLOW_PRIVATE=1 "
                            "to bypass for offline tests"
                        ) from None
                    raise DataSourceError(
                        f"refusing to connect to private/loopback host {host!r}; "
                        "set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in (intended for "
                        "in-cluster or compose service-name destinations)"
                    ) from None
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
                f"probe failed for dialect {self._dialect!r}: {_error_label(exc)}"
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
            with engine.connect() as conn:
                # Session setup MUST run before stream_results is enabled.
                # With stream_results=True psycopg wraps every statement in
                # ``DECLARE ... CURSOR FOR``, and PostgreSQL cannot declare a
                # cursor over ``SET`` -- the session-setup statements would
                # fail with ``syntax error at or near "SET"`` and take the
                # whole fetch down before any row was read.
                self._apply_read_only(conn)
                self._apply_statement_timeout(conn)
                conn = conn.execution_options(stream_results=True)
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
                f"query failed on dialect {self._dialect!r}: {_error_label(exc)}"
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
            # DSN userinfo / hostnames in their ``__str__``.  Class names
            # cannot, so name the DBAPI error too: SQLAlchemy's own wrapper
            # collapses very different faults into ``ProgrammingError``, and
            # the driver class (e.g. ``psycopg.errors.InsufficientPrivilege``
            # vs ``psycopg.errors.SyntaxError``) is what tells an operator
            # whether to look at grants or at the statement.
            raise DataSourceError(
                f"failed to enforce READ ONLY transaction on {self._dialect!r}: "
                f"{_error_label(exc)}; refusing to run the query"
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
            elif _server_is_mariadb(conn, self._dialect):
                # MariaDB uses max_statement_time in seconds (DOUBLE), not ms.
                seconds = self._config.statement_timeout_seconds
                conn.execute(text(f"SET SESSION max_statement_time = {seconds}"))
            elif self._dialect in {"mysql", "mariadb"}:
                # Whole family, not just ``"mysql"``: the branch above already
                # claimed every MariaDB *server*, so what is left here is a
                # MySQL server — reached under either scheme.  Testing for
                # ``"mysql"`` alone would leave a ``mariadb`` scheme in front
                # of a MySQL server setting no timeout at all, which is the
                # silent no-op the SQLite branch goes out of its way to warn
                # about.
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {ms}"))
        except Exception as exc:
            # Do not interpolate ``str(exc)`` — driver error messages can embed
            # DSN userinfo / hostnames.  ``_error_label`` names the DBAPI class
            # and the SQLSTATE instead, which is what distinguishes "the server
            # does not have this variable" from "this account may not SET it";
            # ``from exc`` preserves the chain for debug-mode tracebacks.
            raise DataSourceError(
                f"failed to enforce statement_timeout on {self._dialect!r}: "
                f"{_error_label(exc)}; refusing to run the query"
            ) from exc
