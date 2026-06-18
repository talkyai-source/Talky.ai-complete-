"""Unit tests for on-demand KB via tool-call (#2 voice latency win).

Covers the gating (which turns get the tool vs the inject fallback), the
lookup execution + budget, and the Groq provider's 2-round tool orchestration
(answer-directly fast path vs run-the-tool path) — without any live API.
"""
import asyncio

import app.domain.services.voice_pipeline.knowledge_tool as kt
from app.infrastructure.llm.groq import (
    GroqLLMProvider,
    _accumulate_tool_call_frags,
    _finalize_tool_calls,
)


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------
class _Session:
    call_id = "call-abcd-1234"
    tenant_id = "t1"
    campaign_id = "c1"
    knowledge_mode = "retrieve"


class _GroqProvider:
    name = "groq"
    _model = "llama-3.3-70b-versatile"


class _GeminiProvider:
    name = "gemini"
    _model = "gemini-2.5-flash"


def test_tools_off_by_default(monkeypatch):
    monkeypatch.delenv("VOICE_KB_MODE", raising=False)
    assert kt.knowledge_tools_for(_Session(), _GroqProvider()) is None


def test_tools_on_when_flag_set(monkeypatch):
    monkeypatch.setenv("VOICE_KB_MODE", "tool")
    tools = kt.knowledge_tools_for(_Session(), _GroqProvider())
    assert tools and tools[0]["function"]["name"] == "lookup_company_knowledge"


def test_tools_on_for_gemini(monkeypatch):
    # Gemini now has native function calling wired (stream_chat_with_tools).
    monkeypatch.setenv("VOICE_KB_MODE", "tool")
    tools = kt.knowledge_tools_for(_Session(), _GeminiProvider())
    assert tools and tools[0]["function"]["name"] == "lookup_company_knowledge"


def test_tools_skip_unsupported_provider(monkeypatch):
    monkeypatch.setenv("VOICE_KB_MODE", "tool")

    class _Other:
        name = "anthropic"
        _model = "claude"

    assert kt.knowledge_tools_for(_Session(), _Other()) is None


def test_tools_skip_gpt_oss(monkeypatch):
    monkeypatch.setenv("VOICE_KB_MODE", "tool")
    p = _GroqProvider()
    p._model = "openai/gpt-oss-120b"
    assert kt.knowledge_tools_for(_Session(), p) is None


def test_tools_skip_non_retrieve_mode(monkeypatch):
    monkeypatch.setenv("VOICE_KB_MODE", "tool")
    s = _Session()
    s.knowledge_mode = "inline"
    assert kt.knowledge_tools_for(s, _GroqProvider()) is None


def test_addendum_mentions_tool():
    text = kt.tool_system_addendum()
    assert "lookup_company_knowledge" in text
    assert "smalltalk" in text.lower()


# ---------------------------------------------------------------------------
# run_knowledge_lookup — budget + fail-soft
# ---------------------------------------------------------------------------
def test_lookup_budget_and_format(monkeypatch):
    big = "word " * 4000
    hits = [
        {"heading": f"Node {i}", "voice_answer": None, "summary": None, "content": big}
        for i in range(5)
    ]

    async def fake_retrieve(*a, **k):
        return hits

    class _Pool: ...
    class _DB: pool = _Pool()
    class _Container:
        is_initialized = True
        db_client = _DB()

    monkeypatch.setattr("app.core.container.get_container", lambda: _Container())
    monkeypatch.setattr(
        "app.services.scripts.knowledge.retrieval.retrieve_knowledge", fake_retrieve
    )

    out = asyncio.run(kt.run_knowledge_lookup(_Session(), "what is the price"))
    assert len(out) <= kt._KB_TOTAL_CHARS + 200   # budgeted, not a 100k dump
    assert "Node 0" in out
    assert "…" in out                              # huge bodies were trimmed


def test_lookup_empty_query_returns_sentinel():
    out = asyncio.run(kt.run_knowledge_lookup(_Session(), "   "))
    assert "No specific information" in out


def test_lookup_no_hits_returns_sentinel(monkeypatch):
    async def fake_retrieve(*a, **k):
        return []

    class _Pool: ...
    class _DB: pool = _Pool()
    class _Container:
        is_initialized = True
        db_client = _DB()

    monkeypatch.setattr("app.core.container.get_container", lambda: _Container())
    monkeypatch.setattr(
        "app.services.scripts.knowledge.retrieval.retrieve_knowledge", fake_retrieve
    )
    out = asyncio.run(kt.run_knowledge_lookup(_Session(), "anything"))
    assert "No specific information" in out


# ---------------------------------------------------------------------------
# Tool-call fragment assembly
# ---------------------------------------------------------------------------
class _Fn:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _Frag:
    def __init__(self, index=0, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        self.function = _Fn(name, arguments)


def test_tool_call_fragments_assemble():
    acc = {}
    _accumulate_tool_call_frags(acc, [_Frag(0, id="call_1", name="lookup_company_knowledge")])
    _accumulate_tool_call_frags(acc, [_Frag(0, arguments='{"que')])
    _accumulate_tool_call_frags(acc, [_Frag(0, arguments='ry": "price"}')])
    calls = _finalize_tool_calls(acc)
    assert len(calls) == 1
    assert calls[0]["name"] == "lookup_company_knowledge"
    assert calls[0]["arguments"] == {"query": "price"}
    assert calls[0]["id"] == "call_1"


def test_tool_call_bad_json_yields_empty_args():
    acc = {}
    _accumulate_tool_call_frags(acc, [_Frag(0, id="x", name="t", arguments="{not json")])
    calls = _finalize_tool_calls(acc)
    assert calls[0]["arguments"] == {}


# ---------------------------------------------------------------------------
# stream_chat_with_tools — 2-round orchestration (no live API)
# ---------------------------------------------------------------------------
def _collect(agen):
    async def _run():
        return [t async for t in agen]
    return asyncio.run(_run())


def test_direct_answer_skips_tool(monkeypatch):
    """Model answers in round 0 → no tool runner call, no 2nd round."""
    p = GroqLLMProvider()
    ran_tool = {"called": False}

    async def fake_timeout(messages, **kwargs):
        # Round 0: yields content (model answered directly).
        for tok in ["Sure", ", we ", "can talk."]:
            yield tok

    monkeypatch.setattr(p, "stream_chat_with_timeout", fake_timeout)

    async def runner(name, args):
        ran_tool["called"] = True
        return "facts"

    out = _collect(p.stream_chat_with_tools(
        [], system_prompt="x", tools=[kt.KNOWLEDGE_TOOL_SPEC], tool_runner=runner,
    ))
    assert "".join(out) == "Sure, we can talk."
    assert ran_tool["called"] is False


def test_tool_path_runs_then_answers(monkeypatch):
    """Round 0 yields no content but populates the sink → runner runs → round 1
    streams the grounded answer."""
    p = GroqLLMProvider()
    seen = {"query": None, "rounds": 0}

    async def fake_timeout(messages, **kwargs):
        seen["rounds"] += 1
        sink = kwargs.get("tool_calls_sink")
        if sink is not None:
            # Round 0 — model requests the tool, yields no spoken content.
            sink.append({
                "id": "call_1",
                "name": "lookup_company_knowledge",
                "arguments_raw": '{"query": "price"}',
                "arguments": {"query": "price"},
            })
            return
            yield  # pragma: no cover (makes this an async generator)
        # Round 1 — grounded answer. The tool result is in extra_messages.
        assert kwargs.get("extra_messages")
        for tok in ["It's ", "$99."]:
            yield tok

    monkeypatch.setattr(p, "stream_chat_with_timeout", fake_timeout)

    async def runner(name, args):
        seen["query"] = args.get("query")
        return "Premium plan is $99/mo."

    out = _collect(p.stream_chat_with_tools(
        [], system_prompt="x", tools=[kt.KNOWLEDGE_TOOL_SPEC], tool_runner=runner,
    ))
    assert "".join(out) == "It's $99."
    assert seen["query"] == "price"
    assert seen["rounds"] == 2


def test_no_tools_delegates_to_normal_stream(monkeypatch):
    p = GroqLLMProvider()

    async def fake_timeout(messages, **kwargs):
        assert "tool_calls_sink" not in kwargs
        yield "hi"

    monkeypatch.setattr(p, "stream_chat_with_timeout", fake_timeout)
    out = _collect(p.stream_chat_with_tools([], system_prompt="x"))
    assert out == ["hi"]
