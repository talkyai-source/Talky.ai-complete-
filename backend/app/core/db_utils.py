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
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import asyncpg

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
async def acquire_with_tenant(
    pool: asyncpg.Pool,
    tenant_id: Optional[str],
    *,
    timeout: Optional[float] = None,
) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection with the right RLS context already set.

    * ``tenant_id`` provided → ``SET LOCAL app.current_tenant_id = <id>``.
    * ``tenant_id`` is ``None`` → ``SET LOCAL app.bypass_rls = 'on'``
      plus nil-UUID sentinel; use for genuinely cross-tenant reads
      (admin tooling, workers).

    SET LOCAL inside the wrapping transaction guarantees the GUC is
    dropped when the connection is returned to the pool, so a later
    consumer of the same connection never inherits stale tenant context.

    ``timeout`` (2026-07-08): optional bounded wait for a pool slot,
    mirroring ``app.core.db.get_db()``'s acquire timeout. Defaults to
    ``None`` (asyncpg's own default — wait indefinitely), so existing
    callers are unaffected. Raises ``asyncio.TimeoutError`` on expiry —
    callers that want the saturated-pool case to degrade gracefully
    (e.g. a request/teardown path) should catch it and fail soft rather
    than propagate a hang.
    """
    # Only pass `timeout` through when the caller asked for one — several
    # unit-test fake pools implement a bare `acquire()` with no kwargs, and
    # asyncpg's own default (wait indefinitely) is unaffected by omitting it.
    acquire_cm = pool.acquire(timeout=timeout) if timeout is not None else pool.acquire()
    async with acquire_cm as conn:
        async with conn.transaction():
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
            yield conn
