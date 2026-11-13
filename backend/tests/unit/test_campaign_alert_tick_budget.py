"""An uncertain delivery cannot reset the bounded per-tick attempt allowance."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.services import campaign_alerts
from app.domain.services.minutes_quota import MinutesStatus


@pytest.mark.asyncio
async def test_failed_delivery_persistence_cannot_grant_another_tenant_new_attempts(monkeypatch):
    tenants = [{"id": uuid4()} for _ in range(11)]
    connection = AsyncMock()
    connection.fetch.return_value = tenants
    @asynccontextmanager
    async def acquire(pool, tenant_id, **kwargs):
        yield connection
    monkeypatch.setattr(campaign_alerts, "acquire_with_tenant", acquire)
    monkeypatch.setattr(campaign_alerts, "compute_minutes_status",
                        AsyncMock(return_value=MinutesStatus(100, 0, 100, False, False)))
    observations = AsyncMock()
    monkeypatch.setattr(campaign_alerts, "record_low_minutes", observations)
    attempts = []
    async def delivery(pool, tenant_id, **kwargs):
        # One external attempt occurred, but durable final status is unavailable;
        # the real sender's committed sending fence remains. The caller cannot
        # infer zero consumption from this exception or grant a fresh allowance.
        attempts.append(tenant_id)
        raise RuntimeError("synthetic post-send persistence failure")
    monkeypatch.setattr(campaign_alerts, "deliver_tenant_alerts", delivery)
    result = await campaign_alerts.process_alert_tick(object(), limit=11)
    assert len(attempts) <= 10, "Failed final persistence must not replenish the per-tick send budget"
    assert observations.await_count == 11, "Mail uncertainty must not suppress other tenants' meter observations"
    assert result == str(tenants[-1]["id"])
