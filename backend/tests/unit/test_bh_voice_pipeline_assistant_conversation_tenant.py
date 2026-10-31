"""voice-pipeline-2: the assistant chat and voice sockets must only adopt a
?conversation_id the caller's tenant owns, and every UPDATE of a conversation
must carry a tenant_id predicate (the prod DB role has bypassed RLS).

30 Oct 2026: a conversation also belongs to the user who started it. A
teammate in the same tenant must not resume, read, list or delete it."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect

from app.api.v1.endpoints import assistant_voice_ws, assistant_ws
from app.domain.models.conversation import TranscriptChunk

TENANT = "tenant-A"
FOREIGN = "conv-of-tenant-B"
OWNED = "conv-of-tenant-A"
TEAMMATES = "conv-of-teammate-in-tenant-A"
OWNERS = {OWNED: "u1", TEAMMATES: "u2"}


class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Table:
    def __init__(self, db):
        self.db = db
        self.mode = "select"
        self.filters = []
        self.payload = None

    def select(self, *_a, **_k):
        self.mode = "select"
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def single(self):
        return self

    def execute(self):
        f = dict(self.filters)
        if self.mode == "select":
            # Tenant A holds u1's conversation and teammate u2's conversation.
            owner = OWNERS.get(f.get("id"))
            if owner and f.get("tenant_id") == TENANT and f.get("user_id", owner) == owner:
                return _Resp({"messages": "[]"})
            return _Resp(None)  # the adapter returns data=None for an empty .single()
        if self.mode == "update":
            self.db.updates.append(dict(self.filters))
            self.db.persisted.set()
            return _Resp([])
        self.db.inserts.append(self.payload)
        self.db.persisted.set()
        return _Resp([{"id": "new-conv", **(self.payload or {})}])


class _Db:
    def __init__(self):
        self.updates, self.inserts = [], []
        self.persisted = asyncio.Event()
        self.pool = object()
        self.auth = SimpleNamespace()

    def table(self, name):
        assert name == "assistant_conversations"
        return _Table(self)


def _final_stream(text="ok."):
    async def _gen(**_kw):
        yield {"type": "final", "content": text}
    return _gen


# ---------------------------------------------------------------- chat socket
class _ChatWs:
    def __init__(self):
        self.sent = []
        self.headers = {}
        self.cookies = {"talky_at": "t"}
        self._rx = [{"type": "user_message", "content": "hello"}]

    async def accept(self):
        return None

    async def receive_json(self):
        if self._rx:
            return self._rx.pop(0)
        raise WebSocketDisconnect()

    async def send_json(self, d):
        self.sent.append(d)

    async def close(self, code=1000, reason=""):
        return None


@pytest.fixture
def chat_env(monkeypatch):
    from tests.helpers.assistant_runtime import stub_runtime_receipts
    stub_runtime_receipts(monkeypatch)
    monkeypatch.setattr(assistant_ws, "decode_and_validate_token", lambda _t: {"sub": "u1", "sid": "s1"})
    monkeypatch.setattr(assistant_ws, "get_tenant_assistant_model", AsyncMock(return_value="m"))

    async def _principal(_c, _claims):
        return {"tenant_id": TENANT}

    monkeypatch.setattr(assistant_ws, "load_session_principal", _principal)

    @asynccontextmanager
    async def _acq(*_a):
        yield None

    monkeypatch.setattr(assistant_ws, "acquire_with_tenant", _acq)
    monkeypatch.setattr(assistant_ws, "check_assistant_session", AsyncMock())
    monkeypatch.setattr(
        "app.infrastructure.assistant.streaming.stream_assistant_reply", _final_stream()
    )
    db = _Db()
    monkeypatch.setattr(assistant_ws, "get_db_client", lambda: db)
    return db


@pytest.mark.asyncio
async def test_chat_does_not_adopt_or_update_another_tenants_conversation(chat_env):
    ws = _ChatWs()
    await assistant_ws.assistant_chat(ws, token="t", conversation_id=FOREIGN)
    assert chat_env.updates == [], f"wrote to a conversation the tenant does not own: {chat_env.updates}"
    assert len(chat_env.inserts) == 1 and chat_env.inserts[0]["tenant_id"] == TENANT


@pytest.mark.asyncio
async def test_chat_update_of_owned_conversation_carries_tenant_predicate(chat_env):
    ws = _ChatWs()
    await assistant_ws.assistant_chat(ws, token="t", conversation_id=OWNED)
    assert chat_env.inserts == []
    assert len(chat_env.updates) == 1
    assert chat_env.updates[0].get("id") == OWNED
    assert chat_env.updates[0].get("tenant_id") == TENANT
    assert chat_env.updates[0].get("user_id") == "u1"


@pytest.mark.asyncio
async def test_chat_does_not_adopt_or_update_a_teammates_conversation(chat_env):
    ws = _ChatWs()
    await assistant_ws.assistant_chat(ws, token="t", conversation_id=TEAMMATES)
    assert chat_env.updates == [], f"wrote to a teammate's conversation: {chat_env.updates}"
    assert len(chat_env.inserts) == 1 and chat_env.inserts[0]["user_id"] == "u1"


# --------------------------------------------------------------- voice socket
class _VoiceWs:
    def __init__(self, persisted: asyncio.Event):
        self.sent = []
        self._persisted = persisted

    async def send_json(self, d):
        self.sent.append(d)

    async def send_bytes(self, b):
        return None

    async def close(self, code=1000, reason=""):
        return None

    async def receive(self):
        await asyncio.wait_for(self._persisted.wait(), timeout=5)
        await asyncio.sleep(0.05)
        return {"type": "websocket.disconnect"}


class _Stt:
    async def initialize(self, _cfg):
        return None

    async def stream_transcribe(self, _audio, **_kw):
        yield TranscriptChunk(text="what is my balance", is_final=True)
        await asyncio.sleep(30)

    async def cleanup(self):
        return None


class _Tts:
    async def initialize(self, _cfg):
        return None

    async def stream_synthesize(self, **_kw):
        return
        yield  # pragma: no cover

    async def cleanup(self):
        return None


@pytest.fixture
def voice_env(monkeypatch):
    from tests.helpers.assistant_runtime import stub_runtime_receipts
    stub_runtime_receipts(monkeypatch)
    class _Resolver:
        async def resolve_with_metadata(self, *_a, **_k):
            return SimpleNamespace(secret="key", observation_context={"source": "unattributed"})

    monkeypatch.setattr(
        "app.domain.services.credential_resolver.get_credential_resolver", lambda: _Resolver()
    )
    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.DeepgramFluxSTTProvider", _Stt)
    monkeypatch.setattr("app.infrastructure.tts.cartesia.CartesiaTTSProvider", _Tts)
    monkeypatch.setattr(assistant_voice_ws, "get_tenant_assistant_model", AsyncMock(return_value="m"))
    monkeypatch.setattr(assistant_voice_ws, "check_assistant_session", AsyncMock())
    monkeypatch.setattr(
        "app.infrastructure.assistant.streaming.stream_assistant_reply", _final_stream()
    )
    return _Db()


async def _run_voice(db, conversation_id):
    ws = _VoiceWs(db.persisted)
    await assistant_voice_ws._run_voice_session(
        websocket=ws, session_id="voice_x", tenant_id=TENANT, user_id="u1",
        conversation_id=conversation_id, db_client=db,
    )
    return ws


@pytest.mark.asyncio
async def test_voice_does_not_adopt_or_update_another_tenants_conversation(voice_env):
    ws = await _run_voice(voice_env, FOREIGN)
    ready = next(m for m in ws.sent if m.get("type") == "ready")
    assert ready["conversation_id"] == "new"
    assert voice_env.updates == [], f"wrote to a conversation the tenant does not own: {voice_env.updates}"
    assert len(voice_env.inserts) == 1 and voice_env.inserts[0]["tenant_id"] == TENANT


@pytest.mark.asyncio
async def test_voice_update_of_owned_conversation_carries_tenant_predicate(voice_env):
    await _run_voice(voice_env, OWNED)
    assert voice_env.inserts == []
    assert len(voice_env.updates) == 1
    assert voice_env.updates[0].get("id") == OWNED
    assert voice_env.updates[0].get("tenant_id") == TENANT
    assert voice_env.updates[0].get("user_id") == "u1"


@pytest.mark.asyncio
async def test_voice_does_not_adopt_or_update_a_teammates_conversation(voice_env):
    ws = await _run_voice(voice_env, TEAMMATES)
    ready = next(m for m in ws.sent if m.get("type") == "ready")
    assert ready["conversation_id"] == "new"
    assert voice_env.updates == [], f"wrote to a teammate's conversation: {voice_env.updates}"
    assert len(voice_env.inserts) == 1 and voice_env.inserts[0]["user_id"] == "u1"
