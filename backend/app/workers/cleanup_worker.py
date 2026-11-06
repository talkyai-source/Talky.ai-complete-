"""
Data-retention cleanup worker (Fix 21).

Counts/deletes aged telemetry and explicitly opted-in personal-data table rows
in bounded tenant-scoped batches. Missing optional tables are reported/skipped.

Safety contract (deliberate, do not relax without a product decision):
  * DRY-RUN IS THE DEFAULT. Until CLEANUP_DRY_RUN=false is set, the worker
    only counts and logs what it WOULD delete. It deletes nothing.
  * The `calls` table is NEVER changed. Its inline transcripts/summaries remain
    outside this pass, as do audit chains, immutable receipts and Drive files.
  * New personal-data targets have a SECOND opt-in. An existing telemetry
    deployment using CLEANUP_DRY_RUN=false does not start deleting these rows.
  * Batched deletes (LIMIT loop) with a sleep between batches and a hard
    cap on batches per table per run, so a runaway run cannot wedge the DB.
  * stream_events has ROW LEVEL SECURITY enabled. Every batch runs in its
    own transaction with `SET LOCAL app.bypass_rls = 'on'` — transaction-
    scoped, so it stays correct under PgBouncer transaction mode (unlike a
    session-level SET, which can land on a different backend connection).

Runs as a one-shot (systemd timer, nightly). Exit code 0 on success
(including dry-run), 1 on failure so the failure shows in
`systemctl status talky-cleanup` / journald.

Env:
  CLEANUP_DRY_RUN                 default "true"  — "false" to actually delete
  CLEANUP_CALL_EVENTS_DAYS        default "90"
  CLEANUP_CALL_LEGS_DAYS          default "90"
  CLEANUP_STREAM_EVENTS_DAYS      default "90"  (table's own expires_at is 90d)
  CLEANUP_PERSONAL_RETENTION_ENABLED default "false" — requires reviewed windows
  CLEANUP_TRANSCRIPTS_DAYS        proposed default "365" (no Drive-backed rows)
  CLEANUP_CONVERSATIONS_DAYS      proposed default "365"
  CLEANUP_GUARD_DECISIONS_DAYS     proposed default "90"
  CLEANUP_BATCH_SIZE              default "5000"
  CLEANUP_MAX_BATCHES_PER_TABLE   default "200"   (cap: 1M rows/table/run)
  CLEANUP_BATCH_SLEEP_MS          default "100"   (throttle between batches)
  DATABASE_URL                    required (via app.core.db.init_db_pool)

Recording retention (recording_retention_purge.py) runs after the telemetry
pass. It has its OWN dry-run default, independent of CLEANUP_DRY_RUN:
  RECORDING_RETENTION_PURGE_ENABLED   default "false" - "true" to delete
  RECORDING_RETENTION_PURGE_ACTOR_ID  user_profiles.id for the deletion intents
  RECORDING_RETENTION_PURGE_BATCH     default "200" recordings per run
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

import asyncpg

from app.core import db
from app.core.db_utils import acquire_connection
from app.domain.models.retention_config import PROPOSED_CLEANUP_RETENTION_DAYS
from app.workers.recording_retention_purge import (
    purge_actor_id,
    purge_batch_size,
    purge_enabled,
    purge_expired_recordings,
)

logger = logging.getLogger("cleanup_worker")


# ---------------------------------------------------------------------------
# Retention policy — hardcoded table allowlist. `calls` is deliberately
# absent: this bounded pass does not redact its inline content or alter the
# learning/compliance history. Personal tables require a separate owner opt-in.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetentionTarget:
    table: str
    days: int


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer — using default %d", name, raw, default)
        return default
    if value <= 0:
        logger.warning("%s=%d must be > 0 — using default %d", name, value, default)
        return default
    return value


def _load_targets() -> list[RetentionTarget]:
    return [
        RetentionTarget("call_events", _env_int("CLEANUP_CALL_EVENTS_DAYS", 90)),
        RetentionTarget("call_legs", _env_int("CLEANUP_CALL_LEGS_DAYS", 90)),
        RetentionTarget("stream_events", _env_int("CLEANUP_STREAM_EVENTS_DAYS", 90)),
        RetentionTarget("transcripts", _env_int("CLEANUP_TRANSCRIPTS_DAYS", PROPOSED_CLEANUP_RETENTION_DAYS["transcripts"])),
        RetentionTarget("conversations", _env_int("CLEANUP_CONVERSATIONS_DAYS", PROPOSED_CLEANUP_RETENTION_DAYS["conversations"])),
        RetentionTarget("call_guard_decisions", _env_int("CLEANUP_GUARD_DECISIONS_DAYS", PROPOSED_CLEANUP_RETENTION_DAYS["call_guard_decisions"])),
    ]


def _dry_run_enabled() -> bool:
    return (os.getenv("CLEANUP_DRY_RUN", "true") or "").strip().lower() not in {
        "0", "false", "no",
    }


# Advisory lock key (arbitrary but stable): stops a manual run overlapping
# the nightly timer — the second runner exits immediately instead of
# doubling DELETE load on the same tables.
_ADVISORY_LOCK_KEY = 72741001  # stable key for "talky-cleanup"

_PERSONAL_TABLES = frozenset(PROPOSED_CLEANUP_RETENTION_DAYS)
_TABLES = _PERSONAL_TABLES | {"call_events", "call_legs", "stream_events"}


def _utc_now():
    return datetime.now(timezone.utc)


def _personal_retention_enabled():
    return (os.getenv("CLEANUP_PERSONAL_RETENTION_ENABLED", "false") or "").strip().lower() in {
        "1", "true", "yes",
    }


def _target_sql(target):
    """Fixed identifiers; the caller binds tenant, cutoff and batch limit."""
    if target.table not in _TABLES:
        raise ValueError("Unknown retention table")
    join = ""
    owner = "r.tenant_id = $1::uuid"
    outer_owner = owner
    extra = ""
    if target.table in {"call_events", "call_legs", "transcripts"}:
        join = "JOIN public.calls c ON c.id = r.call_id"
        owner = "c.tenant_id = $1::uuid"
        outer_owner = "EXISTS (SELECT 1 FROM public.calls owner_call WHERE owner_call.id = r.call_id AND owner_call.tenant_id = $1::uuid)"
    if target.table == "transcripts":
        # A legacy NULL tenant is owned through its call. A conflicting tenant
        # is excluded, and external files must not be orphaned by row deletion.
        owner += " AND (r.tenant_id = $1::uuid OR r.tenant_id IS NULL)"
        outer_owner += " AND (r.tenant_id = $1::uuid OR r.tenant_id IS NULL)"
        extra = " AND r.drive_file_id IS NULL"
    return join, owner, outer_owner, extra


@asynccontextmanager
async def _tenant_retention_transaction(conn, tenant_id):
    """Hold creation and each bounded batch share the existing 0025 lock."""
    from app.api.v1.endpoints.admin.media import _has_active_compliance_hold

    async with conn.transaction():
        await db._apply_transaction_timeouts(conn, db._transaction_timeout_settings())
        # call_events' append-only trigger permits privileged retention DELETE;
        # every data statement still has an explicit tenant predicate.
        await conn.execute("SET LOCAL app.bypass_rls = 'on'")
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(tenant_id))
        locked = await conn.fetchval(
            "SELECT pg_try_advisory_xact_lock(hashtextextended('talky:media-hold:' || $1::text, 0))",
            str(tenant_id),
        )
        if not locked:
            yield "busy"
        elif await _has_active_compliance_hold(conn, tenant_id):
            yield "held"
        else:
            yield None


async def _count_expired(conn, target, tenant_id, cutoff):
    join, owner, _, extra = _target_sql(target)
    async with _tenant_retention_transaction(conn, tenant_id) as blocked:
        if blocked:
            return 0, blocked
        count = await conn.fetchval(
            f"SELECT count(*) FROM public.{target.table} AS r {join} "
            f"WHERE {owner} AND r.created_at < $2::timestamptz{extra}",
            tenant_id, cutoff,
        )
        return int(count), None


async def _delete_batch(
    conn: asyncpg.Connection, target: RetentionTarget, tenant_id, cutoff, batch_size: int
) -> tuple[int, str | None]:
    """Delete up to batch_size expired rows. Returns rows deleted.

    Runs inside its own transaction so the RLS bypass is SET LOCAL —
    correct under PgBouncer transaction pooling (a session-level SET can
    detach from the query that follows it).
    """
    join, owner, outer_owner, extra = _target_sql(target)
    async with _tenant_retention_transaction(conn, tenant_id) as blocked:
        if blocked:
            return 0, blocked
        result = await conn.execute(
            f"""
            DELETE FROM public.{target.table} AS r
            WHERE {outer_owner} AND r.created_at < $2::timestamptz{extra}
            AND r.id IN (
                SELECT r.id FROM public.{target.table} AS r {join}
                WHERE {owner} AND r.created_at < $2::timestamptz{extra}
                ORDER BY r.created_at, r.id
                LIMIT $3
            )
            """,
            tenant_id, cutoff, batch_size,
        )
    # asyncpg returns 'DELETE N'
    try:
        return int(result.split()[1]), None
    except (IndexError, ValueError) as exc:
        raise RuntimeError("Unknown retention delete result") from exc


async def cleanup_old_records(
    pool: asyncpg.Pool,
    *,
    dry_run: bool,
    batch_size: int,
    max_batches: int,
    batch_sleep_ms: int,
) -> dict[str, object]:
    """Run one retention pass over every target table.

    Returns {table: rows_deleted_or_would_delete}. Raises on lock
    contention or DB error (caller decides exit code).
    """
    if not 1 <= batch_size <= 5000 or not 1 <= max_batches <= 200 or not 0 <= batch_sleep_ms <= 1000:
        raise ValueError("Retention batch/run limits are outside the supported range")
    targets = _load_targets()
    now = _utc_now()
    cutoffs = {target.table: now - timedelta(days=target.days) for target in targets}
    preview_tables = {target.table for target in targets
                      if dry_run or (target.table in _PERSONAL_TABLES and not _personal_retention_enabled())}
    summary: dict[str, object] = {target.table: 0 for target in targets}
    summary.update(missing_tables=[], held_tenants=[], busy_tenants=[],
                   dry_run_tables=sorted(preview_tables), limited_tables=[])
    batches = {target.table: 0 for target in targets}
    held, busy, limited = set(), set(), set()

    async with acquire_connection(pool) as conn:
        locked = await conn.fetchval("SELECT pg_try_advisory_lock($1)", _ADVISORY_LOCK_KEY)
        if not locked:
            raise RuntimeError(
                "another cleanup run holds the advisory lock — refusing to run concurrently"
            )
        try:
            present = {}
            for table in (*_TABLES, "tenants", "calls", "suspension_events"):
                present[table] = bool(await conn.fetchval("SELECT to_regclass($1)", f"public.{table}"))
                if not present[table]:
                    summary["missing_tables"].append(table)
                    logger.warning("cleanup skipped absent table=%s", table)
            if not present["tenants"] or not present["suspension_events"]:
                # Without the directory or hold proof, no unchecked apply (or
                # misleading eligible-row count) is performed.
                return summary
            active_targets = [target for target in targets if present[target.table]
                              and (present["calls"] or target.table not in {"call_events", "call_legs", "transcripts"})]
            cursor = None
            while True:
                # Explicit system-directory discovery; data work below never
                # relies on this bypass for its tenant ownership boundary.
                async with conn.transaction():
                    await db._apply_transaction_timeouts(conn, db._transaction_timeout_settings())
                    await conn.execute("SET LOCAL app.bypass_rls = 'on'")
                    await conn.execute("SET LOCAL row_security = off")
                    tenants = await conn.fetch(
                        "SELECT id FROM public.tenants WHERE ($1::uuid IS NULL OR id > $1::uuid) "
                        "ORDER BY id LIMIT $2", cursor, 100,
                    )
                if not tenants:
                    break
                for tenant in tenants:
                    tenant_id = tenant["id"]
                    for target in active_targets:
                        expired, blocked = await _count_expired(conn, target, tenant_id, cutoffs[target.table])
                        if blocked:
                            (held if blocked == "held" else busy).add(str(tenant_id))
                            continue
                        if target.table in preview_tables:
                            summary[target.table] += expired
                            logger.info("cleanup DRY-RUN table=%s tenant=%s window=%dd would_delete=%d",
                                        target.table, tenant_id, target.days, expired)
                            continue
                        if not expired:
                            continue
                        while batches[target.table] < max_batches:
                            deleted, blocked = await _delete_batch(
                                conn, target, tenant_id, cutoffs[target.table], batch_size,
                            )
                            if blocked:
                                (held if blocked == "held" else busy).add(str(tenant_id))
                                break
                            batches[target.table] += 1
                            summary[target.table] += deleted
                            logger.info("cleanup table=%s tenant=%s batch=%d deleted=%d",
                                        target.table, tenant_id, batches[target.table], deleted)
                            if deleted < batch_size:
                                break
                            if batch_sleep_ms:
                                await asyncio.sleep(batch_sleep_ms / 1000.0)
                        else:
                            limited.add(target.table)
                cursor = tenants[-1]["id"]
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_KEY)

    summary["held_tenants"] = sorted(held)
    summary["busy_tenants"] = sorted(busy)
    summary["limited_tables"] = sorted(limited)
    return summary


async def main() -> int:
    # Workers never ran app_bootstrap.configure_logging, so personal data
    # reached the journal in clear (QA F2-10). Idempotent.
    from app.core.log_redact import install_pii_log_redaction
    install_pii_log_redaction()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    dry_run = _dry_run_enabled()
    batch_size = min(_env_int("CLEANUP_BATCH_SIZE", 5000), 5000)
    max_batches = min(_env_int("CLEANUP_MAX_BATCHES_PER_TABLE", 200), 200)
    batch_sleep_ms = min(_env_int("CLEANUP_BATCH_SLEEP_MS", 100), 1000)

    targets = _load_targets()
    logger.info(
        "cleanup_start dry_run=%s batch_size=%d max_batches=%d sleep_ms=%d targets=%s",
        dry_run, batch_size, max_batches, batch_sleep_ms,
        {t.table: f"{t.days}d" for t in targets},
    )

    from app.core.db import close_db_pool, init_db_pool

    started = time.monotonic()
    try:
        pool = await init_db_pool()
    except Exception as exc:
        logger.error("cleanup_abort: DB pool init failed: %s", exc)
        return 1

    try:
        summary = await cleanup_old_records(
            pool,
            dry_run=dry_run,
            batch_size=batch_size,
            max_batches=max_batches,
            batch_sleep_ms=batch_sleep_ms,
        )
        recording_summary = await purge_expired_recordings(
            pool,
            enabled=purge_enabled(),
            actor_id=purge_actor_id(),
            limit=purge_batch_size(),
        )
        summary["recordings_retention"] = recording_summary
    except Exception as exc:
        logger.error("cleanup_failed: %s", exc, exc_info=True)
        return 1
    finally:
        try:
            await close_db_pool()
        except Exception:
            pass

    elapsed = time.monotonic() - started
    logger.info(
        "cleanup_complete dry_run=%s elapsed_s=%.1f summary=%s",
        dry_run, elapsed, summary,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
