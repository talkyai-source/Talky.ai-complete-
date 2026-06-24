"""Reproduction tests for the multiple-barge-in SILENCE bug.

Root cause (confirmed on a live call 2026-06-24): ``handle_barge_in`` awaited the
cancelled turn task on the SINGLE STT consumer loop with NO timeout. While it
waited, rapid back-to-back barge-ins piled up in the bounded transcript queue;
when the loop resumed, every queued turn was either cancelled by a backlogged
barge-in or skipped because ``session.current_user_input`` had been reset to ""
— so NO turn survived and the agent went silent for ~20-30s until the caller
spoke again ("Hello?").

The fix has three parts, one test each:
  1. ``_cancel_turn_task`` bounds the cancel-wait so the consumer never freezes.
  2. The turn carries its transcript (``user_text``) so a ``current_user_input``
     reset can't strand it ("Empty transcript, skipping turn").
  3. ``turn_ender``'s ``finally`` only resets shared state if THIS task still
     owns the call's turn slot, so a stale cancelled task can't clobber a newer
     turn's ``llm_active`` / counter.

LOCAL ONLY — not committed.
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from app.domain.models.session import CallSession
from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline_service import VoicePipelineService


class _StreamingLLM:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_chat_with_timeout(self, *a, **k):
        for c in self._chunks:
            yield c

    async def stream_chat_with_tools(self, *a, **k):
        for c in self._chunks:
            yield c


def _make_service(chunks=("ok.",)):
    svc = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=_StreamingLLM(list(chunks)),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
        mute_during_tts=False,
    )
    svc.latency_tracker = MagicMock()
    svc.latency_tracker.get_metrics.return_value = None
    svc.transcript_service = MagicMock()
    return svc


def _make_session():
    s = CallSession(
        call_id="call-1", campaign_id="c", lead_id="l", provider_call_id="p",
        system_prompt="Use plain spoken text.", voice_id="v",
    )
    s.barge_in_event = asyncio.Event()
    return s


# ── 1. The freeze fix ───────────────────────────────────────────────────────

def test_cancel_turn_task_bounds_the_wait(monkeypatch):
    """A cancelled turn that is slow to unwind must NOT freeze the consumer.

    Old behaviour: an un-timed ``await`` here blocked the single STT consumer for
    the FULL unwind; ×N rapid barge-ins that backlog is what produced the dead
    air. New behaviour: bail after BARGE_IN_CANCEL_WAIT_S and let it finish in
    the background.
    """
    monkeypatch.setenv("BARGE_IN_CANCEL_WAIT_S", "0.1")
    svc = _make_service()

    async def scenario():
        resisting = asyncio.Event()

        async def slow_to_unwind():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                resisting.set()
                await asyncio.sleep(0.5)   # parked (e.g. flushing TTS) — slow to unwind
                raise

        task = asyncio.create_task(slow_to_unwind())
        await asyncio.sleep(0.02)          # let it reach the sleep
        t0 = time.monotonic()
        await svc._cancel_turn_task(task, "call-1", "test")
        elapsed = time.monotonic() - t0
        await asyncio.gather(task, return_exceptions=True)   # clean up the detached unwind
        return elapsed, resisting.is_set()

    elapsed, was_cancelled = asyncio.run(scenario())
    assert was_cancelled, "the task should have been cancelled"
    # Must bail near 0.1s, NOT wait the full 0.5s unwind.
    assert elapsed < 0.35, f"_cancel_turn_task blocked {elapsed:.2f}s — the consumer would freeze"


# ── 2. The strand fix ───────────────────────────────────────────────────────

def test_turn_uses_carried_transcript_when_input_was_reset():
    """A wiped current_user_input must NOT strand a turn that carries user_text."""
    svc = _make_service()
    svc._run_turn = AsyncMock(return_value=("ok response", 10.0, 10.0))
    session = _make_session()
    # prior user turn so the turn-0 floor doesn't reject
    session.conversation_history.append(Message(role=MessageRole.USER, content="earlier"))
    session.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="a reply"))
    # The bug condition: a barge-in already wiped the shared input field...
    session.current_user_input = ""

    # ...but the turn was scheduled carrying the real transcript.
    asyncio.run(svc.handle_turn_end(
        session, AsyncMock(), source="final", user_text="what are your opening hours",
    ))

    svc._run_turn.assert_awaited_once()
    passed_transcript = svc._run_turn.call_args.args[1]
    assert passed_transcript == "what are your opening hours", passed_transcript


def test_turn_without_carried_transcript_is_stranded_on_empty_input():
    """Documents the OLD failure: empty current_user_input + no carried text → dropped."""
    svc = _make_service()
    svc._run_turn = AsyncMock(return_value=("ok", 1.0, 1.0))
    session = _make_session()
    session.conversation_history.append(Message(role=MessageRole.USER, content="earlier"))
    session.current_user_input = ""   # wiped by a barge-in

    asyncio.run(svc.handle_turn_end(session, AsyncMock(), source="final"))  # no user_text

    svc._run_turn.assert_not_called()   # stranded → exactly why we now carry the text


# ── 3. The clobber fix ──────────────────────────────────────────────────────

def test_stale_task_finally_does_not_clobber_a_newer_turn():
    """A superseded task's finally must not reset a newer turn's llm_active/slot."""
    svc = _make_service()
    session = _make_session()
    session.conversation_history.append(Message(role=MessageRole.USER, content="earlier"))

    newer_task = MagicMock()
    newer_task.done.return_value = False        # a live, DIFFERENT task owns the slot

    async def run_turn_stub(sess, transcript, ws, turn_id):
        # Simulate a newer turn claiming the slot + activating mid-flight.
        svc._pending_llm_tasks[sess.call_id] = newer_task
        sess.llm_active = True
        return ("ok", 1.0, 1.0)

    svc._run_turn = AsyncMock(side_effect=run_turn_stub)
    asyncio.run(svc.handle_turn_end(session, AsyncMock(), source="final", user_text="hi there"))

    # The stale turn's finally saw a different live owner → left the new state intact.
    assert session.llm_active is True, "stale task clobbered the newer turn's llm_active"
    assert svc._pending_llm_tasks.get(session.call_id) is newer_task
