"""Regression coverage for the dialer's durable pre-provider call row."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.domain.models.dialer_job import DialerJob
from app.workers.dialer_worker import DialerWorker


class _Conn:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.queries = []

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, query, *args):
        self.queries.append((" ".join(query.split()), args))
        return "INSERT 0 1"

    async def fetchrow(self, query, *args):
        self.queries.append((" ".join(query.split()), args))
        if self.fail:
            raise RuntimeError("db down")
        return {
            "id": args[0],
            "talklee_call_id": args[5],
            "status": "initiated",
            "provider_call_id": None,
        }


class _Pool:
    def __init__(self, conn) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def _job() -> DialerJob:
    return DialerJob(
        job_id="11111111-1111-4111-8111-111111111111",
        campaign_id="22222222-2222-4222-8222-222222222222",
        lead_id="33333333-3333-4333-8333-333333333333",
        tenant_id="44444444-4444-4444-8444-444444444444",
        phone_number="+15551234567",
        attempt_number=2,
    )


@pytest.mark.asyncio
async def test_call_intent_persists_job_and_attempt_before_provider_identity():
    worker = DialerWorker()
    conn = _Conn()
    worker._db_pool = _Pool(conn)

    intent = await worker._create_call_intent(_job())

    query, args = next((q, a) for q, a in conn.queries if "INSERT INTO calls" in q)
    columns = query.split("VALUES", 1)[0]
    assert "dialer_job_id" in columns
    assert "dialer_attempt_number" in columns
    assert "external_call_uuid" not in columns
    assert "provider_call_id" not in columns
    assert _job().job_id in args
    assert _job().attempt_number in args
    assert intent.call_id
    assert intent.talklee_call_id


@pytest.mark.asyncio
async def test_call_intent_does_not_swallow_database_failure():
    worker = DialerWorker()
    worker._db_pool = _Pool(_Conn(fail=True))

    with pytest.raises(RuntimeError, match="durable call intent"):
        await worker._create_call_intent(_job())
