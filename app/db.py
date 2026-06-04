"""Connection pool + small query helpers (psycopg 3, sync)."""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SGT = ZoneInfo("Asia/Singapore")


def normalize_dsn(url: str) -> str:
    """psycopg wants a plain ``postgresql://`` DSN.

    The deployment ``.env`` may carry a SQLAlchemy-style driver suffix
    (``postgresql+asyncpg://``); strip any ``+driver`` from the scheme so the
    same URL works for both psql and psycopg.
    """
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url.strip())


DATABASE_URL = normalize_dsn(os.environ["DATABASE_URL"])

# Opened lazily in the app lifespan so import never blocks on the network.
pool: ConnectionPool | None = None


def open_pool() -> ConnectionPool:
    global pool
    if pool is None:
        pool = ConnectionPool(
            conninfo=DATABASE_URL,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return pool


def close_pool() -> None:
    global pool
    if pool is not None:
        pool.close()
        pool = None


@contextmanager
def get_conn():
    assert pool is not None, "connection pool not opened"
    with pool.connection() as conn:
        yield conn


def query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql: str, params: tuple | dict | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | dict | None = None) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount
