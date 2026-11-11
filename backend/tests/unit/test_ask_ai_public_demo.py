"""The website's hero demo talks to visitors without sign-in (owner decision, 12 Nov 2026).

QA F11-06 had limited the Ask-AI voice socket to signed-in sessions. The owner
reversed that: anyone on the website can talk to the demo. Each session still
runs paid speech, model and voice providers, so the socket keeps its other
guards: the browser Origin check before accept, the global and per-client caps
before any provider (test_bh_voice_pipeline_ask_ai_limits.py), and the session
length and idle caps.
"""
import asyncio
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

from app.api.v1.endpoints import ask_ai_ws
from tests.unit.test_bh_voice_pipeline_ask_ai_limits import WEBSITE, _Gateway, _Orchestrator, _Ws, allow_website


class _CookieWs(_Ws):
    def __init__(self, gateway, *, cookie=None, origin=WEBSITE, **kwargs):
        super().__init__(gateway, **kwargs)
        self.cookies = {"talky_at": cookie} if cookie is not None else {}
        headers = {"x-real-ip": "203.0.113.7"}
        if origin:
            headers["origin"] = origin
        self.headers = Headers(headers)
        self.accepted = False

    async def accept(self):
        self.accepted = True


@pytest.fixture
def env(monkeypatch):
    gateway = _Gateway()
    orch = _Orchestrator(gateway)
    created = []
    original = orch.create_voice_session

    async def create(config):
        created.append(config)
        return await original(config)

    orch.create_voice_session = create
    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(voice_orchestrator=orch))
    monkeypatch.setattr(ask_ai_ws, "build_ask_ai_session_config", lambda: SimpleNamespace(gateway_sample_rate=16000))
    monkeypatch.setattr(ask_ai_ws, "_ask_ai_semaphore", None)
    monkeypatch.setattr(ask_ai_ws, "_sessions_by_ip", {}, raising=False)
    monkeypatch.setattr(ask_ai_ws, "_MAX_SESSION_SECONDS", 0.1, raising=False)
    allow_website(monkeypatch)
    return SimpleNamespace(gateway=gateway, orch=orch, created=created)


async def test_a_visitor_without_an_account_gets_the_demo(env):
    ws = _CookieWs(env.gateway)
    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "demo-1"), timeout=5)
    assert ws.accepted is True
    assert len(env.created) == 1, "an anonymous visitor gets a demo session"
    assert ws.sent and ws.sent[0]["type"] == "ready"
    assert ws.closed != 1008, "no sign-in refusal"
    assert ask_ai_ws._get_semaphore()._value == ask_ai_ws._MAX_CONCURRENT_ASK_AI
    assert not ask_ai_ws._sessions_by_ip


async def test_a_signed_in_visitor_still_gets_the_demo(env):
    ws = _CookieWs(env.gateway, cookie="jwt")
    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "demo-1"), timeout=5)
    assert len(env.created) == 1


async def test_a_cross_site_page_cannot_open_it(env):
    ws = _CookieWs(env.gateway, origin="https://evil.example")
    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "demo-1"), timeout=5)
    assert ws.closed == 1008 and ws.accepted is False
    assert env.created == []


async def test_a_script_without_an_origin_cannot_open_it(env):
    """Browsers always send Origin on a socket upgrade; with sign-in gone, a
    request without one is refused before accept and before any provider."""
    ws = _CookieWs(env.gateway, origin=None)
    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "demo-1"), timeout=5)
    assert ws.closed == 1008 and ws.accepted is False
    assert env.created == []


async def test_the_website_itself_can_open_it(env):
    ws = _CookieWs(env.gateway, origin=WEBSITE)
    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "demo-1"), timeout=5)
    assert ws.accepted is True and len(env.created) == 1
