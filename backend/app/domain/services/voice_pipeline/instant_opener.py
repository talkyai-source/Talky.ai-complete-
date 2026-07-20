"""Caller-first INSTANT opener — kill the 14-second "Hello?...silence" lottery.

Audited 2026-07-08: on caller-first outbound calls, the prospect's "Hello?"
triggered a FULL LLM+TTS round trip for the opener — 3s on a good tick, 14s+
under load. Two of eight live calls lost the human to that silence alone.
Meanwhile a personalised greeting was ALREADY synthesized during the ringing
phase (``_presynth_greeting_audio``) and sat unused in caller-first mode.

This module answers the first bare "Hello?" by pumping that pre-synthesized
audio straight to the gateway (~0.3s to first sound) via the existing,
battle-tested ``_send_outbound_greeting`` — barge-in handling and the
history append come with it. The LLM path is skipped for that one turn; every
later turn runs normally with the greeting correctly in history.

Safety: only fires when the first user utterance is a BARE greeting (a real
opening question like "who is this?" deserves the LLM's specific answer),
only once per call, and any failure falls through to the normal LLM turn.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# F-10: bounded echo-immunity window. The instant-opener's own greeting
# echoes back as a StartOfTurn a beat after playback starts (and playback
# itself may still be running when it arrives) — that echo must not be
# treated as a real interrupt. Scoped to actual playback (via
# ``_instant_opener_in_flight``) PLUS this short trailing grace period so a
# StartOfTurn that lands just after ``_send_outbound_greeting`` returns is
# still recognized as the same echo instead of racing to be first.
_INSTANT_OPENER_ECHO_GRACE_S = 0.7

# Words a callee uses to answer the phone content-free. If every token of the
# first utterance is from this set, the pre-synth opener is a perfect reply.
_GREETING_WORDS = frozenset({
    "hello", "hi", "hey", "yeah", "yes", "hiya", "morning", "afternoon",
    "evening", "good", "speaking", "yep", "yo", "allo", "who", "is", "it",
    "this", "there", "that",
})


def is_bare_greeting(text: str) -> bool:
    """True when the utterance is a content-free pickup greeting."""
    if not text:
        return False
    words = [w.strip(".,!?'’-") for w in text.lower().split()]
    if not words or len(words) > 4:
        return False
    return all((w in _GREETING_WORDS or not w) for w in words)


def is_opener_echo(session, text) -> bool:
    """True when a StartOfTurn/BargeInSignal should be treated as the echo of
    the instant-opener's own greeting rather than a real interruption.

    Two-part gate:
      1. TIMING — only while the opener is actually playing
         (``_instant_opener_in_flight``) or within the short trailing grace
         window after it finishes (``_instant_opener_grace_until``). Outside
         that window this is never an echo, whatever the text says.
      2. CONTENT — reuses ``is_bare_greeting``, the same classifier that
         decided to INVOKE the opener in the first place. A bare greeting
         echoing back is exempt; "stop"/"wait"/any real content is NOT a
         bare greeting and still cancels normally, so a genuine interrupt
         during opener playback is never swallowed.
    """
    if not getattr(session, "_instant_opener_in_flight", False) and \
       time.monotonic() >= getattr(session, "_instant_opener_grace_until", 0.0):
        return False
    return is_bare_greeting(text or "")


async def try_instant_opener(session, transcript: str) -> bool:
    """Play the ringing-phase pre-synth greeting as the reply to the caller's
    first bare greeting. Returns True when it played (skip the LLM turn).
    Fail-soft: any problem returns False and the normal turn proceeds."""
    try:
        if getattr(session, "_instant_opener_done", False):
            return False
        vs = getattr(session, "_voice_session_ref", None)
        if vs is None or not getattr(vs, "_presynth_greeting_audio", None):
            return False

        # The caller's greeting belongs in history BEFORE the agent's opener
        # so the next LLM turn sees the true exchange order.
        try:
            from app.domain.models.conversation import Message, MessageRole
            session.conversation_history.append(
                Message(role=MessageRole.USER, content=transcript)
            )
            session.current_user_input = ""
        except Exception:
            pass

        logger.info(
            "instant_opener call_id=%s — pre-synth greeting answers %r",
            str(session.call_id)[:12], (transcript or "")[:40],
        )
        from app.domain.services.telephony.modes.agent_first import (
            _send_outbound_greeting,
        )
        # The opener IS the answer to the caller's own "Hello?" — the trailing
        # audio/echo of that very hello fires a fresh StartOfTurn barge-in a
        # beat after playback starts. Result observed live 15:13: agent
        # permanently silent. F-10: rather than parking the barge-in event
        # (which never touched the real pipeline-dict event the STT path
        # actually arms, and didn't survive task cancellation anyway), the
        # echo is now recognized by CONTENT + this bounded in-flight/grace
        # window (see is_opener_echo) at the two places a barge-in can land
        # — _on_barge_in_direct and handle_barge_in. A real interruption
        # ("stop"/real content) is never a bare greeting, so it still cancels
        # this task normally via asyncio.CancelledError below.
        _completed = False
        session._instant_opener_in_flight = True
        try:
            await _send_outbound_greeting(vs)
            _completed = True
        except asyncio.CancelledError:
            logger.warning(
                "instant_opener_cancelled call_id=%s — real interrupt during "
                "opener playback",
                str(session.call_id)[:12],
            )
            raise
        finally:
            session._instant_opener_in_flight = False
            session._instant_opener_grace_until = (
                time.monotonic() + _INSTANT_OPENER_ECHO_GRACE_S
            )
            session._instant_opener_done = _completed
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — fall through to the LLM turn
        logger.warning(
            "instant_opener_failed call_id=%s err=%s — falling back to LLM",
            str(getattr(session, "call_id", "?"))[:12], exc,
        )
        return False
