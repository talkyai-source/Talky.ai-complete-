"""Focused coverage for the Admin call/media operational controls."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import telephony_bridge
from app.api.v1.endpoints.admin import calls, media
from app.domain.services import call_guard as call_guard_module
from app.domain.services.call_guard import CallGuard, GuardCheck, GuardDecision
from app.domain.services.platform_runtime_controls import (
    OutboundCallPause,
    get_outbound_call_pause,
    set_outbound_call_pause,
)


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


class _ControlConn:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self.row


@pytest.mark.asyncio
async def test_pause_control_round_trips_the_persisted_row():
    paused_at = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    actor_id = str(uuid4())
    row = {
        "outbound_calls_paused": True,
        "paused_at": paused_at,
        "paused_by": actor_id,
        "pause_reason": "Provider incident",
    }
    conn = _ControlConn(row)
    pool = _Pool(conn)

    read_state = await get_outbound_call_pause(pool)
    written_state = await set_outbound_call_pause(
        pool,
        paused=True,
        actor_id=actor_id,
        reason="  Provider incident  ",
    )

    assert read_state == OutboundCallPause(True, paused_at, actor_id, "Provider incident")
    assert written_state == read_state
    assert conn.calls[1][1] == (True, actor_id, "Provider incident")
    assert "ON CONFLICT (id) DO UPDATE" in conn.calls[1][0]


async def _none():
    return None


@pytest.mark.asyncio
async def test_call_guard_blocks_at_the_first_check_when_platform_is_paused(monkeypatch):
    async def paused(_pool):
        return OutboundCallPause(paused=True, reason="Maintenance")

    monkeypatch.setattr(call_guard_module, "get_outbound_call_pause", paused)
    guard = CallGuard(db_pool=object(), redis_client=None)
    guard._get_tenant_limits = lambda _tenant_id: _none()
    guard._get_partner_limits = lambda _tenant_id: _none()
    guard._get_partner_id = lambda _tenant_id: _none()
    guard._log_decision = lambda *_args, **_kwargs: _none()

    result = await guard.evaluate(
        tenant_id="tenant-1",
        phone_number="+15551234567",
        call_type="outbound",
    )

    assert result.decision == GuardDecision.BLOCK
    assert result.failed_checks == [GuardCheck.PLATFORM_CALLS_ENABLED]
    assert len(result.check_results) == 1
    assert result.check_results[0].reason == "platform_outbound_calls_paused"
    assert result.check_results[0].details["reason"] == "Maintenance"


@pytest.mark.asyncio
async def test_platform_pause_does_not_block_inbound_calls(monkeypatch):
    async def must_not_read(_pool):
        raise AssertionError("inbound calls must not consult the outbound pause row")

    monkeypatch.setattr(call_guard_module, "get_outbound_call_pause", must_not_read)
    guard = CallGuard(db_pool=object(), redis_client=None)

    result = await guard._check_platform_calls_enabled(
        tenant_id="tenant-1",
        call_type="inbound",
    )

    assert result.passed is True
    assert result.reason == "not_outbound"


class _TerminateConn:
    def __init__(self, call_row):
        self.call_row = call_row
        self.queries = []

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        if "SELECT id, tenant_id, external_call_uuid" in query:
            return self.call_row
        if "UPDATE calls" in query:
            return {
                "status": "ended",
                "outcome": "agent_hung_up",
                "duration_seconds": 12,
            }
        raise AssertionError(f"Unexpected query: {query}")


class _Audit:
    def __init__(self):
        self.events = []

    async def log(self, **kwargs):
        self.events.append(kwargs)


class _Adapter:
    def __init__(self):
        self.hung_up = []

    async def hangup(self, external_id):
        self.hung_up.append(external_id)


@pytest.mark.asyncio
async def test_admin_terminate_requests_provider_hangup_and_closes_row(monkeypatch):
    call_id = str(uuid4())
    tenant_id = uuid4()
    conn = _TerminateConn({
        "id": call_id,
        "tenant_id": tenant_id,
        "external_call_uuid": "provider-channel-42",
        "status": "in_call",
        "answered_at": datetime.now(timezone.utc),
    })

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        assert tenant_context is None
        yield conn

    adapter = _Adapter()
    audit = _Audit()
    monkeypatch.setattr(calls, "acquire_with_tenant", acquire)
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)

    result = await calls.terminate_call(
        call_id=call_id,
        admin_user=CurrentUser(
            id=str(uuid4()),
            email="operator@example.com",
            tenant_id=None,
            role="platform_admin",
        ),
        db_client=SimpleNamespace(pool=object()),
        audit_logger=audit,
    )

    assert adapter.hung_up == ["provider-channel-42"]
    assert result["provider_hangup_requested"] is True
    assert result["new_status"] == "ended"
    assert any("duration_seconds" in query for query, _args in conn.queries)
    assert audit.events[0]["action"] == "admin_ended_call"


def test_admin_media_models_coerce_database_scalars_and_expose_availability():
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    item = media._recording_item({
        "id": uuid4(),
        "call_id": uuid4(),
        "tenant_id": uuid4(),
        "tenant_name": "Tenant",
        "phone_number": "+15551234567",
        "campaign_id": None,
        "campaign_name": None,
        "status": "uploaded",
        "mime_type": "audio/wav",
        "s3_bucket": "local",
        "duration_seconds": 30,
        "file_size_bytes": 1024,
        "created_at": now,
        "updated_at": now,
    })

    assert item.playable is True
    assert item.storage == "local"
    assert item.created_at == now.isoformat()
    assert len(item.id) == 36


def test_admin_media_date_filter_uses_an_exclusive_next_day_boundary():
    start, end = media._date_bounds(
        datetime(2026, 8, 1).date(),
        datetime(2026, 8, 22).date(),
    )

    assert start == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 23, tzinfo=timezone.utc)


def test_admin_media_rejects_an_inverted_date_range():
    with pytest.raises(HTTPException) as exc:
        media._date_bounds(
            datetime(2026, 8, 23).date(),
            datetime(2026, 8, 22).date(),
        )

    assert getattr(exc.value, "status_code", None) == 422


def test_live_duration_accepts_native_and_serialized_database_timestamps():
    started = datetime.now(timezone.utc) - timedelta(seconds=10)

    native = calls._live_duration_seconds(started)
    serialized = calls._live_duration_seconds(started.isoformat().replace("+00:00", "Z"))

    assert 9 <= native <= 11
    assert 9 <= serialized <= 11
