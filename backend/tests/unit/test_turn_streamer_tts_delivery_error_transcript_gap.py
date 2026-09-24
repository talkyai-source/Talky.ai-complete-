"""Regression test for the now-CLOSED part of hangup-vs-inflight-reply, 12b
(round 2, review of 91b61694, 2026-09-24).

PRODUCTION EVIDENCE
--------------------
Call 6aaeb4dd, turn 17: TTS delivery failed partway through the agent's
reply ("Would you like us to call you tomorrow with the appointment
details?") — 13:10:55.31 "no gateway session", TtsDeliveryError. Round 1
fixed tts_playback.py's own bookkeeping (interrupted=True, kept out of
_spoken_sentences), but the FULL sentence — not just the ~0.65s that
actually played — still reached turn_streamer.py's persisted ``full_text``
and, via ``turn_runner.py:663``'s ``accumulate_turn`` call, the stored
transcript and conversation history.

ROOT CAUSE (confirmed by this test running the REAL turn_streamer.py, not a
mock of it): the persisted-``full_text`` substitution only fired when
``tts_was_interrupted and _barged()`` were BOTH true. A TtsDeliveryError sets
``tts_was_interrupted=True`` but there was no real barge-in event, so
``_barged()`` stayed False and ``full_text`` fell through to
``guardrails.clean_response(raw_response_text, ...)`` — the LLM's full raw
output, unfiltered by what was actually delivered.

FIX (this round): tts_playback.py's TtsDeliveryError clause now also sets
``session._tts_delivery_failed = True`` (reset to False each turn alongside
``session._spoken_sentences = []``). turn_streamer.py's full_text block adds
an ``elif tts_was_interrupted and session._tts_delivery_failed`` branch that
substitutes in only ``_spoken_sentences`` (empty string if none were
delivered) — turn_runner.py already treats an empty reply as nothing to
commit.
"""
from __future__ import annotations

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.turn_streamer import TurnStreamer
from app.domain.services.voice_pipeline.tts_playback import TtsPlayback
from app.infrastructure.telephony.asterisk_adapter import TtsDeliveryError

pytestmark = pytest.mark.asyncio


class _FakeLLMProvider:
    """Emits the whole reply in one token, matching turn 17's shape (a
    single sentence, no multi-sentence pipelining involved)."""

    def __init__(self, text: str):
        self._text = text

    def stream_chat_with_timeout(self, messages, *, system_prompt, temperature, max_tokens, **_kwargs):
        text = self._text

        async def _gen():
            yield text

        return _gen()


class _Chunk:
    def __init__(self, data: bytes):
        self.data = data


class _TtsProvider:
    name = "fake-tts"

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def stream_synthesize(self, text, **kwargs):
        chunks = self._chunks

        async def _gen():
            for c in chunks:
                yield _Chunk(c)

        return _gen()


class _GatewayFailsOnSecondChunk:
    """Delivers the first chunk, then the channel is gone — matches
    6aaeb4dd (some audio already sent — ~0.65s — before delivery failed)."""

    def __init__(self):
        self.sent: list[bytes] = []

    async def send_audio(self, call_id, raw):
        if self.sent:
            raise TtsDeliveryError(f"no gateway session for call_id={call_id[:12]}")
        self.sent.append(raw)

    async def clear_output_buffer(self, call_id):
        return {"ok": True}

    async def flush_tts_buffer(self, call_id):
        return None


class _GatewayFailsOnThirdChunk:
    """The first sentence's two chunks succeed; the second sentence's first
    chunk fails. Proves the fix returns only what was ACTUALLY delivered,
    not that any delivery failure collapses full_text to empty."""

    def __init__(self):
        self.sent: list[bytes] = []

    async def send_audio(self, call_id, raw):
        if len(self.sent) >= 2:
            raise TtsDeliveryError(f"no gateway session for call_id={call_id[:12]}")
        self.sent.append(raw)

    async def clear_output_buffer(self, call_id):
        return {"ok": True}

    async def flush_tts_buffer(self, call_id):
        return None


class _FakeLatencyTracker:
    def mark_llm_first_token(self, call_id): pass
    def mark_llm_end(self, call_id): pass
    def mark_tts_start(self, call_id): pass
    def mark_tts_first_chunk(self, *a, **k): pass
    def mark_response_start(self, *a, **k): pass
    def mark_audio_start(self, *a, **k): pass
    def mark_tts_end(self, *a, **k): pass
    def mark_completed(self, *a, **k): pass
    def mark_interrupted(self, *a, **k): pass


class _FakePipeline:
    """Minimal stand-in for VoicePipelineService — same shape
    test_turn_streamer_tts_overlap.py uses — except
    ``synthesize_and_send_audio`` here is the REAL ``TtsPlayback``, not a
    hand-rolled event log, so the TtsDeliveryError path under test is the
    actual production code, not a simulation of it."""

    def __init__(self, text: str, tts_chunks, gateway):
        self._barge_in_events: dict = {}
        self._barge_in_epoch: dict = {}
        self.llm_provider = _FakeLLMProvider(text)
        self.latency_tracker = _FakeLatencyTracker()
        self.tts_provider = _TtsProvider(tts_chunks)
        self.media_gateway = gateway
        self.tts_sample_rate = 16000
        self.stt_provider = None
        self.silent_turns: list[str] = []
        self._tts_playback = TtsPlayback(self)

    def _supports_llm_end_session_action(self, session):
        return False

    def _response_max_sentences_for_turn(self, session, text, has_custom_prompt):
        return None

    @staticmethod
    def _find_sentence_end(buf, allow_clause=False):
        return buf.find(".")

    def _record_silent_turn(self, call_id, reason):
        self.silent_turns.append(reason)

    async def synthesize_and_send_audio(self, session, sentence, websocket, track_latency=False):
        return await self._tts_playback.synthesize_and_send(
            session, sentence, websocket, track_latency=track_latency,
        )


def _make_session() -> CallSession:
    return CallSession(
        call_id="6aaeb4dd-0000-0000-0000-000000000017",
        campaign_id="camp-1",
        lead_id="lead-1",
        provider_call_id="prov-1",
        system_prompt="You are a helpful sales agent.",
        voice_id="voice-1",
        tenant_id="tenant-1",
        knowledge_mode="none",
        conversation_history=[
            Message(role=MessageRole.USER, content="Can I get a call back tomorrow?"),
        ],
    )


REPLY = "Would you like us to call you tomorrow with the appointment details."


async def test_undelivered_text_is_excluded_from_full_text_on_delivery_failure(monkeypatch):
    """The fix: a TtsDeliveryError with no real caller barge-in must not
    leave the undelivered line in the persisted ``full_text`` (what
    turn_runner.py:663 passes to accumulate_turn).

    tts_playback.py's TtsDeliveryError clause reports interrupted=True AND
    sets session._tts_delivery_failed (proven separately in
    test_tts_delivery_error_stops_turn.py); turn_streamer.py's new elif
    branch substitutes in only what actually reached _spoken_sentences.
    Nothing was delivered here (the only sentence failed mid-send), so
    full_text must come back empty — turn_runner.py's
    ``if response_text and response_text.strip():`` gate then commits
    nothing, closing 6aaeb4dd turn 17's transcript-leak gap.
    """
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    session = _make_session()
    gateway = _GatewayFailsOnSecondChunk()
    pipeline = _FakePipeline(REPLY, [b"\x01\x02" * 80, b"\x03\x04" * 80], gateway)
    streamer = TurnStreamer(pipeline)

    full_text, _llm_ms, _tts_ms = await streamer.stream(session, websocket=None)

    # The delivery failure IS visible in the delivered-sentences bookkeeping...
    assert session._spoken_sentences == [], (
        "the undelivered sentence must not appear in _spoken_sentences — "
        "this part of 12b was already fixed in round 1 (tts_playback.py's "
        "TtsDeliveryError handling)"
    )
    assert pipeline.silent_turns == [], (
        "some audio was sent before the failure, so this must not be "
        "recorded as a fully silent turn"
    )

    # ...and now full_text must be trimmed to match: nothing was actually
    # delivered, so there is nothing for turn_runner.py to persist.
    assert full_text == "", (
        f"expected the undelivered line to be excluded from full_text "
        f"(nothing was actually spoken); got full_text={full_text!r}"
    )
    assert REPLY.rstrip(".") not in full_text


TWO_SENTENCE_REPLY = (
    "Great, thanks for calling. Would you like us to call you tomorrow "
    "with the appointment details."
)


async def test_partial_delivery_keeps_only_what_was_actually_spoken(monkeypatch):
    """Multi-sentence reply where the FIRST sentence played fully and only
    the SECOND failed mid-delivery: the fix must keep the delivered
    sentence in full_text, not collapse the whole turn to "" — the brief
    says "return only what was actually delivered", not "always empty on
    any delivery failure"."""
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    session = _make_session()
    gateway = _GatewayFailsOnThirdChunk()
    pipeline = _FakePipeline(
        TWO_SENTENCE_REPLY, [b"\x01\x02" * 80, b"\x03\x04" * 80], gateway,
    )
    streamer = TurnStreamer(pipeline)

    full_text, _llm_ms, _tts_ms = await streamer.stream(session, websocket=None)

    # clean_response strips the "Great," filler opener — the point of this
    # test is that the DELIVERED sentence survives at all, not its wording.
    assert session._spoken_sentences == ["Thanks for calling."]
    assert full_text == "Thanks for calling."
    assert "tomorrow" not in full_text
