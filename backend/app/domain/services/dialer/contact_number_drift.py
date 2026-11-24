"""A corrected contact number is never dialled under its old value.

Editing a contact's number (website edit, ``manage_lead``) changes
``leads.phone_number`` and cancels the contact's not-yet-dialled jobs, but a job
already in Redis, or a retry booked while a call was live, still carries the old
number. Two guards use the helpers below:

* the dialler worker drops a job whose number no longer matches the contact
  (``contact_number_changed``);
* call settlement books the retry under the contact's saved number and re-points
  the job row to it (``carry_contact_number_into_retry``).

"Cannot tell" is never a reason to drop: an empty side, a job number that is not
strict E.164, or a number that does not parse gives ``None`` and the job is dialled
as before (the existing invalid-number skip and the call guard decide those).

Logs carry 8-character ids only: never a phone number, never exception text.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.domain.services.phone_number_normalizer import (
    is_strict_e164,
    normalize_suppression_key,
)

logger = logging.getLogger(__name__)

REASON_LEAD_NUMBER_CHANGED = "lead_number_changed"


def number_changed(job_number, contact_number) -> Optional[bool]:
    """True: different numbers. False: same number. None: cannot tell."""
    job = str(job_number or "")
    contact = str(contact_number or "")
    if job and job == contact:
        return False
    if not job or not contact or not is_strict_e164(job):
        return None
    job_key = normalize_suppression_key(job)
    contact_key = normalize_suppression_key(contact)
    if not job_key or not contact_key:
        return None
    return job_key != contact_key


async def read_contact_number(conn, *, tenant_id, lead_id) -> Optional[str]:
    row = await conn.fetchrow(
        "SELECT phone_number FROM leads WHERE id = $1::uuid AND tenant_id = $2::uuid",
        str(lead_id),
        str(tenant_id),
    )
    return None if row is None else row["phone_number"]


async def contact_number_changed(acquire, job) -> bool:
    """True only when the contact's saved number provably differs from the job's."""
    ids = (str(job.job_id)[:8], str(job.lead_id)[:8])
    try:
        async with acquire() as conn:
            current = await read_contact_number(
                conn, tenant_id=job.tenant_id, lead_id=job.lead_id
            )
        verdict = number_changed(job.phone_number, current)
    except Exception as exc:  # noqa: BLE001 - never stops a dial on its own failure
        logger.warning(
            "dialer_contact_number_unread job=%s lead=%s error_type=%s",
            *ids, type(exc).__name__,
        )
        return False
    if verdict is True:
        logger.warning("dialer_contact_number_changed job=%s lead=%s action=drop", *ids)
    elif verdict is None:
        logger.info(
            "dialer_contact_number_unknown job=%s lead=%s action=dial_as_before", *ids
        )
    else:
        logger.debug("dialer_contact_number_same job=%s lead=%s", *ids)
    return verdict is True


async def carry_contact_number_into_retry(
    conn, retry_job_data, *, tenant_id, job_id, lead_id
) -> None:
    """Book the retry under the contact's saved number when it differs.

    Runs inside the settlement transaction, in a savepoint: a failure here never
    aborts the settlement and leaves the job's own number in place (the dial gate
    then decides).
    """
    ids = (str(job_id)[:8], str(lead_id)[:8])
    old = retry_job_data.get("phone_number")
    moved = None
    try:
        async with conn.transaction():  # savepoint: never aborts the settlement
            current = await read_contact_number(
                conn, tenant_id=tenant_id, lead_id=lead_id
            )
            verdict = number_changed(old, current)
            if verdict is True:
                moved = await conn.fetchval(
                    "UPDATE dialer_jobs SET phone_number = $3, updated_at = NOW() "
                    "WHERE id = $1::uuid AND tenant_id = $2::uuid "
                    "AND status = 'retry_scheduled' "
                    "AND phone_number = $4 RETURNING id",
                    str(job_id),
                    str(tenant_id),
                    str(current),
                    str(old),
                )
    except Exception as exc:  # noqa: BLE001 - keep the copy; the dial gate decides
        logger.warning(
            "dialer_retry_number_check_failed job=%s lead=%s error_type=%s",
            *ids, type(exc).__name__,
        )
        return
    if verdict is None:
        logger.info("dialer_retry_number_unknown job=%s lead=%s action=keep_copy", *ids)
    elif verdict and moved is None:
        logger.warning(
            "dialer_retry_number_not_moved job=%s lead=%s action=keep_copy", *ids
        )
    elif verdict:
        retry_job_data["phone_number"] = str(current)
        logger.info("dialer_retry_follows_contact_number job=%s lead=%s", *ids)
