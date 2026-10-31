"""
Connector API Endpoints
OAuth flows and connector management.

Day 24: Unified Connector System
"""
import asyncio
import os
import logging
import re
from contextlib import suppress
from typing import Optional, List, Any
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.core.security.rbac import require_permission, Permission
from app.infrastructure.connectors.base import ConnectorFactory
from app.infrastructure.connectors.oauth import get_oauth_state_manager, OAuthStateError
from app.infrastructure.connectors.encryption import get_encryption_service
from app.services.connector_lifecycle import (
    ConnectorActor, ConnectorLifecycle, ConnectorLifecycleError, revoke_token_at_provider as _revoke_token_at_provider,
)

# Import providers to register them
from app.infrastructure.connectors.calendar.google_calendar import GoogleCalendarConnector  # noqa: F401
from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector  # noqa: F401
from app.infrastructure.connectors.email.gmail import GmailConnector  # noqa: F401
from app.infrastructure.connectors.crm.hubspot import HubSpotConnector  # noqa: F401
from app.infrastructure.connectors.crm.salesforce import SalesforceConnector  # noqa: F401
from app.infrastructure.connectors.drive.google_drive import GoogleDriveConnector  # noqa: F401

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
    "salesforce": {
        "type": "crm",
        "name": "Salesforce",
        "description": (
            "Log every call as a Salesforce Task, create Leads for new callees, "
            "and let Salesforce request agent callbacks"
        ),
        "requires_oauth": True
    },
    "google_drive": {
        "type": "drive",
        "name": "Google Drive",
        "description": "Upload and manage files in Google Drive",
        "requires_oauth": True
    }
}


def _frontend_callback_url(frontend_url: str, *, status: str, provider: Optional[str] = None, error: Optional[str] = None) -> str:
    params = [("status", status)]
    # The dashboard keys its cards by CARD key (Salesforce has its own card
    # even though its connector type is "crm"), so report that, not the type.
    provider_type = CARD_KEY_BY_PROVIDER.get(provider or "") or PROVIDER_METADATA.get(provider or "", {}).get("type")
    if provider_type:
        params.append(("type", provider_type))
    if provider:
        params.append(("provider", provider))
    if error:
        params.append(("error", error))
    return f"{frontend_url}/connectors/callback?{urlencode(params)}"


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
    db_client: Client = Depends(get_db_client),
    type: Optional[str] = Query(None, description="Filter by type")
):
    """
    List tenant's connectors.
    
    Returns all connectors with their status.
    Token data is NOT included.
    """
    query = db_client.table("connectors").select(
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
            acc_response = db_client.table("connector_accounts").select(
                "account_email"
            ).eq("connector_id", conn["id"]).eq("status", "active").limit(1).execute()
            
            if acc_response.data:
                account_email = acc_response.data[0].get("account_email")
        
        connectors.append(ConnectorResponse(
            id=str(conn["id"]),
            type=conn["type"],
            provider=conn["provider"],
            name=conn.get("name"),
            status=conn["status"],
            account_email=account_email,
            created_at=conn["created_at"]
        ))
    
    return connectors


# =============================================================================
# Type-keyed shim endpoints (consumed by the dashboard ConnectorCard UI).
# The dashboard treats one card per provider TYPE (calendar/email/crm/drive)
# rather than per connector UUID. These endpoints translate type→default
# provider and reuse the underlying connector machinery.
# =============================================================================

# Canonical provider per connector type. Multi-provider per type (e.g. Outlook
# alongside Google Calendar) is intentionally out of scope for the dashboard
# card UI — admins can still manage those rows directly via the typed
# endpoints in this file.
DEFAULT_PROVIDER_BY_TYPE = {
    "calendar": "google_calendar",
    "email": "gmail",
    "crm": "hubspot",
    "drive": "google_drive",
}

# Dashboard CARD key -> (connector type, provider). The first four cards are
# one-per-type; Salesforce is a second CRM card, so its rows still carry
# type="crm" (the CRM sync resolves by type) but the card UI addresses it as
# "salesforce". Every type-keyed shim below speaks CARD keys.
CARD_PROVIDERS: dict[str, tuple[str, str]] = {
    **{t: (t, p) for t, p in DEFAULT_PROVIDER_BY_TYPE.items()},
    "salesforce": ("crm", "salesforce"),
}
CARD_KEY_BY_PROVIDER: dict[str, str] = {p: key for key, (_t, p) in CARD_PROVIDERS.items()}

_KNOWN_TYPES = set(DEFAULT_PROVIDER_BY_TYPE.keys())


def _card_key_for_row(row: dict) -> Optional[str]:
    """Which dashboard card a connector row belongs to (None = not shown)."""
    provider = str(row.get("provider") or "")
    key = CARD_KEY_BY_PROVIDER.get(provider)
    if key:
        return key
    conn_type = row.get("type")
    return conn_type if conn_type in _KNOWN_TYPES else None


def _salesforce_card_advertised(rows: list[dict]) -> bool:
    """The Salesforce card is only offered when the server can actually run
    the OAuth flow (Connected App credentials present) or the tenant already
    has Salesforce rows to manage."""
    if any(str(r.get("provider") or "") == "salesforce" for r in rows):
        return True
    return ConnectorFactory.is_registered("salesforce") and SalesforceConnector.is_configured()

_GMAIL_READ_SCOPES = {
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
}
_GMAIL_SEND_SCOPES = {
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
}


def _normalize_scopes(raw_scopes: Any) -> set[str]:
    if not raw_scopes:
        return set()
    if isinstance(raw_scopes, str):
        return {part for part in re.split(r"[\s,]+", raw_scopes.strip()) if part}
    if isinstance(raw_scopes, (list, tuple, set)):
        return {str(part).strip() for part in raw_scopes if str(part).strip()}
    return set()


def _missing_operation_scopes(provider: str, granted: set[str]) -> set[str]:
    if provider != "gmail":
        return set()
    missing = set()
    if not (granted & _GMAIL_READ_SCOPES):
        missing.add("https://www.googleapis.com/auth/gmail.readonly")
    if not (granted & _GMAIL_SEND_SCOPES):
        missing.add("https://www.googleapis.com/auth/gmail.send")
    return missing


def _active_account_health(provider: str, account: Optional[dict]) -> tuple[str, Optional[str]]:
    """Assess locally usable credentials without making a provider call."""
    if not account:
        return "error", "The connector has no active credential record. Reconnect it."
    if account.get("refresh_state", "idle") != "idle":
        return "error", "Token refresh is unresolved. Wait for completion; if it stays unresolved, authorize the connector again."

    if not account.get("access_token_encrypted"):
        return "error", "The connector has no saved access credential. Reconnect it."
    if provider == "gmail" and not account.get("refresh_token_encrypted"):
        return "error", "The Gmail authorization cannot be refreshed. Reconnect it."

    # Ciphertext presence alone is not health: a rotated/mismatched encryption
    # key otherwise leaves the dashboard green while every resolver call fails.
    # Validate locally, never log or return the decrypted values.
    try:
        encryption = get_encryption_service()
        if not encryption.decrypt(account["access_token_encrypted"]):
            raise ValueError("empty access credential")
        encrypted_refresh = account.get("refresh_token_encrypted")
        if encrypted_refresh and not encryption.decrypt(encrypted_refresh):
            raise ValueError("empty refresh credential")
    except Exception:
        logger.error("Connector credential health check failed provider=%s", provider)
        return "error", "The saved authorization cannot be used. Reconnect the connector."

    missing = _missing_operation_scopes(provider, _normalize_scopes(account.get("scopes")))
    if missing:
        return (
            "error",
            "The connection is missing required permissions. Connect it again and grant all requested access.",
        )

    expires_at = account.get("token_expires_at")
    if expires_at:
        try:
            parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return "error", "The saved credential expiry is invalid. Reconnect the connector."
        if parsed <= datetime.now(timezone.utc):
            if not account.get("refresh_token_encrypted"):
                return "expired", "The authorization expired and cannot be refreshed. Reconnect it."

    return "connected", None


def _ui_status(db_status: Optional[str]) -> str:
    if db_status == "active":
        return "connected"
    if db_status == "expired":
        return "expired"
    if db_status in ("error", "revoked", "failed"):
        return "error"
    return "disconnected"


class ConnectorTypeStatus(BaseModel):
    type: str
    status: str  # connected | disconnected | expired | error
    provider: Optional[str] = None
    last_sync: Optional[str] = None
    error_message: Optional[str] = None


class ConnectorStatusListResponse(BaseModel):
    items: List[ConnectorTypeStatus]


class TypeAuthorizeResponse(BaseModel):
    authorization_url: str


@router.get("/status", response_model=ConnectorStatusListResponse)
async def list_connector_statuses(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """One status row per known connector TYPE for this tenant.

    The dashboard renders a fixed set of cards (calendar/email/crm/drive),
    so we always return all four — types without a row resolve to
    `disconnected`.
    """
    response = (
        db_client.table("connectors")
        .select("id, type, provider, status, created_at")
        .eq("tenant_id", current_user.tenant_id)
        .order("created_at", desc=True)
        .execute()
    )
    if getattr(response, "error", None):
        logger.error(
            "Connector status lookup failed tenant=%s: %s",
            str(current_user.tenant_id)[:8],
            response.error,
        )
        raise HTTPException(status_code=503, detail="Connector status is temporarily unavailable")

    rows = list(response.data or [])
    connectors_by_type: dict[str, list[dict]] = {key: [] for key in CARD_PROVIDERS}
    for conn in rows:
        key = _card_key_for_row(conn)
        if key in connectors_by_type:
            connectors_by_type[key].append(conn)

    card_keys = [k for k in CARD_PROVIDERS if k != "salesforce"]
    if _salesforce_card_advertised(rows):
        card_keys.append("salesforce")

    items: List[ConnectorTypeStatus] = []
    for type_name in card_keys:
        candidates = connectors_by_type[type_name]
        if not candidates:
            items.append(ConnectorTypeStatus(type=type_name, status="disconnected", provider=None))
            continue

        # The newest active connector is authoritative. Do not fall back across
        # connector IDs: an older row may be a different Gmail account and
        # silently selecting it could expose the wrong mailbox.
        active_candidates = [row for row in candidates if row.get("status") == "active"]
        conn = candidates[0]
        last_sync: Optional[str] = None
        status = _ui_status(conn.get("status"))
        error_message: Optional[str] = None
        first_active_result: Optional[tuple[dict, str, Optional[str], Optional[str]]] = None
        selected_active_result: Optional[tuple[dict, str, Optional[str], Optional[str]]] = None
        for active_conn in active_candidates[:1]:
            acc = (
                db_client.table("connector_accounts")
                .select(
                    "access_token_encrypted, refresh_token_encrypted, token_expires_at, "
                    "scopes, last_refreshed_at, refresh_state"
                )
                .eq("connector_id", active_conn["id"])
                .eq("tenant_id", current_user.tenant_id)
                .eq("status", "active")
                .order("last_refreshed_at", desc=True)
                .limit(1)
                .execute()
            )
            if getattr(acc, "error", None):
                result = (
                    active_conn,
                    "error",
                    "Connector credentials could not be checked right now.",
                    None,
                )
            else:
                account_data = acc.data
                accounts = (
                    account_data
                    if isinstance(account_data, list)
                    else ([account_data] if isinstance(account_data, dict) else [])
                )
                result = (
                    active_conn,
                    "error",
                    "The connector has no usable active credential. Reconnect it.",
                    None,
                )
                for account in accounts:
                    account_status, account_error = _active_account_health(
                        active_conn.get("provider"), account
                    )
                    refreshed = account.get("last_refreshed_at")
                    account_sync = (
                        refreshed
                        if isinstance(refreshed, str)
                        else (refreshed.isoformat() if refreshed else None)
                    )
                    result = (active_conn, account_status, account_error, account_sync)
                    if account_status == "connected":
                        break

            if first_active_result is None:
                first_active_result = result
            if result[1] == "connected":
                selected_active_result = result
                break

        chosen = selected_active_result or first_active_result
        if chosen is not None:
            conn, status, error_message, last_sync = chosen

        items.append(ConnectorTypeStatus(
            type=type_name,
            status=status,
            provider=conn.get("provider"),
            last_sync=last_sync,
            error_message=error_message,
        ))

    return ConnectorStatusListResponse(items=items)


async def _start_connector_authorization(*, connector_type, provider, name,
                                        http_request, current_user, db_client,
                                        frontend_redirect_uri=None):
    """Share human admission and bounded preparation across both OAuth starts."""
    try:
        lifecycle = ConnectorLifecycle(db_client, actor=ConnectorActor.human(
            current_user, "tenant_oauth_start", request=http_request))
        pending = await lifecycle.create_pending(current_user.tenant_id, connector_type, provider, name)
    except ConnectorLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Connector authorization could not be confirmed. Check connection status before starting again.") from exc

    connector_id = pending["id"]
    oauth_manager = None
    state_data = None
    try:
        base_url = os.getenv("API_BASE_URL", str(http_request.base_url).rstrip("/"))
        backend_callback = f"{base_url}/api/v1/connectors/callback"
        extra_data = {"connector_id": connector_id}
        if frontend_redirect_uri is not None:
            extra_data["frontend_redirect_uri"] = frontend_redirect_uri
        oauth_manager = get_oauth_state_manager()
        state_data = await asyncio.wait_for(oauth_manager.create_state(
            tenant_id=current_user.tenant_id, user_id=current_user.id,
            provider=provider, redirect_uri=backend_callback, extra_data=extra_data,
        ), timeout=5.0)
        connector = ConnectorFactory.create(
            provider=provider, tenant_id=current_user.tenant_id, connector_id=connector_id,
        )
        auth_url = connector.get_oauth_url(
            redirect_uri=backend_callback, state=state_data["state"],
            code_challenge=state_data["code_challenge"],
        )
        if not isinstance(auth_url, str) or not auth_url.strip():
            raise ValueError("OAuth authorization URL was not prepared")
        return {"authorization_url": auth_url, "state": state_data["state"]}
    except (Exception, asyncio.CancelledError) as exc:
        # The pending creation already committed. Retire only that operation;
        # this is completion, not a new write authorized by a later session.
        rejected = False
        try:
            await asyncio.wait_for(lifecycle.reject_pending(current_user.tenant_id, connector_id), timeout=5.0)
            rejected = True
        except Exception:
            pass
        state_deleted = False
        if oauth_manager is not None and isinstance(state_data, dict) and isinstance(state_data.get("state"), str):
            with suppress(Exception):
                state_deleted = bool(await asyncio.wait_for(oauth_manager.delete_state(state_data["state"]), timeout=1.0))
        logger.warning("Connector OAuth preparation unavailable connector=%s provider=%s failure=%s rejection_check_completed=%s state_deleted=%s",
                       connector_id, provider, type(exc).__name__, rejected, state_deleted)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(status_code=503, detail="Connector authorization could not be prepared. Check connection status before starting again.") from exc


@router.get("/{type}/authorize", response_model=TypeAuthorizeResponse, dependencies=[Depends(require_permission(Permission.CONNECTORS_CREATE))])
async def authorize_connector_by_type(
    type: str,
    http_request: Request,
    redirect_uri: str = Query(..., description="Frontend OAuth callback URL"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Start OAuth for the provider behind this dashboard card."""
    if type not in CARD_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown connector type: {type}")

    conn_type, provider = CARD_PROVIDERS[type]
    if not ConnectorFactory.is_registered(provider):
        raise HTTPException(status_code=503, detail=f"Provider not configured: {provider}")
    if provider == "salesforce" and not SalesforceConnector.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Salesforce is not enabled on this server (SALESFORCE_CLIENT_ID/SECRET missing)",
        )

    prepared = await _start_connector_authorization(
        connector_type=conn_type, provider=provider,
        name=PROVIDER_METADATA.get(provider, {}).get("name"),
        http_request=http_request, current_user=current_user, db_client=db_client,
        frontend_redirect_uri=redirect_uri,
    )
    return TypeAuthorizeResponse(authorization_url=prepared["authorization_url"])


# Salesforce settings that live in connectors.config and must survive a
# reconnect. Identity keys (instance_url, org_id, ...) are deliberately absent:
# the fresh authorization decides those. No OAuth token material is carried.
_SALESFORCE_RECONNECT_KEYS = (
    "webhook_token_hash", "webhook_token_encrypted", "callback_campaign_id",
    "callback_priority", "log_calls", "create_leads", "sync_inbound",
)


async def _disconnect_connector(db_client: Client, tenant_id: str, connector_id: str, provider: str, *, current_user, request: Request):
    """Disable locally atomically, then report provider revocation separately."""
    try:
        return await ConnectorLifecycle(db_client, actor=ConnectorActor.human(
            current_user, "tenant_disconnect", request=request)).disable(
            tenant_id, connector_id, revoke_token=_revoke_token_at_provider
        )
    except ConnectorLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not confirm connector disconnect. Check its current status before trying again.") from exc


@router.post("/{type}/disconnect", dependencies=[Depends(require_permission(Permission.CONNECTORS_DELETE))])
async def disconnect_connector_by_type(
    type: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    *,
    request: Request,
):
    """Disconnect every connector behind this dashboard card for this tenant.

    Provider-scoped: the CRM type now has two cards (HubSpot, Salesforce), so
    disconnecting one must never remove the other's rows.
    """
    if type not in CARD_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown connector type: {type}")

    conn_type, provider = CARD_PROVIDERS[type]
    response = (
        db_client.table("connectors")
        .select("id, provider")
        .eq("tenant_id", current_user.tenant_id)
        .eq("type", conn_type)
        .eq("provider", provider)
        .execute()
    )
    if getattr(response, "error", None):
        raise HTTPException(status_code=503, detail="Could not check connectors to disconnect")
    ids = [row["id"] for row in (response.data or [])]
    if not ids:
        return {"success": True, "message": "Nothing to disconnect", "removed": 0}

    provider_revoked = True
    receipts = []
    for connector_id in ids:
        result = await _disconnect_connector(
            db_client, current_user.tenant_id, connector_id, provider, current_user=current_user, request=request,
        )
        provider_revoked &= result.provider_revoked
        receipts.append({"connector_id": connector_id, **result.receipt()})

    return {
        "success": True, "message": "Connector disconnected",
        "removed": len(ids), "provider_revoked": provider_revoked,
        "receipts": receipts,
    }


# NOTE: the dynamic GET /{connector_id} route is deliberately registered LATER
# in this file (right after the OAuth /callback route). A single-segment catch-
# all like /{connector_id} matches in registration order, so declaring it here
# would shadow the static GET /callback — Google's OAuth redirect to
# /connectors/callback resolved to get_connector("callback"), which 404'd
# ("Connector not found") and silently broke every connector connection. Keep
# all static single-segment GET routes ABOVE the catch-all.


@router.post("/authorize", response_model=OAuthAuthorizeResponse, dependencies=[Depends(require_permission(Permission.CONNECTORS_CREATE))])
async def authorize_connector(
    request: CreateConnectorRequest,
    http_request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
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

    expected_type = PROVIDER_METADATA.get(request.provider, {}).get("type")
    if request.type not in _KNOWN_TYPES or request.type != expected_type:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Provider {request.provider} belongs to connector type "
                f"{expected_type or 'unknown'}, not {request.type}"
            ),
        )
    
    prepared = await _start_connector_authorization(
        connector_type=request.type, provider=request.provider,
        name=request.name or PROVIDER_METADATA.get(request.provider, {}).get("name"),
        http_request=http_request, current_user=current_user, db_client=db_client,
    )
    return OAuthAuthorizeResponse(**prepared)


@router.get("/callback")
async def oauth_callback(
    request: Request,
    state: str = Query(...),
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db_client: Client = Depends(get_db_client)
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
        return RedirectResponse(_frontend_callback_url(frontend_url, status="error", error=error))
    
    if not code:
        logger.warning("OAuth callback missing code parameter")
        return RedirectResponse(_frontend_callback_url(frontend_url, status="error", error="missing_code"))
    
    try:
        # Validate state
        oauth_manager = get_oauth_state_manager()
        state_data = await oauth_manager.validate_state(state)
        
        if not state_data:
            raise OAuthStateError("Invalid or expired OAuth state")
        
        tenant_id = state_data["tenant_id"]
        provider = state_data["provider"]
        redirect_uri = state_data["redirect_uri"]
        code_verifier = state_data["code_verifier"]
        connector_id = state_data.get("connector_id")
        lifecycle = ConnectorLifecycle(db_client, actor=ConnectorActor.oauth(state_data.get("user_id")))

        if not connector_id:
            raise ValueError("Missing connector_id in state")

        # This endpoint is an UNAUTHENTICATED OAuth redirect (no get_current_user),
        # so the per-request RLS tenant context that middleware normally sets is
        # absent — every db_client.table() write below would otherwise run as the
        # NIL tenant and be silently rejected by RLS, leaving the connector stuck
        # "pending". Set it explicitly from the trusted OAuth state (same pattern
        # assistant_ws.py uses for its WebSocket writes).
        from app.core.security.tenant_isolation import set_current_tenant_id
        set_current_tenant_id(tenant_id)

        connector_row = (
            db_client.table("connectors")
            .select("id, type, provider, status, config")
            .eq("id", connector_id)
            .eq("tenant_id", tenant_id)
            .single()
            .execute()
        )
        if getattr(connector_row, "error", None) or not connector_row.data:
            logger.error("OAuth callback: connector lookup failed for %s", connector_id)
            return RedirectResponse(
                _frontend_callback_url(frontend_url, status="error", error="connector_not_found")
            )
        if connector_row.data.get("provider") != provider:
            logger.error("OAuth callback: provider mismatch for %s", connector_id)
            return RedirectResponse(
                _frontend_callback_url(frontend_url, status="error", error="connector_mismatch")
            )
        if connector_row.data.get("status") != "pending":
            return RedirectResponse(_frontend_callback_url(frontend_url, status="error", error="authorization_changed"))
        conn_type = connector_row.data.get("type")
        expected_type = PROVIDER_METADATA.get(provider, {}).get("type")
        if conn_type != expected_type:
            logger.error(
                "OAuth callback: type/provider mismatch connector=%s type=%s provider=%s",
                connector_id,
                conn_type,
                provider,
            )
            return RedirectResponse(
                _frontend_callback_url(frontend_url, status="error", error="connector_mismatch")
            )

        # Create connector instance
        connector = ConnectorFactory.create(
            provider=provider,
            tenant_id=tenant_id,
            connector_id=connector_id
        )
        if connector.connector_type != conn_type:
            logger.error(
                "OAuth callback: registered provider type mismatch connector=%s expected=%s actual=%s",
                connector_id,
                conn_type,
                connector.connector_type,
            )
            return RedirectResponse(
                _frontend_callback_url(frontend_url, status="error", error="connector_mismatch")
            )
        
        # Commit attributable intent and current authority before provider I/O.
        await lifecycle.begin_oauth(tenant_id, connector_id)
        # Exchange code for tokens
        tokens = await connector.exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier
        )
        
        if not getattr(tokens, "access_token", None):
            raise ValueError("OAuth provider returned no access token")

        granted_scopes = _normalize_scopes(tokens.scope)
        # OAuth 2.0 permits the token response to omit ``scope`` when the
        # granted set is unchanged.  Enforce omissions only when Google
        # explicitly returned a scope set; the live Gmail probe below verifies
        # read capability either way.
        missing_scopes = (
            _missing_operation_scopes(provider, granted_scopes) if granted_scopes else set()
        )
        if missing_scopes:
            logger.warning(
                "OAuth callback: required scopes missing connector=%s provider=%s missing=%s",
                connector_id,
                provider,
                sorted(missing_scopes),
            )
            await lifecycle.reject_pending(tenant_id, connector_id)
            return RedirectResponse(
                _frontend_callback_url(frontend_url, status="error", error="insufficient_scope")
            )

        # A new Gmail authorization without an offline refresh token will look
        # healthy for about an hour and then fail permanently.  Do not activate
        # that half-connection.
        if provider == "gmail" and not tokens.refresh_token:
            logger.warning(
                "OAuth callback: Gmail returned no refresh token connector=%s", connector_id
            )
            await lifecycle.reject_pending(tenant_id, connector_id)
            return RedirectResponse(
                _frontend_callback_url(frontend_url, status="error", error="missing_refresh_token")
            )

        # Verify the token against the actual Gmail resource before showing a
        # green connection.  This catches disabled APIs and denied scopes even
        # when OAuth token exchange itself succeeded.
        account_email = None
        external_account_id = None
        persisted_config: dict = {}
        await connector.set_access_token(tokens.access_token)
        if provider == "gmail":
            try:
                profile = await connector.get_profile()
                account_email = profile.get("emailAddress")
            except Exception as exc:
                logger.warning(
                    "OAuth callback: Gmail capability check failed connector=%s type=%s",
                    connector_id,
                    type(exc).__name__,
                )
                await lifecycle.reject_pending(tenant_id, connector_id)
                return RedirectResponse(
                    _frontend_callback_url(
                        frontend_url, status="error", error="capability_check_failed"
                    )
                )
        else:
            # Providers that carry state beyond the tokens (Salesforce's
            # instance_url) and/or offer an identity probe. A failed probe is
            # an unverified connection and must not activate.
            try:
                extra_config = connector.config_from_tokens(tokens)
                if isinstance(extra_config, dict):
                    persisted_config.update(extra_config)
                identity = await connector.fetch_account_identity()
                if isinstance(identity, dict):
                    account_email = identity.get("email") or None
                    external_account_id = identity.get("external_account_id") or None
                    identity_config = identity.get("config")
                    if isinstance(identity_config, dict):
                        persisted_config.update(
                            {k: v for k, v in identity_config.items() if v is not None}
                        )
            except Exception as exc:
                logger.warning(
                    "OAuth callback: %s identity check failed connector=%s type=%s",
                    provider,
                    connector_id,
                    type(exc).__name__,
                )
                await lifecycle.reject_pending(tenant_id, connector_id)
                return RedirectResponse(
                    _frontend_callback_url(
                        frontend_url, status="error", error="capability_check_failed"
                    )
                )

        # Encrypt tokens
        encryption = get_encryption_service()
        access_token_encrypted = encryption.encrypt(tokens.access_token)
        refresh_token_encrypted = encryption.encrypt(tokens.refresh_token or "")

        # Store tokens in connector_accounts
        account_data = {
            "connector_id": connector_id,
            "tenant_id": tenant_id,
            "access_token_encrypted": access_token_encrypted,
            "refresh_token_encrypted": refresh_token_encrypted,
            "token_expires_at": tokens.expires_at,
            "scopes": sorted(granted_scopes) if granted_scopes else connector.oauth_scopes,
            "account_email": account_email,
            "external_account_id": external_account_id,
            "status": "active",
            "last_refreshed_at": datetime.now(timezone.utc).isoformat(),
        }

        # One transaction installs the checked grant, preserves provider config,
        # and retires the old authorization. A disabled pending row cannot revive.
        try:
            await lifecycle.activate(
                tenant_id, connector_id, provider, conn_type, account_data, persisted_config,
                carry_keys=_SALESFORCE_RECONNECT_KEYS if provider == "salesforce" else (),
            )
        except ConnectorLifecycleError as exc:
            logger.warning("OAuth activation refused connector=%s reason=%s", connector_id, str(exc))
            return RedirectResponse(_frontend_callback_url(frontend_url, status="error", error="authorization_changed"))
        except Exception:
            logger.exception("OAuth activation could not be confirmed connector=%s", connector_id)
            return RedirectResponse(_frontend_callback_url(frontend_url, status="error", error="token_store_failed"))

        logger.info(f"OAuth completed for {provider}, connector {connector_id}")

        return RedirectResponse(_frontend_callback_url(frontend_url, status="success", provider=provider))
        
    except OAuthStateError as e:
        logger.warning(f"OAuth state error: {e}")
        return RedirectResponse(_frontend_callback_url(frontend_url, status="error", error="invalid_state"))
    except Exception as e:
        logger.error("OAuth callback failed type=%s", type(e).__name__)
        return RedirectResponse(_frontend_callback_url(frontend_url, status="error", error="callback_failed"))


# Registered AFTER /callback (and every other static single-segment route) so the
# dynamic catch-all can't shadow them. See the note where the POST /authorize
# route is declared above.
@router.get("/{connector_id}", response_model=ConnectorResponse)
async def get_connector(
    connector_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """Get a specific connector's details."""
    response = db_client.table("connectors").select(
        "id, type, provider, name, status, created_at"
    ).eq("id", connector_id).eq("tenant_id", current_user.tenant_id).single().execute()

    if not response.data:
        raise HTTPException(status_code=404, detail="Connector not found")

    conn = response.data

    # Get account email
    account_email = None
    if conn["status"] == "active":
        acc_response = db_client.table("connector_accounts").select(
            "account_email"
        ).eq("connector_id", conn["id"]).eq("status", "active").limit(1).execute()

        if acc_response.data:
            account_email = acc_response.data[0].get("account_email")

    return ConnectorResponse(
        id=str(conn["id"]),
        type=conn["type"],
        provider=conn["provider"],
        name=conn.get("name"),
        status=conn["status"],
        account_email=account_email,
        created_at=conn["created_at"]
    )


@router.delete("/{connector_id}", dependencies=[Depends(require_permission(Permission.CONNECTORS_DELETE))])
async def delete_connector(
    connector_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    *,
    request: Request,
):
    """
    Disconnect/delete a connector.
    
    Disables local authorization atomically and separately reports provider revocation.
    """
    # Verify ownership
    conn_response = db_client.table("connectors").select(
        "id, provider"
    ).eq("id", connector_id).eq("tenant_id", current_user.tenant_id).single().execute()
    
    if getattr(conn_response, "error", None):
        raise HTTPException(status_code=503, detail="Could not check the connector to disconnect")
    if not conn_response.data:
        raise HTTPException(status_code=404, detail="Connector not found")
    
    result = await _disconnect_connector(
        db_client, current_user.tenant_id, connector_id, conn_response.data["provider"], current_user=current_user, request=request,
    )

    return {"success": True, "message": "Connector disconnected", **result.receipt()}


@router.post("/{connector_id}/refresh", dependencies=[Depends(require_permission(Permission.CONNECTORS_UPDATE))])
async def refresh_connector_tokens(
    connector_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    *,
    request: Request,
):
    """
    Force refresh connector tokens.
    
    Useful when tokens are about to expire.
    """
    try:
        _, account = await ConnectorLifecycle(db_client, actor=ConnectorActor.human(
            current_user, "tenant_refresh", request=request)).refresh(str(current_user.tenant_id), connector_id)
    except ConnectorLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Refresh could not be confirmed. Check connection status; reauthorize if its outcome is unknown.") from exc
    return {"success": True, "message": "Tokens refreshed", "audit_event_id": account["audit_event_id"],
            "operation_id": account["operation_id"]}
