"""The WebSocket handshakes that can START something honour the legal-acceptance switch.

Assistant chat (its ``start_campaign`` tool places real calls), assistant voice and
the campaign test call each call ``legal_gate.acceptance_missing`` right after they
authenticate the user. Switch off: nothing changes. Switch on and not accepted:
the socket is closed with reason ``legal_acceptance_required``.
"""
import inspect
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import assistant_voice_ws, assistant_ws, campaign_test_ws
from app.core import config
from app.core.security import legal_gate
from app.domain.services import legal_acceptance

TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
USER = "bbccddee-1234-4abc-8def-0123456789ab"
PAYLOAD = {"sub": USER, "sid": "s1"}


class FakeSocket:
    def __init__(self):
        self.headers = {}
        self.sent, self.closed = [], []

    async def accept(self):
        return None

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000, reason=""):
        self.closed.append((code, reason))


@pytest.fixture
def switch(monkeypatch):
    legal_gate._accepted.clear()
    state = SimpleNamespace(required=False, owed=True, reads=0)
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(
        legal_acceptance_required=state.required, legal_terms_version="t1", legal_privacy_version="p1"))

    @asynccontextmanager
    async def connection(pool, tenant_id):
        yield object()

    async def acceptance_status(conn, *, tenant_id, user_id):
        state.reads += 1
        return {"legal_acceptance_required": state.owed}

    monkeypatch.setattr(legal_gate, "acquire_with_tenant", connection)
    monkeypatch.setattr(legal_acceptance, "acceptance_status", acceptance_status)
    return state


def _stub_common(monkeypatch, module):
    monkeypatch.setattr(module, "_is_origin_allowed", lambda ws: True)
    monkeypatch.setattr(module, "_resolve_ws_token", AsyncMock(return_value="tok"))


# ---- assistant voice ---------------------------------------------------------

def _voice(monkeypatch):
    m = assistant_voice_ws
    _stub_common(monkeypatch, m)
    monkeypatch.setattr(m, "decode_and_validate_token", lambda token: PAYLOAD)
    monkeypatch.setattr(m, "get_db_client", lambda: SimpleNamespace(pool=object()))

    @asynccontextmanager
    async def conn(pool, tenant_id, **kw):
        yield object()

    monkeypatch.setattr(m, "acquire_with_tenant", conn)
    monkeypatch.setattr(m, "load_session_principal",
                        AsyncMock(return_value={"tenant_id": TENANT, "role": "tenant_admin"}))
    ran = AsyncMock()
    monkeypatch.setattr(m, "_run_voice_session", ran)
    return ran


async def test_voice_switch_off_proceeds(switch, monkeypatch):
    ran = _voice(monkeypatch)
    ws = FakeSocket()
    await assistant_voice_ws.assistant_voice(ws, token=None, conversation_id=None)
    assert ran.await_count == 1 and ws.closed == [] and switch.reads == 0


async def test_voice_switch_on_not_accepted_is_closed(switch, monkeypatch):
    switch.required = True
    ran = _voice(monkeypatch)
    ws = FakeSocket()
    await assistant_voice_ws.assistant_voice(ws, token=None, conversation_id=None)
    assert ran.await_count == 0
    assert ws.closed == [(1008, "legal_acceptance_required")]


# ---- campaign test call ------------------------------------------------------

def _campaign_test(monkeypatch):
    m = campaign_test_ws
    _stub_common(monkeypatch, m)
    monkeypatch.setattr("app.core.jwt_security.decode_and_validate_token", lambda token: PAYLOAD)
    monkeypatch.setattr("app.api.v1.dependencies.get_db_client", lambda: SimpleNamespace(pool=object()))
    monkeypatch.setattr(m, "_check_login_session", AsyncMock(return_value=True))
    monkeypatch.setattr(m, "_resolve_user_tenant", AsyncMock(return_value=TENANT))
    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(is_initialized=False))


async def test_campaign_test_switch_off_proceeds(switch, monkeypatch):
    _campaign_test(monkeypatch)
    ws = FakeSocket()
    await campaign_test_ws.campaign_test_websocket(ws, "c1", first_speaker="agent", allow_barge_in=False)
    assert ws.closed == [(1011, "Container not initialized")] and switch.reads == 0


async def test_campaign_test_switch_on_not_accepted_is_closed(switch, monkeypatch):
    switch.required = True
    _campaign_test(monkeypatch)
    ws = FakeSocket()
    await campaign_test_ws.campaign_test_websocket(ws, "c1", first_speaker="agent", allow_barge_in=False)
    assert ws.closed == [(1008, "legal_acceptance_required")]
    assert {"type": "error", "code": "legal_acceptance_required",
            "message": "Accept the current Terms and Privacy Policy to continue."} in ws.sent


# ---- assistant chat ----------------------------------------------------------

class FakeManager:
    def __init__(self):
        self.sent = []

    async def connect(self, ws, cid):
        return None

    async def send_json(self, cid, data):
        self.sent.append(data)

    def disconnect(self, *a, **k):
        return None


def _chat(monkeypatch):
    m = assistant_ws
    _stub_common(monkeypatch, m)
    fake = FakeManager()
    monkeypatch.setattr(m, "manager", fake)
    monkeypatch.setattr(m, "decode_and_validate_token", lambda token: PAYLOAD)
    monkeypatch.setattr(m, "get_db_client", lambda: SimpleNamespace(pool=object()))

    @asynccontextmanager
    async def conn(pool, tenant_id, **kw):
        yield object()

    monkeypatch.setattr(m, "acquire_with_tenant", conn)
    monkeypatch.setattr(m, "load_session_principal",
                        AsyncMock(return_value={"tenant_id": TENANT, "role": "tenant_admin"}))
    register = AsyncMock(side_effect=RuntimeError("stop here: past the handshake"))
    monkeypatch.setattr(m, "AssistantRuntime", SimpleNamespace(register=register))
    return fake, register


async def test_chat_switch_off_proceeds(switch, monkeypatch):
    _, register = _chat(monkeypatch)
    ws = FakeSocket()
    await assistant_ws.assistant_chat(ws, token=None, conversation_id=None)
    assert register.await_count == 1 and switch.reads == 0


async def test_chat_switch_on_not_accepted_is_closed(switch, monkeypatch):
    switch.required = True
    fake, register = _chat(monkeypatch)
    ws = FakeSocket()
    await assistant_ws.assistant_chat(ws, token=None, conversation_id=None)
    assert register.await_count == 0
    assert ws.closed == [(1008, "legal_acceptance_required")]
    assert any(m.get("code") == "legal_acceptance_required" for m in fake.sent)


# ---- guards: each handler calls the helper after authenticating, before acting

@pytest.mark.parametrize("handler,after,before", [
    (assistant_ws.assistant_chat, 'reason="No tenant"', "AssistantRuntime.register"),
    (assistant_voice_ws.assistant_voice, 'reason="No tenant"', "_run_voice_session"),
    (campaign_test_ws.campaign_test_websocket, 'reason="No tenant"', "_check_campaign_permission"),
])
def test_each_start_handler_calls_the_helper_between_auth_and_action(handler, after, before):
    source = inspect.getsource(handler)
    assert len(re.findall(r"acceptance_missing\(", source)) == 1
    assert source.index(after) < source.index("acceptance_missing(") < source.index(before)
    assert "legal_acceptance_required" in source
