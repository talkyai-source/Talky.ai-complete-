"""Tell "another tenant's record" (403) apart from "no such record" (404).

Every read first filters ``tenant_id`` explicitly. Only when that finds
nothing does ``classify_missing`` look up whether the id exists at all, in a
savepoint with platform context, returning nothing but a yes/no.
"""
from __future__ import annotations

from app.domain.services.support.errors import SupportForbidden, SupportNotFound

_PROBE_TABLES = frozenset({
    "support_tickets", "support_conversations", "support_ticket_attachments",
    "campaigns", "calls",
})


async def record_exists_anywhere(conn, table: str, record_id) -> bool:
    if table not in _PROBE_TABLES:
        raise ValueError(f"no existence probe for {table}")
    async with conn.transaction():
        previous = await conn.fetchval("SELECT current_setting('app.bypass_rls', true)")
        await conn.execute("SELECT set_config('app.bypass_rls', 'on', true)")
        exists = await conn.fetchval(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE id = $1)", record_id)
        await conn.execute("SELECT set_config('app.bypass_rls', $1, true)", previous or "")
    return bool(exists)


async def classify_missing(conn, table: str, record_id) -> None:
    """Raise 403 when the id belongs to someone else, else 404."""
    if await record_exists_anywhere(conn, table, record_id):
        raise SupportForbidden()
    raise SupportNotFound()
