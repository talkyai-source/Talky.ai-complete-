"""A bare 'Yes.' after an embedded (non-terminal) question must reach the LLM.

THE DEFECT (production, 2026-09-23 and 2026-09-22)
----------------------------------------------------
turn_ender.py only trusted the agent's last turn as a QUESTION when the whole
message ended in "?". The Dojo-PC opener embeds its permission question
mid-sentence and then appends a declarative clause:

    "Alex here from Dojo — got a minute? We're checking in on your payment
    setup."

That line ends in "." — so `_agent_asked_question` was False even though the
caller had just been asked something. Call 36357ad0 (18:14:49.056037): the
caller's finalized "Yes." was classified as a backchannel and silently
dropped (`backchannel_suppressed transcript_chars=4`), and the agent never
replied — 9.3s of dead air until the tester closed the browser. The same
signature reproduced the day before on 2b36df30 (19:13:27.701).

THE FIX
-------
Treat the agent's last turn as a question if ANY of its sentences ends in
"?", not just the final character of the whole message. A true backchannel
after a message with NO question anywhere must still be suppressed.

Driven through the REAL TurnEnder.handle via the same STT-boundary path
production uses (TranscriptHandler.handle -> handle_turn_end -> TurnEnder),
not a mock of the backchannel/question-detection logic itself.
"""
from __future__ import annotations

import asyncio

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler
from app.domain.services.voice_pipeline.turn_ender import TurnEnder

DOJO_OPENER = (
    "Alex here from Dojo — got a minute? We're checking in on your payment setup."
)


def real_session(**over) -> CallSession:
    s = CallSession(
        call_id="36357ad0-6b90-4c3e-9a1a-000000000000",
        campaign_id="790ca2db-0000-0000-0000-000000000000",
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
    def __init__(self, text="", is_final=True, confidence=None, metadata=None):
        self.text = text
        self.is_final = is_final
        self.confidence = confidence
        self.metadata = metadata or {}


def eot_marker() -> _Chunk:
    return _Chunk(text="", is_final=True)


class _STT:
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
    """Minimal enough to see, real enough to route — mirrors the harness in
    test_caller_speaking_cleared_on_end_of_turn.py. Everything past the
    backchannel guard (identity disposition etc.) is out of scope for this
    fix; a crash there is swallowed by drain()'s return_exceptions=True, the
    same way that file's own happy-path test tolerates it."""

    def __init__(self):
        self.stt_provider = _STT()
        self.latency_tracker = _Tracker()
        self.transcript_service = _TranscriptService()
        self._utterance_seq = {}
        self._pending_llm_tasks = {}
        self._barge_in_events = {}
        self.spoken = []
        self.repetitive = False

    def _is_repetitive_transcript(self, text): return self.repetitive

    async def synthesize_and_send_audio(self, session, text, websocket=None, **k):
        self.spoken.append(text)
        return False

    async def handle_barge_in(self, session, websocket=None, **k): pass

    async def _cancel_turn_task(self, task, call_id, reason): pass

    async def handle_turn_end(self, session, websocket=None, **k):
        return await TurnEnder(self).handle(session, websocket, **k)


async def drain(pipeline):
    for _ in range(3):
        tasks = [t for t in pipeline._pending_llm_tasks.values() if not t.done()]
        if not tasks:
            await asyncio.sleep(0)
            continue
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)


async def deliver_end_of_turn(pipeline, session):
    await TranscriptHandler(pipeline).handle(session, eot_marker())
    await drain(pipeline)


@pytest.mark.asyncio
async def test_bare_yes_after_a_compound_question_opener_is_not_dropped(caplog):
    """36357ad0: caller says 'Yes.' right after the Dojo-PC opener's embedded
    'got a minute?'. Must be treated as an answer, not a backchannel."""
    p, s = _Pipeline(), real_session(current_user_input="Yes.")
    s.conversation_history = [
        Message(role=MessageRole.ASSISTANT, content="Hello?"),
        Message(role=MessageRole.USER, content="Hello?"),
        Message(role=MessageRole.ASSISTANT, content=DOJO_OPENER),
    ]
    with caplog.at_level("INFO"):
        await deliver_end_of_turn(p, s)

    assert "backchannel_suppressed" not in caplog.text
    assert "backchannel_allowed reason=answers_agent_question" in caplog.text


@pytest.mark.asyncio
async def test_a_true_backchannel_after_a_pure_statement_is_still_suppressed(caplog):
    """No question anywhere in the agent's last turn -> a bare 'yeah' is
    still a listening noise, not an answer."""
    p, s = _Pipeline(), real_session(current_user_input="yeah")
    s.conversation_history = [
        Message(role=MessageRole.ASSISTANT, content="Hello?"),
        Message(role=MessageRole.USER, content="Hello?"),
        Message(role=MessageRole.ASSISTANT, content="Got it, thanks for that."),
    ]
    with caplog.at_level("INFO"):
        await deliver_end_of_turn(p, s)

    assert "backchannel_suppressed" in caplog.text
