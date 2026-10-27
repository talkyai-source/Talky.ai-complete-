"""Read-only partner directory for the existing Admin formal-hold selector."""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.dependencies import CurrentUser, get_db_client, require_platform_admin
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client

from ._serialization import AdminResponseModel

logger = logging.getLogger(__name__)
router = APIRouter()
PartnerStatus = Literal["active", "suspended", "pending_deletion", "unknown"]
_READ_TIMEOUT_SECONDS = 5


class PartnerListItem(AdminResponseModel):
    id: str
    name: str
    status: PartnerStatus
    suspended_at: str | None = None
    tenant_count: int
    updated_at: str | None = None


class PartnerListResponse(AdminResponseModel):
    items: list[PartnerListItem]
    total: int
    page: int
    page_size: int


@router.get("/partners", response_model=PartnerListResponse)
async def list_partners(
    page: int = Query(default=1, ge=1, le=2_147_483_647),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None, max_length=255),
    status: PartnerStatus | None = None,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
):
    """List current partner rows; ordinary status is separate from formal holds.

    Count and page share one read-only snapshot. Paging across requests remains
    live. This route does not grant ordinary partner mutation authority.
    """
    values: list = []
    predicates: list[str] = []
    if q and q.strip():
        # Treat operator input as a literal substring, not LIKE wildcards.
        search = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        values.append("%" + search + "%")
        predicates.append(f"(p.company_name ILIKE ${len(values)} OR p.id::text ILIKE ${len(values)})")
    if status is not None:
        values.append(status)
        predicates.append(f"COALESCE(p.status, 'unknown') = ${len(values)}")
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    try:
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            # Exact current platform-admin authentication precedes deliberate
            # cross-tenant scope. Transaction-local GUCs cannot leak to borrowers.
            async with acquire_with_tenant(db_client.pool, None) as conn:
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                total = await conn.fetchval("SELECT count(*) FROM white_label_partners p" + where, *values)
                rows = await conn.fetch(
                    """SELECT p.id,p.company_name AS name,COALESCE(p.status,'unknown') AS status,
                              p.suspended_at,p.updated_at,
                              (SELECT count(*) FROM tenants t WHERE t.white_label_partner_id=p.id) AS tenant_count
                       FROM white_label_partners p""" + where
                    + f" ORDER BY p.company_name,p.id LIMIT ${len(values)+1} OFFSET ${len(values)+2}",
                    *values, page_size, (page - 1) * page_size,
                )
        return PartnerListResponse(
            items=[PartnerListItem.model_validate(dict(row)) for row in rows],
            total=total, page=page, page_size=page_size,
        )
    except HTTPException:
        raise
    except Exception as exc:
        # Cancellation is a BaseException and propagates. No raw SQL or stored
        # partner configuration is exposed in the diagnostic or client error.
        logger.warning("Partner directory unavailable type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail={
            "code": "partner_directory_unavailable",
            "message": "Partner data is temporarily unavailable.",
        }) from exc
