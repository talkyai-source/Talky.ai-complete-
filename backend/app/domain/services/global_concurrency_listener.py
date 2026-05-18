"""Cross-pod Redis coordination listeners (Phase 2.2).

Two long-lived Redis subscribers run on every pod:

1. ``keyspace_expiry_listener``
   Subscribes to Redis keyspace-notification events for keys matching
   ``telephony:lease:*``. When a lease key expires (TTL ran out — pod
   crashed or call hung mid-flight), this listener immediately removes
   the call_id from the active set so the cluster count is accurate
   within milliseconds, instead of waiting up to 30s for the next
   ``reconcile_orphans`` watchdog tick.

   Requires the Redis server to have keyspace events enabled. The
   listener attempts to enable them via CONFIG SET; if that's denied
   (managed Redis) the operator must enable manually:
       redis-cli CONFIG SET notify-keyspace-events Ex
   Without it, this listener becomes a no-op and the watchdog reconcile
   path is the sole reaper (still correct, just slower).

2. ``quota_alerts_listener``
   Subscribes to ``telephony:quota_alerts``, the existing pub/sub
   channel published from ``telephony_rate_limiter.py`` whenever a
   tenant breaches a threshold. Every pod consumes alerts so any pod
   can short-circuit make_call decisions for a throttled tenant
   without waiting for the next DB read.

Both listeners are wired in ``main.py`` lifespan startup. They are
fault-tolerant: a Redis disconnect logs and reconnects; the loop never
raises out and never blocks shutdown.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from app.domain.services.global_concurrency import (
    _ACTIVE_SET_KEY,
    _LEASE_KEY_PREFIX,
)

logger = logging.getLogger(__name__)


# Cache of recent quota-alert decisions, keyed by tenant_id. Each pod
# consults this map at make_call time before making any DB call.
# Values: {"action": "BLOCK"|"THROTTLE"|"ALLOW", "until": float_monotonic}
_quota_decisions: dict[str, dict] = {}


def get_cached_quota_decision(tenant_id: str) -> Optional[dict]:
    """Return the most recent quota decision for `tenant_id` if it has
    not expired. Used by the make_call hot path so pods don't all hit
    the threshold table for every origination."""
    decision = _quota_decisions.get(tenant_id)
    if decision is None:
        return None
    now = asyncio.get_event_loop().time()
    if decision["until"] < now:
        _quota_decisions.pop(tenant_id, None)
        return None
    return decision


async def _ensure_keyspace_events(redis_client: Any) -> bool:
    """Best-effort: turn on key-event notifications. Returns True if
    notifications are enabled (or were already), False if the server
    refused (managed Redis with locked CONFIG)."""
    try:
        cur = await redis_client.config_get("notify-keyspace-events")
        # cur is dict-like; some clients return a list of [key, val] pairs.
        if isinstance(cur, dict):
            current = cur.get("notify-keyspace-events", "")
        else:
            current = cur[1] if cur and len(cur) >= 2 else ""
        # 'E' enables key-event events; 'x' enables expired-event class.
        # We only need 'Ex'. Preserve any operator-set bits already there.
        needed = set("Ex")
        present = set(current)
        if needed.issubset(present):
            return True
        merged = "".join(sorted(present | needed))
        await redis_client.config_set("notify-keyspace-events", merged)
        logger.info(
            "keyspace_events_enabled previous=%r now=%r", current, merged,
        )
        return True
    except Exception as exc:
        logger.warning(
            "keyspace_events_unavailable err=%s — orphan reaper will rely on "
            "the periodic watchdog reconcile path",
            exc,
        )
        return False


async def keyspace_expiry_listener(
    redis_client: Any,
    *,
    stop_event: asyncio.Event,
) -> None:
    """Long-lived task: react to lease-key expiry by trimming the
    active set in real time.

    Channel pattern: ``__keyevent@<db>__:expired``. The payload of each
    message is the key name that expired. We filter for keys with our
    lease prefix and SREM the matching call_id from the active set.
    """
    if redis_client is None:
        logger.info("keyspace_expiry_listener: Redis unavailable — skipping")
        return

    enabled = await _ensure_keyspace_events(redis_client)
    if not enabled:
        return

    # Pattern covers any database the client is on; '*' is conservative
    # but cheap (we filter messages by prefix).
    pattern = "__keyevent@*__:expired"
    while not stop_event.is_set():
        pubsub = redis_client.pubsub()
        try:
            await pubsub.psubscribe(pattern)
            logger.info("keyspace_expiry_listener subscribed pattern=%s", pattern)
            async for msg in pubsub.listen():
                if stop_event.is_set():
                    break
                if msg.get("type") != "pmessage":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="ignore")
                if not data or not data.startswith(_LEASE_KEY_PREFIX):
                    continue
                call_id = data[len(_LEASE_KEY_PREFIX):]
                try:
                    removed = await redis_client.srem(_ACTIVE_SET_KEY, call_id)
                    if removed:
                        logger.info(
                            "lease_expired_reaped call_id=%s — slot freed instantly",
                            call_id[:12],
                        )
                except Exception as exc:
                    logger.debug(
                        "lease_expired_srem_failed call=%s err=%s",
                        call_id[:12], exc,
                    )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(
                "keyspace_expiry_listener error — reconnecting in 2s: %s", exc,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        finally:
            try:
                await pubsub.punsubscribe(pattern)
                await pubsub.close()
            except Exception:
                pass


async def quota_alerts_listener(
    redis_client: Any,
    *,
    stop_event: asyncio.Event,
    on_alert: Optional[Callable[[dict], Awaitable[None]]] = None,
) -> None:
    """Long-lived task: subscribe to ``telephony:quota_alerts`` and
    cache the most recent decision per tenant.

    Pods read the cache via ``get_cached_quota_decision()`` at
    make_call time; this avoids a DB hop per origination once an alert
    has been published cluster-wide.
    """
    if redis_client is None:
        logger.info("quota_alerts_listener: Redis unavailable — skipping")
        return

    channel = "telephony:quota_alerts"
    while not stop_event.is_set():
        pubsub = redis_client.pubsub()
        try:
            await pubsub.subscribe(channel)
            logger.info("quota_alerts_listener subscribed channel=%s", channel)
            async for msg in pubsub.listen():
                if stop_event.is_set():
                    break
                if msg.get("type") != "message":
                    continue
                payload = _decode_payload(msg.get("data"))
                if not payload:
                    continue
                tenant_id = payload.get("tenant_id")
                action = payload.get("action") or payload.get("decision")
                ttl = float(payload.get("ttl_seconds") or 60.0)
                if tenant_id and action:
                    _quota_decisions[tenant_id] = {
                        "action": action,
                        "until": asyncio.get_event_loop().time() + ttl,
                    }
                    logger.info(
                        "quota_alert_cached tenant=%s action=%s ttl=%.0f",
                        tenant_id, action, ttl,
                    )
                if on_alert is not None:
                    try:
                        await on_alert(payload)
                    except Exception as exc:
                        logger.debug("quota_alert_callback_raised err=%s", exc)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(
                "quota_alerts_listener error — reconnecting in 2s: %s", exc,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception:
                pass


def _decode_payload(data: Any) -> Optional[dict]:
    """Decode a pub/sub payload to a dict. Accepts JSON strings or
    already-decoded mappings; returns None for anything else."""
    if data is None:
        return None
    if isinstance(data, dict):
        return data
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="ignore")
    if isinstance(data, str):
        import json
        try:
            obj = json.loads(data)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None
