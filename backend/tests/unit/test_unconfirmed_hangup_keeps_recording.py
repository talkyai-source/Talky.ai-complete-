"""An unconfirmed PBX hangup must not throw away the call recording.

Bug hunt 2026-10-10 (telephony-infra-media-3 follow-up): hangup_call now
returns False when ARI cannot prove the channel gone (it used to claim True).
End-of-call teardown treated False as "no PBX hangup" and popped the gateway
session locally, recording buffer included. The channel may still be live, and
its later ChannelDestroyed teardown is what saves the recording, so the
recording was lost. The session must instead stop the agent but stay
registered for that save.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services.voice_pipeline_service import VoicePipelineService
from tests.unit.test_voice_pipeline_service import _StreamingLLMProvider, _make_session


def _service(media_gateway):
    tts = MagicMock()

    async def _audio(*_a, **_k):
        yield MagicMock(data=b"\x00" * 320)

    tts.stream_synthesize = MagicMock(side_effect=_audio)
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=_StreamingLLMProvider(
            ['{"action":"end_session","reason":"user_goodbye","farewell":"Goodbye."}']),
        tts_provider=tts, media_gateway=media_gateway, mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None
    return service


@pytest.mark.asyncio
async def test_an_unconfirmed_hangup_keeps_the_session_for_the_recording_save():
    gateway = AsyncMock()
    gateway.hangup_call = AsyncMock(return_value=False)
    gateway.is_session_active = MagicMock(return_value=True)
    gateway.deactivate_session = MagicMock()
    session = _make_session()
    session.current_user_input = "bye"

    await _service(gateway).handle_turn_end(session, None)

    gateway.hangup_call.assert_awaited_once_with(session.call_id, "user_goodbye")
    gateway.on_call_ended.assert_not_awaited()
    gateway.deactivate_session.assert_called_once_with(session.call_id)


@pytest.mark.asyncio
async def test_a_session_already_gone_is_still_torn_down_locally():
    gateway = AsyncMock()
    gateway.hangup_call = AsyncMock(return_value=False)
    gateway.is_session_active = MagicMock(return_value=False)
    gateway.deactivate_session = MagicMock()
    session = _make_session()
    session.current_user_input = "bye"

    await _service(gateway).handle_turn_end(session, None)

    gateway.on_call_ended.assert_awaited_once_with(session.call_id, "user_goodbye")
    gateway.deactivate_session.assert_not_called()
