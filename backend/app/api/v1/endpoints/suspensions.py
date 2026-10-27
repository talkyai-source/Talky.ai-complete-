"""
Suspension Management API Endpoints

User, tenant, and partner suspension with appeal workflow.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.api.v1.dependencies import require_permissions, require_platform_admin, get_audit_logger, get_suspension_service
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.suspension_service import (
    SuspensionService,
    SuspensionType,
    validate_duration_hours,
)

router = APIRouter(prefix="/admin/suspensions", tags=["Suspensions"])


class SuspendRequest(BaseModel):
    """Suspend request"""
    suspension_type: SuspensionType
    reason_category: str
    reason_description: str
    evidence: Optional[dict] = None
    duration_hours: Optional[int] = Field(default=None, gt=0, strict=True)
    notify_user: bool = True

    @field_validator("duration_hours")
    @classmethod
    def duration_is_representable(cls, value):
        return validate_duration_hours(value)


class SuspendResponse(BaseModel):
    """Suspend response"""
    suspension_id: UUID
    target_type: str
    target_id: UUID
    status: str
    propagated_to: list[str]
    propagation_failed: list[str]
    propagation_pending: list[str] = Field(default_factory=list)
    audit_event_id: Optional[UUID] = None
    changed: bool = True
    effective_status: Optional[str] = None
    remaining_hold_ids: list[UUID] = Field(default_factory=list)
    restoration_warnings: list[str] = Field(default_factory=list)
    revoked_sessions: Optional[int] = None


class RestoreRequest(BaseModel):
    """Restore request"""
    reason: str


class UserRestoreRequest(RestoreRequest):
    expected_suspension_id: Optional[UUID] = Field(default=None, description="Exact reviewed hold; Admin UI always supplies it.")


class AccountRestoreRequest(RestoreRequest):
    expected_suspension_id: UUID = Field(
        description="Exact reviewed formal hold. A stale retry cannot restore a later hold."
    )


class AppealRequest(BaseModel):
    """Appeal request"""
    appeal_reason: str = Field(min_length=1, max_length=4000)


class AppealReviewRequest(BaseModel):
    """Appeal review request"""
    decision: str = Field(..., enum=["granted", "denied"])
    response: str


class SuspensionStatusResponse(BaseModel):
    """Suspension status response"""
    target_type: str
    target_id: UUID
    status: str
    is_suspended: bool
    active_suspension: Optional[dict]
    suspension_history: list[dict]
    effective_status: Optional[str] = None
    remaining_hold_ids: list[UUID] = Field(default_factory=list)
    history_complete: bool = True
    history_limit: int = 200


# User Suspension Endpoints

@router.post("/users/{user_id}/suspend", response_model=SuspendResponse)
async def suspend_user(
    user_id: UUID,
    data: SuspendRequest,
    current_user: dict = Depends(require_permissions(["users:suspend"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Suspend a user account"""
    result = await suspension_service.suspend_user(
        user_id=user_id,
        suspension_type=data.suspension_type,
        reason_category=data.reason_category,
        reason_description=data.reason_description,
        evidence=data.evidence,
        suspended_by=current_user["id"],
        duration_hours=data.duration_hours,
        notify_user=data.notify_user,
    )

    return SuspendResponse(
        suspension_id=result.suspension_id,
        target_type=result.target_type.value,
        target_id=result.target_id,
        status=result.status,
        propagated_to=result.propagated_to,
        propagation_failed=result.propagation_failed,
        audit_event_id=result.audit_event_id,
        propagation_pending=result.propagation_pending,
    )


@router.post("/users/{user_id}/restore")
async def restore_user(
    user_id: UUID,
    data: UserRestoreRequest,
    current_user: dict = Depends(require_permissions(["users:restore"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Restore a suspended user"""
    result = await suspension_service.restore_user(
        user_id=user_id,
        restored_by=current_user["id"],
        reason=data.reason,
        expected_suspension_id=data.expected_suspension_id,
    )

    return {"restored": True, "suspension_id": result.suspension_id,
            "audit_event_id": result.audit_event_id,
            "propagated_to": result.propagated_to,
            "propagation_failed": result.propagation_failed,
            "propagation_pending": result.propagation_pending}


# The three read routes below return raw suspension_events rows (evidence,
# internal reasons, suspended_by) for ANY id, so they are platform_admin only.
@router.get("/users/{user_id}/status", response_model=SuspensionStatusResponse)
async def get_user_suspension_status(
    user_id: UUID,
    current_user: dict = Depends(require_platform_admin),
    suspension_service: SuspensionService = Depends(get_suspension_service),
):
    """Get user suspension status and history"""
    status = await suspension_service.get_status(user_id=user_id)

    return SuspensionStatusResponse(
        target_type=status.target_type.value,
        target_id=status.target_id,
        status=status.status.value,
        is_suspended=status.is_suspended,
        effective_status=status.effective_status,
        remaining_hold_ids=status.remaining_hold_ids,
        history_complete=status.history_complete,
        history_limit=status.history_limit,
        active_suspension=status.active_suspension.dict() if status.active_suspension else None,
        suspension_history=[s.dict() for s in status.suspension_history],
    )


@router.get("/users/{user_id}/history")
async def get_user_suspension_history(
    user_id: UUID,
    current_user: dict = Depends(require_platform_admin),
    suspension_service: SuspensionService = Depends(get_suspension_service),
):
    """Get user suspension history"""
    status = await suspension_service.get_status(user_id=user_id)
    return {"history": [s.dict() for s in status.suspension_history],
            "history_complete":status.history_complete,"history_limit":status.history_limit}


# Tenant Suspension Endpoints

@router.post("/tenants/{tenant_id}/suspend", response_model=SuspendResponse)
async def suspend_tenant(
    tenant_id: UUID,
    data: SuspendRequest,
    immediate: bool = Query(True, description="Terminate active sessions immediately"),
    current_user: dict = Depends(require_permissions(["tenants:suspend"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Suspend a tenant and all its users"""
    result = await suspension_service.suspend_tenant(
        tenant_id=tenant_id,
        suspension_type=data.suspension_type,
        reason_category=data.reason_category,
        reason_description=data.reason_description,
        evidence=data.evidence,
        suspended_by=current_user["id"],
        duration_hours=data.duration_hours,
        immediate=immediate,
        notify_users=data.notify_user,
    )

    return SuspendResponse(**result.model_dump())


@router.post("/tenants/{tenant_id}/restore")
async def restore_tenant(
    tenant_id: UUID,
    data: AccountRestoreRequest,
    current_user: dict = Depends(require_permissions(["tenants:restore"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Restore a suspended tenant"""
    result = await suspension_service.restore_tenant(
        tenant_id=tenant_id,
        restored_by=current_user["id"],
        reason=data.reason,
        expected_suspension_id=data.expected_suspension_id,
    )

    return {"restored": True, **result.model_dump()}


@router.get("/tenants/{tenant_id}/status", response_model=SuspensionStatusResponse)
async def get_tenant_suspension_status(
    tenant_id: UUID,
    current_user: dict = Depends(require_platform_admin),
    suspension_service: SuspensionService = Depends(get_suspension_service),
):
    """Get tenant suspension status"""
    status = await suspension_service.get_status(tenant_id=tenant_id)

    return SuspensionStatusResponse(
        target_type=status.target_type.value,
        target_id=status.target_id,
        status=status.status.value,
        is_suspended=status.is_suspended,
        effective_status=status.effective_status,
        remaining_hold_ids=status.remaining_hold_ids,
        history_complete=status.history_complete,
        history_limit=status.history_limit,
        active_suspension=status.active_suspension.dict() if status.active_suspension else None,
        suspension_history=[s.dict() for s in status.suspension_history],
    )


# Partner Suspension Endpoints

@router.get("/partners/{partner_id}/status", response_model=SuspensionStatusResponse)
async def get_partner_suspension_status(
    partner_id: UUID,
    current_user: dict = Depends(require_platform_admin),
    suspension_service: SuspensionService = Depends(get_suspension_service),
):
    status = await suspension_service.get_status(partner_id=partner_id)
    return SuspensionStatusResponse(**status.model_dump())

@router.post("/partners/{partner_id}/suspend", response_model=SuspendResponse)
async def suspend_partner(
    partner_id: UUID,
    data: SuspendRequest,
    current_user: dict = Depends(require_permissions(["partners:suspend"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Suspend a partner and all associated tenants"""
    result = await suspension_service.suspend_partner(
        partner_id=partner_id,
        suspension_type=data.suspension_type,
        reason_category=data.reason_category,
        reason_description=data.reason_description,
        evidence=data.evidence,
        suspended_by=current_user["id"],
        duration_hours=data.duration_hours,
    )

    return SuspendResponse(**result.model_dump())


@router.post("/partners/{partner_id}/restore")
async def restore_partner(
    partner_id: UUID,
    data: AccountRestoreRequest,
    current_user: dict = Depends(require_permissions(["partners:restore"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Restore a suspended partner"""
    result = await suspension_service.restore_partner(
        partner_id=partner_id,
        restored_by=current_user["id"],
        reason=data.reason,
        expected_suspension_id=data.expected_suspension_id,
    )

    return {"restored": True, **result.model_dump()}


@router.post("/{suspension_id}/appeal")
async def submit_appeal(
    suspension_id: UUID,
    data: AppealRequest,
    current_user: dict = Depends(require_platform_admin),
    suspension_service: SuspensionService = Depends(get_suspension_service),
):
    """Record an administrator-operated appeal for an exact active hold."""
    result = await suspension_service.submit_appeal(
        suspension_id=suspension_id,
        appeal_reason=data.appeal_reason,
        submitted_by=current_user["id"],
    )
    return result


@router.post("/{suspension_id}/appeal/review")
async def review_appeal(
    suspension_id: UUID,
    data: AppealReviewRequest,
    current_user: dict = Depends(require_platform_admin),
    suspension_service: SuspensionService = Depends(get_suspension_service),
):
    """Review and decide on a suspension appeal"""
    result = await suspension_service.review_appeal(
        suspension_id=suspension_id,
        reviewed_by=current_user["id"],
        decision=data.decision,
        response=data.response,
    )

    return result


# Bulk Operations

@router.post("/bulk-suspend")
async def bulk_suspend(
    criteria: dict,
    suspension_type: SuspensionType,
    reason_category: str,
    reason_description: str,
    current_user: dict = Depends(require_permissions(["admin:bulk_suspend"])),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """The dormant bulk API has no supported execution workflow."""
    raise HTTPException(status_code=501, detail="Bulk suspension is not supported. Review and suspend individual accounts.")


@router.get("/propagation-status/{suspension_id}")
async def get_propagation_status(
    suspension_id: UUID,
    current_user: dict = Depends(require_platform_admin),
    suspension_service: SuspensionService = Depends(get_suspension_service),
):
    """Read the durable hold and distinguish it from downstream acknowledgement."""
    return await suspension_service.get_propagation_status(suspension_id)
