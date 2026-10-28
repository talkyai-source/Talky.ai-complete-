"""Real socket handlers and runtime helper; synthetic DB/provider ports, no network."""
import asyncio
import copy
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import WebSocketDisconnect

from app.api.v1.endpoints import assistant_ws as chat, assistant_voice_ws as voice
from app.core.security import assistant_runtime as receipt, assistant_session_watch as watch
from app.core.security.principal import PrincipalUnavailable
from app.domain.models.conversation import TranscriptChunk
from app.infrastructure.assistant.streaming import stream_assistant_reply as actual_stream


@pytest_asyncio.fixture
async def ports(monkeypatch):
    claims = {"sub": str(uuid4()), "sid": str(uuid4()), "tenant_id": str(uuid4())}
    state = SimpleNamespace(claims=claims, rows=[], order=[], allowed=True,
        failure=None, principal_tenant=claims["tenant_id"], stopped=asyncio.Event(),
        cleanup_started=asyncio.Event(), cleanup_release=asyncio.Event(), cleanup_mode=None,
        work=asyncio.Event(), generation_stopped=asyncio.Event(), audio=[], provider_started=[],
        turn_mode="idle", cleanup_calls=[])
    def forbidden(*_, **__):
        raise AssertionError("No external socket in synthetic runtime receipt test")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)

    class Conn:
        async def fetchval(self, query):
            assert query == "SELECT clock_timestamp()"
            return datetime.now(timezone.utc)
    @asynccontextmanager
    async def acquire(*_, **__):
        state.order.append("begin")
        yield Conn()
        state.order.append("commit")
        if state.failure == "commit":
            raise RuntimeError("Synthetic commit ambiguity")
    class Audit:
        def __init__(self, _):
            pass
        async def lock_transaction(self, _):
            state.order.append("audit_lock")
        async def log_in_transaction(self, _, **kwargs):
            state.order.append(kwargs["action"])
            if state.failure == kwargs["action"]:
                raise RuntimeError("Synthetic required audit failure")
            row = {**kwargs, "event_id": str(uuid4()), "event_type": kwargs["event_type"].value}
            state.rows.append(row)
            return row["event_id"]
    async def admin_lock(_):
        state.order.append("admin_lock")
    async def principal(_, observed):
        assert observed == claims
        state.order.append("principal")
        if not state.allowed:
            raise PrincipalUnavailable("session_ended")
        return {"tenant_id": state.principal_tenant, "role": "user"}
    async def check(*_):
        if not state.allowed:
            raise PrincipalUnavailable("session_ended")
    for module in (receipt, watch, chat, voice):
        if hasattr(module, "acquire_with_tenant"):
            monkeypatch.setattr(module, "acquire_with_tenant", acquire)
        if hasattr(module, "load_session_principal"):
            monkeypatch.setattr(module, "load_session_principal", principal)
    monkeypatch.setattr(receipt, "AuditLogger", Audit)
    monkeypatch.setattr(receipt, "lock_user_administration", admin_lock)
    monkeypatch.setattr(receipt, "CLEANUP_TIMEOUT_SECONDS", .03)
    monkeypatch.setattr(watch, "AUTH_RECHECK_SECONDS", .02)
    monkeypatch.setattr(watch, "AUTH_CHECK_TIMEOUT_SECONDS", .03)
    monkeypatch.setattr(watch, "CLOSE_TIMEOUT_SECONDS", .02)
    for module in (chat, voice):
        monkeypatch.setattr(module, "decode_and_validate_token", lambda _: dict(claims))
        monkeypatch.setattr(module, "get_db_client", lambda: SimpleNamespace(pool=object()))
        monkeypatch.setattr(module, "get_tenant_assistant_model", AsyncMock(return_value="synthetic"))
        monkeypatch.setattr(module, "check_assistant_session", check)
    monkeypatch.setattr(voice, "_semaphore", None, raising=False)

    async def cleanup(name):
        state.cleanup_calls.append(name)
        state.cleanup_started.set()
        if name == "stt" and state.cleanup_mode in {"delay", "timeout"}:
            await state.cleanup_release.wait()
        if name == "stt" and state.cleanup_mode == "error":
            raise RuntimeError("Synthetic cleanup failure")
    class Stt:
        async def initialize(self, _):
            state.provider_started.append("stt")
            assert any(row["action"] == receipt.REGISTER_ACTION for row in state.rows)
        async def stream_transcribe(self, audio, **_):
            if state.turn_mode == "tokens":
                yield TranscriptChunk(text="synthetic", is_final=True)
                await asyncio.Event().wait()
            else:
                async for chunk in audio:
                    state.audio.append(len(chunk.data))
                    state.work.set()
        async def cleanup(self):
            await cleanup("stt")
    class Tts:
        async def initialize(self, _):
            state.provider_started.append("tts")
        async def stream_synthesize(self, **_):
            if False:
                yield None
        async def cleanup(self):
            await cleanup("tts")
    async def stream(**_):
        assert any(row["action"] == receipt.REGISTER_ACTION for row in state.rows)
        state.provider_started.append("llm")
        try:
            while True:
                state.work.set()
                yield {"type": "token", "delta": "synthetic"}
                await asyncio.sleep(.001)
        finally:
            state.generation_stopped.set()
    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.DeepgramFluxSTTProvider", Stt)
    monkeypatch.setattr("app.infrastructure.tts.cartesia.CartesiaTTSProvider", Tts)
    monkeypatch.setattr("app.domain.services.credential_resolver.get_credential_resolver",
        lambda: SimpleNamespace(resolve_with_metadata=AsyncMock(return_value=SimpleNamespace(
            secret="synthetic", observation_context={}))))
    monkeypatch.setattr("app.infrastructure.assistant.streaming.stream_assistant_reply", stream)
    try:
        yield state
    finally:
        monkeypatch.undo()


class Socket:
    headers = {}
    cookies = {"talky_at": "synthetic"}
    def __init__(self, *, message=False, binary=False, greeting_error=False):
        self.message, self.binary, self.greeting_error = message, binary, greeting_error
        self.ready, self.disconnect = asyncio.Event(), asyncio.Event()
        self.closed, self.sent = [], []
    async def accept(self):
        pass
    async def close(self, **kwargs):
        self.closed.append(kwargs)
    async def send_json(self, value):
        if value.get("type") in {"connected", "ready"}:
            self.ready.set()
        if self.greeting_error and value.get("type") == "assistant_message":
            raise ValueError("Synthetic greeting transport error")
        self.sent.append(value)
    async def send_bytes(self, _):
        pass
    async def receive_json(self):
        if self.message:
            self.message = False
            return {"type": "user_message", "content": "synthetic"}
        await self.disconnect.wait()
        raise WebSocketDisconnect()
    async def receive(self):
        if self.binary and not self.disconnect.is_set():
            await asyncio.sleep(.001)
            return {"type": "websocket.receive", "bytes": bytes(1280)}
        await self.disconnect.wait()
        return {"type": "websocket.disconnect"}


async def run_handler(kind, ws):
    handler = chat.assistant_chat if kind == "chat" else voice.assistant_voice
    await handler(ws, token=None, conversation_id=None)


def stops(ports):
    return [row for row in ports.rows if row["action"] == receipt.STOP_ACTION]


async def test_registration_is_committed_after_lock_order_and_current_authority(ports):
    runtime = await receipt.AssistantRuntime.register(object(), ports.claims, endpoint_kind="chat")
    assert ports.order == ["begin", "audit_lock", "admin_lock", "principal", receipt.REGISTER_ACTION, "commit"]
    assert runtime.registration_event_id == ports.rows[0]["event_id"]
    assert set(runtime.metadata) == {"version", "runtime_id", "user_id", "tenant_id", "security_session_id",
                                    "endpoint_kind", "owner_instance_id", "registered_at"}


@pytest.mark.parametrize("kind", ["chat", "voice"])
@pytest.mark.parametrize("failure", [receipt.REGISTER_ACTION, "commit", "tenant_mismatch"])
async def test_registration_failure_prevents_actual_handler_provider_work(ports, kind, failure):
    ports.failure = failure
    if failure == "tenant_mismatch":
        ports.principal_tenant = str(uuid4())
    ws = Socket(message=True, binary=True)
    await run_handler(kind, ws)
    assert not ports.provider_started and not stops(ports)
    assert ws.closed[-1]["code"] in {1008, 1011}


@pytest.mark.parametrize("kind,mode", [("chat", "idle"), ("chat", "tokens"), ("voice", "audio"), ("voice", "tokens")])
async def test_hold_records_stop_only_after_actual_idle_stream_binary_work_ends(ports, kind, mode):
    ports.turn_mode = mode
    ws = Socket(message=mode == "tokens", binary=mode == "audio")
    task = asyncio.create_task(run_handler(kind, ws))
    try:
        await asyncio.wait_for(ws.ready.wait(), 1)
        if mode != "idle":
            await asyncio.wait_for(ports.work.wait(), 1)
        assert not stops(ports)
        ports.allowed = False
        await asyncio.wait_for(task, 1)
        assert len(stops(ports)) == 1
        assert receipt.stop_receipt_matches(ports.rows[0], stops(ports)[0])
        assert stops(ports)[0]["metadata"]["close_frame_status"] == "sent"
        if mode == "tokens":
            assert ports.generation_stopped.is_set()
        if kind == "voice":
            assert ports.cleanup_calls == ["stt", "tts"]
        count = len(ports.audio)
        await asyncio.sleep(.01)
        assert len(ports.audio) == count
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("mode", ["delay", "timeout", "error"])
async def test_voice_provider_cleanup_receipt_waits_or_remains_unknown(ports, mode):
    ports.cleanup_mode = mode
    ws = Socket()
    task = asyncio.create_task(run_handler("voice", ws))
    await asyncio.wait_for(ws.ready.wait(), 1)
    ws.disconnect.set()
    await asyncio.wait_for(ports.cleanup_started.wait(), 1)
    assert not stops(ports)
    if mode == "delay":
        ports.cleanup_release.set()
    await asyncio.wait_for(task, 1)
    assert bool(stops(ports)) is (mode == "delay")
    assert ports.cleanup_calls == ["stt", "tts"]


async def test_greeting_send_failure_still_drains_created_tasks_before_receipt(ports):
    await run_handler("voice", Socket(greeting_error=True))
    assert ports.cleanup_calls == ["stt", "tts"] and len(stops(ports)) == 1


async def test_final_audit_failure_does_not_replay_stopped_work(ports):
    ports.failure = receipt.STOP_ACTION
    ws = Socket(message=True)
    task = asyncio.create_task(run_handler("chat", ws))
    await asyncio.wait_for(ports.work.wait(), 1)
    ports.allowed = False
    await asyncio.wait_for(task, 1)
    assert ports.generation_stopped.is_set() and not stops(ports)
    assert ports.provider_started == ["llm"]


async def test_cleanup_cancellation_keeps_missing_receipt_unknown(ports):
    ports.cleanup_mode = "delay"
    ws = Socket()
    task = asyncio.create_task(run_handler("voice", ws))
    await asyncio.wait_for(ws.ready.wait(), 1)
    ws.disconnect.set()
    await asyncio.wait_for(ports.cleanup_started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not stops(ports)


@pytest.mark.parametrize("field,value", [("runtime_id", str(uuid4())), ("registration_event_id", str(uuid4())),
    ("security_session_id", str(uuid4())), ("owner_instance_id", str(uuid4())),
    ("cleanup_status", "unknown"), ("owner_stopped", False), ("stopped_at", "invalid")])
async def test_stop_receipt_requires_exact_registration_and_completed_cleanup(ports, field, value):
    runtime = await receipt.AssistantRuntime.register(object(), ports.claims, endpoint_kind="voice")
    await runtime.record_stopped()
    assert receipt.stop_receipt_matches(*ports.rows)
    wrong = copy.deepcopy(ports.rows[1])
    wrong["metadata"][field] = value
    assert not receipt.stop_receipt_matches(ports.rows[0], wrong)


@pytest.mark.parametrize("failure", [None, "error", "timeout"])
async def test_actual_streaming_loop_closes_exact_sdk_stream_before_owner_receipt(ports, monkeypatch, failure):
    from app.infrastructure.assistant import streaming
    # The ports fixture replaced generation for handler tests; restore the
    # actual implementation for this SDK transport boundary reproduction.
    closed = []
    class SdkStream:
        def __aiter__(self):
            return self
        async def __anext__(self):
            return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="x", tool_calls=None))])
        async def close(self):
            if failure == "timeout":
                await asyncio.Event().wait()
            if failure == "error":
                raise RuntimeError("Synthetic SDK close failure")
            closed.append(self)
    sdk_stream = SdkStream()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=AsyncMock(return_value=sdk_stream))))
    monkeypatch.setattr(streaming, "get_assistant_client", lambda _: (client, lambda args: args))
    runtime = await receipt.AssistantRuntime.register(object(), ports.claims, endpoint_kind="chat")
    context = receipt.runtime_context.set(runtime)
    try:
        async with receipt.owned_stream(actual_stream(chat_messages=[{"role": "user", "content": "hi"}],
            tenant_id=ports.claims["tenant_id"], user_id=ports.claims["sub"],
            conversation_id=None, db_client=None, model=None)) as reply:
            assert (await anext(reply))["type"] == "token"
        await runtime.record_stopped()
        assert bool(stops(ports)) is (failure is None)
        assert closed == ([sdk_stream] if failure is None else [])
    finally:
        receipt.runtime_context.reset(context)


async def test_noncooperative_owned_task_never_receives_stopped_proof(ports):
    runtime = await receipt.AssistantRuntime.register(object(), ports.claims, endpoint_kind="voice")
    started, release = asyncio.Event(), asyncio.Event()
    async def work():
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
    task = runtime.own_task(work())
    await started.wait()
    try:
        await runtime.stop_tasks()
        await runtime.record_stopped()
        assert not task.done() and not stops(ports)
    finally:
        release.set()
        await task


async def test_finished_tasks_do_not_accumulate_for_long_lived_runtime(ports):
    runtime = await receipt.AssistantRuntime.register(object(), ports.claims, endpoint_kind="voice")
    for _ in range(100):
        await runtime.own_task(asyncio.sleep(0))
    await asyncio.sleep(0)
    assert not runtime.owned_tasks
