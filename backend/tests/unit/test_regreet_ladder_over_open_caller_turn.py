"""The opening re-greet ladder must not talk over a still-open caller turn.

THE DEFECT (production, 2026-09-23)
------------------------------------
Calls 7dbf415f and 4a9dd845: the caller started speaking right after the
agent's opening "Hello?". The silence monitor's ONLY "caller mid-utterance"
signal was `_barge_in_events[call_id]`, which is armed ONLY when a Flux
StartOfTurn arrives while `session.tts_active` is True (a genuine barge-in).
A StartOfTurn arriving right after the agent has already FINISHED speaking —
exactly what happens right after a bare opening greeting — never arms it, so
the monitor fell back to a once-a-second RMS bucket alone. A single quiet
inter-word gap read as silence and released the next re-greet rung on top of
a still-open utterance:

    18:18:20.953  Flux StartOfTurn ("Who's this?" begins) -- agent already
                  finished its own "Hello?", so tts_active is False
    18:18:21.379  RMS bucket reads 411 (< 500 threshold)
    18:18:21.867  [SilenceMonitor] nudging: 'Hello??'  <- talks over the caller
    18:18:26.755  EndOfTurn (the utterance finally finalizes)

THE FIX
-------
`session._caller_turn_open_since` is now stamped on EVERY StartOfTurn
(audio_ingest.py's `_on_barge_in_direct`, unconditional on tts_active) and
cleared on EndOfTurn (the STT consumer loop, via the provider-agnostic
`detect_turn_end`). While it is fresh (< VOICE_CALLER_TURN_OPEN_MAX_AGE_S,
default 12s) the silence monitor must not nudge and must not count that time
as silence. Past the safety max age (a lost EndOfTurn, e.g. across a
reconnect) nudges must resume.

Drives the REAL `AudioIngest.process` / `_silence_monitor` closure, reusing
the harness in test_audio_ingest_caller_first_silence.py.
"""
from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services.voice_pipeline.audio_ingest import AudioIngest
from tests.unit.test_audio_ingest_caller_first_silence import (
    _instant_yield,
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
async def test_open_caller_turn_suppresses_the_opening_nudge():
    """7dbf415f: StartOfTurn with the agent already done speaking must still
    hold off the re-greet ladder for as long as the turn stays open.

    Uses `_instant_yield` rather than `_run_until_silence_tick`'s plain
    AsyncMock sleep: an AsyncMock-returned coroutine never actually suspends,
    so the monitor's `while` loop busy-spins and burns real (monotonic) wall
    time far past any freshness window before the test's own 1.5s timeout can
    even fire (see test_opening_ladder_is_bounded_and_never_nags_past_its_cap
    for the same pitfall) — which would make this test's own 12s safety cap
    the thing that (wrongly) lets the nudge through, not a real defect.
    `_instant_yield` gives the loop a genuine scheduling point per tick, so
    real elapsed time tracks the 1.5s test window instead.
    """
    session = _make_session("user")
    pipeline = _make_pipeline()
    pipeline.handle_transcript = AsyncMock()
    pipeline.stt_provider = _OpenTurnNeverEndsSTT()

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

    spoken = [c.args[1] for c in pipeline.synthesize_and_send_audio.await_args_list]
    assert not spoken, (
        "nudged over a caller turn that was still open — "
        f"spoke {spoken!r}"
    )


@pytest.mark.asyncio
async def test_nudging_resumes_normally_once_the_turn_actually_ends():
    """Once Flux's own EndOfTurn lands, the ladder must behave exactly as
    before — this is not a permanent suppression."""
    session = _make_session("user")
    pipeline = _make_pipeline()
    pipeline.handle_transcript = AsyncMock()
    pipeline.stt_provider = _StartThenEndOfTurnSTT()

    await _run_until_silence_tick(session, pipeline)

    spoken = [c.args[1] for c in pipeline.synthesize_and_send_audio.await_args_list]
    assert "Hello?" in spoken, (
        "EndOfTurn closed the window, so the caller has gone genuinely quiet "
        "again -- the opening ladder must still nudge"
    )


@pytest.mark.asyncio
async def test_a_stale_open_stamp_does_not_silence_nudges_forever():
    """A lost EndOfTurn (e.g. across an STT reconnect) must not hold the
    'caller turn open' state past its safety max age."""
    session = _make_session("user")
    # Older than VOICE_CALLER_TURN_OPEN_MAX_AGE_S's default (12.0s) -- the
    # provider never told us this turn ended, and never will in this test.
    session._caller_turn_open_since = time.monotonic() - 20.0
    pipeline = _make_pipeline()  # default _ParkedSTT: no further StartOfTurn/EndOfTurn

    await _run_until_silence_tick(session, pipeline)

    spoken = [c.args[1] for c in pipeline.synthesize_and_send_audio.await_args_list]
    assert "Hello?" in spoken, (
        "a stale caller_turn_open stamp must not suppress nudges forever"
    )
