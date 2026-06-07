"""One-shot, idempotent backfill of AI call summaries for existing calls.

New calls are summarized automatically at call-end, and any call opened in the
UI is summarized lazily. The only gap is *existing, never-opened* calls: their
list row shows no headline until someone expands them. This script closes that
gap by pre-generating summaries for transcript-bearing calls that don't have
one yet.

Why a script (vs. ad-hoc psql/python on prod):
  • Idempotent + re-runnable — reuses store.generate_and_store, which skips
    calls that already have a real summary.
  • Self-healing — also re-attempts rows previously poisoned with the
    "Summary unavailable" sentinel (forces a re-summarize for those).
  • Rate-limit aware — Groq's free tier is 30 RPM / 6,000 TPM, and one summary
    is ~2k tokens, so requests are PACED (default 20s apart) to stay under TPM,
    with a one-shot backoff-retry if a transient failure slips through.
  • Bounded — --limit / --days / --tenant keep a large history in check.

Scope is intentionally simple (sequential + pacing, not a token-bucket): the
real backlog is small and does not grow under normal operation (the call-end
hook handles new calls). For very large / long-transcript batches, raise --pace.

Usage::

    cd /opt/talky/backend
    source venv/bin/activate
    export $(grep -E '^(DATABASE_URL|GROQ_API_KEY)=' .env | xargs)

    # See what would be processed (no API calls, no writes):
    python -m app.services.scripts.backfill_call_summaries --dry-run

    # Backfill everything missing, paced for Groq's TPM limit:
    python -m app.services.scripts.backfill_call_summaries --pace 20

    # Scope it:
    python -m app.services.scripts.backfill_call_summaries --days 30 --limit 100
    python -m app.services.scripts.backfill_call_summaries --tenant <uuid>

    # Re-summarize EVERY transcript-bearing call (e.g. after a schema change):
    python -m app.services.scripts.backfill_call_summaries --force
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import NamedTuple, Optional

import asyncpg

from app.domain.services.call_summary.store import generate_and_store
from app.domain.services.call_summary.summarizer import SUMMARY_UNAVAILABLE_HEADLINE

logger = logging.getLogger("backfill_call_summaries")


class Candidate(NamedTuple):
    call_id: str
    tenant_id: str
    current_headline: Optional[str]  # None if no summary_json yet


# ---------------------------------------------------------------------------
# Candidate selection (cross-tenant — runs with RLS bypassed)
# ---------------------------------------------------------------------------

async def _find_candidates(
    pool: asyncpg.Pool,
    *,
    days: Optional[int],
    tenant: Optional[str],
    limit: Optional[int],
    force: bool,
) -> list[Candidate]:
    """Return calls that need a summary, most-recent-first.

    A candidate has a non-empty transcript AND either has no summary yet, or is
    poisoned with the "Summary unavailable" sentinel (so we re-attempt it). With
    ``force`` every transcript-bearing call is a candidate.

    The SELECT is cross-tenant, so it runs with ``app.bypass_rls`` set (the
    per-call WRITE in generate_and_store still scopes itself to the owning
    tenant via acquire_with_tenant — RLS stays correct on writes).
    """
    conditions = ["transcript IS NOT NULL", "length(trim(transcript)) > 0"]
    params: list = []

    if not force:
        params.append(SUMMARY_UNAVAILABLE_HEADLINE)
        conditions.append(
            f"(summary_json IS NULL OR summary_json->>'headline' = ${len(params)})"
        )
    if tenant:
        params.append(tenant)
        conditions.append(f"tenant_id = ${len(params)}::uuid")
    if days is not None:
        params.append(days)
        conditions.append(f"created_at > NOW() - make_interval(days => ${len(params)})")

    sql = (
        "SELECT id, tenant_id, summary_json->>'headline' AS current_headline "
        "FROM calls WHERE " + " AND ".join(conditions) + " ORDER BY created_at DESC"
    )
    if limit is not None:
        params.append(limit)
        sql += f" LIMIT ${len(params)}"

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Best-effort bypass for policies that honor it; harmless for a
            # superuser/BYPASSRLS role (which ignores RLS anyway).
            await conn.execute("SET LOCAL app.bypass_rls = 'true'")
            rows = await conn.fetch(sql, *params)

    return [
        Candidate(str(r["id"]), str(r["tenant_id"]), r["current_headline"])
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Per-call summarize (with one transient-failure backoff retry)
# ---------------------------------------------------------------------------

async def _summarize_one(
    pool: asyncpg.Pool,
    cand: Candidate,
    *,
    force: bool,
    retry_backoff: float,
) -> str:
    """Summarize a single call. Returns 'ok' | 'failed' | 'skipped'.

    A previously-poisoned row (sentinel headline) is re-summarized with
    force=True so generate_and_store's idempotency guard doesn't return the
    stale sentinel. On a transient failure (the store layer refuses to persist
    the sentinel) we wait out the Groq per-minute window once and retry.
    """
    needs_force = force or (cand.current_headline == SUMMARY_UNAVAILABLE_HEADLINE)

    summary = await generate_and_store(pool, cand.tenant_id, cand.call_id, force=needs_force)
    if summary is None:
        return "skipped"  # no transcript / row vanished — shouldn't happen given the filter
    if summary.get("headline") != SUMMARY_UNAVAILABLE_HEADLINE:
        return "ok"

    logger.warning(
        "call %s: transient summary failure — waiting %.0fs for the Groq window, then one retry",
        cand.call_id[:8],
        retry_backoff,
    )
    if retry_backoff > 0:
        await asyncio.sleep(retry_backoff)
    summary = await generate_and_store(pool, cand.tenant_id, cand.call_id, force=True)
    if summary is not None and summary.get("headline") != SUMMARY_UNAVAILABLE_HEADLINE:
        return "ok"
    return "failed"


async def _run_backfill(
    pool: asyncpg.Pool,
    candidates: list[Candidate],
    *,
    pace: float,
    retry_backoff: float,
    force: bool,
) -> dict:
    """Process candidates sequentially, paced for Groq's rate limit."""
    total = len(candidates)
    succeeded = failed = skipped = 0

    for i, cand in enumerate(candidates, start=1):
        if i > 1 and pace > 0:
            await asyncio.sleep(pace)  # pace BETWEEN calls to respect TPM
        outcome = await _summarize_one(
            pool, cand, force=force, retry_backoff=retry_backoff
        )
        if outcome == "ok":
            succeeded += 1
        elif outcome == "failed":
            failed += 1
        else:
            skipped += 1
        logger.info(
            "[%d/%d] call=%s tenant=%s -> %s",
            i, total, cand.call_id[:8], cand.tenant_id[:8], outcome,
        )

    return {
        "candidates": total,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill AI summaries for existing calls.")
    p.add_argument("--limit", type=int, default=None,
                   help="Max calls to process (most-recent-first). Default: all.")
    p.add_argument("--days", type=int, default=None,
                   help="Only calls created within the last N days. Default: all.")
    p.add_argument("--tenant", type=str, default=None,
                   help="Restrict to a single tenant UUID. Default: all tenants.")
    p.add_argument("--pace", type=float, default=20.0,
                   help="Seconds to wait between calls (Groq TPM guard). Default: 20.")
    p.add_argument("--retry-backoff", type=float, default=65.0,
                   help="Seconds to wait before the single retry after a transient "
                        "failure (clears the Groq per-minute window). Default: 65.")
    p.add_argument("--force", action="store_true",
                   help="Re-summarize EVERY transcript-bearing call, even if it "
                        "already has a summary.")
    p.add_argument("--dry-run", action="store_true",
                   help="List candidates and exit — no API calls, no writes.")
    return p.parse_args(argv)


async def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parse_args(argv)

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set — export it before running.", file=sys.stderr)
        return 2
    if not args.dry_run and not os.getenv("GROQ_API_KEY"):
        print("GROQ_API_KEY is not set — summaries would all fail. Export it first.",
              file=sys.stderr)
        return 2

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        candidates = await _find_candidates(
            pool, days=args.days, tenant=args.tenant, limit=args.limit, force=args.force,
        )
        logger.info(
            "Found %d candidate call(s) (force=%s, days=%s, tenant=%s, limit=%s)",
            len(candidates), args.force, args.days,
            (args.tenant[:8] + "…") if args.tenant else None, args.limit,
        )

        if args.dry_run:
            for cand in candidates:
                state = "poisoned" if cand.current_headline == SUMMARY_UNAVAILABLE_HEADLINE \
                    else ("has-summary" if cand.current_headline else "missing")
                logger.info("  would process call=%s tenant=%s (%s)",
                            cand.call_id[:8], cand.tenant_id[:8], state)
            logger.info("DRY RUN — no summaries generated.")
            return 0

        if not candidates:
            logger.info("Nothing to backfill. ✔")
            return 0

        est_min = (len(candidates) * args.pace) / 60.0
        logger.info("Backfilling %d call(s), paced %.0fs apart (~%.1f min)…",
                    len(candidates), args.pace, est_min)
        stats = await _run_backfill(
            pool, candidates, pace=args.pace,
            retry_backoff=args.retry_backoff, force=args.force,
        )
        logger.info(
            "Done. candidates=%d succeeded=%d failed=%d skipped=%d",
            stats["candidates"], stats["succeeded"], stats["failed"], stats["skipped"],
        )
        print(stats)
        return 1 if stats["failed"] else 0
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
