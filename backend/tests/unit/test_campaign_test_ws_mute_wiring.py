"""Regression test for the campaign_test_ws.py half of
test-agent-mute-only-on-greeting (2026-09-23): the CallSession must carry the
resolved ``mute_during_tts`` value so ``tts_playback.synthesize_and_send``
(the object it actually receives is the CallSession, not the VoiceSession/
config) can read it. See test_tts_playback_mute_wiring.py for the consumer
side of this fix.
"""
from __future__ import annotations

import pytest

from app.domain.models.ai_config import AIProviderConfig
from app.api.v1.endpoints import campaign_test_ws
from tests.unit.test_campaign_test_ws import _CAMPAIGN, _Harness, _end_call_frame, FakeWebSocket


@pytest.mark.asyncio
async def test_default_allow_barge_in_false_sets_mute_during_tts_on_call_session():
    """allow_barge_in=False (the endpoint's real-world default, passed
    explicitly here — calling the coroutine directly, bypassing FastAPI's
    dependency injection, does not resolve the `Query(False)` default into a
    plain bool) -> the browser gateway default (mute_during_tts=True) must
    reach the CallSession."""
    tenant_cfg = AIProviderConfig(
        llm_provider="gemini", llm_model="gemini-2.5-flash", pipeline_mode="cascaded",
    )
    with _Harness(tenant_cfg=tenant_cfg, campaign_row=_CAMPAIGN) as h:
        ws = FakeWebSocket(cookies={"talky_at": "tok"}, recv_frames=[_end_call_frame()])
        await campaign_test_ws.campaign_test_websocket(
            ws, "camp-1", first_speaker="agent", allow_barge_in=False
        )

    cfg = h.captured["config"]
    assert cfg.mute_during_tts is True
    ended = h.orchestrator.end_session.await_args.args[0]
    assert ended.call_session._mute_during_tts is True, (
        "the CallSession must carry the resolved mute_during_tts so "
        "tts_playback can mute STT for every reply, not just the greeting"
    )


@pytest.mark.asyncio
async def test_allow_barge_in_true_sets_mute_during_tts_false_on_call_session():
    tenant_cfg = AIProviderConfig(
        llm_provider="gemini", llm_model="gemini-2.5-flash", pipeline_mode="cascaded",
    )
    with _Harness(tenant_cfg=tenant_cfg, campaign_row=_CAMPAIGN) as h:
        ws = FakeWebSocket(cookies={"talky_at": "tok"}, recv_frames=[_end_call_frame()])
        await campaign_test_ws.campaign_test_websocket(
            ws, "camp-1", first_speaker="agent", allow_barge_in=True
        )

    cfg = h.captured["config"]
    assert cfg.mute_during_tts is False
    ended = h.orchestrator.end_session.await_args.args[0]
    assert ended.call_session._mute_during_tts is False
