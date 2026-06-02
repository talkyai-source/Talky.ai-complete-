"""
Emergency Access API Endpoints

Break-glass dual-control access for critical incidents.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_current_user, require_permissions, get_audit_logger, get_emergency_access
from app.core.security.emergency_access import (
    EmergencyAccess,
    EmergencyScenario,
)
from app.domain.services.audit_logger import AuditEvent, AuditLogger

router = APIRouter(prefix="/admin/emergency", tags=["Emergency Access"])


class EmergencyRequest(BaseModel):
    """Emergency access request"""
    scenario: EmergencyScenario
    justification: str = Field(..., min_length=20)
    requested_access: list[str]


class EmergencyApproveRequest(BaseModel):
    """Emergency approval request"""
    method: str = Field(default="totp", enum=["sms", "email", "totp", "in_person"])
    verification_code: Optional[str] = None


class EmergencyDenyRequest(BaseModel):
    """Emergency deny request"""
    reason: str


class EmergencyReviewRequest(BaseModel):
    """Emergency review request"""
    notes: str


@router.post("/request")
async def request_emergency_access(
    data: EmergencyRequest,
    current_user: dict = Depends(require_permissions(["emergency:request"])),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Request emergency break-glass access"""
    request = await emergency_access.request(
        requestor_id=current_user["id"],
        scenario=data.scenario,
        justification=data.justification,
        required_access=data.requested_access,
    )

    await audit_logger.log(
        event_type=AuditEvent.EMERGENCY_ACCESS_REQUESTED,
        action="emergency_access_requested",
        actor_id=current_user["id"],
        resource_type="emergency_request",
        resource_id=request.request_id,
        metadata={
            "scenario": data.scenario.value,
            "requested_access": data.requested_access,
        },
    )

    return {
        "request_id": request.request_id,
        "status": request.status.value,
        "approvals_required": request.approvers_required,
        "expires_at": request.expires_at,
    }


@router.post("/{request_id}/approve")
async def approve_emergency_request(
    request_id: UUID,
    data: EmergencyApproveRequest,
    current_user: dict = Depends(require_permissions(["emergency:approve"])),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Approve an emergency access request"""
    result = await emergency_access.approve(
        request_id=request_id,
        approver_id=current_user["id"],
        method=data.method,
        verification_code=data.verification_code,
    )

    if result["fully_approved"]:
        await audit_logger.log(
            event_type=AuditEvent.EMERGENCY_ACCESS_APPROVED,
            action="emergency_access_approved",
            actor_id=current_user["id"],
            resource_type="emergency_request",
            resource_id=request_id,
            metadata={"approvals_received": result["approvals_received"]},
        )

    return result


@router.post("/{request_id}/deny")
async def deny_emergency_request(
    request_id: UUID,
    data: EmergencyDenyRequest,
    current_user: dict = Depends(require_permissions(["emergency:approve"])),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Deny an emergency access request"""
    result = await emergency_access.deny(
        request_id=request_id,
        denied_by=current_user["id"],
        reason=data.reason,
    )

    await audit_logger.log(
        event_type=AuditEvent.EMERGENCY_ACCESS_APPROVED,  # Re-using for audit trail
        action="emergency_access_denied",
        actor_id=current_user["id"],
        resource_type="emergency_request",
        resource_id=request_id,
        metadata={"denial_reason": data.reason},
    )

    return result


@router.post("/{request_id}/session")
async def create_emergency_session(
    request_id: UUID,
    current_user: dict = Depends(require_permissions(["emergency:request"])),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Create emergency access session after approval"""
    # Verify requestor is the one who created the request
    request = await emergency_access.get_request(request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    if str(request.requestor_id) != current_user["id"]:
        raise HTTPException(status_code=403, detail="Only the requestor can create the session")

    session = await emergency_access.create_session(request_id=request_id)

    await audit_logger.log(
        event_type=AuditEvent.EMERGENCY_ACCESS_USED,
        action="emergency_session_created",
        actor_id=current_user["id"],
        resource_type="emergency_request",
        resource_id=request_id,
        metadata={"session_expires": session.expires_at},
    )

    return {
        "session_token": session.session_token,
        "expires_at": session.expires_at,
        "permissions": session.permissions,
        "warning": "This session is audited - all actions will be logged",
    }


@router.delete("/{request_id}/session")
async def terminate_emergency_session(
    request_id: UUID,
    reason: str = Query("Session completed", description="Reason for termination"),
    current_user: dict = Depends(require_permissions(["emergency:request", "emergency:admin"])),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Terminate an emergency session early"""
    await emergency_access.terminate_session(
        request_id=request_id,
        terminated_by=current_user["id"],
        reason=reason,
    )

    await audit_logger.log(
        event_type=AuditEvent.EMERGENCY_ACCESS_USED,
        action="emergency_session_terminated",
        actor_id=current_user["id"],
        resource_type="emergency_request",
        resource_id=request_id,
        metadata={"termination_reason": reason},
    )

    return {"terminated": True}


@router.get("/requests")
async def list_emergency_requests(
    status: Optional[str] = Query(None, enum=["pending", "approved", "denied", "expired", "used"]),
    current_user: dict = Depends(require_permissions(["emergency:admin"])),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
):
    """List emergency access requests (admin only)"""
    if status == "pending":
        requests = await emergency_access.get_pending_requests()
    else:
        # Would implement filtered query
        requests = []

    return {
        "requests": [
            {
                "request_id": r.request_id,
                "requestor_id": r.requestor_id,
                "scenario": r.scenario.value,
                "status": r.status.value,
                "created_at": r.created_at,
                "approvals": len(r.approvals),
                "approvers_required": r.approvers_required,
            }
            for r in requests
        ]
    }


@router.get("/{request_id}")
async def get_emergency_request(
    request_id: UUID,
    current_user: dict = Depends(require_permissions(["emergency:read"])),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
):
    """Get emergency request details"""
    request = await emergency_access.get_request(request_id)

    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    # Only requestor, approvers, or admins can view
    is_requestor = str(request.requestor_id) == current_user["id"]
    is_approver = str(current_user["id"]) in [str(a.approver_id) for a in request.approvals]
    is_admin = "emergency:admin" in current_user.get("permissions", [])

    if not (is_requestor or is_approver or is_admin):
        raise HTTPException(status_code=403, detail="Cannot view this request")

    return {
        "request_id": request.request_id,
        "requestor_id": request.requestor_id,
        "scenario": request.scenario.value,
        "justification": request.justification,
        "requested_access": request.requested_access,
        "status": request.status.value,
        "approvals": [
            {
                "approver_id": a.approver_id,
                "approved_at": a.approved_at,
                "method": a.method,
            }
            for a in request.approvals
        ],
        "approvers_required": request.approvers_required,
        "created_at": request.created_at,
        "approved_at": request.approved_at,
        "expires_at": request.expires_at,
        "session_created_at": request.session_created_at,
        "session_terminated_at": request.session_terminated_at,
        "actions_taken": request.actions_taken,
    }


@router.post("/{request_id}/review")
async def complete_emergency_review(
    request_id: UUID,
    data: EmergencyReviewRequest,
    current_user: dict = Depends(require_permissions(["emergency:admin"])),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Complete post-incident review"""
    await emergency_access.complete_review(
        request_id=request_id,
        reviewed_by=current_user["id"],
        notes=data.notes,
    )

    await audit_logger.log(
        event_type=AuditEvent.EMERGENCY_ACCESS_USED,
        action="emergency_review_completed",
        actor_id=current_user["id"],
        resource_type="emergency_request",
        resource_id=request_id,
    )

    return {"review_completed": True}
