"""Extra cases for the restored self-dialogue guard (turn_streamer.py).

The model must never speak the caller's side. A terminator with no space after
it marks where it stopped writing its own turn; a normally spaced second
sentence in the same turn is legitimate and must be spoken in full.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService


class _Stream:
    def __init__(self, chunks):
        self._chunks = chunks
        self.consumed = 0

    async def stream_chat_with_timeout(self, *args, **kwargs):
        for chunk in self._chunks:
            self.consumed += 1
            yield chunk


async def _run(chunks, monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    stream = _Stream(chunks)
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=stream,
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.tts_provider._model_id = "deepgram-aura-2"
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = CallSession(
        call_id="call-guard-restored-1",
        campaign_id="campaign-1",
        lead_id="lead-1",
        provider_call_id="provider-1",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-1",
    )
    session.barge_in_event = asyncio.Event()
    service._barge_in_events[session.call_id] = session.barge_in_event
    result = await service._stream_llm_and_tts(session)
    spoken = [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]
    return spoken, session, stream, result


@pytest.mark.asyncio
async def test_question_then_the_agents_own_next_sentence_is_spoken_in_full(monkeypatch):
    spoken, _, _, _ = await _run(["Is 3 pm good?", " I can also do 4."], monkeypatch)
    assert " ".join(spoken) == "Is 3 pm good? I can also do 4."


@pytest.mark.asyncio
async def test_a_turn_without_a_question_is_untouched(monkeypatch):
    spoken, _, _, _ = await _run(["Thanks for calling.", " Have a good day."], monkeypatch)
    assert " ".join(spoken) == "Thanks for calling. Have a good day."


@pytest.mark.asyncio
async def test_cut_text_is_absent_from_history_and_the_stream_stops(monkeypatch):
    spoken, session, stream, result = await _run(
        ["Is that right?", "Yes, that's right.", " What next?", " More."],
        monkeypatch,
    )
    assert spoken == ["Is that right?"]
    assert session._spoken_sentences == ["Is that right?"]
    assert "Yes, that's right" not in str(result)
    assert stream.consumed == 2, "the provider stream kept being consumed after the cut"


@pytest.mark.asyncio
async def test_an_in_buffer_glued_reply_is_cut_without_spoken_fragment(monkeypatch):
    spoken, session, _, _ = await _run(["Is that right?Yes it is. Next question?"], monkeypatch)
    assert spoken == ["Is that right?"]
    assert session._spoken_sentences == ["Is that right?"]
