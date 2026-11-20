"""Bulk read of saved call workflows for one tenant (history export)."""
from __future__ import annotations

from typing import Iterable
from uuid import UUID

from app.core.db_utils import acquire_with_tenant
from app.domain.services.call_workflow_service import _public

# The same columns the single read selects (call_workflow_service._COLUMNS);
# a test keeps the two equal. Spelled out here so the statement below is a
# plain literal the tenant-SQL scanner can read.
_COLUMNS = """tenant_id, call_id, lead_type, notes, contact, interest,
              next_step, completed, updated_by, created_at, updated_at"""


async def read_workflows(pool, *, tenant_id: str, call_ids: Iterable[UUID]) -> dict[str, dict]:
    """Return {call id: public workflow} for calls that have a saved workflow.

    The tenant predicate is explicit: the database role may bypass row-level
    security. Ids of other tenants or of no call are simply absent.
    """
    tenant = UUID(str(tenant_id))
    ids = list(dict.fromkeys(UUID(str(value)) for value in call_ids))
    if not ids:
        return {}
    async with acquire_with_tenant(pool, str(tenant)) as conn:
        rows = await conn.fetch(
            f"SELECT {_COLUMNS} FROM call_workflows WHERE tenant_id = $1 AND call_id = ANY($2::uuid[])",
            tenant, ids,
        )
    return {str(row["call_id"]): _public(row) for row in rows}
