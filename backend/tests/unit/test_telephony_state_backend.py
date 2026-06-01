"""Contract tests for the telephony state backend abstraction.

These tests pin down the behaviour of ``LocalOnlyStateBackend`` so the
forthcoming ``RedisBackedStateBackend`` has a precise spec to satisfy.
Both backends must pass the same contract suite at the bottom of this
file (parametrised once Redis support lands).

The key invariant validated here: ``LocalOnlyStateBackend`` does not
introduce any new persistence — it mirrors today's module-dict
behaviour exactly. That's the safety guarantee for step 1 of the
Phase-1 migration: if anything in the production telephony path
breaks, the cause cannot be this abstraction, because it's a strict
behavioural pass-through over the existing storage.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys

import pytest


# ─────────────────────────────────────────────────────────────────────
# Module-level fakes so the tests don't require a running app.
# ─────────────────────────────────────────────────────────────────────
#
# ``LocalOnlyStateBackend`` does a lazy import of
# ``app.api.v1.endpoints.telephony_bridge`` to read its module dicts.
# Importing the real bridge pulls in FastAPI, the entire telephony
# adapter stack, and database pools — none of which we want in a unit
# test. We inject a tiny shim module under that path before importing
# the backend.

@pytest.fixture
def fake_bridge_module(monkeypatch):
    """Replace ``app.api.v1.endpoints.telephony_bridge`` with a minimal
    shim that just exposes the eight dicts the backend reads.

    Subtlety: ``LocalOnlyStateBackend`` does
    ``from app.api.v1.endpoints import telephony_bridge as _tb``.
    Python resolves that as ``getattr(parent_package, 'telephony_bridge')``
    once the parent is already imported — bypassing ``sys.modules``
    entirely. If a previous test (or test collection) has already
    imported the real bridge, the parent package's attribute is set
    to the real module, and our ``sys.modules`` shim is never seen.

    So we patch *both* ``sys.modules`` and the parent package
    attribute. ``monkeypatch`` restores both at teardown.
    """
    shim_name = "app.api.v1.endpoints.telephony_bridge"
    import types
    shim = types.ModuleType(shim_name)
    shim._telephony_sessions = {}
    shim._gateway_session_to_call_id = {}
    shim._early_audio_buffers = {}
    shim._ringing_warmups = {}
    shim._ringing_warmup_created_at = {}
    shim._ringing_events = {}
    shim._EARLY_AUDIO_MAX_CHUNKS = 250

    # Pre-create the parent packages so the dotted import works even when
    # no earlier test loaded them (running this file alone).
    for parent in ("app", "app.api", "app.api.v1", "app.api.v1.endpoints"):
        if parent not in sys.modules:
            monkeypatch.setitem(sys.modules, parent, types.ModuleType(parent))

    monkeypatch.setitem(sys.modules, shim_name, shim)
    # Override the parent-package attribute so `from app.api.v1.endpoints
    # import telephony_bridge` resolves to the shim, not whatever earlier
    # tests left behind.
    monkeypatch.setattr(
        sys.modules["app.api.v1.endpoints"], "telephony_bridge", shim, raising=False,
    )
    yield shim


@pytest.fixture
def backend(fake_bridge_module):
    """Fresh ``LocalOnlyStateBackend`` reading the shim module.

    Resets the global backend cache so a previous test's choice of
    backend can't bleed in via env-var caching.
    """
    # Make sure no env var forces a non-default choice.
    os.environ["TELEPHONY_STATE_BACKEND"] = "memory"
    from app.domain.services.telephony import state_backend as sb_mod
    importlib.reload(sb_mod)
    sb_mod.reset_state_backend_for_tests()
    return sb_mod.get_state_backend()


# ─────────────────────────────────────────────────────────────────────
# Voice session registry
# ─────────────────────────────────────────────────────────────────────


def test_voice_session_set_get_pop_count(backend, fake_bridge_module):
    """The basic registry round-trips and reflects in the underlying dict."""
    sentinel = object()
    backend.set_voice_session("call-1", sentinel, tenant_id="t-1")

    assert backend.get_voice_session("call-1") is sentinel
    assert backend.voice_session_count() == 1
    # Strict pass-through invariant: the same object is in the legacy dict.
    assert fake_bridge_module._telephony_sessions["call-1"] is sentinel

    popped = backend.pop_voice_session("call-1")
    assert popped is sentinel
    assert backend.get_voice_session("call-1") is None
    assert backend.voice_session_count() == 0


def test_voice_session_metadata_is_ignored_locally(backend, fake_bridge_module):
    """Metadata kwargs are accepted but not persisted in the local backend.

    The Redis backend will use these to populate the ``telephony:session``
    hash, but the local backend has nowhere to put them — and that's the
    point: zero behavioural change vs today.
    """
    backend.set_voice_session(
        "call-1",
        object(),
        tenant_id="t-1",
        campaign_id="c-1",
        first_speaker="agent",
    )
    # No side table for metadata on the local backend.
    assert "call-1" in fake_bridge_module._telephony_sessions
    # And no unexpected attributes magically appeared on the shim.
    forbidden = {"_telephony_session_metadata", "_telephony_meta"}
    assert not (forbidden & set(vars(fake_bridge_module)))


def test_iter_voice_session_items_returns_snapshot(backend, fake_bridge_module):
    """The watchdog mutates the dict while iterating; the snapshot
    contract guarantees that's safe."""
    a, b = object(), object()
    backend.set_voice_session("a", a)
    backend.set_voice_session("b", b)

    items = backend.iter_voice_session_items()
    # Returning a list, not a dict_items, so mid-iteration mutation is fine.
    assert isinstance(items, list)
    assert {k for k, _ in items} == {"a", "b"}

    # Now mutate the underlying dict during a copy — the snapshot must not change.
    fake_bridge_module._telephony_sessions["c"] = object()
    assert {k for k, _ in items} == {"a", "b"}


# ─────────────────────────────────────────────────────────────────────
# Gateway session ⇄ call_id map
# ─────────────────────────────────────────────────────────────────────


def test_gateway_session_set_get_remove(backend):
    backend.set_call_id_for_gateway_session("gw-1", "call-1")
    backend.set_call_id_for_gateway_session("gw-2", "call-1")
    backend.set_call_id_for_gateway_session("gw-3", "call-2")

    assert backend.get_call_id_for_gateway_session("gw-1") == "call-1"
    assert backend.get_call_id_for_gateway_session("missing") is None

    backend.remove_gateway_sessions_for_call("call-1")
    assert backend.get_call_id_for_gateway_session("gw-1") is None
    assert backend.get_call_id_for_gateway_session("gw-2") is None
    assert backend.get_call_id_for_gateway_session("gw-3") == "call-2"


def test_remove_gateway_sessions_for_call_also_drops_early_audio(backend, fake_bridge_module):
    """The legacy ``_on_call_ended`` cleanup drops the audio buffer
    keyed by each gateway-session-id that maps to the dying call.
    The backend's ``remove_gateway_sessions_for_call`` must do the same.
    """
    backend.set_call_id_for_gateway_session("gw-1", "call-1")
    backend.append_early_audio("gw-1", b"audio")
    backend.remove_gateway_sessions_for_call("call-1")
    assert backend.drain_early_audio("gw-1") == []


# ─────────────────────────────────────────────────────────────────────
# Early-audio buffer
# ─────────────────────────────────────────────────────────────────────


def test_early_audio_append_and_drain_returns_in_order(backend):
    backend.append_early_audio("gw-1", b"chunk-a")
    backend.append_early_audio("gw-1", b"chunk-b")
    backend.append_early_audio("gw-1", b"chunk-c")

    drained = backend.drain_early_audio("gw-1")
    assert drained == [b"chunk-a", b"chunk-b", b"chunk-c"]
    # Drain empties the buffer.
    assert backend.drain_early_audio("gw-1") == []


def test_early_audio_cap_drops_oldest(backend, fake_bridge_module):
    """The legacy cap is 250 chunks (~10s of 40ms batches).
    Going over drops the oldest. Verify with a small cap so the test is fast."""
    fake_bridge_module._EARLY_AUDIO_MAX_CHUNKS = 3
    for i in range(5):
        backend.append_early_audio("gw-1", bytes([i]))

    drained = backend.drain_early_audio("gw-1")
    # The first two were dropped; we keep the last 3.
    assert drained == [bytes([2]), bytes([3]), bytes([4])]


def test_drain_missing_returns_empty_list(backend):
    """Drain on a never-seen session is harmless."""
    assert backend.drain_early_audio("never-seen") == []


def test_discard_early_audio_is_idempotent(backend):
    backend.discard_early_audio("never-seen")  # no error
    backend.append_early_audio("gw-1", b"x")
    backend.discard_early_audio("gw-1")
    backend.discard_early_audio("gw-1")  # no error second time
    assert backend.drain_early_audio("gw-1") == []


# ─────────────────────────────────────────────────────────────────────
# Ringing warmup + timestamp + event
# ─────────────────────────────────────────────────────────────────────


def test_ringing_warmup_set_get_pop_has(backend):
    voice = object()
    task = object()
    backend.set_ringing_warmup("call-1", voice, task)

    assert backend.has_ringing_warmup("call-1")
    assert backend.get_ringing_warmup("call-1") == (voice, task)

    popped = backend.pop_ringing_warmup("call-1")
    assert popped == (voice, task)
    assert not backend.has_ringing_warmup("call-1")
    assert backend.pop_ringing_warmup("call-1") is None


def test_ringing_warmup_supports_none_task(backend):
    """The connect_task is Optional — when the warmup completes
    synchronously, no task handle is stored."""
    voice = object()
    backend.set_ringing_warmup("call-1", voice, None)
    assert backend.get_ringing_warmup("call-1") == (voice, None)


def test_ringing_started_at_round_trip(backend):
    backend.set_ringing_started_at("call-1", 1700_000_000.0)
    assert backend.get_ringing_started_at("call-1") == 1700_000_000.0

    items = backend.iter_ringing_started_at_items()
    assert ("call-1", 1700_000_000.0) in items

    backend.clear_ringing_started_at("call-1")
    assert backend.get_ringing_started_at("call-1") is None


def test_ringing_event_round_trip(backend):
    event = asyncio.Event()
    backend.set_ringing_event("call-1", event)
    assert backend.get_ringing_event("call-1") is event
    popped = backend.pop_ringing_event("call-1")
    assert popped is event
    assert backend.get_ringing_event("call-1") is None


# ─────────────────────────────────────────────────────────────────────
# Lifecycle / liveness no-ops for the local backend
# ─────────────────────────────────────────────────────────────────────


def test_touch_call_is_a_noop(backend):
    # Should not raise, should not mutate any visible state.
    backend.touch_call("call-1")  # call_id doesn't exist — still no-op.


@pytest.mark.asyncio
async def test_recover_orphans_returns_empty(backend):
    assert await backend.recover_orphans() == []


@pytest.mark.asyncio
async def test_start_heartbeat_and_shutdown_are_noops(backend):
    await backend.start_heartbeat()
    await backend.shutdown()


# ─────────────────────────────────────────────────────────────────────
# Feature flag selection
# ─────────────────────────────────────────────────────────────────────


def test_default_flag_returns_local_backend(monkeypatch, fake_bridge_module):
    monkeypatch.delenv("TELEPHONY_STATE_BACKEND", raising=False)
    from app.domain.services.telephony import state_backend as sb_mod
    importlib.reload(sb_mod)
    sb_mod.reset_state_backend_for_tests()
    backend = sb_mod.get_state_backend()
    assert isinstance(backend, sb_mod.LocalOnlyStateBackend)


def test_redis_flag_falls_back_to_local_until_step2(monkeypatch, fake_bridge_module, caplog):
    """Selecting ``redis`` before step 2 ships must not crash the pod —
    we log a warning and fall back to local. This is the safety net
    for an operator who flips the env var early."""
    monkeypatch.setenv("TELEPHONY_STATE_BACKEND", "redis")
    from app.domain.services.telephony import state_backend as sb_mod
    importlib.reload(sb_mod)
    sb_mod.reset_state_backend_for_tests()
    with caplog.at_level("WARNING"):
        backend = sb_mod.get_state_backend()
    assert isinstance(backend, sb_mod.LocalOnlyStateBackend)
    assert any("RedisBackedStateBackend" in r.message for r in caplog.records)


def test_unknown_flag_falls_back_to_local_with_warning(monkeypatch, fake_bridge_module, caplog):
    monkeypatch.setenv("TELEPHONY_STATE_BACKEND", "potato")
    from app.domain.services.telephony import state_backend as sb_mod
    importlib.reload(sb_mod)
    sb_mod.reset_state_backend_for_tests()
    with caplog.at_level("WARNING"):
        backend = sb_mod.get_state_backend()
    assert isinstance(backend, sb_mod.LocalOnlyStateBackend)
    assert any("potato" in r.message for r in caplog.records)


def test_get_state_backend_is_cached(monkeypatch, fake_bridge_module):
    """The same instance must come back across calls so the local dicts
    don't get aliased across multiple wrapper instances."""
    monkeypatch.setenv("TELEPHONY_STATE_BACKEND", "memory")
    from app.domain.services.telephony import state_backend as sb_mod
    importlib.reload(sb_mod)
    sb_mod.reset_state_backend_for_tests()
    first = sb_mod.get_state_backend()
    second = sb_mod.get_state_backend()
    assert first is second
