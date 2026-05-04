"""GET /telephony/sip/runtime/versions — list runtime policy version history."""
from __future__ import annotations

from typing import List

import asyncpg
from fastapi import APIRouter, Depends, Request

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.tenant_rls import apply_tenant_rls_context

from ._shared import _require_tenant
from .schemas import RuntimeVersionResponse

router = APIRouter(tags=["Telephony SIP Runtime"])


@router.get("/versions", response_model=List[RuntimeVersionResponse])
async def list_runtime_policy_versions(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        rows = await conn.fetch(
            """
            SELECT
                id,
                tenant_id,
                policy_version,
                source_hash,
                schema_version,
                build_status,
                is_active,
                is_last_good,
                validation_report,
                created_at,
                updated_at,
                activated_at
            FROM tenant_runtime_policy_versions
            WHERE tenant_id = $1
            ORDER BY policy_version DESC
            """,
            current_user.tenant_id,
        )
    return [RuntimeVersionResponse(**dict(row)) for row in rows]
