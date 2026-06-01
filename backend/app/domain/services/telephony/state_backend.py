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

    def iter_gateway_session_items(self) -> list[tuple[str, str]]: ...

    # ── Early-audio buffer (bytes by gateway_session_id) ────────────

    def append_early_audio(self, gateway_session_id: str, chunk: bytes) -> None: ...

    def drain_early_audio(self, gateway_session_id: str) -> list[bytes]: ...

    def discard_early_audio(self, gateway_session_id: str) -> None: ...

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

    # ── Ringing-warmup timestamps (float, for the watchdog) ─────────

    def set_ringing_started_at(self, call_id: str, ts: float) -> None: ...

    def get_ringing_started_at(self, call_id: str) -> Optional[float]: ...

    def clear_ringing_started_at(self, call_id: str) -> None: ...

    def iter_ringing_started_at_items(self) -> list[tuple[str, float]]: ...

    # ── Ringing sync events (asyncio.Event, process-local always) ──

    def set_ringing_event(self, call_id: str, event: object) -> None: ...

    def get_ringing_event(self, call_id: str) -> Optional[object]: ...

    def pop_ringing_event(self, call_id: str) -> Optional[object]: ...

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

    def iter_gateway_session_items(self) -> list[tuple[str, str]]:
        return list(self._tb._gateway_session_to_call_id.items())

    # ── Early-audio buffer ──────────────────────────────────────────

    def append_early_audio(self, gateway_session_id: str, chunk: bytes) -> None:
        buf = self._tb._early_audio_buffers.setdefault(gateway_session_id, [])
        # Preserve the existing 250-chunk cap (~10s of 40ms batches).
        if len(buf) >= self._tb._EARLY_AUDIO_MAX_CHUNKS:
            # Drop oldest — matches today's behaviour at the boundary.
            buf.pop(0)
        buf.append(chunk)

    def drain_early_audio(self, gateway_session_id: str) -> list[bytes]:
        return self._tb._early_audio_buffers.pop(gateway_session_id, []) or []

    def discard_early_audio(self, gateway_session_id: str) -> None:
        self._tb._early_audio_buffers.pop(gateway_session_id, None)

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

    # ── Ringing started_at ──────────────────────────────────────────

    def set_ringing_started_at(self, call_id: str, ts: float) -> None:
        self._tb._ringing_warmup_created_at[call_id] = ts

    def get_ringing_started_at(self, call_id: str) -> Optional[float]:
        return self._tb._ringing_warmup_created_at.get(call_id)

    def clear_ringing_started_at(self, call_id: str) -> None:
        self._tb._ringing_warmup_created_at.pop(call_id, None)

    def iter_ringing_started_at_items(self) -> list[tuple[str, float]]:
        return list(self._tb._ringing_warmup_created_at.items())

    # ── Ringing events ──────────────────────────────────────────────

    def set_ringing_event(self, call_id: str, event: object) -> None:
        self._tb._ringing_events[call_id] = event

    def get_ringing_event(self, call_id: str) -> Optional[object]:
        return self._tb._ringing_events.get(call_id)

    def pop_ringing_event(self, call_id: str) -> Optional[object]:
        return self._tb._ringing_events.pop(call_id, None)

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
# Backend selection (feature flag)
# ─────────────────────────────────────────────────────────────────────


_STATE_BACKEND: Optional[TelephonyStateBackend] = None


def _build_backend() -> TelephonyStateBackend:
    """Build the configured backend.

    Reads ``TELEPHONY_STATE_BACKEND`` once. Defaults to ``memory`` so
    deploying this module doesn't change behaviour until an operator
    explicitly flips the flag. ``redis`` will be implemented in step 2.
    """
    choice = os.getenv("TELEPHONY_STATE_BACKEND", "memory").strip().lower()
    if choice == "redis":
        # Step 2 of the migration will add this branch. Until then we
        # log a warning and fall back to local memory rather than
        # crash, so an early env-var rollout doesn't take the pod down.
        logger.warning(
            "TELEPHONY_STATE_BACKEND=redis selected but RedisBackedStateBackend "
            "is not yet implemented — falling back to LocalOnlyStateBackend. "
            "Track item 1 of ARCHITECTURE_REVIEW_2026-05-31.md."
        )
        return LocalOnlyStateBackend()
    if choice != "memory":
        logger.warning(
            "TELEPHONY_STATE_BACKEND=%s is not a known choice; "
            "falling back to LocalOnlyStateBackend.",
            choice,
        )
    return LocalOnlyStateBackend()


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
