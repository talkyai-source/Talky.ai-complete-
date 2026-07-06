"""Tests for the realtime (gpt-realtime-2) quality pass.

Covers the five fixes:
  FIX 1 — knowledge: the bridge's knowledge lookup passes tenant_id=None (not
          "") when the tenant is unknown, so acquire_with_tenant bypasses RLS
          instead of crashing on _validate_uuid(""); retrieve_knowledge is the
          callable used.
  FIX 2 — transcript: the model pump accumulates ONLY finalised agent + caller
          transcripts (role-tagged, in order) into a TranscriptService; deltas
          do not double-count.
  FIX 3 — persona/instructions: the built string carries the new AI-disclosure
          and lookup-filler direction and NOT the old "never as filler".
  FIX 5 — session controls: speed/temperature/max_output_tokens are omitted by
          default and included (clamped) only when configured.
"""
from __future__ import annotations

import asyncio

import pytest

from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge
from app.infrastructure.realtime.openai_realtime import (
    OpenAIRealtimeSession,
    RealtimeEvent,
)
from app.services.scripts.realtime_instructions import (
    RealtimePersona,
    build_realtime_instructions,
)
from app.domain.services.transcript_service import TranscriptService


# ---------------------------------------------------------------------------
# FIX 3 — instructions
# ---------------------------------------------------------------------------

def test_instructions_ai_disclosure_matches_compliance_floor():
    text = build_realtime_instructions(
        RealtimePersona(agent_name="Sam", company_name="Acme")
    )
    # Aligns with the platform's honesty floor (guardrails.py Rule 1): be honest
    # about being AI, never claim to be human, and disclose when asked. The old
    # "Do NOT volunteer that you're an AI" concealment framing must be gone.
    assert "Do NOT volunteer that you're an AI" not in text
    assert "Be honest about what you are" in text
    assert "never claim or imply you're human" in text
    # Still names it's an AI when the caller asks.
    assert "I'm an AI assistant" in text


def test_instructions_have_lookup_filler_and_drop_never_as_filler():
    text = build_realtime_instructions(RealtimePersona())
    # The OLD, wrong guidance must be gone.
    assert "never as filler" not in text
    # The NEW lookup-hold direction must be present.
    assert "let me check that for you" in text
    assert "NEVER sit in dead silence" in text


def test_instructions_have_opening_and_backchannels():
    text = build_realtime_instructions(
        RealtimePersona(agent_name="Sam", company_name="Acme")
    )
    assert "HOW YOU OPEN" in text
    assert "Sam from Acme" in text
    # Human backchannels / emotional signals.
    assert "mm-hmm" in text


def test_instructions_dependency_free():
    # The composer must IMPORT nothing from the cascaded prompt machinery
    # (the docstring may name it — only real import statements matter).
    import app.services.scripts.realtime_instructions as mod
    with open(mod.__file__, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "app.services.scripts.prompts" not in stripped
                assert "tts" not in stripped.lower()


# ---------------------------------------------------------------------------
# FIX 1 — knowledge lookup tenant semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_knowledge_passes_none_tenant_not_empty(monkeypatch):
    captured = {}

    async def _fake_retrieve(pool, *, tenant_id, campaign_id, query, k):
        captured["tenant_id"] = tenant_id
        captured["campaign_id"] = campaign_id
        return [{"heading": "Hours", "voice_answer": "9 to 5"}]

    import app.services.scripts.knowledge.retrieval as retr
    monkeypatch.setattr(retr, "retrieve_knowledge", _fake_retrieve)

    bridge = RealtimeBridge(
        call_id="c1",
        realtime_session=object(),
        media_gateway=object(),
        internal_sample_rate=8000,
        knowledge_pool=object(),   # non-None so the lookup proceeds
        tenant_id=None,            # unknown tenant
        campaign_id="camp-1",
    )
    out = await bridge._lookup_knowledge("what are your hours")
    assert captured["tenant_id"] is None      # NOT "" (would crash _validate_uuid)
    assert captured["campaign_id"] == "camp-1"
    assert "9 to 5" in out


@pytest.mark.asyncio
async def test_lookup_knowledge_no_pool_returns_graceful():
    bridge = RealtimeBridge(
        call_id="c1",
        realtime_session=object(),
        media_gateway=object(),
        internal_sample_rate=8000,
        knowledge_pool=None,
        campaign_id="camp-1",
    )
    out = await bridge._lookup_knowledge("hours")
    assert "No company information" in out


# ---------------------------------------------------------------------------
# FIX 2 — transcript accumulation from finalised events only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_pump_accumulates_final_transcripts_in_order():
    ts = TranscriptService()
    call_id = "transcript-call-1"
    TranscriptService.clear_all_buffers()

    async def _events():
        # Agent speaks (deltas then a final), caller replies (delta then final).
        yield RealtimeEvent(kind="agent_transcript", text="Hi ")
        yield RealtimeEvent(kind="agent_transcript", text="there", is_final=True)
        yield RealtimeEvent(kind="caller_transcript", text="hel")
        yield RealtimeEvent(kind="caller_transcript", text="Hello there", is_final=True)

    class _RT:
        def events(self):
            return _events()

    bridge = RealtimeBridge(
        call_id=call_id,
        realtime_session=_RT(),
        media_gateway=object(),
        internal_sample_rate=8000,
        transcript_service=ts,
        talklee_call_id="tk-1",
    )
    await asyncio.wait_for(bridge._pump_model_events(), timeout=1.0)

    turns = ts.get_transcript_json(call_id)
    # Exactly TWO turns — the deltas must NOT double-count.
    assert len(turns) == 2
    assert turns[0]["role"] == "assistant"
    assert turns[0]["content"] == "there"
    assert turns[1]["role"] == "user"
    assert turns[1]["content"] == "Hello there"
    # Ordered turn indices.
    assert turns[0]["turn_index"] == 0
    assert turns[1]["turn_index"] == 1
    TranscriptService.clear_all_buffers()


def test_record_turn_fail_soft_without_service():
    bridge = RealtimeBridge(
        call_id="c1",
        realtime_session=object(),
        media_gateway=object(),
        internal_sample_rate=8000,
        transcript_service=None,
    )
    # Must not raise when no transcript service is wired.
    bridge._record_turn("assistant", "hello")


# ---------------------------------------------------------------------------
# FIX 5 — optional session controls
# ---------------------------------------------------------------------------

def test_session_update_omits_optional_controls_by_default():
    s = OpenAIRealtimeSession(api_key="sk")._build_session_update()["session"]
    assert "speed" not in s["audio"]["output"]
    assert "temperature" not in s
    assert "max_output_tokens" not in s


def test_session_update_includes_and_clamps_controls_when_set():
    s = OpenAIRealtimeSession(
        api_key="sk",
        settings={"speed": 3.0, "temperature": 0.8, "max_output_tokens": 512},
    )._build_session_update()["session"]
    # Speed clamped to the documented 0.25–1.5 window.
    assert s["audio"]["output"]["speed"] == 1.5
    assert s["temperature"] == 0.8
    assert s["max_output_tokens"] == 512
