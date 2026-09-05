"""Database access.

Two rules are enforced here rather than left to convention:

1. A tenant connection is opened through `tenant_connection`, which records the
   active tenant on the thread. Opening a second, different tenant while one is
   open raises `TenantIsolationError`. Spec section 7: "There is no code path in
   the orchestrator that holds two tenants' credentials simultaneously."
2. Every statement issued on behalf of a human is written to the control-plane
   access log (spec section 9) by `audited_query`.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any, Iterator, Sequence

import psycopg2
import psycopg2.extras
from psycopg2.extras import register_uuid
from psycopg2.extensions import connection as PGConnection

from fracture.core.errors import TenantIsolationError
from fracture.core.logging import get_logger

log = get_logger("core.db")
_local = threading.local()

# uuid.UUID binds natively; without this every registry call has to str() its keys.
register_uuid()

DEFAULT_STATEMENT_TIMEOUT_MS = 300_000


@contextlib.contextmanager
def connect(dsn: str, autocommit: bool = False, statement_timeout_ms: int | None = None) -> Iterator[PGConnection]:
    """Open a connection, always closing it, never leaking it on error."""
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = autocommit
        with conn.cursor() as cur:
            cur.execute(
                "set statement_timeout = %s",
                (statement_timeout_ms or DEFAULT_STATEMENT_TIMEOUT_MS,),
            )
        if not autocommit:
            conn.commit()
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not conn.closed and not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def active_tenant() -> str | None:
    return getattr(_local, "tenant_slug", None)


@contextlib.contextmanager
def tenant_connection(
    tenant_slug: str, dsn: str, autocommit: bool = False
) -> Iterator[PGConnection]:
    """Open a connection scoped to exactly one tenant.

    Re-entering with the same slug is fine (nested asset code). Entering with a
    different slug while one is held is a hard error, not a warning.
    """
    current = active_tenant()
    if current is not None and current != tenant_slug:
        raise TenantIsolationError(
            f"tenant '{current}' connection is already open in this context; "
            f"refusing to open '{tenant_slug}' alongside it"
        )
    _local.tenant_slug = tenant_slug
    try:
        with connect(dsn, autocommit=autocommit) as conn:
            yield conn
    finally:
        _local.tenant_slug = current


def query(conn: PGConnection, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return []
        return [dict(row) for row in cur.fetchall()]


def query_one(conn: PGConnection, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = query(conn, sql, params)
    return rows[0] if rows else None


def scalar(conn: PGConnection, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def execute(conn: PGConnection, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def execute_batch(
    conn: PGConnection, sql: str, rows: Sequence[Sequence[Any]], page_size: int = 1000
) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=page_size)
        return len(rows)


def execute_values(
    conn: PGConnection, sql: str, rows: Sequence[Sequence[Any]], template: str | None = None, page_size: int = 1000
) -> int:
    """Multi-row INSERT. `sql` must contain a single %s placeholder for VALUES."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, template=template, page_size=page_size)
        return len(rows)


def run_script(conn: PGConnection, sql: str) -> None:
    """Run a multi-statement DDL script."""
    with conn.cursor() as cur:
        cur.execute(sql)


def table_exists(conn: PGConnection, schema: str, table: str) -> bool:
    return bool(
        scalar(
            conn,
            "select 1 from information_schema.tables where table_schema=%s and table_name=%s",
            (schema, table),
        )
    )


def database_exists(dsn: str, db_name: str) -> bool:
    with connect(dsn, autocommit=True) as conn:
        return bool(scalar(conn, "select 1 from pg_database where datname = %s", (db_name,)))
