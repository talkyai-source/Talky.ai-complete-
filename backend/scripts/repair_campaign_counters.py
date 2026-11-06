#!/usr/bin/env python3
"""F14-04 terminal-lead counter repair; read-only unless --apply is explicit.

The operator supplies DATABASE_URL and the existing audit configuration. Stop
affected dispatch/call writers before apply. Each campaign is one audited
transaction; ambiguous history and active work are reported, never guessed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from uuid import UUID

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.domain.models.dialer_job import CallOutcome  # noqa: E402
from app.domain.services.audit_logger import AuditEvent, AuditLogger  # noqa: E402
from app.workers.disposition_policy import DNC_OUTCOMES  # noqa: E402


_FAILED_OUTCOMES = {item.value for item in DNC_OUTCOMES} | {"invalid"}
_KNOWN_OUTCOMES = {item.value for item in CallOutcome}
_ACTIVE_JOBS = {"pending", "queued", "retry_scheduled", "processing", "calling"}
_TERMINAL_JOBS = {"completed", "goal_achieved", "non_retryable", "failed"}
_CAMPAIGN_SQL = """
    SELECT id, tenant_id, status, calls_completed, calls_failed, total_leads
    FROM campaigns WHERE tenant_id = $1::uuid AND id = $2::uuid
"""
_ACTIVE_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM dialer_jobs j
        WHERE j.tenant_id = $1::uuid AND j.campaign_id = $2::uuid
          AND j.status IN ('pending','queued','retry_scheduled','processing','calling')
    ) OR EXISTS (
        SELECT 1 FROM calls c
        WHERE c.tenant_id = $1::uuid AND c.campaign_id = $2::uuid
          AND (c.status NOT IN ('completed','failed','cancelled','ended','busy','no_answer')
               OR c.terminal_settled_at IS NULL)
    )
"""
_LEADS_SQL = """
    SELECT l.id, l.tenant_id, l.campaign_id, l.status, l.last_call_result, l.call_attempts,
           j.id AS job_id, j.status AS job_status, j.last_outcome AS job_outcome,
           j.last_error AS job_error, j.failure_reason AS job_failure_reason,
           c.outcome AS settled_outcome, c.dialer_job_id AS settled_job_id,
           (c.id IS NOT NULL AND c.terminal_settled_at IS NOT NULL) AS has_settled_call
    FROM leads l
    LEFT JOIN LATERAL (
        SELECT j.id, j.status, j.last_outcome, j.last_error, j.failure_reason
        FROM dialer_jobs j
        WHERE j.tenant_id = $1::uuid AND j.campaign_id = $2::uuid AND j.lead_id = l.id
        ORDER BY j.created_at DESC, j.id DESC LIMIT 1
    ) j ON true
    LEFT JOIN LATERAL (
        SELECT c.id, c.outcome, c.dialer_job_id, c.terminal_settled_at
        FROM calls c
        WHERE c.tenant_id = $1::uuid AND c.campaign_id = $2::uuid AND c.lead_id = l.id
        ORDER BY c.created_at DESC, c.id DESC LIMIT 1
    ) c ON true
    WHERE l.tenant_id = $1::uuid AND l.campaign_id = $2::uuid
      AND ($3::uuid IS NULL OR l.id > $3::uuid)
    ORDER BY l.id LIMIT $4
"""
_UPDATE_SQL = """
    UPDATE campaigns SET calls_completed = $3, calls_failed = $4, updated_at = NOW()
    WHERE tenant_id = $1::uuid AND id = $2::uuid
    RETURNING id, calls_completed, calls_failed
"""


def classify_lead(row):
    """Return one bucket per lead, or explicit nonfinal/ambiguous evidence.

    Do not infer retry exhaustion from call_attempts: its historical accounting
    differs from the durable job attempt counter. A current final job or a
    settled jobless call supplies the finality proof instead.
    """
    def result(bucket=None, reason="not_final", ambiguous=False):
        return {"bucket": bucket, "reason": reason, "ambiguous": ambiguous}

    status, job_status = row.get("status"), row.get("job_status")
    if status == "deleted":
        return result(reason="deleted_lead")
    if status in {"pending", "new", "calling", "processing", "skipped"} or job_status in _ACTIVE_JOBS:
        return result(reason="not_final")
    if job_status in {"cancelled", "skipped", "blocked"}:
        return result(reason="job_not_attempted")
    if status == "dnc" and not row.get("call_attempts") and not row.get("has_settled_call") and not row.get("job_id"):
        return result(reason="suppressed_without_attempt")

    outcome = None
    if row.get("job_id"):
        if job_status not in _TERMINAL_JOBS:
            return result(reason="unknown_job_state", ambiguous=True)
        outcome = row.get("job_outcome")
        if not outcome:
            if (job_status == "failed" and status == "failed" and row.get("job_error")
                    and not row.get("has_settled_call")):
                return result("failed", "terminal_pre_answer_failure")
            return result(reason="missing_final_outcome", ambiguous=True)
        if row.get("job_error"):
            # A newer pre-answer failure can leave an older last_outcome on
            # the same job. It is unsafe to assign the old attempt's bucket.
            return result(reason="mixed_failure_evidence", ambiguous=True)
        if row.get("has_settled_call") and (
            str(row.get("settled_job_id")) != str(row["job_id"])
            or row.get("settled_outcome") != outcome
        ):
            return result(reason="conflicting_final_evidence", ambiguous=True)
    elif row.get("has_settled_call") and row.get("settled_job_id") is None:
        outcome = row.get("settled_outcome")
    else:
        return result(reason="unproven_legacy_finality", ambiguous=True)

    if outcome not in _KNOWN_OUTCOMES:
        return result(reason="unknown_final_outcome", ambiguous=True)
    return result("failed" if outcome in _FAILED_OUTCOMES else "completed", "terminal_outcome")


async def _context(conn, tenant_id):
    await conn.execute("SET LOCAL statement_timeout = '30s'")
    await conn.execute("SET LOCAL lock_timeout = '5s'")
    # Refuse silently filtered inventories, including the relevant no-RLS
    # historical children; data SQL independently binds tenant and campaign.
    await conn.execute("SET LOCAL row_security = off")
    await conn.execute(
        "SELECT set_config('app.current_tenant_id', $1, true), "
        "set_config('app.bypass_rls', 'off', true)", str(tenant_id),
    )


async def repair_campaign(conn, tenant_id, campaign_id, *, apply=False, audit=None, batch_size=500):
    tenant_id, campaign_id = UUID(str(tenant_id)), UUID(str(campaign_id))
    if apply and audit is None:
        raise ValueError("An audit logger is required for apply")
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    result = {"tenant_id": str(tenant_id), "campaign_id": str(campaign_id), "applied": False,
              "blocked": None, "ambiguous_leads": 0, "ambiguous_samples": [], "excluded_leads": 0}
    async with conn.transaction(isolation="serializable", readonly=not apply):
        await _context(conn, tenant_id)
        if apply:
            await audit.lock_transaction(conn)
        campaign = await conn.fetchrow(_CAMPAIGN_SQL + (" FOR UPDATE" if apply else ""), tenant_id, campaign_id)
        if campaign is None:
            result["blocked"] = "campaign_not_found"
            return result
        before = {key: int(campaign[key] or 0) for key in ("calls_completed", "calls_failed")}
        after = {"calls_completed": 0, "calls_failed": 0}
        active = await conn.fetchval(_ACTIVE_SQL, tenant_id, campaign_id)
        if not isinstance(active, bool):
            raise RuntimeError("Campaign activity check returned an invalid result")
        cursor = None
        while True:
            rows = await conn.fetch(_LEADS_SQL + (" FOR UPDATE OF l" if apply else ""), tenant_id, campaign_id, cursor, batch_size)
            if not rows:
                break
            for row in rows:
                if cursor is not None and row["id"] <= cursor:
                    raise RuntimeError("Lead pagination did not advance")
                cursor = row["id"]
                decision = classify_lead(row)
                if decision["ambiguous"]:
                    result["ambiguous_leads"] += 1
                    if len(result["ambiguous_samples"]) < 20:
                        result["ambiguous_samples"].append({"lead_id": str(row["id"]), "reason": decision["reason"]})
                elif decision["bucket"]:
                    after[f"calls_{decision['bucket']}"] += 1
                else:
                    result["excluded_leads"] += 1
        result.update(before=before, after=after, delta={key: after[key] - before[key] for key in before})
        if result["ambiguous_leads"]:
            result["blocked"] = "ambiguous_history"
        elif campaign["status"] in {"running", "active", "starting"} or active:
            result["blocked"] = "active_campaign_work"
        if not apply or result["blocked"] or before == after:
            return result
        updated = await conn.fetchrow(_UPDATE_SQL, tenant_id, campaign_id, after["calls_completed"], after["calls_failed"])
        if updated is None:
            raise RuntimeError("Campaign counter update did not persist")
        await audit.log_in_transaction(
            conn, event_type=AuditEvent.CONFIG_CHANGED, action="campaign.counters_repaired", actor_type="system",
            tenant_id=tenant_id, resource_type="campaign", resource_id=campaign_id,
            before_state=before, after_state=after,
            metadata={"tool": "repair_campaign_counters", "basis": "one_per_proven_terminal_lead"},
        )
        result["applied"] = True
    return result


def build_parser():
    parser = argparse.ArgumentParser(description="Rebuild proven terminal-lead counters; dry-run by default.")
    parser.add_argument("--tenant", type=UUID, required=True)
    parser.add_argument("--campaign", type=UUID, help="One campaign; otherwise enumerate this tenant only.")
    parser.add_argument("--apply", action="store_true")
    return parser


async def run(args):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise ValueError("Set DATABASE_URL in the process environment")
    pool = await asyncpg.create_pool(dsn.replace("postgresql+asyncpg://", "postgresql://", 1),
                                    min_size=1, max_size=1, timeout=10, command_timeout=30)
    try:
        audit = AuditLogger(pool) if args.apply else None
        blocked = 0
        async with pool.acquire(timeout=10) as conn:
            cursor = None
            while True:
                if args.campaign:
                    campaigns = [{"id": args.campaign}]
                else:
                    async with conn.transaction(readonly=True):
                        await _context(conn, args.tenant)
                        campaigns = await conn.fetch(
                            "SELECT id FROM campaigns WHERE tenant_id = $1::uuid "
                            "AND ($2::uuid IS NULL OR id > $2::uuid) ORDER BY id LIMIT $3",
                            args.tenant, cursor, 100,
                        )
                if not campaigns:
                    break
                for campaign in campaigns:
                    report = await repair_campaign(conn, args.tenant, campaign["id"], apply=args.apply, audit=audit)
                    blocked += bool(report["blocked"])
                    print(json.dumps(report), flush=True)
                if args.campaign:
                    break
                cursor = campaigns[-1]["id"]
        print(json.dumps({"apply": args.apply, "blocked_campaigns": blocked}), flush=True)
        return 2 if blocked else 0
    finally:
        await pool.close()


def main():
    args = build_parser().parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        print(f"Counter repair stopped ({type(exc).__name__}); inspect committed campaign receipts before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
