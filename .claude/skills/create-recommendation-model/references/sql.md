# Source reference: SQL database

Use for interactions in a relational database or warehouse reachable over a
SQLAlchemy DSN — PostgreSQL, MySQL, or SQLite (each behind its own extra:
`recotem[postgres]`, `recotem[mysql]`, `recotem[sqlite]`).

## Credentials: DSN from an env var (not in the recipe)

The recipe does **not** contain the DSN. It names an environment variable via
`dsn_env`, and the variable name must match `^RECOTEM_RECIPE_[A-Z0-9_]+$`. This
keeps the connection string (which usually embeds a password) out of the
recipe file:

```bash
export RECOTEM_RECIPE_DB_DSN="postgresql://user:pass@host:5432/dbname"
```

By default the SQL source refuses private/loopback DSN hosts (an SSRF guard).
Set `RECOTEM_SQL_ALLOW_PRIVATE=1` to allow a local/internal database — this also
disables the DNS-rebinding re-check, so only opt in for hosts you trust.

## Inputs to gather

- Backend (postgres / mysql / sqlite) and the **DSN** (to put in the env var).
- The **query** that returns user/item/[time] columns, and what counts as a
  positive interaction.
- Rough **row volume** for the window (see Cost / volume).

## `source:` block

```yaml
source:
  type: sql
  dsn_env: RECOTEM_RECIPE_DB_DSN       # env var holding the DSN; name must match RECOTEM_RECIPE_*
  query: |
    SELECT customer_id AS user_id,
           product_id  AS item_id,
           purchased_at AS ts
    FROM purchases
    WHERE purchased_at >= :since
  query_parameters:
    since: "2026-05-01"                # str | int | float | bool
  # connect_timeout_seconds: 10
  # statement_timeout_seconds: 300
```

Bind values via the driver's parameter syntax (e.g. `:name`); `${...}`
expansion is blocked inside `query` and `dsn_env`. Any `item_id` extraction uses
your SQL dialect's functions (`SUBSTRING`, `REGEXP_SUBSTR`/`regexp_match`, etc.).
The "users with ≥2 distinct items" filter from Step 5 goes in this query too.

## Cost / volume

No billing dry run; `validate` probes connectivity and the statement.
`RECOTEM_MAX_SQL_ROWS` (default 50,000,000) hard-caps the returned **row count**
— it does not bound resident DataFrame memory, so for very wide/large results
pre-aggregate in SQL. `connect_timeout_seconds` / `statement_timeout_seconds`
bound a slow or runaway query.

See `docs/data-sources/sql.md` for the full reference, including the
memory-bound caveat and the per-driver SSRF host forms.
