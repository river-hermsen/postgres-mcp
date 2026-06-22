#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.2", "psycopg[binary]>=3.2"]
# ///
"""Multi-database, read-only PostgreSQL MCP server.

One MCP entry, every database on a single cluster. The cluster host and
credentials come from PG_BASE_URI (a libpq URI *without* a database name);
the target database is chosen per tool call. Every statement runs inside a
READ ONLY transaction with a statement timeout, so writes and runaway
queries fail at the server.

Env:
  PG_BASE_URI   required. e.g. postgresql://user:pass@host:5432
                (no trailing database; do not end with "/")
  PG_DEFAULT_DB optional. database used when a tool omits `database`
                (default: "postgres")
  PG_STMT_TIMEOUT_MS optional. per-query timeout in ms (default: 30000)
  PG_MAX_ROWS   optional. max rows returned per query (default: 1000)
"""
import os
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

import psycopg
from mcp.server.fastmcp import FastMCP

BASE = os.environ["PG_BASE_URI"].rstrip("/")
DEFAULT_DB = os.environ.get("PG_DEFAULT_DB", "postgres")
STMT_TIMEOUT_MS = int(os.environ.get("PG_STMT_TIMEOUT_MS", "30000"))
MAX_ROWS = int(os.environ.get("PG_MAX_ROWS", "1000"))
CONNECT_TIMEOUT_S = int(os.environ.get("PG_CONNECT_TIMEOUT_S", "10"))

mcp = FastMCP("pg-server")


def _connect(database: str) -> psycopg.Connection:
    """Open a READ ONLY connection to `database` on the cluster.

    `default_transaction_read_only=on` makes any write/DDL raise at the server,
    and `statement_timeout` bounds query runtime. We never trust the SQL string.
    """
    db = (database or DEFAULT_DB).strip()
    if not db or any(c in db for c in "/ \t\n@?#"):
        raise ValueError(f"invalid database name: {database!r}")
    conn = psycopg.connect(
        f"{BASE}/{db}",
        autocommit=False,
        connect_timeout=CONNECT_TIMEOUT_S,
        options=(
            f"-c default_transaction_read_only=on "
            f"-c statement_timeout={STMT_TIMEOUT_MS} "
            f"-c idle_in_transaction_session_timeout={STMT_TIMEOUT_MS}"
        ),
    )
    conn.read_only = True
    return conn


def _coerce(v):
    """Make a cell JSON-safe (datetimes, Decimal, UUID, bytes -> str)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, (Decimal, UUID)):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, (list, tuple)):
        return [_coerce(x) for x in v]
    if isinstance(v, dict):
        return {k: _coerce(x) for k, x in v.items()}
    return str(v)


def _run(database: str, sql: str, params=None) -> dict:
    try:
        with _connect(database) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return {"rows": [], "rowcount": cur.rowcount, "truncated": False}
            cols = [d.name for d in cur.description]
            rows = cur.fetchmany(MAX_ROWS + 1)
            truncated = len(rows) > MAX_ROWS
            rows = rows[:MAX_ROWS]
            return {
                "columns": cols,
                "rows": [{c: _coerce(v) for c, v in zip(cols, r)} for r in rows],
                "row_count": len(rows),
                "truncated": truncated,
            }
    except Exception as e:  # surface a clean error to the model, not a stack trace
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def list_databases() -> list[str]:
    """List the non-template databases available on the cluster."""
    out = _run(
        DEFAULT_DB,
        "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY 1",
    )
    if "error" in out:
        return [out["error"]]
    return [r["datname"] for r in out["rows"]]


@mcp.tool()
def list_tables(database: str, schema: str = "public") -> dict:
    """List tables in `schema` of `database`, with approximate row counts."""
    return _run(
        database,
        """
        SELECT n.nspname AS schema, c.relname AS table,
               c.reltuples::bigint AS approx_rows
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p') AND n.nspname = %s
        ORDER BY c.relname
        """,
        (schema,),
    )


@mcp.tool()
def describe_table(database: str, table: str, schema: str = "public") -> dict:
    """Show columns (name, type, nullability, default) for `schema.table`."""
    return _run(
        database,
        """
        SELECT column_name, data_type, is_nullable, column_default,
               ordinal_position
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )


@mcp.tool()
def execute_sql(database: str, sql: str) -> dict:
    """Run a read-only SQL query against `database` on the cluster.

    Returns {columns, rows, row_count, truncated} or {error}. Writes and DDL
    fail (the transaction is READ ONLY). Results are capped at PG_MAX_ROWS.
    """
    return _run(database, sql)


if __name__ == "__main__":
    mcp.run()
