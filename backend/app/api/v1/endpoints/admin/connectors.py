"""
Admin Connectors Endpoints
Connector management: list, detail, reconnect, revoke
"""
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from uuid import UUID
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, require_platform_admin, CurrentUser
from ._serialization import AdminResponseModel
from app.services.connector_lifecycle import ConnectorActor, ConnectorLifecycle, ConnectorLifecycleError, revoke_token_at_provider

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================

class AdminConnectorItem(AdminResponseModel):
    """Admin connector list item with token status"""
    id: str
    tenant_id: str
    tenant_name: str
    type: str  # calendar, email, crm, drive
    provider: str  # google_calendar, gmail, hubspot
    name: Optional[str] = None
    status: str  # pending, active, error, expired, disconnected
    account_email: Optional[str] = None
    token_expires_at: Optional[str] = None
    token_status: str  # valid, expiring_soon, expired, unknown
    last_refreshed_at: Optional[str] = None
    created_at: str
    refresh_state: str = "idle"
    provider_revocation_status: str = "not_requested"
    error_message: Optional[str] = None


class AdminConnectorDetail(AdminConnectorItem):
    """Detailed connector info for admin"""
    scopes: List[str] = []
    refresh_count: int = 0


class AdminConnectorSummary(BaseModel):
    scope: str = "all_connectors"
    total: int
    active: int
    expiring_or_expired: int
    error: int


class AdminConnectorListResponse(BaseModel):
    """Paginated connector list response"""
    items: List[AdminConnectorItem]
    total: int
    page: int
    page_size: int
    summary: AdminConnectorSummary


# =============================================================================
# Helper Functions
# =============================================================================

def _get_token_status(token_expires_at: Optional[str], status: str) -> str:
    """Determine token status based on expiry and connector status."""
    if status != "active":
        return "unknown"
    if not token_expires_at:
        return "unknown"
    try:
        expires = datetime.fromisoformat(token_expires_at.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if expires < now:
            return "expired"
        elif expires < now + timedelta(hours=24):
            return "expiring_soon"
        return "valid"
    except Exception:
        return "unknown"


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/connectors", response_model=AdminConnectorListResponse)
async def list_admin_connectors(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant"),
    status: Optional[str] = Query(None, description="Filter by status"),
    type: Optional[str] = Query(None, description="Filter by type"),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """
    List all connectors across all tenants.
    
    Returns paginated list with token expiry status.
    """
    filters, args = [], []
    for column, value in (("tenant_id", tenant_id), ("status", status), ("type", type), ("provider", provider)):
        if value is not None:
            try:
                value = UUID(value) if column == "tenant_id" else value
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid tenant ID") from exc
            args.append(value)
            filters.append(f"c.{column}=${len(args)}")
    where = " WHERE " + " AND ".join(filters) if filters else ""
    # The same current account rule as lifecycle/resolver; refreshing an old
    # account never promotes its identity. Summary is unfiltered platform scope.
    account_join = """ LEFT JOIN LATERAL (
        SELECT account_email,token_expires_at,last_refreshed_at,refresh_state,
               count(*) OVER(PARTITION BY created_at) AS creation_ties
        FROM connector_accounts WHERE connector_id=c.id AND tenant_id=c.tenant_id AND status='active'
        ORDER BY created_at DESC,id DESC LIMIT 1
    ) a ON TRUE """
    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            summary_row = await conn.fetchrow("""SELECT count(*) AS total,
                count(*) FILTER (WHERE c.status='active') AS active,
                count(*) FILTER (WHERE c.status='active' AND a.refresh_state='idle' AND a.creation_ties=1
                    AND a.token_expires_at < NOW()+INTERVAL '24 hours') AS expiring_or_expired,
                count(*) FILTER (WHERE c.status='error' OR a.refresh_state='uncertain' OR a.creation_ties>1) AS error
                FROM connectors c""" + account_join)
            total = await conn.fetchval("SELECT count(*) FROM connectors c" + where, *args)
            rows = await conn.fetch("""SELECT c.id,c.tenant_id,c.type,c.provider,c.name,c.status,c.created_at,
                    c.provider_revocation_status,t.business_name AS tenant_name,
                    a.account_email,a.token_expires_at,a.last_refreshed_at,a.refresh_state,a.creation_ties
                FROM connectors c JOIN tenants t ON t.id=c.tenant_id""" + account_join + where +
                f" ORDER BY c.created_at DESC,c.id DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
                *args, page_size, (page-1)*page_size)
        items = []
        for row in rows:
            item = dict(row)
            item["id"], item["tenant_id"] = str(item["id"]), str(item["tenant_id"])
            expires = item["token_expires_at"]
            token_status = _get_token_status(expires.isoformat() if expires else None, item["status"])
            item["refresh_state"] = item["refresh_state"] or "idle"
            item["token_status"] = token_status if item["refresh_state"] == "idle" else "unknown"
            if (item.pop("creation_ties") or 0) > 1:
                item["token_status"] = "unknown"
                item["account_email"] = None
                item["error_message"] = "Current authorization is ambiguous. Reauthorize the connector."
            items.append(AdminConnectorItem(**item))
        return AdminConnectorListResponse(items=items,total=total,page=page,page_size=page_size,
                                          summary=AdminConnectorSummary(**dict(summary_row)))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Connector list is temporarily unavailable") from exc


@router.get("/connectors/{connector_id}", response_model=AdminConnectorDetail)
async def get_admin_connector_detail(
    connector_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get detailed connector info including scopes and token status.
    """
    try:
        # Get connector with tenant
        response = db_client.table("connectors").select(
            "id, tenant_id, type, provider, name, status, created_at, provider_revocation_status, "
            "tenants(business_name)"
        ).eq("id", connector_id).single().execute()
        if getattr(response, "error", None):
            raise HTTPException(status_code=503, detail="Connector details are temporarily unavailable")
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Connector not found")
        
        conn = response.data
        tenant = conn.get("tenants") or {}
        
        # Get account details
        account_query = db_client.table("connector_accounts").select(
            "account_email, token_expires_at, last_refreshed_at, scopes, status, rotation_count, refresh_state, refresh_error, created_at"
        ).eq("connector_id", connector_id).eq("tenant_id", conn["tenant_id"])
        if conn["status"] == "active":
            account_query = account_query.eq("status", "active")
        acc_response = account_query.order("created_at", desc=True).limit(2).execute()
        if getattr(acc_response, "error", None):
            raise HTTPException(status_code=503, detail="Connector credentials could not be checked")
        
        account = acc_response.data[0] if acc_response.data else {}
        ambiguous = bool(acc_response.data and len(acc_response.data) > 1
                         and acc_response.data[0].get("created_at") == acc_response.data[1].get("created_at"))
        
        token_expires = account.get("token_expires_at")
        token_status = _get_token_status(token_expires, conn["status"])
        if account.get("refresh_state", "idle") != "idle" or ambiguous:
            token_status = "unknown"
        
        refresh_count = account.get("rotation_count") or 0
        
        return AdminConnectorDetail(
            id=conn["id"],
            tenant_id=conn["tenant_id"],
            tenant_name=tenant.get("business_name", "Unknown"),
            type=conn["type"],
            provider=conn["provider"],
            name=conn.get("name"),
            status=conn["status"],
            account_email=None if ambiguous else account.get("account_email"),
            token_expires_at=token_expires,
            token_status=token_status,
            last_refreshed_at=account.get("last_refreshed_at"),
            created_at=conn["created_at"],
            scopes=account.get("scopes") or [],
            error_message="Current authorization is ambiguous. Reauthorize the connector." if ambiguous else account.get("refresh_error"),
            refresh_count=refresh_count,
            refresh_state=account.get("refresh_state") or "idle",
            provider_revocation_status=conn.get("provider_revocation_status") or "not_requested",
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get connector: {str(e)}"
        )


@router.post("/connectors/{connector_id}/reconnect")
async def force_reconnect_connector(
    connector_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    *,
    request: Request,
):
    """
    Force token refresh for a connector.
    
    Admin-initiated refresh when tokens are expired or failing.
    """
    try:
        response = db_client.table("connectors").select("id,tenant_id").eq("id", connector_id).single().execute()
        if getattr(response, "error", None):
            raise HTTPException(status_code=503, detail="Could not check connector")
        if not response.data:
            raise HTTPException(status_code=404, detail="Connector not found")
        _, account = await ConnectorLifecycle(db_client, actor=ConnectorActor.human(
            admin_user, "admin_reconnect", request=request, platform_admin=True)).refresh(
            str(response.data["tenant_id"]), connector_id, allow_expired=True,
        )
        return {"success": True, "message": "Connector tokens refreshed successfully",
                "connector_id": connector_id, "refreshed_at": account["last_refreshed_at"].isoformat(),
                "audit_event_id": account["audit_event_id"], "operation_id": account["operation_id"]}
    except HTTPException:
        raise
    except ConnectorLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Refresh could not be confirmed. Check connection status; reauthorize if its outcome is unknown.") from exc


@router.post("/connectors/{connector_id}/revoke")
async def revoke_connector(
    connector_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    *,
    request: Request,
):
    """
    Revoke connector access.
    
    Marks connector as disconnected and invalidates tokens.
    """
    try:
        response = db_client.table("connectors").select("id,tenant_id").eq("id", connector_id).single().execute()
        if getattr(response, "error", None):
            raise HTTPException(status_code=503, detail="Could not check connector")
        if not response.data:
            raise HTTPException(status_code=404, detail="Connector not found")
        result = await ConnectorLifecycle(db_client, actor=ConnectorActor.human(
            admin_user, "admin_revoke", request=request, platform_admin=True)).disable(
            str(response.data["tenant_id"]), connector_id, revoke_token=revoke_token_at_provider,
        )
        return {"success": True, "message": "Connector disabled locally",
                "connector_id": connector_id, **result.receipt()}
    except HTTPException:
        raise
    except ConnectorLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not confirm connector disconnect. Check its current status before trying again.") from exc
