from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.telephony import lifecycle
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


class _CleanupState:
    def __init__(self) -> None:
        self.cleanup: dict[str, dict[str, object]] = {}
        self.promotions: list[tuple[str, str]] = []

    def get_ringing_warmup(self, _call_id: str):
        return None

    def pop_ringing_warmup(self, _call_id: str):
        return None

    def clear_first_speaker(self, _call_id: str) -> None:
        return None

    def clear_ringing_started_at(self, _call_id: str) -> None:
        return None

    def pop_ringing_event(self, _call_id: str):
        return None

    def pop_voice_session(self, _call_id: str):
        return None

    def remove_gateway_sessions_for_call(self, _call_id: str) -> None:
        return None

    async def register_cleanup_obligation(self, call_id: str, **metadata) -> None:
        self.cleanup[call_id] = dict(metadata)

    async def promote_answered_cleanup_obligation(
        self,
        call_id: str,
        *,
        answered_at: str,
        **_metadata,
    ) -> None:
        self.promotions.append((call_id, answered_at))

    async def acknowledge_orphan_recovery(self, call_id: str) -> None:
        self.cleanup.pop(call_id, None)


def _install_outbound_media_fakes(adapter: AsteriskAdapter) -> None:
    async def ari(_method, path, **_kwargs):
        if path == "/bridges":
            return {"id": "bridge-1"}
        if path == "/channels/externalMedia":
            return {"id": "external-1"}
        return {}

    adapter._ari = ari  # type: ignore[assignment]
    adapter._resolve_unicastrtp_local = AsyncMock(
        return_value=("127.0.0.1", 31_235)
    )
    adapter._start_gateway_session = AsyncMock(return_value={})


async def _wait_for_answer_setup(adapter: AsteriskAdapter, channel_id: str) -> None:
    for _ in range(100):
        task = adapter._outbound_answer_setup_tasks.get(channel_id)
        if task is not None:
            await task
            return
        await asyncio.sleep(0)
    raise AssertionError("outbound answer setup task was not tracked")


def test_outbound_duration_starts_at_confirmed_answer_and_is_capped() -> None:
    voice_session = SimpleNamespace(
        _answered_at_monotonic=100.0,
        _max_call_duration_seconds=60,
        call_session=SimpleNamespace(get_duration_seconds=lambda: 900.0),
    )

    assert lifecycle._confirmed_outbound_duration_seconds(voice_session, 104.01) == 5
    assert lifecycle._confirmed_outbound_duration_seconds(voice_session, 10_000.0) == 60


def test_outbound_duration_is_zero_without_confirmed_answer_or_terminal_proof() -> None:
    never_answered = SimpleNamespace(
        _max_call_duration_seconds=60,
        call_session=SimpleNamespace(get_duration_seconds=lambda: 900.0),
    )
    answered_without_terminal = SimpleNamespace(
        _answered_at_monotonic=100.0,
        _max_call_duration_seconds=60,
        call_session=SimpleNamespace(get_duration_seconds=lambda: 900.0),
    )

    assert lifecycle._confirmed_outbound_duration_seconds(never_answered, 104.01) == 0
    assert lifecycle._confirmed_outbound_duration_seconds(answered_without_terminal, None) == 0


@pytest.mark.asyncio
async def test_asterisk_records_answer_before_outbound_media_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.services.telephony import state_backend

    monkeypatch.setattr(
        state_backend,
        "get_state_backend",
        lambda: _CleanupState(),
    )
    adapter = AsteriskAdapter()
    channel_id = "outbound-answer-proof"
    adapter._pending_outbound[channel_id] = {
        "bridge_id": "bridge-1",
        "listen_port": 31_234,
        "session_id": "gateway-session-1",
    }

    media_setup_started_at: list[float] = []

    async def ari(method, path, **_kwargs):
        if path == "/channels/externalMedia":
            media_setup_started_at.append(time.monotonic())
            return {"id": "external-1"}
        return {}

    adapter._ari = ari  # type: ignore[assignment]
    adapter._resolve_unicastrtp_local = AsyncMock(return_value=("127.0.0.1", 31_235))
    adapter._start_gateway_session = AsyncMock(return_value={})

    await adapter._on_outbound_answered(channel_id)

    answered_at = adapter._active_sessions[channel_id]["answered_at_monotonic"]
    assert isinstance(answered_at, float)
    assert answered_at <= media_setup_started_at[0]
    assert adapter.get_answered_at_monotonic(channel_id) == answered_at


@pytest.mark.asyncio
async def test_answer_clock_is_frozen_in_channel_up_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.services.telephony import state_backend

    state = _CleanupState()
    monkeypatch.setattr(state_backend, "get_state_backend", lambda: state)
    adapter = AsteriskAdapter()
    channel_id = "talky-out-event-answer"
    adapter._pending_outbound[channel_id] = {
        "bridge_id": "bridge-1",
        "listen_port": 31_234,
        "session_id": "gateway-session-1",
    }
    _install_outbound_media_fakes(adapter)

    await adapter._handle_ari_event(
        {
            "type": "ChannelStateChange",
            "channel": {"id": channel_id, "state": "Up"},
        }
    )
    handler_returned_at = asyncio.get_running_loop().time()
    await _wait_for_answer_setup(adapter, channel_id)

    assert adapter.get_answered_at_monotonic(channel_id) <= handler_returned_at

    await adapter._handle_ari_event(
        {
            "type": "ChannelStateChange",
            "channel": {"id": channel_id, "state": "Up"},
        }
    )
    assert channel_id not in adapter._preemptive_up_channels
    assert channel_id not in adapter._preemptive_up_at_monotonic


@pytest.mark.asyncio
async def test_preemptive_up_keeps_original_clock_but_inbound_discards_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.services.telephony import state_backend

    state = _CleanupState()
    monkeypatch.setattr(state_backend, "get_state_backend", lambda: state)
    adapter = AsteriskAdapter()
    outbound_id = "talky-out-preemptive-answer"
    inbound_id = "inbound-preemptive-up"
    _install_outbound_media_fakes(adapter)

    await adapter._handle_ari_event(
        {
            "type": "ChannelStateChange",
            "channel": {"id": outbound_id, "state": "Up"},
        }
    )
    outbound_up_returned_at = asyncio.get_running_loop().time()
    await asyncio.sleep(0.01)
    await adapter._on_outbound_stasis_start(outbound_id)
    await _wait_for_answer_setup(adapter, outbound_id)

    assert adapter.get_answered_at_monotonic(outbound_id) <= outbound_up_returned_at

    adapter._schedule_inbound_start = lambda *_args, **_kwargs: None  # type: ignore[assignment]
    await adapter._handle_ari_event(
        {
            "type": "ChannelStateChange",
            "channel": {"id": inbound_id, "state": "Up"},
        }
    )
    await adapter._handle_ari_event(
        {
            "type": "StasisStart",
            "args": ["inbound"],
            "channel": {"id": inbound_id, "name": "PJSIP/inbound-1"},
        }
    )

    assert inbound_id not in adapter._preemptive_up_channels
    assert inbound_id not in adapter._preemptive_up_at_monotonic
    assert adapter.get_answered_at_monotonic(inbound_id) is None


@pytest.mark.asyncio
async def test_outbound_destroyed_event_freezes_terminal_before_cleanup_task() -> None:
    adapter = AsteriskAdapter()
    channel_id = "talky-out-terminal-clock"
    adapter._active_sessions[channel_id] = {"direction": "outbound"}
    cleanup_can_finish = asyncio.Event()

    async def blocked_cleanup(*_args, **_kwargs):
        await cleanup_can_finish.wait()

    adapter._on_stasis_end = blocked_cleanup  # type: ignore[assignment]

    await adapter._handle_ari_event(
        {
            "type": "ChannelDestroyed",
            "channel": {"id": channel_id, "name": "PJSIP/outbound-1"},
        }
    )
    handler_returned_at = asyncio.get_running_loop().time()

    assert adapter._terminal_at_monotonic[channel_id] <= handler_returned_at
    cleanup_can_finish.set()
    await asyncio.gather(*adapter._terminal_cleanup_tasks.values())


@pytest.mark.asyncio
async def test_fast_pre_stasis_answer_then_destroy_keeps_answer_duration() -> None:
    adapter = AsteriskAdapter()
    channel_id = "talky-out-fast-answer"
    adapter._originated_channels.add(channel_id)
    durations: list[int] = []

    async def ended(call_id: str) -> None:
        durations.append(
            lifecycle._confirmed_outbound_duration_seconds(
                None,
                adapter.pop_terminal_at_monotonic(call_id),
                answered_at_monotonic=(
                    adapter.pop_outbound_answered_at_monotonic(call_id)
                ),
            )
        )

    adapter._on_any_call_end = ended
    await adapter._handle_ari_event(
        {
            "type": "ChannelStateChange",
            "channel": {"id": channel_id, "state": "Up"},
        }
    )
    await adapter._handle_ari_event(
        {
            "type": "ChannelDestroyed",
            "channel": {"id": channel_id, "name": "PJSIP/outbound-1"},
        }
    )
    terminal_task = adapter._terminal_cleanup_tasks[channel_id]
    await terminal_task

    assert durations == [1]


@pytest.mark.asyncio
async def test_failed_answer_setup_keeps_durable_owner_until_hangup_is_proven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.services.telephony import state_backend

    state = _CleanupState()
    monkeypatch.setattr(state_backend, "get_state_backend", lambda: state)
    adapter = AsteriskAdapter()
    adapter._inbound_cleanup_retry_s = 0.001
    channel_id = "talky-out-setup-failure"
    adapter._pending_outbound[channel_id] = {
        "bridge_id": "bridge-1",
        "listen_port": 31_234,
        "session_id": "gateway-session-1",
    }
    gateway_started = asyncio.Event()
    fail_gateway = asyncio.Event()
    first_unconfirmed = asyncio.Event()
    hangup_attempts = 0
    settled_durations: list[int] = []

    async def ari(_method, path, **_kwargs):
        if path == "/channels/externalMedia":
            return {"id": "external-1"}
        return {}

    async def start_gateway(_payload):
        gateway_started.set()
        await fail_gateway.wait()
        raise RuntimeError("gateway unavailable")

    async def hangup_confirmed(_call_id):
        nonlocal hangup_attempts
        hangup_attempts += 1
        if hangup_attempts == 1:
            # The public confirmation contract returns False when DELETE or
            # the subsequent ARI inventory proof times out.
            first_unconfirmed.set()
            return False
        return True

    async def settle(call_id: str) -> None:
        terminal = adapter.pop_terminal_at_monotonic(call_id)
        answered = adapter.pop_outbound_answered_at_monotonic(call_id)
        settled_durations.append(
            lifecycle._confirmed_outbound_duration_seconds(
                None,
                terminal,
                answered_at_monotonic=answered,
            )
        )
        await state.acknowledge_orphan_recovery(call_id)

    adapter._ari = ari  # type: ignore[assignment]
    adapter._resolve_unicastrtp_local = AsyncMock(
        return_value=("127.0.0.1", 31_235)
    )
    adapter._start_gateway_session = start_gateway  # type: ignore[assignment]
    adapter.hangup_confirmed = hangup_confirmed  # type: ignore[assignment]
    adapter._on_any_call_end = settle

    await adapter._handle_ari_event(
        {
            "type": "ChannelStateChange",
            "channel": {"id": channel_id, "state": "Up"},
        }
    )
    await gateway_started.wait()
    task = adapter._outbound_answer_setup_tasks[channel_id]
    assert channel_id in adapter.owned_call_ids()

    fail_gateway.set()
    await first_unconfirmed.wait()
    assert channel_id in state.cleanup
    assert state.cleanup[channel_id]["state"] == "answer_pending"
    assert state.promotions and state.promotions[-1][0] == channel_id
    assert channel_id in adapter.owned_call_ids()

    await task
    assert hangup_attempts == 2
    assert settled_durations == [1]
    assert channel_id not in state.cleanup


@pytest.mark.asyncio
async def test_destroyed_during_answer_setup_cancels_into_single_cleanup_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.services.telephony import state_backend

    state = _CleanupState()
    monkeypatch.setattr(state_backend, "get_state_backend", lambda: state)
    adapter = AsteriskAdapter()
    channel_id = "talky-out-destroyed-during-setup"
    adapter._pending_outbound[channel_id] = {
        "bridge_id": "bridge-1",
        "listen_port": 31_234,
        "session_id": "gateway-session-1",
    }
    gateway_started = asyncio.Event()
    gateway_cancelled = asyncio.Event()
    never_finishes = asyncio.Event()
    settled: list[str] = []

    async def ari(_method, path, **_kwargs):
        if path == "/channels/externalMedia":
            return {"id": "external-1"}
        return {}

    async def start_gateway(_payload):
        gateway_started.set()
        try:
            await never_finishes.wait()
        finally:
            gateway_cancelled.set()

    async def settle(call_id: str) -> None:
        settled.append(call_id)
        adapter.pop_terminal_at_monotonic(call_id)
        adapter.pop_outbound_answered_at_monotonic(call_id)
        await state.acknowledge_orphan_recovery(call_id)

    adapter._ari = ari  # type: ignore[assignment]
    adapter._resolve_unicastrtp_local = AsyncMock(
        return_value=("127.0.0.1", 31_235)
    )
    adapter._start_gateway_session = start_gateway  # type: ignore[assignment]
    adapter._gateway = AsyncMock(return_value={})  # type: ignore[assignment]
    adapter.hangup_confirmed = AsyncMock(return_value=True)  # type: ignore[assignment]
    adapter._on_any_call_end = settle

    await adapter._handle_ari_event(
        {
            "type": "ChannelStateChange",
            "channel": {"id": channel_id, "state": "Up"},
        }
    )
    await gateway_started.wait()
    answer_task = adapter._outbound_answer_setup_tasks[channel_id]

    await adapter._handle_ari_event(
        {
            "type": "ChannelDestroyed",
            "channel": {"id": channel_id, "name": "PJSIP/outbound-1"},
        }
    )
    terminal_task = adapter._terminal_cleanup_tasks[channel_id]
    await terminal_task

    assert gateway_cancelled.is_set()
    assert answer_task.cancelled()
    assert settled == [channel_id]
    assert channel_id not in state.cleanup
    assert channel_id not in adapter.owned_call_ids()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settlement_durable", "expected_retry_state"),
    [(True, None), (False, "answer_pending")],
)
async def test_ari_event_clocks_reach_call_service_duration(
    monkeypatch: pytest.MonkeyPatch,
    settlement_durable: bool,
    expected_retry_state: str | None,
) -> None:
    from app.core import container as container_module
    from app.core import db_utils
    from app.domain.services import call_status, global_concurrency
    from app.domain.services.telephony import state_backend

    state = _CleanupState()
    monkeypatch.setattr(state_backend, "get_state_backend", lambda: state)
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    adapter = AsteriskAdapter()
    channel_id = "talky-out-e2e-clocks"
    adapter._pending_outbound[channel_id] = {
        "bridge_id": "bridge-1",
        "listen_port": 31_234,
        "session_id": "gateway-session-1",
    }
    _install_outbound_media_fakes(adapter)
    adapter._gateway = AsyncMock(return_value={})  # type: ignore[assignment]
    adapter._on_any_call_end = lifecycle._on_call_ended
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)

    await adapter._handle_ari_event(
        {
            "type": "ChannelStateChange",
            "channel": {"id": channel_id, "state": "Up"},
        }
    )
    await _wait_for_answer_setup(adapter, channel_id)
    adapter._outbound_answered_at_monotonic[channel_id] = (
        asyncio.get_running_loop().time() - 4.01
    )

    class _Connection:
        async def fetchrow(self, _query, *_args):
            return {"id": "durable-call-1", "tenant_id": "tenant-1"}

    @asynccontextmanager
    async def acquire_with_tenant(_pool, _tenant_id):
        yield _Connection()

    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire_with_tenant)
    container = SimpleNamespace(
        is_initialized=True,
        db_pool=object(),
        db_client=object(),
        redis=None,
        _queue_service=None,
    )
    monkeypatch.setattr(container_module, "get_container", lambda: container)
    monkeypatch.setattr(global_concurrency, "release_lease", AsyncMock())
    monkeypatch.setattr(
        call_status,
        "record_call_state_by_provider_id",
        AsyncMock(),
    )
    captured_durations: list[int] = []
    captured_outcomes: list[str] = []

    class _CallService:
        def __init__(self, **_kwargs) -> None:
            pass

        async def handle_call_status(self, *, duration: int, outcome, **_kwargs):
            captured_durations.append(duration)
            captured_outcomes.append(outcome.value)
            return SimpleNamespace(
                durable=settlement_durable,
                terminal_outcome="answered",
                error=None,
            )

    monkeypatch.setattr(lifecycle, "CallService", _CallService)
    lifecycle._ended_calls_in_flight.discard(channel_id)
    lifecycle._ended_calls_logically_completed.discard(channel_id)

    await adapter._handle_ari_event(
        {
            "type": "ChannelDestroyed",
            "channel": {"id": channel_id, "name": "PJSIP/outbound-1"},
        }
    )
    terminal_task = adapter._terminal_cleanup_tasks[channel_id]
    await terminal_task

    assert captured_durations == [5]
    assert captured_outcomes == ["failed"]
    assert adapter.get_answered_at_monotonic(channel_id) is None
    assert channel_id not in adapter._terminal_at_monotonic
    if expected_retry_state is None:
        assert channel_id not in state.cleanup
    else:
        assert state.cleanup[channel_id]["state"] == expected_retry_state
    lifecycle._ended_calls_in_flight.discard(channel_id)
    lifecycle._ended_calls_logically_completed.discard(channel_id)
