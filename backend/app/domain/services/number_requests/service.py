"""Customer side of a number request: save the request, two documents and two files as one unit.

Every statement carries ``tenant_id = $N`` and runs on ``acquire_with_tenant``.
Nothing here logs a file name, file content or ``str(exc)``: ids, kinds, sizes
and statuses only.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import asyncpg

from app.core.db_utils import acquire_with_tenant
from app.domain.services.number_requests import config, errors
from app.domain.services.number_requests.intake import SealedSubmission
from app.domain.services.number_requests.store import DocumentStore, wrap_dek

logger = logging.getLogger(__name__)

S1_FIND = (
    "SELECT id, use_type, status, submitted_at, request_fingerprint "
    "FROM telephony_number_requests WHERE tenant_id = $1 AND idempotency_key = $2"
)
S2_INSERT_REQUEST = (
    "INSERT INTO telephony_number_requests "
    "(id, tenant_id, requested_by_user_id, use_type, idempotency_key, request_fingerprint) "
    "VALUES ($1, $2, $3, $4, $5, $6) "
    "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING "
    "RETURNING id, use_type, status, submitted_at"
)
S3_INSERT_DOCUMENT = (
    "INSERT INTO telephony_number_request_documents "
    "(id, tenant_id, request_id, kind, storage_key, file_name, content_type, size_bytes, "
    "sha256, wrapped_key, key_provider) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)"
)
S4_DOCUMENTS = (
    "SELECT kind, file_name, content_type, size_bytes "
    "FROM telephony_number_request_documents WHERE tenant_id = $1 AND request_id = $2"
)
S5_EXISTS = "SELECT id FROM telephony_number_requests WHERE tenant_id = $1 AND id = $2"
S6_COUNT_OPEN = (
    "SELECT count(*) FROM telephony_number_requests WHERE tenant_id = $1 AND status = 'submitted'"
)
# The next two read no table: the transaction id and the fate of that transaction.
S7_TXID = "SELECT txid_current()"
S8_TXID_STATUS = "SELECT txid_status($1)"

_CONFIRM_TIMEOUT = 5.0


def _reused() -> errors.NumberRequestRefused:
    return errors.NumberRequestRefused(
        400, "idempotency_key_reused",
        "This Idempotency-Key was already used with different documents. Send a new key.",
        field="Idempotency-Key")


async def _build_out(conn: Any, tenant_id: str, row: Any) -> dict:
    docs = await conn.fetch(S4_DOCUMENTS, tenant_id, row["id"])
    by_kind = {d["kind"]: d for d in docs}
    ordered = []
    for kind in config.USE_TYPES[row["use_type"]]:
        d = by_kind.get(kind)
        if d is not None:
            ordered.append({
                "kind": kind,
                "file_name": d["file_name"],
                "content_type": d["content_type"],
                "size_bytes": d["size_bytes"],
            })
    return {
        "id": row["id"],
        "use_type": row["use_type"],
        "status": row["status"],
        "submitted_at": row["submitted_at"],
        "documents": ordered,
    }


async def _replay_or_refuse(conn: Any, tenant_id: str, row: Any, fingerprint: str) -> dict:
    if row["request_fingerprint"] != fingerprint:
        raise _reused()
    return await _build_out(conn, tenant_id, row)


async def _confirm_committed(pool: asyncpg.Pool, tenant_id: str, fingerprint: str,
                             idempotency_key: str, txid: int | None) -> tuple[str, dict | None]:
    """Ask the database for the fate of OUR transaction on a fresh connection.

    'committed' (with the answer as a replay), 'aborted', or 'unknown' (in progress,
    NULL, an error, a timeout, or no transaction id was ever obtained).
    """
    if txid is None:
        return "unknown", None

    async def _check() -> tuple[str, dict | None]:
        async with acquire_with_tenant(pool, tenant_id) as conn:
            status = await conn.fetchval(S8_TXID_STATUS, txid)
            if status == "aborted":
                return "aborted", None
            if status != "committed":
                return "unknown", None
            row = await conn.fetchrow(S1_FIND, tenant_id, idempotency_key)
            if row is None:
                return "unknown", None
            return "committed", await _replay_or_refuse(conn, tenant_id, row, fingerprint)

    try:
        return await asyncio.wait_for(asyncio.shield(_check()), timeout=_CONFIRM_TIMEOUT)
    except Exception:
        return "unknown", None


async def submit(pool: asyncpg.Pool, *, tenant_id: str, user_id: str, idempotency_key: str,
                 sealed: SealedSubmission, store: DocumentStore) -> tuple[dict, bool]:
    """Save a sealed submission. Returns (answer, replayed)."""
    tenant_id, user_id = str(tenant_id), str(user_id)
    published: list[str] = []
    request_id = str(uuid.uuid4())
    txid: int | None = None
    try:
        try:
            # 1. wrap both keys; a failure here saves nothing.
            wrapped = [await wrap_dek(f.dek) for f in sealed.files]
        except Exception:
            logger.error("number_request_key_wrap_failed tenant=%s", tenant_id)
            raise errors.StorageUnavailable("keys could not be wrapped") from None

        try:
            async with acquire_with_tenant(pool, tenant_id, user_id=user_id) as conn:
                # the id of this transaction, so a failure later can ask for its fate
                txid = await conn.fetchval(S7_TXID)
                # 2. an earlier answer for this key?
                existing = await conn.fetchrow(S1_FIND, tenant_id, idempotency_key)
                if existing is not None:
                    return await _replay_or_refuse(conn, tenant_id, existing, sealed.fingerprint), True
                # 2b. cap on requests still waiting for review. A replay was answered above.
                # Two simultaneous first submits may both pass this check and exceed the cap
                # by one: accepted.
                if await conn.fetchval(S6_COUNT_OPEN, tenant_id) >= config.MAX_OPEN_REQUESTS_PER_ACCOUNT:
                    raise errors.too_many_open_requests(config.MAX_OPEN_REQUESTS_PER_ACCOUNT)
                # 3. claim the key; None means a twin committed first.
                row = await conn.fetchrow(
                    S2_INSERT_REQUEST, request_id, tenant_id, user_id, sealed.use_type,
                    idempotency_key, sealed.fingerprint)
                if row is None:
                    twin = await conn.fetchrow(S1_FIND, tenant_id, idempotency_key)
                    if twin is None:
                        raise errors.StorageUnavailable("request could not be recorded")
                    return await _replay_or_refuse(conn, tenant_id, twin, sealed.fingerprint), True
                # 4. the two document rows
                for f, (wrapped_key, provider) in zip(sealed.files, wrapped):
                    await conn.execute(
                        S3_INSERT_DOCUMENT, f.document_id, tenant_id, request_id, f.kind,
                        f.storage_key, f.file_name, f.content_type, f.size_bytes, f.sha256,
                        wrapped_key, provider)
                # 5. publish the files, still inside the transaction
                for f in sealed.files:
                    await asyncio.to_thread(store.publish, f.temp_path, f.storage_key)
                    published.append(f.storage_key)
                out = {
                    "id": row["id"],
                    "use_type": row["use_type"],
                    "status": row["status"],
                    "submitted_at": row["submitted_at"],
                    "documents": [
                        {"kind": f.kind, "file_name": f.file_name,
                         "content_type": f.content_type, "size_bytes": f.size_bytes}
                        for f in sealed.files
                    ],
                }
            # 6. committed when the block exited
        except (errors.NumberRequestRefused, errors.StorageUnavailable):
            await _remove_published(store, published)
            raise
        except Exception:
            logger.error("number_request_save_failed tenant=%s request=%s published=%d",
                         tenant_id, request_id, len(published))
            if not published:
                raise errors.StorageUnavailable("nothing was saved") from None
            state, answer = await _confirm_committed(
                pool, tenant_id, sealed.fingerprint, idempotency_key, txid)
            if state == "committed" and answer is not None:
                return answer, True
            if state == "aborted":
                await _remove_published(store, published)
                raise errors.StorageUnavailable("documents could not be saved") from None
            # unknown: keep the files; the nightly sweep removes them only if no row exists.
            raise errors.SaveUnconfirmed("save not confirmed") from None

        logger.info(
            "number_request_saved request=%s tenant=%s use_type=%s kinds=%s sizes=%s status=%s",
            request_id, tenant_id, sealed.use_type,
            ",".join(f.kind for f in sealed.files),
            ",".join(str(f.size_bytes) for f in sealed.files), out["status"])
        return out, False
    finally:
        # 7. temps are always discarded (published ones are already gone).
        for f in sealed.files:
            try:
                store.discard(f.temp_path)
            except Exception:
                logger.warning("number_request_temp_discard_failed tenant=%s", tenant_id)


async def _remove_published(store: DocumentStore, keys: list[str]) -> None:
    for key in keys:
        try:
            await asyncio.to_thread(store.remove, key)
        except Exception:
            logger.warning("number_request_remove_failed")
    keys.clear()
