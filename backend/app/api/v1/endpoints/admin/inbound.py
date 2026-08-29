"""Platform-admin inbound operations and emergency controls."""

from __future__ import annotations

from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.api.v1.dependencies import CurrentUser, get_db_pool, require_platform_admin
from app.api.v1.schemas.inbound_campaigns import (
    AdminAssignmentVersionRequest,
    AdminInboundBillingHoldResolutionRequest,
    AdminInboundBillingHoldResolutionResponse,
    InboundReassignmentApproveRequest,
    InboundReassignmentCreateRequest,
    InboundReassignmentListResponse,
    InboundReassignmentResponse,
    PlatformInboundControlsPatch,
    PlatformInboundControlsResponse,
)
from app.domain.services.inbound_campaign_service import (
    InboundCampaignError,
    InboundCampaignService,
    InboundReadinessError,
)
from app.domain.services.telephony.inbound_admission import (
    InboundAdmissionService,
    InboundHoldResolutionConflictError,
    InboundHoldResolutionNotFoundError,
    InboundHoldResolutionRequest,
)


router = APIRouter(prefix="/inbound", tags=["admin-inbound"])


def _key(value: str = Header(..., alias="Idempotency-Key")) -> str:
    if not 8 <= len(value) <= 255:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_idempotency_key",
                "message": "Idempotency-Key must be between 8 and 255 characters",
            },
        )
    return value


def _raise_service(exc: InboundCampaignError) -> None:
    detail: dict = {"code": exc.code, "message": str(exc)}
    if isinstance(exc, InboundReadinessError):
        detail["readiness"] = exc.readiness
    raise HTTPException(status_code=exc.status_code, detail=detail) from exc


def _raise_hold_resolution(exc: Exception) -> None:
    if isinstance(exc, InboundHoldResolutionNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
        code = "inbound_hold_not_found"
    elif isinstance(exc, InboundHoldResolutionConflictError):
        status_code = status.HTTP_409_CONFLICT
        code = "inbound_hold_resolution_conflict"
    elif isinstance(exc, PermissionError):
        status_code = status.HTTP_403_FORBIDDEN
        code = "platform_admin_required"
    else:
        status_code = status.HTTP_400_BAD_REQUEST
        code = "invalid_inbound_hold_resolution"
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": str(exc)},
    ) from exc


@router.get("/overview")
async def inbound_overview(
    _user: CurrentUser = Depends(require_platform_admin),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).admin_overview()
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.post(
    "/tenants/{tenant_id}/calls/{call_id}/billing-hold/resolve",
    response_model=AdminInboundBillingHoldResolutionResponse,
)
async def resolve_inbound_billing_hold(
    tenant_id: str,
    call_id: str,
    payload: AdminInboundBillingHoldResolutionRequest,
    user: CurrentUser = Depends(require_platform_admin),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    """Release a hold, or request/approve a four-eye finalize from evidence."""

    try:
        result = await InboundAdmissionService(db_pool).resolve_billing_hold(
            InboundHoldResolutionRequest(
                call_id=call_id,
                tenant_id=tenant_id,
                hold_reason=payload.hold_reason,
                decision=payload.decision,
                evidence_type=payload.evidence_type,
                evidence_reference=payload.evidence_reference,
                evidence_sha256=payload.evidence_sha256,
                adjudication_reason=payload.adjudication_reason,
                authoritative_duration_seconds=payload.authoritative_duration_seconds,
                authoritative_cost=payload.authoritative_cost,
                authoritative_currency=payload.authoritative_currency,
                actor_id=user.id,
                actor_role=user.role,
                request_id=idempotency_key,
                approval_action=payload.approval_action,
                approval_request_id=payload.approval_request_id,
            )
        )
        return result.to_dict()
    except (
        InboundHoldResolutionConflictError,
        InboundHoldResolutionNotFoundError,
        PermissionError,
        ValueError,
    ) as exc:
        _raise_hold_resolution(exc)


@router.get("/assignments")
async def list_inbound_assignments(
    tenant_id: Optional[str] = Query(default=None),
    assignment_status: Optional[str] = Query(default=None, alias="status"),
    search: Optional[str] = Query(default=None, max_length=120),
    _user: CurrentUser = Depends(require_platform_admin),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).admin_list_assignments(
            tenant_id=tenant_id, status=assignment_status, search=search
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.get("/campaigns")
async def list_platform_inbound_campaigns(
    tenant_id: Optional[str] = Query(default=None),
    campaign_status: Optional[str] = Query(default=None, alias="status"),
    search: Optional[str] = Query(default=None, max_length=120),
    _user: CurrentUser = Depends(require_platform_admin),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).admin_list_campaigns(
            tenant_id=tenant_id, status=campaign_status, search=search
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.get("/controls", response_model=PlatformInboundControlsResponse)
async def get_platform_inbound_controls(
    _user: CurrentUser = Depends(require_platform_admin),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).get_platform_controls()
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.patch("/controls", response_model=PlatformInboundControlsResponse)
async def patch_platform_inbound_controls(
    payload: PlatformInboundControlsPatch,
    user: CurrentUser = Depends(require_platform_admin),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        result = await InboundCampaignService(db_pool).set_platform_controls(
            actor_id=user.id,
            actor_role=user.role,
            payload=payload.model_dump(),
            idempotency_key=idempotency_key,
        )
        if not result["recording_enabled"]:
            # The durable switch is authoritative. Clear sessions owned by
            # this worker now; every other media owner converges on its next
            # admission heartbeat and the uploader also rechecks fail-closed.
            from app.domain.services.telephony.lifecycle import (
                disable_live_inbound_recordings,
            )

            disable_live_inbound_recordings()
        return result
    except InboundCampaignError as exc:
        _raise_service(exc)


async def _quarantine(
    *,
    assignment_id: str,
    quarantined: bool,
    payload: AdminAssignmentVersionRequest,
    user: CurrentUser,
    idempotency_key: str,
    db_pool: asyncpg.Pool,
) -> dict:
    try:
        return await InboundCampaignService(db_pool).set_assignment_quarantine(
            assignment_id=assignment_id,
            quarantined=quarantined,
            expected_version=payload.expected_version,
            reason=payload.reason,
            actor_id=user.id,
            actor_role=user.role,
            idempotency_key=idempotency_key,
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.post("/assignments/{assignment_id}/quarantine")
async def quarantine_inbound_assignment(
    assignment_id: str,
    payload: AdminAssignmentVersionRequest,
    user: CurrentUser = Depends(require_platform_admin),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    return await _quarantine(
        assignment_id=assignment_id,
        quarantined=True,
        payload=payload,
        user=user,
        idempotency_key=idempotency_key,
        db_pool=db_pool,
    )


@router.post("/assignments/{assignment_id}/unquarantine")
async def unquarantine_inbound_assignment(
    assignment_id: str,
    payload: AdminAssignmentVersionRequest,
    user: CurrentUser = Depends(require_platform_admin),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    return await _quarantine(
        assignment_id=assignment_id,
        quarantined=False,
        payload=payload,
        user=user,
        idempotency_key=idempotency_key,
        db_pool=db_pool,
    )


@router.get("/reassignments", response_model=InboundReassignmentListResponse)
async def list_inbound_reassignments(
    reassignment_status: str = Query(default="pending", alias="status"),
    _user: CurrentUser = Depends(require_platform_admin),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).list_reassignment_requests(
            status=reassignment_status
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.post(
    "/reassignments",
    response_model=InboundReassignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_inbound_reassignment(
    payload: InboundReassignmentCreateRequest,
    user: CurrentUser = Depends(require_platform_admin),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).create_reassignment_request(
            assignment_id=payload.assignment_id,
            target_tenant_id=payload.target_tenant_id,
            target_campaign_id=payload.target_campaign_id,
            expected_version=payload.expected_version,
            reason=payload.reason,
            actor_id=user.id,
            actor_role=user.role,
            idempotency_key=idempotency_key,
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.post(
    "/reassignments/{request_id}/approve",
    response_model=InboundReassignmentResponse,
)
async def approve_inbound_reassignment(
    request_id: str,
    payload: InboundReassignmentApproveRequest,
    user: CurrentUser = Depends(require_platform_admin),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).approve_reassignment(
            request_id=request_id,
            reason=payload.reason,
            actor_id=user.id,
            actor_role=user.role,
            idempotency_key=idempotency_key,
        )
    except InboundCampaignError as exc:
        _raise_service(exc)
