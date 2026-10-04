"""Tests for the realtime (gpt-realtime-2) quality pass.

Covers the five fixes:
  FIX 1 — knowledge: the bridge's knowledge lookup FAILS CLOSED when the tenant
          is unknown — it does NOT call retrieve_knowledge (passing tenant_id=
          None would trip acquire_with_tenant's RLS bypass → cross-tenant read).
          With a valid tenant it renders SOURCE-FIRST (the matched node content,
          not the top-of-node voice_answer summary) and never bumps hit_count.
  FIX 2 — transcript: the model pump accumulates ONLY finalised agent + caller
          transcripts (role-tagged, in order) into a TranscriptService; deltas
          do not double-count.
  FIX 3 — persona/instructions: the built string carries the new AI-disclosure
          and conditional lookup updates without forced filler.
  FIX 5 — session controls: speed/temperature/max_output_tokens are omitted by
          default and included (clamped) only when configured.
"""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio

import pytest

from app.realtime.bridge import (
    _NO_KB_INFO,
    RealtimeBridge,
)
from app.realtime.openai import (
    OpenAIRealtimeSession,
    RealtimeEvent,
)
from app.realtime.prompts import (
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


def test_lookup_updates_are_conditional_and_not_forced_filler():
    text = build_realtime_instructions(RealtimePersona())
    assert "NEVER sit in dead silence" not in text
    assert "let real emotion through" not in text
    # The old stall template ("Give a brief update ... 'I'll check the
    # details.'") was spoken back as "Let me think about the best next
    # question here." (realtime test 1ddf8844, 2026-09-30). At most "One
    # moment." before a lookup, and never narrated thinking.
    assert "I'll check the details." not in text
    assert 'say at most "One moment."' in text
    assert "Never say that you are thinking" in text


def test_instructions_keep_opening_and_short_natural_turns():
    text = build_realtime_instructions(RealtimePersona(agent_name="Sam", company_name="Acme"))
    assert "HOW YOU OPEN" in text
    assert "Sam from Acme" in text
    assert "one or two short sentences" in text
    assert "without scripted filler or forced laughter" in text


def test_instructions_dependency_free():
    # The composer must IMPORT nothing from the cascaded prompt machinery
    # (the docstring may name it — only real import statements matter).
    import app.realtime.prompts as mod
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
async def test_lookup_knowledge_fails_closed_on_missing_tenant(monkeypatch):
    """SECURITY (fail closed): with NO tenant, the bridge must NOT call
    retrieve_knowledge at all — passing tenant_id=None trips acquire_with_tenant
    into RLS bypass (a cross-tenant read). It returns a graceful 'no info'
    string instead."""
    called = {"n": 0}

    async def _fake_retrieve(pool, *, tenant_id, campaign_id, query, k, bump_hits=True, **_kw):
        called["n"] += 1
        return [{"heading": "Hours", "content": "9 to 5"}]

    import app.services.scripts.knowledge.retrieval as retr
    monkeypatch.setattr(retr, "retrieve_knowledge", _fake_retrieve)

    bridge = RealtimeBridge(
        call_id="c1",
        realtime_session=object(),
        media_gateway=object(),
        internal_sample_rate=8000,
        knowledge_pool=object(),   # non-None so we get past the pool guard
        tenant_id=None,            # unknown tenant → must fail closed
        campaign_id="camp-1",
    )
    out = await bridge._lookup_knowledge("what are your hours")
    assert called["n"] == 0                     # retrieve NEVER called (no bypass)
    assert out["status"] == "unavailable"
    assert "cannot confirm" in out["text"].lower()


@pytest.mark.asyncio
async def test_lookup_knowledge_valid_tenant_returns_source_first(monkeypatch):
    """With a valid tenant, the lookup surfaces the SOURCE content fact (not the
    top-of-node voice_answer summary) and never bumps hit_count."""
    captured = {}

    async def _fake_retrieve(pool, *, tenant_id, campaign_id, query, k, bump_hits=True, raise_on_error=False):
        captured["tenant_id"] = tenant_id
        captured["campaign_id"] = campaign_id
        captured["bump_hits"] = bump_hits
        # voice_answer summarises the TOP; the caller's fact lives in content.
        return [{
            "heading": "Coverage",
            "voice_answer": "We serve many areas.",
            "summary": None,
            "content": "We cover Texas, and we also cover Ohio and Florida.",
            "coverage": 1.0,
        }]

    import app.services.scripts.knowledge.retrieval as retr
    monkeypatch.setattr(retr, "retrieve_knowledge", _fake_retrieve)

    tid = "11111111-1111-1111-1111-111111111111"
    bridge = RealtimeBridge(
        call_id="c1",
        realtime_session=object(),
        media_gateway=object(),
        internal_sample_rate=8000,
        knowledge_pool=object(),
        tenant_id=tid,
        campaign_id="camp-1",
    )
    out = await bridge._lookup_knowledge("do you cover florida")
    assert captured["tenant_id"] == tid         # the validated tenant, no bypass
    assert captured["campaign_id"] == "camp-1"
    assert captured["bump_hits"] is False       # voice hot path: no hit_count write
    assert out["status"] == "matched"
    assert "Florida" in out["text"]             # the SOURCE fact, not "many areas"


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
    # No pool and no pinned snapshot => the no-info sentinel: graceful, and
    # crucially nothing for the model to invent from. Compared against the
    # constant so a reworded sentinel can never silently drift this test.
    assert out["status"] == "unavailable"
    assert out["sources"] == []


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
        yield RealtimeEvent(kind="response_candidate", text="there", audio=b"\xff" * 160)
        await asyncio.sleep(0.02)
        yield RealtimeEvent(kind="caller_transcript", text="hel")
        yield RealtimeEvent(kind="caller_transcript", text="Hello there", is_final=True)

    class _RT:
        def events(self):
            return _events()

    bridge = RealtimeBridge(
        call_id=call_id,
        realtime_session=_RT(),
        media_gateway=SimpleNamespace(send_audio=AsyncMock()),
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

def test_session_update_uses_the_same_defaults_displayed_in_ai_options():
    s = OpenAIRealtimeSession(api_key="sk")._build_session_update()["session"]
    assert s["audio"]["output"]["speed"] == 1.0
    assert "temperature" not in s
    assert s["max_output_tokens"] == 1024
    assert s["audio"]["input"]["noise_reduction"] == {"type": "near_field"}


def test_session_update_validates_controls_and_removes_legacy_temperature():
    s = OpenAIRealtimeSession(
        api_key="sk",
        settings={"speed": 1.5, "temperature": 0.8, "max_output_tokens": 512},
    )._build_session_update()["session"]
    assert s["audio"]["output"]["speed"] == 1.5
    assert "temperature" not in s
    assert s["max_output_tokens"] == 512
    with pytest.raises(ValueError):
        OpenAIRealtimeSession(api_key="sk", settings={"speed": 3.0})


# ---------------------------------------------------------------------------
# TURN-DETECTION builder — string / server_vad dict / default
# ---------------------------------------------------------------------------

def _td(settings):
    return OpenAIRealtimeSession(
        api_key="sk", settings=settings
    )._build_session_update()["session"]["audio"]["input"]["turn_detection"]


def test_turn_detection_default_is_semantic_vad_medium():
    # New gentler telephony default: semantic_vad eagerness "medium" (was "high").
    assert _td(None) == {"type": "semantic_vad", "eagerness": "medium"}


def test_turn_detection_bare_string_is_semantic_vad_eagerness():
    # A bare string stays eagerness shorthand for semantic_vad (back-compat).
    assert _td({"turn_detection": "low"}) == {
        "type": "semantic_vad", "eagerness": "low"
    }


def test_turn_detection_server_vad_dict_passes_through():
    # A dict with an explicit type=server_vad is respected and its params pass
    # straight through (the noisy-telephony override the research recommends).
    sv = {
        "type": "server_vad",
        "threshold": 0.6,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 700,
    }
    td = _td({"turn_detection": sv})
    assert td["type"] == "server_vad"
    assert td["threshold"] == 0.6
    assert td["prefix_padding_ms"] == 300
    assert td["silence_duration_ms"] == 700


def test_turn_detection_dict_without_type_defaults_semantic():
    # A dict WITHOUT "type" defaults to semantic_vad (back-compat with the
    # eagerness-only object the AI-Options frontend may send).
    td = _td({"turn_detection": {"eagerness": "high"}})
    assert td == {"type": "semantic_vad", "eagerness": "high"}


# ---------------------------------------------------------------------------
# REASONING effort — default "low", omitted when falsy, dropped on retry
# ---------------------------------------------------------------------------

def test_reasoning_effort_defaults_to_low():
    s = OpenAIRealtimeSession(api_key="sk")._build_session_update()["session"]
    # Wire shape is a nested object: session.reasoning = {"effort": …}.
    assert s["reasoning"] == {"effort": "low"}


def test_reasoning_effort_overridable():
    s = OpenAIRealtimeSession(
        api_key="sk", settings={"reasoning_effort": "medium"},
    )._build_session_update()["session"]
    assert s["reasoning"] == {"effort": "medium"}


def test_reasoning_effort_omitted_when_falsy():
    for val in (None, "", "none", "None", False):
        s = OpenAIRealtimeSession(
            api_key="sk", settings={"reasoning_effort": val},
        )._build_session_update()["session"]
        assert "reasoning" not in s, f"reasoning should be omitted for {val!r}"


def test_reasoning_dropped_on_retry_payload():
    # The fail-soft retry builds the SAME payload minus the reasoning field.
    sess = OpenAIRealtimeSession(api_key="sk")
    full = sess._build_session_update(include_reasoning=True)["session"]
    retry = sess._build_session_update(include_reasoning=False)["session"]
    assert "reasoning" in full
    assert "reasoning" not in retry
    # Everything else is unchanged.
    assert retry["audio"] == full["audio"]
    assert retry["instructions"] == full["instructions"]


# ---------------------------------------------------------------------------
# FAIL-SOFT — connect() retries session.update ONCE without the reasoning field
# ---------------------------------------------------------------------------

class _ScriptedWS:
    """Minimal fake Realtime websocket for the handshake.

    Seeds session.created; then answers each session.update we send: if the
    payload carries a `reasoning` field it replies with an `error` (simulating a
    server that rejects the unknown field), otherwise with `session.updated`.
    Once the handshake finishes it raises StopAsyncIteration so the recv loop
    exits cleanly (no hang).
    """

    def __init__(self):
        import collections
        self._inbox = collections.deque([
            json.dumps({"type": "session.created"})
        ])
        self.sent = []
        self.closed = False

    async def send(self, data):
        payload = json.loads(data)
        self.sent.append(payload)
        has_reasoning = "reasoning" in payload.get("session", {})
        if has_reasoning:
            self._inbox.append(json.dumps(
                {"type": "error", "error": {"code": "unknown_parameter", "param": "session.reasoning", "message": "unknown parameter: reasoning"}}
            ))
        else:
            self._inbox.append(json.dumps({"type": "session.updated"}))

    async def recv(self):
        if self._inbox:
            return self._inbox.popleft()
        raise _ws_module.exceptions.ConnectionClosed(None, None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._inbox:
            return self._inbox.popleft()
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


import json  # noqa: E402  (used by _ScriptedWS above)
import websockets as _ws_module  # noqa: E402


@pytest.mark.asyncio
async def test_connect_retries_without_reasoning_on_rejection(monkeypatch):
    fake = _ScriptedWS()

    async def _fake_connect(*a, **k):
        return fake

    import app.realtime.openai as rt_mod
    monkeypatch.setattr(rt_mod.websockets, "connect", _fake_connect)

    sess = OpenAIRealtimeSession(api_key="sk", call_id="retry-test")
    ok = await sess.connect()
    assert ok is True
    # Two session.updates were sent: the first WITH reasoning (rejected), the
    # second WITHOUT it (accepted) — the fail-soft retry.
    updates = [m for m in fake.sent if m.get("type") == "session.update"]
    assert len(updates) == 2
    assert "reasoning" in updates[0]["session"]
    assert "reasoning" not in updates[1]["session"]
    await sess.close()


@pytest.mark.asyncio
async def test_connect_no_retry_when_first_update_accepted(monkeypatch):
    # With reasoning disabled, the first (reasoning-free) update is accepted and
    # there is NO second attempt.
    fake = _ScriptedWS()

    async def _fake_connect(*a, **k):
        return fake

    import app.realtime.openai as rt_mod
    monkeypatch.setattr(rt_mod.websockets, "connect", _fake_connect)

    sess = OpenAIRealtimeSession(
        api_key="sk", call_id="noretry", settings={"reasoning_effort": "none"},
    )
    ok = await sess.connect()
    assert ok is True
    updates = [m for m in fake.sent if m.get("type") == "session.update"]
    assert len(updates) == 1
    assert "reasoning" not in updates[0]["session"]
    await sess.close()


# ---------------------------------------------------------------------------
# TASK 3 — independent instructions preserve safety and explicit retrieval policy
# ---------------------------------------------------------------------------

def test_instructions_preserve_honesty_and_protected_data_rules():
    text = build_realtime_instructions(RealtimePersona())
    # Honesty remains explicit while the Realtime wording is independently maintained.
    assert "Be honest about what you are" in text
    assert "never claim or imply you're human" in text
    assert "I'm an AI assistant" in text
    for protected in ("payment-card", "social-security", "one-time passcodes"):
        assert protected in text
    # No concealment framing may creep back in.
    assert "Do NOT volunteer" not in text
    assert "don't volunteer" not in text.lower()


def test_knowledge_tool_and_prompt_require_evidence_not_model_confidence():
    from app.realtime.openai import knowledge_lookup_tool
    text = build_realtime_instructions(RealtimePersona())
    description = knowledge_lookup_tool()["description"]
    for fact in ("prices", "policies", "eligibility", "availability", "offers"):
        assert fact in text and fact in description
    assert "not certain" not in description
    assert "verified result from this call" in text and "verified result from this call" in description
    assert "without asking permission" in text
    assert "not general knowledge or campaign sales claims" in text
