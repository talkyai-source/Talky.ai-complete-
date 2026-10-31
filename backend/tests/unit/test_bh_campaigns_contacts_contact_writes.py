"""campaigns-contacts-crm-9 (revive keeps do_not_call) and crm-8 (edit uses the
campaign's default country)."""
from __future__ import annotations

import asyncio

from app.api.v1.endpoints import campaigns as campaigns_ep
from app.api.v1.schemas.campaigns import ContactCreate, ContactUpdate


class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count if count is not None else len(data)
        self.error = None


class _Q:
    def __init__(self, db, table):
        self._db, self._table = db, table
        self._f = []
        self._op = "select"
        self._payload = None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, data):
        self._op, self._payload = "insert", data
        return self

    def update(self, data):
        self._op, self._payload = "update", data
        return self

    def eq(self, c, v):
        self._f.append(("eq", c, v))
        return self

    def neq(self, c, v):
        self._f.append(("neq", c, v))
        return self

    def in_(self, c, vals):
        self._f.append(("in", c, list(vals)))
        return self

    def _m(self, r):
        for k, c, v in self._f:
            if k == "in" and r.get(c) not in v:
                return False
            if k == "eq" and r.get(c) != v:
                return False
            if k == "neq" and r.get(c) == v:
                return False
        return True

    def execute(self):
        rows = self._db.tables.setdefault(self._table, [])
        if self._op == "select":
            return _Resp([dict(r) for r in rows if self._m(r)])
        if self._op == "insert":
            row = dict(self._payload)
            rows.append(row)
            return _Resp([dict(row)])
        changed = []
        for r in rows:
            if self._m(r):
                r.update(self._payload)
                changed.append(dict(r))
        return _Resp(changed)


class _DB:
    def __init__(self, campaign_cfg=None, leads=None):
        self.tables = {
            "campaigns": [{
                "id": "c1", "tenant_id": "t1", "direction": "outbound",
                "name": "n", "status": "draft",
                "script_config": campaign_cfg or {},
            }],
            "leads": leads or [],
        }

    def table(self, name):
        return _Q(self, name)


class _User:
    id = "u1"
    tenant_id = "t1"
    email = "someone@example.com"


def test_readding_a_deleted_dnc_contact_keeps_do_not_call():
    db = _DB(leads=[{
        "id": "l1", "tenant_id": "t1", "campaign_id": "c1",
        "phone_number": "+14155550124", "status": "deleted",
        "is_lead": False, "do_not_call": True,
    }])
    asyncio.run(campaigns_ep.add_contact_to_campaign(
        campaign_id="c1",
        contact=ContactCreate(phone_number="+14155550124", first_name="Ann"),
        current_user=_User(), supabase=db,
    ))
    lead = db.tables["leads"][0]
    assert lead["status"] == "pending"
    assert lead["first_name"] == "Ann"
    assert lead["do_not_call"] is True


def test_readding_a_deleted_contact_can_still_set_do_not_call():
    db = _DB(leads=[{
        "id": "l1", "tenant_id": "t1", "campaign_id": "c1",
        "phone_number": "+14155550124", "status": "deleted",
        "is_lead": False, "do_not_call": False,
    }])
    asyncio.run(campaigns_ep.add_contact_to_campaign(
        campaign_id="c1",
        contact=ContactCreate(phone_number="+14155550124", do_not_call=True),
        current_user=_User(), supabase=db,
    ))
    assert db.tables["leads"][0]["do_not_call"] is True


def test_editing_a_phone_uses_the_campaign_default_country():
    db = _DB(
        campaign_cfg={"default_country_code": "GB"},
        leads=[{
            "id": "l1", "tenant_id": "t1", "campaign_id": "c1",
            "phone_number": "+447700900001", "status": "pending",
        }],
    )
    asyncio.run(campaigns_ep.update_contact_in_campaign(
        campaign_id="c1", contact_id="l1",
        contact=ContactUpdate(phone_number="07700 900123"),
        current_user=_User(), supabase=db,
    ))
    assert db.tables["leads"][0]["phone_number"] == "+447700900123"


def test_readding_keeps_do_not_call_from_any_deleted_duplicate():
    # The revived row is the is_lead one (not DNC); a sibling deleted row for
    # the same number carries the suppression. It must not be lost.
    db = _DB(leads=[
        {"id": "l1", "tenant_id": "t1", "campaign_id": "c1",
         "phone_number": "+14155550124", "status": "deleted",
         "is_lead": True, "do_not_call": False},
        {"id": "l2", "tenant_id": "t1", "campaign_id": "c1",
         "phone_number": "+14155550124", "status": "deleted",
         "is_lead": False, "do_not_call": True},
    ])
    asyncio.run(campaigns_ep.add_contact_to_campaign(
        campaign_id="c1",
        contact=ContactCreate(phone_number="+14155550124", first_name="Ann"),
        current_user=_User(), supabase=db,
    ))
    revived = [r for r in db.tables["leads"] if r["status"] == "pending"]
    assert len(revived) == 1 and revived[0]["do_not_call"] is True
