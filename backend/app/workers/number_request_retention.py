"""Retention of number-request documents, plus removal of leftover files.

Runs nightly from the clean-up worker. Two parts:

1. Retention (only when the owner has set a time; the default is KEEP, so by
   default this part reads and deletes nothing):
     * rejected requests, ``NUMBER_REQUEST_REJECTED_RETENTION_DAYS`` after the
       decision;
     * approved requests, ``NUMBER_REQUEST_RELEASED_RETENTION_DAYS`` after
       ``number_released_at`` (an approved request with no release date is never
       selected).
   Per request, in ONE transaction the document rows are marked deleted
   (``files_deleted_at`` set, ``wrapped_key`` and ``file_name`` cleared, which
   destroys the key) and the request row is marked. Only after that commit are
   the files removed. A crash between the two leaves unreadable files with no
   live row; the leftover sweep below removes them. The reverse order (file
   gone, row not marked) cannot happen.
2. Leftovers, always: ``.incoming/*.part`` files older than the grace, and
   published files older than the grace that no live document row refers to.

The leftover sweep proves, in the same transaction as its row lookup, that the
connection really carries the account setting (``P1``) and that the account
exists in this database (``P2``): a connection whose setting did not take effect
reads nothing under forced row security, and a wrong or partly copied database
has no rows either; "no row came back" must never be taken as "no row exists"
(it would delete customers' documents). A brake stops the sweep when it would
remove more than ``config.SWEEP_MAX_REMOVALS_PER_RUN`` published files in one run.

``dry_run`` (the clean-up worker's own setting, required) changes nothing at all:
it only counts what it would do.

Work goes account by account, every statement carrying that account's
``tenant_id`` predicate. Logs carry ids, counts and reasons only: never a file
name or content.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.db_utils import acquire_with_tenant
from app.domain.services.number_requests import config
from app.domain.services.number_requests.store import DocumentStore, get_store

logger = logging.getLogger("number_request_retention")

_TENANT_PAGE = 500

T0 = (
    "SELECT id FROM tenants WHERE ($1::uuid IS NULL OR id > $1::uuid) "
    "ORDER BY id LIMIT $2"
)
R1 = (
    "SELECT id FROM telephony_number_requests WHERE tenant_id = $1 AND status = 'rejected' "
    "AND files_deleted_at IS NULL AND reviewed_at < $2 ORDER BY reviewed_at LIMIT $3"
)
R2 = (
    "SELECT id FROM telephony_number_requests WHERE tenant_id = $1 AND status = 'approved' "
    "AND files_deleted_at IS NULL AND number_released_at < $2 ORDER BY number_released_at LIMIT $3"
)
R3 = (
    "SELECT id, storage_key FROM telephony_number_request_documents "
    "WHERE tenant_id = $1 AND request_id = $2 AND files_deleted_at IS NULL FOR UPDATE"
)
R4 = (
    "UPDATE telephony_number_request_documents SET files_deleted_at = now(), "
    "wrapped_key = NULL, file_name = NULL "
    "WHERE tenant_id = $1 AND request_id = $2 AND files_deleted_at IS NULL"
)
R5 = (
    "UPDATE telephony_number_requests SET files_deleted_at = now(), updated_at = now() "
    "WHERE tenant_id = $1 AND id = $2 AND files_deleted_at IS NULL"
)
P1 = "SELECT NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid = $1::uuid"
P2 = "SELECT EXISTS(SELECT 1 FROM tenants WHERE id = $1)"
R6 = (
    "SELECT id FROM telephony_number_request_documents "
    "WHERE tenant_id = $1 AND files_deleted_at IS NULL AND id = ANY($2::uuid[])"
)


def _uuid_or_none(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None


async def _account_ids(pool: Any) -> list[Any]:
    ids: list[Any] = []
    cursor = None
    while True:
        async with acquire_with_tenant(pool, None) as conn:
            page = await conn.fetch(T0, cursor, _TENANT_PAGE)
        if not page:
            return ids
        ids.extend(row["id"] for row in page)
        cursor = page[-1]["id"]


async def _retire_request(pool: Any, tenant_id: Any, request_id: Any, store: DocumentStore,
                          rule: str, totals: dict) -> None:
    """Mark one request's documents deleted (one transaction), then remove the files."""
    async with acquire_with_tenant(pool, tenant_id) as conn:
        docs = await conn.fetch(R3, tenant_id, request_id)
        await conn.execute(R4, tenant_id, request_id)
        await conn.execute(R5, tenant_id, request_id)
    # Committed: the keys are gone. Now the files; a failure here leaves a
    # key-less file that the leftover sweep removes after the grace.
    totals["requests"] += 1
    for doc in docs:
        try:
            removed = store.remove(doc["storage_key"])
        except Exception as exc:
            totals["remove_failed"] += 1
            logger.error("number_request_file_remove_failed tenant=%s request=%s document=%s error=%s",
                         tenant_id, request_id, doc["id"], type(exc).__name__)
            continue
        if removed:
            totals["files_removed"] += 1
        else:
            totals["already_missing"] += 1
            logger.warning("number_request_file_already_missing tenant=%s request=%s document=%s",
                           tenant_id, request_id, doc["id"])
    logger.info("number_request_files_deleted tenant=%s request=%s rule=%s documents=%d",
                tenant_id, request_id, rule, len(docs))


async def _would_retire_request(pool: Any, tenant_id: Any, request_id: Any, totals: dict) -> None:
    """Dry run: count the request and its documents; change nothing."""
    async with acquire_with_tenant(pool, tenant_id) as conn:
        docs = await conn.fetch(R3, tenant_id, request_id)
    totals["would_retire"] += 1
    totals["would_remove_files"] += len(docs)


async def _retention_pass(pool: Any, store: DocumentStore, now: datetime, limit: int,
                          rules: list[tuple[str, str, datetime]], totals: dict,
                          dry_run: bool) -> None:
    budget = limit
    for tenant_id in await _account_ids(pool):
        for rule, sql, cutoff in rules:
            if budget <= 0:
                return
            try:
                async with acquire_with_tenant(pool, tenant_id) as conn:
                    if rule == "rejected":
                        found = await conn.fetch(R1, tenant_id, cutoff, budget)
                    else:
                        found = await conn.fetch(R2, tenant_id, cutoff, budget)
                    request_ids = [r["id"] for r in found]
                for request_id in request_ids:
                    if dry_run:
                        await _would_retire_request(pool, tenant_id, request_id, totals)
                    else:
                        await _retire_request(pool, tenant_id, request_id, store, rule, totals)
                    budget -= 1
            except Exception as exc:
                totals["account_failures"] += 1
                logger.error("number_request_retention_account_failed tenant=%s rule=%s error=%s",
                             tenant_id, rule, type(exc).__name__)


async def _collect_orphans(pool: Any, store: DocumentStore, cutoff_ts: float,
                           totals: dict) -> list[tuple[str, str]]:
    """Published files older than the grace that no live row refers to, from accounts
    proven (setting in force AND account exists here). Returns (tenant, document) pairs."""
    by_account: dict[str, list[Any]] = {}
    for published in list(store.iter_published()):
        if published.mtime >= cutoff_ts:
            continue
        tenant = _uuid_or_none(published.tenant_id)
        document = _uuid_or_none(published.document_id)
        if tenant and document:
            by_account.setdefault(tenant, []).append(published)
    candidates: list[tuple[str, str]] = []
    for tenant, files in by_account.items():
        known = False
        try:
            async with acquire_with_tenant(pool, tenant) as conn:
                try:
                    proven = (await conn.fetchval(P1, tenant)) is True
                except Exception:
                    proven = False
                live = set()
                if proven:
                    known = (await conn.fetchval(P2, tenant)) is True
                if proven and known:
                    live = {str(r["id"]) for r in await conn.fetch(
                        R6, tenant, [_uuid_or_none(f.document_id) for f in files])}
        except Exception as exc:
            # Unsure whether a row exists: never delete.
            totals["account_failures"] += 1
            logger.error("number_request_orphan_check_failed tenant=%s error=%s",
                         tenant, type(exc).__name__)
            continue
        if not proven:
            # The account setting is not proven in force: an empty answer proves nothing.
            totals["account_failures"] += 1
            logger.error("number_request_orphan_check_unproven tenant=%s", tenant)
            continue
        if not known:
            # This database does not know the account (wrong or partly copied database).
            totals["account_failures"] += 1
            logger.error("number_request_orphan_check_unknown_account tenant=%s", tenant)
            continue
        for published in files:
            document = _uuid_or_none(published.document_id)
            if document not in live:
                candidates.append((tenant, document))
    return candidates


async def _sweep_leftovers(pool: Any, store: DocumentStore, now: datetime, totals: dict,
                           dry_run: bool) -> None:
    cutoff_ts = (now - config.ORPHAN_GRACE).timestamp()
    # Temp files in .incoming are not counted by the brake: no record refers to them.
    for path, mtime in list(store.iter_incoming()):
        if mtime < cutoff_ts:
            if dry_run:
                totals["would_remove_temps"] += 1
                continue
            try:
                store.discard(path)
                totals["orphans_removed"] += 1
            except Exception as exc:
                totals["remove_failed"] += 1
                logger.error("number_request_orphan_remove_failed error=%s", type(exc).__name__)

    candidates = await _collect_orphans(pool, store, cutoff_ts, totals)
    limit = config.SWEEP_MAX_REMOVALS_PER_RUN
    if len(candidates) > limit:
        totals["error"] = "sweep_brake"
        logger.error("number_request_sweep_brake candidates=%d limit=%d", len(candidates), limit)
        return
    if dry_run:
        totals["would_remove_orphans"] += len(candidates)
        return
    for tenant, document in candidates:
        try:
            if store.remove(f"{tenant}/{document}.tnr"):
                totals["orphans_removed"] += 1
                logger.info("number_request_orphan_removed tenant=%s document=%s", tenant, document)
        except Exception as exc:
            totals["remove_failed"] += 1
            logger.error("number_request_orphan_remove_failed tenant=%s document=%s error=%s",
                         tenant, document, type(exc).__name__)


async def purge_number_request_documents(pool: Any, *, dry_run: bool, now: datetime | None = None,
                                         limit: int = 200,
                                         store: DocumentStore | None = None) -> dict:
    """Never raises: a failure is logged and reported in the returned summary.

    ``dry_run`` has no default on purpose: a caller that forgets it fails loudly.
    When true nothing is changed; the ``would_*`` counts say what would happen.
    """
    totals: dict[str, Any] = {
        "rules": "none", "dry_run": bool(dry_run), "requests": 0, "files_removed": 0,
        "already_missing": 0, "remove_failed": 0, "orphans_removed": 0, "account_failures": 0,
        "would_retire": 0, "would_remove_files": 0, "would_remove_orphans": 0,
        "would_remove_temps": 0, "invalid_settings": [], "error": None}
    try:
        now = now or datetime.now(timezone.utc)
        store = store or get_store()
        totals["invalid_settings"] = config.retention_setting_problems()
        for name in totals["invalid_settings"]:
            logger.error("number_request_retention_setting_invalid name=%s", name)
        rules: list[tuple[str, str, datetime]] = []
        rejected_days = config.rejected_retention_days()
        released_days = config.released_retention_days()
        if rejected_days is not None:
            rules.append(("rejected", R1, now - timedelta(days=rejected_days)))
        if released_days is not None:
            rules.append(("released", R2, now - timedelta(days=released_days)))
        totals["rules"] = ",".join(r[0] for r in rules) or "none"
        if rules:
            try:
                await _retention_pass(pool, store, now, max(1, int(limit)), rules, totals,
                                      bool(dry_run))
            except Exception as exc:
                totals["error"] = type(exc).__name__
                logger.error("number_request_retention_failed error=%s", type(exc).__name__)
        try:
            await _sweep_leftovers(pool, store, now, totals, bool(dry_run))
        except Exception as exc:
            totals["error"] = type(exc).__name__
            logger.error("number_request_sweep_failed error=%s", type(exc).__name__)
    except Exception as exc:
        totals["error"] = type(exc).__name__
        logger.error("number_request_retention_failed error=%s", type(exc).__name__)
    if dry_run:
        logger.info("number_request_retention_dry_run would_retire=%d would_remove_files=%d "
                    "would_remove_orphans=%d would_remove_temps=%d", totals["would_retire"],
                    totals["would_remove_files"], totals["would_remove_orphans"],
                    totals["would_remove_temps"])
    problem = bool(totals["error"] or totals["remove_failed"] or totals["account_failures"]
                   or totals["invalid_settings"])
    logger.log(
        logging.ERROR if problem else logging.INFO,
        "number_request_retention rules=%s dry_run=%s requests=%d files_removed=%d "
        "already_missing=%d remove_failed=%d orphans_removed=%d account_failures=%d "
        "would_retire=%d would_remove_files=%d would_remove_orphans=%d would_remove_temps=%d "
        "invalid_settings=%s error=%s",
        totals["rules"], totals["dry_run"], totals["requests"], totals["files_removed"],
        totals["already_missing"], totals["remove_failed"], totals["orphans_removed"],
        totals["account_failures"], totals["would_retire"], totals["would_remove_files"],
        totals["would_remove_orphans"], totals["would_remove_temps"],
        ",".join(totals["invalid_settings"]) or "none", totals["error"] or "none")
    return totals
