"""Two endpoints must not pass a provider's own exception text to the browser.

1. voice prefetch (`ai_options/preview.py`): the per-voice failure entry.
2. campaign test WebSocket (`campaign_test_ws.py`): the `realtime_ended` frame.

In both cases the detail stays in the server log.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.api.v1.endpoints import campaign_test_ws
from app.domain.models.ai_config import AIProviderConfig, VoiceInfo

from tests.unit.test_campaign_test_ws import (
    _CAMPAIGN,
    FakeWebSocket,
    _Harness,
    _fake_voice_session,
)

MARKER = 'duplicate key value violates unique constraint "leads_phone_key" DETAIL: Key (phone_number)=(+447700900123)'
PARTS = ("duplicate key", "leads_phone_key", "DETAIL", "+447700900123")


def _marker_records(caplog):
    return [
        r for r in caplog.records
        if "leads_phone_key" in r.getMessage()
        or (r.exc_info and "leads_phone_key" in str(r.exc_info[1]))
    ]


@pytest.mark.asyncio
async def test_prefetch_failed_voice_entry_hides_the_provider_text(monkeypatch, caplog):
    from app.api.v1.endpoints.ai_options import preview
    from app.infrastructure.tts import elevenlabs_catalog

    voice = VoiceInfo(id="stock-voice", name="stock-voice", provider="elevenlabs",
                      language="en", gender="female", provider_is_public=True)
    monkeypatch.setattr(preview, "_get_all_tts_voices", AsyncMock(return_value=[voice]))
    monkeypatch.setattr(preview, "filter_tenant_voices", AsyncMock(return_value=[voice]))
    monkeypatch.setattr(preview, "_load_preview_cache", Mock(return_value=None))
    monkeypatch.setattr(elevenlabs_catalog, "ensure_elevenlabs_preview_cached",
                        AsyncMock(side_effect=RuntimeError(MARKER)))

    user = SimpleNamespace(tenant_id="00000000-0000-4000-8000-00000000000a")
    with caplog.at_level(logging.WARNING):
        result = await preview.prefetch_all_voice_samples(user, SimpleNamespace(pool=object()))

    assert result["failed"] == 1
    assert result["failures"] == [
        {"voice_id": "stock-voice", "error": "The voice sample could not be prepared."}
    ]
    dumped = json.dumps(result, default=str)
    for part in PARTS:
        assert part not in dumped
    assert _marker_records(caplog), "the detail must stay in the server log"


class _BlockingReceiveWebSocket(FakeWebSocket):
    """Keeps the browser side open so only the realtime task can end first."""

    async def receive(self):
        await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_realtime_ended_frame_hides_the_provider_text(caplog):
    tenant_cfg = AIProviderConfig(
        llm_provider="gemini", llm_model="gemini-2.5-flash", pipeline_mode="realtime",
    )
    with _Harness(tenant_cfg=tenant_cfg, campaign_row=_CAMPAIGN) as h:
        def _create(config):
            h.captured["config"] = config
            session = _fake_voice_session(realtime=True)
            session.realtime_bridge.run = AsyncMock(side_effect=RuntimeError(MARKER))
            return session

        h.orchestrator.create_voice_session = AsyncMock(side_effect=_create)
        ws = _BlockingReceiveWebSocket(cookies={"talky_at": "tok"})
        with caplog.at_level(logging.WARNING):
            await asyncio.wait_for(
                campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker="user"),
                timeout=20,
            )

    frames = [s for s in ws.sent if s.get("code") == "realtime_ended"]
    assert len(frames) == 1
    assert frames[0]["message"] == "The realtime voice stopped."
    dumped = json.dumps(ws.sent, default=str)
    for part in PARTS:
        assert part not in dumped
    assert _marker_records(caplog), "the reason must stay in the server log"
