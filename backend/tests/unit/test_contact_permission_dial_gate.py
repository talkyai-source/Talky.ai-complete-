"""CONTACT_PERMISSION_DIAL_GATE: an outbound campaign call is placed only when
the contact's list carries a calling-permission confirmation.

The switch is read at call time and is OFF by default (the owner has not yet
decided what happens to contacts that pre-date confirmations). Switched off,
enqueue and dial must behave exactly as before. Switched on:

* enqueue (``CampaignService._get_pending_leads``): contacts whose list is not
  confirmed -- or that have no list at all -- are skipped the way contacts of a
  switched-off list are skipped: no job, nothing written, still pending;
* dial time (``DialerWorker._stale_payload_reason``): a job queued earlier is
  refused with ``contact_permission_missing``, without losing an attempt and
  without failing the contact, so it dials after the list is confirmed.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app.domain.models.dialer_job import JobStatus
from app.domain.services.dialer import block_state
from app.domain.services.dialer.block_reasons import (
    SELF_CLEARING_CODES,
    BlockCode,
    classify,
)
from app.domain.services.queue_service import DialerQueueService
from app.workers.dialer_worker import DialerCallIntent, DialerWorker
from tests.unit.test_w1_dialer_job_lifecycle import (
    TENANT_A,
    TENANT_B,
    _conn_pool,
    _job,
    _published_codes,
    _worker,
)

GATE = "CONTACT_PERMISSION_DIAL_GATE"
REASON = "contact_permission_missing"
CONFIRMED_AT = "2026-11-16T10:00:00+00:00"


@pytest.fixture(autouse=True)
def _gate_unset(monkeypatch):
    monkeypatch.delenv(GATE, raising=False)


def _gate_on(monkeypatch):
    monkeypatch.setenv(GATE, "1")


# ═══ 1. the switch ═══════════════════════════════════════════════════════


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes", "on", "ON"])
def test_switch_turns_on_only_for_the_documented_words(monkeypatch, value):
    from app.domain.services.dialer.block_reasons import contact_permission_dial_gate_enabled

    monkeypatch.setenv(GATE, value)
    assert contact_permission_dial_gate_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "2", "enabled", "y"])
def test_switch_is_off_for_everything_else(monkeypatch, value):
    from app.domain.services.dialer.block_reasons import contact_permission_dial_gate_enabled

    monkeypatch.setenv(GATE, value)
    assert contact_permission_dial_gate_enabled() is False


def test_switch_is_off_when_unset_and_is_read_at_call_time(monkeypatch):
    from app.domain.services.dialer.block_reasons import contact_permission_dial_gate_enabled

    assert contact_permission_dial_gate_enabled() is False
    monkeypatch.setenv(GATE, "on")
    assert contact_permission_dial_gate_enabled() is True
    monkeypatch.setenv(GATE, "off")
    assert contact_permission_dial_gate_enabled() is False


# ═══ 2. enqueue / campaign start ═════════════════════════════════════════


class _Resp:
    def __init__(self, data):
        self.data = data
        self.count = len(data)


class _Query:
    def __init__(self, db, table):
        self._db, self._table, self._filters, self._op = db, table, [], "select"

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def update(self, data):
        self._op = "update"
        self._db.updates.append(dict(data))
        return self

    def insert(self, data):
        self._op = "insert"
        self._db.inserts.append(data)
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
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
        return self

    def _match(self, row):
        for kind, col, val in self._filters:
            rv = row.get(col)
            if kind == "eq" and rv != val:
                return False
            if kind == "in" and rv not in val:
                return False
            if kind == "is" and str(val).upper() == "NOT TRUE" and rv is True:
                return False
            if kind == "is" and str(val).upper() == "TRUE" and rv is not True:
                return False
        return True

    def execute(self):
        if self._db.fail_tables and self._table in self._db.fail_tables:
            raise RuntimeError("boom")
        rows = self._db.tables.setdefault(self._table, [])
        return _Resp([dict(r) for r in rows if self._match(r)])


class _FakeDB:
    def __init__(self, fail_tables=()):
        self.tables = {"leads": [], "contact_lists": []}
        self.updates, self.inserts = [], []
        self.fail_tables = set(fail_tables)

    def table(self, name):
        return _Query(self, name)


def _service(db):
    from app.domain.services.campaign_service import CampaignService

    return CampaignService(db, queue_service=None)


def _seed(db):
    """Four pending leads: confirmed list, unconfirmed list, no list, and a list
    that is confirmed only in ANOTHER tenant but shares the id."""
    def lead(lid, list_id):
        return {
            "id": lid, "campaign_id": "c1", "tenant_id": "t1", "status": "pending",
            "phone_number": f"+1415555{abs(hash(lid)) % 9000 + 1000}",
            "list_id": list_id, "priority": 5, "created_at": "2026-11-16",
        }

    db.tables["leads"] = [
        lead("CONF", "L-OK"), lead("UNCONF", "L-NO"), lead("NOLIST", None),
        lead("XTENANT", "L-X"),
    ]
    db.tables["contact_lists"] = [
        {"id": "L-OK", "tenant_id": "t1", "campaign_id": "c1", "is_active": True,
         "consent_confirmed_at": CONFIRMED_AT},
        {"id": "L-NO", "tenant_id": "t1", "campaign_id": "c1", "is_active": True,
         "consent_confirmed_at": None},
        # same list id, but the confirmation belongs to another tenant's row
        {"id": "L-X", "tenant_id": "t2", "campaign_id": "c1", "is_active": True,
         "consent_confirmed_at": CONFIRMED_AT},
        {"id": "L-X", "tenant_id": "t1", "campaign_id": "c1", "is_active": True,
         "consent_confirmed_at": None},
    ]


async def _pending_ids(db, **kw):
    return {l["id"] for l in await _service(db)._get_pending_leads("c1", **kw)}


@pytest.mark.asyncio
async def test_switch_off_enqueue_is_unchanged_for_every_kind_of_contact():
    db = _FakeDB()
    _seed(db)
    assert await _pending_ids(db) == {"CONF", "UNCONF", "NOLIST", "XTENANT"}


@pytest.mark.asyncio
async def test_switch_off_makes_no_contact_list_confirmation_lookup(monkeypatch):
    db = _FakeDB(fail_tables={"contact_lists"})  # any contact_lists read would raise
    _seed(db)
    # the inactive-list lookup swallows its own error (fail-safe); the point is
    # that the new lookup, which would fail closed, is not made at all
    assert await _pending_ids(db) == {"CONF", "UNCONF", "NOLIST", "XTENANT"}


@pytest.mark.asyncio
async def test_switch_on_only_contacts_of_a_confirmed_list_are_selected(monkeypatch):
    _gate_on(monkeypatch)
    db = _FakeDB()
    _seed(db)
    # unconfirmed list, no list, and the other tenant's same-id list are all out
    assert await _pending_ids(db, ) == {"CONF"}


@pytest.mark.asyncio
async def test_switch_on_skipped_contacts_are_left_untouched_and_pending(monkeypatch):
    _gate_on(monkeypatch)
    db = _FakeDB()
    _seed(db)
    await _pending_ids(db)
    assert db.updates == [] and db.inserts == []
    assert {l["status"] for l in db.tables["leads"]} == {"pending"}


@pytest.mark.asyncio
async def test_switch_on_contact_is_picked_up_once_its_list_is_confirmed(monkeypatch):
    _gate_on(monkeypatch)
    db = _FakeDB()
    _seed(db)
    assert "UNCONF" not in await _pending_ids(db)
    for row in db.tables["contact_lists"]:
        if row["id"] == "L-NO":
            row["consent_confirmed_at"] = CONFIRMED_AT
    assert "UNCONF" in await _pending_ids(db)


@pytest.mark.asyncio
async def test_switch_on_single_list_dial_also_requires_a_confirmed_list(monkeypatch):
    _gate_on(monkeypatch)
    db = _FakeDB()
    _seed(db)
    assert await _pending_ids(db, list_id="L-NO") == set()
    assert await _pending_ids(db, list_id="L-OK") == {"CONF"}


@pytest.mark.asyncio
async def test_switch_on_an_unreadable_confirmation_lookup_at_start_is_a_retryable_error(monkeypatch):
    # At Start a failed lookup is an error the customer can retry (503), not a
    # mass hold-back that reads as "no permission". The dial-time check is separate.
    from app.domain.services.campaign_service import CampaignError

    _gate_on(monkeypatch)
    db = _FakeDB(fail_tables={"contact_lists"})
    _seed(db)
    with pytest.raises(CampaignError) as raised:
        await _pending_ids(db)
    assert raised.value.status_code == 503


@pytest.mark.asyncio
async def test_switch_on_confirmation_lookup_is_tenant_scoped(monkeypatch):
    _gate_on(monkeypatch)
    seen = []
    db = _FakeDB()
    _seed(db)
    original = db.table

    def spy(name):
        q = original(name)
        if name == "contact_lists":
            seen.append(q)
        return q

    db.table = spy
    await _service(db)._get_pending_leads("c1", tenant_id="t1")
    assert any(("eq", "tenant_id", "t1") in q._filters for q in seen)


# ═══ 3. dial time ════════════════════════════════════════════════════════


def _row(*, job="queued", lead_row="lead", lead="pending", dnc=False,
         list_id="L1", confirmed=CONFIRMED_AT):
    return {
        "job_status": job, "lead_row_id": lead_row, "lead_status": lead,
        "lead_do_not_call": dnc, "lead_list_id": list_id,
        "list_confirmed_at": confirmed,
    }


STATES = [
    ("confirmed", _row(), None),
    ("unconfirmed", _row(confirmed=None), REASON),
    ("no_list", _row(list_id=None, confirmed=None), REASON),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,row,_expected", STATES)
async def test_switch_off_dial_time_check_is_unchanged(name, row, _expected):
    w = DialerWorker()
    _conn_pool(w, fetchrow=row)
    assert await w._stale_payload_reason(_job()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("name,row,expected", STATES)
async def test_switch_on_dial_time_check(monkeypatch, name, row, expected):
    _gate_on(monkeypatch)
    w = DialerWorker()
    conn = _conn_pool(w, fetchrow=row)
    assert await w._stale_payload_reason(_job()) == expected
    # still ONE tenant-scoped query, and the list join is tenant-scoped too
    assert conn.fetchrow.await_count == 1
    sql = conn.fetchrow.call_args.args[0]
    assert "cl.tenant_id = $2::uuid" in sql and "l.tenant_id = $2::uuid" in sql
    assert conn.fetchrow.call_args.args[1:] == (
        _job().job_id, TENANT_A, _job().lead_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("row,expected", [
    (_row(job="cancelled", confirmed=None), "job_not_active"),
    (_row(lead="deleted", confirmed=None), "lead_not_dialable"),
    (_row(dnc=True, confirmed=None), "lead_not_dialable"),
    (_row(lead_row=None, lead=None, list_id=None, confirmed=None), "lead_not_dialable"),
])
async def test_switch_on_existing_reasons_keep_their_precedence(monkeypatch, row, expected):
    _gate_on(monkeypatch)
    w = DialerWorker()
    _conn_pool(w, fetchrow=row)
    assert await w._stale_payload_reason(_job()) == expected


@pytest.mark.asyncio
async def test_a_queued_job_is_refused_without_losing_the_attempt_or_the_contact(monkeypatch):
    w = _worker()
    w._stale_payload_reason = AsyncMock(return_value=REASON)
    job = _job(attempt=2)
    await w.process_job(job)

    w._create_call_intent.assert_not_awaited()
    w._make_call.assert_not_awaited()
    # terminal for THIS payload (not a re-defer), reason not in the non-terminal set
    w.queue_service.mark_skipped.assert_awaited_once()
    assert w.queue_service.mark_skipped.call_args.kwargs["reason"] == REASON
    assert REASON not in DialerQueueService.NON_TERMINAL_SKIP_REASONS
    w.queue_service._redefer_inflight.assert_not_awaited()
    # the job is ended SKIPPED, never FAILED: its slot is freed for the restart
    args, kwargs = w._update_job_status.call_args
    assert args[1] is JobStatus.SKIPPED and kwargs["error"] == REASON
    assert JobStatus.FAILED not in [c.args[1] for c in w._update_job_status.call_args_list]
    # attempt untouched, no retry, no failure classification, no DNC, lead untouched
    assert job.attempt_number == 2
    w._persist_job_attempt_number.assert_not_awaited()
    w.queue_service.schedule_retry.assert_not_awaited()
    w._record_job_failure_classification.assert_not_awaited()
    w._emit_unknown_bridge_code_alert.assert_not_awaited()
    w._update_lead_status.assert_not_awaited()
    # and it shows the user its own reason
    assert _published_codes(w) == [BlockCode("contact_permission_missing")]


@pytest.mark.asyncio
async def test_a_failed_job_write_does_not_fall_into_the_failure_boundary():
    w = _worker()
    w._stale_payload_reason = AsyncMock(return_value=REASON)
    w._update_job_status = AsyncMock(side_effect=RuntimeError("0 rows"))
    await w.process_job(_job())
    w._update_lead_status.assert_not_awaited()
    w.queue_service.schedule_retry.assert_not_awaited()
    w._make_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_refused_payload_leaves_redis_for_good():
    fakeredis = pytest.importorskip("fakeredis")
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    queue = DialerQueueService(redis_client=redis)
    await queue.initialize()
    job = _job()
    assert await queue.enqueue_job(job)
    dequeued = await queue.dequeue_job(tenant_ids=[TENANT_A])
    w = _worker()
    w.queue_service = queue
    w._stale_payload_reason = AsyncMock(return_value=REASON)
    await w.process_job(dequeued)
    assert await redis.zcard(DialerQueueService.SCHEDULED_ZSET) == 0
    assert await redis.llen(DialerQueueService.INFLIGHT_LIST) == 0
    assert await redis.hlen(DialerQueueService.INFLIGHT_HASH) == 0


@pytest.mark.asyncio
async def test_after_the_list_is_confirmed_the_same_contact_dials_normally(monkeypatch):
    _gate_on(monkeypatch)
    w = _worker()
    w._stale_payload_reason = type(w)._stale_payload_reason.__get__(w)
    conn = _conn_pool(w, fetchrow=_row(confirmed=None))
    await w.process_job(_job())
    w._make_call.assert_not_awaited()

    # a person confirms the list; the campaign is started again -> a fresh job
    conn.fetchrow.return_value = _row(confirmed=CONFIRMED_AT)
    job = _job()
    await w.process_job(job)
    w._make_call.assert_awaited_once()
    assert job.attempt_number == 1


@pytest.mark.asyncio
async def test_paused_campaign_drops_the_unconfirmed_job_like_any_stale_payload():
    w = _worker()
    w._get_campaign_status = AsyncMock(return_value="paused")
    w._stale_payload_reason = AsyncMock(return_value=REASON)
    await w.process_job(_job())
    w.queue_service._redefer_inflight.assert_not_awaited()
    assert w.queue_service.mark_skipped.call_args.kwargs["reason"] == REASON
    w._update_lead_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_unclaimed_call_intent_is_not_originated_when_permission_is_missing():
    w = _worker()
    w._stale_payload_reason = AsyncMock(return_value=REASON)
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)
    await w._resume_existing_call_intent(_job(), intent)
    w._make_call.assert_not_awaited()
    assert w._mark_call_intent_not_originated.call_args.kwargs["reason"] == (
        f"stale_payload:{REASON}"
    )
    assert w.queue_service.mark_skipped.call_args.kwargs["reason"] == REASON


@pytest.mark.asyncio
async def test_a_refused_intent_row_does_not_count_against_the_daily_cap():
    w = DialerWorker()
    conn = _conn_pool(w, fetchval=0)
    await w._get_lead_attempts_today("lead-1")
    assert f"stale_payload:{REASON}" in conn.fetchval.call_args.args[0]


# ═══ 4. the reason is registered everywhere ══════════════════════════════


def test_reason_classifies_with_its_own_code_and_words():
    for raw in (REASON, f"stale_payload:{REASON}"):
        reason = classify(raw)
        assert reason.code is BlockCode("contact_permission_missing")
        assert reason.severity == "warning" and reason.stage == "contact"
        assert reason.message and not reason.message.startswith("Calls are paused")
    assert block_state._title_for(classify(REASON)) == "Permission to call not confirmed"


def test_reason_is_not_self_clearing_because_only_a_person_clears_it():
    assert BlockCode("contact_permission_missing") not in SELF_CLEARING_CODES
    assert classify(REASON).is_self_clearing is False


def test_reason_has_advice_in_the_exact_user_words():
    from app.domain.services import call_issue_advice

    for raw in (REASON, f"stale_payload:{REASON}", f"lead {REASON}"):
        advice = call_issue_advice.advise(raw)
        assert advice is not call_issue_advice._FALLBACK
        assert advice.title == "Permission to call not confirmed"
        assert advice.suggestion == (
            "Confirm permission on the contact list. While the campaign runs, its "
            "contacts are then queued straight away."
        )
        assert "start the campaign again" not in advice.suggestion
        assert advice.severity == "warning"


def test_reason_does_not_hide_behind_an_earlier_advice_rule():
    from app.domain.services import call_issue_advice

    assert call_issue_advice.advise(REASON).title != "Contact can't be dialed"


def test_reason_never_reaches_the_event_stream_as_critical():
    # error -> "critical" is what raises operator alerts; this reason must not
    assert block_state._SEVERITY_MAP[classify(REASON).severity] != "critical"


def test_call_issues_sql_excludes_the_reason_like_platform_gap():
    import inspect

    from app.api.v1.endpoints import calls

    # guard on the source: the exclusion is one SQL string inside the endpoint
    src = inspect.getsource(calls.list_call_issues)
    lists = [part.split(")")[0] for part in src.split("NOT IN (")[1:]]
    pacing = [p for p in lists if "'platform_gap'" in p]
    assert pacing and f"'{REASON}'" in pacing[0]


# ═══ 5. Start: withheld contacts must not trigger the restart reset ═════════

from tests.unit.test_campaign_start_dispatch_truth import _DB as _StartDB  # noqa: E402
from tests.unit.test_campaign_start_dispatch_truth import _Queue as _StartQueue  # noqa: E402

NOTE_MANY = (
    "{n} contacts were not queued because no permission to call is recorded for "
    "their list. Confirm permission on the contact list; while the campaign runs, "
    "they are then queued straight away."
)
NOTE_ONE = (
    "1 contact was not queued because no permission to call is recorded for "
    "its list. Confirm permission on the contact list; while the campaign runs, "
    "they are then queued straight away."
)
PLAIN = "Campaign campaign-1 started"
PLAIN_EMPTY = "Campaign campaign-1 started (no pending leads)"


def _start_db(*, pending_no=0, pending_ok=0, failed_no=0, failed_ok=0):
    db = _StartDB(lead_count=0)
    n = 0

    def add(status, list_id):
        nonlocal n
        n += 1
        db.tables["leads"].append({
            "id": f"lead-{n}", "tenant_id": "tenant-1", "campaign_id": "campaign-1",
            "phone_number": f"+1415555{2000 + n}", "priority": 5, "status": status,
            "do_not_call": False, "list_id": list_id,
            "created_at": "2026-09-03T00:00:00+00:00",
        })

    for _ in range(pending_no):
        add("pending", "L-NO")
    for _ in range(pending_ok):
        add("pending", "L-OK")
    for _ in range(failed_no):
        add("failed", "L-NO")
    for _ in range(failed_ok):
        add("failed", "L-OK")
    db.tables["contact_lists"] = [
        {"id": "L-OK", "tenant_id": "tenant-1", "campaign_id": "campaign-1",
         "is_active": True, "consent_confirmed_at": CONFIRMED_AT},
        {"id": "L-NO", "tenant_id": "tenant-1", "campaign_id": "campaign-1",
         "is_active": True, "consent_confirmed_at": None},
    ]
    return db


async def _start(db, monkeypatch, **kw):
    from app.domain.services.campaign_service import CampaignService

    svc = CampaignService(db, queue_service=_StartQueue([True] * 20))
    resets = []
    real = svc._reset_leads_for_restart

    async def spy(*a, **k):
        resets.append(1)
        return await real(*a, **k)

    monkeypatch.setattr(svc, "_reset_leads_for_restart", spy)
    result = await svc.start_campaign("campaign-1", tenant_id="tenant-1", **kw)
    return result, resets


def _statuses(db):
    return [l["status"] for l in db.tables["leads"]]


@pytest.mark.asyncio
async def test_on_withheld_pending_contacts_block_the_reset_of_old_failed_ones(monkeypatch):
    _gate_on(monkeypatch)
    db = _start_db(pending_no=3, failed_no=2)
    result, resets = await _start(db, monkeypatch)
    assert result.jobs_enqueued == 0 and db.tables["dialer_jobs"] == []
    assert resets == []
    assert _statuses(db).count("failed") == 2 and _statuses(db).count("pending") == 3
    assert result.message == f"{PLAIN_EMPTY} {NOTE_MANY.format(n=3)}"


@pytest.mark.asyncio
async def test_on_one_withheld_contact_uses_the_singular_sentence(monkeypatch):
    _gate_on(monkeypatch)
    result, _ = await _start(_start_db(pending_no=1), monkeypatch)
    assert result.message == f"{PLAIN_EMPTY} {NOTE_ONE}"


@pytest.mark.asyncio
async def test_on_no_pending_at_all_the_restart_reset_still_runs_then_the_check_applies(monkeypatch):
    _gate_on(monkeypatch)
    ok = _start_db(failed_ok=2)
    result, resets = await _start(ok, monkeypatch)
    assert resets == [1] and result.jobs_enqueued == 2
    assert result.message == PLAIN

    no = _start_db(failed_no=2)
    result, resets = await _start(no, monkeypatch)
    assert resets == [1] and result.jobs_enqueued == 0
    assert result.message == f"{PLAIN_EMPTY} {NOTE_MANY.format(n=2)}"


@pytest.mark.asyncio
async def test_on_mixed_lists_queue_the_confirmed_and_report_the_withheld(monkeypatch):
    _gate_on(monkeypatch)
    db = _start_db(pending_ok=2, pending_no=3)
    result, resets = await _start(db, monkeypatch)
    assert result.jobs_enqueued == 2 and resets == []
    assert result.message == f"{PLAIN} {NOTE_MANY.format(n=3)}"


@pytest.mark.asyncio
async def test_on_single_list_start_reports_the_withheld_list(monkeypatch):
    _gate_on(monkeypatch)
    result, resets = await _start(_start_db(pending_no=2), monkeypatch, list_id="L-NO")
    assert result.jobs_enqueued == 0 and resets == []
    assert result.message == f"{PLAIN_EMPTY} {NOTE_MANY.format(n=2)}"
    assert result.jobs_withheld == 2


@pytest.mark.asyncio
async def test_off_the_same_setups_behave_and_read_exactly_as_before(monkeypatch):
    db = _start_db(pending_no=3, failed_no=2)
    result, resets = await _start(db, monkeypatch)
    assert result.jobs_enqueued == 3 and resets == [] and result.message == PLAIN
    assert result.jobs_withheld == 0

    result, resets = await _start(_start_db(failed_no=2), monkeypatch)
    assert resets == [1] and result.jobs_enqueued == 2 and result.message == PLAIN

    result, resets = await _start(_start_db(pending_ok=2, pending_no=3), monkeypatch)
    assert result.jobs_enqueued == 5 and resets == [] and result.message == PLAIN

    result, resets = await _start(_start_db(), monkeypatch)
    assert result.message == PLAIN_EMPTY


@pytest.mark.asyncio
async def test_off_no_withheld_count_is_computed():
    db = _start_db(pending_no=3)
    out: list = []
    leads = await _service(db)._get_pending_leads("campaign-1", withheld_out=out)
    assert len(leads) == 3 and out == []


@pytest.mark.asyncio
async def test_other_callers_get_the_same_leads_with_or_without_the_count(monkeypatch):
    _gate_on(monkeypatch)
    db = _start_db(pending_ok=1, pending_no=2)
    out: list = []
    with_count = await _service(db)._get_pending_leads("campaign-1", withheld_out=out)
    without = await _service(db)._get_pending_leads("campaign-1")
    assert [l["id"] for l in with_count] == [l["id"] for l in without]
    assert sum(out) == 2


# --------------------------------------------- 6. "Call this list" says why
async def _call_one_list(monkeypatch, withheld):
    from app.api.v1.endpoints import campaigns as campaigns_ep
    from app.api.v1.endpoints import contact_lists as lists_ep
    from app.domain.services.campaign_service import StartCampaignResult
    from tests.unit.test_campaign_start_dispatch_truth import _Query

    class _Service:
        async def start_campaign(self, **_kwargs):
            return StartCampaignResult(
                success=True, message="x", jobs_enqueued=0, campaign_id="campaign-1",
                jobs_withheld=withheld,
            )

    class _ListDB:
        def table(self, name):
            return _Query(self, name)

        tables = {
            "contact_lists": [{
                "id": "list-1", "tenant_id": "tenant-1", "campaign_id": "campaign-1",
                "name": "List", "is_active": True,
            }],
            "leads": [],
        }
        dialer_insert_error = None

    class _User:
        tenant_id = "tenant-1"

    monkeypatch.setattr(
        lists_ep, "require_owned_outbound_campaign",
        lambda *_a, **_k: {"id": "campaign-1", "direction": "outbound"},
    )
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: _Service())
    return await lists_ep.call_contact_list("list-1", _User(), _ListDB())


@pytest.mark.asyncio
async def test_call_this_list_says_why_when_its_contacts_were_withheld(monkeypatch):
    out = await _call_one_list(monkeypatch, withheld=3)
    assert out.jobs_enqueued == 0
    assert out.message.startswith("List activated")
    assert "3 contacts were not queued because no permission to call is recorded for their list." in out.message


@pytest.mark.asyncio
async def test_call_this_list_message_is_unchanged_when_nothing_was_withheld(monkeypatch):
    out = await _call_one_list(monkeypatch, withheld=0)
    assert out.message == "List activated — 0 contact(s) queued for dialing."
