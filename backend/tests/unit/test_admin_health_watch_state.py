"""GET /admin/health/detailed carries the server health watch's last result, so the
System Health page cannot be green while the watch is red."""
import time
from types import SimpleNamespace

import fakeredis.aioredis
import pytest

from app.api.v1.endpoints.admin.health import system
from app.core import container
from app.core import operational_alerts as alerts

ADMIN = SimpleNamespace(id="a1", role="platform_admin")
METRICS = dict(uptime_seconds=5, uptime_display="5s", memory_usage_mb=1.0, memory_total_mb=2.0, memory_percent=50.0,
               cpu_usage_percent=1.0, disk_usage_percent=91.5, disk_free_gb=3.0)


@pytest.fixture
async def redis(monkeypatch):
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(system, "_host_metrics", lambda: METRICS)

    async def providers(pool):
        return []
    monkeypatch.setattr(system, "_internal_dependency_health", providers)
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=True, redis=client))
    yield client
    await client.aclose()


async def _detail():
    return await system.get_detailed_health(admin_user=ADMIN, db_client=SimpleNamespace(pool=object()))


async def test_an_alerting_watch_is_returned_with_its_checks_and_sentences(redis):
    await redis.set(alerts.HEALTHWATCH_LAST_RUN_KEY, str(time.time() - 30), ex=600)
    await redis.set(alerts.HEALTHWATCH_FAILING_KEY, "trunks:alert:2,disk:alert", ex=600)
    watch = (await _detail()).health_watch
    assert watch.overall == "alert" and watch.age_seconds in range(29, 40)
    assert [(c.name, c.status) for c in watch.alerting] == [("trunks", "alert"), ("disk", "alert")]
    assert watch.alerting[0].sentence == "2 phone lines are not connected."
    assert watch.alerting[1].sentence == "The server disk is nearly full."
    assert (await _detail()).disk_usage_percent == 91.5


async def test_a_quiet_watch_is_ok_with_no_checks(redis):
    await redis.set(alerts.HEALTHWATCH_LAST_RUN_KEY, str(time.time() - 5), ex=600)
    watch = (await _detail()).health_watch
    assert watch.overall == "ok" and watch.alerting == [] and watch.age_seconds < 20


async def test_a_watch_that_has_not_run_is_unknown_never_ok(redis):
    watch = (await _detail()).health_watch
    assert watch.overall == "unknown" and watch.last_run_at is None and watch.age_seconds is None


async def test_an_unreadable_store_is_unknown(monkeypatch):
    class Broken:
        async def get(self, key):
            raise ConnectionError("down")
    monkeypatch.setattr(system, "_host_metrics", lambda: METRICS)

    async def providers(pool):
        return []
    monkeypatch.setattr(system, "_internal_dependency_health", providers)
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=True, redis=Broken()))
    assert (await _detail()).health_watch.overall == "unknown"
