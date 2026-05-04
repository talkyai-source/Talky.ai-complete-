"""Ringing warmup state helpers."""
from __future__ import annotations

import asyncio
from typing import Optional


def alias_ringing_call_id(
    *,
    original_call_id: str,
    actual_call_id: str,
    ringing_warmups: dict[str, tuple[object, Optional[asyncio.Task]]],
    ringing_warmup_created_at: dict[str, float],
    ringing_events: dict[str, asyncio.Event],
) -> bool:
    """
    Move pre-originate warmup state when a PBX replaces the planned channel ID.

    Returns True when any warmup-related state was moved.
    """
    if not original_call_id or not actual_call_id or original_call_id == actual_call_id:
        return False

    moved = False

    warmup = ringing_warmups.pop(original_call_id, None)
    if warmup is not None and actual_call_id not in ringing_warmups:
        ringing_warmups[actual_call_id] = warmup
        moved = True

    created_at = ringing_warmup_created_at.pop(original_call_id, None)
    if created_at is not None and actual_call_id not in ringing_warmup_created_at:
        ringing_warmup_created_at[actual_call_id] = created_at
        moved = True

    evt = ringing_events.pop(original_call_id, None)
    if evt is not None and actual_call_id not in ringing_events:
        ringing_events[actual_call_id] = evt
        moved = True

    return moved
