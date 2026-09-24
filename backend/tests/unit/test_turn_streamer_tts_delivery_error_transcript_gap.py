"""Reproduction of the STILL-OPEN part of hangup-vs-inflight-reply, 12b
(review of 91b61694, 2026-09-24).

PRODUCTION EVIDENCE
--------------------
Call 6aaeb4dd, turn 17: TTS delivery failed partway through the agent's
reply ("Would you like us to call you tomorrow with the appointment
details?") — 13:10:55.31 "no gateway session", TtsDeliveryError. Despite
tts_playback.py's TtsDeliveryError handling (interrupted=True, this fix's
fence), the FULL sentence — not just the ~0.65s that actually played — is
still what gets persisted as ``assistant_response`` via
``turn_runner.py:663``'s ``accumulate_turn`` call.

ROOT CAUSE (confirmed by this test running the REAL turn_streamer.py, not a
mock of it): ``turn_streamer.py:1147`` only substitutes the delivered
``session._spoken_sentences`` into the persisted ``full_text`` when
``tts_was_interrupted and _barged()`` are BOTH true. A TtsDeliveryError sets
``tts_was_interrupted=True`` but there was no real barge-in event, so
``_barged()`` stays False and ``full_text`` falls through to
``guardrails.clean_response(raw_response_text, ...)`` — the LLM's full raw
output, unfiltered by what was actually delivered.

FIX NEEDED (outside this fence — turn_streamer.py is not one of the 4 fenced
files): apply the same substitution when `tts_was_interrupted` is True
because of a delivery failure, not only a caller barge-in. One shape: read a
flag tts_playback.py sets on the session (e.g. a delivery-failure marker)
alongside `interrupted=True`, and OR it into the turn_streamer.py:1147
condition, or have turn_runner.py tag the row `metadata={"delivered": False}`
instead of dropping the text. Either change is out of fence; this test
exists so whoever makes it does not need to rediscover the mechanism, and so
this file starts FAILING (not silently) the day someone "fixes" 12b without
also fixing this.
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


async def test_undelivered_text_still_reaches_full_text_despite_interrupted_true(monkeypatch):
    """DOCUMENTS THE GAP (does not assert the fix — the fix is out of fence).

    Even though tts_playback.py correctly reports interrupted=True for this
    TtsDeliveryError (proven separately in
    test_tts_delivery_error_stops_turn.py), turn_streamer.py's ``full_text``
    still equals the LLM's full raw output because ``_barged()`` is False —
    there was no real caller barge-in, just a delivery failure. This is
    exactly what turn_runner.py then commits via accumulate_turn on
    6aaeb4dd, turn 17.
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
        "this part of 12b IS fixed (tts_playback.py's TtsDeliveryError "
        "handling)"
    )
    assert pipeline.silent_turns == [], (
        "some audio was sent before the failure, so this must not be "
        "recorded as a fully silent turn"
    )

    # ...but the text that gets PERSISTED (this is what turn_runner.py:663
    # passes to accumulate_turn) still contains the full undelivered line.
    # THIS IS THE STILL-OPEN GAP. If this assertion ever fails, turn_streamer
    # .py has started honouring a delivery-failure signal the way it already
    # honours a real barge-in — update this test (and the tts_playback.py /
    # test_tts_delivery_error_stops_turn.py comments it's cross-referenced
    # from) to match, do not just delete the assertion.
    assert REPLY.rstrip(".") in full_text, (
        f"expected the known gap (undelivered text still reaches full_text) "
        f"to still be present; got full_text={full_text!r}. If turn_streamer"
        f".py now excludes undelivered text here, 12b's transcript gap is "
        f"actually closed — update the comments that say otherwise."
    )
