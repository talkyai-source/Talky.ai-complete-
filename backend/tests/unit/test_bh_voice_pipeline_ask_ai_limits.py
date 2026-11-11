"""voice-pipeline-9: the public Ask-AI socket needs a per-IP cap and a hard
session-length / idle cap, and every exit must free its slots."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

from app.api.v1.endpoints import ask_ai_ws
from app.domain.models.session import CallSession

WEBSITE = "https://talkleeai.com"


def allow_website(monkeypatch):
    """The settings the origin check reads, and fresh start budgets."""
    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(allowed_origins=[WEBSITE]))
    monkeypatch.setattr(ask_ai_ws, "_starts_by_ip", {})
    monkeypatch.setattr(ask_ai_ws, "_starts_all", ask_ai_ws.deque())


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
        # "X-Real-IP" lookup miss and every socket look like the proxy). The
        # website's own origin: the open demo refuses upgrades without one.
        self.headers = Headers({"x-real-ip": ip, "origin": WEBSITE})
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
    allow_website(monkeypatch)
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


# ---------------------------------------------------------------------------
# Start budgets for the open demo (12 Nov 2026, when sign-in stopped being the
# gate): a visitor may start a few sessions an hour and a day, and the whole
# site a bounded number a day, so a script cycling sessions cannot run up the bill.
# ---------------------------------------------------------------------------


def test_a_visitor_gets_a_few_starts_an_hour_then_waits_for_the_window(monkeypatch):
    allow_website(monkeypatch)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_IP_PER_HOUR", 2)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_IP_PER_DAY", 100)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_DAY", 100)
    assert ask_ai_ws._admit_start("203.0.113.7", 1_000.0) is None
    assert ask_ai_ws._admit_start("203.0.113.7", 1_010.0) is None
    assert ask_ai_ws._admit_start("203.0.113.7", 1_020.0) == "visitor_hourly"
    assert ask_ai_ws._admit_start("198.51.100.9", 1_020.0) is None, "another visitor is unaffected"
    # A refused start is not counted, and the hour rolls over.
    assert ask_ai_ws._admit_start("203.0.113.7", 1_000.0 + 3_601) is None


def test_a_visitor_has_a_daily_budget(monkeypatch):
    allow_website(monkeypatch)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_IP_PER_HOUR", 100)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_IP_PER_DAY", 3)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_DAY", 100)
    for minute in range(3):
        assert ask_ai_ws._admit_start("203.0.113.7", 60.0 * minute) is None
    assert ask_ai_ws._admit_start("203.0.113.7", 7_200.0) == "visitor_daily"
    assert ask_ai_ws._admit_start("203.0.113.7", 86_400.0 + 61) is None, "the day rolls over"


def test_the_site_has_a_daily_budget_across_visitors(monkeypatch):
    allow_website(monkeypatch)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_IP_PER_HOUR", 100)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_IP_PER_DAY", 100)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_DAY", 3)
    for n in range(3):
        assert ask_ai_ws._admit_start(f"198.51.100.{n}", float(n)) is None
    assert ask_ai_ws._admit_start("203.0.113.7", 10.0) == "site_daily"
    assert ask_ai_ws._admit_start("203.0.113.7", 86_400.0 + 1) is None


def test_forgotten_visitors_do_not_grow_memory_without_bound(monkeypatch):
    allow_website(monkeypatch)
    monkeypatch.setattr(ask_ai_ws, "_MAX_TRACKED_IPS", 2)
    for n in range(3):
        ask_ai_ws._admit_start(f"198.51.100.{n}", float(n))
    ask_ai_ws._admit_start("203.0.113.7", 86_400.0 + 10)
    assert set(ask_ai_ws._starts_by_ip) == {"203.0.113.7"}


@pytest.mark.asyncio
async def test_an_exhausted_budget_closes_before_any_paid_session(env, monkeypatch):
    monkeypatch.setattr(ask_ai_ws, "_admit_start", lambda _ip, _now: "visitor_hourly")
    ws = _Ws(env.gateway)
    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "s1"), timeout=5)
    assert ws.closed == 1013
    assert ws.sent and ws.sent[0]["type"] == "error"
    assert env.orch.ended == 0, "no voice session was created"
    assert ask_ai_ws._get_semaphore()._value == ask_ai_ws._MAX_CONCURRENT_ASK_AI
    assert not ask_ai_ws._sessions_by_ip


def test_the_default_session_is_five_minutes():
    import os

    if "ASK_AI_MAX_SESSION_SECONDS" in os.environ:
        pytest.skip("an operator override is set in this environment")
    assert ask_ai_ws._MAX_SESSION_SECONDS == 300


def test_ipv6_visitors_are_counted_per_64_network(monkeypatch):
    """One IPv6 host normally holds a whole /64, so rotating addresses inside
    it must not reset the visitor's budget (security review, 12 Nov 2026)."""
    allow_website(monkeypatch)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_IP_PER_HOUR", 1)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_IP_PER_DAY", 100)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_DAY", 100)
    assert ask_ai_ws._admit_start("2001:db8:1:2::1", 0.0) is None
    assert ask_ai_ws._admit_start("2001:db8:1:2:ffff::9", 1.0) == "visitor_hourly"
    assert ask_ai_ws._admit_start("2001:db8:1:3::1", 2.0) is None, "another /64 is another visitor"
    # IPv4, and IPv4 written as IPv6, stay one address each.
    assert ask_ai_ws._visitor_key("203.0.113.7") == "203.0.113.7"
    assert ask_ai_ws._visitor_key("::ffff:203.0.113.7") == "203.0.113.7"
    assert ask_ai_ws._visitor_key("unknown") == "unknown"


def test_the_site_budget_warns_at_eighty_percent(monkeypatch, caplog):
    import logging

    allow_website(monkeypatch)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_IP_PER_HOUR", 100)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_IP_PER_DAY", 100)
    monkeypatch.setattr(ask_ai_ws, "_STARTS_PER_DAY", 5)
    with caplog.at_level(logging.WARNING, logger=ask_ai_ws.logger.name):
        for n in range(4):
            ask_ai_ws._admit_start(f"198.51.100.{n}", float(n))
    warnings = [r.getMessage() for r in caplog.records if "80% used" in r.getMessage()]
    assert warnings == ["Ask AI site start budget 80% used: 4 of 5 in the last 24 h"]
