"""Conversion of an outbound draft must leave no outbound work behind."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

import app.domain.services.inbound_campaign_service as campaign_module
from app.domain.services.inbound_campaign_service import (
    InboundCampaignService,
    InboundConflictError,
)


TENANT = "11111111-1111-1111-1111-111111111111"
ACTOR = "22222222-2222-2222-2222-222222222222"
CAMPAIGN = "33333333-3333-3333-3333-333333333333"
TRUNK = "44444444-4444-4444-4444-444444444444"


class _ConversionConn:
    def __init__(self, blocker: str, *, calling_config=None):
        self.blocker = blocker
        self.calling_config = calling_config
        self.job_query = None
        self.job_args = None
        self.events: list[str] = []

    async def execute(self, query, *_args):
        if "pg_advisory_xact_lock" in query:
            self.events.append("direction_lock")
            return "SELECT 1"
        self.events.append("execute")
        return "UPDATE 1"

    async def fetchrow(self, query, *_args):
        if "SELECT * FROM campaigns" in query:
            self.events.append("campaign_lock")
            return {
                "id": CAMPAIGN,
                "tenant_id": TENANT,
                "direction": "outbound",
                "status": "draft",
                "calling_config": self.calling_config,
            }
        raise AssertionError(query)

    async def fetchval(self, query, *args):
        if "FROM calls" in query:
            return False
        if "FROM dialer_jobs" in query:
            self.job_query = " ".join(query.split())
            self.job_args = args
            if self.blocker == "terminal_job":
                # A terminal/cancelled row is invisible to the old active-
                # status query, but it is still outbound history and must make
                # this campaign ineligible for conversion.
                return "status = ANY" not in self.job_query
            return self.blocker == "active_job"
        if "FROM leads" in query:
            return self.blocker == "live_lead"
        if "FROM contact_lists" in query:
            return self.blocker == "contact_list"
        raise AssertionError(query)


def _payload():
    return {
        "name": "Inbound",
        "campaign_id": CAMPAIGN,
        "sip_trunk_id": TRUNK,
        "did_number": "+15551234567",
        "timezone": "UTC",
        "after_hours_action": "hangup",
        "recording_enabled": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["active_job", "live_lead", "contact_list"])
async def test_conversion_rejects_every_outbound_artifact(monkeypatch, blocker):
    conn = _ConversionConn(blocker)

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)

    with pytest.raises(InboundConflictError) as exc:
        await service.create_campaign(
            tenant_id=TENANT,
            actor_id=ACTOR,
            actor_role="tenant_admin",
            payload=_payload(),
            idempotency_key=f"block-{blocker}",
        )

    assert exc.value.code == "campaign_direction_conflict"
    assert "status" not in conn.job_query.lower()
    assert conn.job_args == (CAMPAIGN, TENANT)


@pytest.mark.asyncio
async def test_conversion_rejects_terminal_dialer_job_history(monkeypatch):
    conn = _ConversionConn("terminal_job")

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)

    with pytest.raises(InboundConflictError) as exc:
        await service.create_campaign(
            tenant_id=TENANT,
            actor_id=ACTOR,
            actor_role="tenant_admin",
            payload=_payload(),
            idempotency_key="block-terminal-job-history",
        )

    assert exc.value.code == "campaign_direction_conflict"
    assert "status" not in conn.job_query.lower()
    assert conn.job_args == (CAMPAIGN, TENANT)


@pytest.mark.asyncio
async def test_conversion_takes_shared_direction_lock_before_campaign_row_lock(monkeypatch):
    conn = _ConversionConn("active_job")

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)

    with pytest.raises(InboundConflictError):
        await service.create_campaign(
            tenant_id=TENANT,
            actor_id=ACTOR,
            actor_role="tenant_admin",
            payload=_payload(),
            idempotency_key="shared-direction-lock",
        )

    assert conn.events[:2] == ["direction_lock", "campaign_lock"]


@pytest.mark.asyncio
async def test_conversion_rejects_outbound_trunk_snapshot_before_direction_change(monkeypatch):
    conn = _ConversionConn(
        "none",
        calling_config={
            "trunk": {
                "id": TRUNK,
                "endpoint": f"trunk-{TRUNK}",
                "caller_id": "+15551234567",
            }
        },
    )

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)

    with pytest.raises(InboundConflictError) as exc:
        await service.create_campaign(
            tenant_id=TENANT,
            actor_id=ACTOR,
            actor_role="tenant_admin",
            payload=_payload(),
            idempotency_key="block-outbound-trunk",
        )

    assert exc.value.code == "campaign_direction_conflict"
    assert "trunk" in str(exc.value).lower()
    assert conn.job_query is None
