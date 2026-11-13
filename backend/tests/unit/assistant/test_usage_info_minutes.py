"""The assistant reports the minutes the Billing page shows (QA F3-07, audit BIL-13).

``get_usage_info`` read ``tenants.minutes_allocated`` and the stale
``minutes_used`` column, so a pay-as-you-go customer with 250 bought minutes
was told they had none.
"""
from types import SimpleNamespace

import pytest

from app.domain.services import minutes_quota
from app.domain.services.minutes_quota import MinutesStatus, unavailable_minutes
from app.infrastructure.assistant.tools.dashboard import get_usage_info


class _Table:
    def __init__(self, row):
        self.row = row

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.row)


def _db(tenant, plan):
    return SimpleNamespace(table=lambda name: _Table({"tenants": tenant, "plans": plan}[name]))


PAYG_TENANT = {"id": "t1", "plan_id": "payg", "minutes_allocated": 0, "minutes_used": 0,
               "subscription_status": "inactive"}
PAYG_PLAN = {"name": "Pay as you go", "price": 0, "minutes": 0}


def _meter(monkeypatch, status):
    async def meter(_tenant_id):
        return status

    monkeypatch.setattr(minutes_quota, "tenant_minutes_status", meter)


@pytest.mark.asyncio
async def test_a_pay_as_you_go_customer_hears_their_bought_minutes(monkeypatch):
    _meter(monkeypatch, MinutesStatus(250, 0, 250, False, False))
    info = await get_usage_info("t1", _db(PAYG_TENANT, PAYG_PLAN))
    assert info["minutes_allocated"] == 250 and info["minutes_remaining"] == 250


@pytest.mark.asyncio
async def test_an_unknown_meter_is_never_reported_as_zero_minutes(monkeypatch):
    _meter(monkeypatch, unavailable_minutes())
    info = await get_usage_info("t1", _db(PAYG_TENANT, PAYG_PLAN))
    assert info["minutes_state"] == "unavailable"
    assert info["minutes_remaining"] is None and info["minutes_used"] is None
