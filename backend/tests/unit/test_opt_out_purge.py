"""Unit tests for Phase-3d in-call opt-out → DNC purge.

Covers the action-envelope parsing (does the LLM's do_not_call flag come
through?) and the purge orchestration (DNC + job cancel + lead mark, all
best-effort and independent).
"""
import json

import pytest

from app.domain.services.end_session_action import parse_end_session_action
from app.domain.services.dialer.opt_out import purge_lead_on_opt_out, OPT_OUT_REASON


# ── action envelope: do_not_call flag ─────────────────────────────
def test_plain_end_session_has_no_opt_out():
    d = parse_end_session_action(json.dumps({
        "action": "end_session", "reason": "user_goodbye", "farewell": "Bye",
    }))
    assert d is not None
    assert d["do_not_call"] is False


def test_opt_out_flag_true_bool():
    d = parse_end_session_action(json.dumps({
        "action": "end_session", "reason": "user_done",
        "farewell": "Removed.", "do_not_call": True,
    }))
    assert d["do_not_call"] is True
    assert d["farewell"] == "Removed."


@pytest.mark.parametrize("val,expected", [
    ("true", True), ("yes", True), ("1", True),
    ("false", False), ("no", False), ("", False),
])
def test_opt_out_flag_string_spellings(val, expected):
    d = parse_end_session_action(json.dumps({
        "action": "end_session", "do_not_call": val,
    }))
    assert d["do_not_call"] is expected


# ── purge orchestration ───────────────────────────────────────────
class _FakeDNCConn:
    async def fetchrow(self, *a, **k):
        return {
            "id": "1", "tenant_id": "t", "normalized_number": "+15551234567",
            "source": "caller_opt_out", "reason": "x",
            "expires_at": None, "created_at": None,
        }
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False


class _FakeDNCPool:
    def __init__(self):
        self.acquired = 0
    def acquire(self):
        self.acquired += 1
        return _FakeDNCConn()


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, cap, rows):
        self.cap = cap
        self._rows = rows
    def update(self, vals):
        self.cap.setdefault("updates", []).append(vals)
        return self
    def eq(self, c, v):
        self.cap.setdefault("eq", []).append((c, v))
        return self
    def in_(self, c, v):
        self.cap["in"] = (c, list(v))
        return self
    def execute(self):
        return _FakeResult(self._rows)


class _FakeClient:
    def __init__(self, rows):
        self.cap = {}
        self._rows = rows
    def table(self, name):
        self.cap.setdefault("tables", []).append(name)
        return _FakeQuery(self.cap, self._rows)


@pytest.mark.asyncio
async def test_purge_runs_all_three_steps():
    pool = _FakeDNCPool()
    client = _FakeClient(rows=[{"id": "j1"}, {"id": "j2"}])
    res = await purge_lead_on_opt_out(
        db_pool=pool, db_client=client,
        tenant_id="t", lead_id="lead-1", phone_number="+1 (555) 123-4567",
        call_id="call-9",
    )
    assert res["dnc_added"] is True
    assert res["jobs_cancelled"] == 2
    assert res["lead_marked"] is True
    # dialer_jobs cancel + leads update both happened.
    assert "dialer_jobs" in client.cap["tables"]
    assert "leads" in client.cap["tables"]
    # the cancel used the opt-out reason.
    assert any(u.get("failure_reason") == OPT_OUT_REASON for u in client.cap["updates"])
    assert any(u.get("status") == "dnc" for u in client.cap["updates"])


@pytest.mark.asyncio
async def test_purge_is_resilient_to_missing_phone():
    # No phone → DNC step skipped, but jobs + lead still handled.
    client = _FakeClient(rows=[{"id": "j1"}])
    res = await purge_lead_on_opt_out(
        db_pool=None, db_client=client,
        tenant_id="t", lead_id="lead-1", phone_number=None,
    )
    assert res["dnc_added"] is False
    assert res["jobs_cancelled"] == 1
    assert res["lead_marked"] is True


@pytest.mark.asyncio
async def test_purge_noop_with_no_identifiers():
    res = await purge_lead_on_opt_out(
        db_pool=None, db_client=None,
        tenant_id=None, lead_id=None, phone_number=None,
    )
    assert res == {"dnc_added": False, "jobs_cancelled": 0, "lead_marked": False}
