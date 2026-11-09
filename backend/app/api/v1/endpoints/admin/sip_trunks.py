"""Platform-admin SIP trunk directory across tenants (G4). Read-only.

Credentials never leave the table: the query reads only whether a password
is stored, not the username or the encrypted password.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.dependencies import CurrentUser, get_db_client, require_platform_admin
from app.api.v1.validation import pagination_offset
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client

from ._serialization import AdminResponseModel

router = APIRouter()


class AdminSipTrunkItem(AdminResponseModel):
    id: str
    tenant_id: str
    tenant_name: Optional[str] = None
    trunk_name: str
    sip_domain: str
    port: int
    transport: str
    direction: str
    is_active: bool
    auth_configured: bool
    role: Optional[str] = None
    # Kept fresh by the trunk-status updater (registered/rejected/...).
    live_registration_status: Optional[str] = None
    live_status_detail: Optional[str] = None
    live_status_checked_at: Optional[str] = None
    last_tested_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AdminSipTrunkListResponse(AdminResponseModel):
    items: List[AdminSipTrunkItem]
    total: int
    page: int
    page_size: int


@router.get("/sip-trunks", response_model=AdminSipTrunkListResponse)
async def list_admin_sip_trunks(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant"),
    is_active: Optional[bool] = Query(None, description="Filter by the trunk's on/off switch"),
    registration: Optional[str] = Query(None, max_length=32, description="Filter by live registration state"),
    page: int = Query(1, ge=1, le=10001),
    page_size: int = Query(20, ge=1, le=100),
):
    """Every tenant's SIP trunks with their live registration state."""
    filters, args = ["s.deleted_at IS NULL"], []
    if tenant_id is not None:
        try:
            args.append(UUID(tenant_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid tenant ID") from exc
        filters.append(f"s.tenant_id=${len(args)}")
    if is_active is not None:
        args.append(is_active)
        filters.append(f"s.is_active=${len(args)}")
    if registration is not None:
        args.append(registration)
        filters.append(f"s.live_registration_status=${len(args)}")
    where = " WHERE " + " AND ".join(filters)
    offset = pagination_offset(page, page_size)
    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
            total = await conn.fetchval(
                "SELECT count(*) FROM tenant_sip_trunks s" + where, *args)
            rows = await conn.fetch(
                "SELECT s.id, s.tenant_id, t.business_name AS tenant_name, s.trunk_name, s.sip_domain,"
                " s.port, s.transport, s.direction, s.is_active,"
                " (s.auth_password_encrypted IS NOT NULL) AS auth_configured,"
                " s.metadata->>'role' AS role, s.live_registration_status, s.live_status_detail,"
                " s.live_status_checked_at, s.last_tested_at, s.created_at, s.updated_at"
                " FROM tenant_sip_trunks s JOIN tenants t ON t.id=s.tenant_id" + where +
                f" ORDER BY s.created_at DESC, s.id DESC LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
                *args, page_size, offset)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="SIP trunk list is temporarily unavailable") from exc
    return AdminSipTrunkListResponse(
        items=[AdminSipTrunkItem(**dict(row)) for row in rows],
        total=int(total or 0), page=page, page_size=page_size,
    )
