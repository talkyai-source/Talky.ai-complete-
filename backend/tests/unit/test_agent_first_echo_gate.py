"""F-10 extended to the AGENT-FIRST pre-synth greeting path.

Prod 2026-07-20 (two consecutive live calls) showed the outbound agent-first
greeting being truncated ~1s in (elapsed_ms≈1107/906, interrupted=True): the
callee's pickup "Hello?" echoed back as a StartOfTurn and armed the barge-in
event mid-greeting. instant_opener.is_opener_echo already immunizes the
caller-first path; these tests prove the agent-first greeting player
(agent_first._send_outbound_greeting) now arms the SAME content-aware window so:

  1. a bare-greeting echo during playback does NOT interrupt the greeting;
  2. real content ("stop") during playback DOES interrupt, exactly as before;
  3. after the grace window expires there is no permanent immunity — a bare
     greeting arms the event normally again.

The real _send_outbound_greeting fast path is driven end-to-end against a real
VoicePipelineService so the composition with handle_barge_in's is_opener_echo
first-check is genuinely exercised, not mocked.
"""
import asyncio
import time

import pytest

from app.domain.models.session import CallSession
from app.domain.services.telephony.modes.agent_first import _send_outbound_greeting
from app.domain.services.voice_pipeline.instant_opener import is_opener_echo
from app.domain.services.voice_pipeline_service import VoicePipelineService


_GREETING_TEXT = "Hi, James here from Allstate Estimation, how are you today?"
_CHUNKS = [b"c0", b"c1", b"c2", b"c3", b"c4"]


def _session():
    return CallSession(
        call_id="call-1", campaign_id="c", lead_id="l", provider_call_id="p",
        system_prompt="Use plain spoken text.", voice_id="v",
    )


class _FakeMediaGateway:
    """Media gateway whose send_audio fires a barge-in mid-greeting.

    On the 2nd chunk it calls the real ``svc.handle_barge_in`` with the
    configured transcript — reproducing an STT-driven barge-in landing while
    the greeting is still playing (session.tts_active is True at that point).
    """

    def __init__(self, svc, session, transcript):
        self._svc = svc
        self._session = session
        self._transcript = transcript
        self.send_calls = 0
        self.clear_output_calls = 0
        self.flush_calls = 0

    async def send_audio(self, call_id, chunk):
        self.send_calls += 1
        if self.send_calls == 2:
            await self._svc.handle_barge_in(
                self._session, None, transcript_text=self._transcript
            )

    async def clear_output_buffer(self, call_id):
        self.clear_output_calls += 1

    async def flush_tts_buffer(self, call_id):
        self.flush_calls += 1


class _FakeVoiceSession:
    def __init__(self, svc, session, media_gateway):
        self.call_id = session.call_id
        self.call_session = session
        self.pipeline = svc
        self.media_gateway = media_gateway
        self._presynth_greeting_audio = list(_CHUNKS)
        self._presynth_greeting_text = _GREETING_TEXT
        self._first_speaker = "agent"


def _make_svc():
    from unittest.mock import AsyncMock
    return VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=AsyncMock(),
        tts_provider=AsyncMock(), media_gateway=AsyncMock(),
    )


def _run_greeting(transcript):
    """Run the real fast-path greeting with a barge-in of ``transcript`` fired
    on the 2nd chunk. Returns (session, media_gateway)."""
    s = _session()
    svc = _make_svc()
    # Register the shared barge-in Event so session.barge_in_event IS the object
    # handle_barge_in arms (exactly as the live pipeline wires it).
    svc._barge_in_event_for(s)
    mg = _FakeMediaGateway(svc, s, transcript)
    vs = _FakeVoiceSession(svc, s, mg)

    async def scenario():
        await _send_outbound_greeting(vs)

    asyncio.run(scenario())
    return s, mg


def test_bare_greeting_echo_does_not_interrupt_agent_first_greeting():
    """Scenario 1: bare-greeting echo ('hello') during agent-first greeting →
    greeting is NOT interrupted (event never armed): all chunks sent, full
    greeting persisted, no output-buffer clear from a barge-in."""
    s, mg = _run_greeting("hello")

    assert mg.send_calls == len(_CHUNKS)          # played to completion
    assert mg.flush_calls == 1                     # normal (non-interrupted) flush
    assert not s.barge_in_event.is_set()           # echo gate refused to arm
    # Full greeting persisted (no truncation ellipsis).
    last = s.conversation_history[-1]
    assert last.content == _GREETING_TEXT
    assert not last.content.endswith("…")
    # Echo window closed correctly; caller-first once-guard left untouched.
    assert s._instant_opener_in_flight is False
    assert s._instant_opener_grace_until > time.monotonic() - 1.0
    assert getattr(s, "_instant_opener_done", None) is not True


def test_real_interrupt_cuts_agent_first_greeting():
    """Scenario 2: real content ('stop') during greeting → interrupted exactly
    as today: playback breaks early, output buffer cleared, only the spoken
    fraction persisted (truncation ellipsis)."""
    s, mg = _run_greeting("stop")

    assert mg.send_calls < len(_CHUNKS)            # broke out early
    assert mg.clear_output_calls >= 1              # audio flushed on interrupt
    last = s.conversation_history[-1]
    assert last.content.endswith("…")             # only spoken portion kept
    assert last.content != _GREETING_TEXT
    assert s._instant_opener_in_flight is False


def test_no_permanent_immunity_after_grace_expiry():
    """Scenario 3: once the greeting window is closed and its grace has
    expired, a bare greeting is a normal barge-in again — handle_barge_in arms
    the event (no time-only permanent suppression)."""
    s = _session()
    svc = _make_svc()
    event = svc._barge_in_event_for(s)
    # Simulate a call where the greeting finished and the grace already lapsed.
    s._instant_opener_in_flight = False
    s._instant_opener_grace_until = time.monotonic() - 1.0
    s.tts_active = True  # some later agent turn is now audibly playing

    assert not is_opener_echo(s, "hello")          # outside the window: not echo
    asyncio.run(svc.handle_barge_in(s, None, transcript_text="hello"))
    assert event.is_set()                          # armed → real interrupt


def test_gate_composition_ignores_echo_while_in_flight():
    """Direct proof of the composition claim: with the agent-first flags set as
    _send_outbound_greeting sets them, handle_barge_in treats a bare greeting as
    an echo and does NOT arm the event."""
    s = _session()
    svc = _make_svc()
    event = svc._barge_in_event_for(s)
    s._instant_opener_in_flight = True
    s.tts_active = True

    assert is_opener_echo(s, "hello")
    asyncio.run(svc.handle_barge_in(s, None, transcript_text="hello"))
    assert not event.is_set()                      # echo ignored, greeting safe
