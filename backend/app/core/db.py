"""
PostgreSQL Database Layer
Uses asyncpg connection pooling for PostgreSQL.

Usage:
    from app.core.db import get_db, Database

    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
"""
import os
import logging
import asyncpg
from typing import Optional, Any, List, Dict
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def init_db_pool() -> asyncpg.Pool:
    """Create the asyncpg connection pool. Called once at startup."""
    global _pool
    dsn = os.getenv(
        "DATABASE_URL",
        "postgresql://talkyai:talkyai_secret@localhost:5432/talkyai"
    )
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=20,
        command_timeout=60,
        server_settings={"application_name": "talkyai-backend"},
    )
    logger.info("PostgreSQL connection pool initialized")
    return _pool


async def close_db_pool() -> None:
    """Close the connection pool. Called at shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed")


def get_pool() -> asyncpg.Pool:
    """Return the active pool (raises if not initialized)."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_db_pool() first.")
    return _pool


@asynccontextmanager
async def get_db():
    """
    Async context manager for a single DB connection from the pool.

    Usage:
        async with get_db() as conn:
            result = await conn.fetch("SELECT * FROM plans")
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


class Database:
    """
    High-level database helper that mimics the adapter table-builder API
    but uses raw asyncpg under the hood.

    Provides simple CRUD helpers so existing service code needs minimal changes.
    """

    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    async def fetch_all(self, query: str, *args) -> List[Dict]:
        rows = await self._conn.fetch(query, *args)
        return [dict(r) for r in rows]

    async def fetch_one(self, query: str, *args) -> Optional[Dict]:
        row = await self._conn.fetchrow(query, *args)
        return dict(row) if row else None

    async def execute(self, query: str, *args) -> str:
        return await self._conn.execute(query, *args)

    async def fetch_val(self, query: str, *args) -> Any:
        return await self._conn.fetchval(query, *args)

    # ------------------------------------------------------------------
    # Table helpers (CRUD)
    # ------------------------------------------------------------------

    async def select(self, table: str, columns: str = "*", where: Optional[str] = None,
                     args: Optional[list] = None, order_by: Optional[str] = None,
                     limit: Optional[int] = None) -> List[Dict]:
        query = f"SELECT {columns} FROM {table}"
        if where:
            query += f" WHERE {where}"
        if order_by:
            query += f" ORDER BY {order_by}"
        if limit:
            query += f" LIMIT {limit}"
        rows = await self._conn.fetch(query, *(args or []))
        return [dict(r) for r in rows]

    async def select_one(self, table: str, columns: str = "*", where: Optional[str] = None,
                         args: Optional[list] = None) -> Optional[Dict]:
        query = f"SELECT {columns} FROM {table}"
        if where:
            query += f" WHERE {where}"
        query += " LIMIT 1"
        row = await self._conn.fetchrow(query, *(args or []))
        return dict(row) if row else None

    async def insert(self, table: str, data: Dict, returning: str = "*") -> Optional[Dict]:
        columns = list(data.keys())
        values = list(data.values())
        placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))
        col_str = ", ".join(columns)
        query = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) RETURNING {returning}"
        row = await self._conn.fetchrow(query, *values)
        return dict(row) if row else None

    async def insert_many(self, table: str, rows: List[Dict]) -> List[Dict]:
        if not rows:
            return []
        columns = list(rows[0].keys())
        col_str = ", ".join(columns)
        results = []
        for data in rows:
            values = [data[c] for c in columns]
            placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))
            query = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) RETURNING *"
            row = await self._conn.fetchrow(query, *values)
            if row:
                results.append(dict(row))
        return results

    async def update(self, table: str, data: Dict, where: str, args: list,
                     returning: str = "*") -> List[Dict]:
        set_parts = []
        values = []
        for i, (k, v) in enumerate(data.items()):
            set_parts.append(f"{k} = ${i+1}")
            values.append(v)
        # Shift where-clause arg indices
        offset = len(values)
        # Replace $1, $2... in where clause with offset indices
        shifted_where = where
        for j in range(len(args), 0, -1):
            shifted_where = shifted_where.replace(f"${j}", f"${j + offset}")
        values.extend(args)
        set_str = ", ".join(set_parts)
        query = f"UPDATE {table} SET {set_str} WHERE {shifted_where} RETURNING {returning}"
        rows = await self._conn.fetch(query, *values)
        return [dict(r) for r in rows]

    async def delete(self, table: str, where: str, args: list) -> int:
        query = f"DELETE FROM {table} WHERE {where}"
        result = await self._conn.execute(query, *args)
        # result is like "DELETE 3"
        return int(result.split()[-1])

    async def upsert(self, table: str, data: Dict, conflict_columns: List[str],
                     returning: str = "*") -> Optional[Dict]:
        columns = list(data.keys())
        values = list(data.values())
        placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))
        col_str = ", ".join(columns)
        conflict_str = ", ".join(conflict_columns)
        update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in conflict_columns)
        query = (
            f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_str}) DO UPDATE SET {update_str} "
            f"RETURNING {returning}"
        )
        row = await self._conn.fetchrow(query, *values)
        return dict(row) if row else None


@asynccontextmanager
async def get_database():
    """
    Async context manager that yields a Database helper instance.

    Usage:
        async with get_database() as db:
            plans = await db.select("plans")
    """
    async with get_db() as conn:
        yield Database(conn)
