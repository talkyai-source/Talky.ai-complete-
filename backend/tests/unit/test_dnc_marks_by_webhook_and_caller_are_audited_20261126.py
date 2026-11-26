"""QA F12-10 gap: the web hook's spam mark and the caller's own opt-out left
no audit row. The mark is written first, exactly as before; its audit row
follows in its own transaction (``dnc_system_mark_audit``) and can never delay
or undo the mark."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.domain.services import dnc_service
from app.domain.services import dnc_system_mark_audit as sma
from app.domain.services.call_service import CallService
from app.domain.services.dialer import opt_out

TENANT = "11111111-1111-4111-8111-111111111111"
ENTRY = "22222222-2222-4222-8222-222222222222"
_ROW = {
    "id": ENTRY, "tenant_id": TENANT, "normalized_number": "+442079460123",
    "source": "caller_opt_out", "reason": "Caller opt-out", "expires_at": None,
    "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
}


@pytest.mark.asyncio
async def test_signed_in_add_still_records_a_user_actor(monkeypatch):
    class _Conn:
        async def fetchrow(self, query, *args):
            return _ROW

    @asynccontextmanager
    async def scoped(pool, tenant_id, **kwargs):
        yield _Conn()

    async def country(conn, tenant_id, default):
        return "US"

    monkeypatch.setattr(dnc_service, "acquire_with_tenant", scoped)
    monkeypatch.setattr(dnc_service, "_write_country", country)
    audit = SimpleNamespace(lock_transaction=AsyncMock(), log_in_transaction=AsyncMock())
    await dnc_service.DNCService(object()).add(
        tenant_id=TENANT, e164="+442079460123", source="manual",
        added_by=ENTRY, audit=audit, actor_role="tenant_admin",
    )
    assert audit.log_in_transaction.await_args.kwargs["actor_type"] == "user"


# -- call sites: mark first, audit scheduled afterwards ----------------------
class _Client:
    def __init__(self):
        self.pool = object()

    def table(self, name):
        return self

    def __getattr__(self, name):
        return lambda *a, **k: self


def _recorder(monkeypatch, module, *, raises=False):
    calls = []

    def schedule(pool, **kw):
        calls.append(kw)
        if raises:
            raise RuntimeError("scheduler broke")

    monkeypatch.setattr(module, "audit_system_mark_soon", schedule)
    return calls


@pytest.mark.asyncio
async def test_mark_spam_hook_writes_plain_and_schedules_one_webhook_audit(monkeypatch):
    seen = []

    class FakeDNC:
        def __init__(self, pool):
            pass

        async def add(self, **kw):
            seen.append(kw)
            return SimpleNamespace(id=ENTRY)

    monkeypatch.setattr("app.domain.services.dnc_service.DNCService", FakeDNC)
    calls = _recorder(monkeypatch, sma)
    await CallService(db_client=_Client())._add_spam_mark_to_dnc_list(TENANT, "+442046132300", "spam")
    assert len(seen) == 1 and "audit" not in seen[0] and "actor_type" not in seen[0]
    assert len(calls) == 1
    assert calls[0]["actor_role"] == "webhook" and calls[0]["entry_id"] == ENTRY
    assert calls[0]["tenant_id"] == TENANT


@pytest.mark.asyncio
async def test_a_raising_scheduler_never_makes_the_hook_raise(monkeypatch):
    class FakeDNC:
        def __init__(self, pool):
            pass

        async def add(self, **kw):
            return SimpleNamespace(id=ENTRY)

    monkeypatch.setattr("app.domain.services.dnc_service.DNCService", FakeDNC)
    _recorder(monkeypatch, sma, raises=True)
    await CallService(db_client=_Client())._add_spam_mark_to_dnc_list(TENANT, "+442046132300", "spam")


async def _purge():
    return await opt_out.purge_lead_on_opt_out(
        db_pool=object(), db_client=None, tenant_id=TENANT, lead_id=None,
        phone_number="+442079460123", call_id="c1",
    )


@pytest.mark.asyncio
async def test_in_call_opt_out_writes_plain_and_schedules_one_caller_audit(monkeypatch):
    write = AsyncMock(return_value=SimpleNamespace(id=ENTRY))
    monkeypatch.setattr(opt_out.DNCService, "add_caller_opt_out", write)
    calls = _recorder(monkeypatch, opt_out)
    result = await _purge()
    assert result["dnc_added"] is True and write.await_count == 1
    assert "audit" not in write.await_args.kwargs
    assert len(calls) == 1
    assert calls[0]["actor_role"] == "caller_opt_out" and calls[0]["entry_id"] == ENTRY
    assert calls[0]["tenant_id"] == TENANT


@pytest.mark.asyncio
async def test_a_raising_scheduler_never_changes_dnc_added(monkeypatch):
    monkeypatch.setattr(opt_out.DNCService, "add_caller_opt_out",
                        AsyncMock(return_value=SimpleNamespace(id=ENTRY)))
    _recorder(monkeypatch, opt_out, raises=True)
    assert (await _purge())["dnc_added"] is True


@pytest.mark.asyncio
async def test_an_opt_out_that_could_not_be_listed_is_an_error_without_the_number(monkeypatch, caplog):
    monkeypatch.setattr(opt_out.DNCService, "add_caller_opt_out",
                        AsyncMock(side_effect=ConnectionError("db down")))
    calls = _recorder(monkeypatch, opt_out)
    with caplog.at_level(logging.DEBUG):
        result = await _purge()
    assert result["dnc_added"] is False and calls == []   # nothing to audit
    lines = [r for r in caplog.records if "DNC add failed" in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.ERROR
    assert "442079460123" not in caplog.text and "db down" not in caplog.text
    assert "***0123" in lines[0].getMessage() and "ConnectionError" in lines[0].getMessage()


# -- the retrying coroutine ---------------------------------------------------
@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(sma, "RETRY_WAITS_S", (0.01, 0.01, 0.01, 0.01))


def _mine():
    # Only tasks of this loop: finished tasks of other loops may linger in the set.
    loop = asyncio.get_running_loop()
    return [t for t in sma._pending if t.get_loop() is loop]


def _missing(caplog):
    return [r for r in caplog.records if "dnc_mark_audit_missing" in r.getMessage()]


async def _run():
    return await sma.write_mark_audit_with_retries(
        object(), tenant_id=TENANT, entry_id=ENTRY, actor_role="webhook")


@pytest.mark.asyncio
async def test_first_try_success_is_one_call_and_no_error(monkeypatch, caplog, fast):
    write = AsyncMock(return_value=True)
    monkeypatch.setattr(sma, "write_mark_audit", write)
    with caplog.at_level(logging.ERROR):
        assert await _run() is True
    assert write.await_count == 1 and not _missing(caplog)


@pytest.mark.asyncio
async def test_busy_lock_twice_then_success_is_three_calls_no_error(monkeypatch, caplog, fast):
    write = AsyncMock(side_effect=[asyncpg.LockNotAvailableError("busy"),
                                   asyncpg.LockNotAvailableError("busy"), True])
    monkeypatch.setattr(sma, "write_mark_audit", write)
    with caplog.at_level(logging.ERROR):
        assert await _run() is True
    assert write.await_count == 3 and not _missing(caplog)


@pytest.mark.asyncio
async def test_always_failing_is_five_calls_and_one_error_with_the_type_only(monkeypatch, caplog, fast):
    write = AsyncMock(side_effect=asyncpg.LockNotAvailableError("secret text"))
    monkeypatch.setattr(sma, "write_mark_audit", write)
    with caplog.at_level(logging.ERROR):
        assert await _run() is False
    assert write.await_count == 5
    (line,) = _missing(caplog)
    text = line.getMessage()
    assert "LockNotAvailableError" in text and "attempts=5" in text
    assert TENANT in text and ENTRY in text and "secret text" not in text


@pytest.mark.asyncio
async def test_a_non_retryable_error_is_one_call_and_the_error(monkeypatch, caplog, fast):
    write = AsyncMock(side_effect=TypeError("bug"))
    monkeypatch.setattr(sma, "write_mark_audit", write)
    with caplog.at_level(logging.ERROR):
        assert await _run() is False
    assert write.await_count == 1
    (line,) = _missing(caplog)
    assert "TypeError" in line.getMessage() and "attempts=1" in line.getMessage()


@pytest.mark.asyncio
async def test_cancellation_logs_the_error_and_propagates(monkeypatch, caplog):
    monkeypatch.setattr(sma, "RETRY_WAITS_S", (30.0, 30.0, 30.0, 30.0))
    monkeypatch.setattr(sma, "write_mark_audit",
                        AsyncMock(side_effect=asyncpg.LockNotAvailableError("busy")))
    with caplog.at_level(logging.ERROR):
        task = asyncio.ensure_future(_run())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    (line,) = _missing(caplog)
    assert "CancelledError" in line.getMessage()


@pytest.mark.asyncio
async def test_scheduler_keeps_the_task_until_done_then_forgets_it(monkeypatch):
    monkeypatch.setattr(sma, "write_mark_audit", AsyncMock(return_value=True))
    task = sma.audit_system_mark_soon(object(), tenant_id=TENANT, entry_id=ENTRY, actor_role="webhook")
    assert task in sma._pending
    assert await task is True
    await asyncio.sleep(0)
    assert not _mine()


@pytest.mark.asyncio
async def test_scheduler_without_an_entry_id_returns_none_and_does_not_raise():
    assert sma.audit_system_mark_soon(object(), tenant_id=TENANT, entry_id=None, actor_role="x") is None


@pytest.mark.asyncio
async def test_chain_busy_four_times_then_success_is_five_calls_no_error(monkeypatch, caplog, fast):
    write = AsyncMock(side_effect=[sma.ChainBusy()] * 4 + [True])
    monkeypatch.setattr(sma, "write_mark_audit", write)
    with caplog.at_level(logging.ERROR):
        assert await _run() is True
    assert write.await_count == 5 and not _missing(caplog)


@pytest.mark.asyncio
async def test_a_missing_permission_is_one_call_the_error_and_nothing_pending(monkeypatch, caplog, fast):
    write = AsyncMock(side_effect=asyncpg.InsufficientPrivilegeError("no rights"))
    monkeypatch.setattr(sma, "write_mark_audit", write)
    with caplog.at_level(logging.ERROR):
        task = sma.audit_system_mark_soon(object(), tenant_id=TENANT, entry_id=ENTRY, actor_role="webhook")
        assert await task is False
    await asyncio.sleep(0)
    assert write.await_count == 1 and not _mine()
    (line,) = _missing(caplog)
    assert "InsufficientPrivilegeError" in line.getMessage() and "attempts=1" in line.getMessage()
    assert "no rights" not in line.getMessage()


@pytest.mark.asyncio
async def test_a_busy_try_lock_gives_the_connection_back_before_the_wait(monkeypatch):
    in_use = {"now": 0, "at_wait": []}

    class _Conn:
        async def fetchval(self, query, *args):
            if "pg_advisory_xact_lock" in query:
                raise asyncpg.LockNotAvailableError("lock timeout")  # chain lock held elsewhere

    @asynccontextmanager
    async def scoped(pool, tenant_id, **kwargs):
        in_use["now"] += 1
        try:
            yield _Conn()
        finally:
            in_use["now"] -= 1

    real_sleep = asyncio.sleep

    async def sleep(seconds):
        in_use["at_wait"].append(in_use["now"])
        await real_sleep(0)

    monkeypatch.setattr(sma, "acquire_with_tenant", scoped)
    monkeypatch.setattr(sma.asyncio, "sleep", sleep)
    monkeypatch.setattr(sma, "RETRY_WAITS_S", (0.0, 0.0, 0.0, 0.0))
    assert await _run() is False
    assert in_use["at_wait"] == [0, 0, 0, 0]  # nothing held during any of the 4 waits


@pytest.mark.parametrize("error", [asyncpg.AdminShutdownError("restart"), asyncpg.InterfaceError("dead connection")])
@pytest.mark.asyncio
async def test_a_database_restart_or_dead_connection_is_retried(monkeypatch, caplog, fast, error):
    write = AsyncMock(side_effect=[error, True])
    monkeypatch.setattr(sma, "write_mark_audit", write)
    with caplog.at_level(logging.ERROR):
        assert await _run() is True
    assert write.await_count == 2 and not _missing(caplog)


@pytest.mark.asyncio
async def test_waits_are_the_constants_times_the_jitter_factor(monkeypatch):
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(sma.asyncio, "sleep", sleep)
    monkeypatch.setattr(sma.random, "uniform", lambda lo, hi: 1.0)
    monkeypatch.setattr(sma, "write_mark_audit", AsyncMock(side_effect=sma.ChainBusy()))
    assert await _run() is False
    assert slept == [2.0, 5.0, 10.0, 20.0]
    slept.clear()
    monkeypatch.setattr(sma.random, "uniform", lambda lo, hi: hi)
    await _run()
    assert slept == [2.0 * 1.3, 5.0 * 1.3, 10.0 * 1.3, 20.0 * 1.3]
    assert sma.JITTER == (0.7, 1.3)
