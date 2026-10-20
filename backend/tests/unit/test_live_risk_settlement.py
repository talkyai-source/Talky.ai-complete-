"""Live-risk settlement packet: compliance / outcome / retry findings.

compliance-1  DNC write keeps the UK "(0)" trunk digit (never matches the lead)
compliance-2  mark_as_spam writes only leads.status; settlement overwrites 'dnc'
compliance-4  CallGuard timezone picks the first zone of an ambiguous number
compliance-5  assistant SMS send ignores DNC / opt-out
auth-1        Salesforce callback re-queues an opted-out lead
outcome-1     answered-but-silent call resolves ANSWERED (clock starts at prewarm)
dialer-6      post-answer retry job loses the lead identity
calls-6       late settlement resurrects a removed ('deleted') contact
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.dialer_job import CallOutcome
from app.domain.services.call_service import CallService


# ── compliance-1 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    ["+44 (0)20 4613 2300", "+44(0)2046132300", "+44 ( 0 ) 20 4613 2300"],
)
def test_dnc_form_drops_the_bracketed_uk_trunk_digit(raw):
    from app.domain.services.dnc_service import normalize_e164_for_storage

    # Same value libphonenumber gives the lead at import, so the guard matches.
    assert normalize_e164_for_storage(raw) == "+442046132300"


def test_normalize_e164_digits_unchanged_for_ordinary_numbers():
    from app.domain.services.phone_number_normalizer import normalize_e164_digits

    assert normalize_e164_digits("+1 (415) 555-1234") == "+14155551234"
    assert normalize_e164_digits("4155551234") == "+14155551234"
    assert normalize_e164_digits("(0)2046132300") == "+02046132300"  # no '+': untouched


# ── compliance-4 ────────────────────────────────────────────────────


def test_guard_timezone_refuses_a_multi_clock_number():
    from app.domain.services import phone_timezone

    # Australian mobile: several zones with different clocks -> no single
    # answer, so the guard falls back to the tenant tz instead of zones[0].
    assert phone_timezone.lookup_timezone_sync("+61412345678") is None


def test_guard_timezone_still_resolves_a_single_clock_number():
    from app.domain.services import phone_timezone

    assert phone_timezone.lookup_timezone_sync("+442046132300") == "Europe/London"


# ── outcome-1 ───────────────────────────────────────────────────────


def _silent_session(*, started_s_ago: float, answered_s_ago: float | None):
    cs = SimpleNamespace(
        started_at=datetime.now(timezone.utc) - timedelta(seconds=started_s_ago),
        conversation_history=[],
        conversation_context=SimpleNamespace(goal_achieved=False),
        _tts_terminal_failure_hangup=False,
    )
    vs = SimpleNamespace(
        call_session=cs, _goal_achieved=False, _goal_failed=False,
        _pipeline_failed=False, _amd_voicemail=False,
    )
    if answered_s_ago is not None:
        vs._answered_at_monotonic = time.monotonic() - answered_s_ago
    return vs


def test_silent_pickup_after_a_long_ring_is_no_answer():
    from app.domain.services.telephony.outcome_resolver import resolve_call_outcome

    # Session created 25 s ago (prewarm + ring); callee answered 2 s ago and
    # said nothing: not a conversation, must be redialled.
    vs = _silent_session(started_s_ago=25, answered_s_ago=2)
    assert resolve_call_outcome(vs) is CallOutcome.NO_ANSWER


def test_silent_but_connected_for_a_long_time_stays_answered():
    from app.domain.services.telephony.outcome_resolver import resolve_call_outcome

    vs = _silent_session(started_s_ago=60, answered_s_ago=40)
    assert resolve_call_outcome(vs) is CallOutcome.ANSWERED


def test_no_answer_clock_falls_back_to_session_clock():
    from app.domain.services.telephony.outcome_resolver import resolve_call_outcome

    assert resolve_call_outcome(_silent_session(started_s_ago=2, answered_s_ago=None)) is CallOutcome.NO_ANSWER
    assert resolve_call_outcome(_silent_session(started_s_ago=30, answered_s_ago=None)) is CallOutcome.ANSWERED


# ── calls-6 / compliance-2 (settlement) ─────────────────────────────


class _RecConn:
    def __init__(self):
        self.executed = []

    async def fetchval(self, sql, *args):
        return 0

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))


@pytest.mark.parametrize(
    "outcome",
    [CallOutcome.BUSY, CallOutcome.NO_ANSWER, CallOutcome.ANSWERED, CallOutcome.VOICEMAIL],
)
@pytest.mark.asyncio
async def test_settlement_never_overwrites_dnc_or_deleted(outcome):
    conn = _RecConn()
    svc = CallService(db_client=SimpleNamespace())
    await svc._update_lead_status_pooled(
        conn, "lead-1", outcome, scope_tenant_id="t-1", caller_opted_out=False,
    )
    sql, args = conn.executed[0]
    # 'deleted' is always kept; 'dnc' is kept when the lead carries the
    # do_not_call flag (operator spam-mark), whatever the outcome.
    assert "CASE WHEN status = 'deleted' OR (status = 'dnc' AND (do_not_call OR $6::boolean)) THEN status ELSE $2 END" in sql
    assert args[4] == "t-1"


# ── compliance-2 (mark_as_spam) ─────────────────────────────────────


class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, log, table):
        self.log, self.table, self.payload = log, table, None

    def update(self, payload):
        self.payload = payload
        return self

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def single(self):
        return self

    def execute(self):
        if self.payload is not None:
            self.log.append((self.table, self.payload))
            return _Res([{"id": "lead-1", "phone_number": "+442046132300"}])
        return _Res({"id": "call-1", "lead_id": "lead-1"})


class _Client:
    def __init__(self, pool=None):
        self.log = []
        if pool is not None:
            self.pool = pool

    def table(self, name):
        return _Query(self.log, name)


@pytest.mark.asyncio
async def test_spam_mark_sets_flag_and_writes_the_dnc_list(monkeypatch):
    added = []

    class FakeDNC:
        def __init__(self, pool):
            pass

        async def add(self, **kw):
            added.append(kw)

    monkeypatch.setattr("app.domain.services.dnc_service.DNCService", FakeDNC)
    client = _Client(pool=object())
    svc = CallService(db_client=client)
    out = await svc.mark_as_spam("t-1", call_id="call-1", reason="spam")
    assert out["message"] == "Marked as spam"
    lead_update = [p for t, p in client.log if t == "leads"][0]
    assert lead_update["status"] == "dnc" and lead_update["do_not_call"] is True
    assert added and added[0]["e164"] == "+442046132300" and added[0]["tenant_id"] == "t-1"
    assert added[0]["source"] == "manual_admin"


@pytest.mark.parametrize("reason", ["invalid", "disconnected"])
@pytest.mark.asyncio
async def test_dead_line_marks_do_not_suppress_the_number(reason, monkeypatch):
    added = []

    class FakeDNC:
        def __init__(self, pool):
            pass

        async def add(self, **kw):
            added.append(kw)

    monkeypatch.setattr("app.domain.services.dnc_service.DNCService", FakeDNC)
    client = _Client(pool=object())
    await CallService(db_client=client).mark_as_spam("t-1", call_id="call-1", reason=reason)
    lead_update = [p for t, p in client.log if t == "leads"][0]
    assert lead_update["status"] == "failed" and "do_not_call" not in lead_update
    assert added == []


@pytest.mark.asyncio
async def test_spam_mark_survives_a_failed_dnc_list_write():
    # No pool on the client: the lead flag is still written, nothing raises.
    client = _Client()
    out = await CallService(db_client=client).mark_as_spam("t-1", call_id="call-1", reason="spam")
    assert out is not None
    assert [p for t, p in client.log if t == "leads"][0]["do_not_call"] is True


# ── dialer-6 ────────────────────────────────────────────────────────


class _Queue:
    def __init__(self):
        self.jobs = []

    async def schedule_retry(self, job, delay_seconds):
        self.jobs.append(job)
        return True


@pytest.mark.asyncio
async def test_retry_job_keeps_lead_identity_and_opening_mode():
    q = _Queue()
    svc = CallService(db_client=SimpleNamespace(), queue_service=q)
    await svc._schedule_retry(
        "job-1",
        {"priority": 5, "phone_number": "+442046132300", "first_speaker": "user",
         "agent_name": "Sam", "lead_first_name": "Pat", "lead_last_name": "Lee",
         "lead_company": "ACME"},
        CallOutcome.BUSY, "camp-1", "lead-1", "t-1", 1, 60,
    )
    job = q.jobs[0]
    assert (job.first_speaker, job.agent_name) == ("user", "Sam")
    assert (job.lead_first_name, job.lead_last_name, job.lead_company) == ("Pat", "Lee", "ACME")


class _SettleConn:
    """Enough of a connection for _handle_job_completion_pooled's retry path.

    Behaves like Postgres where it matters: leads has no company column (the
    company lives in custom_fields), and a failed statement outside a
    savepoint aborts the transaction, so every later statement fails.
    """

    def __init__(self, *, leg_error=False):
        self.lead_queries = []
        self.leg_error = leg_error
        self.aborted = False
        self._savepoints = 0

    def transaction(self):
        conn = self

        class _Savepoint:
            async def __aenter__(self):
                conn._savepoints += 1

            async def __aexit__(self, *exc):
                conn._savepoints -= 1
                return False

        return _Savepoint()

    def _fail(self, message):
        if not self._savepoints:
            self.aborted = True
        raise RuntimeError(message)

    def _check(self):
        if self.aborted:
            raise RuntimeError("current transaction is aborted")

    async def fetchrow(self, sql, *args):
        self._check()
        if "FROM dialer_jobs" in sql:
            return {"attempt_number": 1, "tenant_id": "t-1", "priority": 5,
                    "phone_number": "+442046132300"}
        if "FROM leads" in sql:
            self.lead_queries.append((sql, args))
            selected = sql.split("FROM leads")[0].replace("custom_fields->>'company' AS company", "")
            if re.search(r"\bcompany\b", selected):
                self._fail('column "company" does not exist')
            return {"first_name": "Pat", "last_name": "Lee", "company": "ACME"}
        if "FROM call_legs" in sql:
            if self.leg_error:
                self._fail("synthetic call_legs read failure")
            return {"metadata": {"first_speaker": "user", "agent_name": "Sam"}}
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        self._check()
        return "job-1"  # the guarded UPDATE ... RETURNING id


@pytest.mark.asyncio
async def test_settlement_rehydrates_lead_identity_for_the_retry(monkeypatch):
    svc = CallService(db_client=SimpleNamespace())
    monkeypatch.setattr(svc, "_effective_attempt_number", AsyncMock(return_value=1))
    conn = _SettleConn()
    terminal, retry_args = await svc._handle_job_completion_pooled(
        conn, job_id="job-1", outcome=CallOutcome.BUSY, campaign_id="camp-1",
        lead_id="lead-1", scope_tenant_id="t-1", call_attempt_number=1,
        caller_opted_out=False,
    )
    assert terminal is False and retry_args is not None
    job_data = retry_args[1]
    assert job_data["lead_first_name"] == "Pat" and job_data["lead_company"] == "ACME"
    # tenant-scoped read
    assert conn.lead_queries[0][1] == ("lead-1", "t-1")
    # the settlement transaction is still usable afterwards
    assert conn.aborted is False


@pytest.mark.asyncio
async def test_a_failed_optional_identity_read_never_aborts_the_settlement(monkeypatch):
    """Live 2026-10-20: the retry lookup read a column leads does not have.
    The except swallowed the error but Postgres had already aborted the
    transaction, so the rest of the settlement failed. Each optional read now
    runs in a savepoint; the retry is still scheduled, minus the cosmetics."""
    svc = CallService(db_client=SimpleNamespace())
    monkeypatch.setattr(svc, "_effective_attempt_number", AsyncMock(return_value=1))
    conn = _SettleConn(leg_error=True)
    terminal, retry_args = await svc._handle_job_completion_pooled(
        conn, job_id="job-1", outcome=CallOutcome.BUSY, campaign_id="camp-1",
        lead_id="lead-1", scope_tenant_id="t-1", call_attempt_number=1,
        caller_opted_out=False, call_id="call-1",
    )
    assert terminal is False and retry_args is not None
    assert conn.aborted is False
    assert await conn.fetchval("SELECT 1") == "job-1"
    job_data = retry_args[1]
    assert job_data["lead_company"] == "ACME"
    assert "first_speaker" not in job_data


# ── auth-1 ──────────────────────────────────────────────────────────


class _FakeSfDB:
    def __init__(self, lead):
        self.lead = lead
        self.writes = []

    def table(self, name):
        db = self

        class Q:
            def select(self, *_a): return self
            def eq(self, *_a): return self
            def neq(self, *_a): return self
            def limit(self, *_a): return self
            def update(self, payload):
                db.writes.append(payload)
                return self
            def execute(self):
                return SimpleNamespace(data=[db.lead], error=None)

        return Q()


@pytest.mark.parametrize(
    "lead",
    [
        {"id": "l1", "status": "dnc", "do_not_call": False, "custom_fields": {}},
        {"id": "l1", "status": "called", "do_not_call": False,
         "last_call_result": "caller_opt_out", "custom_fields": {}},
    ],
)
@pytest.mark.asyncio
async def test_salesforce_callback_refuses_an_opted_out_lead(lead, monkeypatch):
    import app.api.v1.endpoints.salesforce as sf

    monkeypatch.setattr(sf, "require_owned_outbound_campaign",
                        lambda *a, **k: {"id": "c1", "status": "running", "script_config": {}})
    monkeypatch.setattr("app.api.v1.endpoints.contact_lists.create_contact_list",
                        lambda *a, **k: "list-1")
    db = _FakeSfDB(lead)
    with pytest.raises(sf._CallbackConflict) as exc:
        await sf._enqueue_callback(
            db, tenant_id="t-1", config={"callback_campaign_id": "c1"},
            payload=sf.CallbackRequest(phone="+15550100100"),
        )
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "do_not_call"
    assert db.writes == []  # never promoted back to 'pending'


@pytest.mark.asyncio
async def test_salesforce_callback_refuses_a_number_on_the_dnc_list(monkeypatch):
    import app.api.v1.endpoints.salesforce as sf

    monkeypatch.setattr(sf, "require_owned_outbound_campaign",
                        lambda *a, **k: {"id": "c1", "status": "running", "script_config": {}})

    class DNC:
        def __init__(self, pool): pass
        async def is_on_dnc(self, *, tenant_id, e164):
            assert e164 == "+15550100100"
            return True

    monkeypatch.setattr("app.domain.services.dnc_service.DNCService", DNC)
    db = _FakeSfDB(None)
    db.pool = object()
    with pytest.raises(sf._CallbackConflict) as exc:
        await sf._enqueue_callback(
            db, tenant_id="t-1", config={"callback_campaign_id": "c1"},
            payload=sf.CallbackRequest(phone="+15550100100"),
        )
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "do_not_call"


@pytest.mark.asyncio
async def test_salesforce_callback_fails_closed_when_the_dnc_lookup_errors(monkeypatch):
    import app.api.v1.endpoints.salesforce as sf

    monkeypatch.setattr(sf, "require_owned_outbound_campaign",
                        lambda *a, **k: {"id": "c1", "status": "running", "script_config": {}})

    class DNC:
        def __init__(self, pool): pass
        async def is_on_dnc(self, **_kw): raise RuntimeError("db down")

    monkeypatch.setattr("app.domain.services.dnc_service.DNCService", DNC)
    db = _FakeSfDB(None)
    db.pool = object()
    with pytest.raises(sf._CallbackConflict) as exc:
        await sf._enqueue_callback(
            db, tenant_id="t-1", config={"callback_campaign_id": "c1"},
            payload=sf.CallbackRequest(phone="+15550100100"),
        )
    assert exc.value.status_code == 503


# ── compliance-5 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assistant_sms_is_not_sent_to_an_opted_out_number(monkeypatch):
    from app.infrastructure.assistant.tools import comms

    sender = SimpleNamespace(send_sms=AsyncMock(return_value={"success": True, "message_id": "m"}))
    monkeypatch.setattr("app.services.sms_service.get_sms_service", lambda _db: sender)
    monkeypatch.setattr(comms, "_sms_suppression", AsyncMock(return_value="opted_out"))
    result = await comms.send_sms("t-1", object(), ["+15555550101"], "hello", confirm=True)
    assert result["success"] is False and result["status"] == "failed"
    sender.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_assistant_sms_fails_closed_when_suppression_is_unknown(monkeypatch):
    from app.infrastructure.assistant.tools import comms

    sender = SimpleNamespace(send_sms=AsyncMock())
    monkeypatch.setattr("app.services.sms_service.get_sms_service", lambda _db: sender)
    # A db client with no pool cannot be checked -> unknown -> not sent.
    result = await comms.send_sms("t-1", object(), ["+15555550101"], "hello", confirm=True)
    assert result["success"] is False
    sender.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_suppression_flags_dnc_list_and_lead_flags(monkeypatch):
    from app.infrastructure.assistant.tools import comms

    class DNC:
        listed = False
        def __init__(self, pool): pass
        async def is_on_dnc(self, **_kw): return DNC.listed

    class Conn:
        flagged = False
        async def fetchval(self, sql, *a): return Conn.flagged

    class Ctx:
        async def __aenter__(self): return Conn()
        async def __aexit__(self, *a): return False

    monkeypatch.setattr("app.domain.services.dnc_service.DNCService", DNC)
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", lambda pool, tid: Ctx())
    db = SimpleNamespace(pool=object())
    assert await comms._sms_suppression(db, "t-1", "+15555550101") == "ok"
    Conn.flagged = True
    assert await comms._sms_suppression(db, "t-1", "+15555550101") == "opted_out"
    Conn.flagged, DNC.listed = False, True
    assert await comms._sms_suppression(db, "t-1", "+15555550101") == "opted_out"
