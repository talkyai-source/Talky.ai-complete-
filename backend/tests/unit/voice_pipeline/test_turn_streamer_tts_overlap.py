"""Sentence-level TTS pipelining investigation (2026-08-06).

FINDING under investigation
---------------------------
``TurnStreamer.stream`` awaits ``synthesize_and_send_audio`` once per
sentence inside the token loop (turn_streamer.py, the ``session.tts_active
= True`` block). ``synthesize_and_send`` interleaves synthesis with
REAL-TIME-PACED playback (tts_playback.py pulls a provider chunk, then
awaits ``media_gateway.send_audio``, which sleeps until the gateway is no
more than ``TARGET_AHEAD_S = 0.200`` seconds ahead of the wire clock). So
sentence N+1's synthesis does not begin until sentence N has essentially
finished playing, and every sentence boundary pays a fresh generation
latency (~250 ms on Cartesia sonic-3) against only ~200 ms of pre-buffered
audio in the C++ gateway.

CONCLUSION: the overlap CANNOT be added inside turn_streamer.py alone.
Speculatively starting sentence N+1's synthesis as a guarded task is not
merely unhelpful, it is ACTIVELY DANGEROUS on the default provider —
``CartesiaTTSProvider.stream_synthesize`` holds a per-call ``asyncio.Lock``
for the WHOLE generation, and a second generation for the same call that
cannot get that lock within ``_WS_LOCK_ACQUIRE_TIMEOUT_S`` (2.0 s) force-
swaps the lock and sends ``{"context_id": <in-flight ctx>, "cancel": true}``
— cancelling the sentence the caller is CURRENTLY HEARING. Any sentence
longer than ~2 s of audio (i.e. most of them) would be truncated mid-word.
``DeepgramTTSProvider`` is worse still: its ``_synthesis_lock`` is a single
provider-wide lock shared by every concurrent CALL, not per call.

These tests PIN both facts so a future attempt starts from evidence:
  1. the serialization in turn_streamer (the finding itself), and
  2. the Cartesia blocker that makes the naive in-file fix unsafe,
plus the barge-in invariant any real fix must preserve (an unspoken
sentence must never land in ``session._spoken_sentences``).

If/when synthesis is genuinely decoupled from playback (see the report:
it needs a bounded audio queue in tts_playback.py AND a provider layer
that permits two concurrent contexts per call), test 1 is the one that
must be inverted.
"""
from __future__ import annotations

import asyncio
import base64
import json

import aiohttp
import numpy as np
import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.turn_streamer import TurnStreamer
from app.infrastructure.providers.provider_concurrency import reset_guards_for_tests
from app.infrastructure.tts.cartesia import CartesiaTTSProvider

pytestmark = pytest.mark.asyncio

# Simulated real-time playback duration of one sentence. Kept tiny; the
# assertions are on EVENT ORDER, never on wall-clock durations, so this is
# not a race the way a sleep-and-hope test would be.
_PLAYBACK_S = 0.05


# ---------------------------------------------------------------------------
# 1 + 2. TurnStreamer: synthesis is serialized behind playback
# ---------------------------------------------------------------------------


class _FakeLatencyTracker:
    def mark_llm_first_token(self, call_id):
        pass

    def mark_llm_end(self, call_id):
        pass

    def mark_tts_start(self, call_id):
        pass


class _FakeLLMProvider:
    """Emits BOTH sentences in a single token, so the full reply is already
    buffered before the first sentence starts playing. Any overlap that
    existed would therefore be visible — the streamer is not waiting on the
    LLM for sentence 2, only on sentence 1's playback."""

    def __init__(self, text: str):
        self._text = text
        self._model = "fake-model"
        self._primary = None
        self._secondary = None

    def stream_chat_with_timeout(self, messages, *, system_prompt, temperature, max_tokens):
        text = self._text

        async def _gen():
            yield text

        return _gen()


class _FakePipeline:
    """Minimal stand-in for VoicePipelineService: only what stream() reads.

    ``synthesize_and_send_audio`` mimics the real one's shape — synthesis and
    real-time playback inside ONE await — and records an ordered event log.
    """

    def __init__(self, text: str, *, interrupt_on: int | None = None):
        self.events: list[tuple[str, str]] = []
        self._barge_in_events: dict = {}
        self._barge_in_epoch: dict = {}
        self.llm_provider = _FakeLLMProvider(text)
        self.latency_tracker = _FakeLatencyTracker()
        self._calls = 0
        # 1-based index of the sentence whose playback reports "interrupted".
        self._interrupt_on = interrupt_on

    def _supports_llm_end_session_action(self, session):
        return False

    def _response_max_sentences_for_turn(self, session, text, has_custom_prompt):
        return None

    @staticmethod
    def _find_sentence_end(buf, allow_clause=False):
        return buf.find(".")

    async def synthesize_and_send_audio(self, session, sentence, websocket, track_latency=False):
        self._calls += 1
        n = self._calls
        self.events.append(("synth_start", sentence))
        # Provider generation latency (first chunk) — nothing is on the wire
        # yet during this window; this is the gap the overlap would hide.
        await asyncio.sleep(_PLAYBACK_S / 5)
        self.events.append(("first_audio", sentence))
        # Real-time-paced playout of the rest of the utterance.
        await asyncio.sleep(_PLAYBACK_S)
        self.events.append(("playback_end", sentence))
        return self._interrupt_on == n


def _make_session() -> CallSession:
    return CallSession(
        call_id="call-overlap-1",
        campaign_id="camp-1",
        lead_id="lead-1",
        provider_call_id="prov-1",
        system_prompt="You are a helpful sales agent.",
        voice_id="voice-1",
        tenant_id="tenant-1",
        knowledge_mode="none",
        conversation_history=[
            Message(role=MessageRole.USER, content="Tell me about your service."),
        ],
    )


_TWO_SENTENCES = (
    "Absolutely, we cover the whole of the London area. "
    "Our standard rate is fifty pounds an hour."
)


async def test_sentence_synthesis_is_serialized_behind_playback(monkeypatch):
    """THE FINDING. Sentence 2's synthesis starts only AFTER sentence 1's
    playback has finished — no pipelining, so each boundary pays a full
    generation latency with no audio being produced."""
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")

    session = _make_session()
    pipeline = _FakePipeline(_TWO_SENTENCES)
    streamer = TurnStreamer(pipeline)

    await streamer.stream(session, websocket=None)

    assert pipeline._calls == 2, "expected exactly two TTS utterances"
    phases = [name for name, _ in pipeline.events]
    assert phases == [
        "synth_start", "first_audio", "playback_end",     # sentence 1
        "synth_start", "first_audio", "playback_end",     # sentence 2
    ], (
        "strictly serial: sentence 2's synthesis must not have started while "
        f"sentence 1 was still playing. Got {pipeline.events!r}"
    )

    # Both were really spoken, so both are committed to what the agent
    # believes it said.
    assert len(session._spoken_sentences) == 2


async def test_single_sentence_turn_makes_exactly_one_tts_call(monkeypatch):
    """Baseline for the single-sentence path — the one any pipelining change
    must leave byte-for-byte identical (no speculative second synthesis, one
    spoken sentence recorded)."""
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")

    session = _make_session()
    pipeline = _FakePipeline("We cover the whole of the London area.")
    streamer = TurnStreamer(pipeline)

    full_text, _llm_ms, _tts_ms = await streamer.stream(session, websocket=None)

    assert pipeline._calls == 1
    assert [n for n, _ in pipeline.events] == [
        "synth_start", "first_audio", "playback_end",
    ]
    assert len(session._spoken_sentences) == 1
    assert "London" in full_text


async def test_interrupted_sentence_stops_the_turn_and_is_not_recorded(monkeypatch):
    """INVARIANT any pipelining design must preserve: when sentence 1 reports
    interrupted, sentence 2 is never synthesized, and NEITHER sentence lands
    in ``_spoken_sentences`` (the interrupted one was not fully heard, the
    second was never spoken at all). ``_spoken_sentences`` drives what the
    agent believes it said — a prefetched-but-unspoken sentence appearing
    there is the specific corruption to guard against."""
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")

    session = _make_session()
    pipeline = _FakePipeline(_TWO_SENTENCES, interrupt_on=1)
    streamer = TurnStreamer(pipeline)

    await streamer.stream(session, websocket=None)

    assert pipeline._calls == 1, "sentence 2 must never be synthesized"
    assert session._spoken_sentences == [], (
        "an interrupted (or never-spoken) sentence must not be recorded"
    )


# ---------------------------------------------------------------------------
# 3. The blocker: concurrent synthesis on one call cancels the LIVE sentence
# ---------------------------------------------------------------------------


def _audio_frame(context_id: str, value: int) -> str:
    pcm = np.array([value] * 4, dtype=np.int16).tobytes()
    return json.dumps({
        "context_id": context_id,
        "data": base64.b64encode(pcm).decode(),
    })


class _FakeWSMessage:
    def __init__(self, msg_type, data=None):
        self.type = msg_type
        self.data = data


class _EchoFakeWS:
    """Fake persistent Cartesia WS: every generation payload it receives
    queues one audio frame for that payload's context_id (and then nothing —
    the generation stays open, exactly like a sentence still being spoken)."""

    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False
        self._pending: list[_FakeWSMessage] = []
        self._has_frame = asyncio.Event()

    async def send_str(self, s):
        msg = json.loads(s)
        self.sent.append(msg)
        if msg.get("cancel"):
            return
        ctx = msg.get("context_id")
        if ctx:
            self._pending.append(
                _FakeWSMessage(aiohttp.WSMsgType.TEXT, _audio_frame(ctx, 7))
            )
            self._has_frame.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        while not self._pending:
            self._has_frame.clear()
            await self._has_frame.wait()
        return self._pending.pop(0)

    async def close(self):
        self.closed = True


@pytest.fixture
def _clean_guards():
    reset_guards_for_tests()
    yield
    reset_guards_for_tests()


async def test_concurrent_same_call_synthesis_cancels_the_playing_sentence(
    monkeypatch, _clean_guards
):
    """WHY THE NAIVE PREFETCH IS UNSAFE.

    Generation A (the sentence currently being spoken) holds the per-call WS
    lock for its whole lifetime — and because playback is real-time paced,
    "its whole lifetime" is the utterance's audible duration. A prefetch of
    sentence B on the same call blocks on that lock and, after
    ``_WS_LOCK_ACQUIRE_TIMEOUT_S``, force-swaps it and sends a Cartesia
    ``cancel`` for A's context — i.e. it cuts off the audio the caller is
    listening to. Under the shipped 2.0 s timeout that fires on any sentence
    longer than about two seconds of speech.
    """
    import app.infrastructure.tts.cartesia as cartesia_mod

    # Shrink the shipped 2.0s timeout so the test runs fast; the mechanism
    # under test is identical.
    monkeypatch.setattr(cartesia_mod, "_WS_LOCK_ACQUIRE_TIMEOUT_S", 0.05)

    counter = {"n": 0}

    def _seq_urandom(n: int) -> bytes:
        counter["n"] += 1
        return bytes([counter["n"]]) * n

    monkeypatch.setattr(cartesia_mod.os, "urandom", _seq_urandom)

    provider = CartesiaTTSProvider()
    provider._session = object()  # unused: the WS is pre-seeded below
    call_id = "call-live"
    ws = _EchoFakeWS()
    provider._call_ws[call_id] = ws

    # --- Generation A: the sentence the caller is hearing right now. ---
    gen_a = provider.stream_synthesize(
        "This is the sentence the caller is currently hearing.",
        "voice-1", sample_rate=24000, call_id=call_id,
    )
    first_a = await gen_a.__anext__()
    assert first_a is not None
    ctx_a = provider._call_active_context[call_id]
    # A is now suspended at its yield, still holding the call's lock —
    # precisely the state a real, paced playout leaves it in.

    # --- Generation B: what a speculative prefetch of sentence N+1 does. ---
    gen_b = provider.stream_synthesize(
        "This is the prefetched next sentence.",
        "voice-1", sample_rate=24000, call_id=call_id,
    )
    first_b = await asyncio.wait_for(gen_b.__anext__(), timeout=2.0)
    assert first_b is not None, "the prefetch did get through — by force"

    cancels = [m for m in ws.sent if m.get("cancel") is True]
    assert any(m["context_id"] == ctx_a for m in cancels), (
        "the prefetch cancelled the context of the sentence that was still "
        f"being spoken (ctx_a={ctx_a}); sent frames: {ws.sent!r}"
    )

    await gen_b.aclose()
    await gen_a.aclose()


async def test_deepgram_synthesis_lock_is_provider_wide_not_per_call():
    """Second blocker, recorded for the same reason: Deepgram TTS serializes
    every synthesis in the PROCESS behind one ``_synthesis_lock`` guarding a
    single warm WebSocket — there is no per-call lock to contend on, so a
    speculative prefetch would queue behind (and delay) other live calls'
    sentences, not just its own."""
    from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider

    provider = DeepgramTTSProvider()
    # One lock and one warm socket per provider instance (a process singleton
    # via the TTS factory) — both are call-agnostic.
    assert not hasattr(provider, "_call_ws_locks")
    assert "_synthesis_lock" in vars(provider)
    assert "_warm_ws" in vars(provider)
