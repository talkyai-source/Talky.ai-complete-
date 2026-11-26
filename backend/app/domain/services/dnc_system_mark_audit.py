"""Audit row for a do-not-call mark made by the system (QA F12-10).

Two marks are made with no signed-in user: the spam web hook's and the caller's
own opt-out. The mark is written first, exactly as before. Its audit row is
written afterwards, in its own transaction, so a busy audit chain lock (a data
export holds it up to 30 s) can never delay, cancel or undo the mark. If the
row cannot be written one ERROR names the account and the entry (ids and the
error's type only: no phone number, no message text).
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Optional

import asyncpg

from app.core.db_utils import acquire_with_tenant
from app.domain.services.audit_logger import _CHAIN_LOCK_KEY, AuditLogger
from app.domain.services.dnc_service import _audit_dnc_change

logger = logging.getLogger(__name__)

MAX_TRIES = 5
# Waits between tries; they add up to more than the 30 s a data export may hold
# the audit chain lock. No connection is held while waiting.
RETRY_WAITS_S = (2.0, 5.0, 10.0, 20.0)
# Only a busy lock or connection trouble is retried; any other database error
# (for example a missing permission) ends at once with the ERROR line.
_RETRYABLE = (
    asyncpg.LockNotAvailableError,
    asyncpg.OperatorInterventionError,  # query cancelled, admin/crash shutdown, cannot connect now
    asyncpg.DeadlockDetectedError, asyncpg.PostgresConnectionError,
    asyncpg.TooManyConnectionsError, asyncpg.InterfaceError,
    OSError, asyncio.TimeoutError,
)
# Longest one try waits for the chain lock while holding a pool connection
# (a module integer, never caller input; passed as a bound parameter).
CHAIN_WAIT_MS = 500
JITTER = (0.7, 1.3)  # each wait is scaled by a random factor in this range
_pending: set[asyncio.Task] = set()


class ChainBusy(Exception):
    """The audit chain lock is held elsewhere; try again later."""


async def write_mark_audit(pool: Any, *, tenant_id: str, entry_id: str, actor_role: str) -> bool:
    """ONE try: lock the audit chain, re-read the entry, write the audit row."""
    audit = AuditLogger(pool)
    async with acquire_with_tenant(pool, str(tenant_id)) as conn:
        # Wait for the chain lock only briefly (a connection is held at most
        # CHAIN_WAIT_MS per try), as lock_transaction does but with a short
        # timeout. On failure the transaction ends and the connection goes back,
        # so no savepoint is needed.
        # The transaction's own lock limit is put back afterwards ("TO DEFAULT"
        # would leave the rest of this transaction with no limit at all).
        try:
            own_limit = await conn.fetchval("SELECT current_setting('lock_timeout')")
            await conn.fetchval("SELECT set_config('lock_timeout', $1, true)", f"{int(CHAIN_WAIT_MS)}ms")
            await conn.fetchval("SELECT pg_advisory_xact_lock($1::bigint)", _CHAIN_LOCK_KEY)
            await conn.fetchval("SELECT set_config('lock_timeout', $1, true)", own_limit)
        except asyncpg.LockNotAvailableError as exc:
            raise ChainBusy() from exc
        await audit.lock_transaction(conn)  # already ours: returns at once
        row = await conn.fetchrow(
            "SELECT id, tenant_id, normalized_number, source, reason, expires_at, "
            "created_at FROM dnc_entries WHERE tenant_id = $1::uuid AND id = $2::uuid",
            str(tenant_id), str(entry_id),
        )
        if row is None:
            logger.info("dnc_mark_audit_skipped reason=entry_gone tenant=%s entry=%s",
                        tenant_id, entry_id)
            return False
        await _audit_dnc_change(
            audit, conn, "dnc.entry.add", row, tenant_id=str(tenant_id), actor_id=None,
            actor_role=actor_role, after=True, actor_type="system",
        )
    return True


async def write_mark_audit_with_retries(
    pool: Any, *, tenant_id: str, entry_id: str, actor_role: str,
) -> bool:
    """Up to MAX_TRIES tries; never raises except to propagate a cancellation."""
    attempts = 0
    error: Optional[BaseException] = None
    try:
        while attempts < MAX_TRIES:
            attempts += 1
            try:
                return await write_mark_audit(
                    pool, tenant_id=tenant_id, entry_id=entry_id, actor_role=actor_role)
            except (ChainBusy, *_RETRYABLE) as exc:
                error = exc
                if attempts >= MAX_TRIES:
                    break
                wait = RETRY_WAITS_S[min(attempts - 1, len(RETRY_WAITS_S) - 1)]
                await asyncio.sleep(wait * random.uniform(*JITTER))  # noqa: S311 (not security)
            except Exception as exc:  # programming error, fake pool: no point retrying
                error = exc
                break
    except asyncio.CancelledError as exc:
        _log_missing(tenant_id, entry_id, actor_role, attempts, exc)
        raise
    _log_missing(tenant_id, entry_id, actor_role, attempts, error)
    return False


def _log_missing(tenant_id, entry_id, actor_role, attempts, error) -> None:
    logger.error(
        "dnc_mark_audit_missing tenant=%s entry=%s role=%s attempts=%d error_type=%s",
        tenant_id, entry_id, actor_role, attempts, type(error).__name__,
    )


def audit_system_mark_soon(
    pool: Any, *, tenant_id: Any, entry_id: Any, actor_role: str,
) -> Optional[asyncio.Task]:
    """Start the audit write as a task kept alive until done. Never raises."""
    try:
        if not entry_id or not tenant_id:
            logger.debug("dnc_mark_audit not scheduled: no entry id")
            return None
        task = asyncio.get_running_loop().create_task(write_mark_audit_with_retries(
            pool, tenant_id=str(tenant_id), entry_id=str(entry_id), actor_role=actor_role))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
        return task
    except Exception:
        logger.debug("dnc_mark_audit not scheduled", exc_info=True)
        return None
