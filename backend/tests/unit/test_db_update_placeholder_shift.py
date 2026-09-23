"""Reproduces the 2026-09-22/23 'dialer-job-update-placeholder-corruption'
production defect.

See scratchpad issues_all.txt id=dialer-job-update-placeholder-corruption:
`Database.update()` renumbered WHERE-clause placeholders with sequential
`str.replace($N, $(N+offset))` calls in DESCENDING order of N. Once the
total bound params (SET + WHERE) reached 10, the loop wrote `$10` two
steps before doing `$1` -> `$7`, and that later replace's "$1" matched
the leading substring of the already-written "$10", corrupting it to
"$70". Postgres then reported 'could not determine data type of
parameter $10' on every answered outbound dial (DialerWorker's call_id
branch: 6 SET keys against the 4-arg ownership WHERE = exactly 10
params) -- logged 2026-09-22 19:15:48 and three times on 2026-09-23
(18:05:37, 18:09:12, 18:11:55).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.core.db import Database


class _FakeConn:
    """Captures the final query/args `Database.update()` sends to asyncpg."""

    def __init__(self):
        self.query: str | None = None
        self.args: tuple = ()

    async def fetch(self, query, *args):
        self.query = query
        self.args = args
        return []


def _placeholder_numbers(query: str) -> list[int]:
    return sorted(int(n) for n in re.findall(r"\$(\d+)", query))


@pytest.mark.asyncio
async def test_call_id_update_ten_param_where_is_not_corrupted():
    """Exact shape of DialerWorker._update_job_status's call_id branch:
    6 SET keys (status, updated_at, call_id, processed_at, failure_reason,
    last_error) + a 4-arg ownership WHERE (id/tenant_id/campaign_id/
    lead_id) = 10 total bound params -- the combination that hit $10.
    """
    conn = _FakeConn()
    db = Database(conn)
    data = {
        "status": "processing",
        "updated_at": datetime.now(timezone.utc),
        "call_id": "call-1",
        "processed_at": datetime.now(timezone.utc),
        "failure_reason": None,
        "last_error": None,
    }
    where = (
        "id = $1::uuid AND tenant_id = $2::uuid "
        "AND campaign_id = $3::uuid AND lead_id = $4::uuid"
    )
    args = ["job-1", "tenant-1", "campaign-1", "lead-1"]

    await db.update("dialer_jobs", data, where, args)

    assert conn.query is not None
    # Every one of $1..$10 must appear exactly once, and never as a
    # corrupted "$70".
    assert "$70" not in conn.query
    assert _placeholder_numbers(conn.query) == list(range(1, 11))
    assert len(conn.args) == 10
    # The WHERE clause itself must reference $7..$10 in order, each
    # placeholder bound to the matching WHERE arg.
    assert (
        "id = $7::uuid AND tenant_id = $8::uuid "
        "AND campaign_id = $9::uuid AND lead_id = $10::uuid"
    ) in conn.query
    assert conn.args[6:10] == ("job-1", "tenant-1", "campaign-1", "lead-1")


@pytest.mark.asyncio
async def test_eleven_param_update_where_clause_still_correct():
    """A second two-digit case, one param past the production shape, to
    prove the fix is a general single-pass shift and not special-cased
    for exactly 10.
    """
    conn = _FakeConn()
    db = Database(conn)
    data = {f"col{i}": i for i in range(7)}  # 7 SET keys
    where = "a = $1 AND b = $2 AND c = $3 AND d = $4"  # 4 WHERE args -> 11 total
    args = ["a-val", "b-val", "c-val", "d-val"]

    await db.update("t", data, where, args)

    assert _placeholder_numbers(conn.query) == list(range(1, 12))
    assert "a = $8 AND b = $9 AND c = $10 AND d = $11" in conn.query
    assert conn.args[7:11] == ("a-val", "b-val", "c-val", "d-val")
