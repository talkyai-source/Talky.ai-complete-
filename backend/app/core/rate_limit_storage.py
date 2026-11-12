"""Where the slowapi auth limiter keeps its counts (HA plan, phase 3).

In production the counts live in the Redis the app already requires (see
``container.redis_required``), so every API process and every APP server
shares one count per visitor. With per-process memory, two APP servers behind
the load balancer would each allow the full limit. Elsewhere the counts stay
in process memory, as before.

The Redis settings are the container's own (REDIS_MODE single, sentinel or
cluster), so the limiter follows the same Redis as the rest of the app.
"""
from __future__ import annotations

import os
from typing import Any, Optional

# slowapi calls Redis synchronously on the event loop: a hung Redis must fail
# fast into the limiter's in-memory fallback, not stall the API.
_SOCKET_TIMEOUT_S = 0.5


def limiter_storage() -> tuple[Optional[str], dict[str, Any]]:
    """``(storage_uri, storage_options)`` for slowapi; ``(None, {})`` = memory."""
    from app.core.container import _build_redis_url, _parse_address_list, redis_required

    if not redis_required():
        return None, {}
    options: dict[str, Any] = {
        "socket_timeout": _SOCKET_TIMEOUT_S,
        "socket_connect_timeout": _SOCKET_TIMEOUT_S,
    }
    mode = (os.getenv("REDIS_MODE") or "single").strip().lower()
    if mode not in {"sentinel", "cluster"}:
        return _build_redis_url(), options
    password = os.getenv("REDIS_PASSWORD") or None
    if password:
        options["password"] = password
    if mode == "sentinel":
        nodes = _parse_address_list(os.getenv("REDIS_SENTINEL_ADDRESSES", ""))
        service = os.getenv("REDIS_SENTINEL_SERVICE_NAME", "mymaster")
        options["sentinel_kwargs"] = {
            "password": os.getenv("REDIS_SENTINEL_PASSWORD") or None,
            "socket_timeout": _SOCKET_TIMEOUT_S,
        }
        hosts = ",".join(f"{host}:{port}" for host, port in nodes)
        return f"redis+sentinel://{hosts}/{service}", options
    nodes = _parse_address_list(os.getenv("REDIS_CLUSTER_NODES", ""))
    hosts = ",".join(f"{host}:{port}" for host, port in nodes)
    return f"redis+cluster://{hosts}", options
