"""Regression test for greeting-and-disclosure-not-persisted (2026-09-23),
the campaign_test_ws.py part of the issue.

PRODUCTION EVIDENCE
--------------------
Call adf41aa1 (browser Test-agent, agent-first): the opener was spoken
(`orchestrator.send_greeting` ran and `_build_outbound_greeting`/
`send_greeting` were verified live) but ``transcript_json`` had no
ASSISTANT row for it. The greeting-seed block only appended the greeting to
``voice_session.call_session.conversation_history`` — the LLM's own
in-memory context — never to ``TranscriptService.accumulate_turn``, which is
the ONLY thing ``save_call_transcript_on_hangup`` actually persists.

This drives the REAL ``campaign_test_websocket`` coroutine (reusing the
fixtures from ``test_campaign_test_ws.py``) through an agent-first cascaded
session, with a fake pipeline carrying a real-shaped ``transcript_service``
double, and asserts the spoken greeting was accumulated into it.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.ai_config import AIProviderConfig
from app.api.v1.endpoints import campaign_test_ws
from tests.unit.test_campaign_test_ws import (
    _CAMPAIGN,
    _Harness,
    _end_call_frame,
    _fake_voice_session,
    FakeWebSocket,
)


class _FakeTranscriptService:
    def __init__(self):
        self.turns: list[dict] = []

    def accumulate_turn(self, **kwargs):
        self.turns.append(kwargs)


@pytest.mark.asyncio
async def test_agent_first_greeting_is_accumulated_into_the_transcript_service():
    tenant_cfg = AIProviderConfig(
        llm_provider="gemini", llm_model="gemini-2.5-flash", pipeline_mode="cascaded",
    )
    transcript_service = _FakeTranscriptService()

    with _Harness(tenant_cfg=tenant_cfg, campaign_row=_CAMPAIGN) as h:
        def _create_with_pipeline(config):
            vs = _fake_voice_session(realtime=False)
            vs.pipeline = SimpleNamespace(transcript_service=transcript_service)
            h.captured["config"] = config
            return vs

        h.orchestrator.create_voice_session = AsyncMock(side_effect=_create_with_pipeline)
        h.orchestrator.send_greeting = AsyncMock()

        ws = FakeWebSocket(cookies={"talky_at": "tok"}, recv_frames=[_end_call_frame()])
        await campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker="agent")

    assert transcript_service.turns, (
        "the spoken opener was never accumulated into transcript_service — "
        "it only lands in conversation_history (the LLM's context), so it "
        "never appears in a saved Test-agent transcript (adf41aa1)"
    )
    turn = transcript_service.turns[0]
    assert turn["role"] == "assistant"
    assert turn["content"], "the accumulated turn must carry the actual spoken greeting text"
    assert turn["call_id"] == "call-xyz"  # the session's own call_id
