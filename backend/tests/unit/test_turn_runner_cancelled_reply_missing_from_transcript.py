"""Reproduction of the STILL-OPEN part of hangup-vs-inflight-reply, 12c
(review of 4d269660, 2026-09-24).

PRODUCTION EVIDENCE
--------------------
Call adf41aa1 (browser Test-agent): the tester sent an explicit "Hi", the
agent's reply finished generating 10ms AFTER the client's end_call frame.
campaign_test_ws.py's teardown now cancels the in-flight turn (with the
session's own call_id) BEFORE running _persist_test_transcript — see
test_campaign_test_ws_teardown_order.py for that ordering, which is real and
correct. But ordering alone cannot restore adf41aa1's reply, because
cancelling the turn routes through turn_runner.py's own
``except asyncio.CancelledError`` branch, and THAT branch never calls
``transcript_service.accumulate_turn`` — it only re-adds the spoken partial
to ``session.conversation_history`` (the LLM's own context for the next
turn). No matter when ``_persist_test_transcript`` reads the transcript
buffer relative to the cancel, the assistant's spoken partial was never
written there in the first place.

This test drives the REAL ``TurnRunner.run`` (not a mock of it) with a REAL
``TranscriptService``, cancels the in-flight task exactly the way
``VoicePipelineService.cancel_active_turn`` does (task.cancel() + await), and
asserts on what the saved transcript actually CONTAINS — not on call order.

FIX NEEDED (outside this fence — turn_runner.py is explicitly read-only for
this brief): turn_runner.py's ``except asyncio.CancelledError`` branch
(~line 690-697) must also call ``self._p.transcript_service.accumulate_turn``
with the spoken partial (matching what the success path does at line 663),
or campaign_test_ws.py's finally block must independently commit
``session._spoken_sentences`` to the transcript before end_session tears the
pipeline down (also outside this fence: it needs a signal turn_runner.py
does not currently expose past `conversation_history`).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.domain.models.conversation import MessageRole
from app.domain.services.transcript_service import TranscriptService
from app.domain.services.voice_pipeline.turn_runner import TurnRunner
from app.services.scripts import CallState

pytestmark = pytest.mark.asyncio

CALL_ID = "adf41aa1-0000-0000-0000-000000000000"
SPOKEN_PARTIAL = "Sure, I can help with that."


class _StallingPipeline:
    """Stand-in for VoicePipelineService: _stream_llm_and_tts "speaks" a
    partial reply (mirrors turn_streamer.py populating
    session._spoken_sentences mid-turn) and then hangs — the same shape a
    real turn is in when cancel_active_turn cancels it mid-TTS."""

    def __init__(self, transcript_service):
        self.stt_provider = None
        self.transcript_service = transcript_service

    async def _stream_llm_and_tts(self, session, websocket):
        session._spoken_sentences = [SPOKEN_PARTIAL]
        await asyncio.sleep(10)  # never reached — cancelled first
        raise AssertionError("should have been cancelled before this returns")

    def _supports_llm_end_session_action(self, session) -> bool:
        return False


def _session():
    return SimpleNamespace(
        call_id=CALL_ID,
        turn_id=1,
        talklee_call_id=None,
        tts_active=True,
        captured_slots=CallState(),
        conversation_history=[],
    )


async def test_cancelled_turns_spoken_partial_never_reaches_the_transcript_service():
    """DOCUMENTS THE GAP (does not assert a fix — the fix is out of fence).

    Whatever order teardown runs cancel-vs-persist in, a cancelled turn's
    spoken partial is committed only to conversation_history, never to
    transcript_service.accumulate_turn — this is why adf41aa1's agent line
    stayed lost even after the ordering fix in campaign_test_ws.py.
    """
    transcripts = TranscriptService()
    transcripts.clear_buffer(CALL_ID)
    session = _session()

    task = asyncio.create_task(
        TurnRunner(_StallingPipeline(transcripts)).run(session, "Hi")
    )
    # Let _stream_llm_and_tts run up to its "speak the partial, then suspend"
    # point — the exact state a real in-flight turn is in when
    # cancel_active_turn cancels it.
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    try:
        # The partial IS recovered into the LLM's OWN context (this part is
        # correct and unrelated to the gap under test)...
        assert session.conversation_history[-1].role == MessageRole.ASSISTANT
        assert session.conversation_history[-1].content == (
            f"{SPOKEN_PARTIAL} [interrupted by caller]"
        )

        # ...but the persisted transcript never receives it. THIS is
        # adf41aa1's actual symptom, and it is unaffected by cancel-vs-persist
        # ordering. If this assertion ever fails (assistant_turns non-empty),
        # turn_runner.py's CancelledError branch has started calling
        # accumulate_turn — update this test's docstring and the
        # campaign_test_ws.py comment it's cross-referenced from to say the
        # gap is closed, do not just delete the assertion.
        saved = transcripts.get_turns(CALL_ID)
        assistant_turns = [t for t in saved if t.role == "assistant"]
        assert assistant_turns == [], (
            f"expected the cancelled turn's spoken partial to be MISSING "
            f"from the transcript service (the still-open gap); found "
            f"{assistant_turns!r} instead"
        )
    finally:
        transcripts.clear_buffer(CALL_ID)
