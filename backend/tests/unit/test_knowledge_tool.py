"""Exact section tool gating and read behavior; retained diagnostic/provider contracts.

The diagnostic lexical API remains supported, but live reads never use it.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.domain.services.voice_pipeline.knowledge_tool as kt
from app.services.scripts.knowledge.sections import build_section_catalog
from app.infrastructure.llm.groq import (
    GroqLLMProvider, _accumulate_tool_call_frags, _finalize_tool_calls,
)


class _Session:
    call_id = "call-abcd-1234"
    tenant_id = "t1"
    campaign_id = "c1"
    knowledge_mode = "retrieve"

    def __init__(self):
        self._knowledge_catalog = build_section_catalog([
            {"id": "price", "source_id": "handbook", "source_version": 1, "version": 1,
             "heading": "Pricing", "content": "Starter costs $20 per month, excluding tax."},
        ], tenant_id=self.tenant_id, campaign_id=self.campaign_id, source_policy="call_snapshot")


class _GroqProvider:
    supports_tools = True
    name = "groq"
    _model = "llama-3.3-70b-versatile"


class _GeminiProvider:
    supports_tools = True
    name = "gemini"
    _model = "gemini-2.5-flash"


@pytest.mark.parametrize("flag", [None, "inject", "tool"])
@pytest.mark.parametrize("mode", ["inline", "map_retrieve", "retrieve"])
def test_scoped_catalog_offers_reader_independently_of_retired_mode_flag(monkeypatch, flag, mode):
    if flag is None:
        monkeypatch.delenv("VOICE_KB_MODE", raising=False)
    else:
        monkeypatch.setenv("VOICE_KB_MODE", flag)
    session = _Session()
    session.knowledge_mode = mode
    tools = kt.knowledge_tools_for(session, _GroqProvider())
    assert tools and tools[0]["function"]["name"] == kt.KB_TOOL_NAME
    assert "query" not in tools[0]["function"]["parameters"]["properties"]


@pytest.mark.parametrize("provider", [_GroqProvider(), _GeminiProvider()])
def test_reader_supports_existing_tool_providers(provider):
    assert kt.knowledge_tools_for(_Session(), provider)


@pytest.mark.parametrize("missing", ["catalog", "tenant", "campaign", "tools"])
def test_reader_requires_call_scope_and_tool_capability(missing):
    session, provider = _Session(), _GroqProvider()
    if missing == "catalog":
        session._knowledge_catalog = None
    elif missing == "tenant":
        session.tenant_id = "foreign"
    elif missing == "campaign":
        session.campaign_id = "foreign"
    else:
        provider.supports_tools = False
    assert kt.knowledge_tools_for(session, provider) is None


def test_tools_support_gpt_oss():
    provider = _GroqProvider()
    provider._model = "openai/gpt-oss-120b"
    assert kt.knowledge_tools_for(_Session(), provider)


def test_addendum_is_navigation_not_a_source_body_or_answer_claim():
    text = kt.knowledge_system_addendum(_Session())
    assert kt.KB_TOOL_NAME in text
    assert "Small talk needs no lookup" in text
    assert "not that it answers the question" in text
    assert "Pricing" in text and "$20" not in text


async def test_live_lookup_uses_prepared_snapshot_without_db_or_hit_count(monkeypatch):
    live = AsyncMock(side_effect=AssertionError("No live SQL search"))
    pinned = MagicMock(side_effect=AssertionError("No lexical snapshot search"))
    monkeypatch.setattr("app.services.scripts.knowledge.retrieval.retrieve_knowledge", live)
    monkeypatch.setattr("app.services.scripts.knowledge.retrieval.retrieve_pinned_knowledge", pinned)
    session = _Session()
    result = await kt.run_knowledge_lookup(session, {"section_ids": [session._knowledge_catalog.nodes[0]["section_id"]]})
    assert result.count("Starter costs $20 per month, excluding tax.") == 1
    assert session._knowledge_evidence["status"] == "available"
    live.assert_not_awaited()
    pinned.assert_not_called()


@pytest.mark.parametrize("arguments", ["price", {}, {"query": "price"}, {"section_ids": []},
                                      {"section_ids": ["foreign"]}, {"catalog_offset": True}])
async def test_invalid_or_legacy_query_requests_are_explicitly_unavailable(arguments):
    session = _Session()
    session._knowledge_grounding = ["Stale price"]
    result = await kt.run_knowledge_lookup(session, arguments)
    assert session._knowledge_evidence["status"] == "unavailable"
    assert session._knowledge_grounding == []
    assert "$20" not in result


# The existing admin/search diagnostic API intentionally retains its hit-count option.
class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    async def fetch(self, *a, **k):
        return self._rows

    async def execute(self, *a, **k):
        self.executed.append((a, k))


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _run_retrieve(monkeypatch, *, bump_hits):
    import app.services.scripts.knowledge.retrieval as retr

    rows = [{
        "id": "00000000-0000-0000-0000-000000000001",
        "heading": "H", "summary": "S", "voice_answer": None,
        "content": "C", "fts": 0.1, "sim": 0.2,
    }]
    conn = _FakeConn(rows)
    # Accept the new acquire_timeout kwarg the voice path now passes through
    # (retrieve_knowledge → acquire_with_tenant(..., timeout=...)).
    monkeypatch.setattr(
        retr, "acquire_with_tenant",
        lambda pool, tenant, **kw: _FakeAcquire(conn),
    )
    out = asyncio.run(retr.retrieve_knowledge(
        object(), "t1", "c1", "price", k=2, bump_hits=bump_hits,
    ))
    return out, conn


def test_retrieve_bump_false_runs_no_update(monkeypatch):
    out, conn = _run_retrieve(monkeypatch, bump_hits=False)
    assert conn.executed == []              # the hit_count UPDATE is short-circuited
    assert out and out[0]["heading"] == "H"  # rows returned unchanged by the flag


def test_retrieve_bump_true_still_updates(monkeypatch):
    """Default (diagnostic/UI) callers keep the hit_count bump."""
    out, conn = _run_retrieve(monkeypatch, bump_hits=True)
    assert len(conn.executed) == 1          # UPDATE fired
    assert out and out[0]["heading"] == "H"  # same returned shape as bump_hits=False


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
    _accumulate_tool_call_frags(acc, [_Frag(0, arguments='{"section_')])
    _accumulate_tool_call_frags(acc, [_Frag(0, arguments='ids": ["section-1"]}')])
    calls = _finalize_tool_calls(acc)
    assert len(calls) == 1
    assert calls[0]["name"] == "lookup_company_knowledge"
    assert calls[0]["arguments"] == {"section_ids": ["section-1"]}
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
    seen = {"section_ids": None, "rounds": 0}

    async def fake_timeout(messages, **kwargs):
        seen["rounds"] += 1
        sink = kwargs.get("tool_calls_sink")
        if sink is not None:
            # Round 0 — model requests the tool, yields no spoken content.
            sink.append({
                "id": "call_1",
                "name": "lookup_company_knowledge",
                "arguments_raw": '{"section_ids": ["section-1"]}',
                "arguments": {"section_ids": ["section-1"]},
            })
            return
            yield  # pragma: no cover (makes this an async generator)
        # Round 1 — grounded answer. The tool result is in extra_messages.
        assert kwargs.get("extra_messages")
        for tok in ["It's ", "$99."]:
            yield tok

    monkeypatch.setattr(p, "stream_chat_with_timeout", fake_timeout)

    async def runner(name, args):
        seen["section_ids"] = args.get("section_ids")
        return "Premium plan is $99/mo."

    out = _collect(p.stream_chat_with_tools(
        [], system_prompt="x", tools=[kt.KNOWLEDGE_TOOL_SPEC], tool_runner=runner,
    ))
    assert "".join(out) == "It's $99."
    assert seen["section_ids"] == ["section-1"]
    assert seen["rounds"] == 2


def test_no_tools_delegates_to_normal_stream(monkeypatch):
    p = GroqLLMProvider()

    async def fake_timeout(messages, **kwargs):
        assert "tool_calls_sink" not in kwargs
        yield "hi"

    monkeypatch.setattr(p, "stream_chat_with_timeout", fake_timeout)
    out = _collect(p.stream_chat_with_tools([], system_prompt="x"))
    assert out == ["hi"]
