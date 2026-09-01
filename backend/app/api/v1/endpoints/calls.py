"""
Call History Endpoints
Provides paginated call list and individual call details
"""

import logging
import json
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Any, List, Literal, Optional
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.core.db_utils import acquire_with_tenant
from app.core.security.rbac import require_permission, Permission
from app.domain.services.telephony.termination import (
    finalize_proven_inbound_termination,
    mark_termination_pending_and_load_context,
    request_confirmed_hangup,
)
from app.utils.tenant_filter import verify_tenant_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["calls"])


def _json_object(value: Any) -> dict:
    """Normalize JSONB values returned by asyncpg or the REST adapter."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    return {}


def _display_caller_ani(call: Any) -> Optional[str]:
    """Expose inbound ANI only when the carrier did not mark it private."""
    if (call.get("direction") or "outbound") != "inbound":
        return None
    if bool(call.get("caller_ani_private")):
        return None
    value = call.get("caller_ani") or call.get("phone_number")
    return str(value) if value and str(value).lower() != "anonymous" else None


def _route_metadata(call: Any) -> tuple[dict, Optional[str], Optional[str]]:
    snapshot = _json_object(call.get("route_snapshot"))
    inbound_config = _json_object(snapshot.get("inbound_config"))
    route = _json_object(snapshot.get("route"))
    checksum = inbound_config.get("checksum")
    route_id = route.get("assignment_id") or call.get("assignment_id")
    return (
        snapshot,
        str(checksum) if checksum else None,
        str(route_id) if route_id else None,
    )


def _inbound_config_id(call: Any) -> Optional[str]:
    """Return the immutable inbound-config identity pinned at admission.

    ``calls.campaign_id`` always identifies the base campaign.  The inbound
    dashboard route, however, is keyed by ``inbound_campaign_configs.id``.
    Keeping these identities separate avoids broken detail links and prevents
    a mutable assignment lookup from rewriting historical call ownership.
    """
    if (call.get("direction") or "outbound") != "inbound":
        return None
    snapshot = _json_object(call.get("route_snapshot"))
    inbound_config = _json_object(snapshot.get("inbound_config"))
    route = _json_object(snapshot.get("route"))
    value = inbound_config.get("id") or route.get("config_id")
    return str(value) if value else None


def _display_from_number(call: Any) -> Optional[str]:
    """Project a direction-correct, durable source number."""
    if (call.get("direction") or "outbound") == "inbound":
        return _display_caller_ani(call)
    value = call.get("outbound_from_number")
    return str(value) if value else None


def _media_state(call: Any) -> Optional[str]:
    """Give the UI one stable media state without conflating sub-states."""
    if (call.get("direction") or "outbound") != "inbound":
        return None
    admission = call.get("admission_status")
    processing = call.get("processing_status")
    status = str(call.get("status") or "").lower()
    if admission == "denied":
        return "not_started"
    if processing == "failed":
        return "failed"
    if processing in {"completed", "released"} or status in {
        "ended",
        "completed",
        "failed",
        "cancelled",
    }:
        return "completed"
    if processing == "active" or status in {
        "answered",
        "in_call",
        "ringing",
        "initiated",
    }:
        return "active"
    return "pending"


def _transcript_state(call: Any) -> Optional[str]:
    if (call.get("direction") or "outbound") != "inbound":
        return None
    if bool(call.get("has_transcript")) or bool(call.get("transcript")):
        return "done"
    if call.get("processing_status") == "failed":
        return "failed"
    if call.get("admission_status") == "denied":
        return "not_started"
    return "pending"


def _recording_state(call: Any, snapshot: dict) -> Optional[str]:
    if (call.get("direction") or "outbound") != "inbound":
        return call.get("recording_status")
    current = call.get("recording_status")
    if current:
        return str(current)
    inbound_config = _json_object(snapshot.get("inbound_config"))
    controls = _json_object(snapshot.get("controls"))
    if inbound_config.get("recording_enabled") is False:
        return "disabled"
    if controls.get("recording_enabled") is False:
        return "disabled"
    if call.get("consent_status") == "declined":
        return "declined"
    if call.get("admission_status") == "denied":
        return "not_started"
    return "pending"


def _duration_between(start: Any, end: Any) -> Optional[int]:
    if not start:
        return None
    try:
        start_dt = (
            start
            if isinstance(start, datetime)
            else datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        )
        end_dt = (
            end
            if isinstance(end, datetime)
            else (
                datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                if end
                else datetime.now(timezone.utc)
            )
        )
        if start_dt.tzinfo is None and end_dt.tzinfo is not None:
            end_dt = end_dt.replace(tzinfo=None)
        if start_dt.tzinfo is not None and end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=start_dt.tzinfo)
        return max(0, int((end_dt - start_dt).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


class CallListItem(BaseModel):
    """Call list item (summary)"""

    id: str
    talklee_call_id: Optional[str] = None
    timestamp: str
    from_number: Optional[str] = None
    to_number: str
    status: str
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None
    campaign_name: Optional[str] = None
    campaign_id: Optional[str] = None
    summary: Optional[str] = None
    recording_id: Optional[str] = None
    # AI per-call verdict from the post-call summary (e.g. "qualified | …",
    # "callback | …", "no_interest | …") — the "was this call a success" answer.
    lead_outcome: Optional[str] = None
    # Whether a reviewer has left a voice note on this call. Computed per row by
    # an EXISTS against call_feedback, so it varies with the data instead of
    # defaulting to False forever — a list flag wired to nothing looks identical
    # to a list where nobody has left feedback yet.
    has_feedback: bool = False
    direction: Literal["inbound", "outbound"] = "outbound"
    caller_ani: Optional[str] = None
    called_did: Optional[str] = None
    inbound_campaign_id: Optional[str] = None
    assignment_id: Optional[str] = None
    route_id: Optional[str] = None
    route_version: Optional[int] = None
    config_version: Optional[int] = None
    config_checksum: Optional[str] = None
    admission_status: Optional[str] = None
    admission_reason: Optional[str] = None
    consent_status: Optional[str] = None
    processing_status: Optional[str] = None
    billing_status: Optional[str] = None
    billing_hold_reason: Optional[str] = None
    recording_status: Optional[str] = None
    transcript_status: Optional[str] = None
    media_state: Optional[str] = None


class CallDetail(BaseModel):
    """Full call details"""

    id: str
    talklee_call_id: Optional[str] = None
    timestamp: str
    from_number: Optional[str] = None
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
    campaign_name: Optional[str] = None
    direction: Literal["inbound", "outbound"] = "outbound"
    provider: Optional[str] = None
    provider_call_id: Optional[str] = None
    caller_ani: Optional[str] = None
    called_did: Optional[str] = None
    inbound_campaign_id: Optional[str] = None
    assignment_id: Optional[str] = None
    route_id: Optional[str] = None
    route_version: Optional[int] = None
    config_version: Optional[int] = None
    config_checksum: Optional[str] = None
    ingress: Optional[str] = None
    route_snapshot: Optional[dict] = None
    admission_status: Optional[str] = None
    admission_reason: Optional[str] = None
    consent_status: Optional[str] = None
    processing_status: Optional[str] = None
    billing_status: Optional[str] = None
    billing_hold_reason: Optional[str] = None
    reserved_seconds: Optional[int] = None
    recording_status: Optional[str] = None
    transcript_status: Optional[str] = None
    media_state: Optional[str] = None
    answer_delay_seconds: Optional[int] = None
    conversation_duration_seconds: Optional[int] = None
    billed_duration_seconds: Optional[int] = None
    cost: Optional[float] = None
    transfer_legs: List[dict] = Field(default_factory=list)


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
    severity: str  # error | warning | info
    stage: str
    attempts: int = 0
    updated_at: Optional[str] = None
    # Structured block reason (additive — see
    # app/domain/services/dialer/block_reasons.py). `reason_code` above stays
    # the RAW dialer string for backwards compatibility; `block_code` is the
    # stable machine value a UI should switch on, `block_message` the human
    # sentence that says what to do, and `next_eligible_at` the ISO timestamp
    # of the next moment this call can go out (when computable).
    block_code: Optional[str] = None
    block_message: Optional[str] = None
    next_eligible_at: Optional[str] = None


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
    status: str  # CallState value
    started_at: Optional[str] = None
    answered_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    lead_id: Optional[str] = None
    caller_id: Optional[str] = None  # the FROM number used
    direction: Literal["inbound", "outbound"] = "outbound"
    caller_ani: Optional[str] = None
    called_did: Optional[str] = None
    admission_status: Optional[str] = None
    consent_status: Optional[str] = None
    processing_status: Optional[str] = None
    termination_status: Literal["none", "requested", "confirmed", "failed"] = "none"
    termination_requested_at: Optional[str] = None
    termination_error: Optional[str] = None
    provider_hangup_requested: Optional[bool] = None
    provider_hangup_confirmed: bool = False


class LiveCallsResponse(BaseModel):
    items: List[LiveCallItem]
    server_time: str  # so the FE can compute elapsed
    # times even if its clock drifts


class RejectedInboundCallItem(BaseModel):
    """One durable pre-answer denial or after-hours rejection."""

    id: str
    source: Literal["pre_row", "call"]
    occurred_at: str
    status: Literal["denied", "after_hours"]
    reason: str
    provider: Optional[str] = None
    provider_call_id: Optional[str] = None
    caller_ani: Optional[str] = None
    called_did: Optional[str] = None
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    inbound_config_id: Optional[str] = None
    assignment_id: Optional[str] = None


class RejectedInboundCallsResponse(BaseModel):
    items: List[RejectedInboundCallItem]
    page: int
    page_size: int
    total: int
    server_time: str


# Statuses that count as "in flight" for the live panel. Old finalised
# rows (ended/completed/failed) only show up if they ended very recently
# (see `recent_window_seconds` below).
_LIVE_STATUSES = (
    "queued",
    "dialing",
    "ringing",
    "answered",
    "in_call",
    "initiated",
    "termination_pending",
)

# Upper bound on how old a still-"live"-status call may be before we treat it as
# a phantom (crashed worker / missed hangup / stopped campaign) and stop showing
# it. No real AI call runs this long, so anything older is stale DB state, not a
# live call. This is what keeps the panel "exact" — without it a call stuck in
# dialing/in_call lingers in the feed forever.
_LIVE_MAX_AGE_MINUTES = 30


@router.post(
    "/{call_id}/hangup", dependencies=[Depends(require_permission(Permission.CALLS_DELETE))]
)
async def hangup_live_call(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Terminate one tenant call only after authoritative provider/PBX proof.

    A successful control request is not itself a terminal event.  Billing,
    leases, and the call row remain live when the adapter cannot prove every
    owned channel absent within five seconds; the operator can retry while the
    normal provider callback/watchdog continues to reconcile it.

    THE OUTCOME IS NOT A CONSTANT (fixed 2026-08-03)
    ------------------------------------------------
    This used to write `outcome = COALESCE(outcome, 'agent_hung_up')`
    unconditionally. But the docstring above says what this endpoint is mostly
    used for: clearing phantom stuck rows whose channel is already gone —
    calls that were NEVER ANSWERED. `agent_hung_up` means "the AI agent ended
    a call it was having", and it lives in `call_outcomes.ANSWERED_OUTCOMES`,
    so every phantom an operator cleared was counted as a successful
    conversation on the dashboard, in analytics, and in campaign performance.

    In production this was the single largest outcome bucket — more rows than
    every genuinely answered call combined, every one of them with no
    `answered_at`, no transcript and no recording, inflating the reported
    connect rate to well over twice the truth. The effect is self-reinforcing:
    the more stuck calls the system produces, the better its numbers look.

    Now the outcome follows the evidence — `agent_hung_up` only when the call
    really had been answered, `cancelled` (CallOutcome.CANCELLED: "we hung up
    before answer") otherwise, which classifies as failed.

    It also sets `duration_seconds`, which it never did. An operator ending a
    genuinely live conversation recorded zero billable time for it.
    """
    if not current_user.tenant_id:
        raise HTTPException(status_code=403, detail="No tenant context")
    try:
        tenant_uuid = UUID(str(current_user.tenant_id))
        async with acquire_with_tenant(db_client.pool, str(tenant_uuid)) as conn:
            row = await conn.fetchrow(
                "SELECT external_call_uuid, provider_call_id, provider, direction, "
                "status, started_at, answered_at, campaign_id FROM calls "
                "WHERE id = $1 AND tenant_id = $2",
                UUID(call_id),
                tenant_uuid,
            )
        if not row:
            raise HTTPException(status_code=404, detail="Call not found")

        current_status = str(row["status"] or "")
        from app.domain.services.call_status import TERMINAL_CALL_STATUSES

        terminal_statuses = TERMINAL_CALL_STATUSES
        ext = row["provider_call_id"] or row["external_call_uuid"]
        from app.api.v1.endpoints import telephony_bridge as tb

        try:
            termination_context = await mark_termination_pending_and_load_context(
                db_client.pool,
                call_reference=call_id,
                tenant_id=str(current_user.tenant_id),
            )
        except Exception as exc:  # noqa: BLE001 - fail closed at DB boundary
            logger.error(
                "tenant_call_termination_leg_lookup_failed call=%s err_type=%s",
                call_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "termination_context_unavailable",
                    "call_id": call_id,
                    "call_status": current_status,
                    "termination_status": "failed",
                    "provider_hangup_requested": False,
                    "provider_hangup_confirmed": False,
                    "provider_hangup_error": "linked_leg_lookup_failed",
                },
            ) from exc

        proof = await request_confirmed_hangup(
            tb._adapter,
            str(termination_context.provider_call_id or ext or ""),
            provider_leg_ids=termination_context.provider_leg_ids,
        )
        if not proof.confirmed:
            error_code = proof.code
            retry_is_durable = current_status not in terminal_statuses
            logger.warning(
                "tenant_call_termination_unconfirmed call=%s code=%s",
                call_id,
                error_code,
            )
            raise HTTPException(
                status_code=(
                    504 if error_code in {"confirmation_timeout", "hangup_unconfirmed"} else 503
                ),
                detail={
                    "error": "termination_unconfirmed",
                    "reason": error_code,
                    "call_id": call_id,
                    "call_status": ("termination_pending" if retry_is_durable else current_status),
                    "termination_status": ("requested" if retry_is_durable else "failed"),
                    "provider_hangup_requested": proof.requested,
                    "provider_hangup_confirmed": False,
                    "provider_hangup_error": proof.error or error_code,
                },
            )

        # Only a proved-absent channel may release inbound parent/transfer
        # leases, billing reservations, the cluster slot, and cleanup ledger.
        # Terminal replay still runs this idempotent finalizer: a stale terminal
        # parent can coexist with an active persisted transfer child after a
        # crash, and the database label is not settlement proof.
        settlement_deferred = False
        if row["direction"] == "inbound" and termination_context.provider_call_id:
            try:
                from app.core.container import get_container

                try:
                    redis_client = getattr(get_container(), "redis", None)
                except Exception:  # local tests/minimal deployments
                    redis_client = None
                await finalize_proven_inbound_termination(
                    db_client.pool,
                    provider_call_id=termination_context.provider_call_id,
                    durable_call_id=call_id,
                    tenant_id=str(current_user.tenant_id),
                    provider=str(row["provider"] or "asterisk"),
                    terminal_status="ended",
                    reason="tenant_operator_hangup",
                    redis_client=redis_client,
                    campaign_id=(str(row["campaign_id"]) if row.get("campaign_id") else None),
                )
            except Exception as exc:
                settlement_deferred = True
                logger.error(
                    "hangup_live_call inbound settlement failed call=%s: %s",
                    call_id,
                    exc,
                )

        # A database terminal state is not provider/PBX evidence. Replays are
        # successful only after the same all-leg proof and shared settlement.
        if current_status in terminal_statuses:
            return {
                "status": "already_terminal",
                "call_id": call_id,
                "call_status": current_status,
                "termination_status": "confirmed",
                "provider_hangup_requested": proof.requested,
                "provider_hangup_confirmed": True,
                "provider_hangup_error": None,
                "settlement_deferred": settlement_deferred,
            }

        if settlement_deferred:
            # PBX truth is confirmed, but logical settlement must remain
            # visibly non-terminal until the durable recovery owner succeeds.
            return {
                "status": "confirmed",
                "call_id": call_id,
                "call_status": "termination_pending",
                "termination_status": "confirmed",
                "provider_hangup_requested": proof.requested,
                "provider_hangup_confirmed": True,
                "provider_hangup_error": None,
                "settlement_deferred": True,
            }

        latest_status = None
        async with acquire_with_tenant(db_client.pool, str(tenant_uuid)) as conn:
            updated = await conn.fetchrow(
                """
                UPDATE calls
                   SET status   = 'ended',
                       ended_at = COALESCE(ended_at, NOW()),
                       outcome  = COALESCE(
                           outcome,
                           CASE WHEN answered_at IS NOT NULL
                                      OR status IN ('answered', 'in_call')
                                THEN 'agent_hung_up'
                                ELSE 'cancelled'
                           END
                       ),
                       duration_seconds = COALESCE(
                           duration_seconds,
                           CASE WHEN answered_at IS NOT NULL
                                THEN GREATEST(
                                    0,
                                    EXTRACT(EPOCH FROM (NOW() - answered_at))::int
                                )
                                ELSE 0
                           END
                       ),
                       updated_at = NOW()
                 WHERE id = $1
                   AND tenant_id = $2
                   AND status <> ALL($3::text[])
                 RETURNING status
                """,
                UUID(call_id),
                tenant_uuid,
                list(terminal_statuses),
            )
        if not updated:
            async with acquire_with_tenant(db_client.pool, str(tenant_uuid)) as conn:
                latest_status = await conn.fetchval(
                    "SELECT status FROM calls WHERE id = $1 AND tenant_id = $2",
                    UUID(call_id),
                    tenant_uuid,
                )
            if str(latest_status or "") not in terminal_statuses:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "termination_state_conflict", "call_id": call_id},
                )
        final_status = str(updated["status"] if updated else latest_status or "ended")
        return {
            "status": "confirmed",
            "call_id": call_id,
            "call_status": final_status,
            "termination_status": "confirmed",
            "provider_hangup_requested": proof.requested,
            "provider_hangup_confirmed": True,
            "provider_hangup_error": None,
            "settlement_deferred": settlement_deferred,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("hangup_live_call failed call=%s: %s", call_id, exc)
        raise HTTPException(status_code=500, detail="Failed to hang up call")


@router.get("/live", response_model=LiveCallsResponse)
async def list_live_calls(
    campaign_id: Optional[UUID] = Query(
        None,
        description="If set, restrict to this campaign. Otherwise all of the user's calls in flight.",
    ),
    direction: Optional[Literal["inbound", "outbound"]] = Query(
        None, description="If set, restrict the live feed by call direction."
    ),
    recent_window_seconds: int = Query(
        60,
        ge=0,
        le=600,
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

    Tenant scope is enforced in SQL and the SELECT runs through the
    RLS-protected pool — the same defence-in-depth pattern the list endpoint
    uses.
    """
    if not current_user.tenant_id:
        return LiveCallsResponse(items=[], server_time=datetime.now(timezone.utc).isoformat())

    placeholders = ", ".join(f"${i+1}" for i in range(len(_LIVE_STATUSES)))
    # Note: status IN (...) OR (ended within window) — single SELECT
    # so the panel gets a coherent snapshot.
    args: list = list(_LIVE_STATUSES)
    args.append(current_user.tenant_id)
    args.append(recent_window_seconds)
    extra_filters: list[str] = []
    if campaign_id:
        extra_filters.append(f"c.campaign_id = ${len(args) + 1}")
        args.append(campaign_id)
    if direction:
        extra_filters.append(f"c.direction = ${len(args) + 1}")
        args.append(direction)
    where_extra = "".join(f" AND {condition}" for condition in extra_filters)

    sql = f"""
        SELECT c.id, c.talklee_call_id, c.phone_number AS to_number,
               c.status, c.started_at, c.answered_at, c.ended_at,
               c.duration_seconds, c.outcome, c.campaign_id, c.lead_id,
               camp.name AS campaign_name,
               t.calling_rules->>'caller_id' AS caller_id,
               c.direction, c.caller_ani, c.caller_ani_private,
               c.called_did, c.admission_status, c.consent_status,
               c.processing_status, c.updated_at
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
          {where_extra}
        ORDER BY COALESCE(c.started_at, c.created_at) DESC
        LIMIT  100
    """

    try:
        # RLS is enforced (the app role is no longer BYPASSRLS), so the
        # connection must carry the tenant GUC or the policy filters every
        # row out and this endpoint silently returns an empty live panel.
        # The tenant is known here — it is the caller's own — so scope to it
        # rather than bypassing.
        async with acquire_with_tenant(db_client.pool, current_user.tenant_id) as conn:
            rows = await conn.fetch(sql, *args)
    except Exception as exc:
        logger.error("list_live_calls failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list live calls")

    items: List[LiveCallItem] = []
    for r in rows:
        row_status = str(r["status"] or "unknown")
        termination_requested = row_status == "termination_pending"
        items.append(
            LiveCallItem(
                id=str(r["id"]),
                talklee_call_id=r["talklee_call_id"],
                to_number=r["to_number"] or "",
                status=row_status,
                started_at=r["started_at"].isoformat() if r["started_at"] else None,
                answered_at=r["answered_at"].isoformat() if r["answered_at"] else None,
                ended_at=r["ended_at"].isoformat() if r["ended_at"] else None,
                duration_seconds=r["duration_seconds"],
                outcome=r["outcome"],
                campaign_id=str(r["campaign_id"]) if r["campaign_id"] else None,
                campaign_name=r["campaign_name"],
                lead_id=str(r["lead_id"]) if r["lead_id"] else None,
                caller_id=r["caller_id"] if (r["direction"] or "outbound") == "outbound" else None,
                direction=r["direction"] or "outbound",
                caller_ani=_display_caller_ani(r),
                called_did=r["called_did"],
                admission_status=r["admission_status"],
                consent_status=r["consent_status"],
                processing_status=r["processing_status"],
                termination_status=("requested" if termination_requested else "none"),
                termination_requested_at=(
                    r["updated_at"].isoformat()
                    if termination_requested and r["updated_at"]
                    else None
                ),
                termination_error=None,
                # The durable row proves intent/retry ownership, not whether the
                # process got as far as dispatching DELETE before it crashed.
                provider_hangup_requested=None,
                provider_hangup_confirmed=False,
            )
        )

    return LiveCallsResponse(
        items=items,
        server_time=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/rejected", response_model=RejectedInboundCallsResponse)
async def list_rejected_inbound_calls(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    campaign_id: Optional[UUID] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """List every tenant-owned inbound rejection from both durable sources.

    Early denials have no billable ``calls`` row and come from
    ``inbound_rejections``. Capacity/minutes denials and the configured
    after-hours hangup path do have a calls row. The explicit union prevents
    "who called while we were closed?" from silently returning an empty list.
    """

    if not current_user.tenant_id:
        return RejectedInboundCallsResponse(
            items=[],
            page=page,
            page_size=page_size,
            total=0,
            server_time=datetime.now(timezone.utc).isoformat(),
        )

    tenant_id = UUID(str(current_user.tenant_id))
    params: list[Any] = [tenant_id]
    campaign_filter = ""
    if campaign_id is not None:
        params.append(campaign_id)
        campaign_filter = f"WHERE rejected.campaign_id = ${len(params)}"
    params.extend([page_size, (page - 1) * page_size])
    limit_index = len(params) - 1
    offset_index = len(params)

    sql = f"""
        WITH rejected AS (
            SELECT r.id,
                   'pre_row'::text AS source,
                   r.occurred_at,
                   'denied'::text AS status,
                   r.reason,
                   r.provider,
                   r.provider_call_id,
                   r.caller_ani,
                   r.caller_ani_private,
                   r.called_did,
                   r.campaign_id,
                   camp.name AS campaign_name,
                   r.inbound_config_id,
                   r.assignment_id
            FROM inbound_rejections r
            LEFT JOIN campaigns camp ON camp.id=r.campaign_id
            WHERE r.tenant_id=$1
              AND r.retention_until >= NOW()

            UNION ALL

            SELECT c.id,
                   'call'::text AS source,
                   COALESCE(c.ended_at, c.updated_at, c.created_at) AS occurred_at,
                   CASE WHEN c.admission_reason='after_hours_closed'
                        THEN 'after_hours' ELSE 'denied' END AS status,
                   COALESCE(c.admission_reason, 'admission_denied') AS reason,
                   c.provider,
                   c.provider_call_id,
                   c.caller_ani,
                   c.caller_ani_private,
                   c.called_did,
                   c.campaign_id,
                   camp.name AS campaign_name,
                   NULL::uuid AS inbound_config_id,
                   c.assignment_id
            FROM calls c
            LEFT JOIN campaigns camp ON camp.id=c.campaign_id
            WHERE c.tenant_id=$1
              AND c.direction='inbound'
              AND (
                    c.admission_status='denied'
                    OR c.admission_reason='after_hours_closed'
                  )
        )
        SELECT rejected.*, COUNT(*) OVER() AS total_rows
        FROM rejected
        {campaign_filter}
        ORDER BY rejected.occurred_at DESC, rejected.id
        LIMIT ${limit_index} OFFSET ${offset_index}
    """

    try:
        async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
            rows = await conn.fetch(sql, *params)
    except Exception as exc:
        logger.error("list_rejected_inbound_calls failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list rejected inbound calls")

    items = [
        RejectedInboundCallItem(
            id=str(row["id"]),
            source=row["source"],
            occurred_at=row["occurred_at"].isoformat(),
            status=row["status"],
            reason=row["reason"],
            provider=row["provider"],
            provider_call_id=row["provider_call_id"],
            caller_ani=(None if row["caller_ani_private"] else row["caller_ani"]),
            called_did=row["called_did"],
            campaign_id=str(row["campaign_id"]) if row["campaign_id"] else None,
            campaign_name=row["campaign_name"],
            inbound_config_id=(str(row["inbound_config_id"]) if row["inbound_config_id"] else None),
            assignment_id=(str(row["assignment_id"]) if row["assignment_id"] else None),
        )
        for row in rows
    ]
    total = int(rows[0]["total_rows"]) if rows else 0
    return RejectedInboundCallsResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        server_time=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/issues", response_model=CallIssuesResponse)
async def list_call_issues(
    campaign_id: Optional[str] = Query(None, description="If set, restrict to this campaign."),
    window_minutes: int = Query(
        60,
        ge=1,
        le=1440,
        description="How far back to look for stuck/failed dial attempts.",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Recent dial attempts that DIDN'T place a call, each explained — and
    only while the problem is STILL true.

    The live-calls panel only shows rows in ``calls``; the gates that stop a
    call (out of minutes, outside hours, campaign stopped, caller-ID
    unverified, TTS warmup failure, rate limits) all fire in the dialer
    before a ``calls`` row exists. This reads those from ``dialer_jobs``.

    Staleness guards — a fixed problem must disappear, not linger:
      * A later successful call for the same lead supersedes the issue
        (``NOT EXISTS`` newer ``calls`` row).
      * ``out_of_minutes`` is dropped if the tenant now has minutes again.
      * ``campaign_stopped`` is dropped if the campaign is now running.
    One card per phone number (the latest unresolved attempt), tenant-scoped.
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

    # DISTINCT ON (phone_number) → one (latest) issue per number. The
    # NOT EXISTS drops any number that has since placed a real call (the
    # dialer only writes a `calls` row on a successful originate), so
    # "the call is going through now" never shows a stale failure.
    sql = f"""
        SELECT DISTINCT ON (dj.phone_number)
               dj.id, dj.phone_number, dj.campaign_id, dj.lead_id, dj.status,
               dj.last_outcome, dj.last_error, dj.failure_category,
               dj.failure_reason, dj.attempt_number, dj.updated_at,
               camp.name AS campaign_name, camp.status AS campaign_status,
               camp.calling_config AS calling_config
        FROM   dialer_jobs dj
        LEFT   JOIN campaigns camp ON camp.id = dj.campaign_id
        WHERE  dj.tenant_id = $1
          AND  dj.updated_at >= NOW() - make_interval(mins => $2)
          AND  dj.status NOT IN ('completed', 'goal_achieved')
          AND  (dj.failure_reason IS NOT NULL OR dj.last_error IS NOT NULL)
          -- Self-clearing PACING deferrals are normal dialer operation
          -- (inter-call gap, batch slot, per-account rate limiter). They
          -- retry on their own within seconds/minutes and need no operator
          -- action — surfacing one card per lead flooded the panel with
          -- hundreds of "issues" on any healthy paced campaign.
          AND  COALESCE(dj.failure_reason, dj.last_error, '')
                 NOT IN ('call_gap', 'batch_capacity', 'tenant_gap',
                         'call_guard_throttled', 'call_guard_queued')
          AND  NOT EXISTS (
                 SELECT 1 FROM calls c2
                 WHERE c2.lead_id = dj.lead_id
                   AND c2.created_at > dj.updated_at
               )
          {where_campaign}
        ORDER BY dj.phone_number, dj.updated_at DESC
    """

    try:
        async with acquire_with_tenant(db_client.pool, str(current_user.tenant_id)) as conn:
            rows = await conn.fetch(sql, *args)
    except Exception as exc:
        logger.error("list_call_issues failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list call issues")

    # Re-validate the two account-level conditions LIVE so a fixed problem
    # (added minutes, started the campaign) clears immediately instead of
    # waiting out the window. Minutes is one cheap lookup for the tenant.
    minutes_ok = True
    try:
        from app.domain.services.minutes_quota import tenant_minutes_status

        minutes_ok = not (await tenant_minutes_status(current_user.tenant_id)).exhausted
    except Exception:
        minutes_ok = True  # fail open — never hide a real issue on a lookup glitch

    # Effective calling rules per campaign, so a schedule issue can quote the
    # user's OWN window ("Mon & Fri, 14:00-17:00 Europe/London") and compute
    # the next eligible time. One tenant-rules lookup for the whole request;
    # the per-campaign overlay comes from the calling_config already selected
    # above. Best-effort — a lookup problem just yields a generic message.
    from app.domain.models.calling_rules import CallingRules
    from app.domain.services.dialer.block_reasons import classify
    from app.domain.services.dialer.campaign_schedule import effective_rules

    tenant_rules = CallingRules.default()
    try:
        async with acquire_with_tenant(db_client.pool, str(current_user.tenant_id)) as conn:
            raw_rules = await conn.fetchval(
                "SELECT calling_rules FROM tenants WHERE id = $1::uuid",
                current_user.tenant_id,
            )
        if raw_rules:
            if isinstance(raw_rules, str):
                import json as _json

                raw_rules = _json.loads(raw_rules)
            if isinstance(raw_rules, dict):
                tenant_rules = CallingRules.from_dict(raw_rules)
    except Exception as exc:  # noqa: BLE001
        logger.debug("list_call_issues: tenant calling_rules lookup failed: %s", exc)

    def _rules_for(row) -> CallingRules:
        cfg = row["calling_config"] if "calling_config" in row.keys() else None
        if isinstance(cfg, str):
            try:
                import json as _json

                cfg = _json.loads(cfg)
            except Exception:  # noqa: BLE001
                cfg = None
        return effective_rules(tenant_rules, cfg if isinstance(cfg, dict) else None)

    items: List[CallIssueItem] = []
    for r in rows:
        reason = r["failure_reason"] or r["last_error"] or r["status"]
        low = (reason or "").lower()
        # Drop resolved account-level conditions.
        if "out_of_minutes" in low and minutes_ok:
            continue
        if "campaign_stopped" in low and (r["campaign_status"] in ("running", "active")):
            continue
        category = r["failure_category"]
        adv = advise(reason, category=category)
        try:
            blocked = classify(reason, rules=_rules_for(r))
        except Exception as exc:  # noqa: BLE001 — enrichment must never 500
            logger.debug("list_call_issues: classify failed reason=%r: %s", reason, exc)
            blocked = None
        items.append(
            CallIssueItem(
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
                block_code=blocked.code.value if blocked else None,
                block_message=blocked.message if blocked else None,
                next_eligible_at=(
                    blocked.next_eligible_at.isoformat()
                    if blocked and blocked.next_eligible_at
                    else None
                ),
            )
        )

    # Newest first across all numbers (DISTINCT ON forced phone_number order).
    items.sort(key=lambda it: it.updated_at or "", reverse=True)
    return CallIssuesResponse(items=items[:50], server_time=now_iso)


@router.get("/", response_model=CallListResponse)
async def list_calls(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status"),
    direction: Optional[Literal["inbound", "outbound"]] = Query(
        None, description="Filter by call direction."
    ),
    inbound_campaign_id: Optional[UUID] = Query(
        None,
        description="Restrict to one inbound campaign; implies direction=inbound.",
    ),
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
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
        if direction:
            conditions.append(f"c.direction = ${idx}")
            params.append(direction)
            idx += 1
        if inbound_campaign_id:
            conditions.append("c.direction = 'inbound'")
            conditions.append(
                f"COALESCE(c.route_snapshot #>> '{{inbound_config,id}}', "
                f"c.route_snapshot #>> '{{route,config_id}}') = ${idx}::text"
            )
            params.append(str(inbound_campaign_id))
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
                           c.status, c.duration_seconds, c.outcome, c.campaign_id,
                           c.summary,
                           to_jsonb(c)->'summary_json'->>'outcome' AS lead_outcome,
                           c.direction, c.caller_ani, c.caller_ani_private,
                           c.called_did, c.assignment_id, c.route_version,
                           c.config_version, c.route_snapshot,
                           c.admission_status, c.admission_reason,
                           c.consent_status, c.processing_status, c.billing_status,
                           c.billing_hold_reason,
                           camp.name AS campaign_name,
                           (SELECT leg.from_number FROM call_legs leg
                             WHERE leg.call_id = c.id
                               AND leg.direction = 'outbound'
                               AND NULLIF(BTRIM(leg.from_number), '') IS NOT NULL
                             ORDER BY leg.created_at, leg.id LIMIT 1)
                               AS outbound_from_number,
                           (SELECT r.id FROM recordings_s3 r
                             WHERE r.call_id = c.id AND r.status = 'uploaded'
                             ORDER BY r.created_at DESC LIMIT 1) AS recording_id,
                           (SELECT r.status FROM recordings_s3 r
                             WHERE r.call_id = c.id
                             ORDER BY r.created_at DESC LIMIT 1) AS recording_status,
                           ((c.transcript IS NOT NULL AND BTRIM(c.transcript) <> '')
                             OR EXISTS (SELECT 1 FROM transcripts tr
                                         WHERE tr.call_id = c.id
                                           AND COALESCE(tr.full_text, '') <> ''))
                               AS has_transcript,
                           EXISTS (SELECT 1 FROM call_feedback f
                                    WHERE f.call_id = c.id) AS has_feedback
                    FROM calls c
                    LEFT JOIN campaigns camp ON camp.id = c.campaign_id
                    WHERE {where}
                    ORDER BY c.created_at DESC
                    LIMIT ${idx} OFFSET ${idx + 1}
                    """,
                    *params,
                    page_size,
                    offset,
                )
                total = await conn.fetchval(
                    f"SELECT COUNT(*) FROM calls c WHERE {where}",
                    *params,
                )

        items = []
        for row in rows:
            created_at = row["created_at"]
            snapshot, checksum, route_id = _route_metadata(row)
            row_direction = row["direction"] or "outbound"
            inbound_from = _display_caller_ani(row)
            display_from = _display_from_number(row)
            display_to = (
                row["called_did"] if row_direction == "inbound" else row["phone_number"]
            ) or ""
            items.append(
                CallListItem(
                    id=str(row["id"]),
                    talklee_call_id=row["talklee_call_id"],
                    timestamp=(
                        created_at.isoformat()
                        if hasattr(created_at, "isoformat")
                        else str(created_at)
                    ),
                    from_number=display_from,
                    to_number=display_to,
                    status=row["status"] or "unknown",
                    duration_seconds=row["duration_seconds"],
                    outcome=row["outcome"],
                    campaign_name=row["campaign_name"],
                    campaign_id=str(row["campaign_id"]) if row["campaign_id"] else None,
                    summary=row["summary"],
                    recording_id=(
                        str(row["recording_id"]) if row["recording_id"] is not None else None
                    ),
                    lead_outcome=row["lead_outcome"],
                    has_feedback=bool(row["has_feedback"]),
                    direction=row_direction,
                    caller_ani=inbound_from,
                    called_did=row["called_did"],
                    inbound_campaign_id=(_inbound_config_id(row)),
                    assignment_id=str(row["assignment_id"]) if row["assignment_id"] else None,
                    route_id=route_id,
                    route_version=row["route_version"],
                    config_version=row["config_version"],
                    config_checksum=checksum,
                    admission_status=row["admission_status"],
                    admission_reason=row["admission_reason"],
                    consent_status=row["consent_status"],
                    processing_status=row["processing_status"],
                    billing_status=row["billing_status"],
                    billing_hold_reason=row["billing_hold_reason"],
                    recording_status=_recording_state(row, snapshot),
                    transcript_status=_transcript_state(row),
                    media_state=_media_state(row),
                )
            )

        return CallListResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total or 0,
        )

    except Exception as e:
        logger.error(f"Failed to fetch calls: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch calls")


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """
    Get individual call details.

    Used by: Call detail modal/page.

    Returns full call information including transcript and recording reference.
    """
    try:
        if not current_user.tenant_id:
            raise HTTPException(status_code=403, detail="No tenant context")
        try:
            call_uuid = UUID(call_id)
            tenant_uuid = UUID(str(current_user.tenant_id))
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(status_code=404, detail="Call not found")

        async with db_client.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                call_row = await conn.fetchrow(
                    """
                    SELECT c.*,
                           camp.name AS campaign_name,
                           (SELECT tr.full_text FROM transcripts tr
                             WHERE tr.call_id=c.id
                             ORDER BY tr.created_at DESC LIMIT 1)
                               AS persisted_transcript,
                           EXISTS (SELECT 1 FROM transcripts tr
                                    WHERE tr.call_id=c.id
                                      AND COALESCE(tr.full_text, '') <> '')
                               AS has_transcript,
                           (SELECT r.id FROM recordings_s3 r
                             WHERE r.call_id=c.id
                             ORDER BY r.created_at DESC LIMIT 1)
                               AS recording_id,
                           (SELECT r.status FROM recordings_s3 r
                             WHERE r.call_id=c.id
                             ORDER BY r.created_at DESC LIMIT 1)
                               AS recording_status,
                           (SELECT NULLIF(u.policy_snapshot->>'actual_seconds', '')::int
                             FROM inbound_usage_transactions u
                             WHERE u.call_id=c.id
                               AND u.call_leg_id IS NULL
                               AND u.transaction_type IN ('finalize','release','reverse')
                             ORDER BY u.created_at DESC LIMIT 1)
                               AS billed_duration_seconds,
                           (SELECT leg.from_number FROM call_legs leg
                             WHERE leg.call_id = c.id
                               AND leg.direction = 'outbound'
                               AND NULLIF(BTRIM(leg.from_number), '') IS NOT NULL
                             ORDER BY leg.created_at, leg.id LIMIT 1)
                               AS outbound_from_number,
                           to_jsonb(c)->'summary_json' AS persisted_summary_json
                    FROM calls c
                    LEFT JOIN campaigns camp ON camp.id=c.campaign_id
                    WHERE c.id=$1 AND c.tenant_id=$2
                    """,
                    call_uuid,
                    tenant_uuid,
                )
                if not call_row:
                    raise HTTPException(status_code=404, detail="Call not found")
                leg_rows = await conn.fetch(
                    """
                    SELECT id, leg_type, direction, provider, provider_leg_id,
                           from_number, to_number, status, started_at,
                           answered_at, ended_at, duration_seconds, metadata
                    FROM call_legs
                    WHERE call_id=$1 AND leg_type ILIKE '%transfer%'
                    ORDER BY created_at
                    """,
                    call_uuid,
                )

        call = dict(call_row)
        recording_id = call.get("recording_id")

        # Normalize summary_json: asyncpg may return JSONB as str or dict
        raw_summary_json = call.get("persisted_summary_json") or call.get("summary_json")
        if isinstance(raw_summary_json, str):
            try:
                summary_json = json.loads(raw_summary_json)
            except (json.JSONDecodeError, ValueError):
                summary_json = None
        elif isinstance(raw_summary_json, dict):
            summary_json = raw_summary_json
        else:
            summary_json = None

        snapshot, checksum, route_id = _route_metadata(call)
        direction = call.get("direction") or "outbound"
        inbound_from = _display_caller_ani(call)
        display_from = _display_from_number(call)
        display_to = (
            call.get("called_did") if direction == "inbound" else call.get("phone_number")
        ) or ""
        transfer_legs = []
        for leg in leg_rows:
            item = dict(leg)
            for key in ("id",):
                if item.get(key) is not None:
                    item[key] = str(item[key])
            for key in ("started_at", "answered_at", "ended_at"):
                if item.get(key) is not None and hasattr(item[key], "isoformat"):
                    item[key] = item[key].isoformat()
            item["metadata"] = _json_object(item.get("metadata"))
            transfer_legs.append(item)

        created_at = call.get("created_at", "")
        return CallDetail(
            id=str(call["id"]),
            talklee_call_id=call.get("talklee_call_id"),
            timestamp=(
                created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
            ),
            from_number=display_from,
            to_number=display_to,
            status=call.get("status", "unknown"),
            duration_seconds=call.get("duration_seconds"),
            outcome=call.get("outcome"),
            transcript=call.get("transcript") or call.get("persisted_transcript"),
            recording_id=str(recording_id) if recording_id is not None else None,
            campaign_id=str(call["campaign_id"]) if call.get("campaign_id") else None,
            lead_id=str(call["lead_id"]) if call.get("lead_id") else None,
            summary=call.get("summary"),
            summary_json=summary_json,
            campaign_name=call.get("campaign_name"),
            direction=direction,
            provider=call.get("provider"),
            provider_call_id=call.get("provider_call_id"),
            caller_ani=inbound_from,
            called_did=call.get("called_did"),
            inbound_campaign_id=(_inbound_config_id(call)),
            assignment_id=str(call["assignment_id"]) if call.get("assignment_id") else None,
            route_id=route_id,
            route_version=call.get("route_version"),
            config_version=call.get("config_version"),
            config_checksum=checksum,
            ingress=call.get("ingress"),
            route_snapshot=snapshot if direction == "inbound" else None,
            admission_status=call.get("admission_status"),
            admission_reason=call.get("admission_reason"),
            consent_status=call.get("consent_status"),
            processing_status=call.get("processing_status"),
            billing_status=call.get("billing_status"),
            billing_hold_reason=call.get("billing_hold_reason"),
            reserved_seconds=call.get("reserved_seconds"),
            recording_status=_recording_state(call, snapshot),
            transcript_status=_transcript_state(call),
            media_state=_media_state(call),
            answer_delay_seconds=(
                _duration_between(call.get("started_at"), call.get("answered_at"))
                if call.get("answered_at")
                else None
            ),
            conversation_duration_seconds=(
                _duration_between(call.get("answered_at"), call.get("ended_at"))
                if call.get("answered_at")
                else None
            ),
            billed_duration_seconds=call.get("billed_duration_seconds"),
            cost=float(call["cost"]) if call.get("cost") is not None else None,
            transfer_legs=transfer_legs,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch call {call_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch call")


@router.get("/{call_id}/transcript")
async def get_call_transcript(
    call_id: str,
    format: str = Query("json", description="Format: 'json' or 'text'"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
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
        transcript_response = (
            db_client.table("transcripts")
            .select("turns, full_text, word_count, turn_count, created_at")
            .eq("call_id", call_id)
            .execute()
        )

        if transcript_response.data and len(transcript_response.data) > 0:
            transcript_data = transcript_response.data[0]

            if format == "text":
                return {
                    "format": "text",
                    "transcript": transcript_data.get("full_text", ""),
                    "call_id": call_id,
                }
            else:
                return {
                    "format": "json",
                    "turns": transcript_data.get("turns", []),
                    "metadata": {
                        "word_count": transcript_data.get("word_count", 0),
                        "turn_count": transcript_data.get("turn_count", 0),
                        "created_at": transcript_data.get("created_at"),
                    },
                    "call_id": call_id,
                }

        # Fallback to calls table transcript fields
        call_response = (
            db_client.table("calls")
            .select("transcript, transcript_json")
            .eq("id", call_id)
            .single()
            .execute()
        )

        if not call_response.data:
            raise HTTPException(status_code=404, detail="Call not found")

        call_data = call_response.data

        if format == "text":
            return {
                "format": "text",
                "transcript": call_data.get("transcript", ""),
                "call_id": call_id,
            }
        else:
            return {
                "format": "json",
                "turns": call_data.get("transcript_json", []),
                "metadata": {},
                "call_id": call_id,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch transcript for call {call_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch transcript")


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
    db_client: Client = Depends(get_db_client),
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
            "count": len(response.data) if response.data else 0,
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
    db_client: Client = Depends(get_db_client),
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

        response = (
            db_client.table("call_legs")
            .select("*")
            .eq("call_id", call_id)
            .order("created_at", desc=False)
            .execute()
        )

        return {
            "call_id": call_id,
            "legs": response.data or [],
            "count": len(response.data) if response.data else 0,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch legs for call {call_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch call legs")
