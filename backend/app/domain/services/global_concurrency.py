"""Cluster-wide telephony concurrency cap (T1.2).

Before this, `_telephony_sessions` was a per-process dict and
`MAX_TELEPHONY_SESSIONS` was a per-process cap. Two API pods × cap=50
gave a theoretical 100-call ceiling, but because nothing coordinated,
a single pod could be saturated while another sat idle, and the burst
on the first pod dropped calls with 429.

This module moves the cap behind Redis so every pod sees the same
counter. Each active call is represented by a lease key with a TTL
ceiling — if a pod crashes mid-call, the lease expires and the slot
is reclaimed automatically instead of leaking forever.

Design
------
- **Lease key:** `telephony:lease:{call_id}` — value is the origin pod
  id, TTL is `_LEASE_TTL_SECONDS` (renewed by a heartbeat if the call
  lasts longer).
- **Active set:** `telephony:active_call_ids` — a Redis SET that
  contains every live `call_id`. Source of truth for the global count
  (`SCARD`). Membership sweeps reconcile this set against live leases
  so crashed pods don't permanently inflate the count.
- **Caps:**
    - Global `MAX_TELEPHONY_SESSIONS_GLOBAL` (new env; if unset, falls
      back to `MAX_TELEPHONY_SESSIONS` so single-pod deploys behave
      exactly as before).
    - Per-pod `MAX_TELEPHONY_SESSIONS` still honoured as a secondary
      guard — useful for capping memory on any one box.

The bridge calls `acquire_lease()` just before `originate_call`, and
`release_lease()` on `_on_call_ended`. `refresh_lease()` is called
from the existing session watchdog every minute.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Keys — simple strings; stable names so operators can inspect them by
# hand with `redis-cli`.
_ACTIVE_SET_KEY = "telephony:active_call_ids"
_LEASE_KEY_PREFIX = "telephony:lease:"

# Lease TTL ceiling. Longest a call can be silent before we assume the
# pod crashed. Heartbeats from the session watchdog renew this while
# the call is active. 10 minutes is conservative — real calls are
# typically sub-5m; cap-wise watchdog sweeps every 60s.
_LEASE_TTL_SECONDS = 600

# Value stamped on a lease key by refresh_lease. Only its existence matters for
# counting; the value is a human-readable marker for `redis-cli` inspection.
_LEASE_REFRESHED_VALUE = "refreshed"


# ──────────────────────────────────────────────────────────────────────────
# Env-resolution helper
# ──────────────────────────────────────────────────────────────────────────

def resolve_global_cap() -> int:
    """The effective cluster-wide cap. Prefers
    `MAX_TELEPHONY_SESSIONS_GLOBAL`; falls back to
    `MAX_TELEPHONY_SESSIONS` so single-pod deploys don't need a new
    env var."""
    for name in ("MAX_TELEPHONY_SESSIONS_GLOBAL", "MAX_TELEPHONY_SESSIONS"):
        raw = os.getenv(name)
        if raw:
            try:
                value = int(raw)
                if value > 0:
                    return value
            except ValueError:
                logger.warning("invalid_cap env=%s value=%r — ignoring", name, raw)
    return 50  # matches the old default


def _lease_key(call_id: str) -> str:
    return f"{_LEASE_KEY_PREFIX}{call_id}"


# ──────────────────────────────────────────────────────────────────────────
# Operations
# ──────────────────────────────────────────────────────────────────────────

class LeaseResult:
    """Tri-state so the caller can distinguish 'acquired' from
    'refused' from 'Redis is gone, fall back to per-process cap'."""

    def __init__(self, *, acquired: bool, reason: str, current: Optional[int] = None):
        self.acquired = acquired
        self.reason = reason
        self.current = current

    def __bool__(self) -> bool:  # allows `if result:` shorthand
        return self.acquired

    def __repr__(self) -> str:
        return f"LeaseResult(acquired={self.acquired}, reason={self.reason!r}, current={self.current})"


async def acquire_lease(
    redis_client: Any,
    *,
    call_id: str,
    pod_id: str,
    cap: int,
) -> LeaseResult:
    """Try to reserve a global slot for `call_id`.

    Returns `LeaseResult(acquired=True, ...)` on success. On a full
    cluster returns `LeaseResult(acquired=False, reason="cap_reached")`.
    When Redis is unavailable returns
    `LeaseResult(acquired=True, reason="redis_unavailable_fallback")` so
    a degraded Redis doesn't bring down origination — the caller keeps
    its per-pod dict as a backstop.

    Idempotent-ish: re-acquiring the same `call_id` does not double
    the count (SADD is a set). The lease TTL is refreshed.
    """
    if redis_client is None:
        return LeaseResult(acquired=True, reason="redis_unavailable_fallback")

    try:
        # Pipeline two commands atomically-ish so SCARD reflects the
        # would-be post-insert size.
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.sadd(_ACTIVE_SET_KEY, call_id)
            pipe.scard(_ACTIVE_SET_KEY)
            added, size = await pipe.execute()

        # `added` is 1 if newly inserted, 0 if already in the set. If
        # the set size after insert exceeds the cap AND we were the
        # one who inserted, roll back and refuse.
        if size > cap and added:
            await redis_client.srem(_ACTIVE_SET_KEY, call_id)
            return LeaseResult(
                acquired=False,
                reason="cap_reached",
                current=size - 1,
            )

        # Stamp a TTL-decorated key so an orphaned call_id expires on
        # its own even if the pod crashes before release.
        await redis_client.set(
            _lease_key(call_id),
            pod_id,
            ex=_LEASE_TTL_SECONDS,
        )
        return LeaseResult(acquired=True, reason="acquired", current=size)
    except Exception as exc:
        logger.error(
            "global_concurrency_acquire_failed call=%s err=%s — fallback to per-pod cap",
            call_id[:12] if call_id else "-", exc,
        )
        return LeaseResult(acquired=True, reason="redis_error_fallback")


async def release_lease(
    redis_client: Any,
    *,
    call_id: str,
) -> None:
    """Return a slot to the pool. Safe to call multiple times."""
    if redis_client is None:
        return
    try:
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.srem(_ACTIVE_SET_KEY, call_id)
            pipe.delete(_lease_key(call_id))
            await pipe.execute()
    except Exception as exc:
        logger.warning(
            "global_concurrency_release_failed call=%s err=%s",
            call_id[:12] if call_id else "-", exc,
        )


async def refresh_lease(
    redis_client: Any,
    *,
    call_id: str,
) -> None:
    """Extend a live call's TTL. Called from the session watchdog for
    every call still in the per-pod dict. Keeps long calls from having
    their lease expire underneath them.

    If the lease key is missing (e.g. Redis restart / eviction), we
    RE-CREATE it so the next watchdog sweep doesn't count the call as
    orphaned and drop it from the global tally.
    """
    if redis_client is None:
        return
    try:
        # BUG 3 fix: recreate the lease key UNCONDITIONALLY with a plain
        # `SET ... EX`. The old code used `SET ... XX` (write only if the key
        # already exists), so after a Redis restart/eviction — exactly when the
        # key is GONE — the SET was a no-op, the EXPIRE (also only-if-exists)
        # was a no-op, and the SADD merely re-added set membership with no
        # backing lease. `reconcile_orphans` then saw membership + missing lease
        # and removed the (still-live) call from the active set → the global
        # count UNDER-reported live calls → the dialer originated ABOVE the cap.
        # A plain SET actually restores the key, so the count reflects reality.
        #
        # Resurrection-safety: the watchdog only calls refresh_lease for calls
        # still in its live per-pod session dict. A call that ended is removed
        # from that dict BEFORE release_lease runs, so a released call is never
        # refreshed — this SET can't resurrect a slot that was legitimately
        # freed.
        #
        # Ordering vs. reconcile: because the key now genuinely exists after
        # refresh, as long as the watchdog refreshes live calls before it
        # reconciles (its normal order) reconcile sees the key and keeps the
        # membership. Even in the reverse order the worst case is a one-tick
        # undercount that the next refresh self-heals — never a persistent
        # undercount, and never an overcount.
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.sadd(_ACTIVE_SET_KEY, call_id)
            pipe.set(_lease_key(call_id), _LEASE_REFRESHED_VALUE, ex=_LEASE_TTL_SECONDS)
            await pipe.execute()
    except Exception as exc:
        logger.debug(
            "global_concurrency_refresh_failed call=%s err=%s",
            call_id[:12] if call_id else "-", exc,
        )


async def current_count(redis_client: Any) -> Optional[int]:
    """Size of the active-call set. None if Redis is unavailable.

    Cheap enough to call on every `/status` request."""
    if redis_client is None:
        return None
    try:
        return int(await redis_client.scard(_ACTIVE_SET_KEY))
    except Exception as exc:
        logger.debug("global_concurrency_count_failed err=%s", exc)
        return None


async def reconcile_orphans(redis_client: Any) -> int:
    """Walk the active set and drop call_ids whose lease key has
    expired — crashed-pod cleanup. Returns the number of orphan
    entries removed.

    Called from the bridge's session watchdog, so it already runs on a
    timer — we just borrow the tick.
    """
    if redis_client is None:
        return 0
    try:
        members = await redis_client.smembers(_ACTIVE_SET_KEY)
        if not members:
            return 0
        removed = 0
        # Batch existence checks — small pipelines avoid a round-trip
        # per call_id.
        member_list = [m.decode() if isinstance(m, bytes) else m for m in members]
        async with redis_client.pipeline(transaction=False) as pipe:
            for call_id in member_list:
                pipe.exists(_lease_key(call_id))
            existences = await pipe.execute()
        for call_id, exists in zip(member_list, existences):
            if not exists:
                await redis_client.srem(_ACTIVE_SET_KEY, call_id)
                removed += 1
        if removed:
            logger.info(
                "global_concurrency_reconciled orphans=%d remaining=%d",
                removed, len(member_list) - removed,
            )
        return removed
    except Exception as exc:
        logger.warning("global_concurrency_reconcile_failed err=%s", exc)
        return 0
