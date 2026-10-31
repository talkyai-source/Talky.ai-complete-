"""Explicit audit-port stub for tests whose scope is conversation behavior.

Runtime ownership/drain is real; required database receipts are covered by the
separate runtime receipt unit and restricted-PostgreSQL modules.
"""
from unittest.mock import AsyncMock

from app.core.security.assistant_runtime import AssistantRuntime


def stub_runtime_receipts(monkeypatch):
    runtimes = []

    async def register(pool, claims, *, endpoint_kind):
        runtime = AssistantRuntime(pool, {"endpoint_kind": endpoint_kind}, "synthetic-receipt")
        runtime.record_stopped = AsyncMock()
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(AssistantRuntime, "register", register)
    return runtimes
