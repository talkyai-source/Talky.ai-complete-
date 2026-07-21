"""
Data-retention cleanup worker (Fix 21).

Deletes aged rows from the high-growth telemetry tables — call_events,
stream_events, call_legs — in small batches so the DELETEs never lock a
hot table for long.

Safety contract (deliberate, do not relax without a product decision):
  * DRY-RUN IS THE DEFAULT. Until CLEANUP_DRY_RUN=false is set, the worker
    only counts and logs what it WOULD delete. It deletes nothing.
  * The `calls` table is NEVER touched. The table allowlist below is
    hardcoded — retention targets are not env-driven, only the windows are.
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
  CLEANUP_BATCH_SIZE              default "5000"
  CLEANUP_MAX_BATCHES_PER_TABLE   default "200"   (cap: 1M rows/table/run)
  CLEANUP_BATCH_SLEEP_MS          default "100"   (throttle between batches)
  DATABASE_URL                    required (via app.core.db.init_db_pool)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass

import asyncpg

logger = logging.getLogger("cleanup_worker")


# ---------------------------------------------------------------------------
# Retention policy — hardcoded table allowlist. `calls` is deliberately
# absent and must stay absent: call rows feed the call-details UI, the
# learning loop, and compliance history. Only the three telemetry tables
# from Fix 21 are in scope.
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
    ]


def _dry_run_enabled() -> bool:
    return (os.getenv("CLEANUP_DRY_RUN", "true") or "").strip().lower() not in {
        "0", "false", "no",
    }


# Advisory lock key (arbitrary but stable): stops a manual run overlapping
# the nightly timer — the second runner exits immediately instead of
# doubling DELETE load on the same tables.
_ADVISORY_LOCK_KEY = 72741001  # stable key for "talky-cleanup"


async def _count_expired(conn: asyncpg.Connection, target: RetentionTarget) -> int:
    # Same SET LOCAL bypass as _delete_batch: stream_events has RLS enabled,
    # so counting WITHOUT the bypass returns 0 for an anonymous connection —
    # which would make the run log "nothing to do" and silently skip the
    # table (in dry-run AND real runs). Transaction-scoped, PgBouncer-safe.
    async with conn.transaction():
        await conn.execute("SET LOCAL app.bypass_rls = 'on'")
        return await conn.fetchval(
            f"SELECT count(*) FROM {target.table} WHERE created_at < NOW() - make_interval(days => $1)",
            target.days,
        )


async def _delete_batch(
    conn: asyncpg.Connection, target: RetentionTarget, batch_size: int
) -> int:
    """Delete up to batch_size expired rows. Returns rows deleted.

    Runs inside its own transaction so the RLS bypass is SET LOCAL —
    correct under PgBouncer transaction pooling (a session-level SET can
    detach from the query that follows it).
    """
    async with conn.transaction():
        await conn.execute("SET LOCAL app.bypass_rls = 'on'")
        result = await conn.execute(
            f"""
            DELETE FROM {target.table}
            WHERE id IN (
                SELECT id FROM {target.table}
                WHERE created_at < NOW() - make_interval(days => $1)
                ORDER BY created_at
                LIMIT $2
            )
            """,
            target.days,
            batch_size,
        )
    # asyncpg returns 'DELETE N'
    try:
        return int(result.split()[1])
    except (IndexError, ValueError):
        return 0


async def cleanup_old_records(
    pool: asyncpg.Pool,
    *,
    dry_run: bool,
    batch_size: int,
    max_batches: int,
    batch_sleep_ms: int,
) -> dict[str, int]:
    """Run one retention pass over every target table.

    Returns {table: rows_deleted_or_would_delete}. Raises on lock
    contention or DB error (caller decides exit code).
    """
    summary: dict[str, int] = {}

    async with pool.acquire() as conn:
        locked = await conn.fetchval("SELECT pg_try_advisory_lock($1)", _ADVISORY_LOCK_KEY)
        if not locked:
            raise RuntimeError(
                "another cleanup run holds the advisory lock — refusing to run concurrently"
            )
        try:
            for target in _load_targets():
                expired = await _count_expired(conn, target)
                if expired == 0:
                    logger.info("cleanup table=%s window=%dd expired=0 — nothing to do",
                                target.table, target.days)
                    summary[target.table] = 0
                    continue

                if dry_run:
                    logger.info(
                        "cleanup DRY-RUN table=%s window=%dd would_delete=%d "
                        "(set CLEANUP_DRY_RUN=false to enable)",
                        target.table, target.days, expired,
                    )
                    summary[target.table] = expired
                    continue

                deleted_total = 0
                for batch_no in range(1, max_batches + 1):
                    deleted = await _delete_batch(conn, target, batch_size)
                    deleted_total += deleted
                    logger.info(
                        "cleanup table=%s batch=%d deleted=%d total=%d remaining~=%d",
                        target.table, batch_no, deleted, deleted_total,
                        max(expired - deleted_total, 0),
                    )
                    if deleted < batch_size:
                        break  # table drained for this window
                    if batch_sleep_ms:
                        await asyncio.sleep(batch_sleep_ms / 1000.0)
                else:
                    logger.warning(
                        "cleanup table=%s hit max_batches=%d (%d rows) — "
                        "remainder continues next run",
                        target.table, max_batches, deleted_total,
                    )
                summary[target.table] = deleted_total
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_KEY)

    return summary


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    dry_run = _dry_run_enabled()
    batch_size = _env_int("CLEANUP_BATCH_SIZE", 5000)
    max_batches = _env_int("CLEANUP_MAX_BATCHES_PER_TABLE", 200)
    batch_sleep_ms = _env_int("CLEANUP_BATCH_SLEEP_MS", 100)

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
