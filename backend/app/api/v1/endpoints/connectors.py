"""
Connector API Endpoints
OAuth flows and connector management.

Day 24: Unified Connector System
"""
import os
import logging
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from supabase import Client

from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser
from app.infrastructure.connectors.base import ConnectorFactory
from app.infrastructure.connectors.oauth import get_oauth_state_manager, OAuthStateError
from app.infrastructure.connectors.encryption import get_encryption_service

# Import providers to register them
from app.infrastructure.connectors.calendar.google_calendar import GoogleCalendarConnector
from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
from app.infrastructure.connectors.email.gmail import GmailConnector
from app.infrastructure.connectors.crm.hubspot import HubSpotConnector
from app.infrastructure.connectors.drive.google_drive import GoogleDriveConnector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["Connectors"])


# =============================================================================
# Request/Response Models
# =============================================================================

class CreateConnectorRequest(BaseModel):
    """Request to create/authorize a new connector"""
    type: str  # calendar, email, crm, drive
    provider: str  # google_calendar, gmail, hubspot, google_drive
    name: Optional[str] = None


class ConnectorResponse(BaseModel):
    """Connector response (no sensitive data)"""
    id: str
    type: str
    provider: str
    name: Optional[str]
    status: str
    account_email: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class OAuthAuthorizeResponse(BaseModel):
    """OAuth authorization URL response"""
    authorization_url: str
    state: str


class ProviderInfo(BaseModel):
    """Information about an available provider"""
    provider: str
    type: str
    name: str
    description: str
    requires_oauth: bool = True


# =============================================================================
# Provider Metadata
# =============================================================================

PROVIDER_METADATA = {
    "google_calendar": {
        "type": "calendar",
        "name": "Google Calendar",
        "description": "Connect your Google Calendar to book meetings with Google Meet",
        "requires_oauth": True
    },
    "outlook_calendar": {
        "type": "calendar",
        "name": "Microsoft Outlook",
        "description": "Connect your Outlook Calendar to book meetings with Microsoft Teams",
        "requires_oauth": True
    },
    "gmail": {
        "type": "email",
        "name": "Gmail",
        "description": "Send emails using your Gmail account",
        "requires_oauth": True
    },
    "hubspot": {
        "type": "crm",
        "name": "HubSpot",
        "description": "Sync contacts and deals with HubSpot CRM",
        "requires_oauth": True
    },
    "google_drive": {
        "type": "drive",
        "name": "Google Drive",
        "description": "Upload and manage files in Google Drive",
        "requires_oauth": True
    }
}


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/providers", response_model=List[ProviderInfo])
async def list_providers():
    """
    List all available connector providers.
    
    Returns metadata about each provider type.
    """
    providers = []
    for provider_name in ConnectorFactory.list_providers():
        metadata = PROVIDER_METADATA.get(provider_name, {})
        providers.append(ProviderInfo(
            provider=provider_name,
            type=metadata.get("type", "unknown"),
            name=metadata.get("name", provider_name),
            description=metadata.get("description", ""),
            requires_oauth=metadata.get("requires_oauth", True)
        ))
    return providers


@router.get("", response_model=List[ConnectorResponse])
async def list_connectors(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    type: Optional[str] = Query(None, description="Filter by type")
):
    """
    List tenant's connectors.
    
    Returns all connectors with their status.
    Token data is NOT included.
    """
    query = supabase.table("connectors").select(
        "id, type, provider, name, status, created_at"
    ).eq("tenant_id", current_user.tenant_id)
    
    if type:
        query = query.eq("type", type)
    
    response = query.order("created_at", desc=True).execute()
    
    # Get account emails for connected connectors
    connectors = []
    for conn in response.data:
        account_email = None
        if conn["status"] == "active":
            acc_response = supabase.table("connector_accounts").select(
                "account_email"
            ).eq("connector_id", conn["id"]).eq("status", "active").limit(1).execute()
            
            if acc_response.data:
                account_email = acc_response.data[0].get("account_email")
        
        connectors.append(ConnectorResponse(
            id=conn["id"],
            type=conn["type"],
            provider=conn["provider"],
            name=conn.get("name"),
            status=conn["status"],
            account_email=account_email,
            created_at=conn["created_at"]
        ))
    
    return connectors


@router.get("/{connector_id}", response_model=ConnectorResponse)
async def get_connector(
    connector_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """Get a specific connector's details."""
    response = supabase.table("connectors").select(
        "id, type, provider, name, status, created_at"
    ).eq("id", connector_id).eq("tenant_id", current_user.tenant_id).single().execute()
    
    if not response.data:
        raise HTTPException(status_code=404, detail="Connector not found")
    
    conn = response.data
    
    # Get account email
    account_email = None
    if conn["status"] == "active":
        acc_response = supabase.table("connector_accounts").select(
            "account_email"
        ).eq("connector_id", conn["id"]).eq("status", "active").limit(1).execute()
        
        if acc_response.data:
            account_email = acc_response.data[0].get("account_email")
    
    return ConnectorResponse(
        id=conn["id"],
        type=conn["type"],
        provider=conn["provider"],
        name=conn.get("name"),
        status=conn["status"],
        account_email=account_email,
        created_at=conn["created_at"]
    )


@router.post("/authorize", response_model=OAuthAuthorizeResponse)
async def authorize_connector(
    request: CreateConnectorRequest,
    http_request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Start OAuth authorization flow for a connector.
    
    1. Creates a pending connector record
    2. Generates OAuth state with PKCE
    3. Returns authorization URL
    
    User should be redirected to the authorization URL.
    """
    # Validate provider
    if not ConnectorFactory.is_registered(request.provider):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {request.provider}"
        )
    
    # Create pending connector record
    connector_data = {
        "tenant_id": current_user.tenant_id,
        "type": request.type,
        "provider": request.provider,
        "name": request.name or PROVIDER_METADATA.get(request.provider, {}).get("name"),
        "status": "pending"
    }
    
    conn_response = supabase.table("connectors").insert(connector_data).execute()
    
    if not conn_response.data:
        raise HTTPException(status_code=500, detail="Failed to create connector")
    
    connector_id = conn_response.data[0]["id"]
    
    # Generate OAuth state with PKCE
    # Determine callback URL
    base_url = os.getenv("API_BASE_URL", str(http_request.base_url).rstrip("/"))
    redirect_uri = f"{base_url}/api/v1/connectors/callback"
    
    oauth_manager = get_oauth_state_manager()
    state_data = await oauth_manager.create_state(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        provider=request.provider,
        redirect_uri=redirect_uri,
        extra_data={"connector_id": connector_id}
    )
    
    # Create connector instance and get OAuth URL
    connector = ConnectorFactory.create(
        provider=request.provider,
        tenant_id=current_user.tenant_id,
        connector_id=connector_id
    )
    
    auth_url = connector.get_oauth_url(
        redirect_uri=redirect_uri,
        state=state_data["state"],
        code_challenge=state_data["code_challenge"]
    )
    
    return OAuthAuthorizeResponse(
        authorization_url=auth_url,
        state=state_data["state"]
    )


@router.get("/callback")
async def oauth_callback(
    request: Request,
    state: str = Query(...),
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    supabase: Client = Depends(get_supabase)
):
    """
    OAuth callback handler.
    
    Validates state, exchanges code for tokens, and stores encrypted tokens.
    Redirects to frontend success/error page.
    
    Note: This endpoint does NOT require authentication - it's a redirect from OAuth provider.
    """
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    
    if error:
        logger.warning(f"OAuth error: {error}")
        return RedirectResponse(
            f"{frontend_url}/integrations?error={error}"
        )
    
    if not code:
        logger.warning("OAuth callback missing code parameter")
        return RedirectResponse(
            f"{frontend_url}/integrations?error=missing_code"
        )
    
    try:
        # Validate state
        oauth_manager = get_oauth_state_manager()
        state_data = await oauth_manager.validate_state(state)
        
        if not state_data:
            raise OAuthStateError("Invalid or expired OAuth state")
        
        tenant_id = state_data["tenant_id"]
        user_id = state_data["user_id"]
        provider = state_data["provider"]
        redirect_uri = state_data["redirect_uri"]
        code_verifier = state_data["code_verifier"]
        connector_id = state_data.get("connector_id")
        
        if not connector_id:
            raise ValueError("Missing connector_id in state")
        
        # Create connector instance
        connector = ConnectorFactory.create(
            provider=provider,
            tenant_id=tenant_id,
            connector_id=connector_id
        )
        
        # Exchange code for tokens
        tokens = await connector.exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier
        )
        
        # Encrypt tokens
        encryption = get_encryption_service()
        access_token_encrypted = encryption.encrypt(tokens.access_token)
        refresh_token_encrypted = encryption.encrypt(tokens.refresh_token or "")
        
        # Get account email if available (for Google providers)
        account_email = None
        if provider in ["gmail", "google_calendar", "google_drive"]:
            try:
                await connector.set_access_token(tokens.access_token)
                if hasattr(connector, "get_profile"):
                    profile = await connector.get_profile()
                    account_email = profile.get("emailAddress")
            except Exception as e:
                logger.warning(f"Failed to get account email: {e}")
        
        # Store tokens in connector_accounts
        account_data = {
            "connector_id": connector_id,
            "tenant_id": tenant_id,
            "access_token_encrypted": access_token_encrypted,
            "refresh_token_encrypted": refresh_token_encrypted,
            "token_expires_at": tokens.expires_at.isoformat() if tokens.expires_at else None,
            "scopes": tokens.scope.split(" ") if tokens.scope else connector.oauth_scopes,
            "account_email": account_email,
            "status": "active",
            "last_refreshed_at": datetime.utcnow().isoformat()
        }
        
        supabase.table("connector_accounts").insert(account_data).execute()
        
        # Update connector status to active
        supabase.table("connectors").update({
            "status": "active"
        }).eq("id", connector_id).execute()
        
        logger.info(f"OAuth completed for {provider}, connector {connector_id}")
        
        return RedirectResponse(
            f"{frontend_url}/integrations?success=true&provider={provider}"
        )
        
    except OAuthStateError as e:
        logger.warning(f"OAuth state error: {e}")
        return RedirectResponse(
            f"{frontend_url}/integrations?error=invalid_state"
        )
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return RedirectResponse(
            f"{frontend_url}/integrations?error=callback_failed"
        )


@router.delete("/{connector_id}")
async def delete_connector(
    connector_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Disconnect/delete a connector.
    
    Revokes tokens and removes connector record.
    """
    # Verify ownership
    conn_response = supabase.table("connectors").select(
        "id, provider"
    ).eq("id", connector_id).eq("tenant_id", current_user.tenant_id).single().execute()
    
    if not conn_response.data:
        raise HTTPException(status_code=404, detail="Connector not found")
    
    # Delete connector accounts (tokens)
    supabase.table("connector_accounts").delete().eq(
        "connector_id", connector_id
    ).execute()
    
    # Delete connector
    supabase.table("connectors").delete().eq(
        "id", connector_id
    ).execute()
    
    return {"success": True, "message": "Connector disconnected"}


@router.post("/{connector_id}/refresh")
async def refresh_connector_tokens(
    connector_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Force refresh connector tokens.
    
    Useful when tokens are about to expire.
    """
    # Get connector
    conn_response = supabase.table("connectors").select(
        "id, provider, tenant_id"
    ).eq("id", connector_id).eq("tenant_id", current_user.tenant_id).single().execute()
    
    if not conn_response.data:
        raise HTTPException(status_code=404, detail="Connector not found")
    
    provider = conn_response.data["provider"]
    
    # Get account with tokens
    acc_response = supabase.table("connector_accounts").select(
        "id, refresh_token_encrypted"
    ).eq("connector_id", connector_id).eq("status", "active").single().execute()
    
    if not acc_response.data:
        raise HTTPException(status_code=400, detail="No active account found")
    
    account_id = acc_response.data["id"]
    refresh_token_encrypted = acc_response.data["refresh_token_encrypted"]
    
    if not refresh_token_encrypted:
        raise HTTPException(status_code=400, detail="No refresh token available")
    
    # Decrypt refresh token
    encryption = get_encryption_service()
    refresh_token = encryption.decrypt(refresh_token_encrypted)
    
    # Create connector and refresh tokens
    connector = ConnectorFactory.create(
        provider=provider,
        tenant_id=current_user.tenant_id,
        connector_id=connector_id
    )
    
    new_tokens = await connector.refresh_tokens(refresh_token)
    
    # Update stored tokens
    new_access_encrypted = encryption.encrypt(new_tokens.access_token)
    new_refresh_encrypted = encryption.encrypt(new_tokens.refresh_token or refresh_token)
    
    supabase.table("connector_accounts").update({
        "access_token_encrypted": new_access_encrypted,
        "refresh_token_encrypted": new_refresh_encrypted,
        "token_expires_at": new_tokens.expires_at.isoformat() if new_tokens.expires_at else None,
        "last_refreshed_at": datetime.utcnow().isoformat()
    }).eq("id", account_id).execute()
    
    return {"success": True, "message": "Tokens refreshed"}
