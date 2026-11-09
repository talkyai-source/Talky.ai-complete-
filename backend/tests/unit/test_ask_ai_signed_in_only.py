"""QA F11-06: the Ask-AI voice socket serves signed-in sessions only.

wss://.../ws/ask-ai/<any id> accepted scripted sessions with no account or
token; each runs paid speech, model and voice providers. The website offers
the demo only after sign-in, and its popup already relies on the talky_at
HttpOnly cookie travelling on the handshake (a 1008 close sends the visitor
to sign in). The socket now checks the browser Origin, then the cookie with
the same token and session verification REST uses, before any provider or
capacity slot is touched. The global and per-IP caps stay.
"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

from app.api.v1.endpoints import ask_ai_ws
from tests.unit.test_bh_voice_pipeline_ask_ai_limits import _Gateway, _Orchestrator, _Ws


class _CookieWs(_Ws):
    def __init__(self, gateway, *, cookie=None, origin=None, **kwargs):
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
    return SimpleNamespace(gateway=gateway, orch=orch, created=created)


def _principal(monkeypatch, result):
    async def fake(_websocket):
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(ask_ai_ws, "_signed_in_principal", fake)


@pytest.mark.parametrize("result", [None])
async def test_a_visitor_without_a_signed_in_session_is_refused_before_any_provider(env, monkeypatch, result):
    _principal(monkeypatch, result)
    ws = _CookieWs(env.gateway)
    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "demo-1"), timeout=5)
    assert ws.closed == 1008
    assert ws.sent and ws.sent[0]["type"] == "error" and "sign in" in ws.sent[0]["message"].lower()
    assert env.created == [], "no paid provider session for an anonymous socket"
    assert ask_ai_ws._get_semaphore()._value == ask_ai_ws._MAX_CONCURRENT_ASK_AI
    assert not ask_ai_ws._sessions_by_ip


async def test_a_failed_sign_in_check_is_a_retryable_close_not_an_open_door(env, monkeypatch):
    _principal(monkeypatch, RuntimeError("database down"))
    ws = _CookieWs(env.gateway)
    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "demo-1"), timeout=5)
    assert ws.closed == 1011
    assert env.created == []


async def test_a_signed_in_session_gets_the_demo(env, monkeypatch):
    _principal(monkeypatch, {"user_id": "u1", "tenant_id": "t1"})
    ws = _CookieWs(env.gateway, cookie="jwt")
    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "demo-1"), timeout=5)
    assert len(env.created) == 1


async def test_a_cross_site_page_cannot_open_it_with_the_visitors_cookie(env, monkeypatch):
    _principal(monkeypatch, {"user_id": "u1", "tenant_id": "t1"})
    monkeypatch.setattr("app.core.config.get_settings",
                        lambda: SimpleNamespace(allowed_origins=["https://talkleeai.com"]))
    ws = _CookieWs(env.gateway, cookie="jwt", origin="https://evil.example")
    await asyncio.wait_for(ask_ai_ws.ask_ai_websocket(ws, "demo-1"), timeout=5)
    assert ws.closed == 1008 and ws.accepted is False
    assert env.created == []


@pytest.fixture
def verify(monkeypatch):
    state = SimpleNamespace(decoded=[], loaded=[], token_error=None, principal_error=None)

    def decode(token):
        state.decoded.append(token)
        if state.token_error:
            raise state.token_error
        return {"sub": "u1", "sid": "s1"}

    async def load(conn, payload):
        state.loaded.append(payload)
        if state.principal_error:
            raise state.principal_error
        return {"user_id": "u1", "tenant_id": "t1"}

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        assert tenant_id is None, "the principal lookup is service-scoped, as on REST"
        yield object()

    monkeypatch.setattr("app.core.jwt_security.decode_and_validate_token", decode)
    monkeypatch.setattr("app.core.security.principal.load_session_principal", load)
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquire)
    monkeypatch.setattr("app.api.v1.dependencies.get_db_client", lambda: SimpleNamespace(pool=object()))
    return state


async def test_the_cookie_is_verified_like_rest(verify):
    ws = SimpleNamespace(cookies={"talky_at": " jwt "})
    assert await ask_ai_ws._signed_in_principal(ws) == {"user_id": "u1", "tenant_id": "t1"}
    assert verify.decoded == ["jwt"] and verify.loaded == [{"sub": "u1", "sid": "s1"}]


async def test_no_cookie_bad_token_or_ended_session_is_no_principal(verify):
    from app.core.jwt_security import JWTValidationError
    from app.core.security.principal import PrincipalUnavailable

    assert await ask_ai_ws._signed_in_principal(SimpleNamespace(cookies={})) is None
    verify.token_error = JWTValidationError("expired")
    assert await ask_ai_ws._signed_in_principal(SimpleNamespace(cookies={"talky_at": "jwt"})) is None
    verify.token_error = None
    verify.principal_error = PrincipalUnavailable("session_revoked")
    assert await ask_ai_ws._signed_in_principal(SimpleNamespace(cookies={"talky_at": "jwt"})) is None
