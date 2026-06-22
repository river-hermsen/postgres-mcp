# postgres-mcp

A tiny, read-only **multi-database** PostgreSQL MCP server. One MCP entry exposes
**every database on a single cluster** — the target database is chosen per tool
call, so you don't need a separate server entry (or restart) per database.

## Why

`postgres-mcp` (crystaldba) and the official server bind one entry to one
database. This binds one entry to one **cluster** and lets the model pass a
`database` argument to each call. Add one entry per cluster (prod, staging)
instead of one per database.

## Safety

- Every statement runs in a `READ ONLY` transaction (`default_transaction_read_only=on`)
  — `INSERT`/`UPDATE`/`DDL` fail at the server. The SQL string is never trusted.
- `statement_timeout` + `idle_in_transaction_session_timeout` bound runtime.
- Results capped at `PG_MAX_ROWS` (default 1000); `truncated` flags the cut.
- Best practice: connect as a **read-only role**, not a superuser, so safety
  doesn't rely solely on the transaction flag.

## Tools

| Tool             | Args                                   | Returns                                              |
| ---------------- | -------------------------------------- | ---------------------------------------------------- |
| `list_databases` | —                                      | non-template databases on the cluster                |
| `list_tables`    | `database`, `schema="public"`          | tables + approx row counts                           |
| `describe_table` | `database`, `table`, `schema="public"` | columns, types, nullability                          |
| `execute_sql`    | `database`, `sql`                      | `{columns, rows, row_count, truncated}` or `{error}` |

## Config

`PG_BASE_URI` is a libpq URI **without** a database name (no trailing `/db`).

```jsonc
// ~/.claude.json -> mcpServers
"pg-staging": {
  "command": "/Users/you/.local/bin/uv",
  "args": ["run", "/Users/you/Projects/postgres-mcp/server.py"],
  "env": {
    "PG_BASE_URI": "postgresql://user:pass@host:5432",
    "PG_DEFAULT_DB": "postgres"
  }
}
```

Add a second entry (e.g. `pg-prod`) with a different `PG_BASE_URI` to cover
another cluster. See `mcp.example.json`.

### Env vars

| Var                    | Default      | Meaning                                    |
| ---------------------- | ------------ | ------------------------------------------ |
| `PG_BASE_URI`          | _(required)_ | cluster URI, no database                   |
| `PG_DEFAULT_DB`        | `postgres`   | database used when a call omits `database` |
| `PG_STMT_TIMEOUT_MS`   | `30000`      | per-query timeout                          |
| `PG_MAX_ROWS`          | `1000`       | max rows per result                        |
| `PG_CONNECT_TIMEOUT_S` | `10`         | connection timeout                         |

## Run / test

```bash
PG_BASE_URI=postgresql://user:pass@host:5432 uv run server.py   # stdio MCP
```

`uv` resolves the inline PEP 723 dependencies (`mcp`, `psycopg[binary]`) on first run.
