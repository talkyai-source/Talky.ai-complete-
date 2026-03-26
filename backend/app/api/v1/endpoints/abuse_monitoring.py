"""
Abuse Monitoring API (Day 7)

Endpoints for viewing and managing abuse detection events.

Routes:
    GET  /api/v1/admin/abuse/events
    GET  /api/v1/admin/abuse/events/{event_id}
    POST /api/v1/admin/abuse/events/{event_id}/resolve
    GET  /api/v1/admin/abuse/statistics
    GET  /api/v1/admin/abuse/rules
    POST /api/v1/admin/abuse/rules
    PUT  /api/v1/admin/abuse/rules/{rule_id}
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/admin/abuse", tags=["Abuse Monitoring (Day 7)"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AbuseEventResponse(BaseModel):
    """Response schema for abuse event."""
    id: UUID
    tenant_id: UUID
    tenant_name: Optional[str] = None
    partner_id: Optional[UUID] = None
    event_type: str
    severity: str
    trigger_value: Optional[float]
    threshold_value: Optional[float]
    phone_number_called: Optional[str]
    call_id: Optional[UUID]
    destination_country: Optional[str]
    action_taken: str
    action_details: Optional[Dict[str, Any]]
    resolved_at: Optional[str]
    resolved_by: Optional[UUID]
    resolution_notes: Optional[str]
    false_positive: Optional[bool]
    created_at: str


class ResolveEventRequest(BaseModel):
    """Request to resolve an abuse event."""
    notes: Optional[str] = None
    false_positive: bool = False


class AbuseRuleSchema(BaseModel):
    """Schema for abuse detection rule."""
    rule_name: str = Field(..., min_length=1, max_length=255)
    rule_type: str = Field(...)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    warn_threshold: Optional[int] = None
    block_threshold: Optional[int] = None
    action_on_trigger: str = Field(default="flag")
    analysis_window_minutes: int = Field(default=60, ge=1)
    priority: int = Field(default=100, ge=1, le=1000)
    is_active: bool = True


class AbuseRuleResponse(BaseModel):
    """Response schema for abuse rule."""
    id: UUID
    tenant_id: Optional[UUID]
    rule_name: str
    rule_type: str
    parameters: Dict[str, Any]
    warn_threshold: Optional[int]
    block_threshold: Optional[int]
    action_on_trigger: str
    analysis_window_minutes: int
    is_active: bool
    priority: int
    created_at: str
    updated_at: str


class AbuseStatisticsResponse(BaseModel):
    """Response schema for abuse statistics."""
    period_hours: int
    total_events: int
    by_severity: Dict[str, int]
    by_type: Dict[str, int]
    unresolved_high_severity: int
    trend: Optional[str] = None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

async def get_db_pool():
    """Get database pool from app state."""
    from app.core.container import get_container
    return get_container().db_pool


# ---------------------------------------------------------------------------
# Abuse Events Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/events",
    response_model=List[AbuseEventResponse],
    summary="List abuse events",
    description="Get abuse detection events with filtering.",
)
async def list_abuse_events(
    tenant_id: Optional[UUID] = Query(None),
    partner_id: Optional[UUID] = Query(None),
    event_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None, regex="^(low|medium|high|critical)$"),
    unresolved_only: bool = Query(False),
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(100, ge=1, le=1000),
    db_pool=Depends(get_db_pool),
):
    """List abuse events with filtering."""
    query = """
        SELECT
            e.*,
            t.name as tenant_name
        FROM abuse_events e
        JOIN tenants t ON t.id = e.tenant_id
        WHERE e.created_at > NOW() - INTERVAL '%s hours'
    """ % hours
    params = []

    if tenant_id:
        params.append(tenant_id)
        query += f" AND e.tenant_id = ${len(params)}"

    if partner_id:
        params.append(partner_id)
        query += f" AND e.partner_id = ${len(params)}"

    if event_type:
        params.append(event_type)
        query += f" AND e.event_type = ${len(params)}"

    if severity:
        params.append(severity)
        query += f" AND e.severity = ${len(params)}"

    if unresolved_only:
        query += " AND e.resolved_at IS NULL"

    query += " ORDER BY e.created_at DESC"
    params.append(limit)
    query += f" LIMIT ${len(params)}"

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    return [
        AbuseEventResponse(
            id=r["id"],
            tenant_id=r["tenant_id"],
            tenant_name=r["tenant_name"],
            partner_id=r["partner_id"],
            event_type=r["event_type"],
            severity=r["severity"],
            trigger_value=r["trigger_value"],
            threshold_value=r["threshold_value"],
            phone_number_called=r["phone_number_called"],
            call_id=r["call_id"],
            destination_country=r["destination_country"],
            action_taken=r["action_taken"],
            action_details=r["action_details"],
            resolved_at=r["resolved_at"].isoformat() if r["resolved_at"] else None,
            resolved_by=r["resolved_by"],
            resolution_notes=r["resolution_notes"],
            false_positive=r["false_positive"],
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
    ]


@router.get(
    "/events/{event_id}",
    response_model=AbuseEventResponse,
    summary="Get abuse event details",
    description="Get detailed information about a specific abuse event.",
)
async def get_abuse_event(
    event_id: UUID,
    db_pool=Depends(get_db_pool),
):
    """Get details of a specific abuse event."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                e.*,
                t.name as tenant_name
            FROM abuse_events e
            JOIN tenants t ON t.id = e.tenant_id
            WHERE e.id = $1
            """,
            event_id,
        )

    if not row:
        raise HTTPException(status_code=404, detail="Abuse event not found")

    return AbuseEventResponse(
        id=row["id"],
        tenant_id=row["tenant_id"],
        tenant_name=row["tenant_name"],
        partner_id=row["partner_id"],
        event_type=row["event_type"],
        severity=row["severity"],
        trigger_value=row["trigger_value"],
        threshold_value=row["threshold_value"],
        phone_number_called=row["phone_number_called"],
        call_id=row["call_id"],
        destination_country=row["destination_country"],
        action_taken=row["action_taken"],
        action_details=row["action_details"],
        resolved_at=row["resolved_at"].isoformat() if row["resolved_at"] else None,
        resolved_by=row["resolved_by"],
        resolution_notes=row["resolution_notes"],
        false_positive=row["false_positive"],
        created_at=row["created_at"].isoformat(),
    )


@router.post(
    "/events/{event_id}/resolve",
    response_model=AbuseEventResponse,
    summary="Resolve abuse event",
    description="Mark an abuse event as resolved with optional notes.",
)
async def resolve_abuse_event(
    event_id: UUID,
    request: ResolveEventRequest,
    resolved_by: UUID,  # In production, get from auth context
    db_pool=Depends(get_db_pool),
):
    """Resolve an abuse event."""
    async with db_pool.acquire() as conn:
        # Update the event
        result = await conn.execute(
            """
            UPDATE abuse_events
            SET resolved_at = NOW(),
                resolved_by = $2,
                resolution_notes = $3,
                false_positive = $4
            WHERE id = $1 AND resolved_at IS NULL
            """,
            event_id,
            resolved_by,
            request.notes,
            request.false_positive,
        )

        if "UPDATE 0" in result:
            raise HTTPException(
                status_code=404,
                detail="Abuse event not found or already resolved"
            )

        # Fetch updated record
        row = await conn.fetchrow(
            """
            SELECT
                e.*,
                t.name as tenant_name
            FROM abuse_events e
            JOIN tenants t ON t.id = e.tenant_id
            WHERE e.id = $1
            """,
            event_id,
        )

    return AbuseEventResponse(
        id=row["id"],
        tenant_id=row["tenant_id"],
        tenant_name=row["tenant_name"],
        partner_id=row["partner_id"],
        event_type=row["event_type"],
        severity=row["severity"],
        trigger_value=row["trigger_value"],
        threshold_value=row["threshold_value"],
        phone_number_called=row["phone_number_called"],
        call_id=row["call_id"],
        destination_country=row["destination_country"],
        action_taken=row["action_taken"],
        action_details=row["action_details"],
        resolved_at=row["resolved_at"].isoformat() if row["resolved_at"] else None,
        resolved_by=row["resolved_by"],
        resolution_notes=row["resolution_notes"],
        false_positive=row["false_positive"],
        created_at=row["created_at"].isoformat(),
    )


# ---------------------------------------------------------------------------
# Statistics Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/statistics",
    response_model=AbuseStatisticsResponse,
    summary="Get abuse statistics",
    description="Get aggregated abuse detection statistics.",
)
async def get_abuse_statistics(
    tenant_id: Optional[UUID] = Query(None),
    hours: int = Query(24, ge=1, le=720),
    db_pool=Depends(get_db_pool),
):
    """Get abuse detection statistics."""
    async with db_pool.acquire() as conn:
        # Base query conditions
        tenant_filter = "AND tenant_id = $1" if tenant_id else ""
        params = [tenant_id] if tenant_id else []

        # Total events
        total_query = f"""
            SELECT COUNT(*)
            FROM abuse_events
            WHERE created_at > NOW() - INTERVAL '{hours} hours'
            {tenant_filter}
        """
        total = await conn.fetchval(total_query, *params) or 0

        # By severity
        severity_query = f"""
            SELECT severity, COUNT(*) as count
            FROM abuse_events
            WHERE created_at > NOW() - INTERVAL '{hours} hours'
            {tenant_filter}
            GROUP BY severity
        """
        severity_rows = await conn.fetch(severity_query, *params)
        by_severity = {r["severity"]: r["count"] for r in severity_rows}

        # By type
        type_query = f"""
            SELECT event_type, COUNT(*) as count
            FROM abuse_events
            WHERE created_at > NOW() - INTERVAL '{hours} hours'
            {tenant_filter}
            GROUP BY event_type
        """
        type_rows = await conn.fetch(type_query, *params)
        by_type = {r["event_type"]: r["count"] for r in type_rows}

        # Unresolved high severity
        unresolved_query = f"""
            SELECT COUNT(*)
            FROM abuse_events
            WHERE severity IN ('high', 'critical')
              AND resolved_at IS NULL
              {tenant_filter}
        """
        unresolved = await conn.fetchval(unresolved_query, *params) or 0

        # Calculate trend (compare to previous period)
        trend = "stable"
        if hours <= 24:
            prev_query = f"""
                SELECT COUNT(*)
                FROM abuse_events
                WHERE created_at BETWEEN NOW() - INTERVAL '{hours * 2} hours'
                                     AND NOW() - INTERVAL '{hours} hours'
                {tenant_filter}
            """
            prev_count = await conn.fetchval(prev_query, *params) or 0
            if prev_count > 0:
                change = (total - prev_count) / prev_count * 100
                if change > 50:
                    trend = "spiking"
                elif change > 20:
                    trend = "increasing"
                elif change < -50:
                    trend = "decreasing"
                elif change < -20:
                    trend = "improving"

    return AbuseStatisticsResponse(
        period_hours=hours,
        total_events=total,
        by_severity=by_severity,
        by_type=by_type,
        unresolved_high_severity=unresolved,
        trend=trend,
    )


# ---------------------------------------------------------------------------
# Abuse Rules Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/rules",
    response_model=List[AbuseRuleResponse],
    summary="List abuse detection rules",
    description="Get abuse detection rules (global and tenant-specific).",
)
async def list_abuse_rules(
    tenant_id: Optional[UUID] = Query(None, description="NULL for global rules"),
    rule_type: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    db_pool=Depends(get_db_pool),
):
    """List abuse detection rules."""
    query = "SELECT * FROM abuse_detection_rules WHERE 1=1"
    params = []

    if tenant_id is not None:
        params.append(tenant_id)
        query += f" AND tenant_id = ${len(params)}"
    else:
        query += " AND tenant_id IS NULL"

    if rule_type:
        params.append(rule_type)
        query += f" AND rule_type = ${len(params)}"

    if is_active is not None:
        params.append(is_active)
        query += f" AND is_active = ${len(params)}"

    query += " ORDER BY priority ASC, created_at DESC"

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    return [
        AbuseRuleResponse(
            id=r["id"],
            tenant_id=r["tenant_id"],
            rule_name=r["rule_name"],
            rule_type=r["rule_type"],
            parameters=r["parameters"] or {},
            warn_threshold=r["warn_threshold"],
            block_threshold=r["block_threshold"],
            action_on_trigger=r["action_on_trigger"],
            analysis_window_minutes=r["analysis_window_minutes"],
            is_active=r["is_active"],
            priority=r["priority"],
            created_at=r["created_at"].isoformat(),
            updated_at=r["updated_at"].isoformat(),
        )
        for r in rows
    ]


@router.post(
    "/rules",
    response_model=AbuseRuleResponse,
    summary="Create abuse detection rule",
    description="Create a new abuse detection rule.",
    status_code=status.HTTP_201_CREATED,
)
async def create_abuse_rule(
    rule: AbuseRuleSchema,
    tenant_id: Optional[UUID] = Query(None, description="NULL for global rule"),
    db_pool=Depends(get_db_pool),
):
    """Create a new abuse detection rule."""
    import json

    # Validate rule_type
    valid_types = {
        "velocity_spike", "short_duration_pattern", "repeat_number",
        "sequential_dialing", "premium_rate", "international_spike",
        "after_hours", "geographic_impossibility", "account_hopping",
        "toll_fraud", "wangiri", "irs_fraud"
    }
    if rule.rule_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid rule_type. Must be one of: {valid_types}"
        )

    # Validate action
    valid_actions = {"flag", "warn", "throttle", "block", "suspend"}
    if rule.action_on_trigger not in valid_actions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action_on_trigger. Must be one of: {valid_actions}"
        )

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO abuse_detection_rules (
                tenant_id,
                rule_name,
                rule_type,
                parameters,
                warn_threshold,
                block_threshold,
                action_on_trigger,
                analysis_window_minutes,
                priority,
                is_active
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING *
            """,
            tenant_id,
            rule.rule_name,
            rule.rule_type,
            json.dumps(rule.parameters),
            rule.warn_threshold,
            rule.block_threshold,
            rule.action_on_trigger,
            rule.analysis_window_minutes,
            rule.priority,
            rule.is_active,
        )

    return AbuseRuleResponse(
        id=row["id"],
        tenant_id=row["tenant_id"],
        rule_name=row["rule_name"],
        rule_type=row["rule_type"],
        parameters=row["parameters"] or {},
        warn_threshold=row["warn_threshold"],
        block_threshold=row["block_threshold"],
        action_on_trigger=row["action_on_trigger"],
        analysis_window_minutes=row["analysis_window_minutes"],
        is_active=row["is_active"],
        priority=row["priority"],
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


@router.put(
    "/rules/{rule_id}",
    response_model=AbuseRuleResponse,
    summary="Update abuse detection rule",
    description="Update an existing abuse detection rule.",
)
async def update_abuse_rule(
    rule_id: UUID,
    rule: AbuseRuleSchema,
    db_pool=Depends(get_db_pool),
):
    """Update an abuse detection rule."""
    import json

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE abuse_detection_rules
            SET rule_name = $2,
                rule_type = $3,
                parameters = $4,
                warn_threshold = $5,
                block_threshold = $6,
                action_on_trigger = $7,
                analysis_window_minutes = $8,
                priority = $9,
                is_active = $10,
                updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            rule_id,
            rule.rule_name,
            rule.rule_type,
            json.dumps(rule.parameters),
            rule.warn_threshold,
            rule.block_threshold,
            rule.action_on_trigger,
            rule.analysis_window_minutes,
            rule.priority,
            rule.is_active,
        )

    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")

    return AbuseRuleResponse(
        id=row["id"],
        tenant_id=row["tenant_id"],
        rule_name=row["rule_name"],
        rule_type=row["rule_type"],
        parameters=row["parameters"] or {},
        warn_threshold=row["warn_threshold"],
        block_threshold=row["block_threshold"],
        action_on_trigger=row["action_on_trigger"],
        analysis_window_minutes=row["analysis_window_minutes"],
        is_active=row["is_active"],
        priority=row["priority"],
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


@router.delete(
    "/rules/{rule_id}",
    summary="Delete abuse detection rule",
    description="Delete an abuse detection rule.",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_abuse_rule(
    rule_id: UUID,
    db_pool=Depends(get_db_pool),
):
    """Delete an abuse detection rule."""
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM abuse_detection_rules WHERE id = $1",
            rule_id,
        )

    if "DELETE 0" in result:
        raise HTTPException(status_code=404, detail="Rule not found")


# ---------------------------------------------------------------------------
# Alert/Notification Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/alerts",
    summary="Get recent abuse alerts",
    description="Get recent high-priority abuse events requiring attention.",
)
async def get_abuse_alerts(
    tenant_id: Optional[UUID] = Query(None),
    min_severity: str = Query("high", regex="^(medium|high|critical)$"),
    limit: int = Query(20, ge=1, le=100),
    db_pool=Depends(get_db_pool),
):
    """Get recent abuse alerts."""
    severity_order = {"medium": 1, "high": 2, "critical": 3}
    min_level = severity_order.get(min_severity, 2)

    query = """
        SELECT
            e.*,
            t.name as tenant_name
        FROM abuse_events e
        JOIN tenants t ON t.id = e.tenant_id
        WHERE e.resolved_at IS NULL
          AND CASE e.severity
              WHEN 'critical' THEN 3
              WHEN 'high' THEN 2
              WHEN 'medium' THEN 1
              ELSE 0
          END >= $1
    """
    params = [min_level]

    if tenant_id:
        params.append(tenant_id)
        query += f" AND e.tenant_id = ${len(params)}"

    query += " ORDER BY e.created_at DESC"
    params.append(limit)
    query += f" LIMIT ${len(params)}"

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    return [
        {
            "id": str(r["id"]),
            "tenant_id": str(r["tenant_id"]),
            "tenant_name": r["tenant_name"],
            "event_type": r["event_type"],
            "severity": r["severity"],
            "phone_number": r["phone_number_called"],
            "action_taken": r["action_taken"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
