"""One explicit redial through the existing durable job/worker path.

No campaign restart, bulk lead reset or direct carrier call occurs here.
The original call fixes the idempotency identity; the current campaign route
and normal worker guards still decide whether the new attempt may originate.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid5

import asyncpg

from app.core.db_utils import acquire_with_tenant
from app.domain.models.dialer_job import DialerJob
from app.domain.services.call_status import TERMINAL_CALL_STATUSES
from app.domain.services.dialer.job_states import ACTIVE_STATUSES
from app.domain.services.dnc_service import DNCService, normalize_e164
from app.domain.services.telephony.outbound_readiness import evaluate_outbound_readiness

logger = logging.getLogger(__name__)


class RedialError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def redial_job_id(call_id: str) -> str:
    return str(uuid5(UUID(str(call_id)), "manual-redial-v1"))


def _object(value) -> dict:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


async def _context(conn, tenant_id: str, call_id: str, *, lock: bool = False) -> dict:
    row = await conn.fetchrow(
        """SELECT c.id, c.status, c.direction, c.outcome, c.phone_number AS original_phone,
                  c.campaign_id, c.lead_id, l.phone_number, l.status AS lead_status,
                  l.do_not_call, l.first_name, l.last_name, l.custom_fields,
                  p.status AS campaign_status, p.direction AS campaign_direction,
                  p.calling_config, t.calling_rules
           FROM calls c
           JOIN leads l ON l.id=c.lead_id AND l.tenant_id=c.tenant_id
                       AND l.campaign_id=c.campaign_id
           JOIN campaigns p ON p.id=c.campaign_id AND p.tenant_id=c.tenant_id
           JOIN tenants t ON t.id=c.tenant_id
           WHERE c.id=$1::uuid AND c.tenant_id=$2::uuid"""
        + (" FOR UPDATE OF c,l" if lock else ""),
        call_id, tenant_id,
    )
    if row is None:
        raise RedialError("call_not_available", "The original call or contact is no longer available.", 404)
    return dict(row)


def _local_block(row: dict):
    if row.get("direction") != "outbound" or row.get("campaign_direction") != "outbound":
        return "outbound_only", "Dial again is available for outbound campaign calls."
    if row.get("status") not in TERMINAL_CALL_STATUSES:
        return "call_not_finished", "Wait until the current call has finished."
    if row.get("do_not_call") or row.get("lead_status") in {"do_not_call", "dnc", "deleted"}:
        return "do_not_call", "This contact cannot be called."
    if str(row.get("outcome") or "").lower() in {"spam", "invalid", "do_not_call", "opt_out", "dnc"}:
        return "outcome_blocked", "This call outcome does not allow redial."
    if row.get("campaign_status") not in {"running", "active"}:
        return "campaign_not_running", "Resume the original campaign before dialing again."
    phone = normalize_e164(row.get("phone_number") or "")
    if not phone or phone != normalize_e164(row.get("original_phone") or ""):
        return "contact_changed", "The contact number has changed. Review it in the campaign first."
    return None


async def _job_state(conn, tenant_id: str, row: dict, job_id: str):
    own = await conn.fetchrow(
        "SELECT id,status,scheduled_at,phone_number FROM dialer_jobs WHERE id=$1::uuid AND tenant_id=$2::uuid",
        job_id, tenant_id,
    )
    busy = await conn.fetchval(
        """SELECT EXISTS(SELECT 1 FROM dialer_jobs
                WHERE tenant_id=$1::uuid AND lead_id=$2::uuid AND id<>$3::uuid
                  AND status=ANY($4::text[]))
             OR EXISTS(SELECT 1 FROM calls
                WHERE tenant_id=$1::uuid AND lead_id=$2::uuid
                  AND (status IS NULL OR NOT(status=ANY($5::text[]))))""",
        tenant_id, str(row["lead_id"]), job_id, list(ACTIVE_STATUSES), list(TERMINAL_CALL_STATUSES),
    )
    return dict(own) if own else None, bool(busy)


def _preview(row: dict, job_id: str, block=None, *, caller_id=None, trunk_id=None) -> dict:
    return {
        "eligible": block is None, "reason_code": block[0] if block else None,
        "reason": block[1] if block else None, "caller_id": caller_id, "trunk_id": trunk_id,
        "campaign_id": str(row["campaign_id"]), "lead_id": str(row["lead_id"]),
        "phone_number": row["phone_number"], "job_id": job_id,
    }


async def preview_redial(pool, *, tenant_id: str, call_id: str) -> dict:
    job_id = redial_job_id(call_id)
    async with acquire_with_tenant(pool, tenant_id) as conn:
        row = await _context(conn, tenant_id, call_id)
        own, busy = await _job_state(conn, tenant_id, row, job_id)
    block = _local_block(row)
    if block:
        return _preview(row, job_id, block)
    if own and own["status"] != "pending":
        return _preview(row, job_id, ("already_requested", "This call has already been redialled. Open the new call to try again."))
    if busy:
        return _preview(row, job_id, ("contact_busy", "A call or queued attempt already exists for this contact."))
    # Uses the shared global + tenant DNC lookup. Failures propagate; never
    # show a ready button when suppression or route evidence is unavailable.
    if await DNCService(pool).is_on_dnc(tenant_id=tenant_id, e164=row["phone_number"]):
        return _preview(row, job_id, ("do_not_call", "This number is on the do-not-call list."))
    readiness = await evaluate_outbound_readiness(
        pool, tenant_id=tenant_id, campaign_id=str(row["campaign_id"]),
        campaign={"id": str(row["campaign_id"]), "calling_config": row.get("calling_config")},
        calling_rules=row.get("calling_rules") or {},
    )
    block = None if readiness["ready"] else (readiness["reason_code"], readiness["reason"])
    return _preview(row, job_id, block, caller_id=readiness["caller_id"], trunk_id=readiness["trunk_id"])


def _receipt(row: dict, job_id: str, status: str, message: str, *, phone_number=None) -> dict:
    return {"status": status, "job_id": job_id, "campaign_id": str(row["campaign_id"]),
            "lead_id": str(row["lead_id"]), "phone_number": phone_number or row["phone_number"], "message": message}


async def request_redial(pool, queue, *, tenant_id: str, call_id: str, user_id: str | None = None) -> dict:
    job_id = redial_job_id(call_id)
    # Repeated POSTs report the existing durable handoff; they never create a
    # fresh job even after a lost HTTP response or a terminal redial outcome.
    async with acquire_with_tenant(pool, tenant_id) as conn:
        row = await _context(conn, tenant_id, call_id)
        own, _ = await _job_state(conn, tenant_id, row, job_id)
    if own and own["status"] != "pending":
        return _receipt(row, job_id, own["status"], "This redial request already exists.", phone_number=own["phone_number"])
    preview = await preview_redial(pool, tenant_id=tenant_id, call_id=call_id)
    if not preview["eligible"]:
        raise RedialError(preview["reason_code"], preview["reason"])
    try:
        async with acquire_with_tenant(pool, tenant_id, user_id=user_id) as conn:
            row = await _context(conn, tenant_id, call_id, lock=True)
            own, busy = await _job_state(conn, tenant_id, row, job_id)
            if own and own["status"] != "pending":
                return _receipt(row, job_id, own["status"], "This redial request already exists.", phone_number=own["phone_number"])
            block = _local_block(row)
            if block:
                raise RedialError(*block)
            if busy:
                raise RedialError("contact_busy", "A call or queued attempt already exists for this contact.")
            scheduled_at = own["scheduled_at"] if own else datetime.now(timezone.utc)
            await conn.execute(
                """INSERT INTO dialer_jobs (id,tenant_id,campaign_id,lead_id,phone_number,status,scheduled_at)
                   VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,'pending',$6)
                   ON CONFLICT (id) DO NOTHING""",
                job_id, tenant_id, str(row["campaign_id"]), str(row["lead_id"]), row["phone_number"], scheduled_at,
            )
    except asyncpg.UniqueViolationError as exc:
        raise RedialError("contact_busy", "A call or queued attempt already exists for this contact.") from exc
    job = DialerJob(job_id=job_id, tenant_id=tenant_id, campaign_id=str(row["campaign_id"]),
        lead_id=str(row["lead_id"]), phone_number=row["phone_number"], scheduled_at=scheduled_at,
        lead_first_name=row.get("first_name"), lead_last_name=row.get("last_name"),
        lead_company=_object(row.get("custom_fields")).get("company"))
    key = "manual-redial-" + job_id
    try:
        accepted = await queue.schedule_job_once(job, idempotency_key=key)
    except Exception:
        logger.warning("redial_handoff_uncertain job=%s", job_id)
        accepted = False
    if not accepted:
        return _receipt(row, job_id, "pending", "Redial saved; queue confirmation is pending. Retry this request to check it.")
    # DB commit precedes Redis, and its existing atomic schedule operation
    # fences an ambiguous handoff. The worker still checks suppression,
    # campaign state, calling hours, quota and the selected route before dialing.
    async with acquire_with_tenant(pool, tenant_id) as conn:
        updated = await conn.fetchval(
            """UPDATE dialer_jobs SET status='queued',updated_at=NOW()
               WHERE id=$1::uuid AND tenant_id=$2::uuid AND status='pending' RETURNING status""",
            job_id, tenant_id,
        )
        status = updated or await conn.fetchval(
            "SELECT status FROM dialer_jobs WHERE id=$1::uuid AND tenant_id=$2::uuid", job_id, tenant_id
        )
    try:
        await queue.confirm_retry_once(key)
    except Exception:
        logger.warning("redial_handoff_marker_cleanup_pending job=%s", job_id)
    return _receipt(row, job_id, status or "pending", "Redial queued. Normal campaign calling rules still apply.")
