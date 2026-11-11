"""voice-pipeline-9: the public Ask-AI socket needs a per-IP cap and a hard
session-length / idle cap, and every exit must free its slots."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

from app.api.v1.endpoints import ask_ai_ws
from app.domain.models.session import CallSession


class _Gateway:
    def __init__(self):
        self.active = True
        self.ended_reason = None
        self.audio_frames = 0

    def is_session_active(self, _call_id):
        return self.active

    async def on_audio_received(self, _call_id, _data):
        self.audio_frames += 1

    async def on_call_ended(self, _call_id, reason):
        self.ended_reason = reason
        self.active = False


class _Orchestrator:
    def __init__(self, gateway):
        self.gateway = gateway
        self.ended = 0
        self.session = SimpleNamespace(
            call_id="call-1",
            media_gateway=gateway,
            call_session=CallSession(
                call_id="call-1", campaign_id="ask-ai", lead_id="l", provider_call_id="p",
                system_prompt="x", voice_id="v",
            ),
        )

    async def create_voice_session(self, _config):
        return self.session

    async def start_pipeline(self, _session, _ws):
        return None

    async def end_session(self, _session):
        self.ended += 1


class _Ws:
    """receive() streams mic frames (or blocks) until the gateway session ends."""

    def __init__(self, gateway, *, ip="203.0.113.7", stream_frames=True):
        # Case-insensitive like a real Starlette socket (a plain dict made a
        # "X-Real-IP" lookup miss and every socket look like the proxy).
        self.headers = Headers({"x-real-ip": ip})
        self.client = SimpleNamespace(host="127.0.0.1")  # the nginx proxy
        self.sent = []
        self.closed = None
        self._gateway = gateway
        self._stream = stream_frames

    async def accept(self):
        return None

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000, reason=""):
        self.closed = code

    async def receive(self):
        if self._stream:
            await asyncio.sleep(0.01)
            return {"type": "websocket.receive", "bytes": b"\x00\x01"}
        while self._gateway.active:
            await asyncio.sleep(0.01)
        return {"type": "websocket.disconnect"}


@pytest.fixture
def env(monkeypatch):
    gateway = _Gateway()
    orch = _Orchestrator(gateway)
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(voice_orchestrator=orch),
    )
    monkeypatch.setattr(
        ask_ai_ws, "build_ask_ai_session_config",
        lambda: SimpleNamespace(gateway_sample_rate=16000),
    )
    monkeypatch.setattr(ask_ai_ws, "_ask_ai_semaphore", None)
    monkeypatch.setattr(ask_ai_ws, "_sessions_by_ip", {}, raising=False)
    return SimpleNamespace(gateway=gateway, orch=orch)


@pytest.mark.asyncio
async def test_max_session_length_ends_the_session_and_frees_its_slots(env, monkeypatch):
    monkeypatch.setattr(ask_ai_ws, "_MAX_SESSION_SECONDS", 0.15, raising=False)
    monkeypatch.setattr(ask_ai_ws, "_IDLE_SECONDS", 3600, raising=False)
    ws = _Ws(env.gateway)

    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "s1"), timeout=5)

    assert env.gateway.ended_reason == "max_duration"
    assert env.orch.ended == 1
    assert ask_ai_ws._get_semaphore()._value == ask_ai_ws._MAX_CONCURRENT_ASK_AI
    assert not ask_ai_ws._sessions_by_ip


@pytest.mark.asyncio
async def test_idle_session_is_ended(env, monkeypatch):
    monkeypatch.setattr(ask_ai_ws, "_MAX_SESSION_SECONDS", 3600, raising=False)
    monkeypatch.setattr(ask_ai_ws, "_IDLE_SECONDS", 0.05, raising=False)
    # A forgotten tab keeps streaming mic frames; only conversational activity counts.
    ws = _Ws(env.gateway)

    # CallSession.is_stale compares whole-second ages; make the last activity old.
    from datetime import datetime, timedelta
    env.orch.session.call_session.last_activity_at = datetime.utcnow() - timedelta(seconds=30)

    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "s1"), timeout=5)

    assert env.gateway.ended_reason == "idle_timeout"
    assert env.orch.ended == 1


@pytest.mark.asyncio
async def test_per_ip_cap_rejects_the_extra_socket_and_releases_on_exit(env, monkeypatch):
    monkeypatch.setattr(ask_ai_ws, "_MAX_SESSIONS_PER_IP", 1, raising=False)
    monkeypatch.setattr(ask_ai_ws, "_MAX_SESSION_SECONDS", 3600, raising=False)
    monkeypatch.setattr(ask_ai_ws, "_IDLE_SECONDS", 3600, raising=False)

    first = _Ws(env.gateway, stream_frames=False)
    second = _Ws(env.gateway, stream_frames=False)
    third = _Ws(env.gateway, ip="198.51.100.9", stream_frames=False)

    task1 = asyncio.create_task(ask_ai_ws.ask_ai_websocket(first, "s1"))
    await asyncio.sleep(0.1)  # first holds the IP slot

    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(second, "s2"), timeout=5)
    assert second.closed == 1013, "same IP over the cap must be turned away"
    assert any(m.get("type") == "error" for m in second.sent)
    assert env.orch.ended == 0, "a rejected socket must not create a voice session"

    # A different client IP is unaffected.
    task3 = asyncio.create_task(ask_ai_ws.ask_ai_websocket(third, "s3"))
    await asyncio.sleep(0.1)
    assert third.closed is None

    env.gateway.active = False
    await asyncio.wait_for(asyncio.gather(task1, task3), timeout=5)
    assert not ask_ai_ws._sessions_by_ip, "slots must be released when sessions end"
