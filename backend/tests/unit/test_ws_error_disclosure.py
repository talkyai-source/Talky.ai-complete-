"""QA F2-07 (WebSocket part): an unexpected failure never sends its text to the browser.

Codex fixed the HTTP side (fixed client messages, correlated logs). The two
voice sockets still answered any unexpected exception with
{"type": "error", "message": str(e)}, so driver, provider and file-path text
reached the page. They now send a fixed message with a short reference, and
log the exception under the same reference.
"""
import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import ask_ai_ws, campaign_test_ws
from app.domain.models.ai_config import AIProviderConfig
from tests.unit.test_ask_ai_public_demo import _CookieWs, env  # noqa: F401  (fixture)
from tests.unit.test_campaign_test_ws import _CAMPAIGN, FakeWebSocket, _Harness

SECRET = "asyncpg: password authentication failed for user talkyai at 10.0.0.5"


def _assert_safe(frames, caplog):
    error = next(frame for frame in frames if frame.get("type") == "error")
    assert SECRET not in str(error) and "asyncpg" not in str(error)
    reference = error.get("reference")
    assert isinstance(reference, str) and len(reference) >= 8
    assert reference in error["message"]
    assert any(reference in record.getMessage() and record.exc_info for record in caplog.records)


async def test_ask_ai_failure_sends_a_reference_not_the_exception_text(env, monkeypatch, caplog):  # noqa: F811
    env.orch.create_voice_session = AsyncMock(side_effect=RuntimeError(SECRET))
    ws = _CookieWs(env.gateway, cookie="jwt")
    with caplog.at_level(logging.ERROR, logger=ask_ai_ws.logger.name):
        await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "demo-1"), timeout=5)
    assert ws.closed == 1011
    _assert_safe(ws.sent, caplog)


@pytest.mark.asyncio
async def test_campaign_test_failure_sends_a_reference_not_the_exception_text(caplog):
    with _Harness(tenant_cfg=AIProviderConfig(pipeline_mode="cascaded"), campaign_row=_CAMPAIGN) as h:
        h.orchestrator.create_voice_session = AsyncMock(side_effect=RuntimeError(SECRET))
        ws = FakeWebSocket(cookies={"talky_at": "tok"})
        with caplog.at_level(logging.ERROR, logger=campaign_test_ws.logger.name):
            await campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker="agent")
    assert ws.closed_code == 1011
    _assert_safe(ws.sent, caplog)
