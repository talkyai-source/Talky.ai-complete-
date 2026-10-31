"""OP02 red/green inventory: real ownership methods, synthetic transports only.

These are ownership/admission controls, not provider/account limit evidence.
The original red output is preserved under docs/sessions/artifacts/op02.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services import global_concurrency as global_slots
from app.domain.services.resilient_stt import ResilientSTTProvider
from app.infrastructure.providers.provider_concurrency import (
    ProviderGuardTimeout,
    get_provider_guard,
    reset_guards_for_tests,
)
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from tests.unit.test_global_concurrency import _FakeRedis


class Socket:
    def __init__(self):
        self.closed = False
        self.send_started = asyncio.Event()

    async def send(self, _payload):
        self.send_started.set()

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()


@pytest.fixture
def transport(monkeypatch):
    reset_guards_for_tests()
    monkeypatch.setenv("DEEPGRAM_MAX_CONCURRENT", "1")
    guard = get_provider_guard("deepgram")
    guard._wait_timeout = .025
    sockets = []

    async def connect(*_args, **_kwargs):
        socket = Socket()
        sockets.append(socket)
        return socket

    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.websockets.connect", connect)
    yield guard, sockets
    reset_guards_for_tests()


async def provider():
    result = DeepgramFluxSTTProvider()
    await result.initialize({"api_key": "synthetic-no-provider-access"})
    return result


async def waiting_audio():
    await asyncio.Event().wait()
    yield  # pragma: no cover - async iterator whose read stays in flight


async def drain(stream):
    async for _chunk in stream:
        pass


async def cancel(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_flux_prewarm_obeys_shared_deepgram_one_socket_limit(transport):
    _guard, sockets = transport
    a, b = await provider(), await provider()
    try:
        await a.pre_connect("a")
        try:
            await b.pre_connect("b")
        except ProviderGuardTimeout:
            pass
        assert sum(not s.closed for s in sockets) <= 1
    finally:
        await a.cleanup()
        await b.cleanup()


@pytest.mark.asyncio
async def test_flux_prewarm_owns_one_slot_until_cleanup(transport):
    guard, _sockets = transport
    primary = await provider()
    wrapper = ResilientSTTProvider(primary)
    try:
        await wrapper.pre_connect("a")
        assert guard.in_flight == 1
    finally:
        await wrapper.cleanup()
    assert guard.in_flight == 0


@pytest.mark.asyncio
async def test_flux_cold_stream_owns_one_slot_until_cancellation(transport):
    guard, sockets = transport
    primary = await provider()
    wrapper = ResilientSTTProvider(primary)
    task = asyncio.create_task(drain(wrapper.stream_transcribe(waiting_audio(), call_id="a")))
    try:
        for _ in range(50):
            if sockets:
                break
            await asyncio.sleep(0)
        assert sockets, "synthetic socket must actually be opened"
        await asyncio.wait_for(sockets[0].send_started.wait(), 1)
        assert guard.in_flight == 1
    finally:
        await cancel(task)
        await wrapper.cleanup()
    assert guard.in_flight == 0
    assert sockets[0].closed


@pytest.mark.asyncio
async def test_prewarm_handoff_reuses_socket_and_cancel_closes_it(transport):
    _guard, sockets = transport
    primary = await provider()
    wrapper = ResilientSTTProvider(primary)
    await wrapper.pre_connect("a")
    task = asyncio.create_task(drain(wrapper.stream_transcribe(waiting_audio(), call_id="a")))
    try:
        await asyncio.wait_for(sockets[0].send_started.wait(), 1)
        assert len(sockets) == 1
        assert not primary._pre_connections
    finally:
        await cancel(task)
        await wrapper.cleanup()
    assert sockets[0].closed


@pytest.mark.asyncio
async def test_repeated_prewarm_does_not_orphan_first_socket(transport):
    _guard, sockets = transport
    primary = await provider()
    await primary.pre_connect("same-call")
    await primary.pre_connect("same-call")
    await primary.cleanup()
    assert all(s.closed for s in sockets), "replaced prewarm connection was never closed"


@pytest.mark.asyncio
async def test_cleanup_during_handshake_does_not_adopt_socket_after_shutdown(transport, monkeypatch):
    _guard, _sockets = transport
    entered, finish = asyncio.Event(), asyncio.Event()
    socket = Socket()

    async def connect(*_args, **_kwargs):
        entered.set()
        try:
            await finish.wait()
        except asyncio.CancelledError:
            # The handshake completed as shutdown cancelled the caller.
            # Even a transport suppressing cancellation must not be adopted.
            pass
        return socket

    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.websockets.connect", connect)
    primary = await provider()
    task = asyncio.create_task(primary.pre_connect("a"))
    await asyncio.wait_for(entered.wait(), 1)
    await primary.cleanup()
    finish.set()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
    try:
        assert not primary._pre_connections, "late handshake repopulated closed provider"
        assert socket.closed
    finally:
        await primary.cleanup()


@pytest.mark.asyncio
async def test_handshake_failure_is_visible_to_strict_prewarm_caller(transport, monkeypatch):
    async def connect(*_args, **_kwargs):
        raise ConnectionError("synthetic refusal")

    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.websockets.connect", connect)
    wrapper = ResilientSTTProvider(await provider())
    try:
        with pytest.raises(ConnectionError):
            await wrapper.pre_connect("a")
    finally:
        await wrapper.cleanup()


@pytest.mark.asyncio
async def test_cancellation_during_handshake_leaves_no_owned_socket(transport, monkeypatch):
    guard, _sockets = transport
    entered = asyncio.Event()

    async def connect(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.websockets.connect", connect)
    primary = await provider()
    wrapper = ResilientSTTProvider(primary)
    task = asyncio.create_task(wrapper.pre_connect("a"))
    await asyncio.wait_for(entered.wait(), 1)
    await cancel(task)
    await wrapper.cleanup()
    assert not primary._pre_connections
    assert guard.in_flight == 0


@pytest.mark.asyncio
async def test_cancelled_guard_waiter_never_connects_or_releases_anothers_slot(transport):
    guard, sockets = transport
    guard._wait_timeout = 2
    a, b = await provider(), await provider()
    await a.pre_connect("a")
    second = asyncio.create_task(b.pre_connect("b"))
    try:
        for _ in range(50):
            if guard.snapshot()["waiting"]:
                break
            await asyncio.sleep(0)
        assert guard.snapshot()["waiting"] == 1
        await cancel(second)
        assert len(sockets) == 1 and guard.in_flight == 1
        assert guard.snapshot()["waiting"] == 0
    finally:
        await cancel(second)
        await a.cleanup()
        await b.cleanup()
    assert guard.in_flight == 0


@pytest.mark.asyncio
async def test_concurrent_prewarm_and_stream_handoff_use_one_socket(transport, monkeypatch):
    guard, _sockets = transport
    entered, finish = asyncio.Event(), asyncio.Event()
    socket = Socket()
    opens = []

    async def connect(*_args, **_kwargs):
        opens.append(True)
        entered.set()
        await finish.wait()
        return socket

    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.websockets.connect", connect)
    primary = await provider()
    prewarm = asyncio.create_task(primary.pre_connect("a"))
    await asyncio.wait_for(entered.wait(), 1)
    stream = asyncio.create_task(drain(primary.stream_transcribe(waiting_audio(), call_id="a")))
    finish.set()
    try:
        await asyncio.wait_for(prewarm, 1)
        await asyncio.wait_for(socket.send_started.wait(), 1)
        assert len(opens) == 1 and guard.in_flight == 1
    finally:
        await cancel(stream)
        await primary.cleanup()
    assert socket.closed and guard.in_flight == 0


@pytest.mark.asyncio
async def test_cold_stream_then_late_prewarm_cannot_recache_adopted_socket(transport, monkeypatch):
    guard, _sockets = transport
    entered, finish = asyncio.Event(), asyncio.Event()
    socket = Socket()

    async def connect(*_args, **_kwargs):
        entered.set()
        await finish.wait()
        return socket

    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.websockets.connect", connect)
    primary = await provider()
    stream = asyncio.create_task(drain(primary.stream_transcribe(waiting_audio(), call_id="a")))
    await asyncio.wait_for(entered.wait(), 1)
    prewarm = asyncio.create_task(primary.pre_connect("a"))
    await asyncio.sleep(0)
    finish.set()
    try:
        await asyncio.wait_for(prewarm, 1)
        await asyncio.wait_for(socket.send_started.wait(), 1)
        assert not primary._pre_connections, "late prewarm re-cached active stream's socket"
        assert guard.in_flight == 1
    finally:
        await cancel(stream)
        await primary.cleanup()
    assert guard.in_flight == 0


@pytest.mark.asyncio
async def test_failed_handshake_releases_permit_for_later_valid_call(transport, monkeypatch):
    guard, _sockets = transport
    socket = Socket()
    responses = iter([ConnectionError("synthetic 429 or connect failure"), socket])

    async def connect(*_args, **_kwargs):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.websockets.connect", connect)
    primary = await provider()
    with pytest.raises(ConnectionError):
        await primary.pre_connect("a")
    assert guard.in_flight == 0
    await primary.pre_connect("b")
    assert guard.in_flight == 1
    await primary.cleanup()
    assert socket.closed and guard.in_flight == 0


@pytest.mark.asyncio
async def test_cancelled_close_waiter_does_not_free_permit_before_transport_finishes(transport):
    guard, sockets = transport
    primary = await provider()
    await primary.pre_connect("a")
    entered, finish = asyncio.Event(), asyncio.Event()
    closes = []

    async def close():
        closes.append(True)
        entered.set()
        await finish.wait()
        sockets[0].closed = True

    sockets[0].close = close
    first = asyncio.create_task(primary.cleanup())
    await asyncio.wait_for(entered.wait(), 1)
    await cancel(first)
    assert guard.in_flight == 1, "pending transport close still owns capacity"
    second = asyncio.create_task(primary.cleanup())
    finish.set()
    await asyncio.wait_for(second, 1)
    assert len(closes) == 1 and sockets[0].closed and guard.in_flight == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("redis", [None, SimpleNamespace(pipeline=lambda **_kw: (_ for _ in ()).throw(ConnectionError("synthetic Redis outage")))])
async def test_actual_outbound_postanswer_admission_rejects_without_global_proof(monkeypatch, redis):
    # Stop at the first downstream boundary, before any actual provider or DB work.
    import app.api.v1.dependencies  # noqa: F401
    from app.core import container
    from app.domain.services import call_status
    from app.domain.services.telephony import lifecycle

    class ReachedVoiceSetup(BaseException):
        pass

    reached = []

    def voice_setup():
        reached.append(True)
        raise ReachedVoiceSetup()

    state = SimpleNamespace(strict_ownership_active=False, voice_session_count=lambda: 0,
                            has_ringing_warmup=lambda _cid: False,
                            get_voice_session=lambda _cid: None, get_first_speaker=lambda _cid: None)
    monkeypatch.setenv("POD_ID", "synthetic-pod")
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "_get_orchestrator", voice_setup)
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=True, redis=redis, db_pool=None))
    monkeypatch.setattr(call_status, "record_call_state_by_provider_id", AsyncMock())
    reject = AsyncMock()
    monkeypatch.setattr(lifecycle, "_reject_overcap_call", reject)
    try:
        await lifecycle._on_new_call("synthetic-outbound")
    except ReachedVoiceSetup:
        pass
    assert not reached, "actual outbound admission continued without shared capacity proof"
    reject.assert_awaited_once_with("synthetic-outbound")


@pytest.mark.asyncio
async def test_reconcile_cannot_remove_an_in_progress_capacity_claim():
    class BlockFirstLeaseWrite(_FakeRedis):
        def __init__(self):
            super().__init__()
            self.entered, self.finish = asyncio.Event(), asyncio.Event()

        async def eval(self, script, numkeys, active_key, lease_key, *args):
            result = await super().eval(script, numkeys, active_key, lease_key, *args)
            if script == global_slots._ACQUIRE_SCRIPT and lease_key == "telephony:lease:first":
                self.entered.set()
                await self.finish.wait()
            return result

    redis = BlockFirstLeaseWrite()
    first = asyncio.create_task(global_slots.acquire_lease(redis, call_id="first", pod_id="p", cap=1, fail_closed=True))
    await asyncio.wait_for(redis.entered.wait(), 1)
    try:
        await global_slots.reconcile_orphans(redis)
        second = await global_slots.acquire_lease(redis, call_id="second", pod_id="q", cap=1, fail_closed=True)
        redis.finish.set()
        first_result = await asyncio.wait_for(first, 1)
        assert not (first_result.acquired and second.acquired), "cap=1 admitted two distinct calls around reconciliation"
    finally:
        redis.finish.set()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_reconcile_rechecks_expiry_before_removing_a_refreshed_lease():
    class DelayedExistenceReply(_FakeRedis):
        def __init__(self):
            super().__init__()
            self.observed, self.reply = asyncio.Event(), asyncio.Event()

        async def exists(self, key):
            old_result = await super().exists(key)
            self.observed.set()
            await self.reply.wait()
            return old_result

    redis = DelayedExistenceReply()
    await global_slots.acquire_lease(redis, call_id="live", pod_id="p", cap=1, fail_closed=True)
    redis.advance(601)
    reconcile = asyncio.create_task(global_slots.reconcile_orphans(redis))
    await asyncio.wait_for(redis.observed.wait(), 1)
    try:
        await global_slots.refresh_lease(redis, call_id="live")
        redis.reply.set()
        await asyncio.wait_for(reconcile, 1)
        other = await global_slots.acquire_lease(redis, call_id="other", pod_id="q", cap=1, fail_closed=True)
        assert not other.acquired, "stale EXISTS reply removed a refreshed live lease from the cap"
    finally:
        redis.reply.set()
        await asyncio.gather(reconcile, return_exceptions=True)


@pytest.mark.asyncio
async def test_strict_global_unavailable_and_healthy_cap_controls():
    missing = await global_slots.acquire_lease(None, call_id="missing", pod_id="p", cap=1, fail_closed=True)
    assert not missing.acquired
    redis = _FakeRedis()
    first = await global_slots.acquire_lease(redis, call_id="one", pod_id="p", cap=1, fail_closed=True)
    repeated = await global_slots.acquire_lease(redis, call_id="one", pod_id="p", cap=1, fail_closed=True)
    second = await global_slots.acquire_lease(redis, call_id="two", pod_id="p", cap=1, fail_closed=True)
    assert first.acquired and repeated.acquired and not second.acquired
    await global_slots.release_lease(redis, call_id="one")
    assert (await global_slots.acquire_lease(redis, call_id="two", pod_id="p", cap=1, fail_closed=True)).acquired


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_failed_same_id_acquire_cannot_delete_already_live_lease(failure):
    redis = _FakeRedis()
    assert (await global_slots.acquire_lease(redis, call_id="live", pod_id="owner", cap=1, fail_closed=True)).acquired
    original_value = redis.strings[global_slots._lease_key("live")]
    entered = asyncio.Event()
    real_eval = redis.eval

    async def broken_eval(script, *args):
        if script == global_slots._ACQUIRE_SCRIPT:
            entered.set()
            if failure == "error":
                raise ConnectionError("synthetic failure before commit")
            await asyncio.Event().wait()
        return await real_eval(script, *args)

    redis.eval = broken_eval
    task = asyncio.create_task(global_slots.acquire_lease(redis, call_id="live", pod_id="retry", cap=1, fail_closed=True))
    await asyncio.wait_for(entered.wait(), 1)
    if failure == "cancel":
        await cancel(task)
    else:
        assert not (await task).acquired
    assert await global_slots.current_count(redis) == 1
    assert redis.strings[global_slots._lease_key("live")] == original_value
