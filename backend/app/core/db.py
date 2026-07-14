"""
PostgreSQL Database Layer
Uses asyncpg connection pooling for PostgreSQL.

Usage:
    from app.core.db import get_db, Database

    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
"""
import os
import json
import logging
import asyncio
import uuid
import asyncpg
from typing import Optional, Any, List, Dict
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# Sentinel used for `app.current_tenant_id` when there's no real tenant in
# scope (internal/system callers, or RLS-bypass paths). RLS policies cast
# this GUC to ::uuid, so it must be a syntactically valid UUID — never ''.
# Mirrors `app.core.db_utils._NIL_UUID`.
_NIL_TENANT_UUID = "00000000-0000-0000-0000-000000000000"


def _validate_tenant_uuid(value: str) -> str:
    """Raise ValueError if ``value`` isn't a parseable UUID.

    Guards the `set_config()` call below — a malformed tenant id would
    otherwise reach Postgres as an opaque string and only fail later
    (confusingly) at the first RLS policy that casts it to ::uuid.
    """
    uuid.UUID(value)
    return value


async def _apply_rls_context(conn: asyncpg.Connection, tenant_id, bypass_rls: bool) -> None:
    """Set the `app.current_tenant_id` / `app.bypass_rls` GUCs for the
    caller's statement(s).

    Must be called *inside* an open transaction (`conn.transaction()`) —
    `SET LOCAL` / the transaction-local form of `set_config(..., true)`
    both silently no-op outside one, which is the bug this helper fixes.

    Uses `set_config()` with a bound parameter instead of a string-built
    `SET LOCAL app.x = '...'` so a tenant id can never be interpreted as
    SQL syntax; the value is validated as a UUID first regardless, since
    RLS policies cast it to ::uuid.
    """
    if bypass_rls:
        await conn.execute("SELECT set_config('app.bypass_rls', 'true', true)")
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)", _NIL_TENANT_UUID
        )
    elif tenant_id:
        _validate_tenant_uuid(str(tenant_id))
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)", str(tenant_id)
        )
        await conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")
    else:
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)", _NIL_TENANT_UUID
        )
        await conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")

# Bounded wait for a connection when the pool is exhausted. Without this,
# `pool.acquire()` blocks forever — a saturated pool (e.g. a slow query
# holding every connection, or the DB itself wedged) hangs every caller
# indefinitely instead of failing fast. Configurable so ops can tune it;
# 10s is a guess-but-reasonable default for interactive request paths.
_ACQUIRE_TIMEOUT_S = float(os.getenv("PG_POOL_ACQUIRE_TIMEOUT", "10"))


class DatabasePoolTimeoutError(RuntimeError):
    """Raised when no pooled connection became available within the acquire timeout.

    Callers (error handlers) should map this to HTTP 503 — it signals
    the DB/pool is saturated, not a client error.
    """


_pool: Optional[asyncpg.Pool] = None
# Phase 2.3 — optional read-replica pool. Populated when
# READ_DATABASE_URL is set; falls back to the primary pool when unset
# so single-DB deploys behave exactly as before.
_read_pool: Optional[asyncpg.Pool] = None


def _pool_kwargs() -> dict:
    """Shared pool settings.

    `statement_cache_size=0` is mandatory when the pool talks to
    PgBouncer in transaction-pooling mode — server-side prepared
    statements aren't allowed there. asyncpg silently breaks
    otherwise. Off by default so direct-Postgres deploys (dev,
    test) get full prepared-statement performance; flip via
    `PG_STATEMENT_CACHE_SIZE=0` when DATABASE_URL points at PgBouncer.
    """
    cache = os.getenv("PG_STATEMENT_CACHE_SIZE")
    extras: dict = {}
    if cache is not None:
        try:
            extras["statement_cache_size"] = int(cache)
        except ValueError:
            logger.warning("invalid PG_STATEMENT_CACHE_SIZE=%r — ignoring", cache)
    return extras


async def _register_jsonb_codecs(conn: asyncpg.Connection) -> None:
    """Pool ``init`` hook: decode jsonb/json columns to dict/list on read.

    Root-cause fix — raw asyncpg otherwise hands back JSONB/JSON columns as
    JSON *strings*, unlike the old blocking postgres_adapter which decoded
    them. That mismatch caused a production incident (campaign identity
    lost) plus several latent AttributeError/500 sites that did dict-style
    access on what they assumed was already a dict.

    asyncpg's pool ``init=`` callback runs exactly once per physical
    connection, at the moment the pool creates it — not on every
    `acquire()` — so this can't double-register on a connection that's
    simply being checked out again. ``set_type_codec`` itself is also
    idempotent (re-registering the same codec just overwrites it), so even
    a defensive re-call is harmless.

    ``format='text'`` is required for jsonb: asyncpg's binary wire format
    prefixes jsonb with a 1-byte version marker that a plain
    ``json.loads`` decoder can't handle. ``json`` has no such prefix but
    is set the same way for symmetry.
    """
    # Pass-through-if-string encoder. The whole codebase's jsonb write sites
    # historically pass a pre-serialized JSON *string* (``json.dumps(payload)``)
    # as the bind parameter. A naive ``encoder=json.dumps`` would serialize that
    # string a SECOND time — the column would store a quoted JSON string
    # (``"{\"k\": 1}"``) instead of an object, silently corrupting every write.
    # Passing the value through untouched when it's already a ``str`` keeps those
    # existing string-writes correct, while ``json.dumps`` still handles the
    # newer sites that bind a raw dict/list. Both write styles now land as proper
    # jsonb, so no write site needs to change. ``default=str`` mirrors the
    # serialization the old ``postgres_adapter._coerce_value`` applied to jsonb
    # dicts (datetime/UUID -> str); without it a raw-dict write carrying such a
    # value — which the adapter now routes here instead of pre-serializing —
    # would raise TypeError.
    encoder = lambda v: v if isinstance(v, str) else json.dumps(v, default=str)
    for typename in ("jsonb", "json"):
        await conn.set_type_codec(
            typename,
            schema="pg_catalog",
            encoder=encoder,
            decoder=json.loads,
            format="text",
        )


async def init_db_pool() -> asyncpg.Pool:
    """Create the asyncpg connection pool(s). Called once at startup.

    Always creates the primary pool. When ``READ_DATABASE_URL`` is set,
    also creates a separate pool for read-only queries; campaign list /
    detail reads route there to keep the primary's connection budget for
    write-heavy work. When unset, the read pool aliases the primary so
    callers don't branch on availability.
    """
    global _pool, _read_pool
    dsn = os.getenv(
        "DATABASE_URL",
        "postgresql://talkyai:talkyai_secret@localhost:5432/talkyai"
    )
    min_size = int(os.getenv("PG_POOL_MIN_SIZE", "5"))
    max_size = int(os.getenv("PG_POOL_MAX_SIZE", "20"))
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=60,
        server_settings={"application_name": "talkyai-backend"},
        init=_register_jsonb_codecs,
        **_pool_kwargs(),
    )
    logger.info(
        "PostgreSQL primary pool initialized min=%d max=%d", min_size, max_size,
    )

    read_dsn = os.getenv("READ_DATABASE_URL")
    if read_dsn:
        ro_min = int(os.getenv("PG_READ_POOL_MIN_SIZE", "2"))
        ro_max = int(os.getenv("PG_READ_POOL_MAX_SIZE", "15"))
        _read_pool = await asyncpg.create_pool(
            dsn=read_dsn,
            min_size=ro_min,
            max_size=ro_max,
            command_timeout=60,
            server_settings={"application_name": "talkyai-backend-ro"},
            init=_register_jsonb_codecs,
            **_pool_kwargs(),
        )
        logger.info(
            "PostgreSQL read-replica pool initialized min=%d max=%d", ro_min, ro_max,
        )
    else:
        # Aliasing the primary keeps `get_read_pool()` always-callable;
        # callers never need a None-check.
        _read_pool = _pool

    return _pool


async def close_db_pool() -> None:
    """Close the connection pool(s). Called at shutdown."""
    global _pool, _read_pool
    # Close the read pool first so anything pending on it drains while
    # the primary is still up (avoids 'pool closed' surprises on a
    # late-arriving read).
    if _read_pool is not None and _read_pool is not _pool:
        try:
            await _read_pool.close()
        except Exception as exc:
            logger.warning("read pool close raised: %s", exc)
    _read_pool = None

    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pools closed")


def get_pool() -> asyncpg.Pool:
    """Return the active primary pool (raises if not initialized)."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_db_pool() first.")
    return _pool


def get_read_pool() -> asyncpg.Pool:
    """Return the read-only pool. Aliases primary when no replica is configured."""
    if _read_pool is None:
        raise RuntimeError("Database pool not initialized. Call init_db_pool() first.")
    return _read_pool


@asynccontextmanager
async def get_read_db():
    """
    Read-only context manager — same RLS handling as ``get_db()`` but
    leases a connection from the read-replica pool. Use for stateless
    list / detail endpoints (campaigns list, contacts search) so the
    primary's connection budget stays available for transactional work.

    Usage:
        async with get_read_db() as conn:
            rows = await conn.fetch("SELECT * FROM campaigns WHERE ...")
    """
    pool = get_read_pool()
    try:
        conn = await pool.acquire(timeout=_ACQUIRE_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.error(
            "db_pool_acquire_timeout pool=read timeout_s=%.1f — pool exhausted or DB unresponsive",
            _ACQUIRE_TIMEOUT_S,
        )
        raise DatabasePoolTimeoutError(
            f"Timed out after {_ACQUIRE_TIMEOUT_S}s waiting for a read DB connection"
        ) from None
    try:
        from app.core.security.tenant_isolation import (
            get_current_tenant_id, get_bypass_rls,
        )

        tenant_id = get_current_tenant_id()
        bypass_rls = get_bypass_rls()

        # `SET LOCAL` / `set_config(..., true)` only take effect inside an
        # open transaction — outside one Postgres discards the setting
        # (with a warning), so the GUCs never actually reached the query
        # before this wrap. `readonly=True` documents (and, once a real
        # RLS-enforcing role is in front of this pool, enforces) that this
        # path is for read-only work; it's a no-op against today's
        # superuser pool. Every current caller issues exactly one
        # statement, so committing on the way out is equivalent to the
        # previous un-transacted single-statement behavior.
        async with conn.transaction(readonly=True):
            await _apply_rls_context(conn, tenant_id, bypass_rls)
            yield conn
    finally:
        await pool.release(conn)


@asynccontextmanager
async def get_db():
    """
    Async context manager for a single DB connection from the pool.
    Sets the RLS context (tenant_id and bypass_rls) automatically.

    Usage:
        async with get_db() as conn:
            result = await conn.fetch("SELECT * FROM plans")
    """
    pool = get_pool()
    try:
        conn = await pool.acquire(timeout=_ACQUIRE_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.error(
            "db_pool_acquire_timeout pool=primary timeout_s=%.1f — pool exhausted or DB unresponsive",
            _ACQUIRE_TIMEOUT_S,
        )
        raise DatabasePoolTimeoutError(
            f"Timed out after {_ACQUIRE_TIMEOUT_S}s waiting for a DB connection"
        ) from None
    try:
        from app.core.security.tenant_isolation import get_current_tenant_id, get_bypass_rls

        tenant_id = get_current_tenant_id()
        bypass_rls = get_bypass_rls()

        # See get_read_db()'s comment: the GUCs below only stick if set
        # inside an open transaction, so the RLS context is applied here
        # and the caller's statement(s) run inside the same transaction.
        # Every current caller issues exactly one statement, so committing
        # on the way out (normal exit) or rolling back (exception) is
        # equivalent to the previous un-transacted single-statement
        # behavior — no caller relies on a partial multi-statement write
        # surviving an error.
        async with conn.transaction():
            await _apply_rls_context(conn, tenant_id, bypass_rls)
            yield conn
    finally:
        await pool.release(conn)


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
