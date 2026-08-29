from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.services.global_concurrency import LeaseResult
from app.domain.services.telephony import lifecycle


def _admission_payload(call_id: str = "pbx-inbound-1") -> dict:
    return {
        "allowed": True,
        "call_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "campaign_id": "33333333-3333-3333-3333-333333333333",
        "provider": "asterisk",
        "provider_call_id": call_id,
        "opening_mode": "caller_first",
        "config_snapshot": {
            "route": {
                "max_call_duration_seconds": 60,
                "reservation_seconds": 60,
            },
            "inbound_config": {
                "opening_mode": "caller_first",
                "selected_action": "agent",
                "selected_destination": None,
                "after_hours_message": None,
                "transfer_policy": {},
            },
            "schedule_decision": {
                "selected_action": "agent",
                "selected_destination": None,
            },
        },
    }


@pytest.mark.asyncio
async def test_durable_answer_commits_call_before_promoting_cleanup_ledger(
    monkeypatch,
):
    import app.core.container as container_module
    import app.core.db_utils as db_utils

    payload = _admission_payload("pbx-answer-durable")
    answer_time = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    order: list[str] = []

    class Conn:
        async def fetchrow(self, query, *args):
            order.append("postgres_answer")
            assert "answered_at=COALESCE(answered_at,$4::timestamptz)" in query
            assert "direction='inbound'" in query
            assert args[:3] == (
                payload["call_id"],
                payload["tenant_id"],
                "pbx-answer-durable",
            )
            return {"status": "answered", "answered_at": answer_time}

    @asynccontextmanager
    async def acquire(_pool, tenant_id, *, timeout=None):
        assert tenant_id == payload["tenant_id"]
        assert timeout == lifecycle._INBOUND_ANSWER_DURABILITY_TIMEOUT_S
        yield Conn()

    async def promote(call_id, **kwargs):
        order.append("redis_answer")
        assert call_id == "pbx-answer-durable"
        assert kwargs == {
            "answered_at": answer_time.isoformat(),
            "tenant_id": payload["tenant_id"],
            "campaign_id": payload["campaign_id"],
        }

    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    monkeypatch.setattr(
        lifecycle,
        "_state",
        lambda: SimpleNamespace(promote_answered_cleanup_obligation=promote),
    )

    persisted = await lifecycle._persist_inbound_answered(
        "pbx-answer-durable",
        payload,
        answered_at=answer_time.isoformat(),
    )

    assert persisted == answer_time.isoformat()
    assert order == ["postgres_answer", "redis_answer"]


@pytest.mark.asyncio
async def test_durable_answer_db_failure_never_promotes_redis(monkeypatch):
    import app.core.container as container_module
    import app.core.db_utils as db_utils

    payload = _admission_payload("pbx-answer-db-failed")
    promote = AsyncMock()

    class Conn:
        async def fetchrow(self, _query, *_args):
            raise ConnectionError("PostgreSQL unavailable")

    @asynccontextmanager
    async def acquire(_pool, _tenant_id, *, timeout=None):
        del timeout
        yield Conn()

    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    monkeypatch.setattr(
        lifecycle,
        "_state",
        lambda: SimpleNamespace(promote_answered_cleanup_obligation=promote),
    )

    with pytest.raises(ConnectionError, match="PostgreSQL unavailable"):
        await lifecycle._persist_inbound_answered(
            "pbx-answer-db-failed",
            payload,
            answered_at="2026-08-28T12:00:00+00:00",
        )

    promote.assert_not_awaited()


@pytest.mark.asyncio
async def test_lease_loss_fence_persists_recovery_and_requires_all_leg_proof(
    monkeypatch,
):
    import app.domain.services.telephony.termination as termination

    payload = _admission_payload("pbx-lease-lost")
    state = SimpleNamespace(register_cleanup_obligation=AsyncMock())
    context = termination.TerminationContext(
        call_id=payload["call_id"],
        tenant_id=payload["tenant_id"],
        provider_call_id="pbx-lease-lost",
        previous_status="in_progress",
        provider_leg_ids=("linked-transfer-leg",),
    )
    mark_pending = AsyncMock(return_value=context)

    force_end = AsyncMock(return_value=False)
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "_force_end_and_hangup", force_end)
    monkeypatch.setattr(
        termination,
        "mark_termination_pending_and_load_context",
        mark_pending,
    )

    confirmed = await lifecycle._fence_inbound_call_after_lease_loss(
        SimpleNamespace(db_pool=object()),
        pbx_call_id="pbx-lease-lost",
        durable_call_id=payload["call_id"],
        admission=payload,
    )

    assert confirmed is False
    state.register_cleanup_obligation.assert_awaited_once_with(
        "pbx-lease-lost",
        tenant_id=payload["tenant_id"],
        campaign_id=payload["campaign_id"],
        state="termination_pending",
    )
    mark_pending.assert_awaited_once()
    force_end.assert_awaited_once_with(
        "pbx-lease-lost",
        require_confirmation=True,
        provider_leg_ids=["linked-transfer-leg"],
    )


@pytest.mark.asyncio
async def test_lease_loss_retries_until_pbx_absence_is_confirmed(monkeypatch):
    import app.core.container as container_module
    import app.domain.services.telephony.inbound_admission as admission_module

    payload = _admission_payload("pbx-lease-retry")
    container = SimpleNamespace(db_pool=object(), redis=object())
    fence = AsyncMock(side_effect=(False, True))
    sleep = AsyncMock()
    monkeypatch.setattr(container_module, "get_container", lambda: container)
    monkeypatch.setattr(
        admission_module,
        "heartbeat_inbound_call",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        lifecycle,
        "_state",
        lambda: SimpleNamespace(get_voice_session=lambda _call_id: None),
    )
    monkeypatch.setattr(lifecycle, "_fence_inbound_call_after_lease_loss", fence)
    monkeypatch.setattr(lifecycle.asyncio, "sleep", sleep)

    await lifecycle._heartbeat_active_inbound_admission("pbx-lease-retry", payload)

    assert fence.await_count == 2
    sleep.assert_awaited_once_with(lifecycle._INBOUND_LEASE_LOSS_RETRY_S)


@pytest.mark.asyncio
async def test_heartbeat_errors_exhaust_authority_before_minimum_lease_window(
    monkeypatch,
):
    import app.core.container as container_module
    import app.domain.services.telephony.inbound_admission as admission_module

    payload = _admission_payload("pbx-heartbeat-db-loss")
    container = SimpleNamespace(db_pool=object(), redis=object())
    heartbeat = AsyncMock(side_effect=ConnectionError("database unavailable"))
    fence = AsyncMock(return_value=True)
    sleep = AsyncMock()
    clock = Mock(side_effect=(0.0, 0.0, 50.0))
    session = SimpleNamespace(_hangup_reason=None)
    monkeypatch.setattr(container_module, "get_container", lambda: container)
    monkeypatch.setattr(admission_module, "heartbeat_inbound_call", heartbeat)
    monkeypatch.setattr(
        lifecycle,
        "_state",
        lambda: SimpleNamespace(get_voice_session=lambda _call_id: session),
    )
    monkeypatch.setattr(lifecycle, "_fence_inbound_call_after_lease_loss", fence)
    monkeypatch.setattr(lifecycle, "_monotonic_time", clock)
    monkeypatch.setattr(lifecycle.asyncio, "sleep", sleep)

    await lifecycle._heartbeat_active_inbound_admission("pbx-heartbeat-db-loss", payload)

    assert heartbeat.await_count == 2
    sleep.assert_awaited_once_with(lifecycle._INBOUND_HEARTBEAT_ERROR_RETRY_S)
    fence.assert_awaited_once()
    assert session._hangup_reason == "inbound_admission_authority_unverifiable"


@pytest.mark.asyncio
async def test_post_answer_callback_rejected_by_ownership_fence_delegates_cleanup(
    monkeypatch,
):
    reject_handoff = Mock(return_value=True)
    adapter = SimpleNamespace(reject_pending_inbound_handoff=reject_handoff)
    state = SimpleNamespace(
        strict_ownership_active=True,
        is_telephony_owner=lambda: False,
    )
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)

    await lifecycle._on_new_call("pbx-inbound-fenced", _admission_payload())

    reject_handoff.assert_called_once_with(
        "pbx-inbound-fenced",
        reason="ownership_lost_before_registration",
    )
    assert "pbx-inbound-fenced" not in lifecycle._inbound_admissions_in_flight


@pytest.mark.asyncio
async def test_after_hours_transfer_uses_persisted_leg_and_accepts_durable_handoff(
    monkeypatch,
):
    import app.core.container as container_module
    import app.domain.services.call_status as call_status
    import app.domain.services.telephony.inbound_transfer as inbound_transfer

    call_id = "pbx-after-hours-transfer"
    payload = _admission_payload(call_id)
    payload["config_snapshot"]["inbound_config"].update(
        {
            "selected_action": "transfer",
            "selected_destination": "+15559876543",
            "transfer_failure_action": "hangup",
        }
    )
    payload["config_snapshot"]["schedule_decision"].update(
        {
            "selected_action": "transfer",
            "selected_destination": "+15559876543",
        }
    )
    order: list[str] = []
    transfer_kwargs: dict = {}

    class State:
        strict_ownership_active = False

        def is_telephony_owner(self):
            return True

        def voice_session_count(self):
            return 0

        def has_ringing_warmup(self, _call_id):
            return False

        async def register_cleanup_obligation(self, registered_call_id, **kwargs):
            assert registered_call_id == call_id
            order.append("register")
            assert kwargs["state"] == "active"

    class Adapter:
        connected = True
        name = "asterisk"

        async def transfer(self, *args, **kwargs):
            order.append("transfer")
            transfer_kwargs.update(kwargs)
            assert args == (call_id, "+15559876543", "blind")
            return {
                "status": "completed",
                "provider_leg_id": kwargs["provider_leg_id"],
            }

        def accept_inbound_handoff(self, accepted_call_id):
            assert accepted_call_id == call_id
            order.append("accept")
            return True

    adapter = Adapter()
    attempt = SimpleNamespace(
        inbound=True,
        destination="+15559876543",
        provider_leg_id="talky-xfer-0123456789abcdef0123",
        leg_id="44444444-4444-4444-4444-444444444444",
        is_replay=False,
    )
    complete = AsyncMock()
    monkeypatch.setattr(lifecycle, "_state", lambda: State())
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_start_inbound_runtime_guards", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "_get_orchestrator", lambda: object())
    monkeypatch.setattr(
        call_status,
        "record_call_state_by_provider_id",
        AsyncMock(),
    )
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=object(), redis=object()),
    )
    monkeypatch.setattr(
        inbound_transfer,
        "authorize_inbound_transfer",
        AsyncMock(return_value=attempt),
    )
    monkeypatch.setattr(inbound_transfer, "complete_inbound_transfer", complete)

    await lifecycle._on_new_call(call_id, payload)

    assert transfer_kwargs["provider_leg_id"] == attempt.provider_leg_id
    assert order == ["register", "transfer", "accept"]
    complete.assert_awaited_once()
    authorize_kwargs = inbound_transfer.authorize_inbound_transfer.await_args.kwargs
    assert authorize_kwargs["idempotency_key"].startswith("after-hours-")
    assert authorize_kwargs["actor_role"] == "system"
    assert authorize_kwargs["actor_type"] == "service"
    lifecycle._inbound_admissions_in_flight.pop(call_id, None)


@pytest.mark.asyncio
async def test_post_answer_inbound_capacity_rejection_delegates_before_release(
    monkeypatch,
):
    import app.core.container as container_module
    import app.domain.services.call_status as call_status

    call_id = "pbx-over-cap"
    rejected = Mock(return_value=True)
    adapter = SimpleNamespace(reject_pending_inbound_handoff=rejected)
    cancel_guards = AsyncMock()
    finalizer = AsyncMock()
    state = SimpleNamespace(
        strict_ownership_active=False,
        is_telephony_owner=lambda: True,
        voice_session_count=lambda: 1,
        register_cleanup_obligation=AsyncMock(),
    )
    monkeypatch.setattr(lifecycle, "_MAX_TELEPHONY_SESSIONS", 1)
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_start_inbound_runtime_guards", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "_cancel_inbound_runtime_guards", cancel_guards)
    monkeypatch.setattr(lifecycle, "_finalize_inbound_admission", finalizer)
    monkeypatch.setattr(call_status, "record_call_state_by_provider_id", AsyncMock())
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=object()),
    )

    await lifecycle._on_new_call(call_id, _admission_payload(call_id))

    cancel_guards.assert_awaited_once_with(call_id)
    rejected.assert_called_once_with(call_id, reason="pod_capacity")
    finalizer.assert_not_awaited()
    lifecycle._inbound_admissions_in_flight.pop(call_id, None)


@pytest.mark.asyncio
async def test_cancelled_preanswer_admission_retains_strict_global_slot_for_proof_owner(
    monkeypatch,
):
    import app.core.container as container_module
    import app.domain.services.global_concurrency as global_concurrency
    import app.domain.services.telephony.inbound_admission as admission_module

    entered = asyncio.Event()
    never = asyncio.Event()
    released: list[str] = []
    registered: list[tuple[str, str]] = []
    strict_args: list[bool] = []

    async def acquire(_redis, **kwargs):
        strict_args.append(kwargs["fail_closed"])
        return LeaseResult(acquired=True, reason="acquired", current=1)

    async def release(_redis, *, call_id):
        released.append(call_id)

    async def _register_cleanup(call_id, *, state, **_kwargs):
        registered.append((call_id, state))

    async def blocked_admit(_self, _request):
        entered.set()
        await never.wait()

    container = SimpleNamespace(
        is_initialized=True,
        db_pool=object(),
        redis=object(),
    )
    monkeypatch.setattr(container_module, "get_container", lambda: container)
    monkeypatch.setattr(
        lifecycle,
        "_state",
        lambda: SimpleNamespace(
            is_telephony_owner=lambda: True,
            voice_session_count=lambda: 0,
            register_cleanup_obligation=_register_cleanup,
        ),
    )
    monkeypatch.setattr(global_concurrency, "acquire_lease", acquire)
    monkeypatch.setattr(global_concurrency, "release_lease", release)
    monkeypatch.setattr(admission_module.InboundAdmissionService, "admit", blocked_admit)

    task = asyncio.create_task(
        lifecycle._admit_inbound_call(
            "pbx-inbound-1",
            {"called_did": "+15551234567", "caller_number": "+15557654321"},
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert strict_args == [True]
    assert registered == [("pbx-inbound-1", "termination_pending")]
    assert released == []
    assert "pbx-inbound-1" not in lifecycle._inbound_admissions_pending


@pytest.mark.asyncio
async def test_preanswer_cleanup_ledger_precedes_global_lease_and_durable_admission(
    monkeypatch,
):
    import app.core.container as container_module
    import app.domain.services.global_concurrency as global_concurrency
    import app.domain.services.telephony.inbound_admission as admission_module

    call_id = "pbx-preanswer-ledger-order"
    payload = _admission_payload(call_id)
    order: list[str] = []

    class State:
        @staticmethod
        def is_telephony_owner():
            return True

        @staticmethod
        def voice_session_count():
            return 0

        @staticmethod
        async def register_cleanup_obligation(_call_id, **kwargs):
            assert _call_id == call_id
            order.append(f"ledger:{kwargs['state']}")

    async def acquire(_redis, **kwargs):
        assert kwargs["call_id"] == call_id
        order.append("global_lease")
        return LeaseResult(acquired=True, reason="acquired", current=1)

    async def admit(_self, request):
        assert request.provider_call_id == call_id
        order.append("db_admission")
        return payload

    monkeypatch.setattr(lifecycle, "_state", lambda: State())
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(
            is_initialized=True,
            db_pool=object(),
            redis=object(),
        ),
    )
    monkeypatch.setattr(global_concurrency, "acquire_lease", acquire)
    monkeypatch.setattr(admission_module.InboundAdmissionService, "admit", admit)

    lifecycle._inbound_admissions_in_flight.pop(call_id, None)
    try:
        decision = await lifecycle._admit_inbound_call(
            call_id,
            {"called_did": "+15551234567", "caller_number": "+15557654321"},
        )
        assert decision == payload
        assert order == [
            "ledger:termination_pending",
            "global_lease",
            "db_admission",
            "ledger:termination_pending",
        ]
    finally:
        lifecycle._inbound_admissions_in_flight.pop(call_id, None)


@pytest.mark.asyncio
async def test_inbound_guards_start_before_optional_answered_status_io(monkeypatch):
    import app.core.container as container_module
    import app.domain.services.call_status as call_status

    order: list[str] = []
    status_entered = asyncio.Event()
    never = asyncio.Event()

    def start_guards(*_args, **_kwargs):
        order.append("guards")

    async def blocked_status(*_args, **_kwargs):
        order.append("status")
        status_entered.set()
        await never.wait()

    async def cancel_guards(call_id):
        order.append(f"cancel_guards:{call_id}")

    async def register_cleanup(_call_id, **kwargs):
        order.append(f"ledger:{kwargs['state']}")

    state = SimpleNamespace(
        strict_ownership_active=False,
        is_telephony_owner=lambda: True,
        voice_session_count=lambda: 0,
        register_cleanup_obligation=register_cleanup,
    )
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "_start_inbound_runtime_guards", start_guards)
    monkeypatch.setattr(lifecycle, "_cancel_inbound_runtime_guards", cancel_guards)
    monkeypatch.setattr(
        call_status,
        "record_call_state_by_provider_id",
        blocked_status,
    )
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=object()),
    )

    task = asyncio.create_task(lifecycle._on_new_call("pbx-inbound-1", _admission_payload()))
    await status_entered.wait()
    assert order[:3] == ["guards", "ledger:active", "status"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "cancel_guards:pbx-inbound-1" in order


@pytest.mark.asyncio
async def test_terminal_global_release_precedes_optional_status_projection(monkeypatch):
    import app.core.container as container_module
    import app.domain.services.call_status as call_status
    import app.domain.services.global_concurrency as global_concurrency

    order: list[str] = []

    async def release(_redis, *, call_id):
        order.append(f"release:{call_id}")

    async def status(*_args, **_kwargs):
        assert order == ["release:terminal-1"]
        order.append("status")

    state = SimpleNamespace(
        clear_first_speaker=lambda _cid: None,
        pop_voice_session=lambda _cid: None,
        remove_gateway_sessions_for_call=lambda _cid: None,
    )
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "_pop_ringing_warmup", lambda _cid: None)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: None)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda *_a, **_k: None)
    monkeypatch.setattr(global_concurrency, "release_lease", release)
    monkeypatch.setattr(call_status, "record_call_state_by_provider_id", status)
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=False, redis=None, db_pool=None),
    )
    lifecycle._ended_calls_in_flight.discard("terminal-1")

    await lifecycle._on_call_ended("terminal-1")

    assert order == ["release:terminal-1", "status"]
    lifecycle._ended_calls_in_flight.discard("terminal-1")


@pytest.mark.parametrize(
    ("release_only", "durable_label"),
    [(True, "durable_release"), (False, "durable_finalize")],
)
@pytest.mark.asyncio
async def test_canonical_inbound_finalizer_releases_global_slot_after_durable_success(
    monkeypatch,
    release_only,
    durable_label,
):
    import app.core.container as container_module
    import app.domain.services.global_concurrency as global_concurrency
    import app.domain.services.telephony.inbound_admission as admission_module

    pbx_call_id = f"canonical-{'release' if release_only else 'finalize'}"
    payload = _admission_payload(pbx_call_id)
    dedupe_key = ("asterisk", pbx_call_id)
    redis = object()
    order: list[str] = []
    popped: list[str] = []

    async def durable_release(_self, **_kwargs):
        assert release_only
        order.append("durable_release")

    async def durable_finalize(_self, _request):
        assert not release_only
        assert _request.outcome == "answered"
        order.append("durable_finalize")

    async def release_global(actual_redis, *, call_id):
        assert actual_redis is redis
        assert call_id == pbx_call_id
        assert order == [durable_label]
        order.append("global_release")

    def pop_cached(call_id):
        popped.append(call_id)
        order.append("cache_pop")

    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=object(), redis=redis),
    )
    monkeypatch.setattr(
        admission_module.InboundAdmissionService,
        "release",
        durable_release,
    )
    monkeypatch.setattr(
        admission_module.InboundAdmissionService,
        "finalize",
        durable_finalize,
    )
    monkeypatch.setattr(global_concurrency, "release_lease_strict", release_global)
    monkeypatch.setattr(
        lifecycle,
        "get_adapter",
        lambda: SimpleNamespace(pop_inbound_admission=pop_cached),
    )

    lifecycle._inbound_admissions_finalized.discard(dedupe_key)
    lifecycle._inbound_admissions_in_flight[pbx_call_id] = dict(payload)
    try:
        await lifecycle._finalize_inbound_admission(
            pbx_call_id,
            payload,
            terminal_status="failed" if release_only else "completed",
            duration_seconds=0 if release_only else 17,
            outcome="answered",
            reason="focused_test",
            release_only=release_only,
        )
        # A repeated terminal callback is a no-op: it cannot settle again,
        # release the Redis slot again, or pop a newly-owned cache entry.
        await lifecycle._finalize_inbound_admission(
            pbx_call_id,
            payload,
            terminal_status="failed" if release_only else "completed",
            duration_seconds=0 if release_only else 17,
            outcome="answered",
            reason="focused_test_duplicate",
            release_only=release_only,
        )

        assert order == [durable_label, "global_release", "cache_pop"]
        assert popped == [pbx_call_id]
        assert dedupe_key in lifecycle._inbound_admissions_finalized
        assert pbx_call_id not in lifecycle._inbound_admissions_in_flight
    finally:
        lifecycle._inbound_admissions_finalized.discard(dedupe_key)
        lifecycle._inbound_admissions_in_flight.pop(pbx_call_id, None)


@pytest.mark.parametrize("release_only", [True, False])
@pytest.mark.asyncio
async def test_canonical_inbound_finalizer_keeps_global_slot_until_durable_retry_succeeds(
    monkeypatch,
    release_only,
):
    import app.core.container as container_module
    import app.domain.services.global_concurrency as global_concurrency
    import app.domain.services.telephony.inbound_admission as admission_module

    pbx_call_id = f"canonical-retry-{'release' if release_only else 'finalize'}"
    payload = _admission_payload(pbx_call_id)
    dedupe_key = ("asterisk", pbx_call_id)
    order: list[str] = []
    attempts = 0

    async def durable_operation(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        order.append(f"durable:{attempts}")
        if attempts == 1:
            raise RuntimeError("durable write failed")

    async def wrong_operation(*_args, **_kwargs):
        raise AssertionError("wrong durable finalization path")

    async def release_global(_redis, *, call_id):
        assert call_id == pbx_call_id
        assert order == ["durable:1", "durable:2"]
        order.append("global_release")

    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=object(), redis=object()),
    )
    monkeypatch.setattr(
        admission_module.InboundAdmissionService,
        "release",
        durable_operation if release_only else wrong_operation,
    )
    monkeypatch.setattr(
        admission_module.InboundAdmissionService,
        "finalize",
        wrong_operation if release_only else durable_operation,
    )
    monkeypatch.setattr(global_concurrency, "release_lease_strict", release_global)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: None)

    lifecycle._inbound_admissions_finalized.discard(dedupe_key)
    lifecycle._inbound_admissions_in_flight[pbx_call_id] = dict(payload)
    try:
        with pytest.raises(RuntimeError, match="durable write failed"):
            await lifecycle._finalize_inbound_admission(
                pbx_call_id,
                payload,
                terminal_status="failed" if release_only else "completed",
                duration_seconds=0 if release_only else 9,
                release_only=release_only,
            )

        assert order == ["durable:1"]
        assert dedupe_key not in lifecycle._inbound_admissions_finalized
        assert pbx_call_id in lifecycle._inbound_admissions_in_flight

        await lifecycle._finalize_inbound_admission(
            pbx_call_id,
            payload,
            terminal_status="failed" if release_only else "completed",
            duration_seconds=0 if release_only else 9,
            release_only=release_only,
        )

        assert order == ["durable:1", "durable:2", "global_release"]
        assert dedupe_key in lifecycle._inbound_admissions_finalized
    finally:
        lifecycle._inbound_admissions_finalized.discard(dedupe_key)
        lifecycle._inbound_admissions_in_flight.pop(pbx_call_id, None)


@pytest.mark.asyncio
async def test_unclaimed_proof_finalizer_retries_strict_global_release_before_completion(
    monkeypatch,
):
    import app.core.container as container_module
    import app.domain.services.global_concurrency as global_concurrency

    call_id = "unclaimed-after-admission-timeout"
    dedupe_key = ("asterisk", call_id)
    attempts = 0

    async def release_global(_redis, *, call_id: str):
        nonlocal attempts
        assert call_id == "unclaimed-after-admission-timeout"
        attempts += 1
        if attempts == 1:
            raise ConnectionError("ambiguous Redis release")

    async def no_durable_admission(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=object(), redis=object()),
    )
    monkeypatch.setattr(global_concurrency, "release_lease_strict", release_global)
    monkeypatch.setattr(
        lifecycle,
        "_find_inbound_admission_by_provider_identity",
        no_durable_admission,
    )
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: None)
    lifecycle._inbound_admissions_finalized.discard(dedupe_key)
    try:
        with pytest.raises(ConnectionError, match="ambiguous Redis release"):
            await lifecycle._finalize_inbound_admission(
                call_id,
                {},
                terminal_status="failed",
                duration_seconds=0,
                reason="admission_timeout",
                release_only=True,
            )
        assert dedupe_key not in lifecycle._inbound_admissions_finalized

        await lifecycle._finalize_inbound_admission(
            call_id,
            {},
            terminal_status="failed",
            duration_seconds=0,
            reason="admission_timeout",
            release_only=True,
        )
        assert attempts == 2
        assert dedupe_key not in lifecycle._inbound_admissions_finalized
    finally:
        lifecycle._inbound_admissions_finalized.discard(dedupe_key)


@pytest.mark.asyncio
async def test_unclaimed_proof_recovers_lost_committed_admission_before_capacity_release(
    monkeypatch,
):
    import app.core.container as container_module
    import app.domain.services.global_concurrency as global_concurrency
    import app.domain.services.telephony.inbound_admission as admission_module

    call_id = "committed-before-timeout"
    dedupe_key = ("asterisk", call_id)
    order: list[str] = []

    async def find_committed(_container, **identity):
        assert identity == {
            "provider": "asterisk",
            "provider_call_id": call_id,
        }
        return _admission_payload(call_id)

    async def durable_release(_self, **kwargs):
        assert kwargs["call_id"] == _admission_payload(call_id)["call_id"]
        assert kwargs["provider_call_id"] == call_id
        order.append("durable_release")

    async def release_global(_redis, *, call_id: str):
        assert order == ["durable_release"]
        order.append(f"global_release:{call_id}")

    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(
            is_initialized=True,
            db_pool=object(),
            redis=object(),
        ),
    )
    monkeypatch.setattr(
        lifecycle,
        "_find_inbound_admission_by_provider_identity",
        find_committed,
    )
    monkeypatch.setattr(
        admission_module.InboundAdmissionService,
        "release",
        durable_release,
    )
    monkeypatch.setattr(global_concurrency, "release_lease_strict", release_global)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: None)

    lifecycle._inbound_admissions_finalized.discard(dedupe_key)
    try:
        await lifecycle._finalize_inbound_admission(
            call_id,
            {},
            terminal_status="failed",
            duration_seconds=0,
            reason="admission_timeout",
            release_only=True,
        )

        assert order == ["durable_release", f"global_release:{call_id}"]
        assert dedupe_key in lifecycle._inbound_admissions_finalized
    finally:
        lifecycle._inbound_admissions_finalized.discard(dedupe_key)


@pytest.mark.asyncio
async def test_normal_inbound_terminal_path_has_no_early_global_release(monkeypatch):
    import app.core.container as container_module
    import app.domain.services.call_status as call_status
    import app.domain.services.global_concurrency as global_concurrency
    import app.domain.services.telephony.inbound_admission as admission_module
    import app.domain.services.telephony.inbound_transfer as inbound_transfer

    pbx_call_id = "terminal-inbound-canonical"
    payload = _admission_payload(pbx_call_id)
    dedupe_key = ("asterisk", pbx_call_id)
    redis = object()
    order: list[str] = []

    async def status_projection(*_args, **_kwargs):
        order.append("status_projection")

    async def durable_finalize(_self, _request):
        assert "global_release" not in order
        assert "transfers_finalized" in order
        order.append("durable_finalize")

    async def release_global(actual_redis, *, call_id):
        assert actual_redis is redis
        assert call_id == pbx_call_id
        assert "durable_finalize" in order
        order.append("global_release")

    async def cancel_guards(_call_id):
        order.append("guards_cancelled")

    async def finalize_transfers(*_args, **_kwargs):
        order.append("transfers_finalized")

    class _State:
        @staticmethod
        def clear_first_speaker(_call_id):
            return None

        @staticmethod
        def pop_voice_session(_call_id):
            return None

        @staticmethod
        def remove_gateway_sessions_for_call(_call_id):
            return None

    adapter = SimpleNamespace(
        get_inbound_admission=lambda _call_id: dict(payload),
        pop_inbound_admission=lambda _call_id: None,
    )
    container = SimpleNamespace(
        is_initialized=True,
        db_pool=object(),
        redis=redis,
    )

    monkeypatch.setattr(container_module, "get_container", lambda: container)
    monkeypatch.setattr(call_status, "record_call_state_by_provider_id", status_projection)
    monkeypatch.setattr(global_concurrency, "release_lease_strict", release_global)
    monkeypatch.setattr(
        admission_module.InboundAdmissionService,
        "finalize",
        durable_finalize,
    )
    monkeypatch.setattr(
        admission_module.InboundAdmissionService,
        "release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("normal terminal path must finalize, not release")
        ),
    )
    monkeypatch.setattr(
        inbound_transfer,
        "finalize_connected_inbound_transfers",
        finalize_transfers,
    )
    monkeypatch.setattr(lifecycle, "_state", lambda: _State())
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_pop_ringing_warmup", lambda _call_id: None)
    monkeypatch.setattr(lifecycle, "_cancel_inbound_runtime_guards", cancel_guards)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _call_id: None)

    lifecycle._ended_calls_in_flight.discard(pbx_call_id)
    lifecycle._inbound_admissions_finalized.discard(dedupe_key)
    lifecycle._inbound_admissions_in_flight[pbx_call_id] = dict(payload)
    try:
        await lifecycle._on_call_ended(pbx_call_id)

        assert order.count("global_release") == 1
        assert order.index("transfers_finalized") < order.index("durable_finalize")
        assert order.index("durable_finalize") < order.index("global_release")
    finally:
        lifecycle._ended_calls_in_flight.discard(pbx_call_id)
        lifecycle._inbound_admissions_finalized.discard(dedupe_key)
        lifecycle._inbound_admissions_in_flight.pop(pbx_call_id, None)


@pytest.mark.asyncio
async def test_call_end_retries_durable_inbound_finalization_before_return(
    monkeypatch,
):
    import app.core.container as container_module
    import app.domain.services.call_status as call_status
    import app.domain.services.telephony.inbound_transfer as inbound_transfer

    pbx_call_id = "terminal-retry-1"
    payload = _admission_payload(pbx_call_id)
    attempts = 0

    async def status_projection(*_args, **_kwargs):
        return None

    async def finalize_with_one_failure(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary durable database failure")

    async def no_op(*_args, **_kwargs):
        return None

    class State:
        @staticmethod
        def clear_first_speaker(_call_id):
            return None

        @staticmethod
        def pop_voice_session(_call_id):
            return None

        @staticmethod
        def remove_gateway_sessions_for_call(_call_id):
            return None

    adapter = SimpleNamespace(
        get_inbound_admission=lambda _call_id: dict(payload),
    )
    monkeypatch.setenv("INBOUND_FINALIZE_RETRY_INITIAL_S", "0.01")
    monkeypatch.setenv("INBOUND_FINALIZE_RETRY_MAX_S", "0.01")
    monkeypatch.setattr(lifecycle, "_state", lambda: State())
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_pop_ringing_warmup", lambda _call_id: None)
    monkeypatch.setattr(lifecycle, "_cancel_inbound_runtime_guards", no_op)
    monkeypatch.setattr(lifecycle, "_finalize_inbound_admission", finalize_with_one_failure)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _call_id: None)
    monkeypatch.setattr(call_status, "record_call_state_by_provider_id", status_projection)
    monkeypatch.setattr(
        inbound_transfer,
        "finalize_connected_inbound_transfers",
        no_op,
    )
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(
            is_initialized=True,
            db_pool=object(),
            redis=None,
        ),
    )

    lifecycle._ended_calls_in_flight.discard(pbx_call_id)
    lifecycle._inbound_admissions_in_flight[pbx_call_id] = dict(payload)
    try:
        await lifecycle._on_call_ended(pbx_call_id)
        assert attempts == 2
    finally:
        lifecycle._ended_calls_in_flight.discard(pbx_call_id)
        lifecycle._inbound_admissions_in_flight.pop(pbx_call_id, None)


@pytest.mark.asyncio
async def test_outbound_database_outage_retains_ledger_and_logical_marker(monkeypatch):
    import app.core.container as container_module
    import app.domain.services.call_status as call_status
    import app.domain.services.global_concurrency as global_concurrency

    call_id = "outbound-db-down"
    registered: list[tuple[str, str]] = []
    acknowledged: list[str] = []

    class BrokenAcquire:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, *_args):
            return None

    class BrokenPool:
        def acquire(self, *_args, **_kwargs):
            return BrokenAcquire()

    class State:
        @staticmethod
        def clear_first_speaker(_call_id):
            return None

        @staticmethod
        def pop_voice_session(_call_id):
            return None

        @staticmethod
        def remove_gateway_sessions_for_call(_call_id):
            return None

        @staticmethod
        async def register_cleanup_obligation(actual_call_id, **kwargs):
            registered.append((actual_call_id, kwargs["state"]))

        @staticmethod
        async def acknowledge_orphan_recovery(actual_call_id):
            acknowledged.append(actual_call_id)

    async def no_op(*_args, **_kwargs):
        return None

    container = SimpleNamespace(
        is_initialized=True,
        db_pool=BrokenPool(),
        db_client=object(),
        redis=None,
        _queue_service=None,
    )
    monkeypatch.setattr(container_module, "get_container", lambda: container)
    monkeypatch.setattr(lifecycle, "_state", lambda: State())
    monkeypatch.setattr(
        lifecycle,
        "get_adapter",
        lambda: SimpleNamespace(
            get_inbound_admission=lambda _call_id: None,
            get_hangup_cause=lambda _call_id: None,
        ),
    )
    monkeypatch.setattr(lifecycle, "_pop_ringing_warmup", lambda _call_id: None)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _call_id: None)
    monkeypatch.setattr(call_status, "record_call_state_by_provider_id", no_op)
    monkeypatch.setattr(global_concurrency, "release_lease", no_op)

    lifecycle._ended_calls_in_flight.discard(call_id)
    lifecycle._ended_calls_logically_completed.discard(call_id)
    try:
        result = await lifecycle._on_call_ended(call_id)
        assert result is False
        assert registered == [(call_id, "termination_pending")]
        assert acknowledged == []
        assert call_id not in lifecycle._ended_calls_logically_completed
        assert call_id not in lifecycle._ended_calls_in_flight
    finally:
        lifecycle._ended_calls_in_flight.discard(call_id)
        lifecycle._ended_calls_logically_completed.discard(call_id)


@pytest.mark.asyncio
async def test_normal_inbound_transfer_finalize_failure_retains_retry_ledger(
    monkeypatch,
):
    import app.core.container as container_module
    import app.domain.services.call_status as call_status
    import app.domain.services.telephony.inbound_transfer as inbound_transfer

    call_id = "inbound-transfer-settlement-fails"
    payload = _admission_payload(call_id)
    registered: list[str] = []
    acknowledged: list[str] = []
    parent_finalize_calls: list[str] = []

    class State:
        @staticmethod
        def clear_first_speaker(_call_id):
            return None

        @staticmethod
        def pop_voice_session(_call_id):
            return None

        @staticmethod
        def remove_gateway_sessions_for_call(_call_id):
            return None

        @staticmethod
        async def register_cleanup_obligation(actual_call_id, **_kwargs):
            registered.append(actual_call_id)

        @staticmethod
        async def acknowledge_orphan_recovery(actual_call_id):
            acknowledged.append(actual_call_id)

    async def no_op(*_args, **_kwargs):
        return None

    async def transfer_failure(*_args, **_kwargs):
        raise RuntimeError("linked-leg lease settlement failed")

    async def parent_finalize(*_args, **_kwargs):
        parent_finalize_calls.append(call_id)

    container = SimpleNamespace(
        is_initialized=True,
        db_pool=object(),
        db_client=object(),
        redis=object(),
        _queue_service=None,
    )
    monkeypatch.setattr(container_module, "get_container", lambda: container)
    monkeypatch.setattr(lifecycle, "_state", lambda: State())
    monkeypatch.setattr(
        lifecycle,
        "get_adapter",
        lambda: SimpleNamespace(
            get_inbound_admission=lambda _call_id: dict(payload),
            pop_inbound_admission=lambda _call_id: None,
        ),
    )
    monkeypatch.setattr(lifecycle, "_pop_ringing_warmup", lambda _call_id: None)
    monkeypatch.setattr(lifecycle, "_cancel_inbound_runtime_guards", no_op)
    monkeypatch.setattr(lifecycle, "_finalize_inbound_admission", parent_finalize)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _call_id: None)
    monkeypatch.setattr(call_status, "record_call_state_by_provider_id", no_op)
    monkeypatch.setattr(
        inbound_transfer,
        "finalize_connected_inbound_transfers",
        transfer_failure,
    )

    lifecycle._ended_calls_in_flight.discard(call_id)
    lifecycle._ended_calls_logically_completed.discard(call_id)
    lifecycle._inbound_admissions_in_flight[call_id] = dict(payload)
    try:
        result = await lifecycle._on_call_ended(call_id)
        assert result is False
        assert registered == [call_id]
        assert acknowledged == []
        assert parent_finalize_calls == []
        assert call_id not in lifecycle._ended_calls_logically_completed
        assert call_id not in lifecycle._ended_calls_in_flight
    finally:
        lifecycle._ended_calls_in_flight.discard(call_id)
        lifecycle._ended_calls_logically_completed.discard(call_id)
        lifecycle._inbound_admissions_in_flight.pop(call_id, None)


@pytest.mark.asyncio
async def test_registered_inbound_session_is_not_double_counted_for_local_capacity(
    monkeypatch,
):
    import app.core.container as container_module
    import app.domain.services.global_concurrency as global_concurrency
    import app.domain.services.telephony.inbound_admission as admission_module

    existing_id = "already-registered"
    admitted: list[str] = []

    class State:
        @staticmethod
        def is_telephony_owner():
            return True

        @staticmethod
        def voice_session_count():
            return 1

        @staticmethod
        def get_voice_session(call_id):
            return object() if call_id == existing_id else None

        @staticmethod
        async def register_cleanup_obligation(_call_id, **_kwargs):
            return None

    async def acquire(_redis, **_kwargs):
        return LeaseResult(acquired=True, reason="acquired", current=2)

    async def release(_redis, *, call_id):
        return None

    async def admit(_self, request):
        admitted.append(request.provider_call_id)
        return {"allowed": False, "reason": "focused_capacity_probe"}

    monkeypatch.setattr(lifecycle, "_MAX_TELEPHONY_SESSIONS", 2)
    monkeypatch.setattr(lifecycle, "_state", lambda: State())
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(
            is_initialized=True,
            db_pool=object(),
            redis=object(),
        ),
    )
    monkeypatch.setattr(global_concurrency, "acquire_lease", acquire)
    monkeypatch.setattr(global_concurrency, "release_lease", release)
    monkeypatch.setattr(admission_module.InboundAdmissionService, "admit", admit)
    previous_in_flight = dict(lifecycle._inbound_admissions_in_flight)
    previous_pending = set(lifecycle._inbound_admissions_pending)
    lifecycle._inbound_admissions_in_flight.clear()
    lifecycle._inbound_admissions_pending.clear()
    lifecycle._inbound_admissions_in_flight[existing_id] = _admission_payload(existing_id)
    try:
        await lifecycle._admit_inbound_call(
            "new-inbound",
            {"called_did": "+15551234567", "caller_number": "+15557654321"},
        )
        assert admitted == ["new-inbound"]
    finally:
        lifecycle._inbound_admissions_in_flight.clear()
        lifecycle._inbound_admissions_in_flight.update(previous_in_flight)
        lifecycle._inbound_admissions_pending.clear()
        lifecycle._inbound_admissions_pending.update(previous_pending)


@pytest.mark.asyncio
async def test_release_only_finalizer_cancels_preaccept_runtime_guards(monkeypatch):
    import app.core.container as container_module
    import app.domain.services.global_concurrency as global_concurrency
    import app.domain.services.telephony.inbound_admission as admission_module

    pbx_call_id = "preaccept-guard-release"
    payload = _admission_payload(pbx_call_id)
    dedupe_key = ("asterisk", pbx_call_id)
    never = asyncio.Event()
    heartbeat_started = asyncio.Event()
    deadline_started = asyncio.Event()
    cancelled: list[str] = []
    order: list[str] = []

    async def guard(label, started):
        started.set()
        try:
            await never.wait()
        except asyncio.CancelledError:
            cancelled.append(label)
            raise

    async def durable_release(_self, **_kwargs):
        assert pbx_call_id not in lifecycle._inbound_heartbeat_tasks
        assert pbx_call_id not in lifecycle._inbound_deadline_tasks
        assert sorted(cancelled) == ["deadline", "heartbeat"]
        order.append("durable_release")

    async def release_global(_redis, *, call_id):
        assert call_id == pbx_call_id
        order.append("global_release")

    lifecycle._inbound_heartbeat_tasks[pbx_call_id] = asyncio.create_task(
        guard("heartbeat", heartbeat_started)
    )
    lifecycle._inbound_deadline_tasks[pbx_call_id] = asyncio.create_task(
        guard("deadline", deadline_started)
    )
    await heartbeat_started.wait()
    await deadline_started.wait()

    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=object(), redis=object()),
    )
    monkeypatch.setattr(
        admission_module.InboundAdmissionService,
        "release",
        durable_release,
    )
    monkeypatch.setattr(global_concurrency, "release_lease_strict", release_global)
    monkeypatch.setattr(
        lifecycle,
        "get_adapter",
        lambda: SimpleNamespace(pop_inbound_admission=lambda _call_id: None),
    )
    lifecycle._inbound_admissions_finalized.discard(dedupe_key)
    lifecycle._inbound_admissions_in_flight[pbx_call_id] = dict(payload)
    try:
        await lifecycle._finalize_inbound_admission(
            pbx_call_id,
            payload,
            terminal_status="failed",
            duration_seconds=0,
            reason="terminal_before_acceptance",
            release_only=True,
        )
        assert order == ["durable_release", "global_release"]
    finally:
        lifecycle._inbound_admissions_finalized.discard(dedupe_key)
        lifecycle._inbound_admissions_in_flight.pop(pbx_call_id, None)
        for registry in (
            lifecycle._inbound_heartbeat_tasks,
            lifecycle._inbound_deadline_tasks,
        ):
            task = registry.pop(pbx_call_id, None)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
