"""Actual application graph/reducer; synthetic model and tool ports only."""

import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from app.infrastructure.assistant import agent


def state():
    return {
        "messages": [{"role": "user", "content": "Synthetic graph request"}],
        "tenant_id": "synthetic-tenant", "user_id": "synthetic-user",
        "conversation_id": "synthetic-conversation", "db_client": object(),
        "tool_results": [], "model": None,
    }


def response(content, tools=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=tools))])


def model(monkeypatch, replies):
    create = AsyncMock(side_effect=replies)
    client = NS(chat=NS(completions=NS(create=create)))
    monkeypatch.setattr(agent, "get_assistant_client", lambda _: (client, lambda args: args))
    return create


async def test_actual_compiled_graph_text_turn_keeps_message_order(monkeypatch):
    create = model(monkeypatch, [response("Synthetic answer")])
    dispatch = AsyncMock()
    monkeypatch.setattr(agent, "dispatch_tool", dispatch)
    result = await agent.assistant_graph.ainvoke(state())
    assert [message.type for message in result["messages"]] == ["human", "ai"]
    assert [message.content for message in result["messages"]] == ["Synthetic graph request", "Synthetic answer"]
    assert create.await_count == 1
    dispatch.assert_not_awaited()


async def test_actual_compiled_graph_tool_loop_preserves_scope_and_tool_message(monkeypatch):
    call = NS(id="synthetic-call", function=NS(name="get_usage_info", arguments="{}"))
    create = model(monkeypatch, [response("", [call]), response("Usage checked")])
    dispatch = AsyncMock(return_value={"minutes_used": 7})
    monkeypatch.setattr(agent, "dispatch_tool", dispatch)
    initial = state()
    result = await agent.assistant_graph.ainvoke(initial)
    assert [message.type for message in result["messages"]] == ["human", "ai", "tool", "ai"]
    tool_message = result["messages"][2]
    assert tool_message.tool_call_id == "synthetic-call"
    assert json.loads(tool_message.content) == {"minutes_used": 7}
    dispatch.assert_awaited_once_with("get_usage_info", "synthetic-tenant", initial["db_client"],
        "synthetic-conversation", {}, actor_user_id="synthetic-user")
    assert create.await_count == 2
    forwarded = create.await_args_list[1].kwargs["messages"][-1]
    assert forwarded["role"] == "tool" and forwarded["tool_call_id"] == "synthetic-call"
    assert json.loads(forwarded["content"]) == {"minutes_used": 7}
    assert result["tool_results"][0]["tool_call_id"] == "synthetic-call"


async def test_actual_graph_stream_model_error_ends_without_tool_effect(monkeypatch):
    create = model(monkeypatch, [RuntimeError("synthetic private provider detail")])
    dispatch = AsyncMock()
    monkeypatch.setattr(agent, "dispatch_tool", dispatch)
    updates = [update async for update in agent.assistant_graph.astream(state())]
    assert len(updates) == 1 and set(updates[0]) == {"agent"}
    message = updates[0]["agent"]["messages"][0]
    assert message.content == "Something went wrong on my side. Please try that again."
    assert "private" not in message.content
    assert create.await_count == 1
    dispatch.assert_not_awaited()
