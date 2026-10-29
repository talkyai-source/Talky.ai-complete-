"""
Emergency Access API Endpoints

Break-glass dual-control access for critical incidents.
"""
from datetime import datetime
import logging
from typing import Optional, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.v1.dependencies import CurrentUser, require_platform_admin, get_audit_logger, get_emergency_access
from app.core.security.emergency_access import (
    EmergencyAccess,
    EmergencyScenario,
    EmergencyStatus,
)
from app.domain.services.audit_logger import AuditEvent, AuditLogger

router = APIRouter(prefix="/admin/emergency", tags=["Emergency Access"])
logger = logging.getLogger(__name__)


class EmergencyRequest(BaseModel):
    """Emergency access request"""
    scenario: EmergencyScenario
    justification: str = Field(..., min_length=20)
    requested_access: list[str]


class EmergencyApproveRequest(BaseModel):
    """Emergency approval request"""
    method: Literal["totp"] = "totp"
    verification_code: str = Field(..., pattern=r"^[0-9]{6}$", repr=False)


class EmergencyDenyRequest(BaseModel):
    """Emergency deny request"""
    reason: str


class EmergencyReviewRequest(BaseModel):
    """Emergency review request"""
    notes: str


async def _emergency_result(operation):
    try:
        return await operation
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


async def _submit_audit(audit_logger, **event):
    # The request row already records this mutation atomically. A secondary
    # audit transport failure must not discard a one-time issued token.
    try:
        await audit_logger.log(**event)
        return True
    except Exception as exc:
        logger.warning("emergency_audit_submission_failed action=%s resource_id=%s error_type=%s",
                       event["action"], event["resource_id"], type(exc).__name__)
        return False


@router.post("/request")
async def request_emergency_access(
    http_request: Request,
    data: EmergencyRequest,
    current_user: CurrentUser = Depends(require_platform_admin),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Request emergency break-glass access"""
    request = await emergency_access.request(
        requestor_id=current_user["id"],
        scenario=data.scenario,
        justification=data.justification,
        required_access=data.requested_access,
        auth_request=http_request, current_user=current_user,
    )

    audit_submission_accepted = await _submit_audit(audit_logger,
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
        "audit_submission_accepted": audit_submission_accepted,
    }


@router.post("/{request_id}/approve")
async def approve_emergency_request(
    http_request: Request,
    request_id: UUID,
    data: EmergencyApproveRequest,
    current_user: CurrentUser = Depends(require_platform_admin),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Approve an emergency access request"""
    result = await _emergency_result(emergency_access.approve(
        request_id=request_id,
        approver_id=current_user["id"],
        method=data.method,
        verification_code=data.verification_code,
        auth_request=http_request, current_user=current_user,
    ))

    audit_submission_accepted = await _submit_audit(audit_logger,
        event_type=AuditEvent.EMERGENCY_ACCESS_APPROVED,
        action="emergency_access_approved",
        actor_id=current_user["id"],
        resource_type="emergency_request",
        resource_id=request_id,
        metadata={"approvals_received": result["approvals_received"]},
    )

    return {**result, "audit_submission_accepted": audit_submission_accepted}


@router.post("/{request_id}/deny")
async def deny_emergency_request(
    http_request: Request,
    request_id: UUID,
    data: EmergencyDenyRequest,
    current_user: CurrentUser = Depends(require_platform_admin),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Deny an emergency access request"""
    result = await _emergency_result(emergency_access.deny(
        request_id=request_id,
        denied_by=current_user["id"],
        reason=data.reason,
        auth_request=http_request, current_user=current_user,
    ))

    audit_submission_accepted = await _submit_audit(audit_logger,
        event_type=AuditEvent.EMERGENCY_ACCESS_APPROVED,  # Re-using for audit trail
        action="emergency_access_denied",
        actor_id=current_user["id"],
        resource_type="emergency_request",
        resource_id=request_id,
        metadata={"denial_reason": data.reason},
    )

    return {**result, "audit_submission_accepted": audit_submission_accepted}


@router.post("/{request_id}/session")
async def create_emergency_session(
    http_request: Request,
    request_id: UUID,
    current_user: CurrentUser = Depends(require_platform_admin),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Create emergency access session after approval"""
    # Request ownership is checked under the same row lock as issuance.
    session = await _emergency_result(emergency_access.create_session(
        request_id=request_id, auth_request=http_request, current_user=current_user,
    ))

    audit_submission_accepted = await _submit_audit(audit_logger,
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
        "audit_submission_accepted": audit_submission_accepted,
        "warning": ("Session issuance is recorded on the emergency request."
                    if audit_submission_accepted else
                    "Session issuance is recorded on the emergency request; the separate audit submission failed."),
    }


@router.delete("/{request_id}/session")
async def terminate_emergency_session(
    http_request: Request,
    request_id: UUID,
    reason: str = Query("Session completed", description="Reason for termination"),
    current_user: CurrentUser = Depends(require_platform_admin),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Terminate an emergency session early"""
    await _emergency_result(emergency_access.terminate_session(
        request_id=request_id,
        terminated_by=current_user["id"],
        reason=reason,
        auth_request=http_request, current_user=current_user,
    ))

    audit_submission_accepted = await _submit_audit(audit_logger,
        event_type=AuditEvent.EMERGENCY_ACCESS_USED,
        action="emergency_session_terminated",
        actor_id=current_user["id"],
        resource_type="emergency_request",
        resource_id=request_id,
        metadata={"termination_reason": reason},
    )

    return {"terminated": True, "audit_submission_accepted": audit_submission_accepted}


@router.get("/requests")
async def list_emergency_requests(
    http_request: Request,
    status: Optional[EmergencyStatus] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    current_user: CurrentUser = Depends(require_platform_admin),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
):
    """List emergency access requests (admin only)"""
    requests = await emergency_access.list_requests(status=status, limit=limit, offset=offset,
                                                   auth_request=http_request, current_user=current_user)

    return {
        "requests": [
            {
                "request_id": r.request_id,
                "requestor_id": r.requestor_id,
                "scenario": r.scenario.value,
                "status": r.status.value,
                "created_at": r.created_at,
                "approvals": sum(a.verification == "totp_step_up" and a.method == "totp" for a in r.approvals),
                "approvers_required": r.approvers_required,
            }
            for r in requests
        ]
    }


@router.get("/{request_id}")
async def get_emergency_request(
    http_request: Request,
    request_id: UUID,
    current_user: CurrentUser = Depends(require_platform_admin),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
):
    """Get emergency request details"""
    request = await emergency_access.get_request(request_id, auth_request=http_request, current_user=current_user)

    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

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
                "verified": a.verification == "totp_step_up" and a.method == "totp",
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
    http_request: Request,
    request_id: UUID,
    data: EmergencyReviewRequest,
    current_user: CurrentUser = Depends(require_platform_admin),
    emergency_access: EmergencyAccess = Depends(get_emergency_access),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Complete post-incident review"""
    await _emergency_result(emergency_access.complete_review(
        request_id=request_id,
        reviewed_by=current_user["id"],
        notes=data.notes,
        auth_request=http_request, current_user=current_user,
    ))

    audit_submission_accepted = await _submit_audit(audit_logger,
        event_type=AuditEvent.EMERGENCY_ACCESS_USED,
        action="emergency_review_completed",
        actor_id=current_user["id"],
        resource_type="emergency_request",
        resource_id=request_id,
    )

    return {"review_completed": True, "audit_submission_accepted": audit_submission_accepted}
