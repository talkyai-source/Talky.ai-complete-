"""
Assistant Model Configuration Endpoint

Per-tenant GET/PUT for the dashboard assistant's Groq model selection.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
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
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
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

    db_client.table("tenants").update({"assistant_model": body.model}).eq(
        "id", tenant_id
    ).execute()

    return {"current": body.model}


@router.get("/ws-token")
async def assistant_ws_token(
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

    from datetime import timedelta
    from app.core.jwt_security import encode_access_token

    token = encode_access_token(
        user_id=str(current_user.id),
        email=current_user.email,
        role=current_user.role,
        tenant_id=str(current_user.tenant_id),
        ttl=timedelta(minutes=2),
    )
    return {"token": token}
