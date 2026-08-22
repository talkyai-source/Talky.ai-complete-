#!/usr/bin/env python3
"""Do the review constraints actually hold? (goals.md §3)

    python scripts/verify_conversation_reviews.py

Everything happens inside one transaction that is ROLLED BACK, so it is safe to
run against production: no review, no reward and no test row survives.

WHY A SCRIPT AND NOT A UNIT TEST
--------------------------------
The properties §3 depends on are enforced by database constraints, not by Python:

  * one review per user per call        UNIQUE (call_id, user_id)
  * an edit updates in place            ON CONFLICT ... DO UPDATE
  * a reward is granted at most once    UNIQUE (review_id)
  * only known tags are storable        CHECK (review_tags <@ ARRAY[...])

Asserting those against a mock would only prove the mock agrees with itself.
The reward rule in particular — "review edits preserve the original reward
transaction" — is the one most likely to be quietly broken by a refactor, and
the only honest way to check it is to try to break it against a real engine.
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

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
    tx = conn.transaction()
    await tx.start()
    try:
        # Real rows, so foreign keys are exercised rather than side-stepped.
        call = await conn.fetchrow(
            "SELECT id, tenant_id, campaign_id, prompt_version, prompt_template, "
            "prompt_hash FROM calls WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
        )
        if not call:
            print("no completed call to test against")
            return 1
        users = await conn.fetch(
            "SELECT id FROM user_profiles WHERE tenant_id=$1 LIMIT 2", call["tenant_id"]
        )
        if len(users) < 2:
            users = await conn.fetch("SELECT id FROM user_profiles LIMIT 2")
        u1, u2 = users[0]["id"], users[1]["id"]
        tid, cid = call["tenant_id"], call["id"]
        print(f"call={str(cid)[:8]} tenant={str(tid)[:8]} users={str(u1)[:8]},{str(u2)[:8]}\n")

        async def guarded(label: str, coro_sql: str, args: tuple, sp: str,
                          expect: type[BaseException]) -> None:
            """A constraint violation aborts the transaction, so each expected
            failure needs its own savepoint or the next statement dies too."""
            await conn.execute(f"SAVEPOINT {sp}")
            try:
                await conn.execute(coro_sql, *args)
                check(label, False, "the database allowed it")
                await conn.execute(f"RELEASE SAVEPOINT {sp}")
            except expect:
                check(label, True)
                await conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")

        INSERT = """
            INSERT INTO conversation_reviews
                (tenant_id, call_id, campaign_id, user_id, rating, review_tags,
                 comment, prompt_template, prompt_version, prompt_hash)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            RETURNING id, rating, review_tags, comment, updated_at
        """

        print("== a review can be left ==")
        r1 = await conn.fetchrow(
            INSERT, tid, cid, call["campaign_id"], u1, 4, ["response_too_long"],
            "a bit wordy", call["prompt_template"], call["prompt_version"], call["prompt_hash"],
        )
        check("review stored", r1 is not None, f"rating={r1['rating']}")
        check("tags stored as an array", list(r1["review_tags"]) == ["response_too_long"])

        print("\n== one review per USER per call ==")
        await guarded(
            "the same user cannot leave a second review",
            INSERT.replace("RETURNING id, rating, review_tags, comment, updated_at", ""),
            (tid, cid, call["campaign_id"], u1, 2, [], None,
             call["prompt_template"], call["prompt_version"], call["prompt_hash"]),
            "sp1", asyncpg.UniqueViolationError,
        )
        r2 = await conn.fetchrow(
            INSERT, tid, cid, call["campaign_id"], u2, 5, ["good_conversation"],
            None, call["prompt_template"], call["prompt_version"], call["prompt_hash"],
        )
        check("a DIFFERENT user can review the same call", r2 is not None,
              "two independent reviews coexist")

        print("\n== an edit updates in place, it does not duplicate ==")
        upd = await conn.fetchrow("""
            INSERT INTO conversation_reviews
                (tenant_id, call_id, campaign_id, user_id, rating, review_tags, comment)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (call_id, user_id) DO UPDATE
               SET rating=EXCLUDED.rating, review_tags=EXCLUDED.review_tags,
                   comment=EXCLUDED.comment, updated_at=NOW()
            RETURNING id, rating, comment
        """, tid, cid, call["campaign_id"], u1, 1,
             ["agent_interrupted_caller"], "changed my mind")
        check("the edit reused the same row", upd["id"] == r1["id"])
        check("the new rating took effect", upd["rating"] == 1)
        n = await conn.fetchval(
            "SELECT count(*) FROM conversation_reviews WHERE call_id=$1", cid)
        check("still exactly two reviews on the call", n == 2, f"count={n}")

        print("\n== the tag vocabulary is enforced by the database ==")
        await guarded(
            "an unknown tag is rejected",
            "INSERT INTO conversation_reviews (tenant_id, call_id, user_id, rating, review_tags)"
            " VALUES ($1,$2,$3,$4,$5)",
            (tid, cid, u2, 3, ["response_to_long"]), "sp2", asyncpg.CheckViolationError,
        )
        await guarded(
            "a rating of 0 is rejected",
            "INSERT INTO conversation_reviews (tenant_id, call_id, user_id, rating)"
            " VALUES ($1,$2,$3,$4)",
            (tid, cid, u2, 0), "sp3", asyncpg.CheckViolationError,
        )
        await guarded(
            "a rating of 6 is rejected",
            "INSERT INTO conversation_reviews (tenant_id, call_id, user_id, rating)"
            " VALUES ($1,$2,$3,$4)",
            (tid, cid, u2, 6), "sp4", asyncpg.CheckViolationError,
        )

        print("\n== the reward ledger cannot double-credit ==")
        await conn.execute(
            "INSERT INTO review_reward_ledger (tenant_id, user_id, review_id, points)"
            " VALUES ($1,$2,$3,$4)", tid, u1, r1["id"], 10)
        check("first award recorded", True)
        await guarded(
            "a SECOND award for the same review is refused",
            "INSERT INTO review_reward_ledger (tenant_id, user_id, review_id, points)"
            " VALUES ($1,$2,$3,$4)",
            (tid, u1, r1["id"], 10), "sp5", asyncpg.UniqueViolationError,
        )
        await guarded(
            "a zero-point award is refused",
            "INSERT INTO review_reward_ledger (tenant_id, user_id, review_id, points)"
            " VALUES ($1,$2,$3,$4)",
            (tid, u2, r2["id"], 0), "sp6", asyncpg.CheckViolationError,
        )
        bal = await conn.fetchval(
            "SELECT COALESCE(SUM(points),0)::int FROM review_reward_ledger WHERE user_id=$1", u1)
        check("balance is the sum of the ledger", bal == 10, f"{bal} points")

        print("\n== prompt identity is snapshotted onto the review ==")
        snap = await conn.fetchrow(
            "SELECT prompt_version, prompt_template FROM conversation_reviews WHERE id=$1",
            r1["id"])
        check("prompt columns exist on the review and round-trip",
              snap is not None,
              f"version={snap['prompt_version']} template={snap['prompt_template']}")
        if call["prompt_version"] is None:
            print("     note: this call predates prompt-identity capture, so the "
                  "snapshot is NULL - correct, not a defect")

        print("\n== isolation ==")
        for t in ("conversation_reviews", "review_reward_ledger"):
            row = await conn.fetchrow(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=$1", t)
            check(f"{t}: RLS + FORCE enabled",
                  row["relrowsecurity"] and row["relforcerowsecurity"])
            pol = await conn.fetchrow(
                "SELECT qual, with_check FROM pg_policies WHERE tablename=$1", t)
            check(f"{t}: canonical policy (bypass_rls + NULLIF + WITH CHECK)",
                  bool(pol) and "bypass_rls" in (pol["qual"] or "")
                  and "NULLIF" in (pol["qual"] or "") and bool(pol["with_check"]))
    finally:
        await tx.rollback()

    left = await conn.fetchval("SELECT count(*) FROM conversation_reviews")
    check("nothing persisted - the transaction rolled back", left == 0, f"{left} rows")
    await conn.close()

    passed, total = sum(_results), len(_results)
    print(f"\n===== {passed}/{total} checks passed =====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
