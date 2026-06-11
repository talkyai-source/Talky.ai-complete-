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
        sys.modules["app.api.v1.endpoints"], "telephony_bridge", shim, raising=False,
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

    async def register_call(self, call_id, *, state, tenant_id=None, campaign_id=None, first_speaker=None):
        self.calls.append(("register", call_id, state, tenant_id, campaign_id, first_speaker))

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

    async def clear_heartbeat(self):
        self.calls.append(("clear_heartbeat",))

    async def try_acquire_ari_ownership(self, ttl_seconds):
        self.calls.append(("acquire_owner", ttl_seconds))
        return True

    async def renew_ari_ownership(self, ttl_seconds):
        self.calls.append(("renew_owner", ttl_seconds))
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
    backend.set_voice_session("call-1", obj, tenant_id="t1", campaign_id="c1", first_speaker="agent")

    # Read is local + the real module dict was written (delegation).
    assert backend.get_voice_session("call-1") is obj
    assert fake_bridge_module._telephony_sessions["call-1"] is obj

    await _drain(backend)
    assert ("register", "call-1", "active", "t1", "c1", "agent") in reg.calls


@pytest.mark.asyncio
async def test_pop_voice_session_unregisters(sb_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    backend.set_voice_session("call-1", object())
    await _drain(backend)
    reg.calls.clear()

    backend.pop_voice_session("call-1")
    await _drain(backend)
    assert ("unregister", "call-1") in reg.calls


@pytest.mark.asyncio
async def test_ringing_warmup_registers_as_ringing(sb_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    backend.set_ringing_warmup("call-1", object(), None, first_speaker="customer")
    await _drain(backend)
    assert ("register", "call-1", "ringing", None, None, "customer") in reg.calls


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
        {"call_id": "mine", "pod_id": "me", "state": "active"},          # skip: own
        {"call_id": "live-peer", "pod_id": "peer-A", "state": "active"}, # skip: alive
        {"call_id": "dead-peer", "pod_id": "peer-B", "state": "active"}, # RECOVER
    ]
    reg.alive = {"peer-A"}  # peer-B's heartbeat is gone
    backend = sb_module.RedisBackedStateBackend(reg)

    orphans = await backend.recover_orphans()
    ids = {o["call_id"] for o in orphans}
    assert ids == {"dead-peer"}
    # The reclaimed orphan was unregistered (claimed) exactly once.
    assert ("unregister", "dead-peer") in reg.calls
    assert ("unregister", "mine") not in reg.calls
    assert ("unregister", "live-peer") not in reg.calls


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
async def test_shutdown_clears_heartbeat_and_cancels_task(sb_module):
    reg = FakeRegistry()
    backend = sb_module.RedisBackedStateBackend(reg)
    await backend.start_heartbeat()
    task = backend._heartbeat_task
    await backend.shutdown()
    assert ("clear_heartbeat",) in reg.calls
    assert backend._heartbeat_task is None
    assert task.cancelled() or task.done()


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
        current = self.strings.get(key)
        if "expire" in script:           # renew: extend iff still ours
            return 1 if current == argv[0] else 0
        if "del" in script:              # release: delete iff ours
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
    await registry.register_call("call-1", state="active", tenant_id="t1", campaign_id="c1", first_speaker="agent")
    owned = await registry.list_own_calls()
    assert len(owned) == 1
    entry = owned[0]
    assert entry["call_id"] == "call-1"
    assert entry["state"] == "active"
    assert entry["tenant_id"] == "t1"
    assert entry["pod_id"] == "pod-A"
    assert "created_at" in entry


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

    # The dead call's hash was deleted (claimed); the live one remains.
    remaining = {s["call_id"] for s in await me.scan_sessions()}
    assert remaining == {"live-call"}


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

    assert await a.renew_ari_ownership(60) is True       # owner renews
    assert await b.renew_ari_ownership(60) is False      # non-owner can't


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
    await reg.release_ari_ownership()          # no-op, never raises
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
