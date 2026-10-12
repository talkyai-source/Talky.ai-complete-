"""bh2 voice-pipeline-1 (remaining half): a TEARDOWN cancel drops the queued turn.

test_bh_voice_pipeline_queued_turn_after_end.py covers a queued turn that is
dispatched after the agent hung up (state ENDING/ENDED, end action handled, or
the gateway session gone). What it could not cover: teardown cancels the active
turn through ``VoicePipelineService.cancel_active_turn`` BEFORE any other
teardown work, while the gateway session still exists and the state is still
PROCESSING. The cancelled task's ``finally`` then looked like any other turn
that released the slot and dispatched the queued next turn: a second LLM+TTS
round on a dead call ("tts_delivery_failed reason=session_gone", tools running
on a finished call).

Every caller of ``cancel_active_turn`` is teardown (lifecycle ``_on_call_ended``
and ``_on_transfer_connected``, ``VoiceOrchestrator.end_session``, the campaign
test WS teardown), and ``start_pipeline``'s orphaned-turn cancel at pipeline end
is the same thing by another route. A barge-in never goes through them: it
cancels with ``_cancel_turn_task`` and keeps its queue.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline_service import VoicePipelineService


def _svc():
    svc = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
        mute_during_tts=False,
    )
    # The gateway session still exists: this is the case the state/probe
    # checks of _call_is_over cannot catch.
    svc.media_gateway.is_session_active = MagicMock(return_value=True)
    svc.latency_tracker = MagicMock()
    svc.latency_tracker.get_metrics.return_value = None
    svc.transcript_service = MagicMock()
    return svc


def _session(call_id="call-bh1-teardown"):
    s = CallSession(
        call_id=call_id, campaign_id="c", lead_id="l", provider_call_id="p",
        system_prompt="Use plain spoken text.", voice_id="v",
    )
    s.barge_in_event = asyncio.Event()
    s.conversation_history.append(Message(role=MessageRole.USER, content="earlier"))
    s.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="reply"))
    return s


def _queue(session, text="thanks"):
    session._queued_next_turn = {
        "text": text, "latest_text": text, "seq": 2,
        "caller_turn_order": None, "prior_caller_turn_orders": (),
    }


async def _start_turn_in_tts(svc, session, dispatched):
    """Start a real turn that is parked in its reply (TTS streaming), register it
    as the call's pending turn task, and record any queued turn dispatched later."""
    in_tts = asyncio.Event()

    async def run_turn(sess, transcript, ws, turn_id):
        in_tts.set()
        await asyncio.sleep(30)  # the reply is still streaming

    svc._run_turn = AsyncMock(side_effect=run_turn)
    real = svc.handle_turn_end

    async def spy(sess, ws=None, source="final", **kw):
        if source == "queued":
            dispatched.append(kw.get("user_text"))
            return
        return await real(sess, ws, source=source, **kw)

    svc.handle_turn_end = spy
    svc._barge_in_events[session.call_id] = session.barge_in_event
    task = asyncio.create_task(
        svc.handle_turn_end(session, AsyncMock(), user_text="what are your hours")
    )
    task._turn_type = "final"
    svc._pending_llm_tasks[session.call_id] = task
    await in_tts.wait()
    return task


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_teardown_cancel_drops_the_queued_turn_while_the_gateway_session_is_alive():
    svc, session, dispatched = _svc(), _session(), []
    task = await _start_turn_in_tts(svc, session, dispatched)
    _queue(session)  # the caller spoke again while the reply was streaming

    await svc.cancel_active_turn(session.call_id)  # hangup teardown
    await _settle()

    assert task.cancelled()
    assert dispatched == [], "a queued turn ran on a torn-down call"
    assert session._queued_next_turn is None
    assert session.call_id not in svc._pending_llm_tasks  # no replacement turn registered


async def test_barge_in_cancel_still_dispatches_the_queued_turn():
    """The barge-in path is unchanged: it keeps its queue and answers it."""
    svc, session, dispatched = _svc(), _session(), []
    task = await _start_turn_in_tts(svc, session, dispatched)
    _queue(session)
    session.tts_active = True  # audible speech: a genuine interruption

    await svc.handle_barge_in(session, AsyncMock())
    # The barge-in cancel wait is bounded (0.25 s) by design; let the unwind finish.
    await asyncio.gather(task, return_exceptions=True)
    await _settle()

    assert task.cancelled()
    assert dispatched == ["thanks"]
    assert session._queued_next_turn is None


async def test_pipeline_shutdown_orphan_cancel_drops_the_queued_turn():
    """start_pipeline's finally cancels the turn still in flight when the STT loop
    ends (end_session cancels the pipeline task). Same teardown, other route."""
    svc, session, dispatched = _svc(), _session(), []
    task = await _start_turn_in_tts(svc, session, dispatched)
    _queue(session)

    async def stt_loop(*a, **k):
        await asyncio.sleep(30)

    svc.process_audio_stream = stt_loop
    svc.media_gateway = MagicMock()
    svc.media_gateway.is_session_active = MagicMock(return_value=True)
    pipeline = asyncio.create_task(svc.start_pipeline(session))
    await _settle()
    pipeline.cancel()  # VoiceOrchestrator.end_session
    await asyncio.gather(pipeline, return_exceptions=True)
    await _settle()

    assert task.cancelled()
    assert dispatched == [], "a queued turn ran after the pipeline ended"
    assert session._queued_next_turn is None


async def test_teardown_of_one_call_does_not_drop_another_calls_queue():
    svc = _svc()
    a, b = _session("call-A"), _session("call-B")
    dispatched_a, dispatched_b = [], []
    # Two independent calls on the same service; each records its own dispatches.
    real = svc.handle_turn_end

    async def spy(sess, ws=None, source="final", **kw):
        if source == "queued":
            (dispatched_a if sess is a else dispatched_b).append(kw.get("user_text"))
            return
        return await real(sess, ws, source=source, **kw)

    svc.handle_turn_end = spy
    gates = {a.call_id: asyncio.Event(), b.call_id: asyncio.Event()}
    parked = {a.call_id: asyncio.Event(), b.call_id: asyncio.Event()}

    async def run_turn(sess, transcript, ws, turn_id):
        parked[sess.call_id].set()
        await gates[sess.call_id].wait()
        return ("done", 1.0, 1.0)

    svc._run_turn = AsyncMock(side_effect=run_turn)
    tasks = {}
    for s in (a, b):
        svc._barge_in_events[s.call_id] = s.barge_in_event
        t = asyncio.create_task(svc.handle_turn_end(s, AsyncMock(), user_text="hours?"))
        t._turn_type = "final"
        svc._pending_llm_tasks[s.call_id] = t
        tasks[s.call_id] = t
        await parked[s.call_id].wait()
        _queue(s)

    await svc.cancel_active_turn(a.call_id)  # only call A is torn down
    gates[b.call_id].set()  # call B's reply finishes normally
    await asyncio.gather(tasks[b.call_id])
    await _settle()

    assert dispatched_a == []
    assert dispatched_b == ["thanks"]


# -- reviewer addition: dropping the queue must not drop a directed opt-out -----
#
# Before the tag, the queued words were dispatched on the dying call and the turn
# ran the opt-out step of TurnEnder.handle, which sets session._caller_opted_out;
# the call-end purge (lifecycle._on_call_ended) reads that flag AFTER
# cancel_active_turn returns. Dropping the words drops the only thing that would
# have set it, so a caller's queued "stop calling me" followed by a hangup would
# silently never reach the DNC list.

async def test_teardown_drop_keeps_a_directed_opt_out_in_the_queued_words():
    svc, session, dispatched = _svc(), _session(), []
    await _start_turn_in_tts(svc, session, dispatched)
    _queue(session, "stop calling me please")

    await svc.cancel_active_turn(session.call_id)
    await _settle()

    assert dispatched == []  # still no LLM+TTS round on the dead call
    assert session._queued_next_turn is None
    assert getattr(session, "_caller_opted_out", False) is True


async def test_teardown_drop_does_not_flag_ordinary_queued_words():
    svc, session, dispatched = _svc(), _session(), []
    await _start_turn_in_tts(svc, session, dispatched)
    _queue(session, "thanks a lot, take care")

    await svc.cancel_active_turn(session.call_id)
    await _settle()

    assert dispatched == []
    assert getattr(session, "_caller_opted_out", False) is False


async def test_teardown_drop_does_not_flag_an_ask_ai_session():
    """Same gate as the live turn: Ask AI has no lead to purge."""
    svc, session, dispatched = _svc(), _session("call-ask-ai"), []
    session.campaign_id = "ask-ai"
    await _start_turn_in_tts(svc, session, dispatched)
    _queue(session, "stop calling me please")

    await svc.cancel_active_turn(session.call_id)
    await _settle()

    assert dispatched == []
    assert getattr(session, "_caller_opted_out", False) is False


async def test_drop_after_the_agent_ended_the_call_keeps_a_queued_opt_out_too():
    """The pre-existing drop reasons share the branch: the agent hung up (ENDING)
    while the caller's last words, an opt-out, were still waiting."""
    svc, session, dispatched = _svc(), _session("call-agent-ended"), []
    task = await _start_turn_in_tts(svc, session, dispatched)
    _queue(session, "do not call me again")
    session.state = CallState.ENDING

    task.cancel()  # a plain cancel: no teardown tag, so only the state check applies
    await asyncio.gather(task, return_exceptions=True)
    await _settle()

    assert dispatched == []
    assert getattr(session, "_caller_opted_out", False) is True
