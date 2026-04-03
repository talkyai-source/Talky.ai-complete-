"""
Security Events API Endpoints

Management of high-priority security alerts requiring action.
"""
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_audit_logger, get_db_pool, require_permissions
from app.domain.services.audit_logger import AuditEvent, AuditLogger

router = APIRouter(prefix="/admin/security-events", tags=["Security Events"])


def _sla_deadline_for(severity: str) -> datetime:
    hours = {
        "CRITICAL": 4,
        "HIGH": 12,
        "MEDIUM": 24,
        "LOW": 72,
        "INFO": 168,
    }
    return datetime.utcnow() + timedelta(hours=hours.get(severity.upper(), 24))


def _row_to_response(row) -> "SecurityEventResponse":
    return SecurityEventResponse(
        event_id=row["event_id"],
        created_at=row["created_at"],
        event_type=row["event_type"],
        severity=row["severity"],
        status=row["status"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        title=row["title"],
        description=row["description"],
        detection_source=row["detection_source"],
        evidence=row["evidence"],
        assigned_to=row["assigned_to"],
        resolved_at=row["resolved_at"],
        resolved_by=row["resolved_by"],
        resolution_notes=row["resolution_notes"],
        auto_action_taken=row["auto_action_taken"],
        sla_deadline=row["sla_deadline"],
    )


class SecurityEventCreate(BaseModel):
    """Create security event request"""
    event_type: str
    severity: str = Field(..., enum=["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"])
    title: str
    description: str
    tenant_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    evidence: Optional[dict] = None


class SecurityEventUpdate(BaseModel):
    """Update security event request"""
    status: Optional[str] = Field(None, enum=["open", "investigating", "resolved", "false_positive", "escalated"])
    assigned_to: Optional[UUID] = None
    resolution_notes: Optional[str] = None


class SecurityEventResponse(BaseModel):
    """Security event response"""
    event_id: UUID
    created_at: datetime
    event_type: str
    severity: str
    status: str
    tenant_id: Optional[UUID]
    user_id: Optional[UUID]
    title: str
    description: str
    detection_source: str
    evidence: Optional[dict]
    assigned_to: Optional[UUID]
    resolved_at: Optional[datetime]
    resolved_by: Optional[UUID]
    resolution_notes: Optional[str]
    auto_action_taken: Optional[str]
    sla_deadline: Optional[datetime]


@router.get("/events", response_model=list[SecurityEventResponse])
async def list_security_events(
    status: Optional[str] = Query(None, enum=["open", "investigating", "resolved", "false_positive", "escalated"]),
    severity: Optional[str] = Query(None, enum=["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]),
    tenant_id: Optional[UUID] = None,
    assigned_to: Optional[UUID] = None,
    detection_source: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(require_permissions(["security:read"])),
    db_pool=Depends(get_db_pool),
):
    """List security events with filters"""
    conditions = []
    params = []
    param_idx = 1

    if status:
        conditions.append(f"status = ${param_idx}")
        params.append(status)
        param_idx += 1
    if severity:
        conditions.append(f"severity = ${param_idx}")
        params.append(severity)
        param_idx += 1
    scoped_tenant_id = tenant_id or current_user.get("tenant_id")
    if scoped_tenant_id:
        conditions.append(f"tenant_id = ${param_idx}")
        params.append(scoped_tenant_id)
        param_idx += 1
    if assigned_to:
        conditions.append(f"assigned_to = ${param_idx}")
        params.append(assigned_to)
        param_idx += 1
    if detection_source:
        conditions.append(f"detection_source = ${param_idx}")
        params.append(detection_source)
        param_idx += 1

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT *
        FROM security_events
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """
    params.extend([limit, offset])

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [_row_to_response(row) for row in rows]


@router.get("/events/{event_id}", response_model=SecurityEventResponse)
async def get_security_event(
    event_id: UUID,
    current_user: dict = Depends(require_permissions(["security:read"])),
    db_pool=Depends(get_db_pool),
):
    """Get a specific security event"""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM security_events WHERE event_id = $1",
            event_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    if current_user.get("tenant_id") and row["tenant_id"] != current_user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Cannot access other tenant events")
    return _row_to_response(row)


@router.post("/events")
async def create_security_event(
    data: SecurityEventCreate,
    current_user: dict = Depends(require_permissions(["security:write"])),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    db_pool=Depends(get_db_pool),
):
    """Create a new security event (typically called by automated systems)"""
    scoped_tenant_id = data.tenant_id or current_user.get("tenant_id")
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO security_events (
                event_type, severity, status, tenant_id, user_id,
                detection_source, title, description, evidence, sla_deadline
            ) VALUES ($1, $2, 'open', $3, $4, 'manual', $5, $6, $7, $8)
            RETURNING *
            """,
            data.event_type,
            data.severity,
            scoped_tenant_id,
            data.user_id,
            data.title,
            data.description,
            data.evidence,
            _sla_deadline_for(data.severity),
        )

    # Log creation
    await audit_logger.log(
        event_type=AuditEvent.SUSPICIOUS_ACTIVITY,
        action="security_event_created",
        actor_id=current_user["id"],
        tenant_id=scoped_tenant_id,
        resource_type="security_event",
        resource_id=row["event_id"],
        metadata={
            "event_type": data.event_type,
            "severity": data.severity,
            "title": data.title,
        },
    )

    return _row_to_response(row)


@router.patch("/events/{event_id}")
async def update_security_event(
    event_id: UUID,
    data: SecurityEventUpdate,
    current_user: dict = Depends(require_permissions(["security:write"])),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    db_pool=Depends(get_db_pool),
):
    """Update security event status or assignment"""
    assignments = []
    params = []
    idx = 1
    if data.status is not None:
        assignments.append(f"status = ${idx}")
        params.append(data.status)
        idx += 1
    if data.assigned_to is not None:
        assignments.append(f"assigned_to = ${idx}")
        params.append(data.assigned_to)
        idx += 1
        assignments.append(f"first_response_at = COALESCE(first_response_at, ${idx})")
        params.append(datetime.utcnow())
        idx += 1
    if data.resolution_notes is not None:
        assignments.append(f"resolution_notes = ${idx}")
        params.append(data.resolution_notes)
        idx += 1
    if not assignments:
        raise HTTPException(status_code=400, detail="No updates provided")

    params.extend([event_id])
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE security_events
               SET {', '.join(assignments)}
             WHERE event_id = ${idx}
             RETURNING *
            """,
            *params,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")

    # Log update
    await audit_logger.log(
        event_type=AuditEvent.SUSPICIOUS_ACTIVITY,
        action="security_event_updated",
        actor_id=current_user["id"],
        resource_type="security_event",
        resource_id=event_id,
        metadata={
            "status": data.status,
            "assigned_to": str(data.assigned_to) if data.assigned_to else None,
        },
    )

    return _row_to_response(row)


@router.post("/events/{event_id}/resolve")
async def resolve_security_event(
    event_id: UUID,
    resolution_notes: str,
    current_user: dict = Depends(require_permissions(["security:write"])),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    db_pool=Depends(get_db_pool),
):
    """Mark a security event as resolved"""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE security_events
               SET status = 'resolved',
                   resolved_at = $1,
                   resolved_by = $2,
                   resolution_notes = $3
             WHERE event_id = $4
             RETURNING *
            """,
            datetime.utcnow(),
            current_user["id"],
            resolution_notes,
            event_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")

    await audit_logger.log(
        event_type=AuditEvent.SUSPICIOUS_ACTIVITY,
        action="security_event_resolved",
        actor_id=current_user["id"],
        resource_type="security_event",
        resource_id=event_id,
        metadata={"resolution_notes": resolution_notes},
    )

    return _row_to_response(row)


@router.get("/alerts/open")
async def get_open_alerts(
    current_user: dict = Depends(require_permissions(["security:read"])),
    db_pool=Depends(get_db_pool),
):
    """Get open high-priority security alerts"""
    scoped_tenant_id = current_user.get("tenant_id")
    params = []
    tenant_clause = ""
    if scoped_tenant_id:
        tenant_clause = "AND tenant_id = $1"
        params.append(scoped_tenant_id)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT *
            FROM security_events
            WHERE status IN ('open', 'investigating', 'escalated')
              AND severity IN ('CRITICAL', 'HIGH')
              {tenant_clause}
            ORDER BY severity = 'CRITICAL' DESC, created_at DESC
            LIMIT 50
            """,
            *params,
        )
    return {
        "critical_count": sum(1 for row in rows if row["severity"] == "CRITICAL"),
        "high_count": sum(1 for row in rows if row["severity"] == "HIGH"),
        "overdue_count": sum(1 for row in rows if row["sla_deadline"] and row["sla_deadline"] < datetime.utcnow()),
        "alerts": [_row_to_response(row).model_dump(mode="json") for row in rows],
    }


@router.get("/alerts/overdue")
async def get_overdue_alerts(
    current_user: dict = Depends(require_permissions(["security:read"])),
    db_pool=Depends(get_db_pool),
):
    """Get alerts past their SLA deadline"""
    scoped_tenant_id = current_user.get("tenant_id")
    params = [datetime.utcnow()]
    tenant_clause = ""
    if scoped_tenant_id:
        tenant_clause = "AND tenant_id = $2"
        params.append(scoped_tenant_id)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT *
            FROM security_events
            WHERE status IN ('open', 'investigating', 'escalated')
              AND sla_deadline IS NOT NULL
              AND sla_deadline < $1
              {tenant_clause}
            ORDER BY sla_deadline ASC
            """,
            *params,
        )
    return {
        "overdue_count": len(rows),
        "alerts": [_row_to_response(row).model_dump(mode="json") for row in rows],
    }


@router.post("/events/{event_id}/escalate")
async def escalate_event(
    event_id: UUID,
    reason: str,
    current_user: dict = Depends(require_permissions(["security:escalate"])),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    db_pool=Depends(get_db_pool),
):
    """Escalate a security event to senior team"""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE security_events
               SET status = 'escalated',
                   resolution_notes = CASE
                       WHEN resolution_notes IS NULL OR resolution_notes = '' THEN $1
                       ELSE resolution_notes || E'\n\nEscalation: ' || $1
                   END
             WHERE event_id = $2
             RETURNING *
            """,
            reason,
            event_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")

    await audit_logger.log(
        event_type=AuditEvent.SUSPICIOUS_ACTIVITY,
        action="security_event_escalated",
        actor_id=current_user["id"],
        resource_type="security_event",
        resource_id=event_id,
        metadata={"escalation_reason": reason},
    )

    return _row_to_response(row)
