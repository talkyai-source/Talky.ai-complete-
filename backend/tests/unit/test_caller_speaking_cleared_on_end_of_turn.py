"""`_caller_speaking` must be false once the caller's turn has ended — on EVERY path.

THE DEFECT (measured in production 2026-08-17..19)
--------------------------------------------------
`_caller_speaking` is raised by StartOfTurn (audio_ingest._on_barge_in_direct)
and gates playback_gate's pre-TTS hold. It was lowered in exactly ONE place —
turn_ender.py, just after the `turn_end` log — which sits behind twelve early
returns:

    transcript_handler.handle   backchannel-during-TTS, suppressed empty EOT
                                marker, duplicate EndOfTurn, queued-behind-pending
    turn_ender.handle           empty transcript, turn-0 rejection, instant
                                opener, repetitive hallucination, backchannel
                                suppression, pending task, llm busy, self-echo

Any EndOfTurn leaving through one of those left the flag stuck True, so the next
armed hold burned its full 2.5s cap. The production signature was total:

    16 pre_tts_hold_timeout      0 pre_tts_hold_released      (every day, since
                                                               the gate shipped)

    2026-08-18T19:58:54  turn_end                        <- flag cleared
    2026-08-18T19:58:54  barge_in_ignored_final_pre_tts  <- StartOfTurn: flag TRUE, hold armed
    2026-08-18T19:58:55  audio_level
    2026-08-18T19:58:56  audio_level
    2026-08-18T19:58:57  pre_tts_hold_timeout waited=2.51s

THE FIX
-------
Clear where the FACT becomes true — the moment the provider says the turn ended
— not at the end of the happy path. The clear in turn_ender stays as defence in
depth (it also covers the queued-turn dispatch, which re-enters turn_ender
without a fresh EndOfTurn).

WHY A REAL CallSession
----------------------
`CallSession` is a pydantic v2 model without `extra="allow"`: assigning an
undeclared PUBLIC attribute raises, while an underscore-prefixed one is quietly
accepted into the private-attribute store. Two features shipped dead for days
because their tests used `types.SimpleNamespace`, which accepts both. Every test
here builds the real model.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline import turn_ender as turn_ender_mod
from app.domain.services.voice_pipeline.playback_gate import (
    caller_is_speaking,
    mark_caller_speaking,
)
from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler
from app.domain.services.voice_pipeline.turn_ender import TurnEnder


# ── the real session ────────────────────────────────────────────────────────

def real_session(**over) -> CallSession:
    s = CallSession(
        call_id="06a6f8c9-89c9-4707-ab3b-98d12f4930fe",
        campaign_id="c2b6734d-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1",
        provider_call_id="talky-out-1",
        system_prompt="sp",
        voice_id="v1",
    )
    s.state = CallState.LISTENING
    for k, v in over.items():
        setattr(s, k, v)
    return s


class _Chunk:
    """A TranscriptChunk shaped exactly as deepgram_flux emits them."""

    def __init__(self, text="", is_final=True, confidence=None, metadata=None):
        self.text = text
        self.is_final = is_final
        self.confidence = confidence
        self.metadata = metadata or {}


def eot_marker() -> _Chunk:
    """The EndOfTurn control marker.

    Flux emits TWO chunks per EndOfTurn — the text, then this empty final. Only
    this one satisfies `detect_turn_end` (`is_final and not text`), so this is
    the chunk the clear has to key on.
    """
    return _Chunk(text="", is_final=True)


# ── a pipeline stub thin enough to see, real enough to route ────────────────

class _STT:
    """Provider-agnostic default from STTProvider — what production resolves to
    through the resilient wrapper, for Flux and Nova alike."""

    @staticmethod
    def detect_turn_end(chunk) -> bool:
        return bool(chunk.is_final) and not chunk.text


class _Tracker:
    def get_metrics(self, call_id): return None
    def start_turn(self, *a, **k): pass
    def mark_listening_start(self, *a, **k): pass
    def mark_stt_first_transcript(self, *a, **k): pass


class _TranscriptService:
    def bind_call_identity(self, *a, **k): pass
    def accumulate_turn(self, *a, **k): pass


class _Pipeline:
    def __init__(self):
        self.stt_provider = _STT()
        self.latency_tracker = _Tracker()
        self.transcript_service = _TranscriptService()
        self._utterance_seq = {}
        self._pending_llm_tasks = {}
        self._barge_in_events = {}
        self.spawned = []
        self.spoken = []
        self.repetitive = False

    # turn_ender collaborators
    def _is_repetitive_transcript(self, text): return self.repetitive

    async def synthesize_and_send_audio(self, session, text, websocket=None, **k):
        self.spoken.append(text)
        return False

    async def handle_barge_in(self, session, websocket=None, **k): pass

    async def _cancel_turn_task(self, task, call_id, reason): pass

    async def handle_turn_end(self, session, websocket=None, **k):
        """Route to the REAL TurnEnder so its eight early returns are exercised."""
        return await TurnEnder(self).handle(session, websocket, **k)


async def drain(pipeline):
    """Await whatever transcript_handler spawned — the turn runs detached."""
    for _ in range(3):
        tasks = [t for t in pipeline._pending_llm_tasks.values() if not t.done()]
        if not tasks:
            await asyncio.sleep(0)
            continue
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)


async def deliver_end_of_turn(pipeline, session):
    """Exactly what the STT loop does with the marker."""
    await TranscriptHandler(pipeline).handle(session, eot_marker())
    await drain(pipeline)


# ═══════════════════════════════════════════════════════════════════════════
#  The four early returns inside transcript_handler.handle
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_path_01_backchannel_during_agent_speech():
    """The caller said "yeah" over the agent. Their turn still ENDED."""
    p, s = _Pipeline(), real_session(tts_active=True)
    mark_caller_speaking(s)
    # the text chunk is swallowed by the backchannel guard...
    await TranscriptHandler(p).handle(s, _Chunk(text="yeah", is_final=True))
    # ...and the marker that follows it is swallowed by the suppression below
    await deliver_end_of_turn(p, s)
    assert caller_is_speaking(s) is False


@pytest.mark.asyncio
async def test_path_02_suppressed_empty_eot_marker():
    """`empty_eot_marker_suppressed_for_backchannel` returns before everything."""
    p, s = _Pipeline(), real_session(tts_active=True)
    p._utterance_seq[s.call_id] = 4
    s._suppressed_backchannel_seq = 4          # the mark the backchannel left
    mark_caller_speaking(s)

    await deliver_end_of_turn(p, s)

    assert caller_is_speaking(s) is False, (
        "the suppressed-marker path returns at the top of handle(); before the "
        "fix nothing downstream ever ran to lower the flag"
    )


@pytest.mark.asyncio
async def test_path_03_duplicate_end_of_turn():
    """A second EndOfTurn for the same utterance — collapsed as a duplicate."""
    p, s = _Pipeline(), real_session()
    mark_caller_speaking(s)

    async def _never():
        await asyncio.sleep(30)

    existing = asyncio.create_task(_never())
    existing._utterance_seq = 0
    existing._source_text = ""
    p._pending_llm_tasks[s.call_id] = existing
    try:
        await TranscriptHandler(p).handle(s, eot_marker())
        assert existing._turn_type == "final", "promotion still has to happen"
        assert caller_is_speaking(s) is False
    finally:
        existing.cancel()


@pytest.mark.asyncio
async def test_path_04_queued_behind_pending():
    """F-08. Second utterance finishes while turn 1 is still thinking.

    This is the path that pinned the flag on calls b3350aee and 9bf01238 — the
    last event before both hold timeouts was `turn_queued_behind_pending`.
    """
    p, s = _Pipeline(), real_session(current_user_input="a different question")
    mark_caller_speaking(s)
    p._utterance_seq[s.call_id] = 7

    async def _never():
        await asyncio.sleep(30)

    existing = asyncio.create_task(_never())
    existing._utterance_seq = 6           # different seq => distinct utterance
    existing._source_text = "the first question"
    p._pending_llm_tasks[s.call_id] = existing
    try:
        await TranscriptHandler(p).handle(s, eot_marker())
        assert s._queued_next_turn is not None, "the turn must still be queued"
        assert caller_is_speaking(s) is False
    finally:
        existing.cancel()


# ═══════════════════════════════════════════════════════════════════════════
#  The eight early returns inside turn_ender.handle
#  Each is driven END TO END: the EndOfTurn marker goes in at the STT boundary
#  exactly as production delivers it, and the real TurnEnder takes the exit.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_path_05_empty_transcript():
    p, s = _Pipeline(), real_session(current_user_input="   ")
    mark_caller_speaking(s)
    await deliver_end_of_turn(p, s)
    assert caller_is_speaking(s) is False


@pytest.mark.asyncio
async def test_path_06_turn_0_transcript_rejected(monkeypatch):
    monkeypatch.setattr(turn_ender_mod, "_should_reject_turn_0",
                        lambda *a, **k: "garbled")
    p, s = _Pipeline(), real_session(current_user_input="mmf")
    mark_caller_speaking(s)

    await deliver_end_of_turn(p, s)

    assert p.spoken, "the reprompt must still be spoken — rejection is never silent"
    assert caller_is_speaking(s) is False


@pytest.mark.asyncio
async def test_path_07_instant_opener(monkeypatch):
    import app.domain.services.voice_pipeline.instant_opener as io

    monkeypatch.setattr(turn_ender_mod, "_first_speaker_label", lambda s: "user")
    monkeypatch.setattr(io, "is_bare_greeting", lambda t: True)

    async def _took_it(session, text):
        return True

    monkeypatch.setattr(io, "try_instant_opener", _took_it)

    p, s = _Pipeline(), real_session(current_user_input="hello?")
    mark_caller_speaking(s)
    await deliver_end_of_turn(p, s)
    assert caller_is_speaking(s) is False


@pytest.mark.asyncio
async def test_path_08_repetitive_hallucination():
    p, s = _Pipeline(), real_session(current_user_input="blah blah blah blah blah blah")
    p.repetitive = True
    mark_caller_speaking(s)
    await deliver_end_of_turn(p, s)
    assert caller_is_speaking(s) is False


@pytest.mark.asyncio
async def test_path_09_backchannel_suppressed():
    p, s = _Pipeline(), real_session(current_user_input="mm hmm")
    s.conversation_history = [
        Message(role=MessageRole.USER, content="earlier"),
        Message(role=MessageRole.ASSISTANT, content="a statement, not a question."),
    ]
    mark_caller_speaking(s)
    await deliver_end_of_turn(p, s)
    assert caller_is_speaking(s) is False


@pytest.mark.asyncio
async def test_path_10_turn_skipped_pending_task():
    p, s = _Pipeline(), real_session(current_user_input="a real question")

    async def _never():
        await asyncio.sleep(30)

    other = asyncio.create_task(_never())
    mark_caller_speaking(s)
    try:
        # turn_ender is entered directly: the slot is held by a task that is
        # neither current nor done, so handle() bails at turn_skipped_pending_task
        p._pending_llm_tasks[s.call_id] = other
        await TranscriptHandler(p).handle(s, eot_marker())
        assert caller_is_speaking(s) is False
    finally:
        other.cancel()


@pytest.mark.asyncio
async def test_path_11_turn_skipped_llm_busy(caplog):
    """Reached only when the turn slot is EMPTY but llm_active is still set — a
    greeting or a previous turn that has not released the flag yet.

    It cannot be provoked through transcript_handler: that route registers the
    task in `_pending_llm_tasks` before the body runs, so `pending_task is
    current_task` and the guard is skipped. (Which matches production: zero
    turn_skipped_* in the whole journal window.) Driven directly instead, and
    asserted on the log so the test cannot silently stop reaching the branch —
    the way this same test passed against pristine code for the wrong reason.
    """
    p, s = _Pipeline(), real_session(current_user_input="a real question")
    mark_caller_speaking(s)

    await deliver_end_of_turn(p, s)          # the marker lowers the flag
    assert caller_is_speaking(s) is False

    s.llm_active = True
    p._pending_llm_tasks.clear()
    with caplog.at_level("INFO"):
        await TurnEnder(p).handle(s, None, user_text="a real question")

    assert "turn_skipped_llm_busy" in caplog.text, "never reached the branch"
    assert caller_is_speaking(s) is False, "the early return re-raised the flag"


@pytest.mark.asyncio
async def test_path_12_turn_skipped_self_echo():
    """The agent's own words came back through the carrier; nothing real is left."""
    echoed = "we can usually get that sorted within a couple of working days"
    p, s = _Pipeline(), real_session(current_user_input=echoed)
    s.conversation_history = [Message(role=MessageRole.ASSISTANT, content=echoed)]
    mark_caller_speaking(s)
    await deliver_end_of_turn(p, s)
    assert caller_is_speaking(s) is False


# ═══════════════════════════════════════════════════════════════════════════
#  Why the clear had to MOVE rather than be duplicated
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_turn_ender_alone_cannot_clear_on_an_early_return():
    """The defect, pinned. Entered directly — no EndOfTurn at the STT boundary —
    turn_ender's own clear is unreachable behind its guards, which is exactly
    why placing it there was never enough."""
    p, s = _Pipeline(), real_session(current_user_input="blah blah blah blah blah blah")
    p.repetitive = True
    mark_caller_speaking(s)

    await TurnEnder(p).handle(s, None, user_text=s.current_user_input)

    assert caller_is_speaking(s) is True, (
        "if this ever goes False, turn_ender learned to clear on its early "
        "returns and the note in transcript_handler needs revisiting"
    )


@pytest.mark.asyncio
async def test_the_happy_path_still_clears():
    """Defence in depth: the successful turn must keep clearing too."""
    p, s = _Pipeline(), real_session(current_user_input="what does it cost?")
    mark_caller_speaking(s)
    await deliver_end_of_turn(p, s)
    assert caller_is_speaking(s) is False


@pytest.mark.asyncio
async def test_a_text_chunk_does_not_clear_only_the_marker_does():
    """The caller is mid-utterance: interim and final TEXT chunks are not a turn
    boundary. Clearing on those would release the hold while they are talking —
    the exact talk-over the gate exists to prevent."""
    p, s = _Pipeline(), real_session()
    mark_caller_speaking(s)

    await TranscriptHandler(p).handle(s, _Chunk(text="what does", is_final=False))
    assert caller_is_speaking(s) is True

    await TranscriptHandler(p).handle(s, _Chunk(text="what does it cost", is_final=True))
    assert caller_is_speaking(s) is True, (
        "a text-bearing final is Flux's transcript, not its turn boundary"
    )


@pytest.mark.asyncio
async def test_a_provider_that_raises_does_not_kill_the_turn():
    """detect_turn_end is called on the hot path. A provider that throws must
    cost the clear, not the call."""
    class _Angry:
        @staticmethod
        def detect_turn_end(chunk):
            raise RuntimeError("provider exploded")

    p, s = _Pipeline(), real_session()
    p.stt_provider = _Angry()
    mark_caller_speaking(s)

    await TranscriptHandler(p).handle(s, eot_marker())   # must not raise

    assert caller_is_speaking(s) is True  # degrades to the old behaviour


@pytest.mark.asyncio
async def test_clearing_is_idempotent():
    p, s = _Pipeline(), real_session()
    mark_caller_speaking(s)
    await deliver_end_of_turn(p, s)
    await deliver_end_of_turn(p, s)
    assert caller_is_speaking(s) is False


def test_the_flag_survives_a_real_pydantic_model():
    """The trap, pinned. Underscore-prefixed names reach the private store; the
    public form raises. This is why the fix is invisible-until-production if it
    is only ever tested against SimpleNamespace."""
    s = real_session()
    mark_caller_speaking(s)
    assert s._caller_speaking is True
    assert isinstance(s._caller_speaking_since, float)

    with pytest.raises(ValueError, match="has no field"):
        s.caller_speaking = True


# ── TurnResumed is inert now that nothing speculative runs (2026-09-02) ──────

@pytest.mark.asyncio
async def test_turn_resumed_with_nothing_pending_does_not_touch_the_session():
    """Speculative turns were retired (transcript_handler: "do NOT launch a
    speculative turn on eager"), and every dispatched task is stamped final,
    so the cancel-and-rollback branch could never fire. Its one reachable
    effect was `session.llm_active = False` when NOTHING was pending — which
    could clear the flag out from under an in-flight greeting. TurnResumed
    must now leave the session exactly as it found it."""
    p = _Pipeline()
    s = real_session(llm_active=True)
    s.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="Hi there."))
    s._speculative_history_len = 0

    await TranscriptHandler(p).handle(s, _Chunk(text="", is_final=False, metadata={"resumed": True}))

    assert s.llm_active is True
    assert [m.content for m in s.conversation_history] == ["Hi there."]
    assert s._speculative_history_len == 0


@pytest.mark.asyncio
async def test_turn_resumed_never_cancels_a_pending_task():
    p = _Pipeline()
    s = real_session()

    async def _never():
        await asyncio.sleep(30)

    existing = asyncio.create_task(_never())
    existing._turn_type = "final"
    p._pending_llm_tasks[s.call_id] = existing
    try:
        await TranscriptHandler(p).handle(s, _Chunk(text="", is_final=False, metadata={"resumed": True}))
        await asyncio.sleep(0)
        assert not existing.cancelled()
        assert p._pending_llm_tasks.get(s.call_id) is existing
    finally:
        existing.cancel()
