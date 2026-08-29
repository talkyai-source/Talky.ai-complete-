"""Tests for the Redis-backed telephony state backend (Phase 1 step 3).

Two layers under test:

1. ``RedisBackedStateBackend`` — verifies the write-through contract:
   reads/live-objects delegate to the embedded local backend; the four
   lifecycle writes (session set/pop, ringing-warmup set, touch) fire a
   best-effort Redis mirror via the registry. Uses a recording fake
   registry — no Redis needed.

2. ``SessionRegistry`` — verifies the Redis key schema against a small
   hand-rolled async fake Redis (the repo has no fakeredis dependency).
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types

import pytest


# ─────────────────────────────────────────────────────────────────────
# Shared shim for the telephony_bridge module dicts (same as the
# local-backend tests — RedisBackedStateBackend embeds a LocalOnly one).
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_bridge_module(monkeypatch):
    shim_name = "app.api.v1.endpoints.telephony_bridge"
    shim = types.ModuleType(shim_name)
    shim._telephony_sessions = {}
    shim._gateway_session_to_call_id = {}
    shim._early_audio_buffers = {}
    shim._ringing_warmups = {}
    shim._ringing_warmup_created_at = {}
    shim._ringing_events = {}
    shim._EARLY_AUDIO_MAX_CHUNKS = 250
    for parent in ("app", "app.api", "app.api.v1", "app.api.v1.endpoints"):
        if parent not in sys.modules:
            monkeypatch.setitem(sys.modules, parent, types.ModuleType(parent))
    monkeypatch.setitem(sys.modules, shim_name, shim)
    monkeypatch.setattr(
        sys.modules["app.api.v1.endpoints"],
        "telephony_bridge",
        shim,
        raising=False,
    )
    yield shim


@pytest.fixture
def sb_module(fake_bridge_module):
    from app.domain.services.telephony import state_backend as sb_mod

    importlib.reload(sb_mod)
    sb_mod.reset_state_backend_for_tests()
    return sb_mod


# ─────────────────────────────────────────────────────────────────────
# Recording fake registry
# ─────────────────────────────────────────────────────────────────────


class FakeRegistry:
    """Records the async calls the backend makes, so tests can assert
    the write-through behaviour without a real Redis."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.pod_id = "test-pod"
        # Controllable recovery inputs for recover_orphans tests.
        self.sessions: list[dict] = []
        self.alive: set = set()

    async def register_call(
        self,
        call_id,
        *,
        state,
        tenant_id=None,
        campaign_id=None,
        first_speaker=None,
        durable_call_id=None,
        provider=None,
        provider_call_id=None,
    ):
        del durable_call_id, provider, provider_call_id
        self.calls.append(("register", call_id, state, tenant_id, campaign_id, first_speaker))

    async def register_call_strict(
        self,
        call_id,
        *,
        state,
        tenant_id=None,
        campaign_id=None,
        first_speaker=None,
        durable_call_id=None,
        provider=None,
        provider_call_id=None,
    ):
        del durable_call_id, provider, provider_call_id
        self.calls.append(
            ("register_strict", call_id, state, tenant_id, campaign_id, first_speaker)
        )

    async def promote_call_answered_strict(
        self,
        call_id,
        *,
        answered_at,
        tenant_id=None,
        campaign_id=None,
    ):
        self.calls.append(
            (
                "promote_answered_strict",
                call_id,
                answered_at,
                tenant_id,
                campaign_id,
            )
        )

    async def register_call_answer_intent_strict(
        self,
        call_id,
        **kwargs,
    ):
        self.calls.append(("answer_intent_strict", call_id, kwargs))

    async def claim_cleanup_call_if_absent_strict(self, call_id, **kwargs):
        self.calls.append(("claim_cleanup_if_absent", call_id, kwargs))
        return True

    async def unregister_call(self, call_id):
        self.calls.append(("unregister", call_id))

    async def touch_call(self, call_id):
        self.calls.append(("touch", call_id))

    async def scan_sessions(self):
        self.calls.append(("scan",))
        return list(self.sessions)

    async def is_incarnation_alive(self, incarnation_id):
        return incarnation_id in self.alive

    async def list_own_calls(self):
        self.calls.append(("list_own",))
        return [s for s in self.sessions if s.get("pod_id") == self.pod_id]

    async def write_heartbeat(self, ttl_seconds):
        self.calls.append(("heartbeat", ttl_seconds))

    async def write_heartbeat_strict(self, ttl_seconds):
        self.calls.append(("heartbeat_strict", ttl_seconds))

    async def clear_heartbeat(self):
        self.calls.append(("clear_heartbeat",))

    async def try_acquire_ari_ownership(self, ttl_seconds):
        self.calls.append(("acquire_owner", ttl_seconds))
        return True

    async def try_acquire_ari_ownership_strict(self, ttl_seconds):
        self.calls.append(("acquire_owner_strict", ttl_seconds))
        return True

    async def renew_ari_ownership(self, ttl_seconds):
        self.calls.append(("renew_owner", ttl_seconds))
        return True

    async def renew_ari_ownership_strict(self, ttl_seconds):
        self.calls.append(("renew_owner_strict", ttl_seconds))
        return True

    async def release_ari_ownership(self):
        self.calls.append(("release_owner",))

    async def current_ari_owner(self):
        return self.pod_id


async def _drain(backend):
    """Let the backend's fire-and-forget mirror tasks run to completion."""
    # Two yields: one to let create_task'd coros start, one to let them finish.
    await asyncio.sleep(0)
    if backend._tasks:
        await asyncio.gather(*list(backend._tasks), return_exceptions=True)


# ─────────────────────────────────────────────────────────────────────
# RedisBackedStateBackend write-through
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_voice_session_writes_through_and_reads_local(sb_module, fake_bridge_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)

    obj = object()
    backend.set_voice_session(
        "call-1", obj, tenant_id="t1", campaign_id="c1", first_speaker="agent"
    )

    # Read is local + the real module dict was written (delegation).
    assert backend.get_voice_session("call-1") is obj
    assert fake_bridge_module._telephony_sessions["call-1"] is obj

    await _drain(backend)
    assert ("register", "call-1", "active", "t1", "c1", "agent") in reg.calls


@pytest.mark.asyncio
async def test_pop_voice_session_requires_explicit_post_settlement_ack(sb_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    backend.set_voice_session("call-1", object())
    await _drain(backend)
    reg.calls.clear()

    backend.pop_voice_session("call-1")
    await _drain(backend)
    assert ("unregister", "call-1") not in reg.calls

    await backend.acknowledge_orphan_recovery("call-1")
    assert ("unregister", "call-1") in reg.calls


@pytest.mark.asyncio
async def test_ringing_warmup_registers_as_ringing(sb_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    backend.set_ringing_warmup("call-1", object(), None, first_speaker="customer")
    await _drain(backend)
    assert ("register", "call-1", "ringing", None, None, "customer") in reg.calls


@pytest.mark.asyncio
async def test_cleanup_obligation_is_awaited_and_recovered_while_owner_is_alive(
    sb_module,
):
    reg = FakeRegistry()
    reg.pod_id = "me"
    backend = sb_module.RedisBackedStateBackend(reg)

    await backend.register_cleanup_obligation(
        "unclaimed-channel",
        tenant_id="tenant-1",
        campaign_id="campaign-1",
    )
    assert (
        "register_strict",
        "unclaimed-channel",
        "termination_pending",
        "tenant-1",
        "campaign-1",
        None,
    ) in reg.calls

    # Unlike a live call owned by this incarnation, an explicit cleanup
    # obligation is safe and necessary to retry immediately; waiting for our
    # own heartbeat to expire would strand the rejected carrier leg.
    reg.sessions = [
        {
            "call_id": "unclaimed-channel",
            "pod_id": "me",
            "state": "termination_pending",
        },
        {"call_id": "live-call", "pod_id": "me", "state": "active"},
    ]
    orphans = await backend.recover_orphans()
    assert [entry["call_id"] for entry in orphans] == ["unclaimed-channel"]
    assert ("touch", "unclaimed-channel") in reg.calls


@pytest.mark.asyncio
async def test_answered_cleanup_promotion_is_strict_and_awaited(sb_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)

    await backend.promote_answered_cleanup_obligation(
        "answered-call",
        answered_at="2026-08-28T12:00:00+00:00",
        tenant_id="tenant-1",
        campaign_id="campaign-1",
    )

    assert reg.calls == [
        (
            "promote_answered_strict",
            "answered-call",
            "2026-08-28T12:00:00+00:00",
            "tenant-1",
            "campaign-1",
        )
    ]


@pytest.mark.asyncio
async def test_answer_intent_persists_recovery_identity_and_is_immediately_recoverable(
    sb_module,
):
    reg = FakeRegistry()
    reg.pod_id = "me"
    backend = sb_module.RedisBackedStateBackend(reg)

    await backend.register_answer_intent_cleanup_obligation(
        "pbx-answer-intent",
        answer_requested_at="2026-08-28T12:00:00+00:00",
        tenant_id="tenant-1",
        campaign_id="campaign-1",
        durable_call_id="durable-1",
        provider="asterisk",
        provider_call_id="pbx-answer-intent",
    )
    assert reg.calls == [
        (
            "answer_intent_strict",
            "pbx-answer-intent",
            {
                "answer_requested_at": "2026-08-28T12:00:00+00:00",
                "tenant_id": "tenant-1",
                "campaign_id": "campaign-1",
                "durable_call_id": "durable-1",
                "provider": "asterisk",
                "provider_call_id": "pbx-answer-intent",
            },
        )
    ]

    reg.sessions = [
        {
            "call_id": "pbx-answer-intent",
            "pod_id": "me",
            "state": "answer_pending",
        }
    ]
    assert await backend.recover_orphans() == reg.sessions
    assert ("touch", "pbx-answer-intent") in reg.calls


@pytest.mark.asyncio
async def test_pop_ringing_warmup_does_not_unregister(sb_module):
    """Promotion path: pop_ringing_warmup is followed by set_voice_session,
    so popping must NOT unregister (it would race the re-register)."""
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    backend.set_ringing_warmup("call-1", object(), None)
    await _drain(backend)
    reg.calls.clear()

    backend.pop_ringing_warmup("call-1")
    await _drain(backend)
    assert reg.calls == []  # no Redis op on pop


@pytest.mark.asyncio
async def test_touch_call_first_hit_then_debounced(sb_module):
    """First touch hits Redis; an immediate second touch is debounced
    (no Redis op); after the debounce window elapses it hits again."""
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)

    backend.touch_call("call-1")
    await _drain(backend)
    assert reg.calls.count(("touch", "call-1")) == 1

    # Immediate second touch — debounced, no new Redis op.
    backend.touch_call("call-1")
    await _drain(backend)
    assert reg.calls.count(("touch", "call-1")) == 1

    # Simulate the debounce window having elapsed by ageing the bookkeeping.
    backend._last_touch["call-1"] -= backend._TOUCH_DEBOUNCE_S + 1
    backend.touch_call("call-1")
    await _drain(backend)
    assert reg.calls.count(("touch", "call-1")) == 2


@pytest.mark.asyncio
async def test_pop_voice_session_clears_touch_debounce(sb_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    backend.set_voice_session("call-1", object())
    backend.touch_call("call-1")
    await _drain(backend)
    assert "call-1" in backend._last_touch
    backend.pop_voice_session("call-1")
    await _drain(backend)
    assert "call-1" not in backend._last_touch


@pytest.mark.asyncio
async def test_gateway_and_early_audio_are_local_only(sb_module):
    """These are never mirrored — verify no registry calls happen."""
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    backend.set_call_id_for_gateway_session("gw-1", "call-1")
    backend.append_early_audio("gw-1", b"audio")
    backend.set_ringing_started_at("call-1", 1.0)
    backend.set_ringing_event("call-1", asyncio.Event())
    await _drain(backend)
    assert reg.calls == []


@pytest.mark.asyncio
async def test_alias_re_registers_under_new_id(sb_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    backend.set_ringing_warmup("planned", object(), None)
    backend.set_ringing_started_at("planned", 1.0)
    backend.set_ringing_event("planned", asyncio.Event())
    await _drain(backend)
    reg.calls.clear()

    assert backend.alias_ringing_call("planned", "actual") is True
    await _drain(backend)
    assert ("register", "actual", "ringing", None, None, None) in reg.calls


@pytest.mark.asyncio
async def test_recover_orphans_reclaims_only_dead_incarnations(sb_module):
    """The safety invariant: recover only sessions whose owning
    incarnation is dead (no heartbeat), never our own, never a live peer."""
    reg = FakeRegistry()
    reg.pod_id = "me"
    reg.sessions = [
        {"call_id": "mine", "pod_id": "me", "state": "active"},  # skip: own
        {"call_id": "live-peer", "pod_id": "peer-A", "state": "active"},  # skip: alive
        {"call_id": "dead-peer", "pod_id": "peer-B", "state": "active"},  # RECOVER
    ]
    reg.alive = {"peer-A"}  # peer-B's heartbeat is gone
    backend = sb_module.RedisBackedStateBackend(reg)

    orphans = await backend.recover_orphans()
    ids = {o["call_id"] for o in orphans}
    assert ids == {"dead-peer"}
    # Discovery is not acknowledgement: until PBX teardown is proven, the
    # durable entry must remain available to the next recovery pass.
    assert ("unregister", "dead-peer") not in reg.calls
    assert ("touch", "dead-peer") in reg.calls
    assert ("unregister", "mine") not in reg.calls
    assert ("unregister", "live-peer") not in reg.calls

    # Only the explicit post-proof acknowledgement removes it.
    await backend.acknowledge_orphan_recovery("dead-peer")
    assert ("unregister", "dead-peer") in reg.calls


@pytest.mark.asyncio
async def test_recover_orphans_empty_when_all_alive(sb_module):
    reg = FakeRegistry()
    reg.pod_id = "me"
    reg.sessions = [{"call_id": "c1", "pod_id": "peer-A", "state": "active"}]
    reg.alive = {"peer-A"}
    backend = sb_module.RedisBackedStateBackend(reg)
    assert await backend.recover_orphans() == []


@pytest.mark.asyncio
async def test_start_heartbeat_writes_immediately_and_is_idempotent(sb_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    await backend.start_heartbeat()
    # One immediate heartbeat write on start.
    assert any(c[0] == "heartbeat" for c in reg.calls)
    first_task = backend._heartbeat_task
    assert first_task is not None and not first_task.done()
    # Idempotent — a second call doesn't spawn a new task.
    await backend.start_heartbeat()
    assert backend._heartbeat_task is first_task
    await backend.shutdown()


@pytest.mark.asyncio
async def test_strict_heartbeat_fences_process_when_redis_becomes_unverifiable(sb_module):
    class FailingRegistry(FakeRegistry):
        async def renew_ari_ownership_strict(self, ttl_seconds):
            self.calls.append(("renew_owner_strict", ttl_seconds))
            raise ConnectionError("redis unavailable")

    reg = FailingRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    backend._HEARTBEAT_INTERVAL_S = 0

    assert await backend.acquire_telephony_ownership_strict() is True
    await backend.start_heartbeat_strict()
    task = backend._heartbeat_task
    assert task is not None
    await task

    assert backend.strict_ownership_active is True
    assert backend.is_telephony_owner() is False
    assert ("heartbeat_strict", backend._HEARTBEAT_TTL_S) in reg.calls
    assert ("renew_owner_strict", backend._HEARTBEAT_TTL_S) in reg.calls
    await backend.shutdown()


@pytest.mark.asyncio
async def test_ownership_loss_is_bounded_and_drains_local_media_without_pbx_control(
    sb_module,
    monkeypatch,
):
    from app.domain.services.telephony import adapter_registry

    class BlockingAdapter:
        connected = True

        def __init__(self):
            self.cancelled = False
            self.force_handoff = False
            self.fenced = False

        def fence_ownership_loss(self):
            self.fenced = True
            self.connected = False

        async def disconnect(self, *, force_handoff=False):
            self.force_handoff = force_handoff
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    adapter = BlockingAdapter()
    monkeypatch.setattr(adapter_registry, "_adapter_getter", lambda: adapter)
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    backend._OWNERSHIP_LOSS_CLEANUP_TIMEOUT_S = 0.01
    backend._OWNERSHIP_LOSS_SESSION_TIMEOUT_S = 0.005
    backend.set_voice_session("call-1", object())
    await _drain(backend)
    reg.calls.clear()

    await backend._handle_ownership_loss("test fence")

    assert backend.is_telephony_owner() is False
    assert backend.get_voice_session("call-1") is None
    assert adapter.fenced is True
    assert adapter.connected is False
    # Under heavy scheduler load the asynchronous task may be cancelled before
    # it enters ``disconnect``. The synchronous fence is the safety proof; if
    # cleanup did start, it must receive the forced-handoff flag.
    assert not adapter.cancelled or adapter.force_handoff is True
    assert (
        "unregister",
        "call-1",
    ) not in reg.calls, (
        "the stale process must leave durable PBX truth for the new owner/reconciler"
    )


@pytest.mark.asyncio
async def test_ownership_loss_closes_answered_inbound_handoff_gap(
    sb_module,
    monkeypatch,
):
    from app.domain.services.telephony import adapter_registry
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

    adapter = AsteriskAdapter()
    adapter._connected_flag = True
    adapter._inbound_cleanup_retry_s = 0

    class FakeSession:
        async def close(self):
            return None

    # Confirmation-aware hangup deliberately refuses to treat an adapter
    # without a live ARI session as inventory proof.  This test replaces the
    # HTTP helper below, so provide the matching connected-session sentinel.
    adapter._session = FakeSession()
    adapter._inbound_admissions["inbound-gap"] = {
        "allowed": True,
        "call_id": "durable-inbound-gap",
        "provider": "asterisk",
        "provider_call_id": "inbound-gap",
    }
    adapter._active_sessions["inbound-gap"] = {
        "session_id": "gateway-gap",
        "listen_port": 32000,
        "bridge_id": "bridge-gap",
        "direction": "inbound",
    }
    adapter._gateway_sessions["inbound-gap"] = "gateway-gap"
    adapter._ext_channels["inbound-gap"] = "external-gap"
    adapter._bridges["inbound-gap"] = "bridge-gap"

    handoff_entered = asyncio.Event()
    never_register = asyncio.Event()
    actions: list[str] = []

    backend_holder = {}

    async def blocked_handoff(_call_id, _admission):
        handoff_entered.set()
        try:
            await never_register.wait()
        except asyncio.CancelledError:
            # Model the tightest edge: lifecycle registered locally after the
            # ownership-loss drain took its first snapshot, then cancellation
            # arrived before the handoff task completed.
            backend_holder["backend"]._local.set_voice_session("inbound-gap", object())
            raise

    async def ari(method, path, **_kwargs):
        actions.append(f"ari:{method}:{path}")
        if method == "GET" and path == "/channels":
            return []
        if method == "DELETE" and _kwargs.get("return_status"):
            return 204, {}
        return {}

    async def gateway(method, path, **_kwargs):
        actions.append(f"gateway:{method}:{path}")
        return {}

    async def finalize(*_args, **_kwargs):
        actions.append("finalize")

    async def release_port(port):
        actions.append(f"release:{port}")

    adapter._on_new_call = blocked_handoff
    adapter._ari = ari
    adapter._gateway = gateway
    adapter._release_rtp_port = release_port
    adapter.set_inbound_admission_finalizer(finalize)
    monkeypatch.setattr(adapter_registry, "_adapter_getter", lambda: adapter)
    backend = sb_module.RedisBackedStateBackend(FakeRegistry())
    backend_holder["backend"] = backend
    backend._OWNERSHIP_LOSS_CLEANUP_TIMEOUT_S = 1.0
    adapter._schedule_inbound_handoff("inbound-gap", adapter._inbound_admissions["inbound-gap"])
    await handoff_entered.wait()

    await backend._handle_ownership_loss("race test")

    assert backend.is_telephony_owner() is False
    assert adapter.connected is False
    assert backend.get_voice_session("inbound-gap") is None
    assert "inbound-gap" not in adapter._active_sessions
    assert "inbound-gap" not in adapter._inbound_handoff_tasks
    assert "inbound-gap" not in adapter._inbound_cleanup_pending
    assert "inbound-gap" not in adapter._inbound_admissions
    assert actions == [
        "gateway:POST:/v1/sessions/stop",
        "ari:DELETE:/channels/external-gap",
        "ari:DELETE:/channels/inbound-gap",
        "ari:GET:/channels",
        "ari:DELETE:/bridges/bridge-gap",
        "release:32000",
        "finalize",
    ]


@pytest.mark.asyncio
async def test_strict_start_upgrades_an_existing_heartbeat_immediately(sb_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    await backend.start_heartbeat()
    existing_task = backend._heartbeat_task

    await backend.start_heartbeat_strict()

    assert backend._heartbeat_task is existing_task
    assert backend.strict_ownership_active is True
    assert ("heartbeat_strict", backend._HEARTBEAT_TTL_S) in reg.calls
    await backend.shutdown()


@pytest.mark.asyncio
async def test_shutdown_clears_heartbeat_and_cancels_task(sb_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    await backend.start_heartbeat()
    task = backend._heartbeat_task
    await backend.shutdown()
    assert ("clear_heartbeat",) in reg.calls
    assert backend._heartbeat_task is None
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_shutdown_flushes_confirmed_removal_before_heartbeat_handoff(sb_module):
    class OrderedRegistry(FakeRegistry):
        async def unregister_call(self, call_id):
            self.calls.append(("unregister_started", call_id))
            await asyncio.sleep(0)
            self.calls.append(("unregister_finished", call_id))

    reg = OrderedRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    backend.set_voice_session("confirmed-call", object())
    await _drain(backend)
    reg.calls.clear()

    # Local cleanup alone is not settlement and must not delete the recovery
    # obligation. The lifecycle's explicit post-settlement acknowledgement is
    # awaited before shutdown advertises this incarnation dead.
    backend.pop_voice_session("confirmed-call")
    assert ("unregister_started", "confirmed-call") not in reg.calls
    await backend.acknowledge_orphan_recovery("confirmed-call")
    await backend.shutdown()

    assert reg.calls.index(("unregister_finished", "confirmed-call")) < reg.calls.index(
        ("clear_heartbeat",)
    )
    assert reg.calls.index(("clear_heartbeat",)) < reg.calls.index(("release_owner",))


def test_no_running_loop_skips_mirror_but_updates_local(sb_module, fake_bridge_module):
    """Calling a write-through method with no event loop (sync context)
    must still update local state and simply skip the mirror."""
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    obj = object()
    backend.set_voice_session("call-1", obj)  # no running loop here
    assert backend.get_voice_session("call-1") is obj
    assert fake_bridge_module._telephony_sessions["call-1"] is obj
    # Mirror was skipped — no task scheduled.
    assert backend._tasks == set()


# ─────────────────────────────────────────────────────────────────────
# SessionRegistry against a hand-rolled async fake Redis
# ─────────────────────────────────────────────────────────────────────


class FakeRedis:
    """Minimal async Redis supporting hashes + sets + a buffering
    pipeline. Only the commands SessionRegistry uses are implemented."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set] = {}
        self.strings: dict[str, str] = {}

    # hash ops
    async def hset(self, key, mapping=None):
        h = self.hashes.setdefault(key, {})
        if mapping:
            h.update({k: str(v) for k, v in mapping.items()})

    async def hsetnx(self, key, field, value):
        h = self.hashes.setdefault(key, {})
        if field not in h:
            h[field] = str(value)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    # set ops
    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    async def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    # generic
    async def expire(self, key, ttl):
        return True

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def get(self, key):
        return self.strings.get(key)

    async def eval(self, script, numkeys, *args):
        # Only the two owner-lock Lua scripts are used: a compare-and-
        # expire (renew) and a compare-and-del (release). Dispatch on a
        # distinguishing keyword rather than interpreting Lua.
        keys = args[:numkeys]
        argv = args[numkeys:]
        key = keys[0]
        if "claim-cleanup-if-absent" in script:
            if key in self.hashes:
                return 0
            self.hashes[key] = {
                "pod_id": str(argv[0]),
                "state": str(argv[1]),
                "tenant_id": "",
                "campaign_id": "",
                "first_speaker": "",
                "provider": str(argv[2]),
                "provider_call_id": str(argv[3]),
                "recovery_source": "inverse_ari_inventory",
                "created_at": str(argv[4]),
                "updated_at": str(argv[4]),
            }
            return 1
        if "'HSET'" in script:
            if key not in self.hashes:
                return 0
            self.hashes[key]["updated_at"] = str(argv[0])
            return 1
        current = self.strings.get(key)
        if "expire" in script:  # renew: extend iff still ours
            return 1 if current == argv[0] else 0
        if "del" in script:  # release: delete iff ours
            if current == argv[0]:
                self.strings.pop(key, None)
                return 1
            return 0
        return 0

    async def delete(self, key):
        self.hashes.pop(key, None)
        self.strings.pop(key, None)
        self.sets.pop(key, None)

    async def exists(self, key):
        return 1 if (key in self.strings or key in self.hashes or key in self.sets) else 0

    async def scan_iter(self, match=None):
        import fnmatch

        # Snapshot keys so deletion during iteration is safe.
        keys = list(self.hashes.keys())
        for k in keys:
            if match is None or fnmatch.fnmatch(k, match):
                yield k

    def pipeline(self, transaction=True):
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, redis):
        self._redis = redis
        self._ops: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def hset(self, key, mapping=None):
        self._ops.append(("hset", (key,), {"mapping": mapping}))

    def hsetnx(self, key, field, value):
        self._ops.append(("hsetnx", (key, field, value), {}))

    def expire(self, key, ttl):
        self._ops.append(("expire", (key, ttl), {}))

    def sadd(self, key, member):
        self._ops.append(("sadd", (key, member), {}))

    def srem(self, key, member):
        self._ops.append(("srem", (key, member), {}))

    def delete(self, key):
        self._ops.append(("delete", (key,), {}))

    def set(self, key, value, ex=None, xx=None):
        self._ops.append(("set", (key, value), {"ex": ex}))

    async def execute(self):
        results = []
        for name, args, kwargs in self._ops:
            results.append(await getattr(self._redis, name)(*args, **kwargs))
        return results


@pytest.fixture
def registry():
    from app.domain.services.telephony.session_registry import SessionRegistry

    return SessionRegistry(FakeRedis(), "pod-A")


@pytest.mark.asyncio
async def test_registry_register_then_list(registry):
    await registry.register_call(
        "call-1", state="active", tenant_id="t1", campaign_id="c1", first_speaker="agent"
    )
    owned = await registry.list_own_calls()
    assert len(owned) == 1
    entry = owned[0]
    assert entry["call_id"] == "call-1"
    assert entry["state"] == "active"
    assert entry["tenant_id"] == "t1"
    assert entry["pod_id"] == "pod-A"
    assert "created_at" in entry


@pytest.mark.asyncio
async def test_registry_strict_cleanup_registration_is_durable(registry):
    await registry.register_call_strict(
        "unclaimed-call",
        state="termination_pending",
        tenant_id="tenant-1",
    )

    entries = await registry.scan_sessions()
    assert len(entries) == 1
    assert entries[0]["call_id"] == "unclaimed-call"
    assert entries[0]["pod_id"] == "pod-A"
    assert entries[0]["state"] == "termination_pending"
    assert entries[0]["tenant_id"] == "tenant-1"
    assert entries[0]["created_at"]


@pytest.mark.asyncio
async def test_registry_answer_promotion_persists_first_timestamp(registry):
    await registry.register_call_strict(
        "answered-call",
        state="termination_pending",
        tenant_id="tenant-1",
    )
    await registry.promote_call_answered_strict(
        "answered-call",
        answered_at="2026-08-28T12:00:00+00:00",
        tenant_id="tenant-1",
        campaign_id="campaign-1",
    )
    await registry.promote_call_answered_strict(
        "answered-call",
        answered_at="2026-08-28T12:00:09+00:00",
        tenant_id="tenant-1",
        campaign_id="campaign-1",
    )

    entries = await registry.scan_sessions()
    assert len(entries) == 1
    assert entries[0]["call_id"] == "answered-call"
    assert entries[0]["pod_id"] == "pod-A"
    assert entries[0]["state"] == "active"
    assert entries[0]["tenant_id"] == "tenant-1"
    assert entries[0]["campaign_id"] == "campaign-1"
    assert entries[0]["answered_at"] == "2026-08-28T12:00:00+00:00"


@pytest.mark.asyncio
async def test_registry_answer_intent_is_durable_with_full_identity(registry):
    await registry.register_call_answer_intent_strict(
        "pbx-answer-intent",
        answer_requested_at="2026-08-28T12:00:00+00:00",
        tenant_id="tenant-1",
        campaign_id="campaign-1",
        durable_call_id="durable-1",
        provider="asterisk",
        provider_call_id="pbx-answer-intent",
    )

    entries = await registry.scan_sessions()
    assert len(entries) == 1
    assert entries[0]["state"] == "answer_pending"
    assert entries[0]["answer_requested_at"] == "2026-08-28T12:00:00+00:00"
    assert entries[0]["durable_call_id"] == "durable-1"
    assert entries[0]["provider"] == "asterisk"
    assert entries[0]["provider_call_id"] == "pbx-answer-intent"
    assert entries[0]["direction"] == "inbound"


@pytest.mark.asyncio
async def test_registry_strict_cleanup_registration_fails_if_key_not_verifiable():
    from app.domain.services.telephony.session_registry import SessionRegistry

    class UnverifiableRedis(FakeRedis):
        async def exists(self, _key):
            return 0

    registry = SessionRegistry(UnverifiableRedis(), "pod-A")
    with pytest.raises(RuntimeError, match="not durable"):
        await registry.register_call_strict(
            "unverifiable-call",
            state="termination_pending",
        )


@pytest.mark.asyncio
async def test_registry_unregister_removes_from_owned(registry):
    await registry.register_call("call-1", state="active")
    await registry.unregister_call("call-1")
    owned = await registry.list_own_calls()
    assert owned == []


@pytest.mark.asyncio
async def test_registry_promote_ringing_to_active(registry):
    await registry.register_call("call-1", state="ringing")
    await registry.register_call("call-1", state="active", tenant_id="t1")
    owned = await registry.list_own_calls()
    assert len(owned) == 1
    assert owned[0]["state"] == "active"


@pytest.mark.asyncio
async def test_recovery_end_to_end_with_real_registry(sb_module):
    """Full path: a shared FakeRedis holds sessions from a dead peer and a
    live peer; the backend's recover_orphans (via the REAL SessionRegistry
    scan_iter/exists/hgetall) reclaims only the dead peer's call."""
    from app.domain.services.telephony.session_registry import SessionRegistry

    shared = FakeRedis()

    # Dead peer registered a call but never wrote (or lost) its heartbeat.
    dead = SessionRegistry(shared, "host:dead0001")
    await dead.register_call("dead-call", state="active", tenant_id="t-dead")

    # Live peer registered a call AND has a heartbeat.
    live = SessionRegistry(shared, "host:live0002")
    await live.register_call("live-call", state="active", tenant_id="t-live")
    await live.write_heartbeat(60)

    # The recovering process.
    me = SessionRegistry(shared, "host:me000003")
    await me.write_heartbeat(60)
    backend = sb_module.RedisBackedStateBackend(me)

    orphans = await backend.recover_orphans()
    assert {o["call_id"] for o in orphans} == {"dead-call"}
    assert orphans[0]["tenant_id"] == "t-dead"

    # Discovery retains both hashes until confirmation. Explicit
    # acknowledgement is the only operation that removes the orphan.
    remaining = {s["call_id"] for s in await me.scan_sessions()}
    assert remaining == {"dead-call", "live-call"}

    await backend.acknowledge_orphan_recovery("dead-call")
    remaining = {s["call_id"] for s in await me.scan_sessions()}
    assert remaining == {"live-call"}


@pytest.mark.asyncio
async def test_bounded_registry_recovery_rotates_selected_retry(sb_module):
    from app.domain.services.telephony.session_registry import SessionRegistry

    shared = FakeRedis()
    dead = SessionRegistry(shared, "host:dead")
    for index in range(3):
        call_id = f"retry-{index}"
        await dead.register_call_strict(call_id, state="termination_pending")
        shared.hashes[f"telephony:session:{call_id}"]["updated_at"] = (
            f"2020-01-01T00:00:0{index}+00:00"
        )

    backend = sb_module.RedisBackedStateBackend(
        SessionRegistry(shared, "host:successor")
    )
    first = await backend.recover_orphans(limit=1)
    second = await backend.recover_orphans(limit=1)

    assert [row["call_id"] for row in first] == ["retry-0"]
    assert [row["call_id"] for row in second] == ["retry-1"]
    assert shared.hashes["telephony:session:retry-0"]["updated_at"].startswith(
        "20"
    )


@pytest.mark.asyncio
async def test_strict_retry_rotation_never_recreates_missing_hash():
    from app.domain.services.telephony.session_registry import SessionRegistry

    shared = FakeRedis()
    registry = SessionRegistry(shared, "host:successor")

    with pytest.raises(RuntimeError, match="disappeared"):
        await registry.touch_call_strict("missing-call")
    assert "telephony:session:missing-call" not in shared.hashes


@pytest.mark.asyncio
async def test_inverse_claim_never_overwrites_answer_intent_identity():
    from app.domain.services.telephony.session_registry import SessionRegistry

    shared = FakeRedis()
    registry = SessionRegistry(shared, "host:successor")
    await registry.register_call_answer_intent_strict(
        "answer-race",
        answer_requested_at="2026-08-28T12:00:00+00:00",
        tenant_id="tenant-1",
        campaign_id="campaign-1",
        durable_call_id="durable-1",
        provider="asterisk",
        provider_call_id="answer-race",
    )
    before = dict(shared.hashes["telephony:session:answer-race"])

    claimed = await registry.claim_cleanup_call_if_absent_strict(
        "answer-race",
        provider="asterisk",
        provider_call_id="answer-race",
    )

    assert claimed is False
    assert shared.hashes["telephony:session:answer-race"] == before


@pytest.mark.asyncio
async def test_inverse_claim_creates_tagged_retry_record_when_absent():
    from app.domain.services.telephony.session_registry import SessionRegistry

    shared = FakeRedis()
    registry = SessionRegistry(shared, "host:successor")

    assert await registry.claim_cleanup_call_if_absent_strict(
        "unknown-parent",
        provider="asterisk",
        provider_call_id="unknown-parent",
    )
    stored = shared.hashes["telephony:session:unknown-parent"]
    assert stored["state"] == "termination_pending"
    assert stored["provider"] == "asterisk"
    assert stored["provider_call_id"] == "unknown-parent"
    assert stored["recovery_source"] == "inverse_ari_inventory"


@pytest.mark.asyncio
async def test_registry_none_redis_is_safe():
    from app.domain.services.telephony.session_registry import SessionRegistry

    reg = SessionRegistry(None, "pod-A")
    # All ops degrade to no-ops / empty, never raise.
    await reg.register_call("c1", state="active")
    await reg.unregister_call("c1")
    await reg.touch_call("c1")
    await reg.write_heartbeat(60)
    await reg.clear_heartbeat()
    assert await reg.list_own_calls() == []


# ─────────────────────────────────────────────────────────────────────
# Single-owner ARI lock — the core of the --workers >1 safety guarantee
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_backend_is_always_owner(sb_module, fake_bridge_module):
    """Single-process (memory) backend has no coordination peer, so it
    always owns telephony — preserving today's --workers 1 behaviour."""
    backend = sb_module.LocalOnlyStateBackend()
    assert await backend.acquire_telephony_ownership() is True
    assert backend.is_telephony_owner() is True
    assert await backend.telephony_owner_id() is None


@pytest.mark.asyncio
async def test_registry_ownership_single_winner():
    """Two processes race; exactly one wins. The loser sees the winner's
    id as the current owner."""
    from app.domain.services.telephony.session_registry import SessionRegistry

    shared = FakeRedis()
    a = SessionRegistry(shared, "host:aaaa")
    b = SessionRegistry(shared, "host:bbbb")

    assert await a.try_acquire_ari_ownership(60) is True
    assert await b.try_acquire_ari_ownership(60) is False
    assert await b.current_ari_owner() == "host:aaaa"


@pytest.mark.asyncio
async def test_registry_ownership_reacquire_is_idempotent():
    from app.domain.services.telephony.session_registry import SessionRegistry

    shared = FakeRedis()
    a = SessionRegistry(shared, "host:aaaa")
    assert await a.try_acquire_ari_ownership(60) is True
    # Same process re-acquiring still reads as owner (value matches).
    assert await a.try_acquire_ari_ownership(60) is True


@pytest.mark.asyncio
async def test_registry_renew_only_while_owner():
    from app.domain.services.telephony.session_registry import SessionRegistry

    shared = FakeRedis()
    a = SessionRegistry(shared, "host:aaaa")
    b = SessionRegistry(shared, "host:bbbb")
    await a.try_acquire_ari_ownership(60)

    assert await a.renew_ari_ownership(60) is True  # owner renews
    assert await b.renew_ari_ownership(60) is False  # non-owner can't


@pytest.mark.asyncio
async def test_registry_release_only_own_then_successor_acquires():
    from app.domain.services.telephony.session_registry import SessionRegistry

    shared = FakeRedis()
    a = SessionRegistry(shared, "host:aaaa")
    b = SessionRegistry(shared, "host:bbbb")
    await a.try_acquire_ari_ownership(60)

    # A non-owner release is a no-op — it must not steal/clear the lock.
    await b.release_ari_ownership()
    assert await b.try_acquire_ari_ownership(60) is False

    # The owner releasing frees the lock so the successor can acquire.
    await a.release_ari_ownership()
    assert await b.try_acquire_ari_ownership(60) is True


@pytest.mark.asyncio
async def test_registry_ownership_none_redis_fails_open():
    """No Redis ⇒ no coordination layer ⇒ this is the only process ⇒
    own telephony (refusing it would be a worse failure)."""
    from app.domain.services.telephony.session_registry import SessionRegistry

    reg = SessionRegistry(None, "pod-A")
    assert await reg.try_acquire_ari_ownership(60) is True
    assert await reg.renew_ari_ownership(60) is True
    await reg.release_ari_ownership()  # no-op, never raises
    assert await reg.current_ari_owner() is None


@pytest.mark.asyncio
async def test_backend_acquire_sets_owner_flag_and_loser_is_blocked(sb_module):
    """End-to-end through RedisBackedStateBackend + real registries on a
    shared FakeRedis: the first backend owns, the second is blocked."""
    from app.domain.services.telephony.session_registry import SessionRegistry

    shared = FakeRedis()
    reg1 = SessionRegistry(shared, "host:first")
    reg2 = SessionRegistry(shared, "host:second")
    b1 = sb_module.RedisBackedStateBackend(reg1)
    b2 = sb_module.RedisBackedStateBackend(reg2)

    assert await b1.acquire_telephony_ownership() is True
    assert b1.is_telephony_owner() is True

    assert await b2.acquire_telephony_ownership() is False
    assert b2.is_telephony_owner() is False
    assert await b2.telephony_owner_id() == "host:first"
