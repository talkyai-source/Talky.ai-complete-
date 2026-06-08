"""Telephony state backend — abstraction over per-call state storage.

This module is the foundation of Phase 1 of architecture roadmap item 1:
moving telephony module state out of process-only memory so it survives
uvicorn worker restarts (see ``backend/ARCHITECTURE_REVIEW_2026-05-31.md``).

# The problem we're solving

Today, eight module-level dicts in
``backend/app/api/v1/endpoints/telephony_bridge.py`` hold every piece
of per-call state: active VoiceSession objects, the C++ gateway-id ⇄
call-id mapping, early-audio buffers, ringing-phase warmups and their
sync events. A uvicorn worker restart — deploy, OOM, crash — drops
every in-flight call because all that state evaporates.

# The plan, in one paragraph

We keep the live Python objects (VoiceSession, asyncio.Task,
asyncio.Event, CallControlAdapter) in process memory because they're
not serialisable and the audio hot path needs sub-microsecond
lookups. We add a Redis-backed mirror of the *metadata* (tenant_id,
campaign_id, timestamps, call state, pod ownership) so that on a
restart, the new process scans Redis, finds orphaned calls owned by
its previous incarnation, and hangs them up cleanly with a recovery
``stream_events`` row instead of leaving them as zombies.

# Why this file is the only thing in step 1

Every consumer of telephony state in ``telephony_bridge.py``,
``lifecycle.py``, ``main.py`` etc. will be migrated to call through
``get_state_backend()`` over the next few commits. This file exists
first so future commits have something to migrate *to*. Today's
``LocalOnlyStateBackend`` references the existing module dicts
directly — bit-for-bit identical behaviour. Step 2 swaps in a Redis
mirror; step 3 adds the recovery startup hook; step 4 adds the
audio-path TTL refresh debounce.

# Feature flag

``TELEPHONY_STATE_BACKEND`` env var, ``memory`` (default) or
``redis``. Default ``memory`` preserves today's behaviour exactly
during the rollout window. Flipping to ``redis`` enables the
write-through mirror plus restart recovery. Rollback is a single
``systemctl set-environment`` + restart, no redeploy.

# Why a Protocol, not an ABC

The two backends share an interface but have completely different
internals (one is dict-only, one wraps Redis pipelines). A
``typing.Protocol`` documents the contract without forcing inheritance
or forcing the local backend to subclass a Redis-aware base. Mirrors
the pattern in ``app/domain/interfaces/call_control_adapter.py``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Backend protocol — the API every consumer uses
# ─────────────────────────────────────────────────────────────────────


class TelephonyStateBackend(Protocol):
    """Single contract used by ``telephony_bridge`` and ``lifecycle``.

    The split between methods that take/return live Python objects
    (``set_voice_session``, ``get_ringing_warmup``) and methods that
    take/return plain data (``register_call``, ``set_ringing_started_at``)
    is deliberate. The live-object methods are process-local even in
    the Redis backend — Redis holds a metadata mirror, the local cache
    holds the actual ``VoiceSession`` / ``asyncio.Task`` references.
    The plain-data methods are durable across restarts when the Redis
    backend is selected.

    Consumers should not reach around this Protocol to touch backend
    internals. If a new operation is needed, add it here and implement
    on both backends so the feature flag stays a safe rollback path.
    """

    # ── Voice session registry (the dict[call_id → VoiceSession]) ───
    #
    # ``set_voice_session`` also takes the metadata fields so the Redis
    # backend can mirror them in one round-trip. The local backend
    # ignores the metadata kwargs entirely.

    def set_voice_session(
        self,
        call_id: str,
        voice_session: object,
        *,
        tenant_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        first_speaker: Optional[str] = None,
    ) -> None: ...

    def get_voice_session(self, call_id: str) -> Optional[object]: ...

    def pop_voice_session(self, call_id: str) -> Optional[object]: ...

    def voice_session_count(self) -> int: ...

    def iter_voice_session_items(self) -> list[tuple[str, object]]:
        """Snapshot — copies the underlying mapping so callers can
        mutate during iteration (the watchdog needs this)."""
        ...

    # ── Gateway session id ⇄ call_id map (pure strings) ─────────────

    def get_call_id_for_gateway_session(self, gateway_session_id: str) -> Optional[str]: ...

    def set_call_id_for_gateway_session(self, gateway_session_id: str, call_id: str) -> None: ...

    def remove_gateway_sessions_for_call(self, call_id: str) -> None: ...

    def remove_gateway_session(self, gateway_session_id: str) -> None:
        """Remove a single gateway-session mapping by its own id (used by
        the watchdog's orphan sweep, which iterates by gateway id)."""
        ...

    def iter_gateway_session_items(self) -> list[tuple[str, str]]: ...

    # ── Early-audio buffer (bytes by gateway_session_id) ────────────

    def append_early_audio(self, gateway_session_id: str, chunk: bytes) -> int:
        """Append a chunk to the early-audio buffer (capped). Returns the
        new buffer length so the caller can log 'buffering started' once
        on the first chunk."""
        ...

    def drain_early_audio(self, gateway_session_id: str) -> list[bytes]: ...

    def discard_early_audio(self, gateway_session_id: str) -> None: ...

    def iter_early_audio_keys(self) -> list[str]: ...

    # ── Ringing warmup dict (live VoiceSession + Task) ──────────────

    def set_ringing_warmup(
        self,
        call_id: str,
        voice_session: object,
        connect_task: Optional[object],
        *,
        tenant_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        first_speaker: Optional[str] = None,
    ) -> None: ...

    def get_ringing_warmup(self, call_id: str) -> Optional[tuple[object, Optional[object]]]: ...

    def pop_ringing_warmup(self, call_id: str) -> Optional[tuple[object, Optional[object]]]: ...

    def has_ringing_warmup(self, call_id: str) -> bool: ...

    def iter_ringing_warmup_keys(self) -> list[str]: ...

    def ringing_warmup_count(self) -> int: ...

    def alias_ringing_call(self, original_call_id: str, actual_call_id: str) -> bool:
        """Move all three ringing-state entries (warmup, started_at,
        event) from ``original_call_id`` to ``actual_call_id`` when the
        PBX swaps our planned channel id for a trunk-created one.
        Returns True if anything moved. Wraps the existing
        ``ringing_alias.alias_ringing_call_id`` helper."""
        ...

    # ── Ringing-warmup timestamps (float, for the watchdog) ─────────

    def set_ringing_started_at(self, call_id: str, ts: float) -> None: ...

    def get_ringing_started_at(self, call_id: str) -> Optional[float]: ...

    def clear_ringing_started_at(self, call_id: str) -> None: ...

    def iter_ringing_started_at_items(self) -> list[tuple[str, float]]: ...

    # ── Per-call first speaker ("agent"/"user") ─────────────────────

    def set_first_speaker(self, call_id: str, value: str) -> None: ...

    def get_first_speaker(self, call_id: str) -> Optional[str]: ...

    def clear_first_speaker(self, call_id: str) -> None: ...

    # ── Ringing sync events (asyncio.Event, process-local always) ──

    def set_ringing_event(self, call_id: str, event: object) -> None: ...

    def get_ringing_event(self, call_id: str) -> Optional[object]: ...

    def pop_ringing_event(self, call_id: str) -> Optional[object]: ...

    def iter_ringing_event_keys(self) -> list[str]: ...

    # ── Lifecycle / liveness ────────────────────────────────────────
    #
    # ``touch_call`` is called on the audio path to refresh the
    # session's TTL in Redis. ``LocalOnlyStateBackend`` makes it a
    # no-op; ``RedisBackedStateBackend`` debounces to once per ~30s
    # so per-packet calls are cheap.

    def touch_call(self, call_id: str) -> None: ...

    async def recover_orphans(self) -> list[dict[str, Any]]:
        """Called once at process startup. Returns metadata for calls
        that the previous incarnation of this pod owned but didn't
        unregister cleanly. Caller is responsible for hanging them up.

        ``LocalOnlyStateBackend`` always returns ``[]`` (there's no
        persistence so there are no orphans by definition)."""
        ...

    async def start_heartbeat(self) -> None:
        """Start a background task that renews this pod's liveness
        marker in Redis. ``LocalOnlyStateBackend`` makes this a
        no-op."""
        ...

    async def shutdown(self) -> None:
        """Best-effort graceful shutdown — clear the pod heartbeat,
        cancel the heartbeat task. ``LocalOnlyStateBackend`` is a
        no-op."""
        ...


# ─────────────────────────────────────────────────────────────────────
# Local-only implementation
# ─────────────────────────────────────────────────────────────────────
#
# This backend references the legacy module-level dicts in
# ``telephony_bridge.py`` directly. That's intentional for step 1 of
# the migration — we get the abstraction in place without changing
# any storage. As consumers migrate to call through this backend, the
# module dicts can later be moved inside this class without breaking
# anyone.

class LocalOnlyStateBackend:
    """Pure-memory backend matching today's behaviour exactly.

    Holds **references** to the legacy module dicts in
    ``app.api.v1.endpoints.telephony_bridge`` so step 1 of the
    Phase-1 migration changes no observable behaviour. A future step
    will lift the dict ownership into this class.

    The Redis-backed methods are intentional no-ops:

    * ``touch_call`` — local memory doesn't expire, nothing to refresh.
    * ``recover_orphans`` — local memory doesn't persist across
      restarts, so by definition there are no orphans of a previous
      incarnation. Returns ``[]``.
    * ``start_heartbeat`` / ``shutdown`` — no shared liveness state.
    """

    def __init__(self) -> None:
        # Lazy module import — avoids a circular import at module load
        # time (the bridge module imports the lifecycle helpers, which
        # may eventually import this module).
        from app.api.v1.endpoints import telephony_bridge as _tb
        self._tb = _tb
        # Per-call first-speaker ("agent"/"user"), stored independently of the
        # ringing-warmup so the answer path can read it even when the warmup was
        # never consumed (the fresh-session fallback). Process-local.
        self._first_speaker_by_call: dict[str, str] = {}

    # ── Voice session registry ──────────────────────────────────────

    def set_voice_session(
        self,
        call_id: str,
        voice_session: object,
        *,
        tenant_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        first_speaker: Optional[str] = None,
    ) -> None:
        # Metadata is silently dropped — the local backend doesn't
        # persist it. The Redis backend will use these kwargs.
        del tenant_id, campaign_id, first_speaker
        self._tb._telephony_sessions[call_id] = voice_session

    def get_voice_session(self, call_id: str) -> Optional[object]:
        return self._tb._telephony_sessions.get(call_id)

    def pop_voice_session(self, call_id: str) -> Optional[object]:
        return self._tb._telephony_sessions.pop(call_id, None)

    def voice_session_count(self) -> int:
        return len(self._tb._telephony_sessions)

    def iter_voice_session_items(self) -> list[tuple[str, object]]:
        # list(...) snapshots so the watchdog can mutate concurrently.
        return list(self._tb._telephony_sessions.items())

    # ── Gateway session map ─────────────────────────────────────────

    def get_call_id_for_gateway_session(self, gateway_session_id: str) -> Optional[str]:
        return self._tb._gateway_session_to_call_id.get(gateway_session_id)

    def set_call_id_for_gateway_session(self, gateway_session_id: str, call_id: str) -> None:
        self._tb._gateway_session_to_call_id[gateway_session_id] = call_id

    def remove_gateway_sessions_for_call(self, call_id: str) -> None:
        # Mirrors the existing _on_call_ended cleanup pattern.
        stale = [
            gw for gw, cid in self._tb._gateway_session_to_call_id.items()
            if cid == call_id
        ]
        for gw in stale:
            self._tb._gateway_session_to_call_id.pop(gw, None)
            self._tb._early_audio_buffers.pop(gw, None)

    def remove_gateway_session(self, gateway_session_id: str) -> None:
        self._tb._gateway_session_to_call_id.pop(gateway_session_id, None)

    def iter_gateway_session_items(self) -> list[tuple[str, str]]:
        return list(self._tb._gateway_session_to_call_id.items())

    # ── Early-audio buffer ──────────────────────────────────────────

    def append_early_audio(self, gateway_session_id: str, chunk: bytes) -> int:
        buf = self._tb._early_audio_buffers.setdefault(gateway_session_id, [])
        # Faithful to the original audio-callback behaviour: once the
        # buffer hits the cap (~10s of 40ms batches) we STOP appending,
        # keeping the earliest audio (the caller's opening words) and
        # dropping later chunks. The buffer only lives for the brief race
        # window before _on_new_call drains it, so the cap is a safety
        # bound that's never reached in practice.
        if len(buf) < self._tb._EARLY_AUDIO_MAX_CHUNKS:
            buf.append(chunk)
        return len(buf)

    def drain_early_audio(self, gateway_session_id: str) -> list[bytes]:
        return self._tb._early_audio_buffers.pop(gateway_session_id, []) or []

    def discard_early_audio(self, gateway_session_id: str) -> None:
        self._tb._early_audio_buffers.pop(gateway_session_id, None)

    def iter_early_audio_keys(self) -> list[str]:
        return list(self._tb._early_audio_buffers.keys())

    # ── Ringing warmup ──────────────────────────────────────────────

    def set_ringing_warmup(
        self,
        call_id: str,
        voice_session: object,
        connect_task: Optional[object],
        *,
        tenant_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        first_speaker: Optional[str] = None,
    ) -> None:
        del tenant_id, campaign_id, first_speaker
        self._tb._ringing_warmups[call_id] = (voice_session, connect_task)

    def get_ringing_warmup(self, call_id: str) -> Optional[tuple[object, Optional[object]]]:
        return self._tb._ringing_warmups.get(call_id)

    def pop_ringing_warmup(self, call_id: str) -> Optional[tuple[object, Optional[object]]]:
        return self._tb._ringing_warmups.pop(call_id, None)

    def has_ringing_warmup(self, call_id: str) -> bool:
        return call_id in self._tb._ringing_warmups

    def iter_ringing_warmup_keys(self) -> list[str]:
        return list(self._tb._ringing_warmups.keys())

    def ringing_warmup_count(self) -> int:
        return len(self._tb._ringing_warmups)

    def alias_ringing_call(self, original_call_id: str, actual_call_id: str) -> bool:
        from app.domain.services.telephony.ringing_alias import alias_ringing_call_id
        return alias_ringing_call_id(
            original_call_id=original_call_id,
            actual_call_id=actual_call_id,
            ringing_warmups=self._tb._ringing_warmups,
            ringing_warmup_created_at=self._tb._ringing_warmup_created_at,
            ringing_events=self._tb._ringing_events,
        )

    # ── Ringing started_at ──────────────────────────────────────────

    def set_ringing_started_at(self, call_id: str, ts: float) -> None:
        self._tb._ringing_warmup_created_at[call_id] = ts

    def get_ringing_started_at(self, call_id: str) -> Optional[float]:
        return self._tb._ringing_warmup_created_at.get(call_id)

    def clear_ringing_started_at(self, call_id: str) -> None:
        self._tb._ringing_warmup_created_at.pop(call_id, None)

    def iter_ringing_started_at_items(self) -> list[tuple[str, float]]:
        return list(self._tb._ringing_warmup_created_at.items())

    # ── Per-call first speaker ──────────────────────────────────────

    def set_first_speaker(self, call_id: str, value: str) -> None:
        self._first_speaker_by_call[call_id] = value

    def get_first_speaker(self, call_id: str) -> Optional[str]:
        return self._first_speaker_by_call.get(call_id)

    def clear_first_speaker(self, call_id: str) -> None:
        self._first_speaker_by_call.pop(call_id, None)

    # ── Ringing events ──────────────────────────────────────────────

    def set_ringing_event(self, call_id: str, event: object) -> None:
        self._tb._ringing_events[call_id] = event

    def get_ringing_event(self, call_id: str) -> Optional[object]:
        return self._tb._ringing_events.get(call_id)

    def pop_ringing_event(self, call_id: str) -> Optional[object]:
        return self._tb._ringing_events.pop(call_id, None)

    def iter_ringing_event_keys(self) -> list[str]:
        return list(self._tb._ringing_events.keys())

    # ── Lifecycle no-ops ────────────────────────────────────────────

    def touch_call(self, call_id: str) -> None:
        # No-op — local memory doesn't expire.
        del call_id

    async def recover_orphans(self) -> list[dict[str, Any]]:
        # Local memory doesn't persist; nothing to recover.
        return []

    async def start_heartbeat(self) -> None:
        # No shared liveness state to heartbeat against.
        return None

    async def shutdown(self) -> None:
        return None


# ─────────────────────────────────────────────────────────────────────
# Redis-backed implementation (write-through mirror)
# ─────────────────────────────────────────────────────────────────────


class SessionRegistryProto(Protocol):
    """The subset of ``SessionRegistry`` that the Redis backend uses.

    Declared as a Protocol so the backend isn't import-coupled to the
    concrete registry and tests can pass a fake."""

    pod_id: str

    async def register_call(
        self,
        call_id: str,
        *,
        state: str,
        tenant_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        first_speaker: Optional[str] = None,
    ) -> None: ...

    async def unregister_call(self, call_id: str) -> None: ...

    async def touch_call(self, call_id: str) -> None: ...

    async def scan_sessions(self) -> list[dict[str, Any]]: ...

    async def list_own_calls(self) -> list[dict[str, Any]]: ...

    async def is_incarnation_alive(self, incarnation_id: str) -> bool: ...

    async def write_heartbeat(self, ttl_seconds: int) -> None: ...

    async def clear_heartbeat(self) -> None: ...


class RedisBackedStateBackend:
    """Same in-process storage as ``LocalOnlyStateBackend``, plus a
    best-effort Redis mirror of the call-ownership ledger so calls
    survive a process restart.

    # Design: composition + write-through

    All reads and all live-object storage delegate to an embedded
    ``LocalOnlyStateBackend`` — identical hot-path behaviour, no Redis
    on the per-RTP-packet path. On the four lifecycle *writes* that
    change which calls are live (session set/pop, ringing-warmup set),
    we ALSO mirror to Redis via ``SessionRegistry``.

    # Why the mirror is fire-and-forget

    The backend Protocol methods are synchronous (they're called from
    deep inside async handlers without ``await``), but Redis ops are
    async. Rather than make every call site ``await`` — re-touching the
    code step 2 just migrated — we schedule the Redis write as a tracked
    ``asyncio.Task``. This is correct for a restart-safety ledger:

      * Reads never depend on the mirror (they're local).
      * Same-call set→pop happen seconds/minutes apart, so the tiny
        create-order race window is never hit in practice.
      * Every ledger entry has a TTL, so even a lost write self-heals.
      * Recovery (step 4) only acts on entries owned by a *dead* pod
        (missing heartbeat); a live pod's transient inconsistency is
        never acted on and TTLs out.

    If there's no running event loop (e.g. a unit test calling the sync
    method directly), the mirror write is skipped with a debug log —
    the local store still updates, so behaviour degrades to
    local-only, never breaks.
    """

    # Heartbeat cadence: renew every _HEARTBEAT_INTERVAL_S with a
    # _HEARTBEAT_TTL_S TTL. TTL must be comfortably larger than the
    # interval so a single slow tick doesn't make a live process look
    # dead. 20s interval / 60s TTL gives 3 missed beats of slack.
    _HEARTBEAT_INTERVAL_S = 20
    _HEARTBEAT_TTL_S = 60

    # Min seconds between real Redis TTL refreshes for a given call on the
    # audio hot path. Must be comfortably below the active-session TTL
    # (600s) so the entry never expires under a live call: 30s leaves a
    # 20x margin even if a couple of refreshes are missed.
    _TOUCH_DEBOUNCE_S = 30

    def __init__(self, registry: "SessionRegistryProto"):
        self._local = LocalOnlyStateBackend()
        self._registry = registry
        # Keep strong refs to in-flight mirror tasks so they aren't GC'd
        # mid-flight (asyncio only holds weak refs to tasks).
        self._tasks: set[Any] = set()
        self._heartbeat_task: Optional[Any] = None
        # call_id -> last monotonic time we actually hit Redis for a touch.
        self._last_touch: dict[str, float] = {}

    # ── Fire-and-forget helper ──────────────────────────────────────

    def _spawn(self, coro) -> None:
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — skip the mirror (local store still updated).
            logger.debug("redis_state_backend: no running loop, skipping mirror write")
            coro.close()
            return
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: Any) -> None:
        self._tasks.discard(task)
        exc = task.exception() if not task.cancelled() else None
        if exc is not None:
            logger.warning("redis_state_backend mirror task failed: %s", exc)

    # ── Voice session registry (write-through) ──────────────────────

    def set_voice_session(
        self,
        call_id: str,
        voice_session: object,
        *,
        tenant_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        first_speaker: Optional[str] = None,
    ) -> None:
        self._local.set_voice_session(
            call_id, voice_session,
            tenant_id=tenant_id, campaign_id=campaign_id, first_speaker=first_speaker,
        )
        self._spawn(self._registry.register_call(
            call_id, state="active",
            tenant_id=tenant_id, campaign_id=campaign_id, first_speaker=first_speaker,
        ))

    def get_voice_session(self, call_id: str) -> Optional[object]:
        return self._local.get_voice_session(call_id)

    def pop_voice_session(self, call_id: str) -> Optional[object]:
        vs = self._local.pop_voice_session(call_id)
        # Unregister regardless of whether the local pop found anything —
        # idempotent and guards against a local/Redis drift.
        self._spawn(self._registry.unregister_call(call_id))
        # Drop the touch-debounce bookkeeping so the map can't grow
        # unbounded across the process lifetime.
        self._last_touch.pop(call_id, None)
        return vs

    def voice_session_count(self) -> int:
        return self._local.voice_session_count()

    def iter_voice_session_items(self) -> list[tuple[str, object]]:
        return self._local.iter_voice_session_items()

    # ── Gateway map (local only — hot path / not needed for recovery) ─

    def get_call_id_for_gateway_session(self, gateway_session_id: str) -> Optional[str]:
        return self._local.get_call_id_for_gateway_session(gateway_session_id)

    def set_call_id_for_gateway_session(self, gateway_session_id: str, call_id: str) -> None:
        self._local.set_call_id_for_gateway_session(gateway_session_id, call_id)

    def remove_gateway_sessions_for_call(self, call_id: str) -> None:
        self._local.remove_gateway_sessions_for_call(call_id)

    def remove_gateway_session(self, gateway_session_id: str) -> None:
        self._local.remove_gateway_session(gateway_session_id)

    def iter_gateway_session_items(self) -> list[tuple[str, str]]:
        return self._local.iter_gateway_session_items()

    # ── Early audio (local only — transient race-window buffer) ─────

    def append_early_audio(self, gateway_session_id: str, chunk: bytes) -> int:
        return self._local.append_early_audio(gateway_session_id, chunk)

    def drain_early_audio(self, gateway_session_id: str) -> list[bytes]:
        return self._local.drain_early_audio(gateway_session_id)

    def discard_early_audio(self, gateway_session_id: str) -> None:
        self._local.discard_early_audio(gateway_session_id)

    def iter_early_audio_keys(self) -> list[str]:
        return self._local.iter_early_audio_keys()

    # ── Ringing warmup (write-through: register as 'ringing') ───────

    def set_ringing_warmup(
        self,
        call_id: str,
        voice_session: object,
        connect_task: Optional[object],
        *,
        tenant_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        first_speaker: Optional[str] = None,
    ) -> None:
        self._local.set_ringing_warmup(
            call_id, voice_session, connect_task,
            tenant_id=tenant_id, campaign_id=campaign_id, first_speaker=first_speaker,
        )
        self._spawn(self._registry.register_call(
            call_id, state="ringing",
            tenant_id=tenant_id, campaign_id=campaign_id, first_speaker=first_speaker,
        ))

    def get_ringing_warmup(self, call_id: str) -> Optional[tuple[object, Optional[object]]]:
        return self._local.get_ringing_warmup(call_id)

    def pop_ringing_warmup(self, call_id: str) -> Optional[tuple[object, Optional[object]]]:
        # No Redis unregister here: the common path is "consume → promote
        # to active" (set_voice_session re-registers). Orphaned ringing
        # entries TTL out on their own (180s) and are only recovered if
        # the owning pod is dead — see module docstring.
        return self._local.pop_ringing_warmup(call_id)

    def has_ringing_warmup(self, call_id: str) -> bool:
        return self._local.has_ringing_warmup(call_id)

    def iter_ringing_warmup_keys(self) -> list[str]:
        return self._local.iter_ringing_warmup_keys()

    def ringing_warmup_count(self) -> int:
        return self._local.ringing_warmup_count()

    def alias_ringing_call(self, original_call_id: str, actual_call_id: str) -> bool:
        moved = self._local.alias_ringing_call(original_call_id, actual_call_id)
        if moved:
            # Re-register under the new id; the old id's hash TTLs out.
            self._spawn(self._registry.register_call(actual_call_id, state="ringing"))
        return moved

    # ── Ringing started_at / events (local only) ────────────────────

    def set_ringing_started_at(self, call_id: str, ts: float) -> None:
        self._local.set_ringing_started_at(call_id, ts)

    def get_ringing_started_at(self, call_id: str) -> Optional[float]:
        return self._local.get_ringing_started_at(call_id)

    def clear_ringing_started_at(self, call_id: str) -> None:
        self._local.clear_ringing_started_at(call_id)

    def iter_ringing_started_at_items(self) -> list[tuple[str, float]]:
        return self._local.iter_ringing_started_at_items()

    # ── Per-call first speaker (local only) ─────────────────────────

    def set_first_speaker(self, call_id: str, value: str) -> None:
        self._local.set_first_speaker(call_id, value)

    def get_first_speaker(self, call_id: str) -> Optional[str]:
        return self._local.get_first_speaker(call_id)

    def clear_first_speaker(self, call_id: str) -> None:
        self._local.clear_first_speaker(call_id)

    def set_ringing_event(self, call_id: str, event: object) -> None:
        self._local.set_ringing_event(call_id, event)

    def get_ringing_event(self, call_id: str) -> Optional[object]:
        return self._local.get_ringing_event(call_id)

    def pop_ringing_event(self, call_id: str) -> Optional[object]:
        return self._local.pop_ringing_event(call_id)

    def iter_ringing_event_keys(self) -> list[str]:
        return self._local.iter_ringing_event_keys()

    # ── Lifecycle ───────────────────────────────────────────────────
    #
    # touch_call / recover_orphans / heartbeat are fleshed out in step 4.
    # Step 3 wires touch_call straight through (no debounce yet — that's
    # step 5) so the plumbing is exercised end-to-end.

    def touch_call(self, call_id: str) -> None:
        """Refresh the ledger TTL for a live call — safe to call on the
        per-RTP-packet audio path because it's debounced.

        At ~50 packets/sec/call, hitting Redis every packet would be
        thousands of EXPIREs/sec. Instead we record the last real touch
        per call_id (monotonic clock) and only mirror to Redis when
        ``_TOUCH_DEBOUNCE_S`` has elapsed. The common case is a dict
        lookup + float compare — microseconds, no Redis, no task spawn.
        """
        import time as _time
        now = _time.monotonic()
        last = self._last_touch.get(call_id)
        if last is not None and (now - last) < self._TOUCH_DEBOUNCE_S:
            return
        self._last_touch[call_id] = now
        self._spawn(self._registry.touch_call(call_id))

    async def recover_orphans(self) -> list[dict[str, Any]]:
        """Return — and claim — the ledger entries whose owning process
        is confirmed dead (its heartbeat key is gone).

        Safety: an entry is only an orphan if (a) it was NOT written by
        THIS incarnation, and (b) the owning incarnation's heartbeat is
        absent. A live sibling worker's heartbeat is present, so its
        calls are skipped — no cross-worker clobbering. ``is_incarnation_
        alive`` returns True on a Redis error, so a transient blip never
        causes a false 'dead' verdict.

        Claimed orphans are unregistered from the ledger here so a
        concurrent caller (startup + watchdog) can't recover the same
        call twice. The caller is responsible for the ARI hangup and the
        recovery stream-event.
        """
        sessions = await self._registry.scan_sessions()
        my_id = self._registry.pod_id
        orphans: list[dict[str, Any]] = []
        for entry in sessions:
            owner = entry.get("pod_id")
            if not owner or owner == my_id:
                continue
            if await self._registry.is_incarnation_alive(owner):
                continue
            orphans.append(entry)
        # Claim them (delete the hash) so they're recovered exactly once.
        for entry in orphans:
            call_id = entry.get("call_id")
            if call_id:
                await self._registry.unregister_call(call_id)
        if orphans:
            logger.info(
                "telephony recover_orphans: %d dead-process call(s) reclaimed",
                len(orphans),
            )
        return orphans

    async def start_heartbeat(self) -> None:
        """Begin renewing this incarnation's heartbeat. Idempotent."""
        import asyncio
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return
        # Write one immediately so recovery on a fast-restarting peer
        # sees us alive without waiting a full interval.
        await self._registry.write_heartbeat(self._HEARTBEAT_TTL_S)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            "telephony heartbeat started pod=%s interval=%ds ttl=%ds",
            self._registry.pod_id, self._HEARTBEAT_INTERVAL_S, self._HEARTBEAT_TTL_S,
        )

    async def _heartbeat_loop(self) -> None:
        import asyncio
        try:
            while True:
                await asyncio.sleep(self._HEARTBEAT_INTERVAL_S)
                await self._registry.write_heartbeat(self._HEARTBEAT_TTL_S)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # never let the loop die silently
            logger.warning("telephony heartbeat loop error: %s", exc)

    async def shutdown(self) -> None:
        """Graceful shutdown: stop the heartbeat, clear it so the
        successor process recovers our calls immediately, and drain any
        in-flight mirror writes."""
        import asyncio
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass
            self._heartbeat_task = None
        await self._registry.clear_heartbeat()
        pending = list(self._tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


# ─────────────────────────────────────────────────────────────────────
# Backend selection (feature flag)
# ─────────────────────────────────────────────────────────────────────


_STATE_BACKEND: Optional[TelephonyStateBackend] = None


def _resolve_incarnation_id() -> str:
    """Per-PROCESS identity: ``{host}:{short-uuid}``, unique each start.

    NOT a stable hostname. Uniqueness per process is what makes recovery
    safe under ``--workers N``: each worker has its own heartbeat, and a
    restarting worker only reclaims sessions whose owning incarnation's
    heartbeat is gone. A live sibling keeps a distinct id + heartbeat, so
    its calls are never reclaimed. The host prefix keeps the id readable
    in ``redis-cli`` (``telephony:pod:host:ab12cd34:heartbeat``)."""
    import os as _os
    import uuid as _uuid
    host = _os.getenv("POD_ID") or _safe_hostname()
    return f"{host}:{_uuid.uuid4().hex[:8]}"


def _safe_hostname() -> str:
    import os as _os
    try:
        return _os.uname().nodename
    except Exception:
        return "pod-unknown"


def _build_backend() -> TelephonyStateBackend:
    """Build the configured backend.

    Reads ``TELEPHONY_STATE_BACKEND`` once. Defaults to ``memory`` so
    deploying this module doesn't change behaviour until an operator
    explicitly flips the flag. ``redis`` builds the write-through mirror
    backend when the DI container exposes a live Redis client; if Redis
    isn't available it falls back to local memory so a misconfigured
    flag never takes the pod down.
    """
    choice = os.getenv("TELEPHONY_STATE_BACKEND", "memory").strip().lower()
    if choice == "redis":
        redis_client = _resolve_redis_client()
        if redis_client is None:
            logger.warning(
                "TELEPHONY_STATE_BACKEND=redis but no Redis client is available "
                "(container not initialised or redis disabled) — falling back to "
                "LocalOnlyStateBackend. The pod still works, just without "
                "restart-recovery."
            )
            return LocalOnlyStateBackend()
        from app.domain.services.telephony.session_registry import SessionRegistry
        registry = SessionRegistry(redis_client, _resolve_incarnation_id())
        logger.info(
            "telephony_state_backend=redis pod_id=%s — restart recovery enabled",
            registry.pod_id,
        )
        return RedisBackedStateBackend(registry)
    if choice != "memory":
        logger.warning(
            "TELEPHONY_STATE_BACKEND=%s is not a known choice; "
            "falling back to LocalOnlyStateBackend.",
            choice,
        )
    return LocalOnlyStateBackend()


def _resolve_redis_client() -> Optional[Any]:
    """Fetch the live Redis client from the DI container, or None.

    Mirrors the ``getattr(container, "redis", None)`` pattern used
    across the telephony stack. Returns None when the container isn't
    initialised yet (e.g. very early startup) so callers degrade
    gracefully."""
    try:
        from app.core.container import get_container
        container = get_container()
        if not getattr(container, "is_initialized", False):
            return None
        return getattr(container, "redis", None)
    except Exception as exc:
        logger.debug("state_backend: could not resolve redis client: %s", exc)
        return None


def get_state_backend() -> TelephonyStateBackend:
    """Return the process-wide telephony state backend.

    Idempotent — the backend is constructed lazily on first call and
    cached for the lifetime of the process. Tests can call
    :func:`reset_state_backend_for_tests` between cases to force a
    fresh instance with a re-read env var.
    """
    global _STATE_BACKEND
    if _STATE_BACKEND is None:
        _STATE_BACKEND = _build_backend()
    return _STATE_BACKEND


def reset_state_backend_for_tests() -> None:
    """Clear the cached backend so the next ``get_state_backend()`` call
    re-reads the env var and constructs a fresh instance.

    Test-only helper. Production code should never call this — the
    backend is intentionally process-wide so the local dicts stay
    coherent."""
    global _STATE_BACKEND
    _STATE_BACKEND = None
