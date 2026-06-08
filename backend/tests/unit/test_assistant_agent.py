from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.infrastructure.assistant import agent
from app.infrastructure.assistant.tools import ALL_TOOLS


@pytest.mark.asyncio
async def test_tool_executor_json_encodes_decimal_payloads(monkeypatch):
    async def fake_get_usage_info(_tenant_id, _db_client):
        return {
            "plan_name": "Pro",
            "plan_price": Decimal("29.99"),
            "minutes_allocated": Decimal("1500"),
            "minutes_used": Decimal("12.5"),
        }

    # tool_executor now routes through the shared dispatcher (ALL_TOOLS
    # registry), so override the registry entry rather than the agent-module
    # name. The behaviour under test — _dump_json encoding Decimals in the
    # ToolMessage — is unchanged.
    monkeypatch.setitem(ALL_TOOLS["get_usage_info"], "function", fake_get_usage_info)

    result = await agent.tool_executor(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "get_usage_info",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            ],
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "conversation_id": None,
            "db_client": object(),
            "tool_results": [],
        }
    )

    assert len(result["messages"]) == 1
    payload = json.loads(result["messages"][0].content)
    assert payload["plan_price"] == 29.99
    assert payload["minutes_allocated"] == 1500
    assert payload["minutes_used"] == 12.5
