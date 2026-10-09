"""Actual accepted-final scheduling must own chronology, not media callbacks."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole, TranscriptChunk
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService


def pipeline():
    service = VoicePipelineService(
        stt_provider=MagicMock(), llm_provider=AsyncMock(),
        tts_provider=AsyncMock(), media_gateway=AsyncMock(), mute_during_tts=False,
    )
    service.stt_provider.detect_turn_end.side_effect = lambda t: t.is_final and not t.text
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None
    service.transcript_service = MagicMock()
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = CallSession(call_id="dispatch-test", campaign_id="c", lead_id="l",
                          provider_call_id="p", system_prompt="Speak plainly.", voice_id="v")
    session.turn_id = 2
    session.barge_in_event = asyncio.Event()
    session.conversation_history = [Message(role=MessageRole.USER, content="Hello there."),
                                    Message(role=MessageRole.ASSISTANT, content="How can I help?")]
    service._barge_in_events[session.call_id] = session.barge_in_event
    return service, session


async def final(service, session, text):
    await service.handle_transcript(session, TranscriptChunk(text=text, is_final=True))
    await service.handle_transcript(session, TranscriptChunk(text="", is_final=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("coalesce", [False, True])
async def test_real_queue_keeps_acceptance_order_with_reused_media_seq_and_duplicates(coalesce):
    service, session = pipeline()
    release = asyncio.Event()
    entered = asyncio.Event()
    observed = []

    async def run(s, text, *_args, **_kwargs):
        task = asyncio.current_task()
        observed.append((text, getattr(task, "_caller_turn_order", None)))
        if len(observed) == 1:
            entered.set()
            await release.wait()
        return "Thank you for explaining.", 1.0, 1.0

    service._run_turn = run
    first = second = None
    try:
        await final(service, session, "I am not your customer.")
        first = service._pending_llm_tasks[session.call_id]
        await asyncio.wait_for(entered.wait(), 2)
        # Identical pending final is a duplicate, without a new caller order.
        await final(service, session, "I am not your customer.")
        assert getattr(session, "_accepted_caller_turn_order", None) == 1
        assert getattr(session, "_queued_next_turn", None) is None
        await final(service, session, "What are the opening hours?")
        queued = session._queued_next_turn
        assert queued.get("caller_turn_order") == 2
        await final(service, session, "What are the opening hours?")
        assert session._queued_next_turn["caller_turn_order"] == 2
        assert session._accepted_caller_turn_order == 2
        if coalesce:
            await final(service, session, "Actually, I am your customer.")
            assert session._queued_next_turn["caller_turn_order"] == 3
        release.set()
        await asyncio.wait_for(first, 2)
        second = service._pending_llm_tasks.get(session.call_id)
        if second is not None:
            await asyncio.wait_for(second, 2)
        assert len(observed) == 2
        assert [row[1] for row in observed] == [1, 3 if coalesce else 2]
        # A third utterance joins the queued one, newest last. It used to
        # replace it, and "What are the opening hours?" never reached the model
        # (2026-10-09, test_waiting_words_are_answered_together).
        assert observed[-1][0] == ("What are the opening hours? Actually, I am your customer." if coalesce
                                   else "What are the opening hours?")
        assert service._utterance_seq.get(session.call_id, 0) == 0
        assert session._queued_next_turn is None
    finally:
        release.set()
        for task in (first, second, service._pending_llm_tasks.get(session.call_id)):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_actual_detached_old_dispatch_cannot_reverse_a_newer_assertion():
    service, session = pipeline()
    release = asyncio.Event()
    entered = asyncio.Event()
    states = []

    async def turn(s, _websocket=None, *, user_text, **_kwargs):
        if user_text == "I am not your customer.":
            entered.set()
            await release.wait()
        states.append((user_text, asyncio.current_task()._caller_turn_order))

    service.handle_turn_end = turn
    await final(service, session, "I am not your customer.")
    old = service._pending_llm_tasks[session.call_id]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        # A cooperatively cancelled old task can still unwind after its slot
        # was released. Its scheduler-owned stamp must remain the older one.
        service._pending_llm_tasks.pop(session.call_id)
        await final(service, session, "Actually, I am your customer.")
        new = service._pending_llm_tasks[session.call_id]
        await new
        release.set()
        await old
        assert states == [("Actually, I am your customer.", 2), ("I am not your customer.", 1)]
        assert old._caller_turn_order == 1
        assert new._caller_turn_order == 2
    finally:
        release.set()
        if not old.done():
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)


@pytest.mark.asyncio
async def test_false_barge_resume_keeps_original_accepted_stamp(monkeypatch):
    service, session = pipeline()
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "0")
    session._accepted_caller_turn_order = 4
    service._utterance_seq[session.call_id] = 99
    service.handle_turn_end = AsyncMock()
    await service._resume_after_false_barge_in(
        session, None, "What are your opening hours?", time.monotonic(), caller_turn_order=4,
    )
    task = service._pending_llm_tasks[session.call_id]
    await task
    assert task._caller_turn_order == 4
    assert session._accepted_caller_turn_order == 4


@pytest.mark.asyncio
async def test_false_barge_resume_does_not_reissue_older_accepted_turn(monkeypatch):
    service, session = pipeline()
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "0")
    session._accepted_caller_turn_order = 5
    service.handle_turn_end = AsyncMock()
    await service._resume_after_false_barge_in(
        session, None, "What are your opening hours?", time.monotonic(), caller_turn_order=4,
    )
    service.handle_turn_end.assert_not_called()


@pytest.mark.asyncio
async def test_actual_barge_cancel_carries_accepted_order_into_replay(monkeypatch):
    service, session = pipeline()
    session._accepted_caller_turn_order = 1
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "0")
    entered = asyncio.Event()
    resumed = asyncio.Event()
    observations = []

    async def turn(*_args, **_kwargs):
        task = asyncio.current_task()
        observations.append(getattr(task, "_caller_turn_order", None))
        if len(observations) == 1:
            entered.set()
            await asyncio.Event().wait()
        resumed.set()

    service.handle_turn_end = turn
    await final(service, session, "What are your opening hours?")
    old = service._pending_llm_tasks[session.call_id]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        session.tts_active = True
        await service.handle_barge_in(session)
        await asyncio.wait_for(resumed.wait(), 2)
        assert observations == [2, 2]
        assert session._accepted_caller_turn_order == 2
    finally:
        for task in (old, service._pending_llm_tasks.get(session.call_id)):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
