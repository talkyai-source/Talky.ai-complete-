"""Actual assistant socket handlers with synthetic authority/provider ports."""
import asyncio
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.api.v1.endpoints import assistant_ws as text_api, assistant_voice_ws as voice_api
from app.core.security import assistant_session_watch as watch
from app.core.security.principal import PrincipalUnavailable, assistant_session_context
from app.domain.models.conversation import TranscriptChunk


@pytest_asyncio.fixture
async def authority(monkeypatch):
    from tests.helpers.assistant_runtime import stub_runtime_receipts
    stub_runtime_receipts(monkeypatch)
    state = {"alice": True, "bob": True}
    observed = []
    def forbidden(*_, **__):
        raise AssertionError("No network calls in synthetic socket acceptance")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(watch, "AUTH_RECHECK_SECONDS", 0.01)
    monkeypatch.setattr(watch, "AUTH_CHECK_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(watch, "CLOSE_TIMEOUT_SECONDS", 0.02)

    @asynccontextmanager
    async def acquire(*_, **__):
        yield None

    async def principal(_conn, claims):
        observed.append(dict(claims))
        value = state[claims["sub"]]
        if value == "hang":
            await asyncio.Event().wait()
        if value == "outage":
            raise RuntimeError("synthetic database outage")
        if not value:
            raise PrincipalUnavailable("session_ended")
        return {"tenant_id": "tenant-" + claims["sub"]}

    monkeypatch.setattr(watch, "acquire_with_tenant", acquire)
    monkeypatch.setattr(watch, "load_session_principal", principal)
    monkeypatch.setattr(text_api, "acquire_with_tenant", acquire)
    monkeypatch.setattr(text_api, "load_session_principal", principal)
    monkeypatch.setattr(text_api, "check_assistant_session", AsyncMock())
    monkeypatch.setattr(voice_api, "check_assistant_session", AsyncMock())
    monkeypatch.setattr(text_api, "decode_and_validate_token",
        lambda token: {"sub": token, "sid": "session-" + token})
    monkeypatch.setattr(text_api, "get_tenant_assistant_model", AsyncMock(return_value="synthetic-model"))
    monkeypatch.setattr(voice_api, "get_tenant_assistant_model", AsyncMock(return_value="synthetic-model"))
    try:
        yield SimpleNamespace(state=state, observed=observed)
    finally:
        # Restore sockets before pytest-asyncio creates its Windows cleanup
        # loop (which requires an internal loopback socket pair).
        monkeypatch.undo()


class Socket:
    def __init__(self, user="alice", *, message=False, audio=False, hang_close=False, disconnect_on_close=False):
        self.headers = {}
        self.cookies = {"talky_at": user}
        self.sent, self.closed = [], []
        self.message, self.audio, self.hang_close = message, audio, hang_close
        self.connected = asyncio.Event()
        self.close_started = asyncio.Event()
        self.disconnect_on_close = disconnect_on_close
    async def accept(self):
        pass
    async def send_json(self, value):
        self.sent.append(value)
        if value.get("type") in {"connected", "ready"}:
            self.connected.set()
    async def send_bytes(self, _):
        pass
    async def receive_json(self):
        if self.message:
            self.message = False
            return {"type": "user_message", "content": "Synthetic request"}
        await asyncio.Event().wait()
    async def receive(self):
        if self.disconnect_on_close:
            await self.close_started.wait()
            return {"type": "websocket.disconnect"}
        if self.audio:
            await asyncio.sleep(0.001)
            return {"type": "websocket.receive", "bytes": bytes(1280)}
        await asyncio.Event().wait()
    async def close(self, code=1000, reason=""):
        self.closed.append((code, reason))
        self.close_started.set()
        if self.hang_close:
            await asyncio.Event().wait()


@pytest.mark.parametrize("mode", ["idle", "tokens"])
async def test_text_hold_stops_idle_or_token_only_handler_without_stopping_other_user(authority, monkeypatch, mode):
    monkeypatch.setattr(text_api, "get_db_client", lambda: SimpleNamespace(pool=object()))
    generating, stopped = asyncio.Event(), asyncio.Event()
    async def tokens(**_):
        try:
            while True:
                generating.set()
                yield {"type": "token", "delta": "word"}
                await asyncio.sleep(0.001)
        finally:
            stopped.set()
    monkeypatch.setattr("app.infrastructure.assistant.streaming.stream_assistant_reply", tokens)
    alice, bob = Socket(message=mode == "tokens"), Socket("bob")
    tasks = [asyncio.create_task(text_api.assistant_chat(ws, token=None, conversation_id=None))
             for ws in (alice, bob)]
    try:
        await asyncio.wait_for(asyncio.gather(alice.connected.wait(), bob.connected.wait()), 1)
        if mode == "tokens":
            await asyncio.wait_for(generating.wait(), 1)
        authority.state["alice"] = False
        await asyncio.wait_for(tasks[0], 1)
        assert alice.closed[-1][0] == 1008
        assert not tasks[1].done() and not bob.closed
        if mode == "tokens":
            assert stopped.is_set()
        assert all(claims["sid"] == "session-" + claims["sub"] for claims in authority.observed)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("failure", ["outage", "hang"])
async def test_authority_outage_and_stalled_close_still_stop_owned_handler(authority, monkeypatch, failure):
    monkeypatch.setattr(text_api, "get_db_client", lambda: SimpleNamespace(pool=object()))
    ws = Socket(hang_close=True)
    task = asyncio.create_task(text_api.assistant_chat(ws, token=None, conversation_id=None))
    await asyncio.wait_for(ws.connected.wait(), 1)
    authority.state["alice"] = failure
    await asyncio.wait_for(task, 1)
    assert ws.closed[-1][0] == 1011


@pytest.mark.parametrize("mode", ["audio", "tokens", "initializing", "disconnect_overlap"])
async def test_voice_hold_cancels_owned_audio_generation_and_cleans_both_providers(authority, monkeypatch, mode):
    work, generation_stopped = asyncio.Event(), asyncio.Event()
    cleaned, audio_count = [], []
    class Stt:
        async def initialize(self, _):
            if mode == "disconnect_overlap":
                work.set()
            if mode == "initializing":
                work.set()
                await asyncio.Event().wait()
        async def stream_transcribe(self, audio, **_):
            if mode == "tokens":
                yield TranscriptChunk(text="Synthetic request", is_final=True)
                await asyncio.Event().wait()
            else:
                async for chunk in audio:
                    audio_count.append(len(chunk.data))
                    work.set()
        async def cleanup(self):
            cleaned.append("stt")
    class Tts:
        async def initialize(self, _):
            pass
        async def stream_synthesize(self, **_):
            if False:
                yield None
        async def cleanup(self):
            cleaned.append("tts")
    async def tokens(**_):
        try:
            while True:
                work.set()
                yield {"type": "token", "delta": "word"}
                await asyncio.sleep(0.001)
        finally:
            generation_stopped.set()
    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.DeepgramFluxSTTProvider", Stt)
    monkeypatch.setattr("app.infrastructure.tts.cartesia.CartesiaTTSProvider", Tts)
    monkeypatch.setattr("app.domain.services.credential_resolver.get_credential_resolver",
                        lambda: SimpleNamespace(resolve_with_metadata=AsyncMock(return_value=SimpleNamespace(
                            secret="synthetic", observation_context={"source": "unattributed"}))))
    monkeypatch.setattr("app.infrastructure.assistant.streaming.stream_assistant_reply", tokens)
    ws = Socket(audio=mode == "audio", hang_close=mode == "disconnect_overlap",
                disconnect_on_close=mode == "disconnect_overlap")
    async def session():
        identity = assistant_session_context.set({"sub": "alice", "sid": "session-alice", "tenant_id": "tenant-alice"})
        try:
            await voice_api._run_voice_session(websocket=ws, session_id="synthetic-voice",
                tenant_id="tenant-alice", user_id="alice", conversation_id=None,
                db_client=SimpleNamespace(pool=object()))
        finally:
            assistant_session_context.reset(identity)
    task = asyncio.create_task(session())
    try:
        await asyncio.wait_for(work.wait(), 1)
        authority.state["alice"] = False
        await asyncio.wait_for(task, 1)
        assert ws.closed[-1][0] == 1008 and cleaned == ["stt", "tts"]
        if mode == "tokens":
            assert generation_stopped.is_set()
        if mode == "audio":
            count = len(audio_count)
            await asyncio.sleep(0.02)
            assert count > 0 and len(audio_count) == count
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
