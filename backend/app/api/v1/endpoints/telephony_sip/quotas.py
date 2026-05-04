"""GET /telephony/sip/quotas/status — read-only quota counters."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Request

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.tenant_rls import apply_tenant_rls_context

from ._shared import _get_rate_limiter, _require_tenant
from .schemas import TelephonyQuotaStatusItem, TelephonyQuotaStatusResponse

router = APIRouter(tags=["Telephony SIP"])


@router.get("/quotas/status", response_model=TelephonyQuotaStatusResponse)
async def get_telephony_quota_status(
    request: Request,
    policy_scope: str = "api_mutation",
    metric_key: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    limiter = _get_rate_limiter()
    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        statuses = await limiter.get_status(
            conn=conn,
            tenant_id=current_user.tenant_id,
            policy_scope=policy_scope,
            metric_key=metric_key,
        )

    return TelephonyQuotaStatusResponse(
        tenant_id=current_user.tenant_id,
        policy_scope=policy_scope,
        metrics=[TelephonyQuotaStatusItem(**status.to_dict()) for status in statuses],
        generated_at=datetime.now(timezone.utc),
    )
