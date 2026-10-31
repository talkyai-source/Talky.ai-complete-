"""Actual STT start/end events protect caller speech from the silence deadline."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_audio_ingest_caller_first_silence import (
    _make_pipeline,
    _make_session,
    _run_until_silence_tick,
)


class _Chunk:
    def __init__(self, text: str = "", is_final: bool = True) -> None:
        self.text = text
        self.is_final = is_final


class _OpenTurnNeverEndsSTT:
    """StartOfTurn fires once, then the utterance never finalizes — exactly
    the still-open 'Who's this?' on 7dbf415f."""

    @staticmethod
    def detect_turn_end(chunk) -> bool:
        return bool(chunk.is_final) and not chunk.text

    async def stream_transcribe(self, audio_stream, call_id=None, on_barge_in=None, **kwargs):
        if on_barge_in:
            on_barge_in("Who's this?")
        await asyncio.Event().wait()
        yield  # pragma: no cover - unreachable; keeps this an async generator


class _StartThenEndOfTurnSTT:
    """StartOfTurn fires once, immediately followed by its EndOfTurn marker,
    then the caller goes genuinely quiet for the rest of the call."""

    @staticmethod
    def detect_turn_end(chunk) -> bool:
        return bool(chunk.is_final) and not chunk.text

    async def stream_transcribe(self, audio_stream, call_id=None, on_barge_in=None, **kwargs):
        if on_barge_in:
            on_barge_in("Who's this?")
        yield _Chunk(text="", is_final=True)  # EndOfTurn marker
        await asyncio.Event().wait()
        yield  # pragma: no cover - unreachable


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,should_close", [(_OpenTurnNeverEndsSTT, False), (_StartThenEndOfTurnSTT, True)])
async def test_actual_stt_turn_ownership_controls_silence_deadline(provider, should_close):
    session, pipeline = _make_session("user"), _make_pipeline()
    pipeline.handle_transcript = AsyncMock()
    pipeline.stt_provider = provider()
    await _run_until_silence_tick(session, pipeline, hangup_s=0.03)
    assert pipeline._shutdown_session_for_end_action.await_count == int(should_close)
    pipeline.synthesize_and_send_audio.assert_not_awaited()
