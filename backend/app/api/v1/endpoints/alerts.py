"""Endpoints backing the Alert Timeline panel on /campaigns.

Reads / mutates `security_events` rows where `alert_type IS NOT NULL`
(see migration 0004). Legacy security-only rows stay invisible to the
user-facing timeline.

- GET  /alerts                         — list (filtered, paginated)
- POST /alerts/{alert_id}/ack          — assign to current user, status='investigating'
- POST /alerts/{alert_id}/resolve      — status='resolved', resolved_at=NOW(), notes
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])


# ----- shapes --------------------------------------------------------------

# Frontend uses Title-Case status/severity; DB uses lower-case. Translate
# at the boundary so both sides stay idiomatic.
_DB_TO_UI_STATUS = {"open": "Active", "investigating": "Investigating", "resolved": "Resolved"}
_UI_TO_DB_STATUS = {v: k for k, v in _DB_TO_UI_STATUS.items()}
_DB_TO_UI_SEVERITY = {
    "CRITICAL": "Critical",
    "HIGH": "Critical",
    "MEDIUM": "Warning",
    "LOW": "Info",
    "INFO": "Info",
}
_UI_TO_DB_SEVERITY = {"Critical": "CRITICAL", "Warning": "MEDIUM", "Info": "INFO"}


class AlertOut(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    severity: str       # 'Critical' | 'Warning' | 'Info'
    type: str           # 'Network' | 'API' | 'Campaign' | 'System'
    status: str         # 'Active' | 'Investigating' | 'Resolved'
    createdAt: str
    updatedAt: str
    acknowledged: bool
    relatedCampaignIds: list[str] = Field(default_factory=list)
    metadata: Optional[dict[str, Any]] = None
    resolutionNotes: Optional[str] = None


class AlertsListResponse(BaseModel):
    items: list[AlertOut]
    next_cursor: Optional[str] = None


class AckRequest(BaseModel):
    note: Optional[str] = None


class ResolveRequest(BaseModel):
    resolution_notes: str


# ----- cursor helpers ------------------------------------------------------


def _encode_cursor(created_at: datetime, row_id: str) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)])
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> Optional[tuple[datetime, str]]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        [iso, row_id] = json.loads(decoded)
        return datetime.fromisoformat(iso), row_id
    except Exception:  # noqa: BLE001
        return None


# ----- row → AlertOut ------------------------------------------------------


def _row_to_alert(row: dict[str, Any]) -> AlertOut:
    db_status = (row["status"] or "open").lower()
    db_severity = (row["severity"] or "INFO").upper()
    evidence = row["evidence"]
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except Exception:  # noqa: BLE001
            evidence = None

    related_ids: list[str] = []
    if isinstance(evidence, dict):
        rc = evidence.get("related_campaign_id")
        if isinstance(rc, str) and rc:
            related_ids = [rc]

    updated_at = row.get("resolved_at") or row.get("created_at")

    return AlertOut(
        id=str(row["event_id"]),
        title=row["title"] or "",
        description=row.get("description"),
        severity=_DB_TO_UI_SEVERITY.get(db_severity, "Info"),
        type=row["alert_type"] or "System",
        status=_DB_TO_UI_STATUS.get(db_status, "Active"),
        createdAt=row["created_at"].isoformat(),
        updatedAt=updated_at.isoformat() if updated_at else row["created_at"].isoformat(),
        acknowledged=(db_status != "open") or (row.get("assigned_to") is not None),
        relatedCampaignIds=related_ids,
        metadata=evidence if isinstance(evidence, dict) else None,
        resolutionNotes=row.get("resolution_notes"),
    )


# ----- endpoints -----------------------------------------------------------


@router.get("", response_model=AlertsListResponse)
async def list_alerts(
    severity: Optional[list[str]] = Query(default=None, description="Critical|Warning|Info"),
    alert_status: Optional[list[str]] = Query(default=None, alias="status", description="Active|Investigating|Resolved"),
    alert_type: Optional[list[str]] = Query(default=None, description="Network|API|Campaign|System"),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> AlertsListResponse:
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context missing")

    sql = [
        "SELECT event_id, created_at, severity, status, title, description,",
        "       evidence, assigned_to, resolved_at, resolution_notes, alert_type",
        "FROM security_events",
        "WHERE tenant_id = $1",
        "  AND alert_type IS NOT NULL",
    ]
    args: list[Any] = [current_user.tenant_id]

    if severity:
        db_severities = [_UI_TO_DB_SEVERITY.get(s, s) for s in severity]
        sql.append(f"AND severity = ANY(${len(args) + 1})")
        args.append(db_severities)
    if alert_status:
        db_statuses = [_UI_TO_DB_STATUS.get(s, s.lower()) for s in alert_status]
        sql.append(f"AND status = ANY(${len(args) + 1})")
        args.append(db_statuses)
    if alert_type:
        sql.append(f"AND alert_type = ANY(${len(args) + 1})")
        args.append(alert_type)

    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded:
            cur_created, cur_id = decoded
            args.extend([cur_created, cur_id])
            sql.append(
                f"AND (created_at, event_id) < (${len(args) - 1}, ${len(args)}::uuid)"
            )

    sql.append("ORDER BY created_at DESC, event_id DESC")
    sql.append(f"LIMIT ${len(args) + 1}")
    args.append(limit + 1)

    query = "\n".join(sql)
    async with db_client.pool.acquire() as conn:
        rows = await conn.fetch(query, *args)

    has_more = len(rows) > limit
    page = rows[:limit]

    items = [_row_to_alert(dict(r)) for r in page]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(last["created_at"], last["event_id"])

    return AlertsListResponse(items=items, next_cursor=next_cursor)


@router.post("/{alert_id}/ack", response_model=AlertOut)
async def ack_alert(
    alert_id: str,
    body: AckRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> AlertOut:
    """Move an alert from 'open' to 'investigating' and stamp the assignee."""
    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE security_events
            SET status = 'investigating',
                assigned_to = $3
            WHERE event_id = $1
              AND tenant_id = $2
              AND alert_type IS NOT NULL
              AND status = 'open'
            RETURNING event_id, created_at, severity, status, title, description,
                      evidence, assigned_to, resolved_at, resolution_notes, alert_type
            """,
            alert_id,
            current_user.tenant_id,
            current_user.id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Alert not found or not actionable")
        # If a note was supplied, append it to resolution_notes (so the audit
        # trail accumulates rather than overwrites).
        if body.note:
            await conn.execute(
                "UPDATE security_events SET resolution_notes = "
                "  COALESCE(resolution_notes || E'\\n', '') || $2 "
                "WHERE event_id = $1",
                alert_id,
                f"[ack by {current_user.id}] {body.note}",
            )
            row = await conn.fetchrow(
                """
                SELECT event_id, created_at, severity, status, title, description,
                       evidence, assigned_to, resolved_at, resolution_notes, alert_type
                FROM security_events WHERE event_id = $1
                """,
                alert_id,
            )

    return _row_to_alert(dict(row))


@router.post("/{alert_id}/resolve", response_model=AlertOut)
async def resolve_alert(
    alert_id: str,
    body: ResolveRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> AlertOut:
    """Mark resolved, stamp resolved_at and resolution_notes."""
    if not body.resolution_notes.strip():
        raise HTTPException(status_code=400, detail="resolution_notes is required")

    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE security_events
            SET status = 'resolved',
                resolved_at = NOW(),
                resolution_notes = $3
            WHERE event_id = $1
              AND tenant_id = $2
              AND alert_type IS NOT NULL
              AND status <> 'resolved'
            RETURNING event_id, created_at, severity, status, title, description,
                      evidence, assigned_to, resolved_at, resolution_notes, alert_type
            """,
            alert_id,
            current_user.tenant_id,
            body.resolution_notes,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Alert not found or already resolved")

    return _row_to_alert(dict(row))
