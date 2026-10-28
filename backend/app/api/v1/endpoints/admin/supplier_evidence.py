"""Private retained supplier exports; no supplier calls or customer billing writes."""
from __future__ import annotations

import asyncio
import json
from datetime import date
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_db_client, require_platform_admin
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.platform_admin_guard import require_platform_admin_mutation
from app.core.security.principal import PrincipalUnavailable, load_session_principal
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.services.supplier_evidence import ImportEvidence, ReviewEvidence, money_text, parse_evidence, reconciliation_comparison

router = APIRouter()
_COLUMNS = """id,request_id,supplier,source_kind,account_id,reference,period_start,period_end,
    currency,reported_charges,source_sha256,evidence_sha256,normalized,imported_by,imported_at,supersedes_id"""


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


async def _session(conn, request, user):
    actor = str(user.id)
    sid = getattr(request.state, "authenticated_session_id", None)
    if getattr(request.state, "authenticated_user_id", None) != actor or not sid:
        raise HTTPException(401, "Current authenticated session is required.")
    try:
        principal = await load_session_principal(conn, {"sub": actor, "sid": sid, "tenant_id": user.tenant_id})
    except PrincipalUnavailable as exc:
        raise HTTPException(401, "Current account or session is unavailable.") from exc
    if principal["role"] != "platform_admin":
        raise HTTPException(403, "Current platform admin authority is required.")


async def _record(conn, audit, user, row, action, *, metadata=None):
    return await audit.log_in_transaction(conn, event_type=AuditEvent.CONFIG_CHANGED,
        action=action, actor_id=user.id, actor_role="platform_admin",
        resource_type="supplier_statement", resource_id=row["id"],
        metadata={"source_sha256": row["source_sha256"], **(metadata or {})})


async def _view(conn, row):
    result = dict(row)
    result.pop("source_bytes", None)
    result["normalized"] = _json(result["normalized"])
    result["normalized"]["entry_count"] = len(result["normalized"].pop("entries", []))
    result["reported_charges"] = money_text(result["reported_charges"])
    result["reviews"] = [dict(item) for item in await conn.fetch(
        "SELECT id,request_id,stage,review_reference,notes,reviewer_id,recorded_at,comparison "
        "FROM supplier_statement_reviews WHERE statement_id=$1 ORDER BY recorded_at,id", row["id"])]
    for review in result["reviews"]:
        review["comparison"] = _json(review["comparison"])
    stages = {review["stage"] for review in result["reviews"]}
    result["stage"] = "reconciled" if "reconciled" in stages else "reviewed" if "reviewed" in stages else "imported"
    result["superseded_by"] = await conn.fetchval(
        "SELECT id FROM supplier_statement_evidence WHERE supersedes_id=$1", row["id"])
    result["comparison"] = reconciliation_comparison(result)
    return result


@router.get("/usage/statements")
async def list_statements(request: Request, from_date: date, to_date: date,
        limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=10000),
        user: CurrentUser = Depends(require_platform_admin), db: Client = Depends(get_db_client)):
    if to_date < from_date:
        raise HTTPException(400, "Use an ordered date range.")
    try:
        async with asyncio.timeout(8), acquire_with_tenant(db.pool, None, user_id=user.id, timeout=3) as conn:
            await _session(conn, request, user)
            rows = await conn.fetch(f"""SELECT {_COLUMNS} FROM supplier_statement_evidence
                WHERE period_start <= $2 AND period_end >= $1
                ORDER BY imported_at DESC,id DESC LIMIT $3 OFFSET $4""", from_date, to_date, limit+1, offset)
            items = [await _view(conn, row) for row in rows[:limit]]
        return {"items": items, "has_more": len(rows) > limit, "limit": limit, "offset": offset,
                "period_start": str(from_date), "period_end": str(to_date),
                "coverage": "listed_account_periods_only", "missing_supplier_coverage": ["cartesia", "groq"],
                "net_spend": None,
                "note": "Reported charges are not net spend or paid invoices. Overlapping captures are not added together; tenant allocation, credits and other supplier coverage are unavailable."}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Supplier evidence is unavailable. No zero total is inferred.") from exc


@router.get("/usage/statements/imports/{request_id}")
async def get_import(request_id: UUID, request: Request,
        user: CurrentUser = Depends(require_platform_admin), db: Client = Depends(get_db_client)):
    async with asyncio.timeout(8), acquire_with_tenant(db.pool, None, user_id=user.id, timeout=3) as conn:
        await _session(conn, request, user)
        row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM supplier_statement_evidence WHERE request_id=$1", request_id)
        if row is None:
            raise HTTPException(404, "No saved import was found for this request.")
        return await _view(conn, row)


@router.post("/usage/statements")
async def import_statement(body: ImportEvidence, request: Request,
        user: CurrentUser = Depends(require_platform_admin), db: Client = Depends(get_db_client),
        audit: AuditLogger = Depends(get_audit_logger)):
    try:
        parsed = parse_evidence(body)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, "Unsupported supplier export. Use the documented unfiltered daily Deepgram JSON, at most 1 MiB and 2,000 rows.") from exc
    try:
        async with asyncio.timeout(8), acquire_with_tenant(db.pool, None, user_id=user.id, timeout=3) as conn:
            await audit.lock_transaction(conn)
            await require_platform_admin_mutation(conn, user.id)
            await _session(conn, request, user)
            matches = await conn.fetch(f"""SELECT {_COLUMNS} FROM supplier_statement_evidence
                WHERE request_id=$1 OR (supplier='deepgram' AND account_id=$2 AND reference=$3)""",
                body.request_id, body.account_id, body.reference)
            if matches:
                if len(matches) != 1 or matches[0]["evidence_sha256"] != parsed["evidence_sha256"]:
                    raise HTTPException(409, "This import identity already has different evidence. Retain it and create an explicit correction.")
                return await _view(conn, matches[0])
            if body.supersedes_id:
                original = await conn.fetchrow(f"SELECT {_COLUMNS} FROM supplier_statement_evidence WHERE id=$1", body.supersedes_id)
                if (original is None or original["account_id"] != body.account_id
                        or original["period_start"] != parsed["period_start"] or original["period_end"] != parsed["period_end"]):
                    raise HTTPException(409, "A correction must identify the original account and exact period.")
                if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM supplier_statement_evidence WHERE supersedes_id=$1)", body.supersedes_id):
                    raise HTTPException(409, "The source already has a correction. Review its retained history.")
            row = await conn.fetchrow(f"""INSERT INTO supplier_statement_evidence
                (id,request_id,supplier,source_kind,account_id,reference,period_start,period_end,currency,
                 reported_charges,source_bytes,source_sha256,evidence_sha256,normalized,imported_by,supersedes_id)
                VALUES($1,$2,'deepgram','deepgram_billing_breakdown_v1',$3,$4,$5,$6,'USD',$7,$8,$9,$10,$11::jsonb,$12,$13)
                RETURNING {_COLUMNS}""", uuid4(), body.request_id, body.account_id, body.reference,
                parsed["period_start"], parsed["period_end"], parsed["reported_charges"], parsed["raw"],
                parsed["source_sha256"], parsed["evidence_sha256"], json.dumps(parsed["normalized"]),
                UUID(str(user.id)), body.supersedes_id)
            await _record(conn, audit, user, row, "supplier_evidence_imported", metadata={"request_id": str(body.request_id)})
            result = await _view(conn, row)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Import outcome is unconfirmed. Inspect this request before retrying.") from exc


@router.get("/usage/statements/{statement_id}")
async def get_statement(statement_id: UUID, request: Request,
        user: CurrentUser = Depends(require_platform_admin), db: Client = Depends(get_db_client)):
    async with asyncio.timeout(8), acquire_with_tenant(db.pool, None, user_id=user.id, timeout=3) as conn:
        await _session(conn, request, user)
        row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM supplier_statement_evidence WHERE id=$1", statement_id)
        if row is None:
            raise HTTPException(404, "Supplier evidence was not found.")
        return await _view(conn, row)


@router.post("/usage/statements/{statement_id}/reviews")
async def review_statement(statement_id: UUID, body: ReviewEvidence, request: Request,
        user: CurrentUser = Depends(require_platform_admin), db: Client = Depends(get_db_client),
        audit: AuditLogger = Depends(get_audit_logger)):
    if not body.source_account_and_period_checked or (body.stage == "reconciled" and not body.accept_unallocated_difference):
        raise HTTPException(422, "Confirm the source review and retain the unallocated difference explicitly.")
    try:
        async with asyncio.timeout(8), acquire_with_tenant(db.pool, None, user_id=user.id, timeout=3) as conn:
            await audit.lock_transaction(conn)
            await require_platform_admin_mutation(conn, user.id)
            await _session(conn, request, user)
            row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM supplier_statement_evidence WHERE id=$1", statement_id)
            if row is None:
                raise HTTPException(404, "Supplier evidence was not found.")
            if row["source_sha256"] != body.source_sha256:
                raise HTTPException(409, "The reviewed source does not match.")
            if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM supplier_statement_evidence WHERE supersedes_id=$1)", statement_id):
                raise HTTPException(409, "This source was superseded. Review the correction.")
            matches = await conn.fetch("""SELECT * FROM supplier_statement_reviews
                WHERE request_id=$1 OR (statement_id=$2 AND stage=$3)""", body.request_id, statement_id, body.stage)
            if matches:
                existing = matches[0]
                if (len(matches) != 1 or existing["request_id"] != body.request_id or existing["statement_id"] != statement_id
                        or existing["stage"] != body.stage or existing["source_sha256"] != body.source_sha256
                        or existing["review_reference"] != body.review_reference or existing["notes"] != body.notes
                        or str(existing["reviewer_id"]) != str(user.id)):
                    raise HTTPException(409, "A different review is already retained. Inspect the original receipt.")
                return await _view(conn, row)
            if body.stage == "reconciled" and not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM supplier_statement_reviews WHERE statement_id=$1 AND stage='reviewed')", statement_id):
                raise HTTPException(409, "Review the original supplier source before recording reconciliation.")
            comparison = reconciliation_comparison(dict(row))
            await conn.execute("""INSERT INTO supplier_statement_reviews
                (id,request_id,statement_id,stage,source_sha256,review_reference,notes,reviewer_id,comparison)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)""", uuid4(), body.request_id, statement_id, body.stage,
                body.source_sha256, body.review_reference, body.notes, UUID(str(user.id)), json.dumps(comparison))
            await _record(conn, audit, user, row, "supplier_evidence_"+body.stage,
                          metadata={"request_id": str(body.request_id), "unallocated": True})
            result = await _view(conn, row)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Review outcome is unconfirmed. Read the saved evidence before another decision.") from exc


@router.get("/usage/statements/{statement_id}/source")
async def download_source(statement_id: UUID, request: Request,
        user: CurrentUser = Depends(require_platform_admin), db: Client = Depends(get_db_client),
        audit: AuditLogger = Depends(get_audit_logger)):
    async with asyncio.timeout(8), acquire_with_tenant(db.pool, None, user_id=user.id, timeout=3) as conn:
        await audit.lock_transaction(conn)
        await require_platform_admin_mutation(conn, user.id)
        await _session(conn, request, user)
        row = await conn.fetchrow("SELECT id,source_sha256,source_bytes FROM supplier_statement_evidence WHERE id=$1", statement_id)
        if row is None:
            raise HTTPException(404, "Supplier evidence was not found.")
        await _record(conn, audit, user, row, "supplier_evidence_downloaded")
        raw = bytes(row["source_bytes"])
    return Response(raw, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="supplier-source-{statement_id}.json"',
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
