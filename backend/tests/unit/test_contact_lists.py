"""Unit tests for the Contact Lists feature.

Covers:
  * bulk_ingest stamps list_id on inserted + revived leads (and stays
    byte-identical when list_id is None),
  * the dialer's active-list filter in CampaignService._get_pending_leads
    (excludes inactive-list leads, keeps NULL/active, fail-safe on error),
  * single-list scoping ("call this list"),
  * create_contact_list dedup/reuse.

A tiny in-memory fake DB models the leads + contact_lists tables and applies
the eq/in_/is_/neq filters the code actually uses.
"""
import pytest

from app.domain.services.dialer.bulk_ingest import ingest_lead_records, LeadRecord


# ── generic in-memory fake DB ─────────────────────────────────────
class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count if count is not None else (len(data) if isinstance(data, list) else 0)


class _Query:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._filters = []          # list of (kind, col, val)
        self._count_mode = None
        self._op = "select"
        self._payload = None

    # builders
    def select(self, *_a, count=None, **_k):
        self._op = "select"
        self._count_mode = count
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        self._filters.append(("is", col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def _match(self, row):
        for kind, col, val in self._filters:
            rv = row.get(col)
            if kind == "eq" and rv != val:
                return False
            if kind == "neq" and rv == val:
                return False
            if kind == "in" and rv not in val:
                return False
            # Only IS NULL is exercised by the code under test.
            if kind == "is" and val is None and rv is not None:
                return False
        return True

    def execute(self):
        rows = self._db.tables.setdefault(self._table, [])
        if self._op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            return _Resp(matched, count=len(matched))
        if self._op == "insert":
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            for it in items:
                rows.append(dict(it))
            self._db.inserted.extend(items)
            return _Resp([dict(i) for i in items])
        if self._op == "update":
            changed = []
            for r in rows:
                if self._match(r):
                    r.update(self._payload)
                    changed.append(dict(r))
            self._db.updates.append(self._payload)
            return _Resp(changed)
        return _Resp([])


class FakeDB:
    def __init__(self):
        self.tables = {"leads": [], "contact_lists": []}
        self.inserted = []
        self.updates = []

    def table(self, name):
        return _Query(self, name)


def _norm(p):
    d = "".join(c for c in p if c.isdigit())
    if len(d) < 10:
        raise ValueError("too short")
    return "+" + d


# ── bulk_ingest list_id tagging ───────────────────────────────────
def test_ingest_stamps_list_id_on_insert():
    db = FakeDB()
    ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1",
        records=[LeadRecord("+1 415 555 1234", source_row=1)],
        normalize=_norm, list_id="L1",
    )
    assert db.inserted[0]["list_id"] == "L1"


def test_ingest_without_list_id_omits_column():
    db = FakeDB()
    ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1",
        records=[LeadRecord("+1 415 555 1234", source_row=1)],
        normalize=_norm,
    )
    # No list_id key at all → byte-identical to pre-feature behaviour.
    assert "list_id" not in db.inserted[0]


def test_ingest_retags_revived_lead():
    db = FakeDB()
    db.tables["leads"].append(
        {"id": "DEL1", "campaign_id": "c1", "phone_number": "+14155551234",
         "status": "deleted", "is_lead": True, "list_id": "OLD"}
    )
    ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1",
        records=[LeadRecord("+1 415 555 1234", source_row=1)],
        normalize=_norm, list_id="NEW",
    )
    # revived row moved into the new list
    assert db.tables["leads"][0]["list_id"] == "NEW"
    assert db.tables["leads"][0]["status"] == "pending"


# ── dialer active-list filter ─────────────────────────────────────
def _service(db):
    from app.domain.services.campaign_service import CampaignService
    return CampaignService(db, queue_service=None)


def _seed_leads(db, specs):
    """specs: list of (id, list_id, status)."""
    for i, (lid, list_id, status) in enumerate(specs):
        db.tables["leads"].append({
            "id": lid, "campaign_id": "c1", "tenant_id": "t1",
            "phone_number": f"+1415555{1000+i}", "status": status,
            "list_id": list_id, "priority": 5, "created_at": "2026-07-03",
        })


@pytest.mark.asyncio
async def test_dialer_excludes_inactive_list_keeps_null_and_active():
    db = FakeDB()
    _seed_leads(db, [
        ("A", None, "pending"),      # Ungrouped → kept
        ("B", "ACTIVE", "pending"),  # active list → kept
        ("C", "OFF", "pending"),     # inactive list → excluded
    ])
    db.tables["contact_lists"] = [
        {"id": "ACTIVE", "campaign_id": "c1", "is_active": True},
        {"id": "OFF", "campaign_id": "c1", "is_active": False},
    ]
    leads = await _service(db)._get_pending_leads("c1")
    ids = {l["id"] for l in leads}
    assert ids == {"A", "B"}


@pytest.mark.asyncio
async def test_dialer_failsafe_includes_all_on_lookup_error():
    class BrokenLists(FakeDB):
        def table(self, name):
            if name == "contact_lists":
                raise RuntimeError("boom")
            return super().table(name)

    db = BrokenLists()
    _seed_leads(db, [("A", None, "pending"), ("C", "OFF", "pending")])
    leads = await _service(db)._get_pending_leads("c1")
    # lookup failed → fail-safe includes everything (never go dark)
    assert {l["id"] for l in leads} == {"A", "C"}


@pytest.mark.asyncio
async def test_dialer_single_list_scope():
    db = FakeDB()
    _seed_leads(db, [
        ("A", "L1", "pending"),
        ("B", "L2", "pending"),
        ("C", None, "pending"),
    ])
    leads = await _service(db)._get_pending_leads("c1", list_id="L1")
    assert {l["id"] for l in leads} == {"A"}


# ── create_contact_list dedup/reuse ───────────────────────────────
def test_create_contact_list_creates_then_reuses():
    from app.api.v1.endpoints.contact_lists import create_contact_list
    db = FakeDB()
    id1 = create_contact_list(db, campaign_id="c1", tenant_id="t1",
                              name="leads.csv", source="csv")
    id2 = create_contact_list(db, campaign_id="c1", tenant_id="t1",
                              name="leads.csv", source="csv")
    assert id1 is not None
    assert id1 == id2                       # same name → reused, not duplicated
    assert len(db.tables["contact_lists"]) == 1


def test_create_contact_list_distinct_names():
    from app.api.v1.endpoints.contact_lists import create_contact_list
    db = FakeDB()
    a = create_contact_list(db, campaign_id="c1", tenant_id="t1",
                            name="a.csv", source="csv")
    b = create_contact_list(db, campaign_id="c1", tenant_id="t1",
                            name="b.csv", source="csv")
    assert a != b
    assert len(db.tables["contact_lists"]) == 2
