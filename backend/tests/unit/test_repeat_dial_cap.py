"""QA F12-05: repeat-dial limits are on by default.

The per-number cap defaulted to 0 (off) and nothing could switch it on: one
number was dialled 7 times in a day, and busy retries placed 4 calls in 65
minutes. A number is now dialled at most 3 times in any 24 hours across all
of a tenant's campaigns, unless the tenant set its own cap.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.services.scheduling_rules import SchedulingRuleEngine


async def _gate(rules, attempts):
    return await SchedulingRuleEngine().can_make_call(
        tenant_id="t", campaign_id="c", rules=rules,
        lead_attempts_today=attempts, enforce_window=False,
    )


@pytest.mark.asyncio
async def test_the_platform_cap_applies_when_the_tenant_set_none(monkeypatch):
    monkeypatch.delenv("DIAL_CAP_PER_NUMBER_24H", raising=False)
    rules = CallingRules()
    assert rules.max_calls_per_lead_per_day == 0
    assert (await _gate(rules, 2))[0] is True
    assert await _gate(rules, 3) == (False, "daily_lead_cap_reached_3/3")


@pytest.mark.asyncio
async def test_a_tenant_cap_overrides_the_platform_cap(monkeypatch):
    monkeypatch.delenv("DIAL_CAP_PER_NUMBER_24H", raising=False)
    rules = CallingRules(max_calls_per_lead_per_day=5)
    assert (await _gate(rules, 4))[0] is True
    assert await _gate(rules, 5) == (False, "daily_lead_cap_reached_5/5")


@pytest.mark.asyncio
async def test_operations_can_switch_the_platform_cap_off(monkeypatch):
    monkeypatch.setenv("DIAL_CAP_PER_NUMBER_24H", "0")
    assert (await _gate(CallingRules(), 9))[0] is True


@pytest.mark.asyncio
async def test_dials_are_counted_per_number_across_campaigns_for_24_hours():
    """The count joins the lead to its tenant and number, so every campaign's
    calls to that number count, and only for the lead's own tenant."""
    from app.workers.dialer_worker import DialerWorker

    seen = []

    class _Conn:
        async def fetchval(self, query, *args):
            seen.append((" ".join(query.split()), args))
            return 2

    @asynccontextmanager
    async def acquire():
        yield _Conn()

    worker = DialerWorker.__new__(DialerWorker)
    worker._acquire_db = acquire
    assert await worker._get_lead_attempts_today("lead-1", "tenant-1") == 2
    query, args = seen[0]
    assert args == ("lead-1", "tenant-1")
    assert "l.tenant_id = $2" in query, "the lead is looked up within the job's tenant"
    assert "c.tenant_id = l.tenant_id" in query
    assert "c.phone_number = l.phone_number" in query
    assert "interval '24 hours'" in query
    assert "campaign_id" not in query
