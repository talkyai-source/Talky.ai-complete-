"""
Assistant Model Configuration Endpoint

Per-tenant GET/PUT for the dashboard assistant's Groq model selection.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_current_user,
    get_db_client,
    require_admin_tenant,
)
from app.core.db_utils import acquire_with_tenant
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.core.postgres_adapter import Client
from app.core.security.api_security import rate_limit_dependency
from app.infrastructure.assistant.model_config import (
    ALLOWED_ASSISTANT_MODEL_IDS,
    available_models,
    get_tenant_assistant_model,
)

router = APIRouter(
    prefix="/assistant",
    tags=["assistant"],
    dependencies=[Depends(rate_limit_dependency)],
)


class ModelUpdateRequest(BaseModel):
    model: str


@router.get("/model")
async def get_assistant_model(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Return the tenant's current assistant model and the full available list."""
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No tenant_id on user")

    current = await get_tenant_assistant_model(db_client, tenant_id)
    return {"current": current, "available": available_models()}


@router.put("/model")
async def set_assistant_model(
    body: ModelUpdateRequest,
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Set the tenant's assistant model. Must be a known Groq function-calling model."""
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No tenant_id on user")

    if body.model not in ALLOWED_ASSISTANT_MODEL_IDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown model '{body.model}'. "
            f"Allowed: {sorted(ALLOWED_ASSISTANT_MODEL_IDS)}",
        )

    # Change and audit record commit together; an audit failure rolls the change back.
    async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
        await audit_logger.lock_transaction(conn)
        before = await conn.fetchrow(
            "SELECT assistant_model FROM tenants WHERE id = $1 FOR UPDATE", tenant_id
        )
        if before is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        await conn.execute(
            "UPDATE tenants SET assistant_model = $2 WHERE id = $1", tenant_id, body.model
        )
        await audit_logger.log_in_transaction(
            conn, event_type=AuditEvent.CONFIG_CHANGED, action="assistant.model.update",
            actor_id=current_user.id, actor_role=current_user.role, tenant_id=tenant_id,
            resource_type="tenant_assistant_model", resource_id=tenant_id,
            before_state={"assistant_model": before["assistant_model"]},
            after_state={"assistant_model": body.model},
        )

    return {"current": body.model}


@router.get("/ws-token")
async def assistant_ws_token(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Mint a short-lived token for the assistant WebSocket auth frame.

    The browser reliably sends the auth cookie on HTTP requests but NOT on the
    cross-origin WebSocket handshake — so the chat WS would get neither a cookie
    nor a token and close 1008 ("session expired in the assistant"). The client
    fetches this over normal authed HTTP (cookie works here) and sends the
    returned token as the first {"type":"auth","token":...} WS frame. It carries
    the SAME identity claims as the login token and is short-lived (used only to
    open the socket), so the assistant authenticates with the same global auth as
    the rest of the app instead of a separate, failing path.
    """
    if not current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No tenant_id on user")
    # The socket accepts only a session-bound token (it checks that the
    # token's ``sid`` session is still live). Minted without one, this
    # fallback could never open the socket, so the assistant showed "session
    # ended" whenever the cookie missed the handshake (QA F11-07). Bind it to
    # the session this request was verified against.
    session_id = getattr(request.state, "authenticated_session_id", None)
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your login session has ended. Please sign in again.",
        )

    from datetime import timedelta
    from app.core.jwt_security import encode_access_token

    token = encode_access_token(
        user_id=str(current_user.id),
        email=current_user.email,
        role=current_user.role,
        tenant_id=str(current_user.tenant_id),
        session_id=str(session_id),
        ttl=timedelta(minutes=2),
    )
    return {"token": token}
