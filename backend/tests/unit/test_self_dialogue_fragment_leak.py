"""The in-buffer self-dialogue cut must stop the OUTER token stream too.

Production, call 3a17c06c (2026-09-23, build 76426ed6). The agent asked:

    "How many tenders do you typically submit each month?"

and the boundary landed with no space before the next character, inside one
streamed chunk (the same shape as production: the model's own "A" token glued
directly onto the question mark). The in-buffer `model_wrote_caller_turn`
guard (turn_streamer.py, commit 9e7f9c65) correctly detected the cut and
logged it -- but its `break` only exits the inner sentence-flush `while`
loop. The outer `async for token in _token_iter:` loop's own stop condition
never checked `model_wrote_caller_turn`, so it kept consuming the stream:
whatever the model sent next (" few.") arrived normally spaced, was treated
as a brand-new legitimate sentence, and was spoken -- reaching TTS and
`session._spoken_sentences` -- before the SECOND, cross-token guard
(92aac6aa) finally caught the next boundary and ended the turn. Talky-api.log
recorded the reconstructed line verbatim:

    "How many tenders do you typically submit each month? few."

This drives the REAL VoicePipelineService._stream_llm_and_tts, same harness
as test_caller_turn_across_token_edge.py, with the token split that
reproduces the gap: the boundary lands INSIDE one token (not between two, as
in the cross-token test which is a different, already-fixed guard).
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

    async def stream_chat_with_timeout(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


async def _spoken(chunks, monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=_Stream(chunks),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.tts_provider._model_id = "deepgram-aura-2"
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = CallSession(
        call_id="call-fragment-leak-1",
        campaign_id="campaign-1",
        lead_id="lead-1",
        provider_call_id="provider-1",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-1",
    )
    session.barge_in_event = asyncio.Event()
    service._barge_in_events[session.call_id] = session.barge_in_event
    await service._stream_llm_and_tts(session)
    return [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]


@pytest.mark.asyncio
async def test_the_in_buffer_cut_stops_the_outer_stream_too(monkeypatch):
    """3a17c06c: an in-buffer boundary must not let the next chunk leak out."""
    spoken = await _spoken(
        [
            # The '?' is immediately followed by 'A' in the SAME chunk -- the
            # in-buffer boundary the inner while-loop guard (9e7f9c65) catches.
            "How many tenders do you typically submit each month?A",
            " few.",
            " Is that right?",
        ],
        monkeypatch,
    )
    joined = " ".join(spoken)
    assert "How many tenders do you typically submit each month?" in joined
    assert "few." not in joined, spoken
    assert "Is that right" not in joined, spoken
    # Only the real question was ever sent to TTS -- the turn ended there.
    assert len(spoken) == 1, spoken
