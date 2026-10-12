"""campaigns-contacts-crm-6: GET /campaigns/{id}/jobs must carry tenant_id."""
from __future__ import annotations

import asyncio

from app.api.v1.endpoints import campaigns as campaigns_ep


class _Resp:
    def __init__(self, data, count):
        self.data, self.count = data, count


class _Q:
    def __init__(self, db):
        self._db = db
        self._eq = {}

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def execute(self):
        rows = [
            r for r in self._db.rows
            if all(r.get(c) == v for c, v in self._eq.items())
        ]
        self._db.queries.append(dict(self._eq))
        return _Resp(rows, len(rows))


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def table(self, name):
        assert name == "dialer_jobs"
        return _Q(self)


class _User:
    id = "u1"
    tenant_id = "t1"


def test_jobs_query_has_an_explicit_tenant_predicate():
    db = _DB([
        {"id": "j1", "campaign_id": "c-foreign", "tenant_id": "t2", "status": "queued"},
        {"id": "j2", "campaign_id": "c-own", "tenant_id": "t1", "status": "queued"},
    ])
    out = asyncio.run(campaigns_ep.get_campaign_jobs(
        campaign_id="c-foreign", status=None, page=1, page_size=20,
        current_user=_User(), db_client=db,
    ))
    assert out["jobs"] == [] and out["total"] == 0
    assert db.queries[0].get("tenant_id") == "t1"


def test_own_campaign_jobs_still_listed():
    db = _DB([{"id": "j2", "campaign_id": "c-own", "tenant_id": "t1", "status": "queued"}])
    out = asyncio.run(campaigns_ep.get_campaign_jobs(
        campaign_id="c-own", status=None, page=1, page_size=20,
        current_user=_User(), db_client=db,
    ))
    assert [j["id"] for j in out["jobs"]] == ["j2"]
