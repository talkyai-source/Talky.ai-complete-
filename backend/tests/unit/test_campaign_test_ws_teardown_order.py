"""Regression test for hangup-vs-inflight-reply, part 12c (2026-09-23).

PRODUCTION EVIDENCE
--------------------
Call adf41aa1 (browser Test-agent): the tester sent an explicit "Hi", the
agent's reply finished generating 10ms AFTER the client's end_call frame, but
``_persist_test_transcript`` ran BEFORE anything cancelled/awaited that
in-flight turn — the persist read the transcript buffer 2ms too early
(12:41:09.422198 vs the reply committing at .424495). ``end_session()``
further down does call ``pipeline.cancel_active_turn``, but only after
persist already ran — too late. Result: ``transcript_json`` had the caller's
"Hi" and no agent line, though ~1.9s of its audio had already started
streaming.

This drives the REAL ``campaign_test_websocket`` coroutine (reusing the
fixtures from ``test_campaign_test_ws.py``) end-to-end through an
``end_call`` frame, with a fake pipeline that records call order, and
asserts ``cancel_active_turn`` runs (with the session's own call_id) BEFORE
the transcript persist.
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


@pytest.mark.asyncio
async def test_pending_turn_is_cancelled_before_transcript_persist():
    tenant_cfg = AIProviderConfig(
        llm_provider="gemini", llm_model="gemini-2.5-flash", pipeline_mode="cascaded",
    )
    order: list[str] = []
    cancel_calls: list[str] = []

    async def _fake_cancel(cid: str) -> None:
        cancel_calls.append(cid)
        order.append("cancel")

    def _persist_side_effect(*args, **kwargs):
        order.append("persist")

    with _Harness(tenant_cfg=tenant_cfg, campaign_row=_CAMPAIGN) as h:
        h.persist_test_transcript.side_effect = _persist_side_effect

        def _create_with_pipeline(config):
            vs = _fake_voice_session(realtime=False)
            vs.pipeline = SimpleNamespace(cancel_active_turn=_fake_cancel)
            h.captured["config"] = config
            return vs

        h.orchestrator.create_voice_session = AsyncMock(side_effect=_create_with_pipeline)

        ws = FakeWebSocket(cookies={"talky_at": "tok"}, recv_frames=[_end_call_frame()])
        await campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker="agent")

    assert cancel_calls, (
        "the in-flight turn was never cancelled before teardown — a reply "
        "still generating when end_call arrives races the transcript persist "
        "(adf41aa1: persist ran 2ms before the reply committed)"
    )
    # _fake_voice_session's call_id is "call-xyz" — the SESSION's own id, not
    # some other/PBX identifier. That's what _pending_llm_tasks is keyed by.
    assert cancel_calls == ["call-xyz"]
    assert order == ["cancel", "persist"], (
        f"cancel_active_turn must run BEFORE _persist_test_transcript; got {order!r}"
    )
