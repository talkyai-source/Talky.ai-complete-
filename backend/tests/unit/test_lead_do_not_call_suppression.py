"""Compliance regression: the per-lead ``leads.do_not_call`` flag must
actually suppress dialling.

Root cause this guards against
------------------------------
Migration 0020 added ``leads.do_not_call`` (a per-contact suppression flag,
written by the CSV import and the manual contact form) and documented it as
"ADDITIVE to the tenant DNC list ... anything that dials must keep checking".
Nothing ever read it: ``grep -n do_not_call`` over ``call_guard.py``,
``dnc_service.py`` and ``campaign_service.py`` returned zero matches, so a
lead the customer explicitly flagged do-not-call was still selected,
enqueued and dialled.

Two independent layers are asserted here:

  1. **Selection boundary** — ``CampaignService._get_pending_leads`` must not
     return a flagged lead, so ``start_campaign`` never enqueues one and the
     campaign's ``total_leads`` denominator only counts leads it will call.
  2. **Dial time (defence in depth)** — ``CallGuard._check_dnc`` must block a
     flagged lead that reaches the originate path from an OLDER queue entry
     (Redis scheduled-set promotion / crash-orphan reclaim re-enqueue jobs
     that were created before the flag was set).

NULL is "not flagged": the column is NOT NULL DEFAULT FALSE today, but rows
written before the migration backfilled — and any future nullable variant —
must be treated as callable, never as flagged.

``leads.do_not_call`` is NOT ``dnc_entries``. The tenant DNC *list* (keyed by
normalised phone number) is a separate mechanism and is left untouched here;
both must hold.
"""
from __future__ import annotations

import pytest

from app.domain.services.call_guard import CallGuard, GuardCheck


# ─────────────────────────── in-memory fake DB ───────────────────────────
# Models only the eq/in_/is_/order/insert/update surface the code under test
# uses, plus the ``IS NOT TRUE`` predicate this fix introduces.


class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count if count is not None else (
            len(data) if isinstance(data, list) else 0
        )


class _Query:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._filters = []
        self._op = "select"
        self._payload = None
        self._count_mode = None

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

    def limit(self, *_a, **_k):
        # The real adapter still returns an exact COUNT(*) under a LIMIT, so
        # the fake ignores it and keeps counting every match.
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
            if kind == "is":
                token = None if val is None else str(val).strip().upper()
                if token is None and rv is not None:
                    return False
                # SQL three-valued logic: `x IS NOT TRUE` is TRUE for FALSE
                # *and* for NULL; `x IS TRUE` only for TRUE.
                if token == "NOT TRUE" and rv is True:
                    return False
                if token == "TRUE" and rv is not True:
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
            self._db.updates.append(dict(self._payload))
            return _Resp(changed)
        return _Resp([])


class FakeDB:
    def __init__(self):
        self.tables = {"leads": [], "campaigns": [], "dialer_jobs": [], "contact_lists": []}
        self.inserted = []
        self.updates = []

    def table(self, name):
        return _Query(self, name)


def _service(db):
    from app.domain.services.campaign_service import CampaignService

    return CampaignService(db, queue_service=None)


def _seed_lead(db, lead_id, *, do_not_call=..., status="pending"):
    row = {
        "id": lead_id,
        "campaign_id": "c1",
        "tenant_id": "t1",
        "phone_number": f"+1415555{abs(hash(lead_id)) % 10000:04d}",
        "status": status,
        "list_id": None,
        "priority": 5,
        "created_at": "2026-08-28",
    }
    # `...` means the column is absent entirely (pre-migration row shape);
    # None means an explicit SQL NULL. Both must read as "not flagged".
    if do_not_call is not ...:
        row["do_not_call"] = do_not_call
    db.tables["leads"].append(row)
    return row


# ───────────────────── 1. selection boundary ─────────────────────


@pytest.mark.asyncio
async def test_pending_leads_excludes_do_not_call_lead():
    db = FakeDB()
    _seed_lead(db, "FLAGGED", do_not_call=True)
    _seed_lead(db, "CALLABLE", do_not_call=False)

    leads = await _service(db)._get_pending_leads("c1", tenant_id="t1")

    assert {lead["id"] for lead in leads} == {"CALLABLE"}, (
        "a lead the customer flagged do_not_call must never be selected for "
        "dialling"
    )


@pytest.mark.asyncio
async def test_pending_leads_treats_null_and_missing_do_not_call_as_callable():
    db = FakeDB()
    _seed_lead(db, "NULLFLAG", do_not_call=None)
    _seed_lead(db, "NOCOLUMN")

    leads = await _service(db)._get_pending_leads("c1", tenant_id="t1")

    assert {lead["id"] for lead in leads} == {"NULLFLAG", "NOCOLUMN"}, (
        "NULL/absent do_not_call means 'never flagged' — suppressing those "
        "would silently stop a whole campaign"
    )


@pytest.mark.asyncio
async def test_pending_leads_excludes_flagged_lead_stuck_at_calling():
    """`_get_pending_leads` also re-picks leads stranded at 'calling' by a
    crashed run — the flag must win there too."""
    db = FakeDB()
    _seed_lead(db, "FLAGGED", do_not_call=True, status="calling")
    _seed_lead(db, "CALLABLE", do_not_call=False, status="calling")

    leads = await _service(db)._get_pending_leads("c1", tenant_id="t1")

    assert {lead["id"] for lead in leads} == {"CALLABLE"}


# ───────────────────── 2. enqueue + counters ─────────────────────


class _FakeQueue:
    def __init__(self):
        self.enqueued = []

    async def enqueue_job(self, job):
        self.enqueued.append(job)
        return True

    async def get_queue_stats(self):
        return {}

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_start_campaign_never_enqueues_a_flagged_lead_and_counts_only_dialable():
    db = FakeDB()
    db.tables["campaigns"].append(
        {"id": "c1", "tenant_id": "t1", "status": "draft", "direction": "outbound"}
    )
    _seed_lead(db, "FLAGGED", do_not_call=True)
    _seed_lead(db, "CALLABLE-1", do_not_call=False)
    _seed_lead(db, "CALLABLE-2", do_not_call=None)

    queue = _FakeQueue()
    from app.domain.services.campaign_service import CampaignService

    result = await CampaignService(db, queue_service=queue).start_campaign(
        "c1", tenant_id="t1"
    )

    assert result.jobs_enqueued == 2
    assert {job.lead_id for job in queue.enqueued} == {"CALLABLE-1", "CALLABLE-2"}

    # Counter truthfulness: total_leads is the campaign's denominator. A
    # suppressed lead is excluded from it (the same treatment leads on a
    # toggled-off contact list already get) rather than being enqueued and
    # then recorded as an outcome.
    assert db.tables["campaigns"][0]["total_leads"] == 2
    # ... and no dialer job row was written for the flagged lead either.
    assert {row["lead_id"] for row in db.tables["dialer_jobs"]} == {
        "CALLABLE-1",
        "CALLABLE-2",
    }


# ───────────── 3. dial-time defence in depth (CallGuard) ─────────────


class _FakeConn:
    """Answers the two statements ``_check_dnc`` issues, by table."""

    def __init__(self, *, lead_row=None, dnc_row=None):
        self._lead_row = lead_row
        self._dnc_row = dnc_row
        self.queries = []

    async def fetchrow(self, query, *args):
        self.queries.append(query)
        if "FROM leads" in query:
            return self._lead_row
        return self._dnc_row


class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return None


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquireCtx(self._conn)


@pytest.mark.asyncio
async def test_guard_blocks_a_lead_flagged_do_not_call():
    conn = _FakeConn(lead_row={"id": "lead-1"}, dnc_row=None)
    guard = CallGuard(db_pool=_FakePool(conn), redis_client=None)

    result = await guard._check_dnc(
        tenant_id="t1", phone_number="+15551234567", lead_id="lead-1"
    )

    assert result.check == GuardCheck.DNC_CHECK
    assert result.passed is False, (
        "a stale queue entry for a lead flagged do_not_call must still be "
        "blocked at dial time"
    )
    assert result.reason == "lead_marked_do_not_call"
    assert result.details.get("lead_id") == "lead-1"


@pytest.mark.asyncio
async def test_guard_allows_a_lead_that_is_not_flagged():
    conn = _FakeConn(lead_row=None, dnc_row=None)
    guard = CallGuard(db_pool=_FakePool(conn), redis_client=None)

    result = await guard._check_dnc(
        tenant_id="t1", phone_number="+15551234567", lead_id="lead-1"
    )

    assert result.passed is True


@pytest.mark.asyncio
async def test_guard_lead_predicate_is_index_friendly_and_null_safe():
    """The lead lookup must select on the bare column (``AND do_not_call``),
    so the partial index ``idx_leads_do_not_call ON leads (tenant_id) WHERE
    do_not_call`` stays usable, and must be tenant-scoped explicitly (the
    prod app role is superuser+BYPASSRLS, so RLS alone is not enforcement).
    ``x IS TRUE`` semantics also make NULL read as not-flagged for free."""
    conn = _FakeConn(lead_row=None, dnc_row=None)
    guard = CallGuard(db_pool=_FakePool(conn), redis_client=None)

    await guard._check_dnc(
        tenant_id="t1", phone_number="+15551234567", lead_id="lead-1"
    )

    lead_sql = next(q for q in conn.queries if "FROM leads" in q)
    normalized = " ".join(lead_sql.split())
    assert "AND do_not_call" in normalized, (
        "wrapping the column (COALESCE(do_not_call, false) = true) would make "
        "the predicate non-sargable and drop the partial index"
    )
    assert "COALESCE" not in normalized.upper()
    assert "tenant_id = $2" in normalized


@pytest.mark.asyncio
async def test_guard_skips_the_lead_lookup_when_no_lead_id_is_supplied():
    """Non-dialer callers (inbound/manual) pass no lead_id — the guard must
    not issue the leads query at all for them."""
    conn = _FakeConn(lead_row={"id": "lead-1"}, dnc_row=None)
    guard = CallGuard(db_pool=_FakePool(conn), redis_client=None)

    result = await guard._check_dnc(tenant_id="t1", phone_number="+15551234567")

    assert result.passed is True
    assert not any("FROM leads" in q for q in conn.queries)


@pytest.mark.asyncio
async def test_guard_still_blocks_on_the_tenant_dnc_list():
    """The per-lead flag is ADDITIVE — the dnc_entries list check must keep
    working unchanged."""
    conn = _FakeConn(
        lead_row=None,
        dnc_row={"id": "dnc-1", "source": "tenant_upload", "reason": "opt_out"},
    )
    guard = CallGuard(db_pool=_FakePool(conn), redis_client=None)

    result = await guard._check_dnc(
        tenant_id="t1", phone_number="+15551234567", lead_id="lead-1"
    )

    assert result.passed is False
    assert "number_on_dnc_list" in (result.reason or "")


def test_reason_code_maps_onto_the_existing_dnc_block_vocabulary():
    """The reason must classify into the existing user-facing DNC code, not
    a new style — the UI switches on BlockCode."""
    from app.domain.services.dialer.block_reasons import BlockCode, classify

    assert classify("lead_marked_do_not_call").code is BlockCode.DNC


# ─────────── 4. the dialer actually supplies the lead id ───────────


@pytest.mark.asyncio
async def test_dialer_worker_passes_the_lead_id_to_the_guard(monkeypatch):
    """A guard check wired to a signal that is never supplied is the repo's
    recurring trap — assert the dialer really threads lead_id through."""
    from app.domain.models.calling_rules import CallingRules
    from app.domain.models.dialer_job import DialerJob
    from app.workers import dialer_worker as dw_module
    from app.domain.services import call_guard as guard_module

    captured: dict = {}

    class _StubGuard:
        def __init__(self, **_kw):
            pass

        async def evaluate(self, **kwargs):
            captured.update(kwargs)

            class _R:
                decision = guard_module.GuardDecision.ALLOW

            return _R()

    monkeypatch.setattr(guard_module, "CallGuard", _StubGuard)

    worker = dw_module.DialerWorker()
    worker._db_pool = None
    worker._redis = None
    job = DialerJob(
        job_id="job-1",
        campaign_id="camp-1",
        lead_id="lead-9",
        tenant_id="tenant-1",
        phone_number="+15551234567",
    )

    decision = await worker._evaluate_call_guard(job, CallingRules.default())

    assert decision == "allow"
    assert captured.get("lead_id") == "lead-9"
