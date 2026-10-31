"""Live-risk fixes: heterogeneous bulk insert, phone edit vs queued jobs,
re-upload into a switched-off list."""
from __future__ import annotations

import asyncio

import pytest

from app.core import postgres_adapter
from app.core.postgres_adapter import QueryBuilder
from app.api.v1.endpoints import campaigns as campaigns_ep
from app.api.v1.endpoints.contact_lists import create_contact_list
from app.api.v1.schemas.campaigns import ContactUpdate
from tests.unit.test_bh_campaigns_contacts_contact_writes import _DB, _User
from tests.unit.test_postgres_adapter import FakeConn


# ── leads-1: each inserted row binds its own columns ──────────────
def test_bulk_insert_binds_each_rows_own_columns(monkeypatch):
    conn = FakeConn()
    conn.on_fetch(
        "FROM information_schema.columns",
        [
            {"column_name": "id", "udt_name": "text"},
            {"column_name": "phone_number", "udt_name": "text"},
            {"column_name": "do_not_call", "udt_name": "bool"},
        ],
    )
    conn.on_fetchrow("INSERT INTO leads", {"id": "x"})

    async def fake_connect(_dsn):
        return conn

    monkeypatch.setattr(postgres_adapter.asyncpg, "connect", fake_connect)

    QueryBuilder(None, "leads").insert([
        {"id": "a", "phone_number": "+1"},                      # blank DNC cell
        {"id": "b", "phone_number": "+2", "do_not_call": True},  # flagged row
    ]).execute()

    assert len(conn.fetchrow_calls) == 2
    sql1, args1 = conn.fetchrow_calls[0]
    sql2, args2 = conn.fetchrow_calls[1]
    assert "do_not_call" not in sql1 and args1 == ("a", "+1")
    assert "do_not_call" in sql2 and args2 == ("b", "+2", True)


# ── contacts-2 / dialer-3: phone edit cancels not-originated jobs ─
from tests.unit import test_bh_campaigns_contacts_contact_writes as _w


class _JobsQ(_w._Q):
    def in_(self, c, vals):
        self._f.append(("in", c, list(vals)))
        return self

    def _m(self, r):
        for k, c, v in self._f:
            if k == "in" and r.get(c) not in v:
                return False
        return super()._m(r)


class _JobsDB(_w._DB):
    def __init__(self):
        super().__init__(leads=[{
            "id": "l1", "tenant_id": "t1", "campaign_id": "c1",
            "phone_number": "+14155550124", "status": "pending",
        }])
        self.tables["dialer_jobs"] = [
            {"id": "j1", "tenant_id": "t1", "lead_id": "l1", "status": "queued"},
            {"id": "j2", "tenant_id": "t1", "lead_id": "l1", "status": "retry_scheduled"},
            {"id": "j3", "tenant_id": "t1", "lead_id": "l1", "status": "calling"},
            {"id": "j4", "tenant_id": "t1", "lead_id": "other", "status": "queued"},
            {"id": "j5", "tenant_id": "t2", "lead_id": "l1", "status": "queued"},
        ]

    def table(self, name):
        return _JobsQ(self, name)


def _status(db):
    return {j["id"]: j["status"] for j in db.tables["dialer_jobs"]}


def _edit(db, **kw):
    asyncio.run(campaigns_ep.update_contact_in_campaign(
        campaign_id="c1", contact_id="l1", contact=ContactUpdate(**kw),
        current_user=_User(), supabase=db,
    ))


def test_phone_change_cancels_the_leads_not_originated_jobs():
    db = _JobsDB()
    _edit(db, phone_number="+14155550999")
    assert db.tables["leads"][0]["phone_number"] == "+14155550999"
    assert _status(db) == {
        "j1": "cancelled", "j2": "cancelled", "j3": "calling", "j4": "queued",
        "j5": "queued",
    }


def test_non_phone_edit_leaves_jobs_alone():
    db = _JobsDB()
    _edit(db, first_name="Ann")
    assert _status(db) == {
        "j1": "queued", "j2": "retry_scheduled", "j3": "calling", "j4": "queued",
        "j5": "queued",
    }


def test_same_phone_resubmitted_leaves_jobs_alone():
    db = _JobsDB()
    _edit(db, phone_number="+14155550124")
    assert _status(db)["j1"] == "queued"


# ── leads-3: never reuse a switched-off list ──────────────────────
def test_create_contact_list_does_not_reuse_inactive_list():
    from tests.unit.test_contact_lists import FakeDB
    db = FakeDB()
    db.tables["contact_lists"].append({
        "id": "OFF", "campaign_id": "c1", "tenant_id": "t1",
        "name": "contacts.csv", "is_active": False,
    })
    new_id = create_contact_list(
        db, campaign_id="c1", tenant_id="t1", name="contacts.csv", source="csv",
    )
    assert new_id != "OFF"
    assert db.tables["contact_lists"][-1]["is_active"] is True
    again = create_contact_list(
        db, campaign_id="c1", tenant_id="t1", name="contacts.csv", source="csv",
    )
    assert again == new_id
