"""Shared DB helpers for services that hit RLS-protected tables.

Postgres RLS policies on tenant-scoped tables look up the current tenant
from ``current_setting('app.current_tenant_id')``. Web requests get this
set by ``TenantMiddleware`` after JWT validation. But service classes
called from workers, schedulers, or any non-request context bypass that
middleware entirely — querying through a raw ``pool.acquire()`` then
returns zero rows (the policy filters everything out) and the caller
sees a misleading "not found" or "unauthorized" result.

``acquire_with_tenant()`` wraps that pattern in one obvious primitive:
acquire a connection, open a transaction so ``SET LOCAL`` lives until
commit, set the tenant GUC (or bypass RLS entirely), then yield the
connection. Callers should reach for this instead of re-deriving the
``SET LOCAL`` dance every time.

Example::

    async with acquire_with_tenant(self._db_pool, tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM tenant_phone_numbers WHERE e164 = $1",
            e164,
        )

For backend-internal jobs that legitimately need cross-tenant reads
(dialer worker scanning all active campaigns), pass ``tenant_id=None``
and the helper opens the connection with ``app.bypass_rls = 'on'``
plus a nil-UUID sentinel — the same pattern the dialer already uses.
"""

from __future__ import annotations

import logging
import asyncio
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncIterator, Optional

import asyncpg

from app.core import db

logger = logging.getLogger(__name__)


# Used when bypass_rls is enabled so the policy's UUID cast doesn't throw
# if a query path still evaluates `current_setting('app.current_tenant_id')`.
_NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _validate_uuid(value: str) -> str:
    """Raise ValueError if ``value`` isn't a parseable UUID.

    SET LOCAL doesn't accept parameter binding, so the tenant id is
    interpolated into the SQL string. Validating up front prevents any
    chance of a malformed identifier producing a syntax error inside
    the transaction (which would also abort the caller's query).
    """
    uuid.UUID(value)  # raises ValueError for non-UUIDs
    return value


@asynccontextmanager
async def acquire_connection(
    pool: asyncpg.Pool,
    *,
    timeout: Optional[float] = None,
) -> AsyncIterator[asyncpg.Connection]:
    """Bound acquisition and release the lease; set no transaction/RLS context.

    Omitted/None uses PG_POOL_ACQUIRE_TIMEOUT. Only acquisition expiry is
    translated to DatabasePoolTimeoutError; caller errors and cancellation
    retain their identity. Callers own their existing transaction policy.
    """
    acquire_timeout = db._ACQUIRE_TIMEOUT_S if timeout is None else timeout
    async with AsyncExitStack() as stack:
        try:
            # Enter asyncpg's own context so its cancellation/release protocol
            # remains intact. Do not make a short acquisition budget become
            # the connection reset/release budget as pool.acquire(timeout=...)
            # otherwise does. ExitStack releases after the caller below.
            async with asyncio.timeout(acquire_timeout):
                conn = await stack.enter_async_context(pool.acquire())
        except asyncio.TimeoutError:
            logger.warning("db_pool_acquire_timeout pool=shared timeout_s=%.3f", acquire_timeout)
            raise db.DatabasePoolTimeoutError(
                f"Timed out after {acquire_timeout}s waiting for a DB connection"
            ) from None
        yield conn


@asynccontextmanager
async def acquire_with_tenant(
    pool: asyncpg.Pool,
    tenant_id: Optional[str],
    *,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None,
    timeout: Optional[float] = None,
) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection with the right RLS context already set.

    * ``tenant_id`` provided → ``SET LOCAL app.current_tenant_id = <id>``.
    * ``tenant_id`` is ``None`` → ``SET LOCAL app.bypass_rls = 'on'``
      plus nil-UUID sentinel; use for genuinely cross-tenant reads
      (admin tooling, workers).
    * ``user_id``/``request_id`` are optional audit context. They are set in
      the same transaction and therefore cannot leak to the next pool user.

    SET LOCAL inside the wrapping transaction guarantees the GUC is
    dropped when the connection is returned to the pool, so a later
    consumer of the same connection never inherits stale tenant context.

    ``timeout`` bounds only pool acquisition; omitted/None uses
    PG_POOL_ACQUIRE_TIMEOUT (10 seconds by default). Expiry raises
    DatabasePoolTimeoutError, also an asyncio.TimeoutError, for the existing
    HTTP 503 handler and short-budget callers. The caller's transaction and
    connection cleanup are outside that acquisition deadline. Transaction-
    local statement/lock limits are applied before yielding the connection.
    """
    limits = db._transaction_timeout_settings()
    async with acquire_connection(pool, timeout=timeout) as conn:
        async with conn.transaction():
            await db._apply_transaction_timeouts(conn, limits)
            if tenant_id is not None:
                _validate_uuid(str(tenant_id))
                await conn.execute(
                    f"SET LOCAL app.current_tenant_id = '{tenant_id}'"
                )
            else:
                await conn.execute("SET LOCAL app.bypass_rls = 'on'")
                await conn.execute(
                    f"SET LOCAL app.current_tenant_id = '{_NIL_UUID}'"
                )
            if user_id is not None:
                await conn.execute(
                    "SELECT set_config('app.current_user_id', $1, true)",
                    str(user_id),
                )
            if request_id is not None:
                await conn.execute(
                    "SELECT set_config('app.current_request_id', $1, true)",
                    str(request_id).strip()[:128],
                )
            yield conn
