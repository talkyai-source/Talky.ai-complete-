"""Regression tests for the per-campaign "test the agent" WebSocket.

The endpoint (`/ws/campaign-test/{campaign_id}`) must run the REAL campaign
agent, not a demo: it resolves the tenant's live AI Options through the exact
same seam a phone call uses and honors the first-speaker choice. These tests
lock that wiring without touching real providers or the network.

Covered:
  1. cascaded tenant + agent-first  → config resolved from the tenant's
     AI-Options (keyed by campaign.tenant_id), direction=OUTBOUND, greeting
     streamed, ready frame reflects it.
  2. realtime tenant + caller-first → the resolved pipeline_mode drives the
     realtime branch (bridge.run scheduled, no cascaded start_pipeline),
     direction=INBOUND (this is what sets greet_on_start=False on a real
     bridge), no greeting.
  3. missing auth → 1008 close, no session created.
  4. campaign not owned by the tenant (fetch miss) → 1008 close (IDOR guard).

The endpoint imports its collaborators lazily inside the function, so each is
patched at its SOURCE module.

No TestClient — this repo's WS tests drive endpoint coroutines directly with a
fake WebSocket to avoid the starlette/httpx version mismatch in this env
(see test_telephony_bridge_auth.py).
"""
from __future__ import annotations

import json
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.voice_tuning import VoiceTuning
from app.domain.services.voice_orchestrator import Direction
from app.api.v1.endpoints import campaign_test_ws


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeWebSocket:
    """Minimal Starlette-WebSocket stand-in for the endpoint coroutine."""

    def __init__(self, *, cookies=None, origin=None, recv_frames=None, recv_json=None):
        self.cookies = cookies or {}
        self.headers = {"origin": origin} if origin else {}
        self._recv_frames = list(recv_frames or [])
        self._recv_json = list(recv_json or [])
        self.sent: list[dict] = []
        self.closed_code = None
        self.closed_reason = None
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def receive_json(self):
        if not self._recv_json:
            raise asyncio.TimeoutError()
        return self._recv_json.pop(0)

    async def receive(self):
        if self._recv_frames:
            return self._recv_frames.pop(0)
        return {"type": "websocket.disconnect"}

    async def close(self, code=1000, reason=""):
        self.closed_code = code
        self.closed_reason = reason


def _fake_gateway():
    gw = MagicMock()
    gw.is_session_active.return_value = True
    gw.on_call_started = AsyncMock()
    gw.on_audio_received = AsyncMock()
    gw.on_call_ended = AsyncMock()
    gw.mark_playback_complete = MagicMock()
    gw._sample_rate = 8000
    gw._input_sample_rate = 8000
    return gw


def _fake_voice_session(realtime: bool):
    call_session = SimpleNamespace(
        conversation_history=[],
        _first_speaker=None,
        persona_type=None,
        agent_config=None,
        config=None,
        system_prompt="",
    )
    bridge = SimpleNamespace(run=AsyncMock()) if realtime else None
    return SimpleNamespace(
        call_id="call-xyz",
        media_gateway=_fake_gateway(),
        realtime_bridge=bridge,
        call_session=call_session,
        pipeline_task=None,
        _first_speaker=None,
    )


def _end_call_frame():
    return {"type": "websocket.receive", "text": json.dumps({"type": "end_call"})}


class _Harness:
    """Sets up every patch the endpoint needs and records the resolved config."""

    def __init__(self, *, tenant_cfg, campaign_row, tenant_id="tenant-A"):
        self.tenant_cfg = tenant_cfg
        self.campaign_row = campaign_row
        self.tenant_id = tenant_id
        self.captured = {}
        self.orchestrator = MagicMock()
        self.orchestrator.start_pipeline = AsyncMock()
        self.orchestrator.send_greeting = AsyncMock()
        self.orchestrator.end_session = AsyncMock()

        def _create(config):
            self.captured["config"] = config
            return _fake_voice_session(realtime=(config.pipeline_mode == "realtime"))

        self.orchestrator.create_voice_session = AsyncMock(side_effect=_create)

        self.container = SimpleNamespace(
            is_initialized=True,
            db_pool=object(),
            voice_orchestrator=self.orchestrator,
        )

        # Real builder wrapped so the test both exercises real config resolution
        # AND captures what came out of it.
        from app.domain.services import telephony_session_config as tsc

        real_build = tsc.build_telephony_session_config

        def _spy_build(**kwargs):
            cfg = real_build(**kwargs)
            self.captured["build_kwargs"] = kwargs
            return cfg

        db_client = MagicMock()
        (
            db_client.table.return_value.select.return_value.eq.return_value.single
            .return_value.execute.return_value
        ) = SimpleNamespace(data={"tenant_id": tenant_id})

        ai_resolver = SimpleNamespace(
            for_tenant_async=AsyncMock(return_value=tenant_cfg)
        )
        vt_resolver = SimpleNamespace(
            for_tenant_async=AsyncMock(return_value=VoiceTuning())
        )

        self._patches = [
            patch("app.core.jwt_security.decode_and_validate_token", return_value={"sub": "user-1"}),
            patch("app.api.v1.dependencies.get_db_client", return_value=db_client),
            patch("app.core.container.get_container", return_value=self.container),
            patch(
                "app.domain.services.telephony.lifecycle._fetch_campaign_row",
                AsyncMock(return_value=campaign_row),
            ),
            patch(
                "app.domain.services.telephony_session_config.build_telephony_session_config",
                _spy_build,
            ),
            patch(
                "app.domain.services.tenant_ai_config_resolver.get_tenant_ai_config_resolver",
                return_value=ai_resolver,
            ),
            patch(
                "app.domain.services.voice_tuning.get_voice_tuning_resolver",
                return_value=vt_resolver,
            ),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


_CAMPAIGN = {
    "id": "camp-1",
    "tenant_id": "tenant-A",
    "script_config": {"company_name": "Acme", "agent_names": ["Alex"]},
}


def _ready(ws: FakeWebSocket):
    return next((s for s in ws.sent if s.get("type") == "ready"), None)


# ---------------------------------------------------------------------------
# 1. cascaded + agent-first
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cascaded_agent_first_resolves_config_and_greets():
    tenant_cfg = AIProviderConfig(
        llm_provider="gemini", llm_model="gemini-2.5-flash", pipeline_mode="cascaded",
    )
    with _Harness(tenant_cfg=tenant_cfg, campaign_row=_CAMPAIGN) as h:
        ws = FakeWebSocket(cookies={"talky_at": "tok"}, recv_frames=[_end_call_frame()])
        await campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker="agent")

    cfg = h.captured["config"]
    # Resolved from the tenant's AI Options (NOT the process default) and keyed
    # off the campaign's tenant_id.
    assert cfg.llm_model == "gemini-2.5-flash"
    assert cfg.tenant_id == "tenant-A"
    assert cfg.pipeline_mode == "cascaded"
    # first-speaker=agent → OUTBOUND framing, browser gateway.
    assert cfg.direction == Direction.OUTBOUND
    assert h.captured["build_kwargs"]["gateway_type"] == "browser"

    ready = _ready(ws)
    assert ready is not None
    assert ready["pipeline_mode"] == "cascaded"
    assert ready["first_speaker"] == "agent"
    assert ready["sample_rate"] == 8000 and ready["input_sample_rate"] == 8000

    # Cascaded path: pipeline started + greeting streamed; session flag set.
    h.orchestrator.start_pipeline.assert_awaited_once()
    h.orchestrator.send_greeting.assert_awaited_once()
    h.orchestrator.end_session.assert_awaited_once()
    ended = h.orchestrator.end_session.await_args.args[0]
    assert ended._first_speaker == "agent"


# ---------------------------------------------------------------------------
# 2. realtime + caller-first
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_realtime_caller_first_takes_realtime_branch():
    tenant_cfg = AIProviderConfig(
        llm_provider="gemini", llm_model="gemini-2.5-flash", pipeline_mode="realtime",
    )
    with _Harness(tenant_cfg=tenant_cfg, campaign_row=_CAMPAIGN) as h:
        ws = FakeWebSocket(cookies={"talky_at": "tok"}, recv_frames=[_end_call_frame()])
        await campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker="user")

    cfg = h.captured["config"]
    assert cfg.pipeline_mode == "realtime"
    # caller-first → INBOUND, which is what makes a real bridge greet_on_start=False.
    assert cfg.direction == Direction.INBOUND

    ready = _ready(ws)
    assert ready["pipeline_mode"] == "realtime"
    assert ready["first_speaker"] == "user"

    # Realtime branch: gateway wired + bridge.run scheduled; NO cascaded pipeline
    # and NO cascaded greeting.
    ended = h.orchestrator.end_session.await_args.args[0]
    ended.media_gateway.on_call_started.assert_awaited_once()
    ended.realtime_bridge.run.assert_awaited()  # scheduled as a task and awaited on teardown
    assert ended._first_speaker == "user"
    h.orchestrator.start_pipeline.assert_not_awaited()
    h.orchestrator.send_greeting.assert_not_awaited()


# ---------------------------------------------------------------------------
# 3. missing auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_auth_closes_1008_and_creates_no_session():
    tenant_cfg = AIProviderConfig(pipeline_mode="cascaded")
    with _Harness(tenant_cfg=tenant_cfg, campaign_row=_CAMPAIGN) as h:
        # No cookie and no auth frame → _resolve_ws_token returns None.
        ws = FakeWebSocket(recv_json=[])
        await campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker="agent")

    assert ws.closed_code == 1008
    h.orchestrator.create_voice_session.assert_not_called()


# ---------------------------------------------------------------------------
# 4. campaign not owned by tenant (fetch miss) → IDOR guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_campaign_not_found_closes_1008():
    tenant_cfg = AIProviderConfig(pipeline_mode="cascaded")
    with _Harness(tenant_cfg=tenant_cfg, campaign_row=None) as h:
        ws = FakeWebSocket(cookies={"talky_at": "tok"})
        await campaign_test_ws.campaign_test_websocket(ws, "other-tenant-campaign", first_speaker="agent")

    assert ws.closed_code == 1008
    h.orchestrator.create_voice_session.assert_not_called()
