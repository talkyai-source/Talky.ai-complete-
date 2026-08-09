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

# How long after playback STARTS a bare greeting can still be the acoustic echo
# of the caller's own pickup. Real echo is near-immediate — it is the same audio
# arriving late, not a new decision. Past this, a "Hello?" is a person who has
# been listening to the agent and is trying to get a word in. This bound is what
# stops a long agent-first greeting (recording disclosure + opener, several
# seconds) from being one giant window in which the caller cannot interrupt.
_ECHO_ONSET_WINDOW_S = 1.5

# Both barge-in arming sites call is_opener_echo for the SAME event (this is
# why the production journal shows exactly two `instant_opener_echo_ignored`
# lines per occurrence). Two calls this close are one utterance being
# double-checked, NOT the caller repeating themselves.
_ECHO_SAME_EVENT_DEBOUNCE_S = 0.4

# Words a callee uses to answer the phone content-free. If every token of the
# first utterance is from this set, the pre-synth opener is a perfect reply.
#
# `who`, `it`, `this` and `that` were removed 2026-08-04. With them present,
# `is_bare_greeting("who is this?")` returned True — the exact utterance this
# module's own docstring names as one that "deserves the LLM's specific
# answer". That had two consequences:
#
#   1. `try_instant_opener` answered "who is this?" with a canned greeting
#      instead of identifying the caller, which is the one thing a suspicious
#      callee actually asked for.
#   2. `is_opener_echo` classified a GENUINE "who is this" / "who is it" /
#      "yes this is" arriving during opener playback as the agent's own echo,
#      so `_on_barge_in_direct` returned before `event.set()` and the agent
#      talked straight over them.
#
# `there` is deliberately kept so "hi there" still matches, and `is` is kept
# so "good morning, is speaking" style pickups do — neither can form a
# question on its own once the interrogative `who` is gone.
_GREETING_WORDS = frozenset({
    "hello", "hi", "hey", "yeah", "yes", "hiya", "morning", "afternoon",
    "evening", "good", "speaking", "yep", "yo", "allo", "is", "there",
})


def is_bare_greeting(text: str) -> bool:
    """True when the utterance is a content-free pickup greeting."""
    if not text:
        return False
    words = [w.strip(".,!?'’-") for w in text.lower().split()]
    if not words or len(words) > 4:
        return False
    return all((w in _GREETING_WORDS or not w) for w in words)


def _normalise_tokens(text: str) -> frozenset:
    """Lowercased, punctuation-free token set — for opener-similarity only."""
    return frozenset(
        w for w in (t.strip(".,!?'’\"-") for t in (text or "").lower().split()) if w
    )


def _echoes_the_spoken_opener(session, text: str) -> bool:
    """True when every word of ``text`` also appears in the greeting we are
    currently playing — i.e. these are the AGENT's own words coming back.

    This is the one signal that positively identifies self-echo rather than
    merely making it plausible, so it is allowed to classify at any point in
    the playback window. Unknown opener text → False (fail toward treating the
    caller as real).
    """
    spoken = getattr(session, "_instant_opener_spoken_text", None)
    if not spoken:
        return False
    said = _normalise_tokens(text)
    return bool(said) and said <= _normalise_tokens(spoken)


def is_opener_echo(session, text) -> bool:
    """True when a StartOfTurn/BargeInSignal should be treated as the echo of
    our own greeting rather than a real interruption.

    WHY THIS IS NOT A GREETING-WORD LIST (rewritten 2026-08-08)
    ----------------------------------------------------------
    It used to be exactly that: inside the playback window, ANY bare greeting
    was echo. Production over 7 days shows what that cost — 25 events where the
    ignored text was, recovered from the log hashes, literally ``Hello?``,
    ``Hello.`` and ``Hey.``. Those were callers, and the agent talked over
    every one of them.

    The list cannot work alone because it has no way to separate two things
    that produce identical text:
      (a) the tail of the caller's OWN pickup "Hello?" re-segmented by Flux, or
          our greeting bleeding back through imperfect carrier echo cancellation
          — correct to ignore;
      (b) a caller saying "Hello?" AGAIN because the agent is talking over them
          — must interrupt.

    So the gate now uses four signals instead of one, ordered so that evidence
    of deliberate speech always beats evidence of echo:

      1. TIMING — outside playback + grace, never an echo. (unchanged)
      2. CONTENT — not a bare greeting, never an echo, so "stop"/"wait"/any
         real sentence still cuts in immediately. (unchanged)
      3. REPETITION — a bare greeting arriving as a SECOND, distinct utterance
         inside the same window is someone repeating themselves because they
         were not heard. That overrides every echo signal below, including (4).
      4. SIMILARITY / ONSET — the agent's own words coming back are echo
         whenever they land; anything else is only echo if it arrives promptly
         after playback starts. True acoustic echo of the pickup is immediate;
         a greeting five seconds into a long agent-first greeting is a person.

    Echo protection is deliberately NOT removed — the first prompt bare
    greeting, and any words matching what we are currently saying, are still
    ignored, so the agent cannot react to its own playback.

    Idempotent within one barge-in: BOTH arming sites (audio_ingest.
    _on_barge_in_direct and voice_pipeline_service.handle_barge_in) call this
    for the same event, which is why "repetition" is measured as a new
    utterance after a debounce rather than as a call count.
    """
    now = time.monotonic()

    # 1. TIMING.
    if not getattr(session, "_instant_opener_in_flight", False) and \
       now >= getattr(session, "_instant_opener_grace_until", 0.0):
        return False

    # 2. CONTENT. Real words always take the floor.
    if not is_bare_greeting(text or ""):
        return False

    # 3. REPETITION beats everything below.
    last_echo_at = getattr(session, "_instant_opener_echo_at", None)
    if last_echo_at is not None:
        if (now - last_echo_at) < _ECHO_SAME_EVENT_DEBOUNCE_S:
            return True  # second gate call for the SAME utterance — be stable
        logger.info(
            "instant_opener_echo_overridden call_id=%s — repeated greeting "
            "%.2fs after the first is a caller, not an echo",
            str(getattr(session, "call_id", "?"))[:12], now - last_echo_at,
        )
        return False

    # 4a. Our own words, coming back — echo whenever it lands.
    if _echoes_the_spoken_opener(session, text or ""):
        session._instant_opener_echo_at = now
        return True

    # 4b. Otherwise only a PROMPT greeting is plausibly echo of the pickup.
    started_at = getattr(session, "_instant_opener_started_at", None)
    if started_at is not None and (now - started_at) > _ECHO_ONSET_WINDOW_S:
        logger.info(
            "instant_opener_echo_rejected call_id=%s — greeting %.2fs into "
            "playback is too late to be pickup echo",
            str(getattr(session, "call_id", "?"))[:12], now - started_at,
        )
        return False

    session._instant_opener_echo_at = now
    return True


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
        # Arm the echo window. `_started_at` bounds how long a bare greeting can
        # still be pickup echo; `_spoken_text` lets the gate positively identify
        # our OWN words coming back; `_echo_at` is reset so a greeting ignored
        # earlier in the call cannot make this opener's first echo look like a
        # repetition. See is_opener_echo.
        session._instant_opener_started_at = time.monotonic()
        session._instant_opener_spoken_text = getattr(
            vs, "_presynth_greeting_text", None
        )
        session._instant_opener_echo_at = None
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
