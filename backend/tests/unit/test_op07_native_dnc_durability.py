"""Actual native event pump/shared purge; synthetic DB ports, no provider calls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.dialer import opt_out
from app.realtime.openai import RealtimeEvent
from tests.unit.test_realtime_end_call_ownership import _events, _fixture

TENANT = "20000000-0000-4000-8000-000000000017"
LEAD = "30000000-0000-4000-8000-000000000017"
CALL = "10000000-0000-4000-8000-000000000017"


class CleanupConnection:
    def __init__(self):
        self.queries = []
        self.jobs_error = False
        self.lead_error = False
        self.lead_exists = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def transaction(self):
        return self

    async def execute(self, sql, *args):
        self.queries.append((sql, args))
        if "UPDATE dialer_jobs" in sql:
            if self.jobs_error:
                raise RuntimeError("synthetic jobs unavailable")
            return "UPDATE 0"
        return "SET"

    async def fetchrow(self, sql, *args):
        self.queries.append((sql, args))
        if self.lead_error:
            raise RuntimeError("synthetic lead unavailable")
        return {"id": LEAD} if self.lead_exists else None


@pytest.fixture
def ports(monkeypatch):
    conn = CleanupConnection()
    pool = SimpleNamespace(acquire=lambda: conn)
    write = AsyncMock(return_value=SimpleNamespace(id="synthetic-dnc"))
    monkeypatch.setattr(opt_out.DNCService, "add_caller_opt_out", write)
    voice = SimpleNamespace(_dialer_tenant_id=TENANT, _dialer_lead_id=LEAD,
                            _dialer_phone="+15555550107", _dialer_call_id=CALL)
    monkeypatch.setattr("app.domain.services.telephony.lifecycle._state",
                        lambda: SimpleNamespace(get_voice_session=lambda _: voice))
    # Calling the sync adapter is a regression: it blocks the audio event loop.
    client = SimpleNamespace(table=lambda _: pytest.fail("sync cleanup adapter used"))
    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(
        is_initialized=True, db_pool=pool, db_client=client))
    return SimpleNamespace(conn=conn, pool=pool, write=write, voice=voice, client=client)


def native(ports):
    bridge, provider, gateway, ended, session = _fixture()
    session.call_id = "synthetic-close"
    ports.voice.call_session = session
    return bridge, provider, ended, session


async def final(bridge, provider, text):
    await _events(bridge, provider, RealtimeEvent(
        kind="caller_transcript", is_final=True, text=text))


async def drain_opt_out(bridge):
    task = getattr(bridge, "_opt_out_task", None)
    assert task is not None, "native final must start durable DNC before teardown"
    await asyncio.wait_for(asyncio.shield(task), 1)


@pytest.mark.asyncio
async def test_native_continued_question_persists_dnc_without_hangup(ports):
    bridge, provider, ended, session = native(ports)
    try:
        await final(bridge, provider, "Do not call me again, but first I need help with my account.")
        await drain_opt_out(bridge)
        ports.write.assert_awaited_once()
        assert session._caller_opted_out is True
        assert ports.voice._opt_out_purged is True
        assert bridge._termination_task is None
        ended.assert_not_awaited()
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_native_opt_out_does_not_block_pump_and_coalesces_duplicates(ports):
    started, release = asyncio.Event(), asyncio.Event()

    async def held_write(**kwargs):
        started.set()
        await release.wait()

    ports.write.side_effect = held_write
    bridge, provider, ended, _ = native(ports)
    try:
        await final(bridge, provider, "Stop calling me, but I need help first.")
        await asyncio.wait_for(started.wait(), .5)
        original = bridge._opt_out_task
        await final(bridge, provider, "Stop calling me, but I need help first.")
        await final(bridge, provider, "How does this work?")
        assert bridge._latest_caller_text == "How does this work?"
        assert bridge._opt_out_task is original
        assert ports.write.await_count == 1
        ended.assert_not_awaited()
        release.set()
        await drain_opt_out(bridge)
    finally:
        release.set()
        await bridge.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Don't stop calling me.", "What does 'do not call me again' mean?",
                                  "My colleague said 'stop calling me'.", "No thanks to email."])
async def test_non_opt_out_never_starts_durable_write(ports, text):
    bridge, provider, _, _ = native(ports)
    try:
        await final(bridge, provider, text)
        await asyncio.sleep(0)
        ports.write.assert_not_awaited()
        assert getattr(bridge, "_opt_out_task", None) is None
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_native_close_waits_for_started_dnc_and_rechecks_new_caller(ports):
    started, release = asyncio.Event(), asyncio.Event()

    async def held_write(**kwargs):
        started.set()
        await release.wait()

    ports.write.side_effect = held_write
    bridge, provider, ended, session = native(ports)
    try:
        await final(bridge, provider, "Do not call me again. Goodbye.")
        await asyncio.wait_for(started.wait(), .5)
        closing = bridge._termination_task
        bridge._goodbye_completed.set()
        await asyncio.sleep(0)
        ended.assert_not_awaited()
        await final(bridge, provider, "Wait, I need help first.")
        release.set()
        await drain_opt_out(bridge)
        await asyncio.gather(closing, return_exceptions=True)
        ended.assert_not_awaited()
        assert session._caller_opted_out
        assert ports.voice._opt_out_purged
    finally:
        release.set()
        await bridge.stop()


@pytest.mark.asyncio
async def test_native_stop_drains_opt_out_instead_of_canceling_success(ports):
    started, release = asyncio.Event(), asyncio.Event()

    async def held_write(**kwargs):
        started.set()
        await release.wait()

    ports.write.side_effect = held_write
    bridge, provider, _, _ = native(ports)
    await final(bridge, provider, "Stop calling me, but I need help first.")
    await asyncio.wait_for(started.wait(), .5)
    stopping = asyncio.create_task(bridge.stop())
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await asyncio.wait_for(stopping, 1)
    assert ports.voice._opt_out_purged


@pytest.mark.asyncio
async def test_native_stop_cancels_exhausted_write_without_acknowledgement(ports):
    started = asyncio.Event()

    async def held_write(**kwargs):
        started.set()
        await asyncio.Event().wait()

    ports.write.side_effect = held_write
    bridge, provider, _, _ = native(ports)
    await final(bridge, provider, "Stop calling me, but I need help first.")
    await asyncio.wait_for(started.wait(), .5)
    task = bridge._opt_out_task
    await asyncio.wait_for(bridge.stop(), 3)
    assert task.cancelled()
    assert not getattr(ports.voice, "_opt_out_dnc_written", False)
    assert not getattr(ports.voice, "_opt_out_purged", False)


@pytest.mark.asyncio
async def test_nonfinal_transcription_does_not_create_dnc(ports):
    bridge, provider, _, session = native(ports)
    try:
        await _events(bridge, provider, RealtimeEvent(kind="caller_transcript",
            text="Stop calling me.", is_final=False))
        await asyncio.sleep(0)
        ports.write.assert_not_awaited()
        assert not getattr(session, "_caller_opted_out", False)
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_native_genuine_goodbye_closes_after_durable_attempt(ports):
    bridge, provider, ended, _ = native(ports)

    async def close():
        assert ports.voice._opt_out_purged is True

    ended.side_effect = close
    await _events(bridge, provider,
        RealtimeEvent(kind="caller_transcript", text="Do not call me again. Goodbye.", is_final=True),
        RealtimeEvent(kind="response_candidate", text="Goodbye.", audio=b"\xff" * 320))
    try:
        await asyncio.wait_for(bridge._termination_task, 1)
        ended.assert_awaited_once()
        ports.write.assert_awaited_once()
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_expired_caller_order_does_not_discard_monotonic_dnc(ports, monkeypatch):
    bridge, provider, ended, session = native(ports)
    monkeypatch.setattr(bridge, "_admit_caller_final", lambda *args: None)
    try:
        await final(bridge, provider, "Please stop calling me.")
        await drain_opt_out(bridge)
        assert session._caller_opted_out is True
        assert ports.voice._opt_out_purged is True
        assert bridge._termination_task is None  # Old item cannot authorize close.
        ended.assert_not_awaited()
    finally:
        await bridge.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_field", ["jobs_error", "lead_error", "lead_exists"])
async def test_partial_cleanup_remains_retryable_but_dnc_ack_is_distinct(ports, failed_field):
    setattr(ports.conn, failed_field, failed_field != "lead_exists")
    session = SimpleNamespace(call_id="synthetic-close")
    assert await opt_out.purge_opt_out_before_farewell(session) is True
    assert not getattr(ports.voice, "_opt_out_purged", False)
    before = len(ports.conn.queries)
    setattr(ports.conn, failed_field, failed_field == "lead_exists")
    assert await opt_out.purge_opt_out_before_farewell(session) is True
    assert len(ports.conn.queries) > before
    assert ports.voice._opt_out_purged is True
    writes = ports.write.await_count
    before = len(ports.conn.queries)
    assert await opt_out.purge_opt_out_before_farewell(session) is True
    assert ports.write.await_count == writes and len(ports.conn.queries) == before


@pytest.mark.asyncio
async def test_cleanup_is_tenant_scoped_and_never_releases_live_job(ports):
    result = await opt_out.purge_lead_on_opt_out(db_pool=ports.pool, db_client=ports.client,
        tenant_id=TENANT, lead_id=LEAD, phone_number="+15555550107", call_id=CALL)
    assert result["purge_complete"] is True
    assert result["jobs_cleanup_complete"] is True  # UPDATE 0 is a valid empty queue.
    jobs = next((sql, args) for sql, args in ports.conn.queries if "UPDATE dialer_jobs" in sql)
    assert "tenant_id" in jobs[0] and "lead_id" in jobs[0]
    assert TENANT in jobs[1] and LEAD in jobs[1]
    statuses = next(value for value in jobs[1] if isinstance(value, list))
    assert set(statuses) == {"pending", "queued", "retry_scheduled"}
    lead = next((sql, args) for sql, args in ports.conn.queries if "UPDATE leads" in sql)
    assert "tenant_id" in lead[0] and "RETURNING" in lead[0]
    assert TENANT in lead[1] and LEAD in lead[1]


@pytest.mark.asyncio
async def test_no_lead_inbound_dnc_requires_no_lead_cleanup(ports):
    ports.voice._dialer_lead_id = None
    assert await opt_out.purge_opt_out_before_farewell(SimpleNamespace(call_id="synthetic-close"))
    assert ports.voice._opt_out_purged
    assert ports.conn.queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "cancel", "error"])
async def test_no_acknowledgement_on_failed_or_cancelled_dnc(ports, failure):
    started = asyncio.Event()

    async def fail(**kwargs):
        started.set()
        if failure == "error":
            raise RuntimeError("synthetic DB unavailable")
        await asyncio.Event().wait()

    ports.write.side_effect = fail
    task = asyncio.create_task(opt_out.purge_opt_out_before_farewell(
        SimpleNamespace(call_id="synthetic-close"), timeout_s=.01 if failure == "timeout" else 1))
    if failure == "cancel":
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await task is False
    assert not getattr(ports.voice, "_opt_out_purged", False)
    assert not getattr(ports.voice, "_opt_out_dnc_written", False)


@pytest.mark.asyncio
async def test_missing_tenant_or_phone_does_not_write(ports):
    ports.voice._dialer_tenant_id = None
    assert not await opt_out.purge_opt_out_before_farewell(SimpleNamespace(call_id="synthetic-close"))
    ports.write.assert_not_awaited()
    assert ports.conn.queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_recovers", [True, False])
async def test_actual_finalizer_retries_partial_cleanup_and_records_truth(ports, monkeypatch, cleanup_recovers):
    from app.domain.services.telephony import lifecycle
    voice = ports.voice
    voice._opt_out_dnc_written = True
    voice._caller_opted_out = True
    voice._dialer_call_id = None
    voice.pipeline = SimpleNamespace(cancel_active_turn=AsyncMock())
    voice.call_session = SimpleNamespace(call_id="synthetic-close")
    ports.conn.lead_error = not cleanup_recovers
    ended = AsyncMock()
    state = SimpleNamespace(clear_first_speaker=lambda _: None,
        clear_ringing_started_at=lambda _: None, pop_ringing_warmup=lambda _: None,
        pop_voice_session=lambda _: voice, remove_gateway_sessions_for_call=lambda _: None)
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: SimpleNamespace())
    monkeypatch.setattr(lifecycle, "_save_call_recording", AsyncMock())
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda *args, **kwargs: None)
    monkeypatch.setattr(lifecycle, "_get_orchestrator", lambda: SimpleNamespace(end_session=ended))
    monkeypatch.setattr("app.domain.services.global_concurrency.release_lease", AsyncMock())
    monkeypatch.setattr("app.domain.services.call_status.record_call_state_by_provider_id", AsyncMock())
    call_id = f"synthetic-dnc-finalizer-{cleanup_recovers}"
    try:
        await lifecycle._on_call_ended(call_id, terminal_at_monotonic=100, acknowledge_ledger=False)
        ports.write.assert_awaited_once()
        assert bool(getattr(voice, "_opt_out_purged", False)) is cleanup_recovers
        assert voice._opt_out_dnc_written is True
        ended.assert_awaited_once()
    finally:
        lifecycle._ended_calls_in_flight.discard(call_id)
