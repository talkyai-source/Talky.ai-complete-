"""Ownership + lifecycle for per-tenant voice clones (the cloned_voices table).

ElevenLabs clones all live in one shared account, so this table is what
makes them multi-tenant safe:

  * the voice catalog shows a tenant only ITS clones (plus shared library
    voices) — see ``owned_voice_ids`` / ``all_platform_voice_ids`` used by
    the catalog filter;
  * a per-tenant cap (``MAX_PER_TENANT``) protects the shared EL voice-slot
    pool from any single tenant.

DB access uses the asyncpg pool (``db_client.pool``) to match the rest of
the ai_options module. Tenant isolation is enforced with explicit
``tenant_id`` filters on every read/write (RLS is dormant — see project
notes), so nothing here ever trusts the database to scope rows.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Per-tenant clone ceiling. Protects the single shared ElevenLabs account's
# voice-slot limit from one tenant consuming them all.
MAX_PER_TENANT = int(os.getenv("VOICE_CLONE_MAX_PER_TENANT", "5"))


def _row_to_dict(row: Any) -> dict:
    return {
        "id": str(row["id"]),
        "voice_id": row["voice_id"],
        "name": row["name"],
        "provider": row["provider"],
        "created_by": row["created_by"],
        "consent_at": row["consent_at"].isoformat() if row["consent_at"] else None,
        "status": row["status"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


async def count_for_tenant(pool, tenant_id: str) -> int:
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT COUNT(*) FROM cloned_voices WHERE tenant_id = $1", tenant_id,
        )
    return int(val or 0)


async def list_for_tenant(pool, tenant_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, voice_id, name, provider, created_by, consent_at, status, created_at "
            "FROM cloned_voices WHERE tenant_id = $1 ORDER BY created_at DESC",
            tenant_id,
        )
    return [_row_to_dict(r) for r in rows]


async def record_clone(
    pool,
    *,
    tenant_id: str,
    voice_id: str,
    name: str,
    created_by: Optional[str],
    consent_at: Optional[datetime] = None,
) -> dict:
    """Insert the ownership row for a freshly created clone."""
    consent = consent_at or datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO cloned_voices (tenant_id, voice_id, name, created_by, consent_at)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, voice_id, name, provider, created_by, consent_at, status, created_at
            """,
            tenant_id, voice_id, name, created_by, consent,
        )
    return _row_to_dict(row)


async def get_owned(pool, tenant_id: str, clone_id: str) -> Optional[dict]:
    """Fetch one clone, scoped to the tenant (returns None if not theirs)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, voice_id, name, provider, created_by, consent_at, status, created_at "
            "FROM cloned_voices WHERE tenant_id = $1 AND id = $2",
            tenant_id, clone_id,
        )
    return _row_to_dict(row) if row else None


async def delete_owned(pool, tenant_id: str, clone_id: str) -> bool:
    """Delete a tenant's clone row. Returns True if a row was removed."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM cloned_voices WHERE tenant_id = $1 AND id = $2",
            tenant_id, clone_id,
        )
    return isinstance(result, str) and result.endswith(" 1")


async def owned_voice_ids(pool, tenant_id: str) -> set[str]:
    """EL voice_ids of clones owned by this tenant (kept in their catalog)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT voice_id FROM cloned_voices WHERE tenant_id = $1", tenant_id,
        )
    return {r["voice_id"] for r in rows}


async def all_platform_voice_ids(pool) -> set[str]:
    """Every platform-created clone voice_id, across all tenants. The catalog
    drops any of these the current tenant doesn't own, so tenant A never sees
    tenant B's clone. Library/premade EL voices aren't in this set, so they
    stay visible to everyone."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT voice_id FROM cloned_voices")
    return {r["voice_id"] for r in rows}
