"""Buffered audit entries are written before the database pool closes.

Bug hunt 2026-10-10 (calls-recordings-analytics-4 follow-up): AuditLogger now
buffers entries and flushes them on a timer, and gained aclose() as the
shutdown hook, but nothing called it, so a clean shutdown dropped up to one
flush interval of audit entries.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app import main
from app.api.v1 import dependencies


async def test_the_shutdown_hook_closes_the_audit_logger(monkeypatch):
    service = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(dependencies, "_audit_logger_service", service)
    await main._flush_audit_logger()
    service.aclose.assert_awaited_once()


async def test_the_shutdown_hook_is_a_no_op_without_an_audit_logger(monkeypatch):
    monkeypatch.setattr(dependencies, "_audit_logger_service", None)
    await main._flush_audit_logger()


def test_lifespan_flushes_audit_entries_before_the_pool_closes():
    source = inspect.getsource(main.lifespan)
    assert "_flush_audit_logger()" in source
    assert source.index("_flush_audit_logger()") < source.index("container.shutdown()")
