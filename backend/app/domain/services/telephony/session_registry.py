"""Redis-backed ledger of which calls are live and which pod owns them.

This is the durable mirror behind ``RedisBackedStateBackend`` (Phase 1,
item 1 of the architecture roadmap). It exists for ONE purpose: when a
uvicorn worker restarts, the new process must be able to find the calls
the previous incarnation owned and hang them up cleanly instead of
leaving them as zombies that Asterisk thinks are still up.

# What it stores (and deliberately does NOT)

It stores only the *identity + ownership* of a call — never the live
objects:

  * ``telephony:session:{call_id}`` — HASH with ``pod_id``,
    ``tenant_id``, ``campaign_id``, ``first_speaker``, ``state``
    (``active`` | ``ringing``), ``created_at``, ``updated_at``.
    TTL: 600s for active calls, 180s for ringing warmups — so a crashed
    pod's entries expire on their own even if recovery never runs.
  * ``telephony:pod:{pod_id}:owned_calls`` — SET of this pod's live
    call_ids. The fast index recovery scans on startup.
  * ``telephony:pod:{pod_id}:heartbeat`` — liveness marker with a short
    TTL, written by ``RedisBackedStateBackend.start_heartbeat`` (step 4).
    Its absence is how a peer pod knows this pod died.

It intentionally does NOT mirror the gateway-session map, early-audio
buffers, ringing events, or the VoiceSession objects. Those are either
hot-path, transient race-window buffers, or in-process sync primitives;
on restart the affected calls are being torn down anyway, so there's
nothing worth reconstructing. The ledger answers exactly one question:
"which calls were live on a now-dead pod, so I can hang them up?"

# Conventions

Matches ``global_concurrency.py``: ``telephony:`` colon-namespaced
keys, ``set(key, value, ex=ttl)`` for TTLs, ``pipeline(transaction=
True)`` for atomic-ish multi-command writes, bytes-decode on reads, and
best-effort error handling — every method degrades to a no-op (or
empty result) when Redis is unavailable rather than taking down the
call path.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


_SESSION_KEY_PREFIX = "telephony:session:"
_POD_OWNED_PREFIX = "telephony:pod:"          # + {pod_id}:owned_calls
_POD_HEARTBEAT_PREFIX = "telephony:pod:"      # + {pod_id}:heartbeat

# TTL ceilings. Active calls renew via touch_call (step 5); ringing
# warmups never renew — they either get promoted to active (which
# rewrites the hash with the active TTL) or expire. Match the in-process
# constants: 600s active (== global_concurrency lease), 180s ringing
# (== _RINGING_MAX_AGE_S in telephony_bridge).
_ACTIVE_TTL_SECONDS = 600
_RINGING_TTL_SECONDS = 180


def _session_key(call_id: str) -> str:
    return f"{_SESSION_KEY_PREFIX}{call_id}"


def _owned_set_key(pod_id: str) -> str:
    return f"{_POD_OWNED_PREFIX}{pod_id}:owned_calls"


def _heartbeat_key(pod_id: str) -> str:
    return f"{_POD_HEARTBEAT_PREFIX}{pod_id}:heartbeat"


def _decode(value: Any) -> Any:
    return value.decode() if isinstance(value, (bytes, bytearray)) else value


class SessionRegistry:
    """Async Redis operations for the call-ownership ledger.

    Constructed with the live ``redis`` client (from the DI container)
    and this process's ``pod_id``. All methods are best-effort: a
    missing client or a Redis error logs and returns a benign default,
    never raises into the call path.
    """

    def __init__(self, redis_client: Any, pod_id: str):
        self._redis = redis_client
        self._pod_id = pod_id

    @property
    def pod_id(self) -> str:
        return self._pod_id

    # ── Registration ────────────────────────────────────────────────

    async def register_call(
        self,
        call_id: str,
        *,
        state: str,
        tenant_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        first_speaker: Optional[str] = None,
    ) -> None:
        """Record (or update) a call in the ledger.

        ``state`` is ``"active"`` or ``"ringing"`` — it selects the TTL.
        Promoting a ringing call to active just calls this again with
        ``state="active"``, which rewrites the hash and bumps the TTL.
        """
        if self._redis is None:
            return
        ttl = _ACTIVE_TTL_SECONDS if state == "active" else _RINGING_TTL_SECONDS
        now = _now_iso()
        mapping = {
            "pod_id": self._pod_id,
            "state": state,
            "tenant_id": tenant_id or "",
            "campaign_id": campaign_id or "",
            "first_speaker": first_speaker or "",
            "updated_at": now,
        }
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                # created_at is set once; HSETNX-style via separate guard.
                pipe.hset(_session_key(call_id), mapping=mapping)
                pipe.hsetnx(_session_key(call_id), "created_at", now)
                pipe.expire(_session_key(call_id), ttl)
                pipe.sadd(_owned_set_key(self._pod_id), call_id)
                await pipe.execute()
        except Exception as exc:
            logger.warning(
                "session_registry.register_failed call=%s state=%s err=%s",
                call_id[:12] if call_id else "-", state, exc,
            )

    async def unregister_call(self, call_id: str) -> None:
        """Remove a call from the ledger (on hangup / teardown)."""
        if self._redis is None:
            return
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.delete(_session_key(call_id))
                pipe.srem(_owned_set_key(self._pod_id), call_id)
                await pipe.execute()
        except Exception as exc:
            logger.warning(
                "session_registry.unregister_failed call=%s err=%s",
                call_id[:12] if call_id else "-", exc,
            )

    async def touch_call(self, call_id: str) -> None:
        """Renew the active-call TTL. Called from the audio hot path
        (debounced in step 5) and the watchdog so long calls don't have
        their ledger entry expire underneath them."""
        if self._redis is None:
            return
        try:
            await self._redis.expire(_session_key(call_id), _ACTIVE_TTL_SECONDS)
        except Exception as exc:
            logger.debug(
                "session_registry.touch_failed call=%s err=%s",
                call_id[:12] if call_id else "-", exc,
            )

    # ── Heartbeat (used by step 4) ──────────────────────────────────

    async def write_heartbeat(self, ttl_seconds: int) -> None:
        """Stamp this pod's liveness marker. Absence ⇒ pod presumed dead."""
        if self._redis is None:
            return
        try:
            await self._redis.set(
                _heartbeat_key(self._pod_id), _now_iso(), ex=ttl_seconds,
            )
        except Exception as exc:
            logger.debug("session_registry.heartbeat_failed pod=%s err=%s", self._pod_id, exc)

    async def clear_heartbeat(self) -> None:
        """Drop this pod's heartbeat on graceful shutdown so peers know
        the orphan calls below are intentional, not a crash."""
        if self._redis is None:
            return
        try:
            await self._redis.delete(_heartbeat_key(self._pod_id))
        except Exception as exc:
            logger.debug("session_registry.heartbeat_clear_failed pod=%s err=%s", self._pod_id, exc)

    # ── Recovery (used by step 4) ───────────────────────────────────

    async def list_own_calls(self) -> list[dict[str, Any]]:
        """Return the ledger entries this pod_id owns.

        Used on startup: a fresh process inherits the previous
        incarnation's ``pod_id`` (stable per host/deployment), so the
        owned-set still points at the calls the dead process held. Each
        returned dict includes ``call_id`` plus the hash fields.
        """
        if self._redis is None:
            return []
        try:
            members = await self._redis.smembers(_owned_set_key(self._pod_id))
        except Exception as exc:
            logger.warning("session_registry.list_own_failed pod=%s err=%s", self._pod_id, exc)
            return []
        out: list[dict[str, Any]] = []
        for raw in members or []:
            call_id = _decode(raw)
            try:
                h = await self._redis.hgetall(_session_key(call_id))
            except Exception:
                h = None
            entry: dict[str, Any] = {"call_id": call_id}
            if h:
                for k, v in h.items():
                    entry[_decode(k)] = _decode(v)
            out.append(entry)
        return out


def _now_iso() -> str:
    # ISO-ish UTC seconds; cheap and human-readable in redis-cli.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
