"""A refused end_call must not be followed by the "could you say it again?" line.

Production 20 Nov, call a7284aac: the agent reached a voicemail greeting not yet
recognised as a mailbox. The model's only output was end_call, which was refused
(nobody said goodbye); the no-reply safety net then spoke "Sorry, I didn't quite
catch that" into the mailbox. This changes what is said, never when a call ends.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.action_tools import VOICE_ACTION_NAMES
from app.domain.services.voice_pipeline_service import VoicePipelineService

RECOVERY = "could you say it again"


class _ToolLLM:
    supports_tools = True

    def __init__(self, text, call_end_call):
        self._text = text
        self._call_end_call = call_end_call

    async def stream_chat_with_tools(self, messages, **kwargs):
        if self._call_end_call:
            await kwargs["tool_runner"]("end_call", {"reason": "voicemail greeting"})
        if self._text:
            yield self._text


async def _run(text, call_end_call):
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=_ToolLLM(text, call_end_call),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.tts_provider._model_id = "deepgram-aura-2"
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = CallSession(
        call_id="call-refused-end-1",
        campaign_id="campaign-1",
        lead_id="lead-1",
        tenant_id="tenant-1",
        provider_call_id="provider-1",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-1",
    )
    session.barge_in_event = asyncio.Event()
    session._voice_action_context_loaded = True
    session._voice_action_capabilities = {n: "synthetic test executor" for n in VOICE_ACTION_NAMES}
    service._barge_in_events[session.call_id] = session.barge_in_event
    await service._stream_llm_and_tts(session)
    spoken = [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]
    return spoken, session


@pytest.mark.asyncio
async def test_only_a_refused_end_call_speaks_nothing_and_adds_no_history(monkeypatch, caplog):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    with caplog.at_level(logging.WARNING):
        spoken, session = await _run(None, True)
    assert spoken == []
    assert session._spoken_sentences == []
    assert "recovery_line_skipped call=call-refused-end-1 reason=refused_end_call" in caplog.text


@pytest.mark.asyncio
async def test_a_turn_with_no_output_at_all_still_gets_the_recovery_line(monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    spoken, _ = await _run(None, False)
    assert len(spoken) == 1 and RECOVERY in spoken[0]


@pytest.mark.asyncio
async def test_speech_with_a_refused_end_call_is_spoken_without_the_recovery_line(monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    spoken, _ = await _run("Thanks, one more question for you.", True)
    assert spoken == ["Thanks, one more question for you."]


# ---- review fixes (B1, B2) -------------------------------------------------

class _ReasonLLM:
    supports_tools = True

    def __init__(self, reason):
        self._reason = reason

    async def stream_chat_with_tools(self, messages, **kwargs):
        await kwargs["tool_runner"]("end_call", {"reason": self._reason})
        if False:  # pragma: no cover - an async generator with no speech
            yield ""


async def _run_reason(reason):
    service = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=_ReasonLLM(reason),
        tts_provider=AsyncMock(), media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None
    service.transcript_service = MagicMock()
    service.tts_provider._model_id = "deepgram-aura-2"
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = CallSession(
        call_id="call-refused-end-2", campaign_id="campaign-1", lead_id="lead-1",
        tenant_id="tenant-1", provider_call_id="provider-1",
        system_prompt="Use plain spoken text only.", voice_id="voice-1",
    )
    session.barge_in_event = asyncio.Event()
    session._voice_action_context_loaded = True
    session._voice_action_capabilities = {n: "synthetic test executor" for n in VOICE_ACTION_NAMES}
    service._barge_in_events[session.call_id] = session.barge_in_event
    await service._run_turn(session, "Hello, Sarah speaking.", AsyncMock(), turn_id=1)
    spoken = [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]
    return spoken, session


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [
    "voicemail greeting", "Answering machine detected", "call screening in progress",
    "reached a mailbox",
])
async def test_b2_refused_end_call_with_a_machine_reason_is_silent(monkeypatch, reason):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    spoken, _ = await _run_reason(reason)
    assert spoken == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["caller ended the conversation", "", "Hello, Sarah speaking"])
async def test_b2_refused_end_call_with_any_other_reason_speaks_the_recovery_line(monkeypatch, reason):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    spoken, _ = await _run_reason(reason)
    assert len(spoken) == 1 and RECOVERY in spoken[0]


@pytest.mark.asyncio
async def test_b1_a_silent_refused_end_call_keeps_the_callers_message_in_history(monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    spoken, session = await _run_reason("voicemail greeting")
    assert spoken == []
    assert [(m.role.value, m.content) for m in session.conversation_history] == [
        ("user", "Hello, Sarah speaking.")
    ]


@pytest.mark.asyncio
async def test_b1_an_ordinary_empty_turn_still_rolls_the_message_back(monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    service = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=AsyncMock(),
        tts_provider=AsyncMock(), media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.transcript_service = MagicMock()
    session = CallSession(
        call_id="call-empty-1", campaign_id="campaign-1", lead_id="lead-1",
        provider_call_id="provider-1", system_prompt="x", voice_id="voice-1",
    )
    session.barge_in_event = asyncio.Event()
    session._recovery_skipped_for_refused_end_call = True  # stale flag must not matter on a new turn

    async def empty(session_, websocket=None):
        session_._recovery_skipped_for_refused_end_call = False
        return ("", 0.0, 0.0)

    service._stream_llm_and_tts = empty
    await service._run_turn(session, "Hello?", AsyncMock(), turn_id=1)
    assert session.conversation_history == []
