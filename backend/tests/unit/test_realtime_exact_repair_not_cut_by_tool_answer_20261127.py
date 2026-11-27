"""Guard: an exact repair must not be followed by a reply fired at its end.

``repair_unspoken_response`` deliberately does NOT claim ``_response_active``
(unlike ``_create_response``). The bridge hands a parked tool result to the
provider right after the repair's send. If the repair claimed the flag, that
continuation would be deferred and fired at the repair's ``response.done``, and
its reply would cut the repaired sentence (do-not-call acknowledgement, AI
identity line, recording notice) in mid-playback. With the flag unclaimed the
provider rejects the early request and the tool result waits for the next turn.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.realtime.openai import OpenAIRealtimeSession
from app.realtime.xai import XAIRealtimeSession

_REQUIRED = "I cannot confirm that your do-not-call request was saved."
_MSG = ("a reply fired at the repair's end would cut the repaired sentence "
        "in mid-playback")


def _make(provider_type):
    provider = provider_type(api_key="dummy",
                             voice="ash" if provider_type is OpenAIRealtimeSession else "eve")
    provider._ws = SimpleNamespace(send=AsyncMock())
    return provider


def _frames(provider):
    return [json.loads(c.args[0])["type"] for c in provider._ws.send.await_args_list]


async def _assert_nothing_fires_at_repair_end(provider):
    assert provider._pending_response_create is False, _MSG
    await provider._handle_server_event({"type": "response.created", "response": {"id": "r2"}})
    before = _frames(provider).count("response.create")
    await provider._handle_server_event(
        {"type": "response.done", "response": {"id": "r2", "status": "completed", "output": []}})
    assert _frames(provider).count("response.create") == before, _MSG


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [OpenAIRealtimeSession, XAIRealtimeSession])
async def test_a_tool_answer_right_after_an_exact_repair_fires_no_reply_when_the_repair_ends(provider_type):
    provider = _make(provider_type)
    await provider.repair_unspoken_response({"output": []}, required_text=_REQUIRED)
    await provider.send_function_result("tool-1", {"status": "ok"})
    await _assert_nothing_fires_at_repair_end(provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [OpenAIRealtimeSession, XAIRealtimeSession])
async def test_a_plain_continuation_request_right_after_an_exact_repair_is_not_deferred_either(provider_type):
    provider = _make(provider_type)
    await provider.repair_unspoken_response({"output": []}, required_text=_REQUIRED)
    await provider.request_response()
    await _assert_nothing_fires_at_repair_end(provider)
