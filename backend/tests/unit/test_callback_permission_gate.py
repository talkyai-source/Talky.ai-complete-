"""A callback for a contact without recorded permission is failed, not shown as queued."""
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from app.services import voice_callback_service as svc

TENANT = "aaaaaaaa-0000-0000-0000-000000000001"
ACTION = "bbbbbbbb-0000-0000-0000-000000000002"
LEAD = "cccccccc-0000-0000-0000-000000000003"
CAMP = "dddddddd-0000-0000-0000-000000000004"
PHONE = "+15551230000"


class FakeConn:
    def __init__(self):
        self.executed = []

    async def fetch(self, sql, *a):
        return [{"id": ACTION, "tenant_id": TENANT}]

    async def fetchrow(self, sql, *a):
        return {
            "direction": "outbound", "campaign_status": "running",
            "script_config": json.dumps({"campaign_brief": {"approved_next_actions": ["schedule_callback"]}}),
            "phone_number": PHONE, "do_not_call": False, "lead_status": "pending", "expired": False,
            "lead_id": LEAD, "campaign_id": CAMP, "scheduled_at": datetime.now(timezone.utc),
            "input_data": json.dumps({"parameters": {"parameters": {"phone": PHONE}}}),
        }

    async def fetchval(self, sql, *a):
        return False

    async def execute(self, sql, *a):
        self.executed.append((" ".join(sql.split()), a))


class FakeQueue:
    def __init__(self):
        self.jobs = []

    async def schedule_job_once(self, job, idempotency_key=None):
        self.jobs.append(job)
        return True

    async def confirm_retry_once(self, key):
        pass


@pytest.fixture
def env(monkeypatch):
    conn = FakeConn()

    @asynccontextmanager
    async def fake_acquire(pool, tenant):
        yield conn

    monkeypatch.setattr(svc, "acquire_with_tenant", fake_acquire)
    return conn, FakeQueue()


def _permission(monkeypatch, missing):
    async def fake_missing(conn, tenant, lead):
        return missing

    monkeypatch.setattr(svc, "permission_missing_for_lead", fake_missing)


def _inserted_jobs(conn):
    return [e for e in conn.executed if "INSERT INTO dialer_jobs" in e[0]]


async def test_gate_on_permission_missing_fails_action_no_job(env, monkeypatch):
    conn, queue = env
    monkeypatch.setenv("CONTACT_PERMISSION_DIAL_GATE", "on")
    _permission(monkeypatch, True)
    await svc.drain_voice_callbacks(object(), queue)
    failed = [e for e in conn.executed if "status='failed'" in e[0]]
    assert len(failed) == 1 and failed[0][1][-1] == "contact_permission_missing"
    assert not _inserted_jobs(conn) and not queue.jobs


async def test_gate_on_permission_present_queues(env, monkeypatch):
    conn, queue = env
    monkeypatch.setenv("CONTACT_PERMISSION_DIAL_GATE", "on")
    _permission(monkeypatch, False)
    await svc.drain_voice_callbacks(object(), queue)
    assert len(_inserted_jobs(conn)) == 1 and len(queue.jobs) == 1
    assert not [e for e in conn.executed if "status='failed'" in e[0]]


@pytest.mark.parametrize("mode", ["off", "warn"])
async def test_gate_off_or_warn_queues_without_lookup(env, monkeypatch, mode):
    conn, queue = env
    monkeypatch.setenv("CONTACT_PERMISSION_DIAL_GATE", mode)

    async def boom(*a):
        raise AssertionError("no permission lookup outside mode on")

    monkeypatch.setattr(svc, "permission_missing_for_lead", boom)
    await svc.drain_voice_callbacks(object(), queue)
    assert len(_inserted_jobs(conn)) == 1 and len(queue.jobs) == 1
    assert not [e for e in conn.executed if "status='failed'" in e[0]]
