"""
Pod-readiness state.

Tracks two pieces of state that the load balancer needs to know about:

1. **Drain mode** — set by ``lifespan`` on shutdown begin. While draining,
   ``is_pod_ready()`` returns False so the LB stops routing new calls to
   this pod. Existing calls continue until they hang up naturally (up to
   ``DRAIN_TIMEOUT`` seconds).

2. **Pod capacity** — derived from the active telephony session count vs.
   the ``MAX_TELEPHONY_SESSIONS`` ceiling. When the pod is at capacity,
   readiness is False so the LB picks a different pod.

The readiness endpoint at ``/api/v1/healthz/ready`` reads this module.
The make_call endpoint reads ``is_pod_at_capacity()`` directly to short-
circuit with a 503 + Retry-After when full.

Phase 1 — single-pod ready signal. Phase 2 will add LB integration with
consistent-hash affinity so call_id sticks to the pod that opened it.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Optional

# Initial state: NOT draining. Set to True when shutdown begins.
_draining: bool = False
_drain_started_at: float = 0.0

# How long the pod waits for active calls to drain before terminating.
# Container orchestrator's terminationGracePeriodSeconds must be larger.
DRAIN_TIMEOUT_S: int = int(os.getenv("DRAIN_TIMEOUT_S", "300"))

# Capacity probe — injected at startup so readiness has zero coupling to
# the bridge module. Default returns 0 (treated as "no capacity info").
_active_count_provider: Optional[Callable[[], int]] = None
_max_capacity_provider: Optional[Callable[[], int]] = None


def set_capacity_providers(
    active_count: Callable[[], int],
    max_capacity: Callable[[], int],
) -> None:
    """Wire the readiness module to the bridge's session counters."""
    global _active_count_provider, _max_capacity_provider
    _active_count_provider = active_count
    _max_capacity_provider = max_capacity


def begin_drain() -> None:
    """Mark pod NOT_READY. Called by lifespan shutdown."""
    global _draining, _drain_started_at
    _draining = True
    _drain_started_at = time.monotonic()


def is_draining() -> bool:
    return _draining


def drain_seconds_elapsed() -> float:
    if not _draining:
        return 0.0
    return time.monotonic() - _drain_started_at


def is_pod_at_capacity() -> bool:
    """True when active sessions >= max capacity."""
    if _active_count_provider is None or _max_capacity_provider is None:
        return False
    try:
        return _active_count_provider() >= _max_capacity_provider()
    except Exception:
        return False


def is_pod_ready() -> bool:
    """LB readiness: not draining AND not at capacity."""
    return not _draining and not is_pod_at_capacity()


def retry_after_seconds_for_capacity() -> int:
    """Suggested Retry-After for a 503 caused by capacity (not drain).

    Computed from the typical voice-call duration: most customer calls finish
    in 60-180 seconds, so a 30s retry budget keeps the LB cycling without
    hammering the pod.
    """
    return int(os.getenv("CAPACITY_RETRY_AFTER_S", "30"))


def snapshot() -> dict:
    """Diagnostic snapshot for the readiness endpoint."""
    active = _active_count_provider() if _active_count_provider else None
    cap = _max_capacity_provider() if _max_capacity_provider else None
    return {
        "ready": is_pod_ready(),
        "draining": _draining,
        "drain_elapsed_s": round(drain_seconds_elapsed(), 1),
        "drain_timeout_s": DRAIN_TIMEOUT_S,
        "active_sessions": active,
        "max_sessions": cap,
        "at_capacity": is_pod_at_capacity(),
    }
