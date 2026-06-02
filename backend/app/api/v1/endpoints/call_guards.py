"""
Admin Call Guards Management
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.postgres_adapter import Client
from app.api.v1.dependencies import get_db_client, require_admin, CurrentUser

router = APIRouter(prefix="/admin", tags=["Admin Call Guards"])
logger = logging.getLogger(__name__)


class CallGuardRule(BaseModel):
    id: str
    decision: str
    reason: Optional[str] = None
    enabled: bool = True
    created_at: Optional[str] = None


class ToggleCallGuardRequest(BaseModel):
    enabled: bool


@router.get("/call-guards", response_model=List[CallGuardRule])
async def list_call_guards(
    current_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
):
    """List call guard rules."""
    try:
        result = db_client.table("call_guard_decisions").select("*").limit(100).execute()
        data = result.data or []
        return [
            CallGuardRule(
                id=str(row.get("id")),
                decision=row.get("decision") or "allow",
                reason=row.get("reason") or row.get("guard_reason"),
                enabled=row.get("enabled", True),
                created_at=str(row.get("created_at")) if row.get("created_at") else None,
            )
            for row in data
        ]
    except Exception as e:
        logger.error(f"Failed to list call guards: {e}")
        return []


@router.patch("/call-guards/{rule_id}", response_model=CallGuardRule)
async def toggle_call_guard(
    rule_id: str,
    request: ToggleCallGuardRequest,
    current_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
):
    """Toggle a call guard rule."""
    try:
        result = db_client.table("call_guard_decisions").update({
            "enabled": request.enabled,
        }).eq("id", rule_id).execute()
        if result.error or not result.data:
            raise HTTPException(status_code=404, detail="Call guard rule not found")
        row = result.data[0]
        return CallGuardRule(
            id=str(row.get("id")),
            decision=row.get("decision") or "allow",
            reason=row.get("reason") or row.get("guard_reason"),
            enabled=row.get("enabled", True),
            created_at=str(row.get("created_at")) if row.get("created_at") else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle call guard: {e}")
        raise HTTPException(status_code=500, detail="Failed to toggle call guard")
