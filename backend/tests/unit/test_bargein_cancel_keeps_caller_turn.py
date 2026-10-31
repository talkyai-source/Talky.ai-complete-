"""A barge-in that cancels a turn before ANY sentence was spoken must not
erase the caller's own words from history.

THE DEFECT (production, 2026-09-23)
------------------------------------
turn_runner.run()'s `except asyncio.CancelledError` handler rolled the
conversation history all the way back to `history_snapshot` — the length
BEFORE the caller's own USER message was appended at the top of run() —
whenever nothing had been spoken yet (`session._spoken_sentences` empty).
Each Flux EndOfTurn dispatches only its own segment (turn_ender.py builds
`full_transcript` fresh per turn), and nothing downstream ever re-queues a
rolled-back fragment into a later turn, so the caller's words were gone for
the rest of the call:

    51450718: the caller said "I want full body checkup." — the reply was
    cancelled before it spoke a word, the turn rolled back, and the agent's
    NEXT turn never engaged with it at all.
    b97ce4c5: 8 replies cancelled this way in one call; the agent later said
    "I'm sorry I missed that" to a caller who had explained their problem
    twice.

THE FIX
-------
When nothing was spoken, keep the user message (history_snapshot is its
index) and drop only what the cancelled task itself appended after it —
mirroring the already-correct "something was spoken" branch, which keeps the
user message and appends only the spoken partial.

Drives the REAL VoicePipelineService._run_turn (turn_runner.TurnRunner.run),
cancelling the actual asyncio task mid-flight — the same mechanism
interrupt.py's `_cancel_turn_task` uses in production — rather than
simulating cancellation by mocking the CancelledError branch itself.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService


def _make_service() -> VoicePipelineService:
    svc = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=AsyncMock(),
        tts_provider=AsyncMock(), media_gateway=AsyncMock(),
        mute_during_tts=False,
    )
    svc.latency_tracker = MagicMock()
    svc.latency_tracker.get_metrics.return_value = None
    svc.transcript_service = MagicMock()
    return svc


def _make_session() -> CallSession:
    s = CallSession(
        call_id="call-1", campaign_id="c", lead_id="l", provider_call_id="p",
        system_prompt="Use plain spoken text.", voice_id="v",
    )
    s.barge_in_event = asyncio.Event()
    return s


@pytest.mark.asyncio
async def test_cancel_before_any_sentence_keeps_the_user_message():
    """51450718: cancelled before a single word was spoken -- the caller's
    utterance must survive so the next turn can still see it."""
    svc = _make_service()
    session = _make_session()

    async def never_finishes(session_, websocket=None):
        await asyncio.sleep(10)
        return ("unreachable", 0.0, 0.0)  # pragma: no cover

    svc._stream_llm_and_tts = never_finishes

    task = asyncio.ensure_future(
        svc._run_turn(session, "I want full body checkup", AsyncMock(), turn_id=1)
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [m.role for m in session.conversation_history] == [MessageRole.USER], (
        "the caller's own utterance must survive a cancel-before-any-audio barge-in"
    )
    assert session.conversation_history[0].content == "I want full body checkup"


@pytest.mark.asyncio
async def test_cancel_after_a_spoken_sentence_is_unchanged():
    """Regression guard: the already-correct 'something was spoken' branch
    (user + spoken partial + '[interrupted by caller]') must not change."""
    svc = _make_service()
    session = _make_session()
    svc._barge_in_events[session.call_id] = session.barge_in_event

    class _StreamingLLM:
        async def stream_chat_with_timeout(self, *a, **k):
            for c in ["Hello there. ", "How are you doing today. "]:
                yield c

        async def stream_chat_with_tools(self, *a, **k):
            for c in ["Hello there. ", "How are you doing today. "]:
                yield c

    svc.llm_provider = _StreamingLLM()

    calls = {"n": 0}

    async def synth(session_, text, websocket=None, track_latency=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return False  # sentence 1 delivered
        session_.barge_in_event.set()  # caller barges in before sentence 2
        return True

    svc.synthesize_and_send_audio = AsyncMock(side_effect=synth)

    await svc._run_turn(session, "Hi", AsyncMock(), turn_id=1)

    roles = [m.role for m in session.conversation_history]
    assert roles == [MessageRole.USER, MessageRole.ASSISTANT]
    assert session.conversation_history[0].content == "Hi"
    assert session.conversation_history[1].content == "Hello there. [interrupted by caller]"
