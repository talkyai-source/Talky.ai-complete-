"""
Call History Endpoints
Provides paginated call list and individual call details
"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.utils.tenant_filter import apply_tenant_filter, verify_tenant_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["calls"])


class CallListItem(BaseModel):
    """Call list item (summary)"""
    id: str
    talklee_call_id: Optional[str] = None
    timestamp: str
    to_number: str
    status: str
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None
    campaign_name: Optional[str] = None
    summary: Optional[str] = None
    recording_id: Optional[str] = None
    # AI per-call verdict from the post-call summary (e.g. "qualified | …",
    # "callback | …", "no_interest | …") — the "was this call a success" answer.
    lead_outcome: Optional[str] = None


class CallDetail(BaseModel):
    """Full call details"""
    id: str
    talklee_call_id: Optional[str] = None
    timestamp: str
    to_number: str
    status: str
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None
    transcript: Optional[str] = None
    recording_id: Optional[str] = None
    campaign_id: Optional[str] = None
    lead_id: Optional[str] = None
    summary: Optional[str] = None
    summary_json: Optional[dict] = None


class CallListResponse(BaseModel):
    """Paginated call list response"""
    items: List[CallListItem]
    page: int
    page_size: int
    total: int


class CallIssueItem(BaseModel):
    """One stuck/failed dial attempt, explained for the operator.

    These come from ``dialer_jobs`` (NOT ``calls``) because the gates that
    stop a call — out of minutes, outside hours, caller-ID unverified, TTS
    warmup failure, rate limits — all fire before a ``calls`` row exists.
    """
    job_id: str
    phone_number: str
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    status: str
    reason_code: Optional[str] = None
    category: Optional[str] = None
    title: str
    suggestion: str
    severity: str   # error | warning | info
    stage: str
    attempts: int = 0
    updated_at: Optional[str] = None


class CallIssuesResponse(BaseModel):
    items: List[CallIssueItem]
    server_time: str


class LiveCallItem(BaseModel):
    """Snapshot of one currently-in-flight call for the live panel.

    Shape is intentionally lean — the live panel polls every 2s and
    renders dozens of these. Anything not strictly needed for the live
    row (transcript, recording, full metadata) belongs on CallDetail.
    """
    id: str
    talklee_call_id: Optional[str] = None
    to_number: str
    status: str                          # CallState value
    started_at: Optional[str] = None
    answered_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    lead_id: Optional[str] = None
    caller_id: Optional[str] = None      # the FROM number used


class LiveCallsResponse(BaseModel):
    items: List[LiveCallItem]
    server_time: str                     # so the FE can compute elapsed
                                          # times even if its clock drifts


# Statuses that count as "in flight" for the live panel. Old finalised
# rows (ended/completed/failed) only show up if they ended very recently
# (see `recent_window_seconds` below).
_LIVE_STATUSES = ("queued", "dialing", "ringing", "answered", "in_call", "initiated")

# Upper bound on how old a still-"live"-status call may be before we treat it as
# a phantom (crashed worker / missed hangup / stopped campaign) and stop showing
# it. No real AI call runs this long, so anything older is stale DB state, not a
# live call. This is what keeps the panel "exact" — without it a call stuck in
# dialing/in_call lingers in the feed forever.
_LIVE_MAX_AGE_MINUTES = 30


@router.post("/{call_id}/hangup")
async def hangup_live_call(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Hang up a single in-flight call from the live panel (operator action).

    Tenant-scoped. Best-effort drops the telephony channel, then marks the row
    ended so the panel reflects it immediately (this also clears phantom stuck
    rows whose channel is already gone). Never overwrites an outcome the ARI
    callback already recorded.
    """
    if not current_user.tenant_id:
        raise HTTPException(status_code=403, detail="No tenant context")
    try:
        async with db_client.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT external_call_uuid, status FROM calls "
                "WHERE id = $1 AND tenant_id = $2",
                call_id, current_user.tenant_id,
            )
        if not row:
            raise HTTPException(status_code=404, detail="Call not found")

        ext = row["external_call_uuid"]
        if ext:
            try:
                from app.api.v1.endpoints import telephony_bridge as tb
                if tb._adapter is not None:
                    await tb._adapter.hangup(ext)
            except Exception as exc:
                # Channel may already be gone — fall through to mark it ended.
                logger.warning("hangup_live_call adapter hangup failed call=%s: %s", call_id, exc)

        async with db_client.pool.acquire() as conn:
            await conn.execute(
                "UPDATE calls SET status = 'ended', "
                "ended_at = COALESCE(ended_at, NOW()), "
                "outcome = COALESCE(outcome, 'agent_hung_up') "
                "WHERE id = $1 AND tenant_id = $2",
                call_id, current_user.tenant_id,
            )
        return {"status": "ok", "call_id": call_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("hangup_live_call failed call=%s: %s", call_id, exc)
        raise HTTPException(status_code=500, detail="Failed to hang up call")


@router.get("/live", response_model=LiveCallsResponse)
async def list_live_calls(
    campaign_id: Optional[str] = Query(
        None,
        description="If set, restrict to this campaign. Otherwise all of the user's calls in flight.",
    ),
    recent_window_seconds: int = Query(
        60, ge=0, le=600,
        description="Also include calls that ended within this many seconds. "
                    "Keeps the panel showing the outcome briefly before the row vanishes.",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Snapshot of every call currently in flight for the tenant.

    Designed to be polled every 1-2s by a frontend live panel. Returns
    the union of:
      * calls whose status is one of `_LIVE_STATUSES`, and
      * calls that ended within `recent_window_seconds` seconds.

    Tenant scope is enforced via `apply_tenant_filter` AND the
    SELECT runs through the RLS-protected pool — same defence-in-depth
    pattern the list endpoint uses.
    """
    from datetime import datetime, timezone
    if not current_user.tenant_id:
        return LiveCallsResponse(items=[], server_time=datetime.now(timezone.utc).isoformat())

    placeholders = ", ".join(f"${i+1}" for i in range(len(_LIVE_STATUSES)))
    # Note: status IN (...) OR (ended within window) — single SELECT
    # so the panel gets a coherent snapshot.
    args: list = list(_LIVE_STATUSES)
    args.append(current_user.tenant_id)
    args.append(recent_window_seconds)
    where_campaign = ""
    if campaign_id:
        where_campaign = f" AND c.campaign_id = ${len(args) + 1}"
        args.append(campaign_id)

    sql = f"""
        SELECT c.id, c.talklee_call_id, c.phone_number AS to_number,
               c.status, c.started_at, c.answered_at, c.ended_at,
               c.duration_seconds, c.outcome, c.campaign_id, c.lead_id,
               camp.name AS campaign_name,
               t.calling_rules->>'caller_id' AS caller_id
        FROM   calls c
        LEFT   JOIN campaigns camp ON camp.id = c.campaign_id
        LEFT   JOIN tenants   t    ON t.id    = c.tenant_id
        WHERE  c.tenant_id = ${len(_LIVE_STATUSES) + 1}
          AND  (
                  (c.status IN ({placeholders})
                   AND COALESCE(c.started_at, c.created_at)
                       >= NOW() - make_interval(mins => {_LIVE_MAX_AGE_MINUTES}))
                  OR (c.ended_at IS NOT NULL
                      AND c.ended_at >= NOW() - make_interval(secs => ${len(_LIVE_STATUSES) + 2}))
                )
          {where_campaign}
        ORDER BY COALESCE(c.started_at, c.created_at) DESC
        LIMIT  100
    """

    try:
        async with db_client.pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
    except Exception as exc:
        logger.error("list_live_calls failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list live calls")

    items: List[LiveCallItem] = []
    for r in rows:
        items.append(LiveCallItem(
            id=str(r["id"]),
            talklee_call_id=r["talklee_call_id"],
            to_number=r["to_number"] or "",
            status=r["status"] or "unknown",
            started_at=r["started_at"].isoformat() if r["started_at"] else None,
            answered_at=r["answered_at"].isoformat() if r["answered_at"] else None,
            ended_at=r["ended_at"].isoformat() if r["ended_at"] else None,
            duration_seconds=r["duration_seconds"],
            outcome=r["outcome"],
            campaign_id=str(r["campaign_id"]) if r["campaign_id"] else None,
            campaign_name=r["campaign_name"],
            lead_id=str(r["lead_id"]) if r["lead_id"] else None,
            caller_id=r["caller_id"],
        ))

    return LiveCallsResponse(
        items=items,
        server_time=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/issues", response_model=CallIssuesResponse)
async def list_call_issues(
    campaign_id: Optional[str] = Query(
        None, description="If set, restrict to this campaign."
    ),
    window_minutes: int = Query(
        180, ge=1, le=1440,
        description="How far back to look for stuck/failed dial attempts.",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Recent dial attempts that DIDN'T place a call, each explained.

    The live-calls panel only shows rows in ``calls`` — but the gates that
    stop a call (out of minutes, outside hours, campaign stopped, caller-ID
    unverified, TTS warmup failure, rate limits) all fire in the dialer
    before a ``calls`` row exists. This reads those from ``dialer_jobs`` and
    maps each reason to a human title + actionable suggestion so the
    operator can see WHY nothing dialed and how to fix it.

    One card per phone number (the latest issue), tenant-scoped.
    """
    from datetime import datetime, timezone
    from app.domain.services.call_issue_advice import advise

    now_iso = datetime.now(timezone.utc).isoformat()
    if not current_user.tenant_id:
        return CallIssuesResponse(items=[], server_time=now_iso)

    args: list = [current_user.tenant_id, window_minutes]
    where_campaign = ""
    if campaign_id:
        args.append(campaign_id)
        where_campaign = f" AND dj.campaign_id = ${len(args)}"

    # DISTINCT ON (phone_number) → one (latest) issue per number. Exclude
    # statuses that mean the call DID go through or is actively running, so
    # a since-resolved job doesn't linger as a phantom issue.
    sql = f"""
        SELECT DISTINCT ON (dj.phone_number)
               dj.id, dj.phone_number, dj.campaign_id, dj.status,
               dj.last_outcome, dj.last_error, dj.failure_category,
               dj.failure_reason, dj.attempt_number, dj.updated_at,
               camp.name AS campaign_name
        FROM   dialer_jobs dj
        LEFT   JOIN campaigns camp ON camp.id = dj.campaign_id
        WHERE  dj.tenant_id = $1
          AND  dj.updated_at >= NOW() - make_interval(mins => $2)
          -- Only exclude terminal-SUCCESS states. We deliberately keep
          -- 'processing' etc.: a job stuck there WITH a failure_reason is
          -- exactly a problem to surface. Healthy in-flight calls have no
          -- failure_reason (cleared on successful originate) so the filter
          -- below excludes them anyway.
          AND  dj.status NOT IN ('completed', 'goal_achieved')
          AND  (dj.failure_reason IS NOT NULL OR dj.last_error IS NOT NULL)
          {where_campaign}
        ORDER BY dj.phone_number, dj.updated_at DESC
    """

    try:
        async with db_client.pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
    except Exception as exc:
        logger.error("list_call_issues failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list call issues")

    items: List[CallIssueItem] = []
    for r in rows:
        reason = r["failure_reason"] or r["last_error"] or r["status"]
        category = r["failure_category"]
        adv = advise(reason, category=category)
        items.append(CallIssueItem(
            job_id=str(r["id"]),
            phone_number=r["phone_number"] or "",
            campaign_id=str(r["campaign_id"]) if r["campaign_id"] else None,
            campaign_name=r["campaign_name"],
            status=r["status"] or "unknown",
            reason_code=reason,
            category=category,
            title=adv.title,
            suggestion=adv.suggestion,
            severity=adv.severity,
            stage=adv.stage,
            attempts=r["attempt_number"] or 0,
            updated_at=r["updated_at"].isoformat() if r["updated_at"] else None,
        ))

    # Newest first across all numbers (DISTINCT ON forced phone_number order).
    items.sort(key=lambda it: it.updated_at or "", reverse=True)
    return CallIssuesResponse(items=items[:50], server_time=now_iso)


@router.get("/", response_model=CallListResponse)
async def list_calls(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status"),
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Get paginated list of calls.
    
    Used by: /dashboard/history page.
    
    Query params:
        - page: Page number (1-indexed)
        - page_size: Items per page (max 100)
        - status: Filter by call status
        - from: Start date filter
        - to: End date filter
    """
    try:
        import uuid as _uuid
        tenant_uuid = _uuid.UUID(str(current_user.tenant_id))
        offset = (page - 1) * page_size

        conditions = ["c.tenant_id = $1"]
        params: list = [tenant_uuid]
        idx = 2

        if status:
            conditions.append(f"c.status = ${idx}")
            params.append(status)
            idx += 1
        if from_date:
            conditions.append(f"c.created_at >= ${idx}")
            params.append(from_date)
            idx += 1
        if to_date:
            conditions.append(f"c.created_at <= ${idx}")
            params.append(to_date + "T23:59:59Z")
            idx += 1

        where = " AND ".join(conditions)

        async with db_client.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                rows = await conn.fetch(
                    f"""
                    SELECT c.id, c.talklee_call_id, c.created_at, c.phone_number,
                           c.status, c.duration_seconds, c.outcome,
                           c.summary,
                           c.summary_json->>'outcome' AS lead_outcome,
                           camp.name AS campaign_name,
                           (SELECT r.id FROM recordings_s3 r
                             WHERE r.call_id = c.id AND r.status = 'uploaded'
                             ORDER BY r.created_at DESC LIMIT 1) AS recording_id
                    FROM calls c
                    LEFT JOIN campaigns camp ON camp.id = c.campaign_id
                    WHERE {where}
                    ORDER BY c.created_at DESC
                    LIMIT ${idx} OFFSET ${idx + 1}
                    """,
                    *params, page_size, offset,
                )
                total = await conn.fetchval(
                    f"SELECT COUNT(*) FROM calls c WHERE {where}",
                    *params,
                )

        items = []
        for row in rows:
            created_at = row["created_at"]
            items.append(CallListItem(
                id=str(row["id"]),
                talklee_call_id=row["talklee_call_id"],
                timestamp=created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
                to_number=row["phone_number"] or "",
                status=row["status"] or "unknown",
                duration_seconds=row["duration_seconds"],
                outcome=row["outcome"],
                campaign_name=row["campaign_name"],
                summary=row["summary"],
                recording_id=str(row["recording_id"]) if row["recording_id"] is not None else None,
                lead_outcome=row["lead_outcome"],
            ))

        return CallListResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total or 0,
        )
    
    except Exception as e:
        logger.error(f"Failed to fetch calls: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch calls"
        )


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Get individual call details.
    
    Used by: Call detail modal/page.
    
    Returns full call information including transcript and recording reference.
    """
    try:
        # Get call details with tenant filtering
        query = db_client.table("calls").select("*").eq("id", call_id)
        query = apply_tenant_filter(query, current_user.tenant_id)
        call_response = query.single().execute()
        
        if not call_response.data:
            raise HTTPException(
                status_code=404,
                detail="Call not found"
            )
        
        call = call_response.data
        
        # Get recording if exists (recordings live in recordings_s3 table)
        recording_id = None
        async with db_client.pool.acquire() as conn:
            rec_row = await conn.fetchrow(
                "SELECT id FROM recordings_s3 WHERE call_id = $1 ORDER BY created_at DESC LIMIT 1",
                __import__("uuid").UUID(call_id),
            )
        if rec_row:
            recording_id = str(rec_row["id"])
        
        # Normalize summary_json: asyncpg may return JSONB as str or dict
        import json as _json
        raw_summary_json = call.get("summary_json")
        if isinstance(raw_summary_json, str):
            try:
                summary_json = _json.loads(raw_summary_json)
            except (_json.JSONDecodeError, ValueError):
                summary_json = None
        elif isinstance(raw_summary_json, dict):
            summary_json = raw_summary_json
        else:
            summary_json = None

        created_at = call.get("created_at", "")
        return CallDetail(
            id=str(call["id"]),
            talklee_call_id=call.get("talklee_call_id"),
            timestamp=created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
            to_number=call.get("phone_number", ""),
            status=call.get("status", "unknown"),
            duration_seconds=call.get("duration_seconds"),
            outcome=call.get("outcome"),
            transcript=call.get("transcript"),
            recording_id=str(recording_id) if recording_id is not None else None,
            campaign_id=str(call["campaign_id"]) if call.get("campaign_id") else None,
            lead_id=str(call["lead_id"]) if call.get("lead_id") else None,
            summary=call.get("summary"),
            summary_json=summary_json,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch call {call_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch call"
        )


@router.get("/{call_id}/transcript")
async def get_call_transcript(
    call_id: str,
    format: str = Query("json", description="Format: 'json' or 'text'"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Get call transcript in requested format (Day 10).
    
    Used by: Transcript viewer in call details.
    
    Query params:
        - format: 'json' for structured turns, 'text' for plain text
    
    Returns:
        JSON format: {"turns": [...], "metadata": {...}}
        Text format: Plain text transcript
    """
    try:
        # Verify call belongs to tenant before fetching transcript
        if not verify_tenant_access(db_client, "calls", call_id, current_user.tenant_id):
            raise HTTPException(status_code=404, detail="Call not found")
        
        # First try the transcripts table (Day 10)
        transcript_response = db_client.table("transcripts").select(
            "turns, full_text, word_count, turn_count, created_at"
        ).eq("call_id", call_id).execute()
        
        if transcript_response.data and len(transcript_response.data) > 0:
            transcript_data = transcript_response.data[0]
            
            if format == "text":
                return {
                    "format": "text",
                    "transcript": transcript_data.get("full_text", ""),
                    "call_id": call_id
                }
            else:
                return {
                    "format": "json",
                    "turns": transcript_data.get("turns", []),
                    "metadata": {
                        "word_count": transcript_data.get("word_count", 0),
                        "turn_count": transcript_data.get("turn_count", 0),
                        "created_at": transcript_data.get("created_at")
                    },
                    "call_id": call_id
                }
        
        # Fallback to calls table transcript fields
        call_response = db_client.table("calls").select(
            "transcript, transcript_json"
        ).eq("id", call_id).single().execute()
        
        if not call_response.data:
            raise HTTPException(
                status_code=404,
                detail="Call not found"
            )
        
        call_data = call_response.data
        
        if format == "text":
            return {
                "format": "text",
                "transcript": call_data.get("transcript", ""),
                "call_id": call_id
            }
        else:
            return {
                "format": "json",
                "turns": call_data.get("transcript_json", []),
                "metadata": {},
                "call_id": call_id
            }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch transcript for call {call_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch transcript"
        )


@router.get("/{call_id}/summary")
async def get_call_summary(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """AI call summary (structured). Generates on first request if missing
    (lazy backfill) and caches it; returns {available:false} when the call has
    no transcript to summarize."""
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID required")
    from app.domain.services.call_summary.store import generate_and_store
    try:
        summary = await generate_and_store(db_client.pool, str(current_user.tenant_id), call_id)
    except Exception:
        logger.error("get_call_summary failed call=%s", call_id[:12], exc_info=True)
        raise HTTPException(status_code=500, detail="Could not generate summary")
    if summary is None:
        return {"available": False, "summary": None}
    return {"available": True, "summary": summary}


# =============================================================================
# Day 1: Call Events & Legs Endpoints
# =============================================================================

@router.get("/{call_id}/events")
async def get_call_events(
    call_id: str,
    limit: int = Query(100, ge=1, le=500),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Get call events (timeline) for a specific call.
    
    Returns chronological list of events: state changes, leg starts,
    transcripts, LLM calls, TTS, webhooks, etc.
    
    Query params:
        - limit: Max events to return (default 100, max 500)
        - event_type: Filter by type (state_change, transcript, etc.)
    """
    try:
        # Verify call belongs to tenant
        if not verify_tenant_access(db_client, "calls", call_id, current_user.tenant_id):
            raise HTTPException(status_code=404, detail="Call not found")
        
        # Build query
        query = db_client.table("call_events").select("*").eq("call_id", call_id)
        
        if event_type:
            query = query.eq("event_type", event_type)
        
        response = query.order("created_at", desc=False).limit(limit).execute()
        
        return {
            "call_id": call_id,
            "events": response.data or [],
            "count": len(response.data) if response.data else 0
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch events for call {call_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch call events")


@router.get("/{call_id}/legs")
async def get_call_legs(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Get call legs for a specific call.
    
    Returns all legs (PSTN, WebSocket, SIP, etc.) with their status
    and timing information.
    """
    try:
        # Verify call belongs to tenant
        if not verify_tenant_access(db_client, "calls", call_id, current_user.tenant_id):
            raise HTTPException(status_code=404, detail="Call not found")
        
        response = db_client.table("call_legs").select("*").eq("call_id", call_id).order("created_at", desc=False).execute()
        
        return {
            "call_id": call_id,
            "legs": response.data or [],
            "count": len(response.data) if response.data else 0
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch legs for call {call_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch call legs")

