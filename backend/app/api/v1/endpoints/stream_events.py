"""GET /api/v1/events — backing API for the Event Stream panel.

Returns the tenant's recent stream_events rows, paginated by
(created_at DESC, id) cursor. Frontend polls every 10 seconds.

Events are written by:
  - campaigns endpoints on start/pause/stop
  - dialer worker on batch progress + goal-reached (throttled)
  - telephony rate limiter on threshold transitions

See `app/domain/services/event_emitter.py` for the write path.
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

router = APIRouter(prefix="/events", tags=["events"])


# ----- response shapes ----------------------------------------------------


class StreamEventOut(BaseModel):
    id: str
    category: str
    title: str
    description: Optional[str] = None
    severity: Optional[str] = None
    relatedCampaignId: Optional[str] = Field(default=None, alias="related_campaign_id")
    relatedCallId: Optional[str] = Field(default=None, alias="related_call_id")
    actorUserId: Optional[str] = Field(default=None, alias="actor_user_id")
    metadata: Optional[dict[str, Any]] = None
    createdAt: str = Field(alias="created_at")

    class Config:
        populate_by_name = True


class StreamEventsResponse(BaseModel):
    items: list[StreamEventOut]
    next_cursor: Optional[str] = None


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


# ----- endpoint -----------------------------------------------------------


@router.get("", response_model=StreamEventsResponse)
async def list_stream_events(
    category: Optional[list[str]] = Query(default=None),
    severity: Optional[list[str]] = Query(default=None),
    since: Optional[str] = Query(default=None, description="ISO8601 timestamp; only return rows newer than this"),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> StreamEventsResponse:
    """List recent stream_events for the current tenant.

    Cursor-paginated (created_at, id) DESC. The frontend uses `since` for
    the polling-refresh case (only fetch rows newer than the latest one
    it already has) and falls back to `cursor` for older-pagination.
    """
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant context missing",
        )

    sql = [
        "SELECT id, category, title, description, severity,",
        "       related_campaign_id, related_call_id, actor_user_id,",
        "       metadata, created_at",
        "FROM stream_events",
        "WHERE tenant_id = $1",
    ]
    args: list[Any] = [current_user.tenant_id]

    if category:
        sql.append(f"AND category = ANY(${len(args) + 1})")
        args.append(category)
    if severity:
        sql.append(f"AND severity = ANY(${len(args) + 1})")
        args.append(severity)

    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            sql.append(f"AND created_at > ${len(args) + 1}")
            args.append(since_dt)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid 'since' timestamp",
            )

    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded:
            cur_created, cur_id = decoded
            args.extend([cur_created, cur_id])
            sql.append(
                f"AND (created_at, id) < (${len(args) - 1}, ${len(args)}::uuid)"
            )

    sql.append("ORDER BY created_at DESC, id DESC")
    sql.append(f"LIMIT ${len(args) + 1}")
    args.append(limit + 1)  # over-fetch by one to detect next page

    query = "\n".join(sql)

    async with db_client.pool.acquire() as conn:
        rows = await conn.fetch(query, *args)

    has_more = len(rows) > limit
    page = rows[:limit]

    items = [
        StreamEventOut(
            id=str(r["id"]),
            category=r["category"],
            title=r["title"],
            description=r["description"],
            severity=r["severity"],
            related_campaign_id=str(r["related_campaign_id"]) if r["related_campaign_id"] else None,
            related_call_id=str(r["related_call_id"]) if r["related_call_id"] else None,
            actor_user_id=str(r["actor_user_id"]) if r["actor_user_id"] else None,
            metadata=json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"],
            created_at=r["created_at"].isoformat(),
        )
        for r in page
    ]

    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(last["created_at"], last["id"])

    return StreamEventsResponse(items=items, next_cursor=next_cursor)
