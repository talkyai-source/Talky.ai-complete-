"""Pause-then-decide barge-in (test call 850b885e, 2026-10-13).

The agent waited for the caller's first non-filler word before it stopped, and
AssemblyAI sends the next words about a second after a filler, so half the
interruptions took 1.2-3.1 s to stop. Now the agent holds its audio the moment
the caller starts talking, interrupts for good on real words, and resumes from
where it held when the turn ends as only a backchannel.
"""
from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import BargeInSignal
from app.domain.services.voice_pipeline import barge_in_pause
from app.domain.services.voice_pipeline.barge_in_pause import (
    pause_for_caller,
    resume_after_caller,
    wait_while_paused,
)
from app.domain.services.voice_pipeline.tts_playback import TtsPlayback
# synthesize_and_send imports this lazily; a cold first import can take seconds
# on a slow machine, which would eat the timing these tests measure.
from app.infrastructure.telephony import asterisk_adapter  # noqa: F401
from tests.unit.test_tts_empty_stream import _Pipeline, _Provider, _session


class _Socket:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message["type"])


@pytest.mark.asyncio
async def test_the_agent_holds_only_while_it_is_audibly_speaking():
    speaking, idle, ws = _session(), _session(), _Socket()
    idle.tts_active = False
    assert await pause_for_caller(speaking, ws, text="yeah") is True
    assert await pause_for_caller(idle, ws, text="yeah") is False
    assert barge_in_pause.is_paused(speaking) and not barge_in_pause.is_paused(idle)
    assert ws.sent == ["tts_pause"]
    assert await resume_after_caller(speaking, ws, reason="backchannel") is True
    assert not barge_in_pause.is_paused(speaking) and ws.sent == ["tts_pause", "tts_resume"]


@pytest.mark.asyncio
async def test_a_hold_with_no_decision_releases_itself(monkeypatch):
    monkeypatch.setattr(barge_in_pause, "PAUSE_MAX_S", 0.05)
    session, ws = _session(), _Socket()
    await pause_for_caller(session, ws)
    await asyncio.wait_for(wait_while_paused(session, ws), timeout=1.0)
    assert not barge_in_pause.is_paused(session) and ws.sent == ["tts_pause", "tts_resume"]


@pytest.mark.asyncio
async def test_a_backchannel_hold_resumes_as_soon_as_the_caller_goes_quiet(monkeypatch):
    # Live test 2026-10-14: the provider closed a lone "Yeah." 1.1 s after the
    # caller stopped, and the agent sat silent 1.5 s. The hold now ends on the
    # caller's own silence, with no playback loop waiting (the reply was
    # already with the player).
    import time

    monkeypatch.setattr(barge_in_pause, "PAUSE_MAX_S", 5.0)
    monkeypatch.setattr(barge_in_pause, "PAUSE_QUIET_RESUME_S", 0.15)
    session, ws = _session(), _Socket()
    session._caller_voice_last_at = time.monotonic()  # the "Yeah." just ended
    started = time.monotonic()
    await pause_for_caller(session, ws, text="yeah")
    for _ in range(100):
        if not barge_in_pause.is_paused(session):
            break
        await asyncio.sleep(0.02)
    assert not barge_in_pause.is_paused(session)
    assert 0.1 <= time.monotonic() - started < 1.0, "released by the caller's silence, not the 5 s cap"
    assert ws.sent == ["tts_pause", "tts_resume"]


@pytest.mark.asyncio
async def test_a_caller_who_keeps_talking_keeps_the_hold(monkeypatch):
    import time

    monkeypatch.setattr(barge_in_pause, "PAUSE_MAX_S", 5.0)
    monkeypatch.setattr(barge_in_pause, "PAUSE_QUIET_RESUME_S", 0.15)
    session, ws = _session(), _Socket()

    async def voiced_frames(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            session._caller_voice_last_at = time.monotonic()
            await asyncio.sleep(0.02)

    session._caller_voice_last_at = time.monotonic()
    await pause_for_caller(session, ws, text="uh")
    await voiced_frames(0.4)
    assert barge_in_pause.is_paused(session), "still speaking: no resume over the caller"
    await asyncio.sleep(0.4)
    assert not barge_in_pause.is_paused(session) and ws.sent == ["tts_pause", "tts_resume"]


@pytest.mark.asyncio
async def test_without_an_audio_measurement_the_hold_waits_for_the_decision(monkeypatch):
    monkeypatch.setattr(barge_in_pause, "PAUSE_MAX_S", 5.0)
    monkeypatch.setattr(barge_in_pause, "PAUSE_QUIET_RESUME_S", 0.05)
    session, ws = _session(), _Socket()  # no _caller_voice_last_at: no opinion
    await pause_for_caller(session, ws, text="yeah")
    await asyncio.sleep(0.3)
    assert barge_in_pause.is_paused(session)
    await resume_after_caller(session, ws, reason="backchannel")
    assert ws.sent == ["tts_pause", "tts_resume"]


@pytest.mark.asyncio
async def test_a_confirmed_interruption_ends_the_wait_at_once():
    session, event = _session(), asyncio.Event()
    await pause_for_caller(session)
    waiter = asyncio.create_task(wait_while_paused(session, barge_in_event=event))
    await asyncio.sleep(0.05)
    assert not waiter.done()
    event.set()
    await asyncio.wait_for(waiter, timeout=1.0)


async def _speak_with_hold(*, resolve):
    """Two chunks; the caller starts talking after the first one is sent."""
    pipe = _Pipeline(_Provider([[b"\x01\x02" * 80, b"\x03\x04" * 80]]))
    session, event = _session(), asyncio.Event()
    original_send = pipe.media_gateway.send_audio

    async def send_then_caller_talks(call_id, raw):
        await original_send(call_id, raw)
        if len(pipe.media_gateway.sent) == 1:
            await pause_for_caller(session)

            async def decide():
                await asyncio.sleep(0.05)
                assert pipe.media_gateway.sent == [b"\x01\x02" * 80]  # held, not sent
                await resolve(session, event)

            asyncio.create_task(decide())

    pipe.media_gateway.send_audio = send_then_caller_talks
    interrupted = await asyncio.wait_for(
        TtsPlayback(pipe).synthesize_and_send(
            session, "Our plans start at eleven ninety-nine.", None,
            track_latency=False, barge_in_event=event,
        ),
        timeout=30.0,
    )
    return pipe, interrupted


@pytest.mark.asyncio
async def test_after_a_backchannel_the_agent_continues_with_the_next_unsent_audio():
    async def backchannel(session, _event):
        await resume_after_caller(session, reason="backchannel")

    pipe, interrupted = await _speak_with_hold(resolve=backchannel)
    assert interrupted is False
    assert pipe.media_gateway.sent == [b"\x01\x02" * 80, b"\x03\x04" * 80]  # nothing lost or repeated


@pytest.mark.asyncio
async def test_after_real_words_the_held_audio_is_never_sent():
    async def real_interruption(session, event):
        barge_in_pause.end_pause_for_interruption(session)
        event.set()

    pipe, interrupted = await _speak_with_hold(resolve=real_interruption)
    assert interrupted is True
    assert pipe.media_gateway.sent == [b"\x01\x02" * 80]


@pytest.mark.asyncio
async def test_the_transcript_handler_routes_each_phase():
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    pipeline = types.SimpleNamespace(handle_barge_in=AsyncMock())
    handler, session, ws = TranscriptHandler(pipeline), _session(), _Socket()
    await handler.handle(session, BargeInSignal(text="yeah", phase="pause"), ws)
    assert barge_in_pause.is_paused(session)
    pipeline.handle_barge_in.assert_not_awaited()
    await handler.handle(session, BargeInSignal(text="yeah", phase="resume"), ws)
    assert not barge_in_pause.is_paused(session)
    await handler.handle(session, BargeInSignal(text="uh", phase="pause"), ws)
    await handler.handle(session, BargeInSignal(text="wait, how much?"), ws)
    assert not barge_in_pause.is_paused(session)
    pipeline.handle_barge_in.assert_awaited_once_with(session, ws, transcript_text="wait, how much?")
    assert ws.sent == ["tts_pause", "tts_resume", "tts_pause"]
