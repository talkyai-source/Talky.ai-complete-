"""healthwatch `trunks`: only a line that WAS working and went down alerts.

16 of 27 saved phone lines were never working; they kept the check red for
days. A line seen ready (marker in Redis, 7 days) that is down for 5 minutes
alerts; a never-ready line is only counted in never_ready_count.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"
TRUNK = "22222222-2222-4222-8222-222222222222"
TRUNK_B = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def watch():
    spec = importlib.util.spec_from_file_location("qa_healthwatch_trunk", BACKEND / "scripts/healthwatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


def line(*, trunk_id=TRUNK, status="rejected", active=True, deleted=None):
    return {"id": trunk_id, "tenant_id": TENANT, "is_active": active, "deleted_at": deleted,
            "direction": "both", "metadata": {"register": True},
            "live_registration_status": status, "live_status_checked_at": NOW}


async def observe(watch, redis, rows, elapsed):
    observed = NOW + timedelta(seconds=elapsed)
    rows = [{**row, "live_status_checked_at": observed} for row in rows]
    return await watch.check_trunks(rows, redis, now=observed)


def marker(trunk_id=TRUNK):
    return f"talky:healthwatch:trunk_ready:{TENANT}:{trunk_id}"


async def test_a_line_seen_ready_then_down_five_minutes_alerts(watch, redis):
    assert (await observe(watch, redis, [line(status="registered")], 0))["status"] == "ok"
    for elapsed in (10, 130, 250):
        assert (await observe(watch, redis, [line()], elapsed))["status"] == "pending"
    result = await observe(watch, redis, [line()], 310)
    assert result["status"] == "alert" and result["alert_count"] == 1 and result["never_ready_count"] == 0


async def test_a_never_ready_line_down_five_minutes_does_not_alert(watch, redis):
    await observe(watch, redis, [line()], 0)
    result = await observe(watch, redis, [line()], 300)
    assert result["status"] == "ok"
    assert (result["never_ready_count"], result["alert_count"], result["pending_count"]) == (1, 0, 0)
    assert result["checked"] == 1


async def test_both_kinds_together(watch, redis):
    await observe(watch, redis, [line(status="registered")], 0)  # TRUNK was ready once
    rows = [line(), line(trunk_id=TRUNK_B)]
    await observe(watch, redis, rows, 1)
    result = await observe(watch, redis, rows, 301)
    assert (result["alert_count"], result["never_ready_count"]) == (1, 1)
    assert result["status"] == "alert"


async def test_the_marker_is_written_with_a_seven_day_expiry(watch, redis):
    await observe(watch, redis, [line(status="registered")], 0)
    assert watch._TRUNK_READY_SECONDS == 7 * 86400
    assert await redis.exists(marker()) == 1
    assert 7 * 86400 - 5 <= await redis.ttl(marker()) <= 7 * 86400


@pytest.mark.parametrize("kw", [{"active": False}, {"deleted": NOW}])
async def test_an_ignored_line_gets_no_marker(watch, redis, kw):
    result = await observe(watch, redis, [line(status="registered", **kw)], 0)
    assert result["status"] == "ok"
    assert await redis.exists(marker()) == 0


@pytest.mark.parametrize("failure", [
    AsyncMock(side_effect=ConnectionError("x")), AsyncMock(return_value="yes"), AsyncMock(return_value=7)])
async def test_an_unreadable_marker_is_unavailable_never_ok(watch, redis, failure):
    await observe(watch, redis, [line()], 0)
    await observe(watch, redis, [line()], 200)
    redis.expire = failure
    assert (await observe(watch, redis, [line()], 301))["status"] == "unavailable"


@pytest.mark.parametrize("failure", [
    AsyncMock(side_effect=ConnectionError("x")), AsyncMock(side_effect=TimeoutError()),
    AsyncMock(return_value=None), AsyncMock(return_value=False)])
async def test_an_unwritable_marker_is_unavailable_never_ok(watch, redis, failure):
    redis.set = failure
    assert (await observe(watch, redis, [line(status="registered")], 0))["status"] == "unavailable"


@pytest.mark.parametrize("marked", [True, False])
async def test_a_line_down_less_than_five_minutes_is_pending(watch, redis, marked):
    if marked:
        await observe(watch, redis, [line(status="registered")], 0)
    await observe(watch, redis, [line()], 1)
    result = await observe(watch, redis, [line()], 200)
    assert result["status"] == "pending"
    assert (result["pending_count"], result["alert_count"], result["never_ready_count"]) == (1, 0, 0)


async def test_the_alert_run_renews_the_marker_to_the_full_seven_days(watch, redis):
    await observe(watch, redis, [line(status="registered")], 0)
    await observe(watch, redis, [line()], 1)
    await redis.expire(marker(), 100)  # the marker is old: little life left
    assert await redis.ttl(marker()) <= 100
    result = await observe(watch, redis, [line()], 301)
    assert result["alert_count"] == 1
    assert 7 * 86400 - 5 <= await redis.ttl(marker()) <= 7 * 86400


async def test_a_line_that_stays_down_keeps_alerting_after_its_marker_would_have_expired(watch, redis):
    await observe(watch, redis, [line(status="registered")], 0)
    await observe(watch, redis, [line()], 1)
    await observe(watch, redis, [line()], 200)
    assert (await observe(watch, redis, [line()], 301))["alert_count"] == 1  # renewed here
    await redis.expire(marker(), 5)  # a few seconds of the renewed life would remain
    result = await observe(watch, redis, [line()], 400)
    assert result["alert_count"] == 1 and result["status"] == "alert"


@pytest.mark.parametrize("answer,alerts,never", [(1, 1, 0), (0, 0, 1), (True, 1, 0), (False, 0, 1)])
async def test_expire_answers_of_int_or_bool_are_accepted(watch, redis, answer, alerts, never):
    await observe(watch, redis, [line()], 0)
    await observe(watch, redis, [line()], 200)
    redis.expire = AsyncMock(return_value=answer)
    result = await observe(watch, redis, [line()], 301)
    assert (result["alert_count"], result["never_ready_count"]) == (alerts, never)
