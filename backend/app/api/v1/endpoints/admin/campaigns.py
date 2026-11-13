"""Platform-admin campaign directory across tenants (G3). Read-only."""
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.dependencies import CurrentUser, get_db_client, require_platform_admin
from app.api.v1.validation import pagination_offset
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client

from ._serialization import AdminResponseModel

router = APIRouter()


class AdminCampaignItem(AdminResponseModel):
    id: str
    tenant_id: str
    tenant_name: Optional[str] = None
    name: str
    status: str
    direction: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    started_at: Optional[str] = None
    contacts: int = 0
    calls: int = 0
    live_calls: int = 0


class AdminCampaignListResponse(AdminResponseModel):
    items: List[AdminCampaignItem]
    total: int
    page: int
    page_size: int


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/campaigns", response_model=AdminCampaignListResponse)
async def list_admin_campaigns(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant"),
    status: Optional[str] = Query(None, description="Filter by status; deleted campaigns only when asked"),
    direction: Optional[Literal["outbound", "inbound"]] = Query(None),
    search: Optional[str] = Query(None, max_length=120, description="Campaign or tenant name"),
    page: int = Query(1, ge=1, le=10001),
    page_size: int = Query(20, ge=1, le=100),
):
    """Every tenant's campaigns with their contacts, calls and live calls."""
    from app.api.v1.endpoints.telephony_bridge import CAMPAIGN_LIVE_CALL_STATUSES

    filters, args = [], []
    if tenant_id is not None:
        try:
            args.append(UUID(tenant_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid tenant ID") from exc
        filters.append(f"c.tenant_id=${len(args)}")
    if status is not None:
        args.append(status)
        filters.append(f"c.status=${len(args)}")
    else:
        filters.append("c.status<>'deleted'")
    if direction is not None:
        args.append(direction)
        filters.append(f"c.direction=${len(args)}")
    if search:
        args.append(_like(search))
        filters.append(f"(c.name ILIKE ${len(args)} ESCAPE '\\' OR t.business_name ILIKE ${len(args)} ESCAPE '\\')")
    where = " WHERE " + " AND ".join(filters)
    offset = pagination_offset(page, page_size)
    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
            total = await conn.fetchval(
                "SELECT count(*) FROM campaigns c JOIN tenants t ON t.id=c.tenant_id" + where, *args)
            rows = await conn.fetch(
                "SELECT c.id, c.tenant_id, t.business_name AS tenant_name, c.name, c.status, c.direction,"
                " c.created_at, c.updated_at, c.started_at,"
                " (SELECT count(*) FROM leads l WHERE l.campaign_id=c.id AND l.tenant_id=c.tenant_id"
                " AND l.status<>'deleted') AS contacts,"
                " (SELECT count(*) FROM calls k WHERE k.campaign_id=c.id AND k.tenant_id=c.tenant_id) AS calls,"
                " (SELECT count(*) FROM calls k WHERE k.campaign_id=c.id AND k.tenant_id=c.tenant_id"
                f" AND k.status=ANY(${len(args) + 1}::text[])) AS live_calls"
                " FROM campaigns c JOIN tenants t ON t.id=c.tenant_id" + where +
                f" ORDER BY c.created_at DESC NULLS LAST, c.id DESC LIMIT ${len(args) + 2} OFFSET ${len(args) + 3}",
                *args, list(CAMPAIGN_LIVE_CALL_STATUSES), page_size, offset)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Campaign list is temporarily unavailable") from exc
    return AdminCampaignListResponse(
        items=[AdminCampaignItem(**dict(row)) for row in rows],
        total=int(total or 0), page=page, page_size=page_size,
    )
