"""Synthetic PBX identity fixture; never contacts a telephony provider."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

INFO = {
    "system": {"entity_id": "synthetic-pbx-A"},
    "status": {"startup_time": "2026-10-21T00:00:00+00:00"},
}
BINDING = "pbx1:" + "a" * 64


def bind_test_adapter(adapter):
    """Supply an explicitly original backend to an existing adapter stub."""
    if hasattr(type(adapter), "control_backend_identity"):
        adapter._control_backend_system = INFO["system"]["entity_id"]
        adapter._control_backend_startup = INFO["status"]["startup_time"]
        previous = adapter._ari

        async def ari(method, path, **kwargs):
            if path == "/asterisk/info":
                return INFO
            return await previous(method, path, **kwargs)

        adapter._ari = AsyncMock(side_effect=ari)
    else:
        adapter.control_backend_identity = BINDING

        @asynccontextmanager
        async def guard(expected):
            assert expected == adapter.control_backend_identity
            yield

        adapter.control_backend_guard = guard
    return adapter.control_backend_identity


def bound_test_adapter(adapter):
    if not hasattr(adapter, "name"):
        adapter.name = "asterisk"
    bind_test_adapter(adapter)
    return adapter
