"""Do not START talking while the caller is still talking.

WHY THIS EXISTS (2026-08-11, review finding 2)
----------------------------------------------
`handle_barge_in` deliberately protects an in-flight FINAL answer that has not
begun playing (`barge_in_ignored_final_pre_tts`). That guard is correct and must
stay: a StartOfTurn arriving while we are still generating means the caller
began a NEW utterance, not that they are interrupting audio — cancelling the
answer there deletes it and leaves the caller in silence. That was a real
production bug and the guard is what fixed it.

But the guard only decided NOT TO CANCEL. It said nothing about WHEN the
protected answer is allowed to start speaking. So the sequence

    caller starts speaking  ──►  answer finishes generating  ──►  playback starts

put the agent's voice on top of a caller who was still mid-sentence. Barge-in
could not help: barge-in stops audio that is ALREADY playing, and here the
talk-over begins at the first packet.

The fix is a hold, not a cancel. Playback waits for the caller to finish, then
speaks. The answer is preserved (no silence) and the caller is not spoken over.

BOUNDED, AND FAIL-OPEN
----------------------
A caller who never stops — background TV, a long monologue, an STT stream that
never delivers EndOfTurn — must not mute the agent forever. The wait is capped
at ``_MAX_HOLD_S``; on timeout we speak anyway. Talking over someone is bad;
never speaking again is worse.

The gate is edge-triggered per pending answer: `arm()` marks that a barge-in
landed during generation, and the hold applies to that one playback only. It is
disarmed as soon as it is consumed, so a later turn is not slowed by it.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Longest we will hold a ready answer waiting for the caller to stop. Chosen
# above a normal sentence but below the point where the caller thinks the line
# has gone dead. On timeout we speak — see module docstring.
_MAX_HOLD_S = 2.5

# How often the hold re-checks. 20ms = one PCMU frame, so the hold cannot add
# more than a single frame of latency beyond the caller actually stopping.
_POLL_S = 0.02


def mark_caller_speaking(session) -> None:
    """Caller took the floor (a real StartOfTurn, past the echo gate)."""
    session._caller_speaking = True
    session._caller_speaking_since = time.monotonic()


def mark_caller_stopped(session) -> None:
    """Caller's turn ended (EndOfTurn, or the turn was otherwise resolved)."""
    session._caller_speaking = False


def caller_is_speaking(session) -> bool:
    return bool(getattr(session, "_caller_speaking", False))


def arm_pre_tts_hold(session, reason: str = "barge_in_during_generation") -> None:
    """Mark that a barge-in landed while a FINAL answer was still generating.

    The next playback for this session will wait for the caller to stop before
    emitting its first packet. Idempotent.
    """
    session._defer_playback_for_caller = True
    session._defer_playback_reason = reason


def disarm_pre_tts_hold(session) -> None:
    session._defer_playback_for_caller = False


async def await_caller_pause(session, *, call_id: str = "") -> float:
    """Hold playback until the caller stops, or ``_MAX_HOLD_S`` elapses.

    Returns the number of seconds actually waited (0.0 when not armed, or when
    the caller was already quiet). Never raises — a broken clock or a missing
    attribute must not stop the agent from speaking.
    """
    if not getattr(session, "_defer_playback_for_caller", False):
        return 0.0

    # One-shot: consume the arm regardless of how this resolves, so a later
    # turn is never slowed by a stale flag.
    disarm_pre_tts_hold(session)

    if not caller_is_speaking(session):
        return 0.0

    reason = getattr(session, "_defer_playback_reason", "unknown")
    started = time.monotonic()
    try:
        while caller_is_speaking(session):
            waited = time.monotonic() - started
            if waited >= _MAX_HOLD_S:
                logger.warning(
                    "pre_tts_hold_timeout call=%s waited=%.2fs reason=%s — "
                    "speaking anyway; a caller who never yields must not mute "
                    "the agent permanently",
                    call_id[:12], waited, reason,
                )
                return waited
            await asyncio.sleep(_POLL_S)
    except Exception as exc:  # noqa: BLE001 — never block speech on bookkeeping
        logger.warning("pre_tts_hold error call=%s: %s", call_id[:12], exc)
        return time.monotonic() - started

    waited = time.monotonic() - started
    logger.info(
        "pre_tts_hold_released call=%s waited_ms=%.0f reason=%s — caller "
        "finished; starting playback without talking over them",
        call_id[:12], waited * 1000.0, reason,
    )
    return waited
