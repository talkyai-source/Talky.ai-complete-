import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService


class EmptySTTProvider:
    async def stream_transcribe(self, *args, **kwargs):
        if False:
            yield None


class StreamingLLMProvider:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_chat_with_timeout(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


class StreamingTTSProvider:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_synthesize(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


def _make_session() -> CallSession:
    session = CallSession(
        call_id="call-123",
        campaign_id="campaign-123",
        lead_id="lead-123",
        provider_call_id="provider-123",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-123",
    )
    session.barge_in_event = asyncio.Event()
    return session


@pytest.mark.asyncio
async def test_process_audio_stream_does_not_start_turn_before_user_speech():
    media_gateway = MagicMock()
    media_gateway.get_audio_queue.return_value = asyncio.Queue()

    service = VoicePipelineService(
        stt_provider=EmptySTTProvider(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=media_gateway,
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    session.stt_active = False

    await service.process_audio_stream(session)

    service.latency_tracker.start_turn.assert_not_called()
    service.latency_tracker.mark_listening_start.assert_not_called()


@pytest.mark.asyncio
async def test_get_llm_response_marks_first_token_latency():
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=StreamingLLMProvider(["Hello", " there."]),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    response = await service.get_llm_response(session, "Hi")

    assert response == "Hello there."
    service.latency_tracker.mark_llm_first_token.assert_called_once_with(session.call_id)


@pytest.mark.asyncio
async def test_synthesize_and_send_audio_skips_latency_tracking_for_greeting():
    media_gateway = AsyncMock()
    tts_provider = StreamingTTSProvider([b"\x00" * 160])

    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=tts_provider,
        media_gateway=media_gateway,
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    service._barge_in_events[session.call_id] = session.barge_in_event

    await service.synthesize_and_send_audio(
        session,
        "Hello there.",
        track_latency=False,
    )

    media_gateway.send_audio.assert_awaited_once_with(session.call_id, b"\x00" * 160)
    media_gateway.flush_tts_buffer.assert_awaited_once_with(session.call_id)
    service.latency_tracker.mark_tts_first_chunk.assert_not_called()
    service.latency_tracker.mark_response_start.assert_not_called()
    service.latency_tracker.mark_audio_start.assert_not_called()
    service.latency_tracker.mark_tts_end.assert_not_called()
    service.latency_tracker.mark_completed.assert_not_called()


@pytest.mark.asyncio
async def test_synthesize_and_send_audio_marks_user_turn_latency_hooks():
    media_gateway = AsyncMock()
    tts_provider = StreamingTTSProvider([b"\x00" * 160])

    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=tts_provider,
        media_gateway=media_gateway,
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    service._barge_in_events[session.call_id] = session.barge_in_event

    await service.synthesize_and_send_audio(
        session,
        "Hello there.",
        track_latency=True,
    )

    service.latency_tracker.mark_tts_first_chunk.assert_called_once_with(session.call_id)
    service.latency_tracker.mark_response_start.assert_called_once_with(session.call_id)
    service.latency_tracker.mark_audio_start.assert_called_once_with(session.call_id)
    service.latency_tracker.mark_tts_end.assert_called_once_with(session.call_id)
    service.latency_tracker.mark_completed.assert_called_once_with(session.call_id)
