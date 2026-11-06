"""Cache aggregate work briefly, but re-authorize and read live state each time."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app.api.v1.endpoints import campaigns


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters = db, table, []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def neq(self, column, value):
        return self

    def execute(self):
        self.db.reads.append((self.table, self.filters))
        if self.table == "campaigns":
            row = self.db.campaign
            rows = [dict(row)] if row and all(row.get(k) == v for k, v in self.filters) else []
            return SimpleNamespace(data=rows, count=None)
        assert self.table == "leads"
        return SimpleNamespace(data=[], count=3)


class Database:
    def __init__(self):
        self.tenant, self.id = str(uuid4()), str(uuid4())
        self.campaign = {"id": self.id, "tenant_id": self.tenant, "status": "running", "updated_at": "2026-11-06T00:00:00Z"}
        self.pool = self
        self.reads, self.aggregates = [], []

    def table(self, table):
        return Query(self, table)

    async def fetch(self, query, *args):
        assert "tenant_id = $2::uuid" in query and args == (self.id, self.tenant)
        self.aggregates.append(query)
        if "FROM dialer_jobs" in query:
            return [{"status": "completed", "n": 7}]
        if query == campaigns._STATS_STRING_SUMMARY_SQL:
            return []
        return [{"outcome": "answered", "n": 4, "goals": 2}]


@pytest.fixture
def setup(monkeypatch):
    db, clock = Database(), [1000.0]
    @asynccontextmanager
    async def acquire(pool, tenant):
        assert pool is db and tenant == db.tenant
        yield db
    monkeypatch.setattr(campaigns, "acquire_with_tenant", acquire)
    monkeypatch.setattr(campaigns, "_stats_now", lambda: clock[0], raising=False)
    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(redis=None))
    return db, clock


async def stats(db, tenant=None):
    return await campaigns.get_campaign_stats(
        db.id, current_user=SimpleNamespace(tenant_id=tenant or db.tenant, id=str(uuid4())), db_client=db,
    )


@pytest.mark.asyncio
async def test_repeated_requests_reuse_aggregates_but_keep_live_campaign_status(setup):
    db, clock = setup
    first = await stats(db)
    first["job_status_counts"]["completed"] = 999
    db.campaign["status"] = "paused"
    second = await stats(db)
    assert len(db.aggregates) == 3, "Repeated stats requests must reuse the short-lived aggregate snapshot"
    assert second["job_status_counts"] == {"completed": 7}
    assert second["campaign_status"] == "paused"
    clock[0] += 11
    third = await stats(db)
    assert len(db.aggregates) == 6
    assert third["goals_achieved"] == 2


@pytest.mark.asyncio
async def test_cached_stats_still_require_an_owned_existing_campaign(setup):
    db, _ = setup
    await stats(db)
    with pytest.raises(HTTPException) as exc:
        await stats(db, str(uuid4()))
    assert exc.value.status_code == 404
    db.campaign = None
    with pytest.raises(HTTPException) as exc:
        await stats(db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_every_adapter_read_has_an_explicit_tenant_predicate(setup):
    db, _ = setup
    await stats(db)
    assert db.reads
    assert all(("tenant_id", db.tenant) in filters for _table, filters in db.reads)


@pytest.mark.asyncio
async def test_distinct_request_clients_sharing_pool_reuse_aggregate_cache(setup, monkeypatch):
    from app.core.postgres_adapter import Client

    db, _ = setup
    # get_db_client constructs Client(pool) for each HTTP request. Keep those
    # real, distinct wrappers while only their storage port uses the fixture.
    first_client, second_client = Client(db), Client(db)
    assert first_client is not second_client and first_client.pool is second_client.pool is db
    monkeypatch.setattr(first_client, "table", db.table)
    monkeypatch.setattr(second_client, "table", db.table)
    user = SimpleNamespace(tenant_id=db.tenant, id=str(uuid4()))
    first = await campaigns.get_campaign_stats(db.id, current_user=user, db_client=first_client)
    db.campaign["status"] = "paused"
    second = await campaigns.get_campaign_stats(db.id, current_user=user, db_client=second_client)
    assert len(db.aggregates) == 3, "A fresh request Client must not bypass the shared-pool cache"
    assert first["goals_achieved"] == second["goals_achieved"] == 2
    assert second["campaign_status"] == "paused"
    assert len([table for table, _ in db.reads if table == "campaigns"]) == 2
    assert all(("tenant_id", db.tenant) in filters for _table, filters in db.reads)
