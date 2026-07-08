"""
Call Event Repository — Day 1
Persistence layer for call_events and call_legs tables.

All write operations are designed to be *non-blocking* for the main call
flow: callers should wrap calls in try/except so that a logging failure
never interrupts an active call.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class CallEventRepository:
    """
    Repository for the append-only call_events log and call_legs table.
    """

    def __init__(self, db_client: Client):
        self._db_client = db_client

    # ─── Events ──────────────────────────────────────────────────────────

    async def log_event(
        self,
        call_id: str,
        event_type: str,
        source: str,
        event_data: Optional[Dict[str, Any]] = None,
        talklee_call_id: Optional[str] = None,
        leg_id: Optional[str] = None,
        previous_state: Optional[str] = None,
        new_state: Optional[str] = None,
    ) -> Optional[str]:
        """
        Write an event to the call_events table.

        Returns the event id on success, None on failure.

        2026-07-08: this fires on every call end (hot path), so it was moved
        off the blocking postgres_adapter (`Client.table().insert().execute()`
        — blocks the event loop on the shared 4-worker thread pool AND opens
        an unpooled asyncpg connection per call) onto the pooled async
        `get_db()` connection. `get_db()` reads the exact same tenant-isolation
        contextvars (`get_current_tenant_id()` / `get_bypass_rls()`) the
        adapter did, so RLS behaviour is unchanged; call_events itself has no
        RLS policy today, but we keep the same SET LOCAL sequencing for
        parity. Callers already wrap this in best-effort try/except — that
        fail-soft contract is preserved (any error is logged and swallowed,
        never raised).
        """
        event_id = str(uuid.uuid4())
        try:
            from app.core.db import get_db

            async with get_db() as conn:
                await conn.execute(
                    """
                    INSERT INTO call_events (
                        id, call_id, event_type, source, event_data, created_at,
                        talklee_call_id, leg_id, previous_state, new_state
                    ) VALUES (
                        $1::uuid, $2::uuid, $3, $4, $5::jsonb, $6,
                        $7, $8::uuid, $9, $10
                    )
                    """,
                    event_id,
                    call_id,
                    event_type,
                    source,
                    json.dumps(event_data or {}, default=str),
                    datetime.now(timezone.utc),
                    talklee_call_id,
                    leg_id,
                    previous_state,
                    new_state,
                )
            logger.debug(f"Call event logged: {event_type} for call {call_id}")
            return event_id
        except Exception as e:
            logger.warning(f"Failed to log call event ({event_type}): {e}")
            return None

    async def list_events(
        self,
        call_id: str,
        limit: int = 100,
    ) -> list:
        """Retrieve events for a call, ordered by creation time."""
        try:
            response = (
                self._db_client.table("call_events")
                .select("*")
                .eq("call_id", call_id)
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )
            if getattr(response, "error", None):
                logger.warning(f"Failed to list call events for {call_id}: {response.error}")
                return []
            return response.data or []
        except Exception as e:
            logger.warning(f"Failed to list call events for {call_id}: {e}")
            return []

    # ─── Legs ────────────────────────────────────────────────────────────

    async def create_leg(
        self,
        call_id: str,
        leg_type: str,
        direction: str,
        provider: str,
        talklee_call_id: Optional[str] = None,
        provider_leg_id: Optional[str] = None,
        from_number: Optional[str] = None,
        to_number: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Insert a new call_leg record.

        Returns the leg id on success, None on failure.
        """
        leg_id = str(uuid.uuid4())
        record = {
            "id": leg_id,
            "call_id": call_id,
            "leg_type": leg_type,
            "direction": direction,
            "provider": provider,
            "status": "initiated",
            "started_at": datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat(),
        }
        if talklee_call_id:
            record["talklee_call_id"] = talklee_call_id
        if provider_leg_id:
            record["provider_leg_id"] = provider_leg_id
        if from_number:
            record["from_number"] = from_number
        if to_number:
            record["to_number"] = to_number
        if metadata:
            record["metadata"] = metadata

        try:
            response = self._db_client.table("call_legs").insert(record).execute()
            if getattr(response, "error", None):
                raise RuntimeError(response.error)
            logger.debug(f"Call leg created: {leg_type} for call {call_id}")
            return leg_id
        except Exception as e:
            logger.warning(f"Failed to create call leg ({leg_type}): {e}")
            return None

    async def update_leg_status(
        self,
        leg_id: str,
        status: str,
        ended_at: Optional[str] = None,
        duration_seconds: Optional[int] = None,
    ) -> bool:
        """Update a leg's status and optional timing fields."""
        update_data: Dict[str, Any] = {"status": status}
        if ended_at:
            update_data["ended_at"] = ended_at
        if duration_seconds is not None:
            update_data["duration_seconds"] = duration_seconds

        try:
            response = self._db_client.table("call_legs").update(update_data).eq("id", leg_id).execute()
            if getattr(response, "error", None):
                raise RuntimeError(response.error)
            return True
        except Exception as e:
            logger.warning(f"Failed to update leg {leg_id}: {e}")
            return False

    async def get_legs(self, call_id: str) -> list:
        """Retrieve all legs for a call."""
        try:
            response = (
                self._db_client.table("call_legs")
                .select("*")
                .eq("call_id", call_id)
                .order("created_at", desc=False)
                .execute()
            )
            if getattr(response, "error", None):
                logger.warning(f"Failed to get legs for {call_id}: {response.error}")
                return []
            return response.data or []
        except Exception as e:
            logger.warning(f"Failed to get legs for {call_id}: {e}")
            return []
