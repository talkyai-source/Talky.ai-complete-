"""The Test Agent endpoints a caller-first call like a phone call (TT-3).

Test call 6b9cd4c4 (2026-10-08, caller-first): Flux ran with
eot_timeout_ms=500, so "Who is this?", "Hello? Can you listen?" and "Okay.
So" were cut into separate turns. A real phone call with the same campaign
gets at least 1000 ms (telephony/prewarm.py, callee_first_eot_timeout); the
browser test socket built its config without that floor, so the Test Agent
was not the live-call agent it claims to be.
"""
from __future__ import annotations

import pytest

from app.api.v1.endpoints import campaign_test_ws
from app.domain.models.ai_config import AIProviderConfig
from tests.unit.test_campaign_test_ws import _CAMPAIGN, FakeWebSocket, _end_call_frame, _Harness


async def _config_for(first_speaker):
    tenant_cfg = AIProviderConfig(llm_provider="gemini", llm_model="gemini-2.5-flash", pipeline_mode="cascaded")
    with _Harness(tenant_cfg=tenant_cfg, campaign_row=_CAMPAIGN) as h:
        ws = FakeWebSocket(cookies={"talky_at": "tok"}, recv_frames=[_end_call_frame()])
        await campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker=first_speaker)
    return h.captured["config"]


@pytest.mark.asyncio
async def test_a_caller_first_test_call_gets_the_phone_end_of_turn_floor():
    config = await _config_for("user")
    assert config.stt_eot_timeout_ms >= 1000


@pytest.mark.asyncio
async def test_an_agent_first_test_call_keeps_the_tenant_timing():
    config = await _config_for("agent")
    assert config.stt_eot_timeout_ms == 500
