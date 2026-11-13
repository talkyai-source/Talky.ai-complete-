"""Durable call-history notes, lead classification and post-call form."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_current_user, get_db_pool
from app.core.db import DATABASE_RETRYABLE_ERRORS
from app.core.security.rbac import Permission, require_permission
from app.domain.services.audit_logger import AuditLogger
from app.domain.services.call_workflow_service import CallWorkflowNotFound, CallWorkflowService


router = APIRouter(prefix="/calls", tags=["call-workflow"])


def _safe_database_text(value):
    if isinstance(value, str):
        if "\x00" in value or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("Workflow text contains an unsupported character")
    return value


class WorkflowForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact: str = Field(max_length=160)
    interest: str = Field(max_length=1000)
    nextStep: str = Field(max_length=1000)
    completed: StrictBool

    @field_validator("contact", "interest", "nextStep", mode="before")
    @classmethod
    def database_text(cls, value):
        return _safe_database_text(value)


class WorkflowWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    leadType: Literal["cold", "warm", "hot", "follow_up"]
    notes: str = Field(max_length=4000)
    form: WorkflowForm
    create_only: StrictBool = False

    @field_validator("notes", mode="before")
    @classmethod
    def database_text(cls, value):
        return _safe_database_text(value)


def get_call_workflow_service(pool=Depends(get_db_pool), audit: AuditLogger = Depends(get_audit_logger)):
    return CallWorkflowService(pool, audit=audit)


def _tenant(user: CurrentUser) -> str:
    if not user.tenant_id:
        raise HTTPException(403, "No tenant context")
    return str(user.tenant_id)


@router.get("/{call_id}/workflow", dependencies=[Depends(require_permission(Permission.CALLS_READ))])
async def get_call_workflow(
    call_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: CallWorkflowService = Depends(get_call_workflow_service),
):
    tenant = _tenant(current_user)
    try:
        return {"workflow": await service.get(tenant_id=tenant, call_id=str(call_id))}
    except CallWorkflowNotFound as exc:
        raise HTTPException(404, "Call not found") from exc
    except DATABASE_RETRYABLE_ERRORS:
        raise
    except Exception as exc:
        raise HTTPException(503, "Call workflow is temporarily unavailable") from exc


@router.put("/{call_id}/workflow", dependencies=[Depends(require_permission(Permission.CALLS_CREATE))])
async def put_call_workflow(
    call_id: UUID,
    body: WorkflowWrite,
    current_user: CurrentUser = Depends(get_current_user),
    service: CallWorkflowService = Depends(get_call_workflow_service),
):
    tenant = _tenant(current_user)
    try:
        return await service.save(
            tenant_id=tenant, call_id=str(call_id), actor_id=str(current_user.id),
            actor_role=current_user.role, workflow=body.model_dump(exclude={"create_only"}),
            create_only=body.create_only,
        )
    except CallWorkflowNotFound as exc:
        raise HTTPException(404, "Call not found") from exc
    except DATABASE_RETRYABLE_ERRORS:
        raise
    except Exception as exc:
        raise HTTPException(503, "Call workflow was not saved; retry later") from exc
