"""D3-15/D3-16: a stopped or paused campaign says when its live calls are still ending.

Stop and Pause write the new status at once, then the hang-up sweep marks
the campaign's live calls termination_pending and the recovery loop retries
them until they are proved gone. Nothing told the app that calls were still
ending, or when the last one ended. GET /campaigns/{id} now returns
progress.live_calls (the sweep's own status set, so it reaches 0 exactly
when the sweep has nothing left) and progress.phase ("stopping"/"pausing"
while that count is above 0). The status column itself is unchanged: the
database check (0088) has no stopping value.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import campaigns as campaigns_ep
from app.api.v1.endpoints.telephony_bridge import CAMPAIGN_LIVE_CALL_STATUSES
from app.domain.services.campaign_service import campaign_phase

TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
CAMPAIGN = "11111111-2222-4333-8444-555555555555"


@pytest.mark.parametrize("status,live,phase", [
    ("stopped", 2, "stopping"),
    ("stopped", 0, "stopped"),
    ("paused", 1, "pausing"),
    ("paused", 0, "paused"),
    ("running", 3, "running"),
    ("draft", 0, "draft"),
    ("stopped", None, "stopped"),
])
def test_phase_shows_calls_still_ending(status, live, phase):
    assert campaign_phase(status, live) == phase


def test_the_count_uses_the_stop_sweeps_own_status_set():
    assert "termination_pending" in CAMPAIGN_LIVE_CALL_STATUSES
    assert {"ringing", "in_call", "answered"} <= set(CAMPAIGN_LIVE_CALL_STATUSES)
    assert not {"ended", "completed", "failed"} & set(CAMPAIGN_LIVE_CALL_STATUSES)


@pytest.fixture
def endpoint(monkeypatch):
    state = SimpleNamespace(status="stopped", live=2, fail=False, queries=[])

    class Conn:
        async def fetchval(self, query, *args):
            state.queries.append((query, args))
            if "FROM calls" in query:
                if state.fail:
                    raise RuntimeError("synthetic read failure")
                return state.live
            return None

        async def fetch(self, query, *args):
            return []

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        yield Conn()

    class Service:
        async def get_campaign(self, campaign_id):
            return {"id": campaign_id, "tenant_id": TENANT, "status": state.status}

    monkeypatch.setattr(campaigns_ep, "acquire_with_tenant", acquire)
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: Service())
    return state


async def _get():
    return await campaigns_ep.get_campaign(
        campaign_id=CAMPAIGN, request=SimpleNamespace(),
        current_user=SimpleNamespace(id="u1", tenant_id=TENANT),
        db_client=SimpleNamespace(pool=object()))


async def test_a_stopped_campaign_with_live_calls_reads_stopping_until_they_end(endpoint):
    first = await _get()
    assert first["campaign"]["status"] == "stopped"
    assert first["progress"] == {"live_calls": 2, "phase": "stopping"}

    endpoint.live = 0
    later = await _get()
    assert later["progress"] == {"live_calls": 0, "phase": "stopped"}

    query, args = next((q, a) for q, a in endpoint.queries if "FROM calls" in q)
    assert "tenant_id = $1" in query and "campaign_id = $2" in query
    assert args == (TENANT, CAMPAIGN, list(CAMPAIGN_LIVE_CALL_STATUSES))


async def test_an_unreadable_count_does_not_break_the_campaign_page(endpoint):
    endpoint.fail = True
    response = await _get()
    assert response["campaign"]["id"] == CAMPAIGN
    assert response["progress"] is None
