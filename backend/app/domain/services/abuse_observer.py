"""Record-only abuse observation for outbound calls.

Thin subclass of AbuseDetectionService. It never rejects, throttles or alters a
call: it evaluates the call-initiation rules and persists what fires.

Why not use AbuseDetectionService._record_event directly: that method inserts the
detector's suggestion ("block", "warn", "flag") into abuse_events.action_taken, but
the CHECK added by migration 0093 only admits flagged/warned/throttled/blocked/
suspended, so every insert would be rejected (and swallowed). Nothing is enforced
here, so the row says "flagged" and the suggestion is kept in action_details.
"""
from __future__ import annotations

import json
import logging
import time
from typing import List, Optional
from uuid import UUID

from app.core.db_utils import acquire_with_tenant
from app.domain.services.abuse_detection import AbuseDetectionService, AbuseEvent

logger = logging.getLogger(__name__)

# Never compete with the call path for DB connections: at most this many
# observations run at once; extra ones are dropped at once, not queued.
MAX_IN_FLIGHT = 2
_in_flight = 0
_dropped = 0
_last_drop_log = float("-inf")

# One row per (tenant, event type, destination) per hour.
DEDUP_SECONDS = 3600
_DEDUP_LOCAL_MAX = 1000
_dedup_local: dict[str, float] = {}


def _local_claim(key: str) -> bool:
    """In-process dedup used when Redis is absent or erroring (bounded)."""
    now = time.monotonic()
    if len(_dedup_local) >= _DEDUP_LOCAL_MAX:
        for k in [k for k, exp in _dedup_local.items() if exp <= now]:
            del _dedup_local[k]
        if len(_dedup_local) >= _DEDUP_LOCAL_MAX:
            return False  # full of live keys: record nothing rather than flood
    if _dedup_local.get(key, 0.0) > now:
        return False
    _dedup_local[key] = now + DEDUP_SECONDS
    return True


class AbuseObserver(AbuseDetectionService):
    async def _record_event(
        self,
        event: AbuseEvent,
        call_id: Optional[str] = None,
    ) -> Optional[UUID]:
        try:
            async with acquire_with_tenant(self._db_pool, str(event.tenant_id)) as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO abuse_events (
                        tenant_id, partner_id, event_type, severity,
                        trigger_value, threshold_value, phone_number_called,
                        call_id, action_taken, action_details, destination_country
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'flagged', $9, $10)
                    RETURNING id
                    """,
                    event.tenant_id,
                    event.partner_id,
                    event.abuse_type.value,
                    event.severity.value,
                    event.trigger_value,
                    event.threshold_value,
                    event.phone_number,
                    call_id,
                    json.dumps({**event.details, "recommended_action": event.recommended_action}),
                    event.details.get("country_code"),
                )
                return row["id"] if row else None
        except Exception as exc:  # noqa: BLE001 - recording must never surface
            logger.error("Failed to record abuse event: %s", type(exc).__name__)
            return None

    async def _claim(self, event: AbuseEvent) -> bool:
        key = f"abuse:obs:{event.tenant_id}:{event.abuse_type.value}:{event.phone_number or '-'}"
        if self._redis is not None:
            try:
                return bool(await self._redis.set(key, "1", nx=True, ex=DEDUP_SECONDS))
            except Exception as exc:  # noqa: BLE001 - fail open, capped locally
                logger.warning("abuse_observe_dedup_redis_error error=%s", type(exc).__name__)
        return _local_claim(key)

    async def observe_outbound_call(
        self,
        tenant_id: str,
        phone_number: str,
        call_id: Optional[str] = None,
    ) -> List[AbuseEvent]:
        """Evaluate and record; meant to run as a background task, result discarded.

        Uses the app's main db_pool: 2 reads (rapid-repeat, sequential) plus one
        INSERT per newly recorded event. Dropped at once when 2 are in flight.
        """
        global _in_flight, _dropped, _last_drop_log
        if _in_flight >= MAX_IN_FLIGHT:
            _dropped += 1
            now = time.monotonic()
            if now - _last_drop_log >= 60:
                _last_drop_log = now
                logger.warning("abuse_observe_dropped total_dropped=%d", _dropped)
            return []
        _in_flight += 1
        try:
            events = await self.analyze_call_initiated(str(tenant_id), phone_number)
            for event in events:
                if await self._claim(event):
                    await self._record_event(event, call_id=call_id)
        finally:
            _in_flight -= 1
        logger.info(
            "abuse_observe tenant=%s dest_prefix=%s country=%s call=%s events=%s",
            str(tenant_id)[:8],
            phone_number[:5],
            self._extract_country_code(phone_number),
            (call_id or "-")[:8],
            [e.abuse_type.value for e in events],
        )
        return events
