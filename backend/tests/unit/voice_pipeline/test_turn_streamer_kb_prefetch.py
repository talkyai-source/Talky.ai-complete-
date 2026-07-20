"""Case 3 prefetch-overlap investigation (Defect 3).

Root-cause finding: a prefetch launched at EagerEndOfTurn/TurnResumed time was
previously rejected as unsafe (stale-query race — see turn_streamer.py comment
at the knowledge_block resolution site). Re-investigating whether launching the
_knowledge_block_for_turn() fetch as an asyncio.create_task() INSIDE
TurnStreamer.stream() (race-free by construction, since `messages` is already
the final fixed transcript by the time stream() runs) and awaiting it later
would still be worth doing found: every step between the KB call and where
knowledge_block is consumed (build_turn_prompt()) is synchronous, pure Python
— no network/DB awaits — so overlapping would win nothing measurable against
an up to 500ms retrieval budget. The honest conclusion was a NO-OP: keep the
serial `await` in place.

This test PINS that current (unchanged) behavior: even with a slow KB fetch,
the LLM provider is invoked strictly AFTER the KB fetch resolves (no
overlap), and total elapsed time is at least the KB delay — i.e. there is no
hidden concurrency silently doing the wrong thing.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline import turn_streamer as ts_module
from app.domain.services.voice_pipeline.turn_streamer import TurnStreamer

pytestmark = pytest.mark.asyncio

_KB_DELAY_S = 0.05


class _FakeLatencyTracker:
    def mark_llm_first_token(self, call_id):
        pass

    def mark_llm_end(self, call_id):
        pass

    def mark_tts_start(self, call_id):
        pass


class _FakeLLMProvider:
    """Records the moment stream_chat_with_timeout is first invoked, then
    yields one short sentence."""

    def __init__(self, call_log: list):
        self._call_log = call_log
        self._model = "fake-model"
        self._primary = None
        self._secondary = None

    def stream_chat_with_timeout(self, messages, *, system_prompt, temperature, max_tokens):
        self._call_log.append(("llm_stream_start", time.monotonic()))

        async def _gen():
            yield "Absolutely, our standard rate is fifty dollars an hour."

        return _gen()


class _FakePipeline:
    """Minimal stand-in for VoicePipelineService — only the attributes/methods
    TurnStreamer.stream() actually reads."""

    def __init__(self, call_log: list):
        self._call_log = call_log
        self._barge_in_events = {}
        self._barge_in_epoch = {}
        self.llm_provider = _FakeLLMProvider(call_log)
        self.latency_tracker = _FakeLatencyTracker()

    def _supports_llm_end_session_action(self, session):
        return False

    def _response_max_sentences_for_turn(self, session, text, has_custom_prompt):
        return None

    @staticmethod
    def _find_sentence_end(buf, allow_clause=False):
        idx = buf.find(".")
        return idx

    async def synthesize_and_send_audio(self, session, sentence, websocket, track_latency=False):
        return False  # not interrupted


async def _slow_knowledge_block(session, messages):
    """Stand-in for _knowledge_block_for_turn: records start/resolve times so
    the test can assert ordering, and sleeps to simulate a cache-miss FTS
    round trip."""
    t0 = time.monotonic()
    await asyncio.sleep(_KB_DELAY_S)
    session._test_call_log.append(("kb_resolved", time.monotonic()))
    return "COMPANY KNOWLEDGE: rates are $50/hr."


def _make_session(call_log: list) -> CallSession:
    session = CallSession(
        call_id="call-1",
        campaign_id="camp-1",
        lead_id="lead-1",
        provider_call_id="prov-1",
        system_prompt="You are a helpful sales agent.",
        voice_id="voice-1",
        tenant_id="tenant-1",
        knowledge_mode="retrieve",
        conversation_history=[
            Message(role=MessageRole.USER, content="What are your rates?"),
        ],
    )
    session._test_call_log = call_log
    return session


async def test_kb_fetch_still_serial_no_overlap(monkeypatch):
    """Pins the honest-no-op decision: the LLM stream only starts AFTER the
    (slow) knowledge fetch resolves — there is no task launched earlier that
    would make this overlap silently."""
    # Disable the thinking-filler task — unrelated to this investigation and
    # its own cancellation adds timing noise to what we're measuring here.
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    call_log: list = []
    monkeypatch.setattr(ts_module, "_knowledge_block_for_turn", _slow_knowledge_block)

    session = _make_session(call_log)
    pipeline = _FakePipeline(call_log)
    streamer = TurnStreamer(pipeline)

    t_start = time.monotonic()
    full_text, llm_ms, tts_ms = await streamer.stream(session, websocket=None)
    t_end = time.monotonic()

    kb_resolved_at = next(t for name, t in call_log if name == "kb_resolved")
    llm_started_at = next(t for name, t in pipeline._call_log if name == "llm_stream_start")

    # Ordering: LLM stream creation happens no earlier than the KB fetch
    # resolving — i.e. still fully serial, exactly as documented.
    assert llm_started_at >= kb_resolved_at

    # No hidden overlap: total wall time is at least the KB delay (a
    # concurrent/prefetched design could in principle finish faster than
    # kb_delay + llm/tts time, but this pins that we do NOT do that).
    # 0.8 tolerance: Windows' ~15.6ms timer granularity lets asyncio.sleep()
    # wake fractionally early relative to time.monotonic, which made the
    # exact >= flaky under full-suite load. The ordering assertion above is
    # the real pin; this one only guards against a fully-instant path.
    assert (t_end - t_start) >= _KB_DELAY_S * 0.8

    assert "fifty dollars" in full_text
