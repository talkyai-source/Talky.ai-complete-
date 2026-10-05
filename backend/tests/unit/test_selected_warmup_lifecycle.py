"""Selected outbound sessions survive ringing until provider absence is known."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import socket

import pytest

from app.api.v1.endpoints import telephony_bridge
from app.domain.services.telephony import lifecycle
from app.domain.services.telephony.state_backend import LocalOnlyStateBackend


@pytest.fixture(autouse=True)
async def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Warmup regression attempted network access")
    with monkeypatch.context() as network:
        network.setattr(socket.socket, "connect", blocked)
        network.setattr(socket.socket, "connect_ex", blocked)
        network.setattr(socket, "getaddrinfo", blocked)
        yield


@pytest.fixture
async def warmup(monkeypatch):
    for name in ("_telephony_sessions", "_ringing_warmups", "_ringing_warmup_created_at",
                 "_ringing_events", "_gateway_session_to_call_id", "_early_audio_buffers"):
        monkeypatch.setattr(telephony_bridge, name, {})
    state = LocalOnlyStateBackend()
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "_zombie_channel_ticks", {})
    monkeypatch.setattr(lifecycle, "_ended_calls_in_flight", set())
    monkeypatch.setattr(lifecycle, "recover_orphaned_calls", AsyncMock())
    monkeypatch.setattr("app.core.container.get_container", lambda: NS(is_initialized=False, redis=None))
    monkeypatch.setenv("POD_ID", "offline-warmup")
    saved = NS(config=NS(pipeline_mode="realtime", realtime_voice="ash"), call_session=NS())
    end = AsyncMock()
    orchestrator = NS(end_session=end, _active_sessions={})
    monkeypatch.setattr(lifecycle, "_get_orchestrator", lambda: orchestrator)
    adapter = NS(name="asterisk", list_active_channel_ids=AsyncMock(return_value={"call"}),
                 hangup=AsyncMock())
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    state.set_ringing_warmup("call", saved, None)
    state.set_ringing_started_at("call", asyncio.get_running_loop().time() - 181)
    state.set_ringing_event("call", asyncio.Event())
    state.set_first_speaker("call", "agent")
    return NS(state=state, saved=saved, end=end, adapter=adapter, orchestrator=orchestrator)


async def ticks(monkeypatch, count=1):
    sleeps = 0
    async def tick(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > count:
            raise asyncio.CancelledError()
    with monkeypatch.context() as clock:
        clock.setattr(lifecycle.asyncio, "sleep", tick)
        await lifecycle._session_watchdog()


@pytest.mark.parametrize("presence", ["live", "unknown", "exception", "timeout"])
async def test_long_ringing_keeps_selected_session_until_absence_proven(monkeypatch, warmup, presence):
    if presence == "unknown":
        warmup.adapter.list_active_channel_ids.return_value = None
    elif presence in {"exception", "timeout"}:
        warmup.adapter.list_active_channel_ids.side_effect = (
            TimeoutError("synthetic ARI timeout") if presence == "timeout" else RuntimeError("synthetic ARI outage")
        )
    await ticks(monkeypatch)
    assert warmup.state.get_ringing_warmup("call")[0] is warmup.saved
    warmup.end.assert_not_awaited()
    warmup.adapter.hangup.assert_not_awaited()


async def test_expired_absent_warmup_closes_once_without_inventing_call_settlement(monkeypatch, warmup):
    warmup.adapter.list_active_channel_ids.return_value = set()
    await ticks(monkeypatch, count=2)
    assert not warmup.state.has_ringing_warmup("call")
    assert warmup.state.get_ringing_started_at("call") is None
    warmup.end.assert_awaited_once_with(warmup.saved)
    warmup.adapter.hangup.assert_not_awaited()


async def test_normal_thirty_second_ring_window_keeps_selected_session(monkeypatch, warmup):
    warmup.state.set_ringing_started_at("call", asyncio.get_running_loop().time() - 10)
    await ticks(monkeypatch)
    assert warmup.state.get_ringing_warmup("call")[0] is warmup.saved
    warmup.end.assert_not_awaited()


@pytest.mark.parametrize("race", ["replaced", "consumed"])
async def test_absence_probe_cannot_end_a_different_or_consumed_owner(monkeypatch, warmup, race):
    replacement = NS()
    created_at = warmup.state.get_ringing_started_at("call")
    async def probe():
        if race == "replaced":
            # Same timestamp deliberately: identity must be checked as well.
            warmup.state.set_ringing_warmup("call", replacement, None)
            warmup.state.set_ringing_started_at("call", created_at)
        else:
            selected = lifecycle._pop_ringing_warmup("call")[0]
            warmup.state.set_voice_session("call", selected)
        return set()
    warmup.adapter.list_active_channel_ids.side_effect = probe
    await ticks(monkeypatch)
    warmup.end.assert_not_awaited()
    if race == "replaced":
        assert warmup.state.get_ringing_warmup("call")[0] is replacement
    else:
        assert warmup.state.get_voice_session("call") is warmup.saved


async def test_failed_latest_probe_does_not_reuse_prior_empty_result(monkeypatch, warmup):
    original = warmup.state.get_ringing_started_at("call")
    warmup.state.set_ringing_started_at("call", asyncio.get_running_loop().time())
    async def first():
        warmup.state.set_ringing_started_at("call", original)
        return set()
    calls = 0
    async def probe():
        nonlocal calls
        calls += 1
        if calls == 1:
            return await first()
        raise RuntimeError("synthetic next-tick outage")
    warmup.adapter.list_active_channel_ids.side_effect = probe
    await ticks(monkeypatch, count=2)
    assert warmup.state.get_ringing_warmup("call")[0] is warmup.saved
    warmup.end.assert_not_awaited()

class Registered(BaseException):
    """Stop the offline probe before starting any real media pipeline."""


@pytest.fixture
async def answered(monkeypatch, warmup):
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
    from app.domain.services import global_concurrency
    from tests.unit.test_global_concurrency import _FakeRedis
    adapter = AsteriskAdapter(ari_password="synthetic-not-a-real-password")
    adapter._record_outbound_answered_at("call")
    adapter.hangup = AsyncMock()
    adapter.list_active_channel_ids = AsyncMock(return_value={"call"})
    warmup.adapter = adapter
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    redis = _FakeRedis()
    container = NS(is_initialized=True, redis=redis, db_pool=None)
    monkeypatch.setattr("app.core.container.get_container", lambda: container)
    status = AsyncMock()
    monkeypatch.setattr("app.domain.services.call_status.record_call_state_by_provider_id", status)
    configs = []
    async def create(config):
        configs.append(config)
        return NS(config=config, call_session=NS())
    warmup.orchestrator.create_voice_session = AsyncMock(side_effect=create)
    registered = []
    original = warmup.state.set_voice_session
    def observe(call_id, session, **kwargs):
        original(call_id, session, **kwargs)
        registered.append(session)
        raise Registered()
    monkeypatch.setattr(warmup.state, "set_voice_session", observe)
    warmup.redis = redis
    warmup.status = status
    warmup.registered = registered
    warmup.configs = configs
    warmup.slots = global_concurrency
    return warmup


async def answer_once():
    try:
        await lifecycle._on_new_call("call")
    except Registered:
        pass


async def test_delayed_answer_after_long_live_ring_uses_original_selected_session(monkeypatch, answered):
    await ticks(monkeypatch)
    await answer_once()
    assert answered.registered == [answered.saved]
    answered.orchestrator.create_voice_session.assert_not_awaited()
    answered.adapter.hangup.assert_not_awaited()


@pytest.mark.parametrize("proof", ["first_speaker", "outbound_answer_clock"])
async def test_lost_owned_selection_refuses_default_factory(answered, proof):
    lifecycle._pop_ringing_warmup("call")
    answered.state.pop_ringing_event("call")
    if proof == "first_speaker":
        answered.adapter.pop_outbound_answered_at_monotonic("call")
    else:
        answered.state.clear_first_speaker("call")
    await answer_once()
    answered.orchestrator.create_voice_session.assert_not_awaited()
    answered.adapter.hangup.assert_awaited_once_with("call")
    assert not answered.registered


async def test_genuinely_unowned_legacy_callback_keeps_existing_default_compatibility(answered):
    lifecycle._pop_ringing_warmup("call")
    answered.state.pop_ringing_event("call")
    answered.state.clear_first_speaker("call")
    answered.adapter.pop_outbound_answered_at_monotonic("call")
    await answer_once()
    assert len(answered.configs) == 1
    assert answered.configs[0].tenant_id is None


async def test_duplicate_registered_callback_at_pod_capacity_does_not_end_current_session(monkeypatch, answered):
    telephony_bridge._telephony_sessions["call"] = answered.saved
    monkeypatch.setattr(lifecycle, "_MAX_TELEPHONY_SESSIONS", 1)
    reject = AsyncMock()
    monkeypatch.setattr(lifecycle, "_reject_overcap_call", reject)
    await answer_once()
    reject.assert_not_awaited()
    answered.status.assert_not_awaited()
    answered.orchestrator.create_voice_session.assert_not_awaited()
    assert answered.state.get_voice_session("call") is answered.saved


@pytest.mark.parametrize("boundary", ["before_callback", "held_status", "held_lease"])
async def test_terminal_dispatch_cannot_restart_default_session_or_reacquire_lease(monkeypatch, answered, boundary):
    async def terminal():
        # Existing Asterisk terminal dispatcher owns this token before its
        # cleanup starts; no additional map is invented by this fixture.
        async def cleanup():
            lifecycle._pop_ringing_warmup("call")
            answered.state.pop_ringing_event("call")
            answered.state.clear_first_speaker("call")
            answered.adapter.pop_outbound_answered_at_monotonic("call")
            await answered.slots.release_lease(answered.redis, call_id="call")
        assert answered.adapter._schedule_terminal_cleanup("call", cleanup, reason="synthetic_provider_terminal")
        await answered.adapter._terminal_cleanup_tasks["call"]
    if boundary == "before_callback":
        await terminal()
    elif boundary == "held_status":
        answered.status.side_effect = lambda *args, **kwargs: None
        async def status(*args, **kwargs):
            await terminal()
        answered.status.side_effect = status
    else:
        original = answered.slots.acquire_lease
        async def acquire(*args, **kwargs):
            await terminal()
            return await original(*args, **kwargs)
        monkeypatch.setattr(answered.slots, "acquire_lease", acquire)
    await answer_once()
    answered.orchestrator.create_voice_session.assert_not_awaited()
    assert not answered.registered
    assert await answered.slots.current_count(answered.redis) == 0
    answered.adapter.hangup.assert_not_awaited()

async def test_terminal_during_ringing_wait_never_restarts_or_requests_second_hangup(answered):
    lifecycle._pop_ringing_warmup("call")
    async def wait():
        assert await answered.slots.current_count(answered.redis) == 1
        async def cleanup():
            answered.state.clear_first_speaker("call")
            answered.adapter.pop_outbound_answered_at_monotonic("call")
            await answered.slots.release_lease(answered.redis, call_id="call")
        assert answered.adapter._schedule_terminal_cleanup("call", cleanup, reason="synthetic_provider_terminal")
        await answered.adapter._terminal_cleanup_tasks["call"]
    answered.state.set_ringing_event("call", NS(wait=wait))
    await answer_once()
    answered.orchestrator.create_voice_session.assert_not_awaited()
    answered.adapter.hangup.assert_not_awaited()
    assert not answered.registered
    assert await answered.slots.current_count(answered.redis) == 0


async def test_selected_session_that_finishes_warmup_during_wait_is_preserved(answered):
    lifecycle._pop_ringing_warmup("call")
    async def wait():
        answered.state.set_ringing_warmup("call", answered.saved, None)
    answered.state.set_ringing_event("call", NS(wait=wait))
    await answer_once()
    assert answered.registered == [answered.saved]
    answered.orchestrator.create_voice_session.assert_not_awaited()

async def test_late_ringing_callback_cannot_rebuild_lost_owned_profile(answered):
    lifecycle._pop_ringing_warmup("call")
    answered.state.pop_ringing_event("call")
    async def reached(config):
        raise Registered()
    answered.orchestrator.create_voice_session.side_effect = reached
    try:
        await lifecycle._on_ringing("call")
    except Registered:
        pass
    answered.orchestrator.create_voice_session.assert_not_awaited()


def unowned_ringing(answered):
    lifecycle._pop_ringing_warmup("call")
    answered.state.pop_ringing_event("call")
    answered.state.clear_first_speaker("call")
    answered.adapter.pop_outbound_answered_at_monotonic("call")


async def dispatch_terminal(answered):
    assert answered.adapter._schedule_terminal_cleanup(
        "call", AsyncMock(), reason="synthetic_provider_terminal"
    )
    await answered.adapter._terminal_cleanup_tasks["call"]


@pytest.mark.parametrize("change", ["terminal", "selected_session"])
async def test_ringing_status_await_cannot_recreate_terminal_or_replace_selected_owner(answered, change):
    unowned_ringing(answered)
    async def status(*args, **kwargs):
        if change == "terminal":
            await dispatch_terminal(answered)
        else:
            answered.state.set_first_speaker("call", "agent")
            answered.state.set_ringing_warmup("call", answered.saved, None)
    answered.status.side_effect = status
    await lifecycle._on_ringing("call")
    answered.orchestrator.create_voice_session.assert_not_awaited()
    if change == "selected_session":
        assert answered.state.get_ringing_warmup("call")[0] is answered.saved


@pytest.mark.parametrize("change", ["terminal", "selected_session", "lost_reservation", "cleanup_failure"])
async def test_ringing_factory_await_cannot_publish_after_ownership_changes(answered, change):
    unowned_ringing(answered)
    created = NS(tts_provider=NS(), stt_provider=NS(), llm_provider=NS())
    async def create(config):
        if change == "terminal":
            await dispatch_terminal(answered)
            answered.state.pop_ringing_event("call")
        elif change in {"selected_session", "cleanup_failure"}:
            answered.state.set_first_speaker("call", "agent")
            answered.state.set_ringing_warmup("call", answered.saved, None)
        else:
            answered.state.pop_ringing_event("call")
        return created
    answered.orchestrator.create_voice_session.side_effect = create
    if change == "cleanup_failure":
        answered.end.side_effect = RuntimeError("synthetic unused-session close failure")
    try:
        await lifecycle._on_ringing("call")
        answered.end.assert_awaited_once_with(created)
        if change in {"selected_session", "cleanup_failure"}:
            assert answered.state.get_ringing_warmup("call")[0] is answered.saved
        else:
            assert not answered.state.has_ringing_warmup("call")
    finally:
        # A failing baseline can start the existing background presynthesis;
        # cancel only this fixture's task before returning to pytest.
        cached = answered.state.get_ringing_warmup("call")
        if cached and cached[1] is not None:
            cached[1].cancel()
            await asyncio.gather(cached[1], return_exceptions=True)


async def test_unowned_ringing_still_prepares_and_answer_reuses_that_session(answered):
    unowned_ringing(answered)
    created = NS(tts_provider=NS(), stt_provider=NS(), llm_provider=NS(), call_session=NS())
    answered.orchestrator.create_voice_session.side_effect = None
    answered.orchestrator.create_voice_session.return_value = created
    await lifecycle._on_ringing("call")
    cached = answered.state.get_ringing_warmup("call")
    assert cached[0] is created
    # Stop presynthesis; this control ends at actual answer registration.
    cached[1].cancel()
    await asyncio.gather(cached[1], return_exceptions=True)
    await answer_once()
    assert answered.registered == [created]
    answered.orchestrator.create_voice_session.assert_awaited_once()
