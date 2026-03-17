"""
Tenant RLS context helpers.

These helpers set per-connection PostgreSQL runtime settings used by RLS
policies to enforce tenant isolation at the database layer.
"""

from __future__ import annotations

import asyncpg


async def apply_tenant_rls_context(
    conn: asyncpg.Connection,
    tenant_id: str,
    user_id: str | None = None,
    request_id: str | None = None,
) -> None:
    if not tenant_id:
        raise ValueError("tenant_id is required to apply RLS context")
    await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id))
    await conn.execute(
        "SELECT set_config('app.current_user_id', $1, false)",
        str(user_id) if user_id else "",
    )
    normalized_request_id = str(request_id).strip()[:128] if request_id else ""
    await conn.execute(
        "SELECT set_config('app.current_request_id', $1, false)",
        normalized_request_id,
    )
