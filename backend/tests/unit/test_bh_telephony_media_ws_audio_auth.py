"""telephony-infra-media-2: /sip/telephony/ws-audio/{call_uuid} must not be open.

The FreeSWITCH mod_audio_fork route used to hand every socket to the audio
bridge, which accepts unconditionally and lets a second connection for the same
call uuid overwrite (and later pop) the first one's session.  The route now
refuses, before the bridge sees the socket:

  * sources that are neither direct loopback nor carrying the internal token,
  * a second concurrent socket for a uuid that already has one,
  * sockets beyond a global cap.
"""
from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.api.v1.endpoints.telephony_bridge as tbr
import app.infrastructure.telephony.freeswitch_audio_bridge as fsb

TOKEN = "unit-internal-service-token"
URL = "/api/v1/sip/telephony/ws-audio/call-uuid-1"


class _FakeBridge:
    """Stands in for FreeSwitchAudioBridge: accept, then hold until released."""

    def __init__(self):
        self.handled: list[str] = []
        self.active: set[str] = set()
        self.release = threading.Event()

    def is_session_active(self, call_uuid: str) -> bool:
        return call_uuid in self.active

    async def handle_websocket(self, websocket, call_uuid: str) -> None:
        await websocket.accept()
        self.handled.append(call_uuid)
        self.active.add(call_uuid)
        try:
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        finally:
            self.active.discard(call_uuid)


@pytest.fixture()
def bridge(monkeypatch):
    fake = _FakeBridge()
    monkeypatch.setattr(fsb, "get_audio_bridge", lambda: fake)
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    monkeypatch.delenv("TELEPHONY_WS_AUDIO_MAX_CONNECTIONS", raising=False)
    # The in-flight registry is module state; keep tests independent.
    if hasattr(tbr, "_ws_audio_inflight"):
        tbr._ws_audio_inflight.clear()
    yield fake
    fake.release.set()
    if hasattr(tbr, "_ws_audio_inflight"):
        tbr._ws_audio_inflight.clear()


def _client(host: str) -> TestClient:
    app = FastAPI()
    app.include_router(tbr.router, prefix="/api/v1")
    return TestClient(app, client=(host, 50000))


def _assert_refused(client: TestClient, url: str = URL, **kw) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url, **kw):
            pass
    return exc.value.code


def test_remote_source_without_token_is_refused_before_the_bridge(bridge):
    code = _assert_refused(_client("203.0.113.9"))
    assert code == 1008
    assert bridge.handled == []


def test_remote_source_with_wrong_token_is_refused(bridge):
    code = _assert_refused(_client("203.0.113.9"), url=URL + "?token=nope")
    assert code == 1008
    assert bridge.handled == []


def test_internal_token_in_the_url_is_refused(bridge):
    """The shared internal token must never travel in a URL: access logs keep
    query strings, and that token opens every internal route.  Header only."""
    code = _assert_refused(_client("203.0.113.9"), url=URL + f"?token={TOKEN}")
    assert code == 1008
    assert bridge.handled == []


def test_remote_source_with_internal_token_header_is_accepted(bridge):
    with _client("203.0.113.9").websocket_connect(
        URL, headers={"x-internal-service-token": TOKEN}
    ):
        assert bridge.handled == ["call-uuid-1"]


def test_direct_loopback_without_token_is_accepted(bridge):
    """mod_audio_fork on the same host dials ws://127.0.0.1:8000/... unauthenticated."""
    with _client("127.0.0.1").websocket_connect(URL):
        assert bridge.handled == ["call-uuid-1"]


def test_loopback_behind_a_reverse_proxy_is_not_trusted(bridge):
    """nginx on the same host makes every public client look like loopback."""
    code = _assert_refused(
        _client("127.0.0.1"), headers={"x-forwarded-for": "198.51.100.7"}
    )
    assert code == 1008
    assert bridge.handled == []


def test_unset_internal_token_does_not_open_remote_sources(bridge, monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    code = _assert_refused(_client("203.0.113.9"), url=URL + "?token=")
    assert code == 1008
    assert bridge.handled == []


def test_second_concurrent_socket_for_same_uuid_is_refused(bridge):
    client = _client("127.0.0.1")
    with client.websocket_connect(URL):
        assert bridge.handled == ["call-uuid-1"]
        code = _assert_refused(client)
        assert code == 1008
        # The first socket's session was neither replaced nor re-registered.
        assert bridge.handled == ["call-uuid-1"]
        assert bridge.active == {"call-uuid-1"}


def test_uuid_is_reusable_after_the_first_socket_closes(bridge):
    client = _client("127.0.0.1")
    with client.websocket_connect(URL):
        pass
    bridge.release.set()
    # Give the first handler a moment to leave; then a reconnect must work.
    bridge.release.clear()
    with client.websocket_connect(URL):
        assert bridge.handled.count("call-uuid-1") == 2


def test_global_connection_cap_refuses_extra_sockets(bridge, monkeypatch):
    monkeypatch.setenv("TELEPHONY_WS_AUDIO_MAX_CONNECTIONS", "1")
    client = _client("127.0.0.1")
    with client.websocket_connect(URL):
        code = _assert_refused(client, url="/api/v1/sip/telephony/ws-audio/other-uuid")
        assert code in (1008, 1013)
        assert bridge.handled == ["call-uuid-1"]
