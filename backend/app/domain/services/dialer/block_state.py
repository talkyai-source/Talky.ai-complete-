"""Per-campaign "why nothing is dialling right now" state, published live.

Surface reuse (deliberate — no new real-time transport was invented):

  * ``stream_events`` — the table behind ``GET /api/v1/events``, the Event
    Stream panel the dashboard already polls. Rows carry
    ``related_campaign_id``, so a blocking reason is per-campaign for free.
    Written through ``event_emitter.emit_event_via_pool``, the same fire-and-
    forget path ``dialer_worker`` already uses for progress / out-of-minutes.
  * Redis — holds the CURRENT reason as JSON under one key per campaign, so
    the campaign page can answer "why is this campaign not dialling?" in one
    O(1) read instead of scanning ``dialer_jobs``. Read by
    ``GET /api/v1/campaigns/{id}/stats``, which the campaign detail page
    already polls every 7 seconds.

Emit-on-CHANGE, never per poll
------------------------------
A blocked campaign re-evaluates its jobs continuously; naively emitting one
event per evaluation would bury the Event Stream in thousands of identical
rows. The Redis key doubles as the dedup marker: an event is written only
when the ``code`` differs from the code already stored (plus a slow
``REEMIT_AFTER_S`` heartbeat so a long block doesn't scroll off the panel and
vanish). Everything else just refreshes the stored state and its TTL.

Fail-open everywhere: this is observability. A Redis or DB problem must never
change whether a call is placed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.services.dialer.block_reasons import (
    SELF_CLEARING_CODES,
    BlockCode,
    BlockReason,
)

logger = logging.getLogger(__name__)

#: How long a stored reason stays readable without a refresh. Comfortably
#: longer than the dialer's own poll cadence so the campaign page never sees a
#: gap, short enough that a stopped worker's stale reason expires on its own.
STATE_TTL_S = 900

#: Re-emit an unchanged reason at most this often, so a campaign blocked for
#: hours still has a recent Event Stream row.
REEMIT_AFTER_S = 3600

#: stream_events severity vocabulary (see event_emitter.VALID_SEVERITIES)
#: keyed by our reason severity.
_SEVERITY_MAP = {"error": "critical", "warning": "warning", "info": "info"}


def state_key(campaign_id: str) -> str:
    return f"dialer:block_state:{campaign_id}"


#: Pacing/cooldown codes that come and go on EVERY dial cycle: the worker
#: publishes one, then ``clear_block_reason`` runs after the next origination,
#: so each cycle looked like a "change" and wrote a fresh "Calls are paused"
#: stream_events row. They keep their Redis state (the campaign page still
#: reads them) but never write an Event Stream row.
_QUIET_CODES = SELF_CLEARING_CODES | {BlockCode.LEAD_COOLDOWN}

_TITLES: dict[BlockCode, str] = {
    BlockCode.TESTING_MODE_SCHEDULE_BYPASSED: "TESTING MODE: schedule bypassed",
    BlockCode.SCHEDULE_DAY_NOT_ALLOWED: "Waiting for the next calling day",
    BlockCode.SCHEDULE_OUTSIDE_WINDOW: "Waiting for the calling window to open",
    BlockCode.SCHEDULE_INVALID: "Calling hours are invalid",
    BlockCode.OUT_OF_MINUTES: "Out of plan minutes",
    BlockCode.METERING_UNAVAILABLE: "Plan minutes could not be checked",
    BlockCode.CAMPAIGN_NOT_RUNNING: "Campaign is not running",
    BlockCode.CALLER_ID_NOT_VERIFIED: "Caller ID not verified",
    BlockCode.CALL_GUARD_BLOCKED: "Blocked by safety rules",
    BlockCode.DNC: "Number is on a Do-Not-Call list",
    BlockCode.VOICE_PIPELINE_UNAVAILABLE: "Voice provider not ready",
    BlockCode.LINE_NOT_READY: "Phone line not ready",
    BlockCode.TELEPHONY_UNAVAILABLE: "Calling service not connected",
    BlockCode.MAX_CONCURRENT_CALLS: "Waiting for a free call slot",
    BlockCode.DAILY_LEAD_CAP: "Daily call limit reached",
    BlockCode.ORIGINATE_FAILED: "Call could not be placed",
    # Self-clearing codes never reach the Event Stream, but the title is part
    # of the stored state the campaign page reads.
    BlockCode.LEAD_COOLDOWN: "Contact was called recently",
    BlockCode.BATCH_CAPACITY: "Waiting for the current batch",
    BlockCode.CALL_GAP: "Pacing between calls",
    BlockCode.TENANT_GAP: "Taking turns with another campaign",
    BlockCode.CALL_GUARD_THROTTLED: "Call rate limit reached",
    BlockCode.CALL_GUARD_QUEUED: "Queued behind the rate limiter",
}


def _title_for(reason: BlockReason) -> str:
    """Short Event Stream headline. The full sentence goes in description."""
    return _TITLES.get(reason.code, "Calls are paused")


def _payload(reason: BlockReason, *, emitted_at: datetime) -> dict[str, Any]:
    data = reason.to_dict()
    data["title"] = _title_for(reason)
    data["observed_at"] = emitted_at.isoformat()
    return data


async def read_block_reason(redis, campaign_id: str) -> Optional[dict[str, Any]]:
    """The campaign's current blocking reason, or None when it isn't blocked.

    Fail-open: any Redis problem reports "no known reason" rather than raising
    into a request handler.
    """
    if redis is None or not campaign_id:
        return None
    try:
        raw = await redis.get(state_key(str(campaign_id)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("block_state: read failed campaign=%s err=%s", campaign_id, exc)
        return None
    if not raw:
        return None
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("block_state: decode failed campaign=%s err=%s", campaign_id, exc)
        return None


async def publish_block_reason(
    redis,
    db_pool,
    *,
    tenant_id: Optional[str],
    campaign_id: Optional[str],
    reason: BlockReason,
    now: Optional[datetime] = None,
) -> bool:
    """Store ``reason`` as the campaign's current state and, ON CHANGE ONLY,
    write one ``stream_events`` row so the UI shows it live.

    Returns True when an Event Stream row was written (i.e. the reason was new
    or the re-emit heartbeat elapsed), False when it was a no-op refresh.
    """
    if not campaign_id:
        return False
    now = now or datetime.now(timezone.utc)
    payload = _payload(reason, emitted_at=now)

    should_emit = True
    if redis is not None:
        try:
            previous = await read_block_reason(redis, campaign_id)
            if previous and previous.get("code") == reason.code.value:
                should_emit = False
                # …unless the last emit is old enough that the Event Stream
                # row has scrolled away. Keep the ORIGINAL observed_at so the
                # UI can still say how long the block has lasted.
                prev_emitted = previous.get("emitted_at") or previous.get("observed_at")
                payload["observed_at"] = previous.get("observed_at") or payload["observed_at"]
                try:
                    last = datetime.fromisoformat(str(prev_emitted))
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if (now - last).total_seconds() >= REEMIT_AFTER_S:
                        should_emit = True
                except (TypeError, ValueError):
                    should_emit = True
            payload["emitted_at"] = (
                now.isoformat() if should_emit
                else (previous or {}).get("emitted_at") or now.isoformat()
            )
            await redis.set(
                state_key(str(campaign_id)), json.dumps(payload), ex=STATE_TTL_S,
            )
        except Exception as exc:  # noqa: BLE001 — observability must not gate dialing
            logger.debug(
                "block_state: store failed campaign=%s err=%s", campaign_id, exc,
            )
    else:
        # No Redis ⇒ no dedup marker. Emitting on every evaluation would spam
        # the Event Stream, so stay silent rather than flood; the reason is
        # still persisted on the job row and logged.
        payload["emitted_at"] = now.isoformat()
        should_emit = False

    if reason.code in _QUIET_CODES:
        # State is stored above; a self-clearing pace never writes an event.
        return False

    if not should_emit or db_pool is None or not tenant_id:
        return False

    try:
        from app.domain.services.event_emitter import emit_event_via_pool

        severity = _SEVERITY_MAP.get(reason.severity, "warning")
        await emit_event_via_pool(
            db_pool,
            tenant_id=str(tenant_id),
            # "alert" is what the dashboard treats as needs-attention; benign
            # pacing/testing notices ride the ordinary campaign category.
            category="alert" if reason.severity == "error" else "campaign",
            severity=severity,
            title=_title_for(reason),
            description=reason.message,
            related_campaign_id=str(campaign_id),
            metadata={
                "block_code": reason.code.value,
                "stage": reason.stage,
                "next_eligible_at": (
                    reason.next_eligible_at.isoformat()
                    if reason.next_eligible_at else None
                ),
                "retry_after_seconds": reason.retry_after_seconds,
                **{k: v for k, v in (reason.details or {}).items() if k != "raw"},
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("block_state: emit failed campaign=%s err=%s", campaign_id, exc)
        return False


async def clear_block_reason(redis, campaign_id: Optional[str]) -> None:
    """Drop the stored reason — called after a successful origination, so a
    campaign that starts dialling again stops reporting a stale blocker."""
    if redis is None or not campaign_id:
        return
    try:
        await redis.delete(state_key(str(campaign_id)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("block_state: clear failed campaign=%s err=%s", campaign_id, exc)
