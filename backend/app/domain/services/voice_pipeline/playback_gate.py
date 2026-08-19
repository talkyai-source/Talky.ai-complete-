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
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Longest we will hold a ready answer waiting for the caller to stop. Chosen
# above a normal sentence but below the point where the caller thinks the line
# has gone dead. On timeout we speak — see module docstring.
_MAX_HOLD_S = 2.5

# How often the hold re-checks. 20ms = one PCMU frame, so the hold cannot add
# more than a single frame of latency beyond the caller actually stopping.
_POLL_S = 0.02

# TWO WAYS OUT, BECAUSE THERE ARE TWO WAYS IN (2026-08-20)
# -------------------------------------------------------
# `_caller_speaking` is raised by StartOfTurn and lowered by EndOfTurn. Flux
# fires StartOfTurn on ANY speech-like sound but only fires EndOfTurn on a real
# turn boundary, so the two are not paired: a cough, a line blip, or a caller
# who trails off raises the flag and nothing ever lowers it.
#
# Measured over every hold in production (2026-08-17..19, 14 timeouts):
#   * 5 had an EndOfTurn that a filter swallowed  -> fixed by clearing the flag
#     at the EndOfTurn itself (see transcript_handler).
#   * 9 had NO EndOfTurn at all. Nothing to clear. Those held the full 2.5s.
#
# For those nine the only witness to whether the caller is still talking is the
# audio itself. `note_voice_activity` already stamps `_caller_voice_last_at` on
# every voiced frame (RMS >= 500, the same threshold the rest of the audio path
# uses) — the same writer that produced 236 detect_ms samples, so it is known to
# vary in production rather than being another guard wired to a constant.
#
# This window is deliberately far above an inter-word gap (50-200ms) and an
# inter-sentence pause (300-800ms upper end), and far below the 2.5s cap it
# replaces. Set to 0 to disable and restore the exact previous behaviour.
_QUIET_RELEASE_S = float(os.getenv("VOICE_PRE_TTS_QUIET_RELEASE_S", "0.7"))


def _caller_quiet_for_s(session) -> Optional[float]:
    """Seconds since the caller's last voiced frame, or None if unmeasurable.

    None means "no acoustic evidence" and MUST be read as *no opinion* — the
    hold then behaves exactly as it did before this signal existed. Never
    substitute 0.0 (which reads as "speaking right now") and never substitute a
    large number (which would release the hold on missing data — the failure
    mode that actually talks over people). Browser / ask-AI sessions never run
    the telephony ingest loop, so None is their normal state, not an error.
    """
    last = getattr(session, "_caller_voice_last_at", None)
    if last is None:
        return None
    try:
        age = time.monotonic() - float(last)
    except (TypeError, ValueError):
        return None
    return age if age >= 0.0 else None


def _fmt_ms(seconds: Optional[float]) -> str:
    """Render a measurement for the log, keeping "no measurement" visible.

    `none` and `0` mean opposite things here — one is "we could not tell", the
    other is "the caller is making noise this instant" — and collapsing them is
    how a dead signal reads as a healthy one for days.
    """
    return "none" if seconds is None else f"{seconds * 1000.0:.0f}"


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
    # Which of the two exits actually fired. Logged on every release, because
    # three guards this month were wired to signals that never varied in
    # production and their tests could not tell — the only defence that has
    # ever worked is making the wiring a fact in the journal, per call.
    released_by = "end_of_turn"
    quiet_at_release: Optional[float] = None
    try:
        while caller_is_speaking(session):
            waited = time.monotonic() - started
            if waited >= _MAX_HOLD_S:
                logger.warning(
                    "pre_tts_hold_timeout call=%s waited=%.2fs reason=%s "
                    "quiet_ms=%s — speaking anyway; a caller who never yields "
                    "must not mute the agent permanently",
                    call_id[:12], waited, reason,
                    _fmt_ms(_caller_quiet_for_s(session)),
                )
                return waited
            # No EndOfTurn is coming for 9 of every 14 holds. Ask the audio.
            quiet = _caller_quiet_for_s(session)
            if _QUIET_RELEASE_S > 0.0 and quiet is not None and quiet >= _QUIET_RELEASE_S:
                released_by = "acoustic_quiet"
                quiet_at_release = quiet
                break
            await asyncio.sleep(_POLL_S)
    except Exception as exc:  # noqa: BLE001 — never block speech on bookkeeping
        logger.warning("pre_tts_hold error call=%s: %s", call_id[:12], exc)
        return time.monotonic() - started

    waited = time.monotonic() - started
    logger.info(
        "pre_tts_hold_released call=%s waited_ms=%.0f reason=%s released_by=%s "
        "quiet_ms=%s — caller finished; starting playback without talking over "
        "them",
        call_id[:12], waited * 1000.0, reason, released_by,
        _fmt_ms(quiet_at_release if quiet_at_release is not None
                else _caller_quiet_for_s(session)),
    )
    return waited
