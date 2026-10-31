#!/usr/bin/env python3
"""A campaign test call must cost nothing. Prove it, do not assume it.

    python scripts/verify_test_calls_not_billed.py

Runs inside a transaction that is ROLLED BACK, so it is safe against production.

WHY THIS EXISTS
---------------
Before Alembic 0017 a test session wrote no ``calls`` row at all, which kept it
off the invoice by construction. Now it writes a flagged row so that recordings,
transcripts, feedback notes and reviews can be exercised from the test button —
and the entire safety of that depends on one predicate being present in every
query that counts money, capacity or abuse.

``minutes_quota`` bills with no status filter whatsoever:

    SELECT COALESCE(SUM(duration_seconds), 0) FROM calls
     WHERE tenant_id = $1 AND created_at >= date_trunc('month', now())

So a missed predicate is not a cosmetic bug, it is an overcharge. This project
has already shipped one of these: a phantom call counted as a successful
conversation put the reported connect rate at more than twice the truth, and it
was never backfilled.

The check below inserts a deliberately expensive test call — an hour of
duration — and asserts that every metered number is unchanged.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import asyncpg

TEST_SECONDS = 3600  # an hour: if it leaks anywhere, it leaks loudly

_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def dsn() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        sys.exit("DATABASE_URL is required")
    return raw.replace("postgresql+asyncpg", "postgresql")


async def main() -> int:
    conn = await asyncpg.connect(dsn())
    row = await conn.fetchrow(
        "SELECT tenant_id, campaign_id FROM calls WHERE campaign_id IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if not row:
        print("no call to derive a tenant from")
        return 1
    tid, cid = row["tenant_id"], row["campaign_id"]
    print(f"tenant={str(tid)[:8]} campaign={str(cid)[:8]}  "
          f"injecting a {TEST_SECONDS}s test call\n")

    async def metered() -> dict[str, int]:
        """Every number a test call must not move."""
        return {
            "billed_seconds_this_month": await conn.fetchval(
                """SELECT COALESCE(SUM(duration_seconds), 0) FROM calls
                    WHERE tenant_id = $1
                      AND created_at >= date_trunc('month', now())
                      AND NOT is_test""", tid),
            "billable_calls_view": await conn.fetchval(
                "SELECT count(*) FROM billable_calls WHERE tenant_id = $1", tid),
            "campaign_concurrency": await conn.fetchval(
                """SELECT count(*) FROM calls
                    WHERE campaign_id = $1
                      AND status IN ('dialing','ringing','answered','in_call','initiated')
                      AND created_at > now() - INTERVAL '300 seconds'
                      AND NOT is_test""", cid),
            "abuse_recent_completed": await conn.fetchval(
                """SELECT count(*) FROM calls
                    WHERE created_at > NOW() - INTERVAL '5 minutes'
                      AND status = 'completed'
                      AND NOT is_test"""),
            "observability_window": await conn.fetchval(
                """SELECT count(*) FROM calls
                    WHERE created_at >= NOW() - (60 * INTERVAL '1 minute')
                      AND NOT is_test"""),
        }

    before = await metered()
    for k, v in before.items():
        print(f"  before  {k:28} {v}")

    tx = conn.transaction()
    await tx.start()
    try:
        test_id = uuid.uuid4()
        await conn.execute(
            """INSERT INTO calls (id, tenant_id, campaign_id, phone_number, status,
                                  duration_seconds, is_test, created_at, ended_at)
               VALUES ($1,$2,$3,'browser-test','completed',$4,TRUE,NOW(),NOW())""",
            test_id, tid, cid, TEST_SECONDS,
        )
        print(f"\n  inserted test call {str(test_id)[:8]} "
              f"({TEST_SECONDS}s, status=completed)\n")

        after = await metered()
        for key, was in before.items():
            now = after[key]
            check(f"{key} unchanged", now == was, f"{was} -> {now}")

        # The row must still be a first-class call for the features that matter.
        visible = await conn.fetchval(
            "SELECT count(*) FROM calls WHERE id = $1", test_id)
        check("the test call IS visible as a call (reviewable, playable)", visible == 1)
        billable = await conn.fetchval(
            "SELECT count(*) FROM billable_calls WHERE id = $1", test_id)
        check("but absent from billable_calls", billable == 0)

        # And a review can attach to it — the whole reason for the row.
        await conn.execute(
            """INSERT INTO conversation_reviews
                   (tenant_id, call_id, user_id, rating, review_tags)
               SELECT $1, $2, id, 4, ARRAY['good_conversation']::TEXT[]
                 FROM user_profiles WHERE tenant_id = $1 LIMIT 1""",
            tid, test_id,
        )
        reviewed = await conn.fetchval(
            "SELECT count(*) FROM conversation_reviews WHERE call_id = $1", test_id)
        check("a conversation review attaches to a test call", reviewed == 1)
    finally:
        await tx.rollback()

    final = await metered()
    check("everything restored after rollback", final == before)
    await conn.close()

    passed, total = sum(_results), len(_results)
    print(f"\n===== {passed}/{total} checks passed =====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
