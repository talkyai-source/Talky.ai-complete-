"""Bounded catalog continuation through real tool loops with synthetic model turns."""
from types import SimpleNamespace
import json
import asyncio

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline import knowledge_tool as kt
from app.infrastructure.llm.streaming import stream_tool_turn
from app.services.scripts.knowledge.sections import build_section_catalog, render_catalog_page, read_sections
from tests.unit.test_gemini_tools import rich_genai, _FakeChunk, _FakeStream, _signed_call  # noqa: F401


def catalog_session(count=500):
    nodes = [{"id": str(i), "source_id": "manual", "source_version": 1, "version": "1",
              "heading": f"Section {i}: " + "label " * 30,
              "content": f"Original section {i}. All amounts exclude tax."} for i in range(count)]
    catalog = build_section_catalog(nodes, tenant_id="tenant", campaign_id="campaign", source_policy="call_snapshot")
    return SimpleNamespace(tenant_id="tenant", campaign_id="campaign", _knowledge_catalog=catalog)


def navigation_steps(session, count=4):
    page = render_catalog_page(session._knowledge_catalog)
    steps = []
    for _ in range(count):
        assert page["next_offset"] is not None
        steps.append({"catalog_offset": page["next_offset"]})
        page = render_catalog_page(session._knowledge_catalog, page["next_offset"])
    steps.append({"section_ids": [page["entries"][0]["section_id"]]})
    return steps


async def test_live_turn_streamer_wires_navigation_budget_before_exact_read(monkeypatch):
    from tests.unit.test_model_driven_voice_turn import setup_turn
    scoped = catalog_session()
    steps = navigation_steps(scoped)
    steps.append("The authored conditions apply.")
    service, session, rounds = setup_turn(monkeypatch, "Explain the late section.", steps)
    session.tenant_id, session.campaign_id = scoped.tenant_id, scoped.campaign_id
    session._knowledge_catalog = scoped._knowledge_catalog
    model = service.llm_provider.stream_chat_with_timeout
    async def honour_tool_availability(messages, **kwargs):
        if not kwargs.get("tools"):
            yield "The authored conditions apply."
            return
        async for token in model(messages, **kwargs):
            yield token
    monkeypatch.setattr(service.llm_provider, "stream_chat_with_timeout", honour_tool_availability)
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == "The authored conditions apply."
    assert session._knowledge_evidence["status"] == "available"
    assert "exclude tax" in session._knowledge_evidence["text"]
    assert len(rounds) == 6


def test_oversized_navigation_heading_remains_browsable_without_clipping_source():
    heading = "Authored long heading " * 410
    rows = [{"id": "first", "source_id": "manual", "source_version": 1, "version": 1,
             "heading": heading, "content": "Complete source body, excluding tax."}]
    catalog = build_section_catalog(rows, tenant_id="tenant", campaign_id="campaign", source_policy="call_snapshot")
    page = render_catalog_page(catalog)
    assert page["status"] == "catalog"
    entry = page["entries"][0]
    assert entry["labels_truncated"] is True
    assert len(entry["heading"]) <= 160
    result = read_sections(catalog, [entry["section_id"]])
    assert result["status"] == "available"
    assert heading in result["text"] and "excluding tax" in result["text"]


@pytest.mark.asyncio
async def test_shared_loop_four_catalog_steps_still_reaches_exact_source():
    session = catalog_session()
    steps = navigation_steps(session)
    requests = []

    async def model(messages, **kwargs):
        requests.append(kwargs)
        if kwargs.get("tools") and len(requests) <= len(steps):
            args = steps[len(requests) - 1]
            kwargs["tool_calls_sink"].append({"id": str(len(requests)), "name": kt.KB_TOOL_NAME,
                                               "arguments": args, "arguments_raw": json.dumps(args)})
        else:
            yield "The authored conditions apply."

    async def runner(name, args):
        return await kt.run_knowledge_lookup(session, args)

    output = [part async for part in stream_tool_turn(SimpleNamespace(stream_chat_with_timeout=model),
        [Message(role=MessageRole.USER, content="Please explain the late section.")],
        tools=[kt.KNOWLEDGE_TOOL_SPEC], tool_runner=runner, max_tool_rounds=3,
        read_only_tools={kt.KB_TOOL_NAME}, navigation_round_allowed=kt.knowledge_navigation_continuation(session))]
    assert output == ["The authored conditions apply."]
    assert session._knowledge_evidence["status"] == "available"
    assert "exclude tax" in session._knowledge_evidence["text"]
    assert len(requests) == 6


def test_catalog_cursor_metadata_and_unfit_identity_allow_explicit_skip():
    session = catalog_session(3)
    context = session._knowledge_catalog
    page = render_catalog_page(context, max_chars=700)
    assert page["offset"] == 0 and page["total_sections"] == 3
    assert page["next_offset"] == len(page["entries"]) == 1
    skipped = render_catalog_page(context, max_chars=1)
    assert skipped["status"] == "too_large" and skipped["skipped_offset"] == 0
    assert skipped["next_offset"] == 1 and skipped["complete"] is False
    final = render_catalog_page(context, 2, max_chars=1)
    assert final["next_offset"] is None and final["complete"] is True
    assert read_sections(context, [context.nodes[0]["section_id"]])["status"] == "available"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["repeat", "jump", "boolean", "mixed", "changed_scope", "bad_wire", "bad_result"])
async def test_navigation_credit_needs_one_current_advancing_catalog_result(damage):
    session = catalog_session()
    allowed = kt.knowledge_navigation_continuation(session)
    args = navigation_steps(session)[0]
    wire = await kt.run_knowledge_lookup(session, args)
    call = {"name": kt.KB_TOOL_NAME, "arguments": args}
    if damage == "repeat":
        assert allowed([(call, wire)])
    elif damage == "jump":
        call = {**call, "arguments": {"catalog_offset": args["catalog_offset"] + 1}}
    elif damage == "boolean":
        call = {**call, "arguments": {"catalog_offset": True}}
    elif damage == "changed_scope":
        session.campaign_id = "another"
    elif damage == "bad_wire":
        wire = "unavailable"
    elif damage == "bad_result":
        session._knowledge_evidence = {"status": "unavailable"}
    results = [(call, wire)]
    if damage == "mixed":
        results.append(({"name": "send_email", "arguments": {}}, '{"success":true}'))
    assert not allowed(results)


def make_loop_provider(adapter, steps, rich):
    """Actual adapters; only their already-parsed model/SDK transport is synthetic."""
    requests, signed = [], []
    if adapter == "gemini":
        from app.infrastructure.llm.gemini import GeminiLLMProvider
        provider = GeminiLLMProvider()
        async def sdk(**kwargs):
            requests.append({**kwargs, "contents": tuple(kwargs["contents"])})
            if getattr(kwargs["config"], "tools", None) and len(requests) <= len(steps):
                chunks = []
                for name, args in steps[len(requests) - 1]:
                    chunk, part = _signed_call(rich, name, args, bytes([len(requests)]))
                    chunks.append(chunk)
                    signed.append(part)
                return _FakeStream(chunks)
            return _FakeStream([_FakeChunk(text="Final answer.")])
        provider._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=sdk)))
    else:
        from app.infrastructure.llm.groq import GroqLLMProvider
        from app.infrastructure.llm.cerebras import CerebrasLLMProvider
        from app.infrastructure.llm.openai import OpenAILLMProvider
        provider = {"groq": GroqLLMProvider, "cerebras": CerebrasLLMProvider, "openai": OpenAILLMProvider}[adapter]()
        async def model(messages, **kwargs):
            requests.append(kwargs)
            # A real model given tool_choice="none" cannot call a tool.
            if kwargs.get("tools") and kwargs.get("tool_choice") != "none" and len(requests) <= len(steps):
                for index, (name, args) in enumerate(steps[len(requests) - 1]):
                    kwargs["tool_calls_sink"].append({"id": f"{len(requests)}-{index}", "name": name,
                        "arguments": args, "arguments_raw": json.dumps(args)})
            else:
                yield "Final answer."
        provider.stream_chat_with_timeout = model
    return provider, requests, signed


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["groq", "cerebras", "openai", "gemini"])
@pytest.mark.parametrize("scenario", ["navigate_read", "repeat", "mixed_write", "hard_cap", "default"])
async def test_actual_provider_navigation_is_bounded_and_does_not_replay_writes(adapter, scenario, rich_genai):  # noqa: F811 - imported pytest fixture
    session = catalog_session()
    navigation = navigation_steps(session, 8)
    if scenario == "navigate_read":
        calls = navigation_steps(session, 4)
    elif scenario == "repeat":
        calls = [navigation[0]] * 8
    else:
        calls = navigation
    steps = [[(kt.KB_TOOL_NAME, args)] for args in calls]
    specs = [kt.KNOWLEDGE_TOOL_SPEC]
    if scenario == "mixed_write":
        for step in steps:
            step.append(("save_fixture", {}))
        specs.append({"type": "function", "function": {"name": "save_fixture",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}})
    provider, requests, signed = make_loop_provider(adapter, steps, rich_genai)
    writes = []
    async def run(name, args):
        if name == kt.KB_TOOL_NAME:
            return await kt.run_knowledge_lookup(session, args)
        writes.append(name)
        return '{"success":true}'
    kwargs = {} if scenario == "default" else {"navigation_round_allowed": kt.knowledge_navigation_continuation(session)}
    output = [part async for part in provider.stream_chat_with_tools(
        [Message(role=MessageRole.USER, content="The original question.")], tools=specs, tool_runner=run,
        max_tool_rounds=3, read_only_tools={kt.KB_TOOL_NAME}, **kwargs)]
    assert output == ["Final answer."]
    expected = {"navigate_read": 6, "repeat": 5, "mixed_write": 4, "hard_cap": 8, "default": 4}[scenario]
    assert len(requests) == expected
    if scenario == "navigate_read":
        assert session._knowledge_evidence["status"] == "available"
        assert "exclude tax" in session._knowledge_evidence["text"]
    else:
        assert session._knowledge_evidence["status"] == "catalog" and not session._knowledge_grounding
        if adapter == "gemini":
            assert not getattr(requests[-1]["config"], "tools", None)
        else:
            # The answer round cannot call tools: tool_choice "none" and no
            # tool channel (dropping the tools made DeepSeek answer "0").
            assert requests[-1].get("tool_choice") == "none"
            assert "tool_calls_sink" not in requests[-1]
    assert writes == (["save_fixture"] if scenario == "mixed_write" else [])
    if adapter == "gemini":
        final_parts = [part for content in requests[-1]["contents"] if content.role == "model" for part in content.parts]
        assert all(any(part is preserved for preserved in final_parts) for part in signed)
        assert requests[-1]["contents"][0].parts[0].text == "The original question."


@pytest.mark.asyncio
async def test_cancelled_navigation_does_not_start_a_followup_or_final():
    session = catalog_session()
    provider, requests, _ = make_loop_provider("groq", [[(kt.KB_TOOL_NAME, navigation_steps(session)[0])]], None)
    async def cancelled(*_args):
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        _ = [part async for part in provider.stream_chat_with_tools([], tools=[kt.KNOWLEDGE_TOOL_SPEC],
            tool_runner=cancelled, max_tool_rounds=3,
            navigation_round_allowed=kt.knowledge_navigation_continuation(session))]
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_native_adapter_keeps_short_labels_separate_from_complete_source():
    from app.realtime.bridge import RealtimeBridge
    session = catalog_session(1)
    row = {**session._knowledge_catalog.nodes[0], "heading": "Long authored heading " * 400}
    context = build_section_catalog([row], tenant_id="tenant", campaign_id="campaign", source_policy="call_snapshot")
    bridge = RealtimeBridge(call_id="synthetic", realtime_session=SimpleNamespace(), media_gateway=object(),
        tenant_id="tenant", campaign_id="campaign")
    bridge._knowledge_catalog = context
    page = await bridge._lookup_knowledge({"catalog_offset": 0})
    assert page["status"] == "catalog" and '"labels_truncated": true' in page["text"]
    assert row["heading"] not in page["text"] and not bridge._verified_knowledge
    result = await bridge._lookup_knowledge({"section_ids": [context.nodes[0]["section_id"]]})
    assert result["status"] == "available" and row["heading"] in result["text"]
    assert "exclude tax" in result["text"]


@pytest.mark.asyncio
async def test_dashboard_actual_dispatch_preserves_label_and_full_source_contract(monkeypatch):
    from contextlib import asynccontextmanager
    from tests.unit.test_assistant_knowledge_authorization import (
        _section_connection, _read_sections, TENANT_ID, ACTOR_ID, access,
    )
    @asynccontextmanager
    async def acquire(pool, tenant_id, *, user_id=None, **_kwargs):
        assert tenant_id == TENANT_ID and user_id == ACTOR_ID
        yield pool.conn
    monkeypatch.setattr(access, "acquire_with_tenant", acquire)
    conn = _section_connection()
    heading = "Long authored title " * 420
    conn.node["heading"] = heading
    page = await _read_sections(conn, catalog_offset=0)
    assert page["status"] == "catalog" and page["offset"] == 0 and page["total_sections"] == 1
    assert page["entries"][0]["labels_truncated"] is True
    result = await _read_sections(conn, section_ids=[page["entries"][0]["section_id"]])
    assert result["status"] == "available" and heading in result["passages"][0]["text"]
    assert "excludes installation and tax" in result["passages"][0]["text"]
    assert result["source_policy"] == "current_read" and conn.updated == []
