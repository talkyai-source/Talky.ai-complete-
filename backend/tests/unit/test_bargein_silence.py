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


# ── 4. F-08 — a distinct 2nd utterance while turn 1 is still "thinking" ─────
#
# Root cause (2026-07-20): _on_barge_in_direct armed the barge-in event
# UNCONDITIONALLY, so a 2nd StartOfTurn while turn 1's LLM was still in
# flight (tts_active=False, nothing audible yet) pre-armed the event turn 1's
# own TTS would see the instant it tried to speak — turn 1 never spoke.
# Separately, transcript_handler's "existing task still running" guard always
# collapsed a 2nd EndOfTurn into a no-op promote-to-final, so turn 2's
# content was simply lost. Net effect: BOTH turns silenced.
#
# Fix: (1) audio_ingest / handle_barge_in only arm the event when
# tts_active is True; (2) transcript_handler distinguishes a genuine
# duplicate from a distinct new utterance (utterance-seq, with a
# current_user_input content fallback for the case Flux's own StartOfTurn
# gate suppressed the seq bump) and QUEUES a distinct one depth-1 instead of
# dropping it; (3) turn_ender dispatches the queued turn from its `finally`,
# but ONLY when this task still owns the turn slot (_owns_slot) — never from
# a stale/superseded task.

from app.domain.models.conversation import TranscriptChunk
from app.domain.services.voice_pipeline.audio_ingest import AudioIngest


def _tagged_pending_task(seq, text, turn_type="final"):
    """A never-finishing task tagged the way transcript_handler tags a
    freshly-created turn task, for tests that drive the dedup/queue logic
    directly without running a real turn to completion. Must be called from
    inside a running event loop (e.g. within an `asyncio.run(scenario())`)."""
    async def _never():
        await asyncio.Event().wait()

    t = asyncio.create_task(_never())
    t._turn_type = turn_type
    t._utterance_seq = seq
    t._source_text = text
    return t


class _FluxLikeSTT:
    """Minimal stand-in for the real STT provider's detect_turn_end: Flux
    semantics — EndOfTurn is signalled by an empty, is_final chunk."""

    def detect_turn_end(self, transcript) -> bool:
        return bool(transcript.is_final) and not transcript.text


class _TwoTurnLLM:
    """Each call's stream blocks on its own release event before yielding —
    call 1 simulates turn 1 still "thinking" (nothing sent to TTS yet,
    tts_active stays False); call 2 gives the test the same deterministic
    control over turn 2's completion, so the dispatched task can be observed
    mid-flight instead of racing the event loop's own scheduling order."""

    def __init__(self, release1: asyncio.Event, release2: asyncio.Event):
        self._releases = [release1, release2]
        self._texts = ["Turn one reply. ", "Turn two reply. "]
        self._call_n = 0

    async def _gen(self):
        idx = self._call_n
        self._call_n += 1
        await self._releases[idx].wait()
        yield self._texts[idx]

    async def stream_chat_with_timeout(self, *a, **k):
        async for c in self._gen():
            yield c

    async def stream_chat_with_tools(self, *a, **k):
        async for c in self._gen():
            yield c


def test_distinct_second_utterance_while_thinking_is_queued_not_dropped():
    """Full integration: turn 1 pending (slow LLM, pre-TTS) + a DISTINCT 2nd
    utterance's EndOfTurn arrives — turn 1 must still speak, the 2nd turn
    must be queued (not dropped), and both replies must land in history
    once turn 1 releases the slot."""
    release1 = asyncio.Event()
    release2 = asyncio.Event()
    svc = VoicePipelineService(
        stt_provider=_FluxLikeSTT(),
        llm_provider=_TwoTurnLLM(release1, release2),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
        mute_during_tts=False,
    )
    svc.latency_tracker = MagicMock()
    svc.latency_tracker.get_metrics.return_value = None
    svc.transcript_service = MagicMock()
    svc.synthesize_and_send_audio = AsyncMock(return_value=False)  # never interrupted

    session = _make_session()
    call_id = session.call_id
    svc._barge_in_events[call_id] = session.barge_in_event
    # Prior turn so the turn-0 floor / instant-opener paths don't apply.
    session.conversation_history.append(Message(role=MessageRole.USER, content="earlier"))
    session.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="hi"))

    async def scenario():
        # ── Turn 1: text chunk then Deepgram's always-emitted empty EOT marker.
        svc._utterance_seq[call_id] = 1
        await svc.handle_transcript(
            session, TranscriptChunk(text="what are your hours", is_final=True),
        )
        await svc.handle_transcript(session, TranscriptChunk(text="", is_final=True))

        task1 = svc._pending_llm_tasks.get(call_id)
        assert task1 is not None and not task1.done()
        await asyncio.sleep(0)  # let turn 1's task actually start and block on `release`

        # (i) Still "thinking" — nothing has been sent to TTS, so the event
        # must NOT have been armed by anything in this flow.
        assert not session.barge_in_event.is_set()

        # ── Turn 2: a DISTINCT utterance — new StartOfTurn bumps the seq.
        svc._utterance_seq[call_id] = 2
        await svc.handle_transcript(
            session, TranscriptChunk(text="also can you text me the address", is_final=True),
        )
        await svc.handle_transcript(session, TranscriptChunk(text="", is_final=True))

        # (ii) Queued, not dropped — and turn 1's task is untouched/still running.
        queued = getattr(session, "_queued_next_turn", None)
        assert queued is not None, "distinct 2nd utterance was dropped, not queued"
        assert queued["text"] == "also can you text me the address"
        assert queued["seq"] == 2
        assert svc._pending_llm_tasks.get(call_id) is task1
        assert not task1.done()

        # Release turn 1 and let it finish. Turn 2's LLM call blocks on
        # release2 (still unset), so turn 2 is dispatched but parked —
        # giving us a deterministic window to observe it mid-flight instead
        # of racing the event loop's own scheduling order.
        release1.set()
        for _ in range(1000):
            if svc._pending_llm_tasks.get(call_id) is not task1:
                break
            await asyncio.sleep(0)
        else:
            raise AssertionError("turn 1 never completed / dispatched turn 2")

        # (iii) turn_ender's finally dispatched the queued turn automatically.
        task2 = svc._pending_llm_tasks.get(call_id)
        assert task2 is not None and task2 is not task1
        release2.set()
        await asyncio.wait_for(task2, timeout=2.0)
        assert getattr(session, "_queued_next_turn", None) is None

        return session.conversation_history

    history = asyncio.run(scenario())

    # (iv) Both assistant replies landed in conversation_history.
    assistant_texts = " ".join(
        m.content for m in history if m.role == MessageRole.ASSISTANT
    )
    assert "Turn one reply." in assistant_texts, assistant_texts
    assert "Turn two reply." in assistant_texts, assistant_texts


def test_duplicate_same_seq_and_text_is_still_collapsed():
    """Regression: a 2nd EndOfTurn carrying the SAME seq/text as the pending
    task (e.g. Flux EndOfTurn->TurnResumed->EndOfTurn on ONE utterance) must
    still be treated as a duplicate — promoted-to-final, no queuing."""
    svc = _make_service()
    svc.stt_provider.detect_turn_end = MagicMock(
        side_effect=lambda t: t.is_final and not t.text
    )
    session = _make_session()
    call_id = session.call_id
    svc._utterance_seq[call_id] = 1
    session.current_user_input = "same utterance text"

    async def scenario():
        task = _tagged_pending_task(seq=1, text="same utterance text")
        svc._pending_llm_tasks[call_id] = task
        await svc.handle_transcript(
            session, TranscriptChunk(text="same utterance text", is_final=True),
        )
        await svc.handle_transcript(session, TranscriptChunk(text="", is_final=True))
        result = (
            getattr(session, "_queued_next_turn", None),
            task._turn_type,
            svc._pending_llm_tasks.get(call_id) is task,
        )
        task.cancel()
        return result

    queued, turn_type, kept_same_task = asyncio.run(scenario())

    assert queued is None
    assert turn_type == "final"
    assert kept_same_task, "duplicate must not replace the pending task"


def test_grown_utterance_with_suppressed_seq_bump_is_distinct_via_content():
    """Grow-through-gate: Flux's own StartOfTurn gate suppressed the barge-in
    callback for this utterance (no seq bump), but current_user_input grew
    into text different from what launched the pending task — must still be
    classified DISTINCT via the content fallback, not silently dropped."""
    svc = _make_service()
    svc.stt_provider.detect_turn_end = MagicMock(
        side_effect=lambda t: t.is_final and not t.text
    )
    session = _make_session()
    call_id = session.call_id
    svc._utterance_seq[call_id] = 1  # NOT bumped for this utterance (gate suppressed it)

    async def scenario():
        task = _tagged_pending_task(seq=1, text="yeah")
        svc._pending_llm_tasks[call_id] = task
        await svc.handle_transcript(
            session, TranscriptChunk(text="yeah, but what does it cost", is_final=True),
        )
        await svc.handle_transcript(session, TranscriptChunk(text="", is_final=True))
        task.cancel()

    asyncio.run(scenario())

    queued = getattr(session, "_queued_next_turn", None)
    assert queued is not None, "grown utterance (content differs, seq unchanged) must be distinct"
    assert queued["text"] == "yeah, but what does it cost"
    assert queued["seq"] == 1


class _ImmediateBargeSTT:
    """Fires the pipeline's on_barge_in callback exactly once, mirroring how
    the real STT provider invokes it from within stream_transcribe, then
    ends the stream (no transcripts)."""

    async def stream_transcribe(self, audio_iter, call_id=None, on_barge_in=None, **kwargs):
        if on_barge_in:
            on_barge_in()
        return
        yield  # pragma: no cover - unreachable; keeps this an async generator


def test_on_barge_in_direct_does_not_arm_event_while_thinking():
    """Direct test of the audio_ingest closure: a StartOfTurn that reaches
    _on_barge_in_direct while tts_active=False (agent thinking, nothing
    audible) must bump the utterance seq but NOT arm the barge-in event."""
    pipeline = MagicMock()
    pipeline.media_gateway.get_audio_queue.return_value = asyncio.Queue(maxsize=10)
    pipeline.stt_provider = _ImmediateBargeSTT()
    pipeline._barge_in_events = {}
    pipeline._barge_in_epoch = {}
    pipeline._utterance_seq = {}
    pipeline.latency_tracker = MagicMock()
    pipeline.latency_tracker.get_metrics.return_value = None
    pipeline.synthesize_and_send_audio = AsyncMock()

    session = _make_session()
    session.stt_active = True
    session.tts_active = False
    pipeline._barge_in_events[session.call_id] = session.barge_in_event

    ingest = AudioIngest(pipeline)

    async def scenario():
        task = asyncio.ensure_future(ingest.process(session))
        await asyncio.sleep(0.05)
        session.stt_active = False
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()

    asyncio.run(scenario())

    assert not session.barge_in_event.is_set(), (
        "barge-in event armed while tts_active=False (thinking) — F-08 regression"
    )
    assert getattr(session, "_last_caller_activity_monotonic", None) is not None
    assert pipeline._utterance_seq.get(session.call_id) == 1


def test_handle_barge_in_does_not_arm_event_for_speculative_task_while_thinking():
    """Same F-08 gate, exercised through VoicePipelineService.handle_barge_in
    directly: a speculative (non-final) pending task with tts_active=False
    must be cancelled as before, but the event must not be armed — there is
    nothing audible to stop."""
    svc = _make_service()
    session = _make_session()
    call_id = session.call_id
    svc._barge_in_events[call_id] = session.barge_in_event
    session.tts_active = False

    async def scenario():
        spec_task = _tagged_pending_task(seq=1, text="x", turn_type="speculative")
        svc._pending_llm_tasks[call_id] = spec_task
        await svc.handle_barge_in(session, AsyncMock())

    asyncio.run(scenario())

    assert not session.barge_in_event.is_set()
    assert call_id not in svc._pending_llm_tasks


# ── F-10 x F-08 interaction ─────────────────────────────────────────────────

class _EchoSTT:
    """Fires the pipeline's on_barge_in callback exactly once WITH the
    recognized text (mirrors how deepgram_flux invokes it after F-10), then
    ends the stream — no transcript chunks. Used to drive the real
    _on_barge_in_direct closure for the opener-echo seq-bump ordering test."""

    async def stream_transcribe(self, audio_iter, call_id=None, on_barge_in=None, **kwargs):
        if on_barge_in:
            on_barge_in("hello")
        return
        yield  # pragma: no cover - unreachable; keeps this an async generator


def test_opener_echo_does_not_bump_seq_and_matching_eot_is_deduped():
    """Regression for the F-10 x F-08 interaction: if an IGNORED opener echo
    still bumped _utterance_seq, a matching text EndOfTurn ("hello") arriving
    while the opener task is still running would look DISTINCT under F-08's
    seq-mismatch check and get queued as a spurious extra LLM turn — the
    agent answering its own opener echo. Moving the seq bump to AFTER the
    is_opener_echo early-return (this fix) must keep the seq unchanged for
    an ignored echo, so the matching EndOfTurn collapses as a duplicate
    (existing task promoted to final, nothing queued) exactly like a real
    Flux EndOfTurn->TurnResumed->EndOfTurn split of one utterance."""
    svc = _make_service()
    svc.stt_provider.detect_turn_end = MagicMock(
        side_effect=lambda t: t.is_final and not t.text
    )
    session = _make_session()
    call_id = session.call_id
    session._instant_opener_in_flight = True
    svc._utterance_seq[call_id] = 0

    async def scenario():
        # Opener task tagged the way try_instant_opener's caller (turn_ender)
        # tags a fresh turn task: seq at launch time (0) + source text.
        task = _tagged_pending_task(seq=0, text="hello")
        svc._pending_llm_tasks[call_id] = task

        # ── Phase 1: StartOfTurn echo through the REAL audio_ingest closure.
        pipeline = MagicMock()
        pipeline.media_gateway.get_audio_queue.return_value = asyncio.Queue(maxsize=10)
        pipeline.stt_provider = _EchoSTT()
        pipeline._barge_in_events = {call_id: session.barge_in_event}
        pipeline._barge_in_epoch = {}
        pipeline._utterance_seq = svc._utterance_seq  # shared dict — same counter
        pipeline.latency_tracker = MagicMock()
        pipeline.latency_tracker.get_metrics.return_value = None
        pipeline.synthesize_and_send_audio = AsyncMock()

        session.stt_active = True
        ingest = AudioIngest(pipeline)
        ingest_task = asyncio.ensure_future(ingest.process(session))
        await asyncio.sleep(0.05)
        session.stt_active = False
        try:
            await asyncio.wait_for(ingest_task, timeout=1.0)
        except asyncio.TimeoutError:
            ingest_task.cancel()

        assert svc._utterance_seq.get(call_id, 0) == 0, (
            "an ignored opener echo must NOT bump _utterance_seq"
        )

        # ── Phase 2: the matching "hello" EndOfTurn reaches transcript_handler
        # while the opener task is still "in flight" — must dedupe, not queue.
        await svc.handle_transcript(
            session, TranscriptChunk(text="hello", is_final=True),
        )
        await svc.handle_transcript(session, TranscriptChunk(text="", is_final=True))

        result = (
            getattr(session, "_queued_next_turn", None),
            task._turn_type,
            svc._pending_llm_tasks.get(call_id) is task,
        )
        task.cancel()
        return result

    queued, turn_type, kept_same_task = asyncio.run(scenario())

    assert queued is None, "opener echo's EndOfTurn was queued as a spurious extra turn"
    assert turn_type == "final"
    assert kept_same_task, "duplicate must not replace the pending opener task"
