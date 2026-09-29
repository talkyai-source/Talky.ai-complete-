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
import os
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services.voice_pipeline.audio_ingest import AudioIngest
from tests.unit.test_audio_ingest_caller_first_silence import (
    _instant_yield,
    _make_pipeline,
    _make_session,
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


async def _spoken_after(stt) -> list:
    session = _make_session("user")
    pipeline = _make_pipeline()
    pipeline.handle_transcript = AsyncMock()
    pipeline.stt_provider = stt
    ingest = AudioIngest(pipeline)
    with (
        patch.dict(
            os.environ,
            {
                "VOICE_OPENING_HELLO_S": "0.03",
                "VOICE_MID_NUDGE_S": "0.03",
                "VOICE_SILENCE_HANGUP_S": "30",
                "VOICE_NUDGE_MIN_GAP_S": "0.03",
            },
        ),
        patch("asyncio.sleep", new=_instant_yield),
    ):
        task = asyncio.ensure_future(ingest.process(session))
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=1.5)
        except asyncio.TimeoutError:
            pass
        finally:
            session.stt_active = False
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    return [c.args[1] for c in pipeline.synthesize_and_send_audio.await_args_list]


@pytest.mark.asyncio
async def test_words_still_arriving_hold_the_opening_nudge():
    spoken = await _spoken_after(_WordsNoStartOfTurnSTT())
    assert not spoken, f"nudged over a caller who was mid-turn: {spoken!r}"


@pytest.mark.asyncio
async def test_once_the_turn_ends_the_opening_ladder_still_runs():
    spoken = await _spoken_after(_WordsThenEndOfTurnSTT())
    assert spoken, "EndOfTurn closed the turn; the ladder must still be able to nudge"
