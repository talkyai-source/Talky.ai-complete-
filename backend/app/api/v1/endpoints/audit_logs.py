"""
Audit Logs API Endpoints

Provides access to comprehensive audit trail for compliance and forensics.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.v1.dependencies import get_current_user, require_permissions
from app.domain.services.audit_logger import (
    AuditEvent,
    AuditLogger,
    EventCategory,
    Severity,
    get_audit_logger,
)

router = APIRouter(prefix="/admin/audit", tags=["Audit Logs"])


class AuditLogResponse(BaseModel):
    """Audit log entry response"""
    event_id: UUID
    event_time: datetime
    event_type: str
    event_category: str
    severity: str
    actor_id: Optional[UUID]
    actor_type: str
    tenant_id: Optional[UUID]
    resource_type: Optional[str]
    resource_id: Optional[UUID]
    action: str
    description: Optional[str]
    ip_address: Optional[str]
    user_agent: Optional[str]
    session_id: Optional[UUID]
    before_state: Optional[dict]
    after_state: Optional[dict]
    metadata: Optional[dict]
    compliance_tags: list[str]


class AuditStatsResponse(BaseModel):
    """Audit statistics response"""
    total_events: int
    events_by_type: dict[str, int]
    events_by_severity: dict[str, int]
    failed_logins_24h: int
    admin_actions_24h: int


@router.get("/logs", response_model=list[AuditLogResponse])
async def query_audit_logs(
    request: Request,
    start_date: Optional[datetime] = Query(None, description="Filter from date"),
    end_date: Optional[datetime] = Query(None, description="Filter to date"),
    event_type: Optional[AuditEvent] = Query(None, description="Filter by event type"),
    actor_id: Optional[UUID] = Query(None, description="Filter by actor"),
    tenant_id: Optional[UUID] = Query(None, description="Filter by tenant"),
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    severity: Optional[Severity] = Query(None, description="Minimum severity"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(require_permissions(["audit:read"])),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Query audit logs with filters.

    Requires: audit:read permission
    """
    # Non-platform admins can only see their tenant's logs
    user_tenant_id = current_user.get("tenant_id")
    if user_tenant_id and tenant_id and tenant_id != user_tenant_id:
        raise HTTPException(status_code=403, detail="Cannot access other tenant logs")

    if user_tenant_id:
        tenant_id = user_tenant_id

    logs = await audit_logger.query(
        start_date=start_date,
        end_date=end_date,
        event_type=event_type,
        actor_id=actor_id,
        tenant_id=tenant_id,
        resource_type=resource_type,
        severity=severity,
        limit=limit,
        offset=offset,
    )

    # Log this access
    await audit_logger.log(
        event_type=AuditEvent.RECORD_VIEWED,
        action="audit_logs_queried",
        actor_id=current_user["id"],
        tenant_id=tenant_id,
        resource_type="audit_logs",
        metadata={"filters": {"event_type": event_type.value if event_type else None, "limit": limit}},
    )

    return [
        AuditLogResponse(
            event_id=log.event_id,
            event_time=log.event_time,
            event_type=log.event_type.value if isinstance(log.event_type, AuditEvent) else log.event_type,
            event_category=log.event_category.value if isinstance(log.event_category, EventCategory) else log.event_category,
            severity=log.severity.value if isinstance(log.severity, Severity) else log.severity,
            actor_id=log.actor_id,
            actor_type=log.actor_type,
            tenant_id=log.tenant_id,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            action=log.action,
            description=log.description,
            ip_address=str(log.ip_address) if log.ip_address else None,
            user_agent=log.user_agent,
            session_id=log.session_id,
            before_state=log.before_state,
            after_state=log.after_state,
            metadata=log.metadata,
            compliance_tags=log.compliance_tags,
        )
        for log in logs
    ]


@router.get("/logs/{event_id}", response_model=AuditLogResponse)
async def get_audit_log(
    event_id: UUID,
    current_user: dict = Depends(require_permissions(["audit:read"])),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Get a specific audit log entry"""
    logs = await audit_logger.query(
        start_date=datetime.min,
        end_date=datetime.utcnow(),
        limit=1,
    )

    # Find specific log (would use direct query in production)
    log = next((l for l in logs if l.event_id == event_id), None)

    if not log:
        raise HTTPException(status_code=404, detail="Audit log not found")

    # Check tenant access
    user_tenant_id = current_user.get("tenant_id")
    if user_tenant_id and log.tenant_id and log.tenant_id != user_tenant_id:
        raise HTTPException(status_code=403, detail="Cannot access this audit log")

    return AuditLogResponse(
        event_id=log.event_id,
        event_time=log.event_time,
        event_type=log.event_type.value if isinstance(log.event_type, AuditEvent) else log.event_type,
        event_category=log.event_category.value if isinstance(log.event_category, EventCategory) else log.event_category,
        severity=log.severity.value if isinstance(log.severity, Severity) else log.severity,
        actor_id=log.actor_id,
        actor_type=log.actor_type,
        tenant_id=log.tenant_id,
        resource_type=log.resource_type,
        resource_id=log.resource_id,
        action=log.action,
        description=log.description,
        ip_address=str(log.ip_address) if log.ip_address else None,
        user_agent=log.user_agent,
        session_id=log.session_id,
        before_state=log.before_state,
        after_state=log.after_state,
        metadata=log.metadata,
        compliance_tags=log.compliance_tags,
    )


@router.post("/logs/export")
async def export_audit_logs(
    request: Request,
    start_date: datetime,
    end_date: datetime,
    tenant_id: Optional[UUID] = None,
    format: str = Query("json", enum=["json", "csv"]),
    current_user: dict = Depends(require_permissions(["audit:export"])),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Export audit logs for compliance.

    Requires: audit:export permission
    """
    # Check tenant access
    user_tenant_id = current_user.get("tenant_id")
    if user_tenant_id and tenant_id and tenant_id != user_tenant_id:
        raise HTTPException(status_code=403, detail="Cannot export other tenant logs")

    if user_tenant_id:
        tenant_id = user_tenant_id

    logs = await audit_logger.query(
        start_date=start_date,
        end_date=end_date,
        tenant_id=tenant_id,
        limit=10000,  # Max export size
    )

    # Log export
    await audit_logger.log(
        event_type=AuditEvent.RECORD_EXPORTED,
        action="audit_logs_exported",
        actor_id=current_user["id"],
        tenant_id=tenant_id,
        metadata={
            "format": format,
            "record_count": len(logs),
            "date_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        },
        compliance_tags=["soc2", "gdpr"],
    )

    if format == "json":
        return {
            "export_time": datetime.utcnow().isoformat(),
            "record_count": len(logs),
            "records": [
                {
                    "event_id": str(log.event_id),
                    "event_time": log.event_time.isoformat(),
                    "event_type": log.event_type.value if isinstance(log.event_type, AuditEvent) else log.event_type,
                    "actor_id": str(log.actor_id) if log.actor_id else None,
                    "action": log.action,
                    "description": log.description,
                }
                for log in logs
            ]
        }
    else:
        # Return CSV format info
        return {
            "export_time": datetime.utcnow().isoformat(),
            "record_count": len(logs),
            "format": "csv",
            "note": "CSV generation would be implemented with streaming response",
        }


@router.get("/stats/events-by-type")
async def get_event_stats(
    days: int = Query(30, ge=1, le=365),
    tenant_id: Optional[UUID] = None,
    current_user: dict = Depends(require_permissions(["audit:read"])),
):
    """Get event type distribution statistics"""
    # Implementation would query database for aggregates
    return {
        "period_days": days,
        "events_by_type": {},
        "note": "Statistics aggregation would be implemented",
    }


@router.get("/stats/failed-logins")
async def get_failed_login_stats(
    days: int = Query(7, ge=1, le=90),
    current_user: dict = Depends(require_permissions(["audit:read", "security:monitor"])),
):
    """Get failed login analysis"""
    return {
        "period_days": days,
        "total_failed": 0,
        "unique_ips": 0,
        "blocked_attempts": 0,
        "top_countries": [],
        "note": "Failed login analysis would be implemented",
    }


@router.get("/verify-integrity")
async def verify_audit_integrity(
    current_user: dict = Depends(require_permissions(["audit:admin"])),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Verify the integrity of the audit log chain.

    Requires: audit:admin permission
    """
    result = await audit_logger.verify_chain_integrity()

    # Log integrity check
    await audit_logger.log(
        event_type=AuditEvent.RECORD_VIEWED,
        action="audit_integrity_verified",
        actor_id=current_user["id"],
        metadata={"result": result["valid"], "entries_checked": result["entries_checked"]},
    )

    return result
