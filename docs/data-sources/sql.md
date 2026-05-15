# SQL Data Source

The `sql` source lets Recotem train recommenders directly from a relational
database via [SQLAlchemy 2](https://www.sqlalchemy.org/). PostgreSQL, MySQL,
and SQLite are officially supported; other SQLAlchemy-compatible dialects
work if you install the driver yourself.

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
    WHERE purchased_at >= :since
      AND status = 'paid'
  query_parameters:
    since: ${RECOTEM_RECIPE_SINCE}
  connect_timeout_seconds: 10
  statement_timeout_seconds: 300
```

| Field | Required | Default | Notes |
|---|---|---|---|
| `dsn_env` | yes | — | Name of an env var matching `^RECOTEM_RECIPE_[A-Z0-9_]+$` containing the DSN. The DSN itself is never written to the recipe. |
| `query` | yes | — | Raw SQL. Never subject to `${...}` expansion (SQL injection foreclosure). |
| `query_parameters` | no | `{}` | Bound via SQLAlchemy `text().bindparams(...)`. Subject to `${RECOTEM_RECIPE_*}` expansion. |
| `connect_timeout_seconds` | no | 10 | Clamp `[1, 60]`. Passed as `connect_timeout` (PG/MySQL) or `timeout` (SQLite). |
| `statement_timeout_seconds` | no | 300 | Clamp `[1, 1800]`. PG: `SET LOCAL statement_timeout`. MySQL/MariaDB: `SET SESSION MAX_EXECUTION_TIME`. Failure aborts training on PG/MySQL/MariaDB. SQLite: no-op (no server-side timeout). |

## DSN examples

| Dialect | DSN |
|---|---|
| PostgreSQL | `postgresql+psycopg://user:pass@host:5432/db?sslmode=require` |
| MySQL | `mysql+pymysql://user:pass@host:3306/db?ssl=true` |
| SQLite (file) | `sqlite:///absolute/path/to/file.db` |
| SQLite (read-only) | `sqlite:///file:absolute/path/to/file.db?mode=ro&uri=true` |
| Snowflake (BYO driver) | `snowflake://user:pass@account/db?warehouse=wh` |

## Parameter binding

Use SQLAlchemy named bind parameters (`:name`) for any value that varies between runs.
Do **not** use Python string formatting or `${...}` expansion in `query` — the latter is
explicitly blocked to foreclose SQL injection.

```yaml
source:
  type: sql
  dsn_env: RECOTEM_RECIPE_DB_DSN
  query: |
    SELECT user_id, item_id, ts
    FROM events
    WHERE ts >= :since
      AND event_type = :event_type
  query_parameters:
    since: ${RECOTEM_RECIPE_SINCE}
    event_type: purchase
```

## Security

- The DSN must come from an env var whose name matches `^RECOTEM_RECIPE_[A-Z0-9_]+$`; it is
  **never** written to the recipe. Any userinfo in the DSN is stripped before it reaches log
  lines by `recotem.log_redaction`.
- TLS is strongly recommended in production. Always set `sslmode=require` (PG) or `ssl=true`
  (MySQL). Recotem does not inspect or enforce DSN flags.
- The DB user should have `SELECT` only on the relevant tables. Recotem issues
  `SET TRANSACTION READ ONLY` (PG) or `SET SESSION TRANSACTION READ ONLY` (MySQL/MariaDB)
  before running the query. If this command fails (e.g. insufficient privilege), training
  is aborted with `DataSourceError`; it is not silently skipped. SQLite has no transactional
  READ ONLY mechanism and the call is a no-op there. The authoritative boundary is still your
  grant model — never rely solely on the session flag.
- SSRF: by default, DSN hosts that resolve to private / loopback / link-local IPs are
  rejected. Set `RECOTEM_SQL_ALLOW_PRIVATE=1` to opt in (intended for Docker Compose /
  Kubernetes service-name destinations).

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `RECOTEM_RECIPE_*` | — | The env var whose name you set in `dsn_env`. |
| `RECOTEM_MAX_SQL_ROWS` | 50_000_000 | Hard cap on rows returned by the query. Clamp `[1_000, 500_000_000]`. |
| `RECOTEM_SQL_ALLOW_PRIVATE` | (unset) | Truthy values (`1`, `true`, `yes`, `on`) opt into private/loopback DSN hosts. |

## Errors and exit codes

| Error | Exit | Message pattern |
|-------|------|----------------|
| DSN env var not set or empty | 3 | `DataSourceError: env var RECOTEM_RECIPE_DB_DSN is not set or is empty; set it to the database DSN (e.g. postgresql://user:pass@host/db)` |
| Unsupported dialect | 3 | `DataSourceError: unsupported SQL dialect 'oracle'; officially supported: ['mysql', 'postgres', 'sqlite']. Other SQLAlchemy dialects work if you install the driver yourself.` |
| Missing driver for dialect | 3 | `DataSourceError: psycopg driver is required for dialect 'postgresql'. Install it with: pip install 'recotem[postgres]'` |
| Query exceeds row cap | 3 | `DataSourceError: query result exceeds RECOTEM_MAX_SQL_ROWS=50000000 rows; tighten the query or raise the cap` |
| Private/loopback host refused | 3 | `DataSourceError: refusing to connect to private/loopback host '10.0.0.5'; set RECOTEM_SQL_ALLOW_PRIVATE=1 to opt in (intended for in-cluster or compose service-name destinations)` |
| sqlalchemy not installed | 3 | `DataSourceError: sqlalchemy is required for SQLSource. Install one of: recotem[postgres], recotem[mysql], recotem[sqlite].` |
| Column missing after query | 2 | `RecipeError: column 'item_id' not found in query result` |

All SQL exceptions are wrapped in `DataSourceError` and produce exit 3. The full error type is
included in the stderr JSON line.

## Notes

- `recotem validate recipes/my_recipe.yaml` probes the database by issuing `SELECT 1`
  before training starts. This validates the DSN, driver installation, and host connectivity.
- Query results are read in chunks to bound memory usage during streaming. The chunk size is
  `min(100_000, RECOTEM_MAX_SQL_ROWS)` so the row cap is enforced before the first chunk is
  fully loaded.
- `source.query` and `source.dsn_env` are unconditionally exempt from `${...}` expansion
  regardless of variable name; only `query_parameters` values are expanded.
- SQLite `statement_timeout_seconds` is accepted by the recipe schema but is a no-op —
  SQLite has no server-side query timeout mechanism. On PostgreSQL and MySQL/MariaDB,
  failure to set the timeout aborts training with `DataSourceError`.
- `flock` is host-local; across hosts use scheduler-level mutex (`concurrencyPolicy: Forbid`
  in Kubernetes CronJobs).
