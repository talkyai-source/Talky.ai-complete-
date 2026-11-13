#!/usr/bin/env python3
"""Review non-E.164 live leads; write only with an explicit --apply.

Run from backend/: python scripts/repair_non_e164_leads.py [--tenant UUID]
The owner supplies DATABASE_URL in the process environment; apply uses the
existing AuditLogger configuration. Stop affected campaign dispatch before applying:
queued jobs hold their own phone copy and are deliberately not rewritten here.

Only the exact QA population is selected: status <> 'deleted' and phone_number
!~ '^\\+[1-9][0-9]{6,14}$'. Invalid numbers and canonical-number collisions are
flagged and left unchanged. --all-numbers also reads live leads already in
E.164 form and flags the ones that are not real numbers (for example
+12345678901234): the dialer skips those as invalid_phone_number (D2-7), so
this lists them for the owner to fix; they are never rewritten. Each applied batch commits with its audit receipts;
an error rolls back that batch, while earlier batches remain committed. Rerun
after resolving the error. No rows are deleted and no CHECK is added: inbound
lead creation still accepts unknown caller numbers, and owner repair is pending.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.domain.services.audit_logger import AuditEvent, AuditLogger  # noqa: E402
from app.domain.services.phone_number_normalizer import (  # noqa: E402
    campaign_country,
    is_strict_e164,
    normalize_phone_number,
    normalize_suppression_key,
)


_NON_E164 = r"phone_number !~ '^\+[1-9][0-9]{6,14}$'"
_COUNT_SQL = f"""
    SELECT count(*) FROM leads
    WHERE tenant_id = $1::uuid AND status <> 'deleted' AND {_NON_E164}
"""
_PAGE_SQL = f"""
    SELECT l.id, l.tenant_id, l.campaign_id, l.phone_number,
           c.script_config, t.calling_rules
    FROM leads l
    LEFT JOIN campaigns c ON c.id = l.campaign_id AND c.tenant_id = l.tenant_id
    LEFT JOIN tenants t ON t.id = l.tenant_id
    WHERE l.tenant_id = $1::uuid AND l.status <> 'deleted' AND l.{_NON_E164}
      AND ($2::uuid IS NULL OR l.id > $2::uuid)
    ORDER BY l.id LIMIT $3
"""
# --all-numbers: every live lead, so E.164-shaped numbers that are not real
# numbers are found too (the regex above cannot judge validity).
_COUNT_ALL_SQL = """
    SELECT count(*) FROM leads
    WHERE tenant_id = $1::uuid AND status <> 'deleted'
"""
_PAGE_ALL_SQL = """
    SELECT l.id, l.tenant_id, l.campaign_id, l.phone_number,
           c.script_config, t.calling_rules
    FROM leads l
    LEFT JOIN campaigns c ON c.id = l.campaign_id AND c.tenant_id = l.tenant_id
    LEFT JOIN tenants t ON t.id = l.tenant_id
    WHERE l.tenant_id = $1::uuid AND l.status <> 'deleted'
      AND ($2::uuid IS NULL OR l.id > $2::uuid)
    ORDER BY l.id LIMIT $3
"""
_UPDATE_SQL = """
    UPDATE leads SET phone_number = $4, updated_at = NOW()
    WHERE tenant_id = $1::uuid AND id = $2::uuid AND phone_number = $3
      AND status <> 'deleted'
    RETURNING id
"""
_COLLISION_SQL = """
    SELECT id FROM leads
    WHERE tenant_id = $1::uuid AND campaign_id = $2::uuid AND phone_number = $3
      AND id <> $4::uuid AND status <> 'deleted'
    LIMIT 1
"""


def _object(value):
    if isinstance(value, str):
        value = json.loads(value)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Invalid country settings")
    return value


def plan_row(row):
    """Never salvage a damaged +country prefix by guessing its original form."""
    if is_strict_e164(str(row.get("phone_number") or "")):
        # Only --all-numbers reads these. Same key the dialer and call guard
        # use: a valid number needs nothing; an invalid one is listed, never
        # rewritten (there is nothing safe to rewrite it to).
        if normalize_suppression_key(row["phone_number"]):
            return {"action": "valid", "candidate": None}
        return {"action": "invalid", "candidate": None, "reason": "invalid_e164_number"}
    try:
        # Historical rows were saved under the old US default; this owner-run,
        # dry-run-first repair keeps that fallback explicitly (D2-5).
        country = campaign_country(
            {"script_config": _object(row.get("script_config"))},
            account_rules=_object(row.get("calling_rules")),
            fallback="US",
        )
        candidate = normalize_phone_number(row["phone_number"], default_country=country)
    except (ValueError, TypeError):
        return {"action": "invalid", "candidate": None, "reason": "invalid_number_or_country"}
    return {"action": "repair", "candidate": candidate, "country": country}


def _masked(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return "****" + digits[-4:] if len(digits) > 4 else "****"


async def _tenant_context(conn, tenant_id):
    await conn.execute(
        "SELECT set_config('app.current_tenant_id', $1, true), "
        "set_config('app.bypass_rls', 'off', true)", str(tenant_id),
    )


async def repair_tenant(conn, tenant_id, *, apply=False, batch_size=100, sample_limit=10, audit=None,
                        all_numbers=False):
    """Count and scan one tenant by keyset; commit writes with matching audits."""
    tenant_id = UUID(str(tenant_id))
    if not 1 <= batch_size <= 1000 or not 0 <= sample_limit <= 1000:
        raise ValueError("Invalid repair limits")
    if apply and audit is None:
        raise ValueError("An audit logger is required for apply")
    count_sql, page_sql = (_COUNT_ALL_SQL, _PAGE_ALL_SQL) if all_numbers else (_COUNT_SQL, _PAGE_SQL)
    async with conn.transaction(readonly=True):
        await _tenant_context(conn, tenant_id)
        matched = await conn.fetchval(count_sql, tenant_id)
    result = {"tenant_id": str(tenant_id), "matched": matched, "proposed": 0,
              "updated": 0, "invalid": 0, "conflicts": 0, "valid": 0, "samples": []}
    cursor = None
    proposed_keys = set()
    while True:
        async with conn.transaction(readonly=not apply):
            await _tenant_context(conn, tenant_id)
            if apply:
                await audit.lock_transaction(conn)
            rows = await conn.fetch(
                page_sql + (" FOR UPDATE OF l" if apply else ""),
                tenant_id, cursor, batch_size,
            )
            if not rows:
                break
            for row in rows:
                plan = plan_row(row)
                if plan["action"] == "valid":
                    result["valid"] += 1
                    continue
                sample = {"lead_id": str(row["id"]), "action": plan["action"],
                          "before": _masked(row["phone_number"]),
                          "after": _masked(plan["candidate"]) if plan["candidate"] else None}
                if plan["action"] == "invalid":
                    result["invalid"] += 1
                else:
                    key = (str(row["campaign_id"]), plan["candidate"])
                    collision = await conn.fetchrow(
                        _COLLISION_SQL, tenant_id, row["campaign_id"],
                        plan["candidate"], row["id"],
                    )
                    if collision or key in proposed_keys:
                        result["conflicts"] += 1
                        sample["action"] = "conflict"
                    else:
                        proposed_keys.add(key)
                        result["proposed"] += 1
                    if apply and sample["action"] == "repair":
                        try:
                            # A collision must not poison the surrounding batch.
                            async with conn.transaction():
                                updated = await conn.fetchrow(
                                    _UPDATE_SQL, tenant_id, row["id"],
                                    row["phone_number"], plan["candidate"],
                                )
                        except asyncpg.UniqueViolationError:
                            updated = None
                        if updated is None:
                            result["conflicts"] += 1
                            sample["action"] = "conflict"
                        else:
                            await audit.log_in_transaction(
                                conn, event_type=AuditEvent.CONFIG_CHANGED,
                                action="leads.phone_number_repaired", actor_type="system",
                                tenant_id=tenant_id, resource_type="lead", resource_id=row["id"],
                                before_state={"phone_number": sample["before"]},
                                after_state={"phone_number": sample["after"]},
                                metadata={"campaign_id": str(row["campaign_id"]),
                                          "default_country": plan["country"], "tool": "repair_non_e164_leads"},
                            )
                            result["updated"] += 1
                if len(result["samples"]) < sample_limit:
                    result["samples"].append(sample)
            cursor = rows[-1]["id"]
    return result


def _bounded_int(minimum, maximum):
    def parse(value):
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("Must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"Must be between {minimum} and {maximum}")
        return number
    return parse


def build_parser():
    parser = argparse.ArgumentParser(description="Review non-E.164 live leads; dry-run by default.")
    parser.add_argument("--apply", action="store_true", help="Commit valid repairs with audit rows.")
    parser.add_argument("--tenant", type=UUID, help="Restrict the report/repair to one tenant UUID.")
    parser.add_argument("--batch-size", type=_bounded_int(1, 1000), default=100)
    parser.add_argument("--sample-limit", type=_bounded_int(0, 1000), default=10,
                        help="Maximum masked samples per tenant.")
    parser.add_argument("--all-numbers", action="store_true",
                        help="Also check numbers already in E.164 form and list the ones that are "
                             "not real numbers (never rewritten). Reads every live lead.")
    return parser


async def run(args):
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("Set DATABASE_URL in the process environment")
    pool = await asyncpg.create_pool(
        database_url.replace("postgresql+asyncpg://", "postgresql://", 1),
        min_size=1, max_size=1, timeout=10, command_timeout=60,
    )
    try:
        audit = AuditLogger(pool) if args.apply else None
        totals = {key: 0 for key in ("matched", "proposed", "updated", "invalid", "conflicts", "valid")}
        async with pool.acquire(timeout=10) as conn:
            if args.tenant:
                tenants = [args.tenant]
            else:
                # Explicit operator-only cross-tenant directory enumeration.
                # Fail if RLS would silently filter the global report.
                async with conn.transaction(readonly=True):
                    await conn.execute("SET LOCAL row_security = off")
                    tenants = [row["id"] for row in await conn.fetch("SELECT id FROM tenants ORDER BY id")]
            print("APPLY: each batch is audited" if args.apply else "DRY RUN: no rows will be written")
            for tenant_id in tenants:
                report = await repair_tenant(
                    conn, tenant_id, apply=args.apply, batch_size=args.batch_size,
                    sample_limit=args.sample_limit, audit=audit, all_numbers=args.all_numbers,
                )
                print(json.dumps(report), flush=True)
                for key in totals:
                    totals[key] += report[key]
        print(json.dumps({"totals": totals, "apply": args.apply}), flush=True)
        return 0
    finally:
        await pool.close()


def main():
    args = build_parser().parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        # Database exceptions can contain values/DSNs. Never print their text.
        print(f"Repair stopped ({type(exc).__name__}); current batch rolled back. "
              "Review prior batch receipts before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
