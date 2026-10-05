"""Real lifecycle/warmup methods; local registry and fake provider/Redis I/O."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.api.v1.dependencies  # noqa: F401
from app.domain.services import global_concurrency as slots
from app.domain.services.telephony import lifecycle, prewarm
from app.domain.services.telephony.state_backend import LocalOnlyStateBackend
from tests.unit.test_global_concurrency import _FakeRedis


@pytest.fixture
def local_state(monkeypatch):
    from app.api.v1.endpoints import telephony_bridge

    monkeypatch.setattr(telephony_bridge, "_telephony_sessions", {})
    monkeypatch.setattr(lifecycle, "_ended_calls_in_flight", set())
    return LocalOnlyStateBackend()


@pytest.mark.asyncio
@pytest.mark.parametrize("end_boundary", ["ended_marker", "setup_error_pop", "ownership_loss_pop"])
async def test_old_watchdog_snapshot_does_not_refresh_ended_or_removed_session(local_state, end_boundary):
    redis = _FakeRedis()
    session = SimpleNamespace()
    local_state.set_voice_session("old", session)
    await slots.acquire_lease(redis, call_id="old", pod_id="p", cap=1, fail_closed=True)
    snapshot = local_state.iter_voice_session_items()
    if end_boundary == "ended_marker":
        lifecycle._ended_calls_in_flight.add("old")
    else:
        local_state.pop_voice_session("old")
    await lifecycle._serialized_global_release(slots.release_lease, redis, "old")
    for call_id, captured in snapshot:
        await lifecycle._refresh_current_global_lease(redis, call_id, captured, local_state)
    assert await slots.current_count(redis) == 0
    assert await slots.reconcile_orphans(redis) == 0


@pytest.mark.asyncio
async def test_terminal_release_waits_for_already_in_flight_local_refresh(local_state, monkeypatch):
    redis = _FakeRedis()
    session = SimpleNamespace()
    local_state.set_voice_session("live", session)
    await slots.acquire_lease(redis, call_id="live", pod_id="p", cap=1, fail_closed=True)
    entered, finish = asyncio.Event(), asyncio.Event()
    refresh = slots.refresh_lease

    async def blocked_refresh(client, *, call_id):
        entered.set()
        await finish.wait()
        await refresh(client, call_id=call_id)

    monkeypatch.setattr(slots, "refresh_lease", blocked_refresh)
    refreshing = asyncio.create_task(lifecycle._refresh_current_global_lease(redis, "live", session, local_state))
    await asyncio.wait_for(entered.wait(), 1)
    local_state.pop_voice_session("live")
    releasing = asyncio.create_task(lifecycle._serialized_global_release(slots.release_lease, redis, "live"))
    await asyncio.sleep(0)
    assert not releasing.done()
    finish.set()
    await asyncio.wait_for(asyncio.gather(refreshing, releasing), 1)
    assert await slots.current_count(redis) == 0


@pytest.mark.asyncio
async def test_live_snapshot_can_restore_its_own_missing_redis_lease(local_state):
    redis = _FakeRedis()
    session = SimpleNamespace()
    local_state.set_voice_session("live", session)
    await lifecycle._refresh_current_global_lease(redis, "live", session, local_state)
    assert await slots.current_count(redis) == 1
    assert await redis.exists(slots._lease_key("live")) == 1


@pytest.mark.asyncio
async def test_cancelled_actual_pre_originate_warmup_cleans_already_open_flux(monkeypatch):
    from app.domain.services.resilient_stt import ResilientSTTProvider
    from app.domain.services import tenant_ai_config_resolver, voice_tuning
    from app.infrastructure.providers.provider_concurrency import get_provider_guard, reset_guards_for_tests
    from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
    from app.services.scripts.knowledge import session_inject
    from tests.unit.test_op02_provider_boundary_inventory import Socket

    reset_guards_for_tests()
    socket = Socket()
    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.websockets.connect", AsyncMock(return_value=socket))
    stt = DeepgramFluxSTTProvider()
    await stt.initialize({"api_key": "synthetic"})
    wrapper = ResilientSTTProvider(stt)
    session = SimpleNamespace(
        call_id="synthetic-prewarm", call_session=SimpleNamespace(call_id="synthetic-prewarm"),
        stt_provider=wrapper, tts_provider=SimpleNamespace(), llm_provider=SimpleNamespace(),
    )
    cleanup = AsyncMock(side_effect=lambda _session: wrapper.cleanup())
    # AsyncMock does not await a coroutine returned by a synchronous effect.
    async def end_session(_session):
        await wrapper.cleanup()
    cleanup.side_effect = end_session
    orchestrator = SimpleNamespace(create_voice_session=AsyncMock(return_value=session), end_session=cleanup)
    monkeypatch.setattr(prewarm, "_get_orchestrator", lambda: orchestrator)
    monkeypatch.setattr(prewarm, "_build_telephony_session_config", lambda **_kw: SimpleNamespace(tts_provider_type="cartesia", tts_model="synthetic"))
    monkeypatch.setattr(prewarm, "_start_opening_ladder_generation", lambda *_args: None)
    monkeypatch.setattr(voice_tuning, "get_voice_tuning_resolver", lambda: SimpleNamespace(for_tenant_async=AsyncMock(return_value={})))
    monkeypatch.setattr(tenant_ai_config_resolver, "get_tenant_ai_config_resolver", lambda: SimpleNamespace(for_tenant_async=AsyncMock(return_value={})))
    monkeypatch.setattr(session_inject, "apply_campaign_knowledge", AsyncMock())
    monkeypatch.setattr(prewarm, "warm_llm_stream", AsyncMock())
    blocked = asyncio.Event()

    async def pending_tts(_session):
        blocked.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(prewarm, "warm_tts_inference_path", pending_tts)
    task = asyncio.create_task(prewarm.prepare_prewarmed_session(
        first_speaker="agent", campaign_id="synthetic", agent_name=None,
        container=SimpleNamespace(), campaign_row={"tenant_id": "synthetic-tenant"},
    ))
    try:
        await asyncio.wait_for(blocked.wait(), 1)
        for _ in range(50):
            if stt._pre_connections:
                break
            await asyncio.sleep(0)
        assert stt._pre_connections, "Flux must be fully open before cancelling another warmup"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        cleanup.assert_awaited_once_with(session)
        assert socket.closed and get_provider_guard("deepgram").in_flight == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await wrapper.cleanup()
        reset_guards_for_tests()
