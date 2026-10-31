"""Wiring tests for transfer-aware telephony restart recovery.

The database claim helper has its own transaction-level tests.  These tests
protect the lifecycle boundary around it: only the exclusive telephony owner
may scan, locally owned calls are excluded, the durable claim happens before
the termination-pending scan, and PBX teardown receives every persisted leg
identity.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.domain.services.telephony import lifecycle


def test_normal_lifespan_wires_answer_fence_before_connect_and_readiness():
    """Boot auto-connect must match the manual telephony-start contract."""

    from app import main

    source = inspect.getsource(main.lifespan)
    answer_wiring = source.index("set_inbound_answered_persist_callback")
    terminal_wiring = source.index("set_inbound_terminal_proof_persist_callback")
    connect = source.index("await _tb._adapter.connect()")
    live_validation = source.index("validate_live_production_inbound_adapter(", connect)
    readiness = source.index("_tb.ensure_session_management_started()", connect)

    assert answer_wiring < terminal_wiring < connect < live_validation < readiness
    assert "_tb._persist_inbound_answered" in source
    assert "_tb._persist_inbound_terminal_proof" in source


class _OwnerState:
    def __init__(self, *, owner: bool = True) -> None:
        self._owner = owner

    async def recover_orphans(self, *, limit=None):
        del limit
        return []

    def is_telephony_owner(self) -> bool:
        return self._owner

    def iter_voice_session_items(self):
        return [("voice-local", object())]

    def iter_ringing_warmup_keys(self):
        return ["warmup-local"]


@pytest.mark.asyncio
async def test_exclusive_owner_claims_before_scan_and_cleans_all_exact_legs(
    monkeypatch,
):
    import app.core.container as container_module
    from app.domain.services.telephony import transfer_restart_recovery

    state = _OwnerState()
    pool = object()
    order: list[str] = []
    force_calls: list[tuple[str, bool, tuple[str, ...]]] = []
    previous_admissions = dict(lifecycle._inbound_admissions_in_flight)
    lifecycle._inbound_admissions_in_flight.clear()
    lifecycle._inbound_admissions_in_flight["admission-local"] = {"direction": "inbound"}

    async def claim(
        actual_pool,
        *,
        exclusive_owner_confirmed,
        excluded_provider_call_ids,
        **_kwargs,
    ):
        order.append("claim")
        assert actual_pool is pool
        assert exclusive_owner_confirmed is True
        assert set(excluded_provider_call_ids) == {
            "admission-local",
            "voice-local",
            "warmup-local",
        }
        return [SimpleNamespace(provider_call_id="claimed-parent")]

    async def load_candidates():
        order.append("load")
        return [
            {
                "call_id": "claimed-parent",
                "pod_id": "database:termination_pending",
                "tenant_id": "tenant-1",
                "provider": "asterisk",
                "durable_call_id": "durable-parent",
                "_termination_pending": True,
                "_inbound_settlement_pending": True,
                "_has_redis_ledger": False,
            }
        ]

    async def hydrate(call_id, entry):
        order.append("hydrate")
        assert call_id == "claimed-parent"
        assert entry["durable_call_id"] == "durable-parent"
        return {
            "provider_leg_ids": ["target-leg-1", "target-leg-2"],
            "provider": "asterisk",
            "logical_settled": False,
        }

    async def force_end(
        call_id,
        *,
        require_confirmation,
        provider_leg_ids,
        recovery_context,
        acknowledge_ledger,
    ):
        order.append("force")
        force_calls.append((call_id, require_confirmation, tuple(provider_leg_ids)))
        assert recovery_context["provider_leg_ids"] == [
            "target-leg-1",
            "target-leg-2",
        ]
        assert acknowledge_ledger is False
        return True

    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=pool),
    )
    monkeypatch.setattr(
        transfer_restart_recovery,
        "claim_inbound_transfer_takeovers",
        claim,
    )
    monkeypatch.setattr(
        lifecycle,
        "_load_termination_pending_candidates",
        load_candidates,
    )
    monkeypatch.setattr(
        lifecycle,
        "_hydrate_orphan_recovery_context",
        hydrate,
    )
    monkeypatch.setattr(lifecycle, "_force_end_and_hangup", force_end)
    lifecycle._orphan_recovery_in_flight.discard("claimed-parent")
    lifecycle._orphan_recovery_contexts_by_call.pop("claimed-parent", None)

    try:
        assert await lifecycle.recover_orphaned_calls() == 1
        assert order == ["claim", "load", "hydrate", "force"]
        assert force_calls == [
            (
                "claimed-parent",
                True,
                ("target-leg-1", "target-leg-2"),
            )
        ]
    finally:
        lifecycle._inbound_admissions_in_flight.clear()
        lifecycle._inbound_admissions_in_flight.update(previous_admissions)
        lifecycle._orphan_recovery_in_flight.discard("claimed-parent")
        lifecycle._orphan_recovery_contexts_by_call.pop("claimed-parent", None)


@pytest.mark.asyncio
async def test_non_owner_never_runs_database_takeover_claim(monkeypatch):
    import app.core.container as container_module
    from app.domain.services.telephony import transfer_restart_recovery

    claim_calls = 0
    load_calls = 0

    async def claim(*_args, **_kwargs):
        nonlocal claim_calls
        claim_calls += 1
        return []

    async def load_candidates():
        nonlocal load_calls
        load_calls += 1
        return []

    monkeypatch.setattr(lifecycle, "_state", lambda: _OwnerState(owner=False))
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(
        transfer_restart_recovery,
        "claim_inbound_transfer_takeovers",
        claim,
    )
    monkeypatch.setattr(
        lifecycle,
        "_load_termination_pending_candidates",
        load_candidates,
    )

    assert await lifecycle.recover_orphaned_calls() == 0
    assert claim_calls == 0
    assert load_calls == 0


@pytest.mark.asyncio
async def test_owner_loss_mid_batch_stops_before_next_pbx_request(monkeypatch):
    from app.domain.services.telephony import transfer_restart_recovery

    class FlippingOwner(_OwnerState):
        def __init__(self):
            super().__init__()
            self.checks = 0

        def is_telephony_owner(self) -> bool:
            self.checks += 1
            return self.checks <= 4

    state = FlippingOwner()
    forced = []
    hydrated = []

    async def claim(*_args, **_kwargs):
        return []

    async def load_candidates():
        return [
            {
                "call_id": "first-parent",
                "durable_call_id": "11111111-1111-1111-1111-111111111111",
                "_has_redis_ledger": False,
            },
            {
                "call_id": "second-parent",
                "durable_call_id": "22222222-2222-2222-2222-222222222222",
                "_has_redis_ledger": False,
            },
        ]

    async def hydrate(call_id, entry):
        hydrated.append(call_id)
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": [],
            "durable_call_id": entry["durable_call_id"],
            "direction": "outbound",
            "provider": "asterisk",
            "logical_settled": True,
        }

    async def force_end(call_id, **_kwargs):
        forced.append(call_id)
        return True

    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(transfer_restart_recovery, "claim_inbound_transfer_takeovers", claim)
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", load_candidates)
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_force_end_and_hangup", force_end)

    assert await lifecycle.recover_orphaned_calls() == 1
    assert forced == ["first-parent"]
    assert hydrated == ["first-parent"]


@pytest.mark.asyncio
async def test_owner_loss_during_hydration_stops_before_pbx_request(monkeypatch):
    from app.domain.services.telephony import transfer_restart_recovery

    class HydrationFlipOwner(_OwnerState):
        def __init__(self):
            super().__init__()
            self.checks = 0

        def is_telephony_owner(self) -> bool:
            self.checks += 1
            return self.checks <= 3

    state = HydrationFlipOwner()
    forced = []

    async def claim(*_args, **_kwargs):
        return []

    async def load_candidates():
        return [
            {
                "call_id": "hydrated-parent",
                "durable_call_id": "11111111-1111-1111-1111-111111111111",
                "_has_redis_ledger": False,
            }
        ]

    async def hydrate(call_id, entry):
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": [],
            "durable_call_id": entry["durable_call_id"],
            "direction": "outbound",
            "logical_settled": False,
        }

    async def force_end(call_id, **_kwargs):
        forced.append(call_id)
        return True

    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(transfer_restart_recovery, "claim_inbound_transfer_takeovers", claim)
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", load_candidates)
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_force_end_and_hangup", force_end)

    assert await lifecycle.recover_orphaned_calls() == 0
    assert forced == []


class _DelayedOwnerState:
    def __init__(self) -> None:
        self.acquire_calls = 0
        self.heartbeat_calls = 0
        self.shutdown_calls = 0

    async def acquire_telephony_ownership(self) -> bool:
        self.acquire_calls += 1
        return self.acquire_calls >= 2

    async def start_heartbeat(self) -> None:
        self.heartbeat_calls += 1

    async def telephony_owner_id(self):
        return "previous-owner"

    def voice_session_count(self) -> int:
        return 0

    def iter_voice_session_items(self):
        return []

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


class _ConnectedAdapter:
    connected = True
    name = "test-adapter"

    def owned_call_ids(self):
        return set()

    async def disconnect(self, **_kwargs) -> None:
        self.connected = False


class _Container:
    db_pool = None
    redis = None
    redis_pubsub = None

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class _AsyncioWithoutRetryDelay:
    """Proxy real asyncio while making the hard-coded owner retry immediate."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, _delay, result=None):
        await asyncio.sleep(0)
        return result


@pytest.mark.asyncio
async def test_delayed_ownership_connects_then_immediately_recovers_orphans(
    monkeypatch,
):
    """The stale-lock retry must not wait for a later watchdog interval."""

    import app.core.container as container_module
    import app.core.legacy_campaign_audit as legacy_audit
    import app.core.prod_gate as prod_gate
    import app.core.redis_durability as redis_durability
    import app.core.sentry_init as sentry_init
    import app.core.telemetry as telemetry
    import app.core.validation as validation
    import app.domain.services.provider_cost_ledger as provider_cost_ledger
    import app.infrastructure.telephony.adapter_factory as adapter_factory
    from app import main
    from app.api.v1.endpoints import telephony_bridge
    from app.core import inbound_startup
    from app.domain.services.telephony import state_backend
    from app.services.scripts.prompts import bodies as prompt_bodies

    state = _DelayedOwnerState()
    adapter = _ConnectedAdapter()
    order: list[str] = []
    recovered = asyncio.Event()

    async def platform_enabled(_pool, *, environment):
        assert environment == "development"
        return False

    async def recover():
        order.append("recover")
        recovered.set()
        return 1

    async def start_flusher(_pool_factory):
        return None

    async def stop_flusher():
        return None

    async def durability_probe(_redis):
        return SimpleNamespace()

    async def campaign_audit(_pool):
        return SimpleNamespace()

    async def record_versions(_pool):
        return 0

    async def load_versions(_pool):
        return 0

    original_ensure_started = telephony_bridge.ensure_session_management_started

    def ensure_started():
        # Auto-connect must finish before the delayed-owner recovery call.
        order.append("connect")

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(main, "asyncio", _AsyncioWithoutRetryDelay())
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: _Container(),
    )
    monkeypatch.setattr(prod_gate, "enforce_production_gate", lambda: None)
    monkeypatch.setattr(sentry_init, "init_sentry", lambda: None)
    monkeypatch.setattr(telemetry, "setup_telemetry", lambda _app: None)
    monkeypatch.setattr(telemetry, "shutdown_telemetry", lambda: None)
    monkeypatch.setattr(validation, "validate_providers_on_startup", lambda **_kwargs: None)
    monkeypatch.setattr(redis_durability, "probe_redis_durability", durability_probe)
    monkeypatch.setattr(legacy_audit, "audit_legacy_campaigns", campaign_audit)
    monkeypatch.setattr(legacy_audit, "log_audit_summary", lambda _result: None)
    monkeypatch.setattr(prompt_bodies, "record_current_versions", record_versions)
    monkeypatch.setattr(prompt_bodies, "load_cache", load_versions)
    monkeypatch.setattr(provider_cost_ledger, "start_flusher", start_flusher)
    monkeypatch.setattr(provider_cost_ledger, "stop_flusher", stop_flusher)
    monkeypatch.setattr(inbound_startup, "platform_inbound_enabled", platform_enabled)
    monkeypatch.setattr(
        inbound_startup,
        "validate_production_inbound_adapter",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        inbound_startup,
        "validate_production_inbound_state_backend",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(state_backend, "get_state_backend", lambda: state)
    monkeypatch.setattr(
        adapter_factory.CallControlAdapterFactory,
        "create",
        pytest.fail,
    )
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    monkeypatch.setattr(
        telephony_bridge,
        "ensure_session_management_started",
        ensure_started,
    )
    monkeypatch.setattr(lifecycle, "recover_orphaned_calls", recover)

    app = FastAPI()
    # Avoid creating the unrelated loop-lag heartbeat during this lifecycle
    # wiring test; the retry task itself remains a real asyncio task.
    app.state._loop_lag_heartbeat_started = True
    context = main.lifespan(app)
    entered = False
    try:
        await context.__aenter__()
        entered = True
        await asyncio.wait_for(recovered.wait(), timeout=1.0)

        assert state.acquire_calls == 2
        assert state.heartbeat_calls == 2
        assert order == ["connect", "recover"]
    finally:
        if entered:
            await context.__aexit__(None, None, None)
        telephony_bridge.ensure_session_management_started = original_ensure_started
