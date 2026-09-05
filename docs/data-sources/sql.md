# SQL Data Source

The `sql` source lets Recotem train recommenders directly from a relational
database via [SQLAlchemy 2](https://www.sqlalchemy.org/). Supported dialects
are PostgreSQL, MySQL/MariaDB, and SQLite. Other dialects are not supported
and will raise a `DataSourceError` at training time.

See `examples/sql-sqlite/` for a zero-cloud walkthrough.

## Install

```bash
pip install "recotem[postgres]"   # PostgreSQL (via psycopg)
pip install "recotem[mysql]"      # MySQL / MariaDB (via PyMySQL)
pip install "recotem[sqlite]"     # SQLite (stdlib — no extra driver needed)
```

Without any of these extras, `recotem train` exits with:

```
DataSourceError: sqlalchemy is required for SQLSource. Install one of: recotem[postgres], recotem[mysql], recotem[sqlite].
```

## Recipe configuration

```yaml
source:
  type: sql
  dsn_env: RECOTEM_RECIPE_DB_DSN
  query: |
    SELECT user_id, product_id, purchased_at
    FROM orders
    WHERE purchased_at >= CAST(:since AS timestamp)
      AND status = 'paid'
  query_parameters:
    # Literal, not ${...}: query_parameters is on the no-expand list.
    since: "2026-01-01"
  connect_timeout_seconds: 10
  statement_timeout_seconds: 300
```

| Field | Required | Default | Notes |
|---|---|---|---|
| `dsn_env` | yes | — | Name of an env var matching `^RECOTEM_RECIPE_[A-Z0-9_]+$` containing the DSN. The DSN itself is never written to the recipe. |
| `query` | yes | — | Raw SQL. Never subject to `${...}` expansion (SQL injection foreclosure). |
| `query_parameters` | no | `{}` | Bound via SQLAlchemy `text().bindparams(...)`. **Not** subject to `${RECOTEM_RECIPE_*}` expansion — `query` and `query_parameters` are both on the loader's no-expand list, so a `${...}` here reaches the database as those literal characters. That is deliberate: expansion into a SQL string is an injection path. Parameterise with `:name` placeholders and set the values in the recipe. |
| `connect_timeout_seconds` | no | 10 | Valid range `[1, 60]` (out-of-range raises ValidationError). Passed as `connect_timeout` (PG/MySQL) or `timeout` (SQLite). |
| `statement_timeout_seconds` | no | 300 | Valid range `[1, 1800]` (out-of-range raises ValidationError). PG: `SET LOCAL statement_timeout = <ms>`. MySQL: `SET SESSION MAX_EXECUTION_TIME = <ms>`. MariaDB: `SET SESSION max_statement_time = <seconds>` (different unit and variable from MySQL; each server rejects the other's with `ERROR 1193 Unknown system variable`). Which of the two is issued follows the **server**, not the DSN scheme — SQLAlchemy identifies MariaDB from the connection banner, so `mysql+pymysql://` pointed at MariaDB still gets `max_statement_time`. Failure aborts training on PG/MySQL/MariaDB. SQLite: not enforced (no server-side timeout primitive); a `sql_statement_timeout_unsupported_on_sqlite` warning is logged so operators know the documented safety control is not in effect. |

## DSN examples

| Dialect | DSN |
|---|---|
| PostgreSQL | `postgresql+psycopg://user:pass@host:5432/db?sslmode=require` |
| MySQL | `mysql+pymysql://user:pass@host:3306/db?ssl=true` |
| MariaDB | `mariadb+pymysql://user:pass@host:3306/db?ssl=true` — `mysql+pymysql://` also works and reaches the same server |
| SQLite (file) | `sqlite:///absolute/path/to/file.db` |
| SQLite (read-only) | `sqlite:///file:absolute/path/to/file.db?mode=ro&uri=true` |

**The `+driver` suffix is required, not decorative.** A bare scheme picks
SQLAlchemy's default DBAPI, and for every dialect except SQLite that default is
a driver recotem does not install: `postgresql://` routes to `psycopg2` (the
extra ships psycopg v3), and `mysql://` / `mariadb://` route to `mysqldb` (the
extra ships PyMySQL). Recotem refuses such a DSN up front, naming the driver
and the spelling to use. `postgres://` is refused outright — SQLAlchemy 2.x
removed that dialect alias, so no suffix can rescue it.

## Parameter binding

Use SQLAlchemy named bind parameters (`:name`) for any value that varies between runs.
Do **not** use Python string formatting or `${...}` expansion in `query` or in
`query_parameters` — both are on the loader's no-expand list, so a `${...}` there is sent
to the database as a literal string rather than being substituted.

### Binding a date or timestamp

`query_parameters` values are typed `str | int | float | bool`, so a date or timestamp is
always bound as a **string**. Comparing a string bind against a date/timestamp column is
dialect-specific, and there is no portable spelling — use the form for your dialect:

| Dialect | Form | If you use the wrong one |
|---|---|---|
| PostgreSQL | `WHERE ts >= CAST(:since AS timestamp)` | Without the cast: `operator does not exist: timestamp without time zone >= character varying` (exit 3). |
| MySQL / MariaDB | `WHERE ts >= CAST(:since AS DATETIME)` | `CAST(... AS timestamp)` is a **syntax error** on MySQL (`SQLSTATE 42000`). The bare form also fails under strict mode. |
| SQLite | `WHERE ts >= :since` (no cast) | **Do not cast on SQLite.** `CAST('2026-06-01' AS timestamp)` evaluates to the integer `2026`, so the comparison silently matches every row instead of failing. Store timestamps as ISO-8601 text, which compares correctly as a string. |

```yaml
source:
  type: sql
  dsn_env: RECOTEM_RECIPE_DB_DSN
  query: |
    -- PostgreSQL spelling of the date bind; see the table above for MySQL and SQLite.
    SELECT user_id, item_id, ts
    FROM events
    WHERE ts >= CAST(:since AS timestamp)
      AND event_type = :event_type
  query_parameters:
    since: "2026-01-01"
    event_type: purchase
```

## Security

- The DSN must come from an env var whose name matches `^RECOTEM_RECIPE_[A-Z0-9_]+$`; it is
  **never** written to the recipe. Any userinfo in the DSN is stripped before it reaches log
  lines by `recotem.log_redaction`.
- TLS is strongly recommended in production. Always set `sslmode=require` (or stricter:
  `verify-ca`, `verify-full`) on PostgreSQL, or `ssl=true` (or specify a CA bundle via
  `ssl_ca=...`) on MySQL/MariaDB. Recotem does not enforce TLS — but the source emits a
  `sql_dsn_tls_not_configured` structlog warning at init when the DSN appears plaintext
  (PG without `sslmode`, or with `disable`/`allow`/`prefer`; MySQL/MariaDB without any
  `ssl*` query parameter). Operators with deployment-level TLS (service mesh, sidecar)
  can silence the warning by adding the explicit DSN flag.
- The DB user should have `SELECT` only on the relevant tables. Recotem issues
  `SET TRANSACTION READ ONLY` (PG), `SET SESSION TRANSACTION READ ONLY` (MySQL/MariaDB),
  or `PRAGMA query_only = ON` (SQLite) before running the query. If this command fails
  (e.g. insufficient privilege, or the SQLite pragma cannot be set), training is aborted
  with `DataSourceError`; it is not silently skipped. The authoritative boundary is still
  your grant model — never rely solely on the session flag.
- SSRF: by default, DSN hosts that resolve to private / loopback / link-local IPs are
  rejected. The guard inspects every routing form the libpq / PyMySQL drivers honour,
  not just the URL netloc:
  - `url.host` (the netloc, e.g. `postgresql://u:p@host/db`)
  - `?host=name` (libpq for PostgreSQL, PyMySQL for MySQL/MariaDB) — when set,
    SQLAlchemy's `make_url` leaves `url.host` empty but the driver still routes the
    TCP connect to the query value
  - `?hostaddr=ip` (libpq) — the actual TCP target IP; if both `host` and `hostaddr`
    are set, libpq uses `hostaddr` for the connect and `host` only for SNI / TLS
    certificate validation
  Three routing forms are refused outright because they cannot be resolved to a TCP
  target the SSRF check can validate and all amount to local pivots:
  - `?service=` (PostgreSQL) — libpq looks up parameters in `pg_service.conf`
  - `?unix_socket=` (MySQL/MariaDB) — connects to a local Unix domain socket
  - `?host=/abs/path` (PostgreSQL) — libpq treats absolute-path values as a
    Unix-socket directory
  Network-dialect DSNs that contain *no* host information at all (e.g.
  `postgresql:///db`) are also refused, because libpq / PyMySQL would otherwise
  default to the local socket / `127.0.0.1`.
  Set `RECOTEM_SQL_ALLOW_PRIVATE=1` to opt in to any of the above (intended for
  Docker Compose / Kubernetes service-name destinations, Unix-socket connections,
  or libpq service files). Note that this env var **also disables the
  DNS-rebinding re-check** before each probe/fetch — opting in means trusting the
  host end-to-end.
- DNS rebinding TOCTOU: the SSRF check pins the **full set of resolved public IPs**
  (IPv4 + IPv6) at init across every candidate routing host.  Before each probe/fetch
  the effective TCP target (libpq: `hostaddr` > query `host` > netloc; PyMySQL: query
  `host` > netloc) is re-resolved via `socket.getaddrinfo`; if no address overlaps the
  pinned set, the run is aborted. This is a best-effort defence — the SQL driver does
  its own resolution at connect time, so a sufficiently fast attacker controlling DNS
  can still rebind between our check and the driver's resolution.  Use platform
  controls (private network access, VPC peering, firewalls) as the authoritative
  boundary.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `RECOTEM_RECIPE_*` | — | The env var whose name you set in `dsn_env`. |
| `RECOTEM_MAX_SQL_ROWS` | 50_000_000 | Hard cap on rows returned by the query. Clamp `[1_000, 500_000_000]`. |
| `RECOTEM_SQL_ALLOW_PRIVATE` | (unset) | Truthy values (`1`, `true`, `yes`, `on`) opt into private/loopback DSN hosts. |

## Errors and exit codes

| Error | Exit | Message pattern |
|-------|------|----------------|
| DSN env var not set or empty | 3 | `DataSourceError: env var RECOTEM_RECIPE_DB_DSN is not set or is empty; set it to the database DSN (e.g. postgresql+psycopg://user:pass@host/db). The +driver suffix is required: a bare postgresql:// or mysql:// DSN routes to a driver recotem does not install` |
| Unsupported dialect | 3 | `DataSourceError: unsupported SQL dialect 'oracle'; supported DSN forms: ['mariadb+pymysql://', 'mysql+pymysql://', 'postgresql+psycopg://', 'sqlite:///'].` |
| `postgres://` alias | 3 | `DataSourceError: SQL dialect 'postgres' was removed in SQLAlchemy 2.x and cannot be loaded by any driver. Use postgresql+psycopg:// instead.` |
| DSN routes to an uninstalled driver | 3 | `DataSourceError: cannot load the 'psycopg2' driver for dialect 'postgresql': postgresql:// with no +driver suffix defaults to 'psycopg2', which recotem does not install. Write the DSN as postgresql+psycopg:// to use the driver pip install 'recotem[postgres]' provides, or install 'psycopg2' yourself.` |
| Query exceeds row cap | 3 | `DataSourceError: query result exceeds RECOTEM_MAX_SQL_ROWS=50000000 rows; tighten the query or raise the cap` |
| Private/loopback host refused | 3 | `DataSourceError: refusing to connect to private/loopback host '10.0.0.5'; set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in (intended for in-cluster or compose service-name destinations)` |
| DSN hostname does not resolve | 3 | `DataSourceError: hostname 'db.internal' does not resolve; verify the DSN host or set RECOTEM_SQL_ALLOW_PRIVATE=1 to bypass for offline tests` |
| libpq service-file routing refused | 3 | `DataSourceError: DSN routes via libpq service file (?service=...); this bypasses the network SSRF guard. Set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in.` |
| MySQL Unix-socket routing refused | 3 | `DataSourceError: DSN routes via Unix socket (?unix_socket=...); this bypasses the network SSRF guard. Set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in.` |
| Absolute-path host refused | 3 | `DataSourceError: DSN host is an absolute path (libpq Unix-socket form); this bypasses the network SSRF guard. Set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in.` |
| Network DSN with no host refused | 3 | `DataSourceError: DSN for dialect 'postgresql' does not specify a host; the driver would default to the local socket / 127.0.0.1 which is rejected by the SSRF guard. Specify a host explicitly or set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in.` |
| sqlalchemy not installed | 3 | `DataSourceError: sqlalchemy is required for SQLSource. Install one of: recotem[postgres], recotem[mysql], recotem[sqlite].` |
| Query returned no rows | 3 | `DataSourceError: source 'sql' returned no rows for recipe '<name>'; the query or file matched no data. ...` |
| Column missing after query | 3 | `DataSourceError: schema column(s) ['ts'] not found in the fetched data for recipe '<name>'; available columns: [...]` |

All SQL failures are wrapped in `DataSourceError` and produce exit 3 — including a missing
`schema:` column, which is a data-source problem (the query did not produce what the recipe
names), not a recipe-schema problem. The full error type is included in the stderr JSON line.

That covers every routing form the SSRF guard refuses — netloc host, `?host=`,
`?hostaddr=`, `?service=`, `?unix_socket=`, an absolute-path host, and a network DSN
with no host at all. The guard shares its public/private IP check with the HTTP source
fetcher, but a SQL DSN is not an HTTP fetch: it never reports exit 7, and none of the
`RECOTEM_HTTP_*` settings apply to it. Retry logic that treats exit 7 as transient will
therefore never see a DSN refusal, which is correct — a refused DSN is a permanent
configuration decision, not a transient network condition.

## Notes

- `recotem validate recipes/my_recipe.yaml` probes the database by issuing `SELECT 1`
  before training starts. This validates the DSN, driver installation, and host connectivity.
  It does **not** check that the columns named in `schema:` are in the result set — that
  would mean running the query, so validate reports `Schema columns: not checked (sql ...)`
  and the columns are verified at train time (exit 3 on a mismatch).
- Query results are read in chunks to bound memory usage during streaming. The chunk size is
  `min(100_000, RECOTEM_MAX_SQL_ROWS)` so the row cap is enforced before the first chunk is
  fully loaded.
- **Memory bound caveat:** `RECOTEM_MAX_SQL_ROWS` caps the total **row count**, not the
  resulting DataFrame's resident memory.  Chunks are accumulated into a list and concatenated
  at the end, so peak RAM is approximately `total_rows × bytes_per_row`.  Trainers with
  default cap (50 M rows) should expect ~2.5–5 GiB resident under wide-result queries;
  with the upper clamp (500 M rows) the same query can require 25 GiB+ of RAM.  Tighten
  the cap or the query columns if you need a memory bound, not just a row bound.  Server-
  side streaming via `stream_results=True` controls only the **wire-level** cursor;
  the row cap is the right knob for the consumer-side bound.
- `source.query`, `source.query_parameters` and `source.dsn_env` are all exempt from
  `${...}` expansion, unconditionally and regardless of variable name. A `${...}` written
  in any of them reaches the database as those literal characters. Bind values must be
  written literally in the recipe.
- SQLite `statement_timeout_seconds` is accepted by the recipe schema but is **not
  enforced** at the server level — SQLite has no equivalent of Postgres'
  `statement_timeout` or MySQL's `MAX_EXECUTION_TIME`. A `sql_statement_timeout_unsupported_on_sqlite`
  warning is emitted so operators know the documented safety control is not in effect on
  this dialect. (Read-only enforcement on SQLite uses `PRAGMA query_only = ON`, which IS
  effective — failure to set that pragma aborts training.) On PostgreSQL and MySQL/MariaDB,
  failure to set the timeout aborts training with `DataSourceError`.
- `flock` is host-local; across hosts use scheduler-level mutex (`concurrencyPolicy: Forbid`
  in Kubernetes CronJobs).
