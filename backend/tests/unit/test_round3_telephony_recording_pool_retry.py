"""Upstream telephony acquisition failures before the actual recording save."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.db_utils import acquire_with_tenant
from app.domain.services import recording_service as recording
from app.domain.services.telephony import recording as telephony
from tests.unit.test_round3_recording_pool_retry import (
    CALL, CAMPAIGN, TENANT, fixture as service_fixture,  # noqa: F401
)

PBX = "synthetic-pbx-recording-retry"


@pytest.fixture
def fixture(service_fixture, monkeypatch):
    fx = service_fixture
    fx.lookup_rows = True
    fx.lookup_queries = []
    fx.lookup_error = None
    fx.stub_error = None
    fx.stub_inserts = []
    fx.foreign_campaign = False
    fx.clears = []
    original_fetchrow, original_execute = fx.conn.fetchrow, fx.conn.execute

    async def fetchrow(sql, *args):
        query = " ".join(sql.split())
        if query == (
            "SELECT id, tenant_id, campaign_id FROM calls WHERE external_call_uuid = $1 "
            "AND tenant_id = $2 ORDER BY created_at DESC LIMIT 1"
        ):
            assert args == (PBX, UUID(TENANT))
            fx.lookup_queries.append(args)
            if fx.lookup_error is not None:
                raise fx.lookup_error
            return {"id": UUID(CALL), "tenant_id": UUID(TENANT), "campaign_id": UUID(CAMPAIGN)} if fx.lookup_rows else None
        if query == "SELECT id FROM campaigns WHERE id = $1 AND tenant_id = $2":
            assert args == (UUID(CAMPAIGN), UUID(TENANT))
            return None if fx.foreign_campaign else {"id": UUID(CAMPAIGN)}
        return await original_fetchrow(sql, *args)

    async def execute(sql, *args):
        query = " ".join(sql.split())
        if query.startswith("INSERT INTO calls (") and query.endswith("ON CONFLICT (id) DO NOTHING"):
            assert args[:3] == (UUID(CALL), UUID(TENANT), UUID(CAMPAIGN))
            fx.stub_inserts.append(args)
            if fx.stub_error is not None:
                raise fx.stub_error
            return "INSERT 0 1"
        return await original_execute(sql, *args)

    @asynccontextmanager
    async def scoped_lookup():
        fx.pool.attempt_timeout = 10.0
        async with acquire_with_tenant(fx.pool, TENANT) as conn:
            yield conn

    fx.conn.fetchrow, fx.conn.execute = fetchrow, execute
    monkeypatch.setattr("app.core.db.get_db", scoped_lookup)
    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(
        is_initialized=True, db_pool=fx.pool, db_client=SimpleNamespace(pool=fx.pool),
    ))
    monkeypatch.setattr(recording, "RecordingService", lambda _: fx.service)
    gateway = SimpleNamespace(
        get_recording_buffer=lambda _: [b"\x01\x00" * 9600],
        get_tts_recording_buffer=lambda _: [],
        clear_recording_buffer=lambda call_id: fx.clears.append((call_id, fx.clock.now)),
        _sample_rate=16000,
    )
    fx.voice = SimpleNamespace(
        call_id=CALL, media_gateway=gateway, _dialer_tenant_id=TENANT,
        config=SimpleNamespace(tenant_id=TENANT, campaign_id=CAMPAIGN),
    )
    return fx


@pytest.mark.asyncio
@pytest.mark.parametrize("failures", [1, 2])
async def test_c4_upstream_timeout_recovers_original_call_and_saves_and_links(fixture, failures):
    fixture.pool.fail_at = set(range(1, failures + 1))
    await telephony._save_call_recording(fixture.voice, PBX)
    assert len(fixture.clock.sleeps) == failures
    assert fixture.lookup_queries == [(PBX, UUID(TENANT))]
    assert fixture.stub_inserts == []  # a timeout is not proof of a missing calls row
    assert len(fixture.uploads) == len(fixture.conn.inserts) == len(fixture.conn.links) == 1
    assert fixture.clears == [(CALL, fixture.clock.now)]


@pytest.mark.asyncio
async def test_c4_stub_acquisition_timeout_retries_without_repeating_stub_write(fixture):
    fixture.lookup_rows = False
    fixture.pool.fail_at = {2}
    await telephony._save_call_recording(fixture.voice, PBX)
    assert len(fixture.clock.sleeps) == 1
    assert len(fixture.stub_inserts) == 1
    assert len(fixture.uploads) == len(fixture.conn.inserts) == len(fixture.conn.links) == 1


@pytest.mark.asyncio
async def test_c4_upstream_and_service_share_the_same_minute_budget(fixture, caplog):
    fixture.pool.fail_at = {1, 2, 3, 9, 10, 11}
    await telephony._save_call_recording(fixture.voice, PBX)
    assert 50 <= fixture.clock.now <= 60.01
    assert len(fixture.uploads) == 1
    assert fixture.conn.inserts == fixture.conn.links == []
    assert fixture.deletes == []
    assert "recording_db_pool_retry_exhausted" in caplog.text


@pytest.mark.asyncio
async def test_c4_upstream_exhaustion_waits_with_backoff_then_logs_and_discards(fixture, caplog):
    fixture.pool.fail_from = 1
    await telephony._save_call_recording(fixture.voice, PBX)
    assert 50 <= fixture.clock.now <= 60.01
    assert fixture.clock.sleeps
    assert fixture.uploads == fixture.conn.inserts == fixture.stub_inserts == []
    assert fixture.clears == [(CALL, fixture.clock.now)]
    assert any(item.levelname == "ERROR" and PBX in item.message for item in caplog.records)


@pytest.mark.asyncio
async def test_c4_upstream_query_timeout_keeps_existing_fallback_without_retry(fixture):
    fixture.lookup_error = TimeoutError("Synthetic lookup query timeout")
    await telephony._save_call_recording(fixture.voice, PBX)
    assert len(fixture.lookup_queries) == len(fixture.stub_inserts) == 1
    assert fixture.clock.sleeps == []
    assert len(fixture.uploads) == len(fixture.conn.inserts) == len(fixture.conn.links) == 1


@pytest.mark.asyncio
async def test_c4_ambiguous_stub_write_is_never_repeated(fixture):
    fixture.lookup_rows = False
    fixture.stub_error = OSError("Synthetic uncertain stub write")
    await telephony._save_call_recording(fixture.voice, PBX)
    assert len(fixture.stub_inserts) == 1
    assert fixture.clock.sleeps == []
    assert fixture.uploads == fixture.conn.inserts == []
    assert fixture.clears == [(CALL, 0)]


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["unknown-tenant", "conflicting-tenant", "foreign-campaign"])
async def test_c4_upstream_retry_does_not_weaken_ownership_gates(fixture, gate):
    fixture.lookup_rows = False
    if gate == "unknown-tenant":
        fixture.voice._dialer_tenant_id = None
        fixture.voice.config.tenant_id = None
    elif gate == "conflicting-tenant":
        fixture.voice.config.tenant_id = "20000000-0000-4000-8000-000000000099"
    else:
        fixture.foreign_campaign = True
    await telephony._save_call_recording(fixture.voice, PBX)
    assert fixture.stub_inserts == fixture.uploads == fixture.conn.inserts == []
    assert fixture.clears == [(CALL, 0)]


@pytest.mark.asyncio
async def test_c4_inbound_revocation_after_retry_still_prevents_storage(fixture, monkeypatch):
    gates = []

    async def allowed(voice, pool):
        gates.append((voice, pool))
        return len(gates) == 1

    monkeypatch.setattr(telephony, "_inbound_recording_persist_allowed", allowed)
    fixture.pool.fail_at = {1, 2}
    await telephony._save_call_recording(fixture.voice, PBX)
    assert len(fixture.clock.sleeps) == 2
    assert len(gates) == 2
    assert fixture.uploads == fixture.conn.inserts == []

