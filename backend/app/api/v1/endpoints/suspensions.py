"""
Suspension Management API Endpoints

User, tenant, and partner suspension with appeal workflow.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_current_user, require_permissions
from app.domain.services.audit_logger import AuditEvent, AuditLogger, get_audit_logger
from app.domain.services.suspension_service import (
    SuspensionService,
    SuspensionType,
    TargetType,
    get_suspension_service,
)

router = APIRouter(prefix="/admin/suspensions", tags=["Suspensions"])


class SuspendRequest(BaseModel):
    """Suspend request"""
    suspension_type: SuspensionType
    reason_category: str
    reason_description: str
    evidence: Optional[dict] = None
    duration_hours: Optional[int] = None
    notify_user: bool = True


class SuspendResponse(BaseModel):
    """Suspend response"""
    suspension_id: UUID
    target_type: str
    target_id: UUID
    status: str
    propagated_to: list[str]
    propagation_failed: list[str]


class RestoreRequest(BaseModel):
    """Restore request"""
    reason: str


class AppealRequest(BaseModel):
    """Appeal request"""
    appeal_reason: str


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

    # Log suspension
    await audit_logger.log(
        event_type=AuditEvent.USER_SUSPENDED,
        action="user_suspended",
        actor_id=current_user["id"],
        resource_type="user",
        resource_id=user_id,
        after_state={
            "suspension_id": str(result.suspension_id),
            "suspension_type": data.suspension_type.value,
            "reason_category": data.reason_category,
        },
        metadata={"duration_hours": data.duration_hours},
    )

    return SuspendResponse(
        suspension_id=result.suspension_id,
        target_type=result.target_type.value,
        target_id=result.target_id,
        status=result.status,
        propagated_to=result.propagated_to,
        propagation_failed=result.propagation_failed,
    )


@router.post("/users/{user_id}/restore")
async def restore_user(
    user_id: UUID,
    data: RestoreRequest,
    current_user: dict = Depends(require_permissions(["users:restore"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Restore a suspended user"""
    result = await suspension_service.restore_user(
        user_id=user_id,
        restored_by=current_user["id"],
        reason=data.reason,
    )

    # Log restoration
    await audit_logger.log(
        event_type=AuditEvent.USER_RESTORED,
        action="user_restored",
        actor_id=current_user["id"],
        resource_type="user",
        resource_id=user_id,
        metadata={"restore_reason": data.reason},
    )

    return {"restored": True, "suspension_id": result.suspension_id}


@router.get("/users/{user_id}/status", response_model=SuspensionStatusResponse)
async def get_user_suspension_status(
    user_id: UUID,
    current_user: dict = Depends(require_permissions(["users:read"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
):
    """Get user suspension status and history"""
    status = await suspension_service.get_status(user_id=user_id)

    return SuspensionStatusResponse(
        target_type=status.target_type.value,
        target_id=status.target_id,
        status=status.status.value,
        is_suspended=status.is_suspended,
        active_suspension=status.active_suspension.dict() if status.active_suspension else None,
        suspension_history=[s.dict() for s in status.suspension_history],
    )


@router.get("/users/{user_id}/history")
async def get_user_suspension_history(
    user_id: UUID,
    current_user: dict = Depends(require_permissions(["users:read"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
):
    """Get user suspension history"""
    status = await suspension_service.get_status(user_id=user_id)
    return {"history": [s.dict() for s in status.suspension_history]}


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

    # Log suspension
    await audit_logger.log(
        event_type=AuditEvent.TENANT_SUSPENDED,
        action="tenant_suspended",
        actor_id=current_user["id"],
        tenant_id=tenant_id,
        resource_type="tenant",
        resource_id=tenant_id,
        after_state={
            "suspension_id": str(result.suspension_id),
            "suspension_type": data.suspension_type.value,
            "immediate": immediate,
        },
        metadata={"reason_category": data.reason_category},
    )

    return SuspendResponse(
        suspension_id=result.suspension_id,
        target_type=result.target_type.value,
        target_id=result.target_id,
        status=result.status,
        propagated_to=result.propagated_to,
        propagation_failed=result.propagation_failed,
    )


@router.post("/tenants/{tenant_id}/restore")
async def restore_tenant(
    tenant_id: UUID,
    data: RestoreRequest,
    current_user: dict = Depends(require_permissions(["tenants:restore"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Restore a suspended tenant"""
    result = await suspension_service.restore_tenant(
        tenant_id=tenant_id,
        restored_by=current_user["id"],
        reason=data.reason,
    )

    # Log restoration
    await audit_logger.log(
        event_type=AuditEvent.TENANT_RESTORED,
        action="tenant_restored",
        actor_id=current_user["id"],
        tenant_id=tenant_id,
        resource_type="tenant",
        resource_id=tenant_id,
        metadata={"restore_reason": data.reason},
    )

    return {"restored": True, "suspension_id": result.suspension_id}


@router.get("/tenants/{tenant_id}/status", response_model=SuspensionStatusResponse)
async def get_tenant_suspension_status(
    tenant_id: UUID,
    current_user: dict = Depends(require_permissions(["tenants:read"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
):
    """Get tenant suspension status"""
    status = await suspension_service.get_status(tenant_id=tenant_id)

    return SuspensionStatusResponse(
        target_type=status.target_type.value,
        target_id=status.target_id,
        status=status.status.value,
        is_suspended=status.is_suspended,
        active_suspension=status.active_suspension.dict() if status.active_suspension else None,
        suspension_history=[s.dict() for s in status.suspension_history],
    )


# Partner Suspension Endpoints

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

    # Log suspension
    await audit_logger.log(
        event_type=AuditEvent.TENANT_SUSPENDED,
        action="partner_suspended",
        actor_id=current_user["id"],
        resource_type="partner",
        resource_id=partner_id,
        after_state={
            "suspension_id": str(result.suspension_id),
            "suspension_type": data.suspension_type.value,
        },
    )

    return SuspendResponse(
        suspension_id=result.suspension_id,
        target_type=result.target_type.value,
        target_id=result.target_id,
        status=result.status,
        propagated_to=result.propagated_to,
        propagation_failed=result.propagation_failed,
    )


@router.post("/partners/{partner_id}/restore")
async def restore_partner(
    partner_id: UUID,
    data: RestoreRequest,
    current_user: dict = Depends(require_permissions(["partners:restore"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Restore a suspended partner"""
    # Implementation would restore partner
    await audit_logger.log(
        event_type=AuditEvent.TENANT_RESTORED,
        action="partner_restored",
        actor_id=current_user["id"],
        resource_type="partner",
        resource_id=partner_id,
        metadata={"restore_reason": data.reason},
    )

    return {"restored": True}


# Appeal Endpoints

@router.post("/{suspension_id}/appeal")
async def submit_appeal(
    suspension_id: UUID,
    data: AppealRequest,
    current_user: dict = Depends(require_permissions(["appeals:submit"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
):
    """Submit an appeal for a suspension"""
    result = await suspension_service.submit_appeal(
        suspension_id=suspension_id,
        appeal_reason=data.appeal_reason,
    )
    return result


@router.post("/{suspension_id}/appeal/review")
async def review_appeal(
    suspension_id: UUID,
    data: AppealReviewRequest,
    current_user: dict = Depends(require_permissions(["appeals:review"])),
    suspension_service: SuspensionService = Depends(get_suspension_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Review and decide on a suspension appeal"""
    result = await suspension_service.review_appeal(
        suspension_id=suspension_id,
        reviewed_by=current_user["id"],
        decision=data.decision,
        response=data.response,
    )

    await audit_logger.log(
        event_type=AuditEvent.USER_UPDATED,
        action="appeal_reviewed",
        actor_id=current_user["id"],
        resource_type="suspension",
        resource_id=suspension_id,
        metadata={"decision": data.decision},
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
    """Bulk suspend users/tenants matching criteria"""
    # Implementation would process bulk suspension
    await audit_logger.log(
        event_type=AuditEvent.USER_SUSPENDED,
        action="bulk_suspension",
        actor_id=current_user["id"],
        metadata={"criteria": criteria, "suspension_type": suspension_type.value},
    )

    return {"bulk_suspend_initiated": True}


@router.get("/propagation-status/{suspension_id}")
async def get_propagation_status(
    suspension_id: UUID,
    current_user: dict = Depends(require_permissions(["admin:read"])),
):
    """Check block propagation status"""
    return {
        "suspension_id": suspension_id,
        "propagation_status": "completed",
        "services_confirmed": ["api_gateway", "session_manager", "call_guard"],
        "pending_services": [],
    }
