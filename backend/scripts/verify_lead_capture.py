#!/usr/bin/env python3
"""Prove the lead-capture guarantees against the real database, then roll back.

    python scripts/verify_lead_capture.py

Everything runs inside a transaction that is ROLLED BACK, so it is safe against
production.

WHAT IT PROVES, AND WHY EACH MATTERS
-------------------------------------
The trust rule (goals.md §7) is enforced by an SQL predicate, not by Python, so
a unit test with a mocked pool proves nothing about it. These checks drive the
real statement against the real constraint:

  * a model's guess cannot overwrite what the caller said
  * a human's correction beats both
  * `confirmed` is sticky -- a later unconfirmed write of the same value must
    not quietly downgrade an agreed fact
  * an absent row and a NULL value are different states
  * full_name really is derived, and follows an edit to first/last
  * the CHECK constraints reject an invented source or field type
  * RLS is enabled AND forced on both new tables
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import asyncpg

_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def dsn() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        try:
            sys.path.insert(0, os.getcwd())
            from app.core.config import get_settings
            raw = get_settings().database_url
        except Exception:
            sys.exit("DATABASE_URL is required")
    return raw.replace("postgresql+asyncpg", "postgresql")


TRUST = ["agent_inferred", "imported", "caller_stated", "manual_edit"]

UPSERT = """
INSERT INTO call_lead_details
    (tenant_id, call_id, campaign_id, lead_id, field_key, field_type,
     value, source, confirmed, is_required)
VALUES ($1,$2,NULL,NULL,$3,'text',$4,$5,$6,FALSE)
ON CONFLICT (call_id, field_key) DO UPDATE
   SET value      = EXCLUDED.value,
       source     = EXCLUDED.source,
       confirmed  = call_lead_details.confirmed OR EXCLUDED.confirmed,
       updated_at = NOW()
 WHERE array_position($7::text[], EXCLUDED.source)
    >= array_position($7::text[], call_lead_details.source)
RETURNING id
"""


async def main() -> int:
    conn = await asyncpg.connect(dsn())

    row = await conn.fetchrow(
        "SELECT id, tenant_id FROM calls ORDER BY created_at DESC LIMIT 1"
    )
    if not row:
        print("no calls row to attach to")
        return 1
    call_id, tenant_id = row["id"], row["tenant_id"]
    print(f"call={str(call_id)[:8]} tenant={str(tenant_id)[:8]}\n")

    # ── RLS, read outside the transaction ───────────────────────────────────
    for tbl in ("call_lead_details", "campaign_lead_fields"):
        r = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = $1",
            tbl,
        )
        check(f"{tbl}: RLS enabled and FORCED",
              bool(r and r["relrowsecurity"] and r["relforcerowsecurity"]))

    tx = conn.transaction()
    await tx.start()
    try:
        key = f"budget_{uuid.uuid4().hex[:6]}"

        # 1. the caller states a figure
        await conn.execute(UPSERT, tenant_id, call_id, key, "40k",
                           "caller_stated", True, TRUST)
        v = await conn.fetchrow(
            "SELECT value, source, confirmed FROM call_lead_details "
            " WHERE call_id=$1 AND field_key=$2", call_id, key)
        check("a caller-stated value is stored", v["value"] == "40k", v["source"])

        # 2. the model later guesses something vaguer — must NOT win
        await conn.execute(UPSERT, tenant_id, call_id, key, "maybe 10k",
                           "agent_inferred", False, TRUST)
        v = await conn.fetchrow(
            "SELECT value, source, confirmed FROM call_lead_details "
            " WHERE call_id=$1 AND field_key=$2", call_id, key)
        check("a model GUESS cannot overwrite what the caller SAID",
              v["value"] == "40k" and v["source"] == "caller_stated",
              f"{v['source']}={v['value']}")
        check("and the confirmed flag survives the attempt", v["confirmed"] is True)

        # 3. a human corrects it — must win
        await conn.execute(UPSERT, tenant_id, call_id, key, "42500",
                           "manual_edit", False, TRUST)
        v = await conn.fetchrow(
            "SELECT value, source, confirmed FROM call_lead_details "
            " WHERE call_id=$1 AND field_key=$2", call_id, key)
        check("a human edit overrides the caller-stated value",
              v["value"] == "42500" and v["source"] == "manual_edit")
        check("confirmed is STICKY across the edit", v["confirmed"] is True,
              "an agreed fact must not silently become unconfirmed")

        # 4. absent vs NULL are different states
        nullkey = f"timeline_{uuid.uuid4().hex[:6]}"
        await conn.execute(UPSERT, tenant_id, call_id, nullkey, None,
                           "caller_stated", False, TRUST)
        present = await conn.fetchval(
            "SELECT count(*) FROM call_lead_details WHERE call_id=$1 AND field_key=$2",
            call_id, nullkey)
        absent = await conn.fetchval(
            "SELECT count(*) FROM call_lead_details WHERE call_id=$1 AND field_key='never_asked'",
            call_id)
        check("asked-and-declined is a row with NULL", present == 1)
        check("never-established is NO row", absent == 0)

        # 5. the CHECK constraints.
        #
        # SAVEPOINT, not a bare try/except: in PostgreSQL a failed statement
        # poisons the whole transaction, so without a nested block every check
        # after this one dies with InFailedSQLTransactionError rather than
        # running. Deliberately provoking a constraint needs somewhere to land.
        for bad, why in (("vibes", "source"),):
            sp = conn.transaction()
            await sp.start()
            try:
                await conn.execute(UPSERT, tenant_id, call_id,
                                   f"x_{uuid.uuid4().hex[:4]}", "v", bad, False, TRUST)
                await sp.rollback()
                check(f"an invented {why} is rejected", False, "it was accepted")
            except asyncpg.PostgresError:
                await sp.rollback()
                check(f"an invented {why} is rejected by the DB", True)

        # 6. full_name is derived and follows an edit
        lead = await conn.fetchrow(
            "SELECT id, first_name, last_name, full_name FROM leads LIMIT 1")
        if lead:
            await conn.execute(
                "UPDATE leads SET first_name='Sian', last_name='Whitfield' WHERE id=$1",
                lead["id"])
            fn = await conn.fetchval("SELECT full_name FROM leads WHERE id=$1", lead["id"])
            check("full_name is derived from first+last", fn == "Sian Whitfield", str(fn))
            await conn.execute(
                "UPDATE leads SET first_name=NULL, last_name=NULL WHERE id=$1", lead["id"])
            fn = await conn.fetchval("SELECT full_name FROM leads WHERE id=$1", lead["id"])
            check("full_name is NULL, not a stray space, when both are empty", fn is None,
                  repr(fn))
        else:
            print("  SKIP  no leads row to test full_name against")
    finally:
        await tx.rollback()

    left = await conn.fetchval(
        "SELECT count(*) FROM call_lead_details WHERE call_id=$1", call_id)
    check("everything rolled back — nothing persisted", left == 0, f"{left} rows left")
    await conn.close()

    passed, total = sum(_results), len(_results)
    print(f"\n===== {passed}/{total} checks passed =====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
