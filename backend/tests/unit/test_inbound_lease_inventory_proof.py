"""Actual callback admission at lease-loss proof boundaries; synthetic I/O only."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.telephony import lifecycle, termination
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


@pytest.fixture
def path(monkeypatch):
    state = SimpleNamespace(register_cleanup_obligation=AsyncMock())
    value = SimpleNamespace(
        db_available=False,
        deletes=[],
        entries=[],
        callback_results=[],
        blocked=None,
        child_present=False,
        after_parent=None,
        started=asyncio.Event(),
        parent="lease-proof-parent",
        child="lease-proof-child",
        source="asterisk",
    )
    value.admission = {
        "provider": "asterisk",
        "provider_call_id": value.parent,
        "tenant_id": "00000000-0000-4000-8000-000000000002",
        "allowed": True,
    }

    class Conn:
        async def fetchrow(self, query, *_args):
            row = dict(
                call_id="00000000-0000-4000-8000-000000000001",
                tenant_id=value.admission["tenant_id"],
                provider_call_id=value.parent,
                status="in_progress",
                provider=value.source,
                direction="inbound",
                campaign_id=None,
                answered_at=None,
            )
            if "recovery_match_count" in query:
                row.update(id=row["call_id"], recovery_match_count=1)
            return row

        async def fetch(self, *_args):
            return [{"provider_leg_id": value.child, "provider": "asterisk"}]

        async def execute(self, *_args):
            return "UPDATE 1"

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        if not value.db_available:
            raise ConnectionError("synthetic DB unavailable")
        yield Conn()

    adapter = AsteriskAdapter()
    adapter._hangup_confirm_timeout_s = 0.03
    adapter._hangup_confirm_poll_s = 0.001
    adapter.list_active_channel_ids = AsyncMock(
        side_effect=lambda: {value.child} if value.child_present else set()
    )

    class LogicalEntry(Exception):
        pass

    def logical_entry(call_id):
        # The first mutation after the real _on_call_ended callback guard.
        # Stop here: this suite does not claim real settlement/DB effects.
        value.entries.append((call_id, tuple(value.deletes)))
        raise LogicalEntry()

    actual_ended = lifecycle._on_call_ended
    value.release_marker = lifecycle._release_ended_marker_later

    async def ended(call_id, **kwargs):
        try:
            return await actual_ended(call_id, **kwargs)
        except LogicalEntry:
            lifecycle._ended_calls_in_flight.discard(call_id)
            return True

    async def ari(method, route, **_kwargs):
        assert method == "DELETE"
        value.deletes.append(route)
        if route == "/channels/" + value.parent:
            value.callback_results.append(await ended(value.parent))
            value.started.set()
            if value.blocked is not None:
                await value.blocked.wait()
            if value.after_parent is not None:
                value.after_parent()
        if route == "/channels/" + value.child and value.child_present:
            return 204, {}
        return 404, {}

    adapter._ari = ari
    monkeypatch.setattr(termination, "acquire_with_tenant", acquire)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "_on_call_ended", ended)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", logical_entry)
    monkeypatch.setattr(lifecycle, "_ended_calls_in_flight", set())
    monkeypatch.setattr(lifecycle, "_ended_call_marker_tokens", {})
    monkeypatch.setattr(lifecycle, "_ended_calls_logically_completed", set())
    monkeypatch.setattr(lifecycle, "_orphan_recovery_in_flight", set())
    monkeypatch.setattr(lifecycle, "_orphan_recovery_contexts_by_call", {})

    async def invoke():
        return await lifecycle._fence_inbound_call_after_lease_loss(
            SimpleNamespace(db_pool=object()),
            pbx_call_id=value.parent,
            durable_call_id="00000000-0000-4000-8000-000000000001",
            admission=value.admission,
        )

    value.invoke, value.ended, value.state = invoke, ended, state
    value.acquire, value.adapter = acquire, adapter
    value.logical_entry = logical_entry
    return value


@pytest.mark.asyncio
async def test_outage_parent_request_and_synchronous_callback_never_settle(path):
    assert await path.invoke() is False
    assert path.deletes == ["/channels/" + path.parent]
    assert path.callback_results == [False]
    assert path.entries == []
    assert path.parent in lifecycle._orphan_recovery_contexts_by_call
    assert path.parent not in lifecycle._orphan_recovery_in_flight
    assert await path.ended(path.parent) is False
    assert path.entries == []


@pytest.mark.asyncio
async def test_repeated_db_outage_then_durable_all_leg_proof_converges(path):
    assert await path.invoke() is False
    assert await path.invoke() is False
    assert path.entries == []
    path.db_available = True
    assert await path.invoke() is True
    assert len(path.entries) == 1
    assert "/channels/" + path.child in path.entries[0][1]
    assert path.callback_results == [False, False, False]
    assert path.parent not in lifecycle._orphan_recovery_contexts_by_call
    assert path.state.register_cleanup_obligation.await_count == 3


@pytest.mark.asyncio
async def test_cancellation_retains_callback_fence_and_retry_ownership(path):
    path.blocked = asyncio.Event()
    task = asyncio.create_task(path.invoke())
    await asyncio.wait_for(path.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert path.entries == []
    assert path.parent in lifecycle._orphan_recovery_contexts_by_call
    assert path.parent not in lifecycle._orphan_recovery_in_flight
    assert await path.ended(path.parent) is False
    path.blocked = None
    path.db_available = True
    assert await path.invoke() is True
    assert len(path.entries) == 1


@pytest.mark.asyncio
async def test_other_recovery_owner_is_never_overwritten(path):
    other = {"_awaiting_all_leg_absence_proof": True, "provider": "asterisk"}
    lifecycle._orphan_recovery_contexts_by_call[path.parent] = other
    assert await path.invoke() is False
    assert lifecycle._orphan_recovery_contexts_by_call[path.parent] is other
    assert path.deletes == []
    assert path.entries == []


@pytest.mark.asyncio
async def test_in_flight_recovery_owner_prevents_second_request(path):
    lifecycle._orphan_recovery_in_flight.add(path.parent)
    assert await path.invoke() is False
    assert path.parent in lifecycle._orphan_recovery_in_flight
    assert path.deletes == []


@pytest.mark.asyncio
async def test_live_inventory_still_fences_callbacks_until_child_proof(path):
    path.db_available = True
    assert await path.invoke() is True
    assert path.callback_results == [False]
    assert len(path.entries) == 1
    assert "/channels/" + path.child in path.entries[0][1]


@pytest.mark.asyncio
async def test_outage_with_mismatched_admission_does_not_touch_adapter(path):
    path.admission["provider"] = "twilio"
    assert await path.invoke() is False
    assert path.deletes == []
    assert path.entries == []


@pytest.mark.asyncio
async def test_callback_during_first_redis_await_is_already_fenced(path):
    async def register(*_args, **_kwargs):
        assert await path.ended(path.parent) is False

    path.state.register_cleanup_obligation.side_effect = register
    assert await path.invoke() is False
    assert path.entries == []


@pytest.mark.asyncio
async def test_unconfirmed_child_keeps_fence_until_later_all_leg_proof(path):
    path.db_available = True
    path.child_present = True
    assert await path.invoke() is False
    assert path.entries == []
    assert path.parent in lifecycle._orphan_recovery_contexts_by_call
    assert await path.ended(path.parent) is False
    path.child_present = False
    assert await path.invoke() is True
    assert len(path.entries) == 1


@pytest.mark.asyncio
async def test_context_replaced_during_proof_is_not_removed_or_settled(path):
    path.db_available = True
    other = {"_awaiting_all_leg_absence_proof": True, "provider": "asterisk"}
    path.after_parent = lambda: lifecycle._orphan_recovery_contexts_by_call.__setitem__(
        path.parent, other
    )
    assert await path.invoke() is False
    assert lifecycle._orphan_recovery_contexts_by_call[path.parent] is other
    assert path.entries == []


@pytest.mark.asyncio
async def test_second_lease_attempt_cannot_overwrite_paused_owner(path):
    path.blocked = asyncio.Event()
    task = asyncio.create_task(path.invoke())
    await asyncio.wait_for(path.started.wait(), timeout=2)
    fence = lifecycle._orphan_recovery_contexts_by_call[path.parent]
    assert await path.invoke() is False
    assert lifecycle._orphan_recovery_contexts_by_call[path.parent] is fence
    assert path.deletes == ["/channels/" + path.parent]
    path.blocked.set()
    assert await task is False


@pytest.mark.asyncio
async def test_watchdog_recovery_rehydrates_retained_inventory_fence(path, monkeypatch):
    from app.core import container as container_module, db_utils
    from app.domain.services.telephony import transfer_restart_recovery

    assert await path.invoke() is False
    path.db_available = True
    path.state.is_telephony_owner = lambda: True
    path.state.recover_orphans = AsyncMock(
        return_value=[
            {
                "call_id": path.parent,
                "provider": "asterisk",
                "durable_call_id": "00000000-0000-4000-8000-000000000001",
            }
        ]
    )
    path.state.acknowledge_orphan_recovery = AsyncMock()
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(db_utils, "acquire_with_tenant", path.acquire)
    monkeypatch.setattr(
        transfer_restart_recovery, "claim_inbound_transfer_takeovers", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        lifecycle, "_load_termination_pending_candidates", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        lifecycle, "_register_unknown_asterisk_cleanup_candidates", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(lifecycle, "_current_recovery_exclusions", lambda *_args, **_kwargs: set())
    assert await lifecycle.recover_orphaned_calls() == 1
    assert len(path.entries) == 1
    assert "/channels/" + path.child in path.entries[0][1]
    assert path.parent not in lifecycle._orphan_recovery_contexts_by_call
    path.state.acknowledge_orphan_recovery.assert_awaited_once_with(path.parent)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "error"])
async def test_actual_logical_finalizer_failure_does_not_strand_own_marker(
    path, monkeypatch, failure
):
    path.db_available = True
    entered = asyncio.Event()
    hold = asyncio.Event()

    async def cancel_guards(_call_id):
        entered.set()
        if failure == "error":
            raise RuntimeError("synthetic runtime guard failure")
        await hold.wait()

    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _call_id: None)
    monkeypatch.setattr(lifecycle, "_cancel_inbound_runtime_guards", cancel_guards)
    monkeypatch.setattr(lifecycle, "_inbound_admissions_in_flight", {path.parent: path.admission})
    monkeypatch.setattr(lifecycle, "_ended_calls_logically_completed", set())
    task = asyncio.create_task(path.invoke())
    await asyncio.wait_for(entered.wait(), timeout=2)
    if failure == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeError, match="runtime guard failure"):
            await task
    assert path.parent not in lifecycle._ended_calls_in_flight
    assert path.parent in lifecycle._orphan_recovery_contexts_by_call
    assert path.parent not in lifecycle._orphan_recovery_in_flight
    # A later attempt can re-prove inventory and reach the real callback's
    # logical admission boundary instead of waiting for the 10-minute marker.
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", path.logical_entry)
    assert await path.invoke() is True
    assert len(path.entries) == 1


@pytest.mark.asyncio
async def test_other_logical_finalizer_marker_is_never_cleared(path):
    path.db_available = True
    lifecycle._ended_calls_in_flight.add(path.parent)
    assert await path.invoke() is False
    assert path.parent in lifecycle._ended_calls_in_flight
    assert path.entries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("new_completed", [False, True])
async def test_stale_expiry_cannot_remove_new_callback_marker(path, monkeypatch, new_completed):
    timers = []

    def track(coro):
        task = asyncio.create_task(coro)
        timers.append(task)
        return task

    async def fail_after_acquisition(_call_id):
        raise RuntimeError("synthetic finalizer dependency")

    monkeypatch.setattr(lifecycle, "_track_task", track)
    # Restore the real expiry scheduler; acquisition is still the real callback.
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", path.release_marker)
    monkeypatch.setattr(lifecycle, "_cancel_inbound_runtime_guards", fail_after_acquisition)
    monkeypatch.setattr(lifecycle, "_inbound_admissions_in_flight", {path.parent: path.admission})
    try:
        with pytest.raises(RuntimeError, match="finalizer dependency"):
            await path.ended(path.parent)
        old_token = lifecycle._ended_call_marker_tokens[path.parent]
        await asyncio.sleep(0)
        assert lifecycle._clear_ended_marker(path.parent, old_token)
        with pytest.raises(RuntimeError, match="finalizer dependency"):
            await path.ended(path.parent)
        new_token = lifecycle._ended_call_marker_tokens[path.parent]
        assert new_token is not old_token
        if new_completed:
            # Completion retention is a marker-state control, not DB proof.
            lifecycle._ended_calls_logically_completed.add(path.parent)
        await asyncio.sleep(0)
        timers[0].cancel()
        await asyncio.gather(timers[0], return_exceptions=True)
        assert path.parent in lifecycle._ended_calls_in_flight
        assert lifecycle._ended_call_marker_tokens[path.parent] is new_token
        assert (path.parent in lifecycle._ended_calls_logically_completed) is new_completed
        timers[1].cancel()
        await asyncio.gather(timers[1], return_exceptions=True)
        assert path.parent not in lifecycle._ended_calls_in_flight
        assert path.parent not in lifecycle._ended_call_marker_tokens
        assert path.parent not in lifecycle._ended_calls_logically_completed
    finally:
        for timer in timers:
            timer.cancel()
        await asyncio.gather(*timers, return_exceptions=True)


def test_missing_token_cannot_release_preexisting_marker(path):
    lifecycle._ended_calls_in_flight.add(path.parent)
    assert lifecycle._clear_ended_marker(path.parent, None) is False
    assert path.parent in lifecycle._ended_calls_in_flight


@pytest.mark.asyncio
async def test_late_finalizer_cannot_complete_or_ack_newer_owner(monkeypatch):
    from tests.unit.test_telephony_orphan_recovery_confirmation import _RetryLedger
    from app.core import container as container_module
    from app.domain.services.telephony import inbound_transfer

    call_id = "late-finalizer-generation"
    state = _RetryLedger([{"call_id": call_id}])
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    finalizations = []

    async def finalize(*_args, **_kwargs):
        index = len(finalizations)
        finalizations.append(index)
        entered[index].set()
        await release[index].wait()

    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: SimpleNamespace())
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _call_id: None)
    monkeypatch.setattr(lifecycle, "_finalize_inbound_admission", finalize)
    monkeypatch.setattr(lifecycle, "_ended_calls_in_flight", set())
    monkeypatch.setattr(lifecycle, "_ended_call_marker_tokens", {})
    monkeypatch.setattr(lifecycle, "_ended_calls_logically_completed", set())
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(
        inbound_transfer, "finalize_connected_inbound_transfers", AsyncMock(return_value=0)
    )
    context = {
        "direction": "inbound",
        "provider": "asterisk",
        "duration_seconds": 2,
        "admission": {
            "allowed": True,
            "call_id": "00000000-0000-4000-8000-000000000001",
            "provider": "asterisk",
            "provider_call_id": call_id,
        },
    }
    tasks = []
    try:
        tasks.append(
            asyncio.create_task(lifecycle._on_call_ended(call_id, recovery_context=dict(context)))
        )
        await asyncio.wait_for(entered[0].wait(), timeout=2)
        old = lifecycle._ended_call_marker_tokens[call_id]
        assert lifecycle._clear_ended_marker(call_id, old)
        tasks.append(
            asyncio.create_task(lifecycle._on_call_ended(call_id, recovery_context=dict(context)))
        )
        await asyncio.wait_for(entered[1].wait(), timeout=2)
        current = lifecycle._ended_call_marker_tokens[call_id]
        assert current is not old
        release[0].set()
        assert await tasks[0] is False
        assert lifecycle._ended_call_marker_tokens[call_id] is current
        assert call_id not in lifecycle._ended_calls_logically_completed
        assert state.acknowledged == []
        release[1].set()
        assert await tasks[1] is True
        assert call_id in lifecycle._ended_calls_logically_completed
        assert state.acknowledged == [call_id]
    finally:
        for event in release:
            event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
