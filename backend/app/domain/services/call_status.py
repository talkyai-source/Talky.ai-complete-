"""Per-call status transitions and outcome mapping.

A single place that owns the call lifecycle:

* ``CallState`` enum — every status the UI may render.
* ``record_call_state()`` — UPDATEs ``calls.status`` (+ correct
  timestamp) and emits a structured ``stream_events`` row so the
  live-calls panel can pick it up.
* ``classify_hangup_cause()`` — maps Q.850 / SIP hangup causes to a
  short, user-friendly outcome string (``answered``, ``busy``,
  ``no_answer``, ``voicemail``, …).

Why this lives in one module: the call passes through three subsystems
(dialer worker, telephony bridge, ARI adapter) and each used to do its
own ``UPDATE calls SET status=…`` with no shared vocabulary. Centralising
the transitions here gives us:

* one place to add observability,
* one canonical state machine to reason about,
* one shape for the live-calls SSE/polling feed.

The state machine intentionally accepts any forward transition. ARI
events arrive out of order under load (StasisStart racing
ChannelStateChange(Up) is a real bug we've already seen and worked
around) — refusing "illegal" transitions would re-introduce that
race. We log unexpected transitions so they're traceable.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# State machine
# ─────────────────────────────────────────────────────────────────────


class CallState(str, Enum):
    """States a call may be in, in roughly time order.

    Values are written verbatim into ``calls.status`` and into the
    ``stream_events.metadata.state`` field consumed by the live-calls
    panel. Renaming is a breaking change for that panel.
    """
    QUEUED    = "queued"     # job picked up, originate not yet sent
    DIALING   = "dialing"    # bridge accepted originate, channel created
    RINGING   = "ringing"    # remote side is ringing (StasisStart fired)
    ANSWERED  = "answered"   # remote picked up (ChannelStateChange Up)
    IN_CALL   = "in_call"    # media flowing, AI conversation in progress
    ENDED     = "ended"      # hangup received; outcome stored separately

    # Backwards-compat aliases for older calls.status values already in
    # production rows so the API can serialise them too.
    INITIATED = "initiated"
    COMPLETED = "completed"
    FAILED    = "failed"


# Maps a CallState to the matching timestamp column on `calls`. None
# means "this state doesn't have a dedicated timestamp column".
_STATE_TIMESTAMP_COLUMN: dict[CallState, Optional[str]] = {
    CallState.QUEUED:    None,
    CallState.DIALING:   "started_at",
    CallState.RINGING:   None,
    CallState.ANSWERED:  "answered_at",
    CallState.IN_CALL:   None,
    CallState.ENDED:     "ended_at",
}


# Reflexive: lets callers pass a string or an enum interchangeably.
def coerce_state(value: str | CallState) -> CallState:
    if isinstance(value, CallState):
        return value
    try:
        return CallState(value)
    except ValueError:
        logger.warning("call_status.unknown_state value=%r — coercing to ENDED", value)
        return CallState.ENDED


# ─────────────────────────────────────────────────────────────────────
# Hangup cause classification
# ─────────────────────────────────────────────────────────────────────
#
# Q.850 cause codes — the canonical SIP/ITU mapping. Asterisk emits the
# integer cause on the Hangup ARI event. We bucket them into a small,
# user-meaningful set so the UI can show "Busy", "No answer", "Voicemail",
# "Customer hung up", etc. instead of leaking integer codes.

class CallOutcome(str, Enum):
    ANSWERED          = "answered"           # call was picked up and ran
    BUSY              = "busy"               # remote was busy
    NO_ANSWER         = "no_answer"          # rang out, no pickup
    VOICEMAIL         = "voicemail"          # detected voicemail
    REJECTED          = "rejected"           # carrier or callee declined
    UNREACHABLE       = "unreachable"        # routing failed, no circuit
    NETWORK_FAILURE   = "network_failure"    # carrier / SIP infra error
    CANCELLED         = "cancelled"          # we hung up before answer
    CUSTOMER_HUNG_UP  = "customer_hung_up"   # callee ended after answer
    AGENT_HUNG_UP     = "agent_hung_up"      # AI agent ended after answer
    FAILED            = "failed"             # uncategorised failure


_CAUSE_TO_OUTCOME: dict[int, CallOutcome] = {
    1:   CallOutcome.UNREACHABLE,       # unallocated number
    16:  CallOutcome.ANSWERED,          # normal clearing (post-answer hangup)
    17:  CallOutcome.BUSY,
    18:  CallOutcome.NO_ANSWER,         # no user responding
    19:  CallOutcome.NO_ANSWER,         # no answer from user (user alerted)
    20:  CallOutcome.NO_ANSWER,         # subscriber absent
    21:  CallOutcome.REJECTED,
    22:  CallOutcome.UNREACHABLE,       # number changed
    27:  CallOutcome.UNREACHABLE,       # destination out of order
    28:  CallOutcome.UNREACHABLE,       # invalid number format
    31:  CallOutcome.FAILED,            # normal, unspecified
    34:  CallOutcome.NETWORK_FAILURE,   # no circuit / channel available
    38:  CallOutcome.NETWORK_FAILURE,   # network out of order
    41:  CallOutcome.NETWORK_FAILURE,   # temporary failure
    42:  CallOutcome.NETWORK_FAILURE,   # switching equipment congestion
    44:  CallOutcome.NETWORK_FAILURE,   # requested channel not available
    127: CallOutcome.FAILED,            # interworking, unspecified
}


def classify_hangup_cause(
    cause: Optional[int],
    *,
    answered: bool,
    hung_up_by: Optional[str] = None,   # "agent" | "customer" | None
) -> CallOutcome:
    """Bucket a hangup into a user-friendly outcome.

    ``answered`` — true if the call had reached ANSWERED before hangup.
    A clean Q.850 cause 16 means different things in either case
    (post-answer normal clearing vs pre-answer normal abort).

    ``hung_up_by`` — when the bridge can tell who initiated the hangup
    (customer vs AI agent), pass it through so the UI can distinguish.
    Most carriers don't expose this; leave None and we fall back to a
    generic ANSWERED/CANCELLED bucket.
    """
    if cause is None and answered:
        return CallOutcome.ANSWERED if not hung_up_by else (
            CallOutcome.CUSTOMER_HUNG_UP if hung_up_by == "customer"
            else CallOutcome.AGENT_HUNG_UP
        )
    if cause is None:
        return CallOutcome.CANCELLED
    if not answered and cause == 16:
        return CallOutcome.CANCELLED
    outcome = _CAUSE_TO_OUTCOME.get(cause, CallOutcome.FAILED)
    # Refine ANSWERED → CUSTOMER/AGENT_HUNG_UP when we know who hung up.
    if outcome == CallOutcome.ANSWERED and hung_up_by:
        return CallOutcome.CUSTOMER_HUNG_UP if hung_up_by == "customer" else CallOutcome.AGENT_HUNG_UP
    return outcome


# ─────────────────────────────────────────────────────────────────────
# State recorder
# ─────────────────────────────────────────────────────────────────────


async def record_call_state_by_provider_id(
    db_pool,
    *,
    provider_call_id: str,
    new_state: str | CallState,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Resolve a provider channel-id (Asterisk channel, FreeSWITCH UUID, …)
    to the internal call row, then call :func:`record_call_state`.

    Telephony lifecycle hooks (``_on_ringing``, ``_on_new_call``,
    ``_on_call_ended``) only see the provider's channel id — the
    internal ``calls.id`` UUID is hidden behind the
    ``external_call_uuid`` foreign-key column. This wrapper does the
    one extra SELECT so callers don't have to.

    Returns silently when no matching call row is found (e.g. inbound
    call that never went through our dialer, or an early-hangup race
    where the INSERT hasn't landed yet). The state emit is best-effort
    by design.
    """
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, campaign_id
                FROM calls
                WHERE external_call_uuid = $1
                LIMIT 1
                """,
                provider_call_id,
            )
    except Exception as exc:
        logger.warning(
            "call_status.resolve_failed provider_id=%s err=%s",
            provider_call_id, exc,
        )
        return

    if not row:
        # Inbound, race, or test channel — not an error per se.
        logger.debug(
            "call_status.no_call_for_provider provider_id=%s state=%s",
            provider_call_id, new_state,
        )
        return

    await record_call_state(
        db_pool,
        call_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        campaign_id=str(row["campaign_id"]) if row["campaign_id"] else None,
        new_state=new_state,
        metadata=metadata,
    )


async def record_call_state(
    db_pool,
    *,
    call_id: str,
    tenant_id: str,
    new_state: str | CallState,
    campaign_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Transition a call to ``new_state``.

    Performs two writes:
      1. ``UPDATE calls SET status = …`` plus the matching timestamp
         column (``answered_at``, ``ended_at``, …) when applicable.
      2. ``emit_event`` into ``stream_events`` with category ``call``
         and ``metadata.state = new_state`` — that's what the
         live-calls feed consumes.

    Both writes are wrapped in best-effort try/except blocks so a status
    update never bubbles up to crash an ARI callback. Lost transitions
    surface as ``WARNING`` log lines — the next event will still arrive
    and the UI catches up.
    """
    state = coerce_state(new_state)
    ts_col = _STATE_TIMESTAMP_COLUMN.get(state)

    # Forward-only guard for EARLY states. ARI events race under load
    # (a late RINGING task can land after ANSWERED), and early-ringing
    # signals may repeat. Never let an early state overwrite a live or
    # terminal one — a call the UI shows as "answered" must not snap
    # back to "ringing". Live/terminal states still accept any forward
    # transition (see module docstring on out-of-order ARI events).
    _early = state in (
        CallState.QUEUED, CallState.DIALING, CallState.RINGING, CallState.INITIATED,
    )
    _guard_sql = (
        " AND NOT (status = ANY('{answered,in_call,ended,completed,failed}'))"
        if _early else ""
    )

    # 1. UPDATE the calls table.
    try:
        async with db_pool.acquire() as conn:
            if ts_col:
                # COALESCE so we never clobber an earlier real timestamp
                # if the same state event arrives twice (race-tolerant).
                sql = (
                    f"UPDATE calls SET status = $1, {ts_col} = COALESCE({ts_col}, NOW()), "
                    f"updated_at = NOW() WHERE id = $2{_guard_sql}"
                )
            else:
                sql = f"UPDATE calls SET status = $1, updated_at = NOW() WHERE id = $2{_guard_sql}"
            result = await conn.execute(sql, state.value, call_id)
            if _early and result == "UPDATE 0":
                # Downgrade blocked (or row gone) — skip the stream event
                # too so the live panel never regresses either.
                logger.debug(
                    "call_status.downgrade_skipped call=%s state=%s",
                    call_id, state.value,
                )
                return
    except Exception as exc:
        logger.warning(
            "call_status.update_failed call=%s state=%s err=%s",
            call_id, state.value, exc,
        )

    # 2. Emit a structured stream_events row.
    try:
        from app.domain.services.event_emitter import emit_event_via_pool
        payload = {
            "state": state.value,
            "call_id": str(call_id),
        }
        if metadata:
            payload.update({k: v for k, v in metadata.items() if k != "state"})
        await emit_event_via_pool(
            db_pool,
            tenant_id=str(tenant_id),
            category="call",
            title=f"Call {state.value}",
            description=metadata.get("description") if metadata else None,
            related_campaign_id=str(campaign_id) if campaign_id else None,
            related_call_id=str(call_id),
            metadata=payload,
        )
    except Exception as exc:
        logger.warning(
            "call_status.emit_failed call=%s state=%s err=%s",
            call_id, state.value, exc,
        )
