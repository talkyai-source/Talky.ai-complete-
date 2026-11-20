"""Bulk read of call-history notes, lead type and form (one request per export)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.db import DATABASE_RETRYABLE_ERRORS
from app.core.security.rbac import Permission, require_permission
from app.domain.services.call_workflow_batch import read_workflows


router = APIRouter(prefix="/calls", tags=["call-workflow"])


class WorkflowBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_ids: list[UUID] = Field(min_length=1, max_length=200)


class SavedWorkflowForm(BaseModel):
    contact: str
    interest: str
    nextStep: str
    completed: bool


class SavedWorkflow(BaseModel):
    """The same object the single read returns inside "workflow"."""
    leadType: str
    notes: str
    form: SavedWorkflowForm
    updatedAt: str


class WorkflowBatchResponse(BaseModel):
    items: dict[str, SavedWorkflow]


@router.post(
    "/workflow/batch",
    response_model=WorkflowBatchResponse,
    dependencies=[Depends(require_permission(Permission.CALLS_READ))],
)
async def get_call_workflows_batch(
    body: WorkflowBatchRequest,
    current_user: CurrentUser = Depends(get_current_user),
    pool=Depends(get_db_pool),
):
    if not current_user.tenant_id:
        raise HTTPException(403, "No tenant context")
    try:
        items = await read_workflows(
            pool, tenant_id=str(current_user.tenant_id), call_ids=body.call_ids)
    except DATABASE_RETRYABLE_ERRORS:
        raise
    except Exception as exc:
        raise HTTPException(503, "Call workflow is temporarily unavailable") from exc
    return {"items": items}
