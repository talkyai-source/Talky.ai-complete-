"""Redis-backed ledger of which calls are live and which process owns them.

This is the durable mirror behind ``RedisBackedStateBackend`` (Phase 1,
item 1 of the architecture roadmap). It exists for ONE purpose: when a
uvicorn worker restarts, the new process must find the calls the dead
process owned and hang them up cleanly instead of leaving them as
zombies that Asterisk thinks are still up.

# Identity: per-process incarnation, not per-host

The ``incarnation_id`` passed in is unique per process start
(``{hostname}:{short-uuid}``), NOT a stable hostname. This is the key
to safety under uvicorn ``--workers N``: every worker process has its
own identity and its own heartbeat. Recovery only reclaims a call when
the owning incarnation's heartbeat is *absent* (that exact process is
confirmed dead). A live sibling worker's heartbeat is present, so its
calls are never touched — no cross-worker clobbering.

# What it stores (and deliberately does NOT)

Only call identity + ownership, never the live objects:

  * ``telephony:session:{call_id}`` — HASH {pod_id (= owning
    incarnation), state (active|ringing), tenant_id, campaign_id,
    first_speaker, created_at, updated_at}. TTL 600s active / 180s
    ringing, so a crashed process's entries self-expire even if
    recovery never runs.
  * ``telephony:pod:{incarnation}:heartbeat`` — liveness marker with a
    short TTL, renewed by the heartbeat task. Absence ⇒ that process is
    dead and its sessions are eligible for recovery.

Recovery scans ``telephony:session:*`` (bounded by the concurrency cap,
~50 keys) rather than maintaining a per-incarnation owned-set — simpler,
and avoids leaking a dangling set key when an incarnation dies.

It intentionally does NOT mirror the gateway-session map, early-audio
buffers, ringing events, or VoiceSession objects: those are hot-path,
transient race-window buffers, or in-process sync primitives — on
restart the affected calls are torn down anyway.

# Conventions

Matches ``global_concurrency.py``: ``telephony:`` colon keys,
``set(key, value, ex=ttl)`` TTLs, ``pipeline(transaction=True)`` for
atomic-ish writes, bytes-decode on reads, best-effort error handling
(missing client / Redis error ⇒ benign default, never raises into the
call path).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


_SESSION_KEY_PREFIX = "telephony:session:"
_SESSION_SCAN_MATCH = "telephony:session:*"
_HEARTBEAT_PREFIX = "telephony:pod:"          # + {incarnation}:heartbeat

# Single-owner lock: exactly one process may hold the ARI event
# connection to Asterisk and serve telephony. The value is the owning
# incarnation id; the key carries a short TTL renewed by the heartbeat
# task. If the owner dies, the key lapses and a successor can acquire it.
_ARI_OWNER_KEY = "telephony:ari_owner"

# Lua: extend the TTL only if WE still hold the lock. Prevents a process
# that lost ownership (e.g. paused past the TTL, another took over) from
# stealing it back on renew. Returns 1 if renewed, 0 otherwise.
_RENEW_OWNER_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
)

# Lua: delete only if WE own it — never drop a successor's lock.
_RELEASE_OWNER_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)

# TTL ceilings. Active calls renew via touch_call (step 5) and the
# watchdog; ringing warmups never renew — they either get promoted to
# active (which rewrites the hash with the active TTL) or expire. Match
# the in-process constants: 600s active (== global_concurrency lease),
# 180s ringing (== _RINGING_MAX_AGE_S in telephony_bridge).
_ACTIVE_TTL_SECONDS = 600
_RINGING_TTL_SECONDS = 180


def _session_key(call_id: str) -> str:
    return f"{_SESSION_KEY_PREFIX}{call_id}"


def _heartbeat_key(incarnation_id: str) -> str:
    return f"{_HEARTBEAT_PREFIX}{incarnation_id}:heartbeat"


def _call_id_from_session_key(key: str) -> str:
    return key[len(_SESSION_KEY_PREFIX):] if key.startswith(_SESSION_KEY_PREFIX) else key


def _decode(value: Any) -> Any:
    return value.decode() if isinstance(value, (bytes, bytearray)) else value


class SessionRegistry:
    """Async Redis operations for the call-ownership ledger.

    Constructed with the live ``redis`` client (from the DI container)
    and this process's ``incarnation_id``. All methods are best-effort:
    a missing client or a Redis error logs and returns a benign default,
    never raises into the call path.
    """

    def __init__(self, redis_client: Any, incarnation_id: str):
        self._redis = redis_client
        self._pod_id = incarnation_id

    @property
    def pod_id(self) -> str:
        """This process's incarnation id (kept named ``pod_id`` because
        it's the value stored in each session hash's ``pod_id`` field)."""
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

        ``state`` is ``"active"`` or ``"ringing"`` — selects the TTL.
        Promoting ringing → active just calls this again with
        ``state="active"``, rewriting the hash and bumping the TTL.
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
                pipe.hset(_session_key(call_id), mapping=mapping)
                pipe.hsetnx(_session_key(call_id), "created_at", now)
                pipe.expire(_session_key(call_id), ttl)
                await pipe.execute()
        except Exception as exc:
            logger.warning(
                "session_registry.register_failed call=%s state=%s err=%s",
                call_id[:12] if call_id else "-", state, exc,
            )

    async def unregister_call(self, call_id: str) -> None:
        """Remove a call from the ledger (on hangup / teardown / recovery)."""
        if self._redis is None:
            return
        try:
            await self._redis.delete(_session_key(call_id))
        except Exception as exc:
            logger.warning(
                "session_registry.unregister_failed call=%s err=%s",
                call_id[:12] if call_id else "-", exc,
            )

    async def touch_call(self, call_id: str) -> None:
        """Renew the active-call TTL. Called from the audio path
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

    # ── Heartbeat ───────────────────────────────────────────────────

    async def write_heartbeat(self, ttl_seconds: int) -> None:
        """Stamp this incarnation's liveness marker. Absence ⇒ this
        process is presumed dead and its sessions become recoverable."""
        if self._redis is None:
            return
        try:
            await self._redis.set(
                _heartbeat_key(self._pod_id), _now_iso(), ex=ttl_seconds,
            )
        except Exception as exc:
            logger.debug("session_registry.heartbeat_failed pod=%s err=%s", self._pod_id, exc)

    async def clear_heartbeat(self) -> None:
        """Drop this incarnation's heartbeat on graceful shutdown so the
        successor process recovers its sessions immediately rather than
        waiting for the TTL to expire."""
        if self._redis is None:
            return
        try:
            await self._redis.delete(_heartbeat_key(self._pod_id))
        except Exception as exc:
            logger.debug("session_registry.heartbeat_clear_failed pod=%s err=%s", self._pod_id, exc)

    async def is_incarnation_alive(self, incarnation_id: str) -> bool:
        """True iff that incarnation's heartbeat key still exists.

        Errs on the side of 'alive' (returns True) when Redis is
        unavailable — better to leave a possibly-dead call alone than to
        hang up a possibly-live one on a transient Redis blip."""
        if self._redis is None:
            return True
        try:
            return bool(await self._redis.exists(_heartbeat_key(incarnation_id)))
        except Exception as exc:
            logger.debug("session_registry.alive_check_failed pod=%s err=%s", incarnation_id, exc)
            return True

    # ── Single-owner ARI lock ───────────────────────────────────────

    async def try_acquire_ari_ownership(self, ttl_seconds: int) -> bool:
        """Atomically claim the single telephony-owner slot for THIS
        incarnation. ``SET telephony:ari_owner <me> NX EX ttl``.

        Returns True iff this process is now the owner. Re-acquiring when
        we already hold it also returns True (the value matches), so a
        heartbeat-driven retry is idempotent.

        Fails OPEN: if Redis is unavailable we return True. Rationale —
        with no Redis there is no coordination layer, which only happens
        in single-process/dev runs where this process *is* the only one;
        refusing telephony there would be a worse failure than the lock
        being absent.
        """
        if self._redis is None:
            return True
        try:
            won = await self._redis.set(
                _ARI_OWNER_KEY, self._pod_id, nx=True, ex=ttl_seconds,
            )
            if won:
                return True
            # Someone holds it — True only if it's us (idempotent re-acquire).
            current = _decode(await self._redis.get(_ARI_OWNER_KEY))
            return current == self._pod_id
        except Exception as exc:
            logger.warning(
                "session_registry.ari_acquire_failed pod=%s err=%s — failing open",
                self._pod_id, exc,
            )
            return True

    async def renew_ari_ownership(self, ttl_seconds: int) -> bool:
        """Extend the owner-lock TTL, but only while we still own it.
        Returns True if renewed, False if we no longer hold the lock.
        Best-effort: a Redis error returns True (assume still ours) so a
        transient blip doesn't make a live owner relinquish telephony."""
        if self._redis is None:
            return True
        try:
            res = await self._redis.eval(
                _RENEW_OWNER_LUA, 1, _ARI_OWNER_KEY, self._pod_id, str(ttl_seconds),
            )
            return bool(res)
        except Exception as exc:
            logger.debug("session_registry.ari_renew_failed pod=%s err=%s", self._pod_id, exc)
            return True

    async def release_ari_ownership(self) -> None:
        """Drop the owner lock on graceful shutdown — but only if it's
        ours — so the successor process acquires telephony immediately
        instead of waiting out the TTL."""
        if self._redis is None:
            return
        try:
            await self._redis.eval(
                _RELEASE_OWNER_LUA, 1, _ARI_OWNER_KEY, self._pod_id,
            )
        except Exception as exc:
            logger.debug("session_registry.ari_release_failed pod=%s err=%s", self._pod_id, exc)

    async def current_ari_owner(self) -> Optional[str]:
        """The incarnation id currently holding the owner lock, or None.
        Used for the 503 message and diagnostics."""
        if self._redis is None:
            return None
        try:
            return _decode(await self._redis.get(_ARI_OWNER_KEY))
        except Exception:
            return None

    # ── Scanning / recovery ─────────────────────────────────────────

    async def scan_sessions(self) -> list[dict[str, Any]]:
        """Return every session entry in the ledger (across all
        incarnations). Each dict has ``call_id`` plus the hash fields.

        Uses SCAN (cursor-based, non-blocking) rather than KEYS so it's
        safe to call on a live Redis."""
        if self._redis is None:
            return []
        out: list[dict[str, Any]] = []
        try:
            async for raw_key in self._redis.scan_iter(match=_SESSION_SCAN_MATCH):
                key = _decode(raw_key)
                call_id = _call_id_from_session_key(key)
                try:
                    h = await self._redis.hgetall(key)
                except Exception:
                    h = None
                entry: dict[str, Any] = {"call_id": call_id}
                if h:
                    for k, v in h.items():
                        entry[_decode(k)] = _decode(v)
                out.append(entry)
        except Exception as exc:
            logger.warning("session_registry.scan_failed err=%s", exc)
        return out

    async def list_own_calls(self) -> list[dict[str, Any]]:
        """Session entries owned by THIS incarnation. (Diagnostic;
        recovery uses scan_sessions + is_incarnation_alive instead.)"""
        return [s for s in await self.scan_sessions() if s.get("pod_id") == self._pod_id]


def _now_iso() -> str:
    # ISO-ish UTC seconds; cheap and human-readable in redis-cli.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
