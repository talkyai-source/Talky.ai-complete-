from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from app.api.v1.endpoints import assistant_ws


@pytest.fixture(autouse=True)
def _stub_auth_and_model(monkeypatch):
    """Post-hardening the WS verifies auth through decode_and_validate_token
    (same path as REST), not the old db_client.auth.get_user, and resolves the
    tenant's model via get_tenant_assistant_model. Stub both so these tests
    exercise the post-auth conversation flow they target."""
    monkeypatch.setattr(
        assistant_ws, "decode_and_validate_token", lambda _token: {"sub": "user-1"}
    )

    async def _fake_model(_db, _tenant):
        return "test-model"

    monkeypatch.setattr(assistant_ws, "get_tenant_assistant_model", _fake_model)


def _stub_stream(events, captured=None):
    """Build a stand-in for stream_assistant_reply: an async generator that
    optionally records the chat_messages it was handed, then yields the given
    ReAct events (e.g. a single {"type":"final","content":...})."""

    async def _gen(**kwargs):
        if captured is not None:
            captured["chat_messages"] = kwargs.get("chat_messages")
        for ev in events:
            yield ev

    return _gen


class _FakeResponse:
    def __init__(self, data=None, count=None, error=None):
        self.data = data
        self.count = count
        self.error = error


class _FakeTable:
    def __init__(self, table_name: str, state: dict):
        self.table_name = table_name
        self.state = state
        self._mode = "select"
        self._filters: list[tuple[str, object]] = []
        self._payload = None
        self._single = False

    def select(self, *_args, **_kwargs):
        self._mode = "select"
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, column: str, value):
        self._filters.append((column, value))
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        if self.table_name == "user_profiles" and self._mode == "select":
            return _FakeResponse({"tenant_id": "tenant-1"})

        if self.table_name == "assistant_conversations" and self._mode == "insert":
            row = {
                "id": self.state.get("inserted_id", "conv-1"),
                **(self._payload or {}),
            }
            self.state["inserted_conversation"] = row
            return _FakeResponse(row if self._single else [row])

        if self.table_name == "assistant_conversations" and self._mode == "update":
            self.state["updated_conversation"] = self._payload
            return _FakeResponse([])

        if self.table_name == "assistant_conversations" and self._mode == "select":
            return _FakeResponse(None if self._single else [])

        raise AssertionError(f"Unexpected query: table={self.table_name} mode={self._mode}")


class _FakeDbClient:
    def __init__(self):
        self.state: dict = {}
        self.auth = SimpleNamespace(
            get_user=lambda _token: SimpleNamespace(user=SimpleNamespace(id="user-1"))
        )

    def table(self, table_name: str):
        return _FakeTable(table_name, self.state)


class _FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent_messages: list[dict] = []
        self._received = [{"type": "user_message", "content": "Show my campaign stats"}]
        # Auth-hardening contract (2026-05-20/05-21):
        #  - `headers` is read for the Origin CSRF check. Empty == no Origin,
        #    i.e. a non-browser client, which is allowed.
        #  - `cookies` carries the preferred `talky_at` auth token, mirroring
        #    a real browser handshake (no `?token=` URL query anymore).
        self.headers: dict[str, str] = {}
        self.cookies: dict[str, str] = {"talky_at": "test-token"}

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        if self._received:
            return self._received.pop(0)
        raise WebSocketDisconnect()

    async def send_json(self, data: dict):
        self.sent_messages.append(data)


@pytest.mark.asyncio
async def test_assistant_chat_creates_conversation_from_single_insert_response(monkeypatch):
    fake_db = _FakeDbClient()
    fake_websocket = _FakeWebSocket()
    fake_db.state["inserted_id"] = uuid.UUID("12345678-1234-5678-1234-567812345678")

    monkeypatch.setattr(assistant_ws, "get_db_client", lambda: fake_db)
    monkeypatch.setattr(
        "app.infrastructure.assistant.streaming.stream_assistant_reply",
        _stub_stream([{"type": "final", "content": "You have no campaigns."}]),
    )

    await assistant_ws.assistant_chat(fake_websocket, token="test-token", conversation_id=None)

    assert fake_websocket.accepted is True
    assert fake_websocket.sent_messages[0]["type"] == "connected"
    assert any(
        event["type"] == "assistant_message_end"
        and event["content"] == "You have no campaigns."
        for event in fake_websocket.sent_messages
    )
    assert any(
        event["type"] == "conversation_created"
        and event["conversation_id"] == "12345678-1234-5678-1234-567812345678"
        for event in fake_websocket.sent_messages
    )
    assert fake_db.state["inserted_conversation"]["title"] == "Show my campaign stats"


@pytest.mark.asyncio
async def test_assistant_chat_does_not_send_error_when_conversation_update_fails(monkeypatch):
    fake_db = _FakeDbClient()
    fake_websocket = _FakeWebSocket()
    fake_websocket._received = [{"type": "user_message", "content": "hello"}]
    fake_db.state["update_error"] = RuntimeError('record "new" has no field "updated_at"')

    original_execute = _FakeTable.execute

    def execute_with_update_failure(self):
        if (
            self.table_name == "assistant_conversations"
            and self._mode == "update"
            and self.state.get("update_error") is not None
        ):
            raise self.state["update_error"]
        return original_execute(self)

    monkeypatch.setattr(_FakeTable, "execute", execute_with_update_failure)
    monkeypatch.setattr(assistant_ws, "get_db_client", lambda: fake_db)
    monkeypatch.setattr(
        "app.infrastructure.assistant.streaming.stream_assistant_reply",
        _stub_stream([{"type": "final", "content": "Hello there."}]),
    )

    await assistant_ws.assistant_chat(
        fake_websocket,
        token="test-token",
        conversation_id="conv-existing",
    )

    assert any(
        event["type"] == "assistant_message_end" and event["content"] == "Hello there."
        for event in fake_websocket.sent_messages
    )
    assert not any(
        event.get("content") == "Sorry, I encountered an error. Please try again."
        for event in fake_websocket.sent_messages
    )


@pytest.mark.asyncio
async def test_assistant_chat_parses_stringified_history_for_existing_conversation(monkeypatch):
    fake_db = _FakeDbClient()
    fake_websocket = _FakeWebSocket()
    fake_websocket._received = [{"type": "user_message", "content": "what is the status of campaigns"}]

    original_execute = _FakeTable.execute

    def execute_with_string_history(self):
        if self.table_name == "assistant_conversations" and self._mode == "select":
            return _FakeResponse(
                {
                    "messages": '[{"role":"user","content":"hi"},{"role":"assistant","content":"hello"}]'
                }
            )
        return original_execute(self)

    captured_state = {}

    monkeypatch.setattr(_FakeTable, "execute", execute_with_string_history)
    monkeypatch.setattr(assistant_ws, "get_db_client", lambda: fake_db)
    monkeypatch.setattr(
        "app.infrastructure.assistant.streaming.stream_assistant_reply",
        _stub_stream(
            [{"type": "final", "content": "All campaigns are idle."}],
            captured=captured_state,
        ),
    )

    await assistant_ws.assistant_chat(
        fake_websocket,
        token="test-token",
        conversation_id="conv-existing",
    )

    assert captured_state["chat_messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "what is the status of campaigns"},
    ]
    assert any(
        event["type"] == "assistant_message_end"
        and event["content"] == "All campaigns are idle."
        for event in fake_websocket.sent_messages
    )
