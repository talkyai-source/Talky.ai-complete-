"""CONTACT_PERMISSION_DIAL_GATE: the switch's three positions and its logging.

``off`` (default, anything unrecognised): nothing is looked up, nothing blocked.
``warn``: the real lookup runs and what it WOULD block is logged; nothing is
blocked. ``on`` (1/true/yes/on): contacts without a recorded permission are not
queued and not dialled. The switch is read on every call.

``contact_permission_dial_gate_enabled()`` (block_reasons) stays True only for
``on``, so ``warn`` reads as ``off`` for every caller that predates it.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Tuple

logger = logging.getLogger(__name__)

_ON_WORDS = ("1", "true", "yes", "on")

# The calling-permission state of ONE contact. No row (lead not in this tenant)
# counts as missing. Both tables are matched on the caller's tenant; the list is
# read through a tenant-scoped scalar subquery (no outer join), so the tenant
# predicate sits on every table the statement reads.
_LEAD_PERMISSION_SQL = (
    "SELECT (l.list_id IS NULL OR (SELECT cl.consent_confirmed_at "
    "FROM contact_lists AS cl "
    "WHERE cl.tenant_id = $1::uuid AND cl.id = l.list_id) IS NULL) AS missing "
    "FROM leads AS l "
    "WHERE l.tenant_id = $1::uuid AND l.id = $2::uuid"
)

# How many contacts of a campaign still wait to be called but have no recorded
# permission, and how many of those have no list at all. A contact of a list
# that is switched off is not waiting.
_WAITING_SQL = (
    "SELECT COUNT(*) AS waiting, "
    "COUNT(*) FILTER (WHERE l.list_id IS NULL) AS ungrouped "
    "FROM leads AS l "
    "WHERE l.tenant_id = $1::uuid AND l.campaign_id = $2::uuid "
    "AND l.status IN ('pending','calling') AND l.do_not_call IS NOT TRUE "
    "AND (l.list_id IS NULL OR EXISTS (SELECT 1 FROM contact_lists AS cl "
    "WHERE cl.tenant_id = $1::uuid AND cl.id = l.list_id "
    "AND cl.consent_confirmed_at IS NULL AND cl.is_active IS NOT FALSE))"
)


def contact_permission_dial_gate_mode() -> str:
    """``on`` / ``warn`` / ``off``, read from the environment at call time."""
    value = os.getenv("CONTACT_PERMISSION_DIAL_GATE", "").strip().lower()
    if value in _ON_WORDS:
        return "on"
    if value == "warn":
        return "warn"
    return "off"


def _short(value: Any) -> str:
    return str(value)[:8]


def dial_time_decision(job: Any, *, has_list: bool, recorded: bool) -> str:
    """``off`` | ``allow`` | ``would_block`` | ``block`` for one dequeued job.

    Logs one INFO line per decision in every mode, so a gate that is wired to a
    constant input shows up in the log. Only ``block`` stops the call.
    """
    mode = contact_permission_dial_gate_mode()
    if mode == "off":
        decision = "off"
    elif recorded:
        decision = "allow"
    else:
        decision = "block" if mode == "on" else "would_block"
    logger.info(
        "contact_permission_gate job=%s tenant=%s campaign=%s mode=%s "
        "has_list=%s recorded=%s decision=%s",
        getattr(job, "job_id", None), _short(getattr(job, "tenant_id", "")),
        _short(getattr(job, "campaign_id", "")), mode, has_list, recorded, decision,
    )
    return decision


def log_start_decision(
    campaign_id: Any,
    tenant_id: Any,
    mode: str,
    candidates: int,
    without_permission: int,
    lookup_ok: bool,
) -> None:
    """One INFO line per start in ``warn`` / ``on``."""
    logger.info(
        "contact_permission_gate_start campaign=%s tenant=%s mode=%s "
        "candidates=%d without_permission=%d lookup=%s",
        _short(campaign_id), _short(tenant_id), mode, candidates,
        without_permission, "ok" if lookup_ok else "failed",
    )


async def permission_missing_for_lead(conn: Any, tenant_id: str, lead_id: str) -> bool:
    """True when the contact has no list or its list has no recorded permission."""
    row = await conn.fetchrow(_LEAD_PERMISSION_SQL, str(tenant_id), str(lead_id))
    if row is None:
        return True
    return bool(row["missing"])


async def count_waiting_without_permission(
    conn: Any, tenant_id: str, campaign_id: str,
) -> Tuple[int, int]:
    """(waiting without permission, of which have no list) for one campaign."""
    row = await conn.fetchrow(_WAITING_SQL, str(tenant_id), str(campaign_id))
    if row is None:
        return 0, 0
    return int(row["waiting"] or 0), int(row["ungrouped"] or 0)
