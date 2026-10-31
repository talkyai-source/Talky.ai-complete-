"""Tenant bootstrap for WebSocket endpoints.

A WebSocket never passes through ``TenantMiddleware``, so when the handshake is
verified there is no tenant in the contextvar yet. The compatibility ``.table()``
adapter installs the **nil** tenant in that state, and since the app role lost
BYPASSRLS (migration 0038 put ``user_profiles`` under forced RLS) that lookup
returns no row at all. Every assistant socket then closed with "User profile not
found." on the same second it connected — silently, because the miss was not
logged.

The one correct way to resolve a signed JWT subject before a tenant is known is
the pooled path REST authentication uses: an explicit subject predicate on a
connection that carries the user audit context and ``app.bypass_rls``. Callers
install the returned tenant into the contextvar immediately afterwards.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def resolve_user_tenant(db_pool: Any, user_id: str) -> Optional[str]:
    """Return the tenant id for ``user_id`` or ``None`` when no profile exists.

    Database failures propagate so an endpoint can tell an auth miss from a
    backend outage instead of collapsing both into "profile not found".
    """
    from app.core.db_utils import acquire_with_tenant

    async with acquire_with_tenant(db_pool, None, user_id=user_id) as conn:
        row = await conn.fetchrow(
            "SELECT tenant_id FROM user_profiles WHERE id = $1",
            user_id,
        )
    tenant_id = row.get("tenant_id") if row else None
    return str(tenant_id) if tenant_id else None
