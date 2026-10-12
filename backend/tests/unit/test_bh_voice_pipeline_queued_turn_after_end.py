"""voice-pipeline-1 / voice-pipeline-4: a queued next turn must not be
dispatched on a dead call, and must not be stranded by an early return.

The F-08 queue (session._queued_next_turn) is dispatched from turn_ender's
finally. After the agent hangs up (state ENDING/ENDED, end action handled) or
the media-gateway session is gone, dispatching it runs a second LLM+TTS turn
on a finished call.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline_service import VoicePipelineService


def _svc():
    svc = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=MagicMock(),
        mute_during_tts=False,
    )
    svc.latency_tracker = MagicMock()
    svc.latency_tracker.get_metrics.return_value = None
    svc.transcript_service = MagicMock()
    return svc


def _session():
    s = CallSession(
        call_id="call-bh1", campaign_id="c", lead_id="l", provider_call_id="p",
        system_prompt="Use plain spoken text.", voice_id="v",
    )
    s.barge_in_event = asyncio.Event()
    s.conversation_history.append(Message(role=MessageRole.USER, content="earlier"))
    s.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="reply"))
    return s


def _queue(session):
    session._queued_next_turn = {
        "text": "thanks", "latest_text": "thanks", "seq": 2,
        "caller_turn_order": None, "prior_caller_turn_orders": (),
    }


async def _run_first_turn(svc, session, mutate):
    """Run one turn whose _run_turn applies `mutate` (the hangup) while a
    distinct utterance is queued. Returns the calls to the next dispatch."""
    dispatched = []
    real = svc.handle_turn_end

    async def spy(sess, ws=None, source="final", **kw):
        if source == "queued":
            dispatched.append(kw.get("user_text"))
            return
        return await real(sess, ws, source=source, **kw)

    async def run_turn(sess, transcript, ws, turn_id):
        _queue(sess)
        mutate(sess)
        return ("Goodbye.", 1.0, 1.0)

    svc._run_turn = AsyncMock(side_effect=run_turn)
    svc.handle_turn_end = spy
    task = asyncio.create_task(svc.handle_turn_end(session, AsyncMock(), user_text="what are your hours"))
    svc._pending_llm_tasks[session.call_id] = task
    await task
    await asyncio.sleep(0)
    return dispatched


def test_queued_turn_not_dispatched_after_agent_end_call():
    svc, session = _svc(), _session()

    def hangup(sess):
        sess.state = CallState.ENDING  # what _shutdown_session_for_end_action sets

    dispatched = asyncio.run(_run_first_turn(svc, session, hangup))
    assert dispatched == []
    assert session._queued_next_turn is None
    assert session.state == CallState.ENDING


def test_queued_turn_not_dispatched_after_end_session_action():
    svc, session = _svc(), _session()
    dispatched = asyncio.run(_run_first_turn(
        svc, session, lambda s: setattr(s, "_end_session_action_handled", True)))
    assert dispatched == []
    assert session._queued_next_turn is None


def test_queued_turn_not_dispatched_when_gateway_session_is_gone():
    svc, session = _svc(), _session()
    svc.media_gateway.is_session_active = MagicMock(return_value=False)
    dispatched = asyncio.run(_run_first_turn(svc, session, lambda s: None))
    assert dispatched == []
    assert session._queued_next_turn is None


def test_queued_turn_still_dispatched_on_a_live_call():
    """F-08 must keep working: live gateway session, call not ending."""
    svc, session = _svc(), _session()
    svc.media_gateway.is_session_active = MagicMock(return_value=True)
    dispatched = asyncio.run(_run_first_turn(svc, session, lambda s: None))
    assert dispatched == ["thanks"]
    assert session._queued_next_turn is None


def test_queued_turn_not_stranded_by_turn_0_reprompt_early_return():
    """voice-pipeline-4: the turn-0 'say that again?' path returns before the
    try/finally that dispatches the queue. The caller's repeat must not sit in
    _queued_next_turn until some later turn replays it out of order."""
    svc = _svc()
    session = _session()
    session.conversation_history.clear()  # turn 0: no prior caller turn
    svc.synthesize_and_send_audio = AsyncMock(return_value=False)
    dispatched = []
    real = svc.handle_turn_end

    async def spy(sess, ws=None, source="final", **kw):
        if source == "queued":
            dispatched.append(kw.get("user_text"))
            return
        return await real(sess, ws, source=source, **kw)

    svc.handle_turn_end = spy

    async def scenario():
        async def first():
            _queue(session)  # the caller repeated themselves during the reprompt
            await svc.handle_turn_end(
                session, AsyncMock(), source="final", user_text="uh", confidence=0.05,
            )

        task = asyncio.create_task(first())
        svc._pending_llm_tasks[session.call_id] = task
        await task
        await asyncio.sleep(0)

    asyncio.run(scenario())
    svc.synthesize_and_send_audio.assert_awaited()  # the reprompt path did run
    assert dispatched == ["thanks"]
    assert session._queued_next_turn is None


def test_cancelled_turn_does_not_dispatch_from_the_early_return_wrapper():
    """A turn cancelled during the reprompt (barge-in) leaves the queue to the
    barge-in path; the wrapper must not fire on CancelledError."""
    svc = _svc()
    session = _session()
    session.conversation_history.clear()
    started = asyncio.Event()

    async def slow_tts(*a, **k):
        started.set()
        await asyncio.sleep(10)

    svc.synthesize_and_send_audio = slow_tts
    dispatched = []
    real = svc.handle_turn_end

    async def spy(sess, ws=None, source="final", **kw):
        if source == "queued":
            dispatched.append(kw.get("user_text"))
            return
        return await real(sess, ws, source=source, **kw)

    svc.handle_turn_end = spy

    async def scenario():
        _queue(session)
        task = asyncio.create_task(
            svc.handle_turn_end(session, AsyncMock(), user_text="uh", confidence=0.05)
        )
        svc._pending_llm_tasks[session.call_id] = task
        await started.wait()
        svc._pending_llm_tasks.pop(session.call_id, None)  # barge-in pops, then cancels
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert dispatched == []


def test_queued_turn_that_starts_after_the_call_ended_does_not_run():
    """The dispatched task starts later than the finally that created it; a
    call that ended in between must not get an LLM+TTS round."""
    svc, session = _svc(), _session()
    svc._run_turn = AsyncMock(return_value=("never", 1.0, 1.0))
    session.state = CallState.ENDED

    asyncio.run(svc.handle_turn_end(
        session, AsyncMock(), source="queued", user_text="thanks"))

    svc._run_turn.assert_not_called()
    assert session.state == CallState.ENDED
