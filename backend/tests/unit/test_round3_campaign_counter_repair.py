"""F14-04: rebuild terminal-lead counters without inventing outcomes."""
from __future__ import annotations

import copy
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "repair_campaign_counters.py"
TENANT = UUID("00000000-0000-0000-0000-000000000081")
OTHER = UUID("00000000-0000-0000-0000-000000000082")
CAMPAIGN = UUID("00000000-0000-0000-0000-000000000083")
JOB = UUID("00000000-0000-0000-0000-000000000084")


@pytest.fixture
def script():
    assert SCRIPT.exists(), "F14-04 is missing the dry-run campaign-counter repair"
    spec = importlib.util.spec_from_file_location("round3_counter_repair", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lead(number=1, **changes):
    row = {
        "id": UUID(int=number), "tenant_id": TENANT, "campaign_id": CAMPAIGN,
        "status": "called", "last_call_result": "busy", "call_attempts": 4,
        "job_id": JOB, "job_status": "failed", "job_outcome": "busy",
        "job_error": None, "job_failure_reason": "busy_max_attempts",
        "settled_outcome": "busy", "settled_job_id": JOB,
        "has_settled_call": True,
    }
    row.update(changes)
    return row


@pytest.mark.parametrize("outcome", ["goal_achieved", "answered", "goal_not_achieved", "voicemail", "busy", "no_answer", "timeout", "failed"])
def test_each_post_answer_terminal_outcome_uses_existing_completed_bucket(script, outcome):
    row = lead(job_outcome=outcome, settled_outcome=outcome, last_call_result=outcome)
    assert script.classify_lead(row)["bucket"] == "completed"


@pytest.mark.parametrize("outcome", ["spam", "invalid", "unavailable", "disconnected", "rejected"])
def test_nonreachable_terminal_outcomes_use_failed_bucket(script, outcome):
    row = lead(status="failed", job_status="non_retryable", job_outcome=outcome,
               settled_outcome=outcome, last_call_result=outcome)
    assert script.classify_lead(row)["bucket"] == "failed"


@pytest.mark.parametrize("status", ["pending", "queued", "retry_scheduled", "processing", "calling"])
def test_called_or_contacted_lead_with_active_job_is_not_final(script, status):
    result = script.classify_lead(lead(status="contacted", last_call_result="answered", job_status=status))
    assert result["bucket"] is None and not result["ambiguous"]


def test_pre_answer_failure_is_failed_even_though_post_answer_failed_is_completed(script):
    row = lead(status="failed", job_outcome=None, settled_outcome=None,
               has_settled_call=False, settled_job_id=None,
               job_error="redacted-failure", job_failure_reason="invalid_phone_number")
    assert script.classify_lead(row)["bucket"] == "failed"


@pytest.mark.parametrize("status", ["cancelled", "skipped", "blocked"])
def test_nonattempted_job_is_not_a_finished_call(script, status):
    row = lead(status="pending", job_status=status, job_outcome=None,
               has_settled_call=False, settled_outcome=None)
    assert script.classify_lead(row)["bucket"] is None


def test_deleted_lead_and_never_called_manual_dnc_are_excluded(script):
    assert script.classify_lead(lead(status="deleted"))["bucket"] is None
    row = lead(status="dnc", call_attempts=0, job_id=None, job_status=None,
               has_settled_call=False, settled_outcome=None, job_outcome=None)
    result = script.classify_lead(row)
    assert result["bucket"] is None and not result["ambiguous"]


def test_jobless_direct_settled_call_still_counts_its_lead_once(script):
    row = lead(status="contacted", last_call_result="answered", job_id=None,
               job_status=None, job_outcome=None, settled_job_id=None,
               settled_outcome="answered")
    assert script.classify_lead(row)["bucket"] == "completed"


@pytest.mark.parametrize("changes", [
    {"job_outcome": "future_outcome", "settled_outcome": "future_outcome"},
    {"job_id": None, "job_status": None, "has_settled_call": False},
    {"job_outcome": "busy", "settled_outcome": "rejected"},
    {"job_outcome": "busy", "job_error": "new pre-answer failure"},
])
def test_ambiguous_history_is_reported_instead_of_guessed(script, changes):
    result = script.classify_lead(lead(**changes))
    assert result["ambiguous"] and result["bucket"] is None


class Conn:
    def __init__(self, rows):
        self.campaign = {"id": CAMPAIGN, "tenant_id": TENANT, "status": "stopped",
                         "calls_completed": 99, "calls_failed": 77, "total_leads": 20}
        self.rows = rows
        self.active = False
        self.events = []
        self.queries = []
        self.receipts = []

    @asynccontextmanager
    async def transaction(self, **kwargs):
        before = copy.deepcopy((self.campaign, self.receipts))
        self.events.append(("begin", kwargs))
        try:
            yield
        except BaseException:
            self.campaign, self.receipts = before
            self.events.append(("rollback",))
            raise
        else:
            self.events.append(("commit",))

    async def execute(self, query, *args):
        flat = " ".join(query.split())
        assert flat.startswith(("SELECT set_config(", "SET LOCAL ")), flat
        return "SELECT 1"

    async def fetchrow(self, query, *args):
        flat = " ".join(query.split())
        self.queries.append((flat, args))
        assert "tenant_id" in flat and args[:2] == (TENANT, CAMPAIGN), "Repair crossed tenant/campaign boundary"
        if flat.startswith("SELECT") and "FROM campaigns" in flat:
            if self.campaign is None:
                return None
            return dict(self.campaign)
        if flat.startswith("UPDATE campaigns"):
            assert "total_leads =" not in flat
            assert "audit_lock" in [e[0] for e in self.events]
            self.campaign.update(calls_completed=args[2], calls_failed=args[3])
            self.events.append(("write",))
            return dict(self.campaign)
        raise AssertionError(f"Unexpected SQL: {flat}")

    async def fetchval(self, query, *args):
        flat = " ".join(query.split())
        self.queries.append((flat, args))
        assert args[:2] == (TENANT, CAMPAIGN)
        assert "tenant_id" in flat and "dialer_jobs" in flat and "calls" in flat
        assert "EXISTS" in flat
        return self.active

    async def fetch(self, query, *args):
        flat = " ".join(query.split())
        self.queries.append((flat, args))
        assert args[:2] == (TENANT, CAMPAIGN)
        assert "l.tenant_id" in flat and "l.campaign_id" in flat
        assert "LEFT JOIN LATERAL" in flat and "LIMIT 1" in flat, "One lead must not multiply by its job/call history"
        assert "j.tenant_id" in flat and "c.tenant_id" in flat
        cursor, limit = args[2:]
        return [dict(row) for row in self.rows if row["tenant_id"] == TENANT
                and row["campaign_id"] == CAMPAIGN and (cursor is None or row["id"] > cursor)][:limit]


class Audit:
    def __init__(self, fail=False):
        self.fail = fail

    async def lock_transaction(self, conn):
        conn.events.append(("audit_lock",))

    async def log_in_transaction(self, conn, **event):
        if self.fail:
            raise RuntimeError("audit unavailable")
        assert event["tenant_id"] == TENANT
        conn.receipts.append(event)
        conn.events.append(("audit",))


def fixture_rows():
    return [lead(1), lead(2, status="failed", job_status="non_retryable", job_outcome="rejected", settled_outcome="rejected"),
            lead(3, status="pending", job_status="cancelled", job_outcome=None, has_settled_call=False), lead(4, tenant_id=OTHER)]


@pytest.mark.asyncio
async def test_dry_run_reports_drift_without_writes_or_audit(script):
    conn = Conn(fixture_rows())
    result = await script.repair_campaign(conn, TENANT, CAMPAIGN, batch_size=1)
    assert result["before"] == {"calls_completed": 99, "calls_failed": 77}
    assert result["after"] == {"calls_completed": 1, "calls_failed": 1}
    assert result["delta"] == {"calls_completed": -98, "calls_failed": -76}
    assert not result["applied"]
    assert conn.campaign["calls_completed"] == 99 and not conn.receipts


@pytest.mark.asyncio
async def test_apply_is_atomic_audited_per_lead_and_second_apply_is_noop(script):
    conn = Conn(fixture_rows())
    first = await script.repair_campaign(conn, TENANT, CAMPAIGN, apply=True, audit=Audit(), batch_size=1)
    assert first["applied"] and conn.campaign["calls_completed"] == 1 and conn.campaign["calls_failed"] == 1
    assert conn.campaign["total_leads"] == 20 and len(conn.receipts) == 1
    assert [e[0] for e in conn.events].index("write") < [e[0] for e in conn.events].index("audit") < [e[0] for e in conn.events].index("commit")
    second = await script.repair_campaign(conn, TENANT, CAMPAIGN, apply=True, audit=Audit())
    assert not second["applied"] and second["delta"] == {"calls_completed": 0, "calls_failed": 0}
    assert len(conn.receipts) == 1


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_counter_update(script):
    conn = Conn(fixture_rows())
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await script.repair_campaign(conn, TENANT, CAMPAIGN, apply=True, audit=Audit(fail=True))
    assert conn.campaign["calls_completed"] == 99 and conn.campaign["calls_failed"] == 77
    assert not conn.receipts and conn.events[-1][0] == "rollback"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["running", "active", "ambiguous"])
async def test_apply_refuses_live_or_unprovable_campaign_without_partial_changes(script, reason):
    conn = Conn(fixture_rows())
    if reason == "running":
        conn.campaign["status"] = "running"
    elif reason == "active":
        conn.active = True
    else:
        conn.rows.append(lead(5, job_outcome="unknown", settled_outcome="unknown"))
    result = await script.repair_campaign(conn, TENANT, CAMPAIGN, apply=True, audit=Audit())
    assert not result["applied"] and result["blocked"]
    assert conn.campaign["calls_completed"] == 99 and not conn.receipts


@pytest.mark.asyncio
async def test_wrong_tenant_campaign_is_not_repaired(script):
    conn = Conn([])
    conn.campaign = None
    result = await script.repair_campaign(conn, TENANT, CAMPAIGN, apply=True, audit=Audit())
    assert result["blocked"] == "campaign_not_found" and not conn.receipts


def test_cli_is_dry_run_by_default_and_apply_is_explicit(script):
    args = script.build_parser().parse_args(["--tenant", str(TENANT), "--campaign", str(CAMPAIGN)])
    assert args.apply is False
    assert script.build_parser().parse_args(["--tenant", str(TENANT), "--campaign", str(CAMPAIGN), "--apply"]).apply is True


@pytest.mark.asyncio
async def test_apply_requires_audit_before_any_query(script):
    conn = Conn(fixture_rows())
    with pytest.raises(ValueError, match="audit"):
        await script.repair_campaign(conn, TENANT, CAMPAIGN, apply=True)
    assert not conn.queries
