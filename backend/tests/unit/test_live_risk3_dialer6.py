"""dialer-6: a retry keeps first_speaker / agent_name (carried via call_legs.metadata)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.dialer_job import DialerJob
from app.domain.models.dialer_job import CallOutcome
from app.domain.services.call_service import CallService
from app.workers.dialer_worker import DialerWorker
from tests.unit.test_dialer_worker_call_record import _Conn, _Pool
from tests.unit.test_live_risk_settlement import _SettleConn, _Queue


def _job(**kw) -> DialerJob:
    return DialerJob(
        job_id="11111111-1111-4111-8111-111111111111",
        campaign_id="22222222-2222-4222-8222-222222222222",
        lead_id="33333333-3333-4333-8333-333333333333",
        tenant_id="44444444-4444-4444-8444-444444444444",
        phone_number="+15551234567",
        attempt_number=1,
        **kw,
    )


async def _leg_metadata(job):
    worker = DialerWorker()
    conn = _Conn()
    worker._db_pool = _Pool(conn)
    await worker._create_call_intent(job)
    q, args = next((q, a) for q, a in conn.queries if "INSERT INTO call_legs" in q)
    return json.loads(args[4])


@pytest.mark.asyncio
async def test_leg_metadata_records_first_speaker_and_agent_name():
    meta = await _leg_metadata(_job(first_speaker="user", agent_name="Sam"))
    assert meta["first_speaker"] == "user" and meta["agent_name"] == "Sam"
    assert meta["dialer_attempt_number"] == 1


@pytest.mark.asyncio
async def test_leg_metadata_omits_agent_name_when_unset():
    meta = await _leg_metadata(_job())
    assert "agent_name" not in meta


class _LegConn(_SettleConn):
    def __init__(self, metadata):
        super().__init__()
        self.metadata = metadata
        self.leg_queries = []

    async def fetchrow(self, sql, *args):
        if "FROM call_legs" in sql:
            self.leg_queries.append((sql, args))
            return None if self.metadata is None else {"metadata": self.metadata}
        return await super().fetchrow(sql, *args)


async def _settle(monkeypatch, metadata):
    svc = CallService(db_client=SimpleNamespace())
    monkeypatch.setattr(svc, "_effective_attempt_number", AsyncMock(return_value=1))
    conn = _LegConn(metadata)
    _, retry_args = await svc._handle_job_completion_pooled(
        conn, job_id="job-1", outcome=CallOutcome.BUSY, campaign_id="camp-1",
        lead_id="lead-1", scope_tenant_id="t-1", call_attempt_number=1,
        caller_opted_out=False, call_id="call-1",
    )
    return conn, retry_args[1]


@pytest.mark.asyncio
async def test_busy_callee_first_attempt_retries_callee_first_same_agent(monkeypatch):
    conn, data = await _settle(
        monkeypatch, json.dumps({"first_speaker": "user", "agent_name": "Sam"})
    )
    assert data["first_speaker"] == "user" and data["agent_name"] == "Sam"
    # tenant-scoped through the calls row, for THIS call
    sql, args = conn.leg_queries[0]
    assert args == ("call-1", "t-1") and "c.tenant_id" in sql

    # and the job actually scheduled carries them
    q = _Queue()
    svc = CallService(db_client=SimpleNamespace(), queue_service=q)
    await svc._schedule_retry("job-1", data, CallOutcome.BUSY, "camp-1", "lead-1", "t-1", 1, 60)
    assert (q.jobs[0].first_speaker, q.jobs[0].agent_name) == ("user", "Sam")


@pytest.mark.asyncio
async def test_leg_without_keys_behaves_as_before(monkeypatch):
    _, data = await _settle(monkeypatch, {"job_id": "job-1"})
    assert "first_speaker" not in data and "agent_name" not in data
    _, data = await _settle(monkeypatch, None)
    assert "first_speaker" not in data and data["lead_first_name"] == "Pat"


@pytest.mark.asyncio
async def test_bad_first_speaker_is_ignored(monkeypatch):
    _, data = await _settle(
        monkeypatch, {"first_speaker": "robot", "agent_name": "  "}
    )
    assert "first_speaker" not in data and "agent_name" not in data
