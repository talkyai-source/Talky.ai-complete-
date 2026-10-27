"""Bounded cancellation/ownership behavior; synthetic SQL and registry ports only."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.domain.services import inbound_campaign_service as campaign
from app.domain.services.telephony import inbound_admission as admission


@pytest.mark.parametrize("kind", ["campaign", "billing"])
@pytest.mark.parametrize("stage", ["acquire", "body", "commit"])
async def test_ordinary_deadline_covers_acquire_body_and_commit(monkeypatch, kind, stage):
    module = campaign if kind == "campaign" else admission
    error = campaign.InboundCampaignError if kind == "campaign" else admission.InboundHoldResolutionUnavailableError
    events = []
    timeout = asyncio.timeout

    def accelerated_timeout(seconds):
        assert seconds == 10.0
        return timeout(0.03)

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(timeout=accelerated_timeout))
    conn = SimpleNamespace(execute=AsyncMock())

    @asynccontextmanager
    async def acquire(_pool, tenant_id):
        assert tenant_id == (None if kind == "campaign" else "tenant")
        try:
            if stage == "acquire":
                await asyncio.Event().wait()
            events.append("acquired")
            yield conn
            events.append("commit_started")
            if stage == "commit":
                await asyncio.Event().wait()
            events.append("committed")
        finally:
            events.append("released")

    async def guard(actual_conn, actor_id):
        assert actual_conn is conn and actor_id == "actor"
        events.append("authority")

    monkeypatch.setattr(module, "acquire_with_tenant", acquire)
    monkeypatch.setattr(module, "require_platform_admin_mutation", guard)
    cm = (campaign.InboundCampaignService(object())._admin_transaction("actor") if kind == "campaign"
          else admission.InboundAdmissionService(object())._admin_hold_transaction("tenant", "actor"))
    with pytest.raises(error) as exc:
        async with cm:
            assert events == ["acquired", "authority"]
            if stage == "body":
                await asyncio.Event().wait()
    assert "could not be confirmed" in str(exc.value)
    assert "committed" not in events
    assert events[-1] == "released"


@pytest.mark.parametrize("kind", ["campaign", "billing"])
@pytest.mark.parametrize("failure", ["denied", "cancelled"])
async def test_current_denial_and_caller_cancellation_are_not_relabelled(monkeypatch, kind, failure):
    module = campaign if kind == "campaign" else admission
    conn = SimpleNamespace(execute=AsyncMock())
    released = False

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        nonlocal released
        try:
            yield conn
        finally:
            released = True

    async def guard(_conn, _actor):
        if failure == "denied":
            raise HTTPException(403, "Current authority denied")
        raise asyncio.CancelledError()

    monkeypatch.setattr(module, "acquire_with_tenant", acquire)
    monkeypatch.setattr(module, "require_platform_admin_mutation", guard)
    cm = (campaign.InboundCampaignService(object())._admin_transaction("actor") if kind == "campaign"
          else admission.InboundAdmissionService(object())._admin_hold_transaction("tenant", "actor"))
    with pytest.raises(HTTPException if failure == "denied" else asyncio.CancelledError) as exc:
        async with cm:
            pytest.fail("Denied/cancelled caller reached business work")
    if failure == "denied":
        assert exc.value.status_code == 403
    assert released
    conn.execute.assert_not_awaited()


async def test_enable_timeout_keeps_controls_closed_and_existing_heartbeat_single(monkeypatch):
    from app.core import inbound_startup
    from app.domain.services.telephony import adapter_registry, state_backend

    timeout = asyncio.timeout
    monkeypatch.setattr(campaign, "asyncio", SimpleNamespace(timeout=lambda _seconds: timeout(0.04)))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(inbound_startup, "validate_production_inbound_database_role", AsyncMock())
    monkeypatch.setattr(inbound_startup, "validate_live_production_inbound_adapter", lambda **_kwargs: None)
    monkeypatch.setattr(inbound_startup, "validate_production_inbound_state_backend", lambda **_kwargs: None)
    monkeypatch.setattr(adapter_registry, "get_adapter", lambda: object())
    registry = SimpleNamespace(try_acquire_ari_ownership_strict=AsyncMock(return_value=True),
                               write_heartbeat_strict=AsyncMock())
    backend = object.__new__(state_backend.RedisBackedStateBackend)
    backend._registry, backend._is_owner, backend._strict_ownership = registry, False, False
    backend._heartbeat_task = None
    heartbeat_starts = []

    async def owned_heartbeat():
        heartbeat_starts.append(True)
        await asyncio.Event().wait()

    backend._heartbeat_loop = owned_heartbeat
    monkeypatch.setattr(state_backend, "get_state_backend", lambda: backend)
    control_updates, commits = [], []

    class Conn:
        async def execute(self, _query):
            return "SET"

        async def fetchrow(self, query, *_args):
            if query.startswith("SELECT * FROM platform_runtime_controls"):
                return {"inbound_controls_version": 1, "inbound_enabled": False, "inbound_transfer_enabled": False}
            if "UPDATE platform_runtime_controls" in query:
                # Delay the SQL port after actual owner/heartbeat methods have
                # completed; cancellation means no durable controls result.
                control_updates.append("attempted")
                await asyncio.Event().wait()
            raise AssertionError(query)

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield Conn()
        commits.append(True)

    monkeypatch.setattr(campaign, "acquire_with_tenant", acquire)
    monkeypatch.setattr(campaign, "require_platform_admin_mutation", AsyncMock())
    service = campaign.InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    service._audit = AsyncMock()
    try:
        for _ in range(2):
            with pytest.raises(campaign.InboundCampaignError) as exc:
                await service.set_platform_controls(actor_id="11111111-1111-1111-1111-111111111111",
                    actor_role="platform_admin", idempotency_key="synthetic-enable-key",
                    payload={"inbound_enabled": True, "recording_enabled": False, "transfer_enabled": False,
                             "settlement_enabled": False, "expected_version": 1, "reason": "Synthetic enable"})
            assert exc.value.status_code == 503
        assert len(control_updates) == 2 and commits == []
        assert len(heartbeat_starts) == 1
        assert backend.is_telephony_owner() is True  # External lease is not a DB rollback.
        service._audit.assert_not_awaited()
    finally:
        if backend._heartbeat_task is not None:
            backend._heartbeat_task.cancel()
            await asyncio.gather(backend._heartbeat_task, return_exceptions=True)
