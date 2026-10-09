"""A caller turn that stops the agent is still answered.

Test calls 08b2791d and 6b9cd4c4 (2026-10-08). The agent was speaking when the
caller said "Okay. Thank you. Bye." (08b2791d, 19:52:49) and "No" (6b9cd4c4,
19:17:27). At EndOfTurn the transcript handler stopped the agent first; that
stop (interrupt._do_interrupt, step 1) clears session.current_user_input, and
only afterwards did the handler read the caller's words. It dispatched an
empty turn, turn_ender dropped it at DEBUG ("Empty transcript, skipping
turn"), and the false-barge-in resume stood down because a newer caller turn
had been accepted. No reply, no goodbye: 6 s and 2 s of silence until the
caller gave up. The production journal shows the signature: barge_in_detected,
task_cancelled=True, then nothing until the session ends.

The pipeline below runs the real VoicePipelineService.handle_barge_in and the
real interrupt_playback, so the reset that caused this is exercised, not
stubbed.
"""
from __future__ import annotations

import asyncio

import pytest

from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler
from app.domain.services.voice_pipeline_service import VoicePipelineService


class _Chunk:
    def __init__(self, text="", is_final=True):
        self.text, self.is_final, self.confidence, self.metadata = text, is_final, None, {}


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
    def accumulate_turn(self, *a, **k): return None


class _Gateway:
    async def clear_output_buffer(self, call_id):
        return None


class _Pipeline:
    handle_barge_in = VoicePipelineService.handle_barge_in
    _cancel_turn_task = VoicePipelineService._cancel_turn_task
    _await_task_after_cancel = VoicePipelineService._await_task_after_cancel

    def __init__(self):
        self.stt_provider, self.latency_tracker = _STT(), _Tracker()
        self.transcript_service, self.media_gateway, self.tts_provider = _TranscriptService(), _Gateway(), None
        self._utterance_seq, self._pending_llm_tasks = {}, {}
        self._barge_in_events, self._barge_in_epoch = {}, {}
        self.turns, self.resumes = [], []

    async def _resume_after_false_barge_in(self, session, websocket, text, barge_at, **kwargs):
        self.resumes.append(text)

    async def handle_turn_end(self, session, websocket=None, **kwargs):
        self.turns.append(kwargs)


def _agent_speaking(p, seq=3):
    """The agent is mid-reply: TTS playing, the reply task still running."""
    s = CallSession(call_id="08b2791d-3886-45d0-898e-fb2b9b21bc72", campaign_id="c1", lead_id="l1",
                    provider_call_id="p1", system_prompt="sp", voice_id="v1")
    s.state, s.tts_active = CallState.SPEAKING, True
    p._utterance_seq[s.call_id] = seq
    p._barge_in_events[s.call_id] = asyncio.Event()

    async def _speaking():
        await asyncio.sleep(30)

    reply = asyncio.create_task(_speaking())
    reply._turn_type, reply._utterance_seq, reply._caller_turn_order = "final", seq, 19
    reply._source_text = "Can you send me over the details? I'm running out of time right now."
    p._pending_llm_tasks[s.call_id] = reply
    return s, reply


async def _deliver(p, s, chunks):
    handler = TranscriptHandler(p)
    for chunk in chunks:
        await handler.handle(s, chunk)
    for task in list(p._pending_llm_tasks.values()):
        await asyncio.gather(task, return_exceptions=True)


async def test_a_goodbye_that_began_as_okay_is_answered():
    # Flux suppressed the StartOfTurn as a backchannel ("Okay."), so no seq bump.
    p = _Pipeline()
    s, reply = _agent_speaking(p)
    await _deliver(p, s, [_Chunk("Okay.", is_final=False), _Chunk("Okay. Thank you.", is_final=False),
                          _Chunk("Okay. Thank you. Bye."), _Chunk("")])
    assert reply.cancelled(), "the agent's reply must still be stopped"
    assert [t["user_text"] for t in p.turns] == ["Okay. Thank you. Bye."]
    assert s.current_user_input == "Okay. Thank you. Bye."


async def test_a_short_no_over_the_agent_is_answered():
    # 6b9cd4c4: a real StartOfTurn (seq bumped), the agent started speaking again,
    # then the caller's "No" ended while that reply was playing.
    p = _Pipeline()
    s, reply = _agent_speaking(p, seq=10)
    await _deliver(p, s, [_Chunk("No", is_final=False), _Chunk("No."), _Chunk("")])
    assert reply.cancelled()
    assert [t["user_text"] for t in p.turns] == ["No."]


async def test_a_plain_backchannel_still_neither_stops_nor_answers():
    p = _Pipeline()
    s, reply = _agent_speaking(p)
    try:
        handler = TranscriptHandler(p)
        for chunk in (_Chunk("Okay.", is_final=False), _Chunk("Okay."), _Chunk("")):
            await handler.handle(s, chunk)
        await asyncio.sleep(0)
        assert not reply.done() and s.tts_active is True
        assert p.turns == []
    finally:
        reply.cancel()


async def test_the_words_reach_the_turn_even_if_the_stop_reports_nothing_cancelled():
    # The reply finished a moment before EndOfTurn (nothing left to cancel).
    p = _Pipeline()
    s, reply = _agent_speaking(p)
    reply.cancel()
    await asyncio.gather(reply, return_exceptions=True)
    await _deliver(p, s, [_Chunk("Okay. Bye."), _Chunk("")])
    assert [t["user_text"] for t in p.turns] == ["Okay. Bye."]


@pytest.fixture(autouse=True)
def _fast_cancel(monkeypatch):
    monkeypatch.setenv("BARGE_IN_CANCEL_WAIT_S", "0.5")
