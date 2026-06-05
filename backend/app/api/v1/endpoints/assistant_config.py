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
