"""The opening "Hello?" must not be said over a caller whose words are still
arriving, even when their StartOfTurn was never recorded.

Test call 5dfa4416 (2026-09-29): the caller's "Hello" came through as words at
22:30:39-41, the STT provider kept the turn open until 22:30:46, but no
turn-open stamp existed -- so at 22:30:42, after one quiet second, the opening
nudge said "Hello?" on top of them ("Oh, hi." / "Hello" / "Hello?" / "Hello?").

Drives the REAL AudioIngest.process / silence monitor, with the harness from
test_regreet_ladder_over_open_caller_turn.py.
"""
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
    def __init__(self, text: str = "", is_final: bool = False) -> None:
        self.text = text
        self.is_final = is_final


class _WordsNoStartOfTurnSTT:
    """Interim words arrive; no StartOfTurn callback, no EndOfTurn yet."""

    @staticmethod
    def detect_turn_end(chunk) -> bool:
        return bool(chunk.is_final) and not chunk.text

    async def stream_transcribe(self, audio_stream, call_id=None, on_barge_in=None, **kwargs):
        yield _Chunk(text="Hello", is_final=False)
        await asyncio.Event().wait()
        yield  # pragma: no cover


class _WordsThenEndOfTurnSTT:
    """The same words, then the provider ends the turn and all is quiet."""

    @staticmethod
    def detect_turn_end(chunk) -> bool:
        return bool(chunk.is_final) and not chunk.text

    async def stream_transcribe(self, audio_stream, call_id=None, on_barge_in=None, **kwargs):
        yield _Chunk(text="Hello", is_final=False)
        yield _Chunk(text="", is_final=True)  # EndOfTurn marker
        await asyncio.Event().wait()
        yield  # pragma: no cover


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,should_close", [(_WordsNoStartOfTurnSTT, False), (_WordsThenEndOfTurnSTT, True)])
async def test_stt_words_without_start_event_own_the_silence_deadline(provider, should_close):
    session, pipeline = _make_session("user"), _make_pipeline()
    pipeline.handle_transcript = AsyncMock()
    pipeline.stt_provider = provider()
    await _run_until_silence_tick(session, pipeline, hangup_s=0.03)
    assert pipeline._shutdown_session_for_end_action.await_count == int(should_close)
    pipeline.synthesize_and_send_audio.assert_not_awaited()
