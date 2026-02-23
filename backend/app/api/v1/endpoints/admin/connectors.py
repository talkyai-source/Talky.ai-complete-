"""
Admin Connectors Endpoints
Connector management: list, detail, reconnect, revoke
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, require_admin, CurrentUser

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================

class AdminConnectorItem(BaseModel):
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


class AdminConnectorDetail(AdminConnectorItem):
    """Detailed connector info for admin"""
    scopes: List[str] = []
    error_message: Optional[str] = None
    refresh_count: int = 0


class AdminConnectorListResponse(BaseModel):
    """Paginated connector list response"""
    items: List[AdminConnectorItem]
    total: int
    page: int
    page_size: int


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
        now = datetime.utcnow().replace(tzinfo=expires.tzinfo)
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
    admin_user: CurrentUser = Depends(require_admin),
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
    try:
        # Build query with tenant join
        query = db_client.table("connectors").select(
            "id, tenant_id, type, provider, name, status, created_at, "
            "tenants(business_name)"
        )
        
        if tenant_id:
            query = query.eq("tenant_id", tenant_id)
        if status:
            query = query.eq("status", status)
        if type:
            query = query.eq("type", type)
        if provider:
            query = query.eq("provider", provider)
        
        # Get total count
        count_response = query.execute()
        total = len(count_response.data) if count_response.data else 0
        
        # Apply pagination
        offset = (page - 1) * page_size
        query = db_client.table("connectors").select(
            "id, tenant_id, type, provider, name, status, created_at, "
            "tenants(business_name)"
        )
        
        if tenant_id:
            query = query.eq("tenant_id", tenant_id)
        if status:
            query = query.eq("status", status)
        if type:
            query = query.eq("type", type)
        if provider:
            query = query.eq("provider", provider)
        
        response = query.order("created_at", desc=True).range(offset, offset + page_size - 1).execute()
        
        items = []
        for conn in response.data or []:
            # Get account info (token expiry, email)
            acc_response = db_client.table("connector_accounts").select(
                "account_email, token_expires_at, last_refreshed_at"
            ).eq("connector_id", conn["id"]).eq("status", "active").limit(1).execute()
            
            account = acc_response.data[0] if acc_response.data else {}
            tenant = conn.get("tenants") or {}
            
            token_expires = account.get("token_expires_at")
            token_status = _get_token_status(token_expires, conn["status"])
            
            items.append(AdminConnectorItem(
                id=conn["id"],
                tenant_id=conn["tenant_id"],
                tenant_name=tenant.get("business_name", "Unknown"),
                type=conn["type"],
                provider=conn["provider"],
                name=conn.get("name"),
                status=conn["status"],
                account_email=account.get("account_email"),
                token_expires_at=token_expires,
                token_status=token_status,
                last_refreshed_at=account.get("last_refreshed_at"),
                created_at=conn["created_at"]
            ))
        
        return AdminConnectorListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list connectors: {str(e)}"
        )


@router.get("/connectors/{connector_id}", response_model=AdminConnectorDetail)
async def get_admin_connector_detail(
    connector_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get detailed connector info including scopes and token status.
    """
    try:
        # Get connector with tenant
        response = db_client.table("connectors").select(
            "id, tenant_id, type, provider, name, status, created_at, "
            "tenants(business_name)"
        ).eq("id", connector_id).single().execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Connector not found")
        
        conn = response.data
        tenant = conn.get("tenants") or {}
        
        # Get account details
        acc_response = db_client.table("connector_accounts").select(
            "account_email, token_expires_at, last_refreshed_at, scopes, status"
        ).eq("connector_id", connector_id).limit(1).execute()
        
        account = acc_response.data[0] if acc_response.data else {}
        
        token_expires = account.get("token_expires_at")
        token_status = _get_token_status(token_expires, conn["status"])
        
        # Count refreshes (placeholder - would need refresh_log table)
        refresh_count = 0
        
        return AdminConnectorDetail(
            id=conn["id"],
            tenant_id=conn["tenant_id"],
            tenant_name=tenant.get("business_name", "Unknown"),
            type=conn["type"],
            provider=conn["provider"],
            name=conn.get("name"),
            status=conn["status"],
            account_email=account.get("account_email"),
            token_expires_at=token_expires,
            token_status=token_status,
            last_refreshed_at=account.get("last_refreshed_at"),
            created_at=conn["created_at"],
            scopes=account.get("scopes") or [],
            error_message=None,
            refresh_count=refresh_count
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
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Force token refresh for a connector.
    
    Admin-initiated refresh when tokens are expired or failing.
    """
    try:
        # Get connector
        conn_response = db_client.table("connectors").select(
            "id, provider, tenant_id, status"
        ).eq("id", connector_id).single().execute()
        
        if not conn_response.data:
            raise HTTPException(status_code=404, detail="Connector not found")
        
        conn = conn_response.data
        
        if conn["status"] not in ("active", "error", "expired"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reconnect connector with status: {conn['status']}"
            )
        
        # Get account with refresh token
        acc_response = db_client.table("connector_accounts").select(
            "id, refresh_token_encrypted"
        ).eq("connector_id", connector_id).limit(1).execute()
        
        if not acc_response.data:
            raise HTTPException(status_code=400, detail="No account found for connector")
        
        account = acc_response.data[0]
        
        if not account.get("refresh_token_encrypted"):
            raise HTTPException(status_code=400, detail="No refresh token available")
        
        # Import required services
        from app.infrastructure.connectors.base import ConnectorFactory
        from app.infrastructure.connectors.encryption import get_encryption_service
        
        # Decrypt refresh token
        encryption = get_encryption_service()
        refresh_token = encryption.decrypt(account["refresh_token_encrypted"])
        
        # Create connector and refresh tokens
        connector = ConnectorFactory.create(
            provider=conn["provider"],
            tenant_id=conn["tenant_id"],
            connector_id=connector_id
        )
        
        new_tokens = await connector.refresh_tokens(refresh_token)
        
        # Update stored tokens
        new_access_encrypted = encryption.encrypt(new_tokens.access_token)
        new_refresh_encrypted = encryption.encrypt(new_tokens.refresh_token or refresh_token)
        
        now = datetime.utcnow().isoformat()
        db_client.table("connector_accounts").update({
            "access_token_encrypted": new_access_encrypted,
            "refresh_token_encrypted": new_refresh_encrypted,
            "token_expires_at": new_tokens.expires_at.isoformat() if new_tokens.expires_at else None,
            "last_refreshed_at": now,
            "status": "active"
        }).eq("id", account["id"]).execute()
        
        # Update connector status to active
        db_client.table("connectors").update({
            "status": "active"
        }).eq("id", connector_id).execute()
        
        return {
            "success": True,
            "message": "Connector tokens refreshed successfully",
            "connector_id": connector_id,
            "refreshed_at": now
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reconnect connector: {str(e)}"
        )


@router.post("/connectors/{connector_id}/revoke")
async def revoke_connector(
    connector_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Revoke connector access.
    
    Marks connector as disconnected and invalidates tokens.
    """
    try:
        # Get connector
        conn_response = db_client.table("connectors").select(
            "id, status"
        ).eq("id", connector_id).single().execute()
        
        if not conn_response.data:
            raise HTTPException(status_code=404, detail="Connector not found")
        
        # Update connector status
        now = datetime.utcnow().isoformat()
        db_client.table("connectors").update({
            "status": "disconnected"
        }).eq("id", connector_id).execute()
        
        # Revoke account tokens
        db_client.table("connector_accounts").update({
            "status": "revoked",
            "access_token_encrypted": None,
            "refresh_token_encrypted": None
        }).eq("connector_id", connector_id).execute()
        
        return {
            "success": True,
            "message": "Connector access revoked",
            "connector_id": connector_id,
            "revoked_at": now
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to revoke connector: {str(e)}"
        )
