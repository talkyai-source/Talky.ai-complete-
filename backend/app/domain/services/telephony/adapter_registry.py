"""Domain-owned registry for the live PBX call-control adapter.

``lifecycle.py`` (call-lifecycle orchestration) needs to reach the single
process-wide ``CallControlAdapter`` connection (ARI/ESL) that the endpoint
module ``telephony_bridge.py`` owns and mutates: it is created in
``start_telephony``/disconnected in ``stop_telephony``, and ``app/main.py``
also writes it directly (``_tb._adapter = ...``) during the boot-time
auto-connect.

The adapter is deliberately NOT modeled by
``app.domain.services.telephony.state_backend`` — it's a single live
connection object, not per-call state, and its owner is the API layer.
Previously the domain layer reached it by lazily importing
``app.api.v1.endpoints.telephony_bridge`` (the ``_bridge()`` helper in
``lifecycle.py``), which is a domain→API dependency inversion. This module
fixes that: the API layer registers a zero-arg getter here once, at import
time, and the domain layer calls :func:`get_adapter` — the dependency now
points the correct direction (API → domain), never the reverse.

The getter is a closure over the API module's ``_adapter`` global (not a
captured value) so it always reflects the current connection even though
``_adapter`` is reassigned in place by ``app/main.py`` and the
``/telephony/start`` and ``/telephony/stop`` REST handlers.
"""
from __future__ import annotations

from typing import Callable, Optional

from app.domain.interfaces.call_control_adapter import CallControlAdapter

_adapter_getter: Optional[Callable[[], Optional[CallControlAdapter]]] = None


def register_adapter_getter(
    getter: Callable[[], Optional[CallControlAdapter]],
) -> None:
    """Called once by ``telephony_bridge.py`` at import time to hand the
    domain layer a live view of the adapter it owns.

    Idempotent — safe to call again (e.g. module re-import under a test
    reloader); the latest registration wins.
    """
    global _adapter_getter
    _adapter_getter = getter


def get_adapter() -> Optional[CallControlAdapter]:
    """The current live PBX adapter, or ``None`` when telephony hasn't
    been started yet (or, in a test that imports this module in
    isolation, before the API layer has registered its getter)."""
    if _adapter_getter is None:
        return None
    return _adapter_getter()
