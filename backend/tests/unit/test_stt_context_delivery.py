"""Recognition context must not describe empty or interrupted agent speech."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.services.telephony.modes import agent_first
from app.domain.services.voice_orchestrator import VoiceOrchestrator, VoiceSessionConfig
from app.domain.services.voice_pipeline.tts_playback import TtsPlayback

TEXT = "Hello, this is Sarah from Talky. What is your email address?"
AUDIO = b"\x01\x02" * 80


class Gateway:
    def __init__(self):
        self.send_audio = AsyncMock()
        self.flush_audio_buffer = AsyncMock()
        self.flush_tts_buffer = AsyncMock()
        self.clear_output_buffer = AsyncMock()


def speech(chunks=(AUDIO,), failure=None):
    async def stream_synthesize(*args, **kwargs):
        for data in chunks:
            yield SimpleNamespace(data=data)
        if failure is not None:
            raise failure
    return SimpleNamespace(stream_synthesize=stream_synthesize, name="synthetic-tts")


def session():
    return SimpleNamespace(
        call_id="synthetic-context-call", talklee_call_id="synthetic-context-id",
        turn_id=0, llm_active=False, tts_active=False, voice_id="synthetic-voice",
        barge_in_event=asyncio.Event(), conversation_history=[],
        current_ai_response="", current_user_input="",
    )


@pytest.mark.parametrize("case", ["complete", "empty_chunk", "late_barge_in", "negative_receipt", "cancelled"])
async def test_browser_greeting_context_requires_nonempty_uninterrupted_submission(case):
    call = session()
    gateway = Gateway()
    gateway.start_playback_tracking = Mock()
    stt = SimpleNamespace(update_agent_context=AsyncMock())

    async def wait_for_playback_complete(call_id):
        if case == "late_barge_in":
            call.barge_in_event.set()
        return case not in {"late_barge_in", "negative_receipt"}

    gateway.wait_for_playback_complete = wait_for_playback_complete
    voice = SimpleNamespace(
        call_id=call.call_id, call_session=call, media_gateway=gateway, stt_provider=stt,
        config=VoiceSessionConfig(mute_during_tts=False),
        tts_provider=speech((b"",) if case == "empty_chunk" else (AUDIO,),
                            asyncio.CancelledError() if case == "cancelled" else None),
    )
    if case == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await VoiceOrchestrator().send_greeting(voice, TEXT, AsyncMock())
    else:
        await VoiceOrchestrator().send_greeting(voice, TEXT, AsyncMock())
    if case == "complete":
        stt.update_agent_context.assert_awaited_once_with(call.call_id, TEXT)
    else:
        stt.update_agent_context.assert_not_awaited()
    if case == "empty_chunk":
        gateway.send_audio.assert_not_awaited()


@pytest.mark.parametrize("case", ["complete", "late_barge_in", "late_state_interrupt", "negative_receipt", "flush_failure", "cancelled"])
async def test_reply_context_rechecks_interruptions_after_output_flush(case):
    call = session()
    gateway = Gateway()
    stt = SimpleNamespace(update_agent_context=AsyncMock(), mute=AsyncMock(), unmute=AsyncMock())

    async def flush(call_id):
        if case == "late_barge_in":
            call.barge_in_event.set()
        elif case == "late_state_interrupt":
            call.tts_active = False
        elif case == "flush_failure":
            raise RuntimeError("synthetic flush failure")

    gateway.flush_tts_buffer = flush
    websocket = None
    if case == "negative_receipt":
        # The older browser gateway contract returns an explicit negative
        # receipt rather than raising; it must not authorize context either.
        call._mute_during_tts = True
        gateway.wait_for_playback_complete = AsyncMock(return_value=False)
        websocket = AsyncMock()
    pipe = SimpleNamespace(
        stt_provider=stt, media_gateway=gateway,
        tts_provider=speech(failure=asyncio.CancelledError() if case == "cancelled" else None),
        tts_sample_rate=16000, latency_tracker=Mock(), _record_silent_turn=Mock(),
    )
    invocation = TtsPlayback(pipe).synthesize_and_send(
        call, TEXT, websocket, barge_in_event=call.barge_in_event, track_latency=False,
    )
    if case == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await invocation
    else:
        await invocation
    if case == "complete":
        stt.update_agent_context.assert_awaited_once_with(call.call_id, TEXT)
    else:
        stt.update_agent_context.assert_not_awaited()


@pytest.mark.parametrize("case", ["complete", "late_barge_in", "late_state_interrupt", "flush_failure", "cancelled"])
async def test_presynthesized_greeting_context_rechecks_after_flush(case, monkeypatch):
    call = session()
    gateway = Gateway()
    stt = SimpleNamespace(update_agent_context=AsyncMock())

    async def flush(call_id):
        if case == "late_barge_in":
            call.barge_in_event.set()
        elif case == "late_state_interrupt":
            call.tts_active = False
        elif case == "flush_failure":
            raise RuntimeError("synthetic flush failure")
        elif case == "cancelled":
            raise asyncio.CancelledError()

    gateway.flush_tts_buffer = flush
    monkeypatch.setattr(agent_first, "_speak_recording_disclosure", AsyncMock())
    voice = SimpleNamespace(
        call_id=call.call_id, call_session=call, stt_provider=stt, media_gateway=gateway,
        pipeline=SimpleNamespace(clear_barge_in_event=Mock(), transcript_service=Mock()),
        _presynth_greeting_audio=[AUDIO], _presynth_greeting_text=TEXT,
    )
    if case == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await agent_first._send_outbound_greeting(voice)
    else:
        await agent_first._send_outbound_greeting(voice)
    if case == "complete":
        stt.update_agent_context.assert_awaited_once_with(call.call_id, TEXT)
    else:
        stt.update_agent_context.assert_not_awaited()
