"""Durable all-leg PBX evidence shared by admin and background termination."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.core.db_utils import acquire_with_tenant
from app.domain.services.telephony.termination import TerminationContext

logger = logging.getLogger(__name__)

_TERMINATION_RECEIPT_EVENT = "admin_termination_confirmed"
_TERMINATION_RECEIPT_SOURCE = "admin_termination"
_RECEIPT_WRITE_TIMEOUT_SECONDS = 5.0
_POTENTIALLY_LIVE_LEGS = {"initiated", "ringing", "answered", "in_progress", "in_call", "active"}


@dataclass(frozen=True, slots=True)
class TerminationReceiptResult:
    saved: bool
    current_identity_verified: bool
    code: str


def _receipt_time(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def receipt_matches(call: Mapping[str, Any], legs: list[dict], receipt: dict) -> bool:
    """Validate saved all-leg proof, never a terminal label or parent timestamp.

    Version 2 binds proof to the original control backend. Version 3 also
    requires the exact pre-proof row inventory, irrespective of timestamps.
    Older family-only receipts cannot prove absence on the original PBX.
    """
    from app.domain.services.call_status import TERMINAL_CALL_STATUSES

    from app.domain.services.telephony.control_backend_identity import saved_control_backend
    binding = saved_control_backend(call.get("route_snapshot"))
    if (type(receipt.get("version")) is not int or receipt["version"] not in {2, 3}
            or receipt.get("provider_hangup_confirmed") is not True
            or not binding or receipt.get("control_backend") != binding):
        return False
    if str(call.get("status")) not in {*TERMINAL_CALL_STATUSES, "termination_pending"}:
        return False
    for key, expected in {
        "call_id": str(call.get("id") or ""),
        "tenant_id": str(call.get("tenant_id") or ""),
        "provider": str(call.get("provider") or ""),
        "provider_call_id": str(call.get("provider_call_id") or call.get("external_call_uuid") or ""),
    }.items():
        if not expected or receipt.get(key) != expected:
            return False
    proven_at = _receipt_time(receipt.get("confirmed_at"))
    if proven_at is None or proven_at > datetime.now(timezone.utc):
        return False
    covered = receipt.get("legs")
    if not isinstance(covered, list) or any(not isinstance(item, dict) for item in covered):
        return False
    if receipt.get("version") == 3:
        inventory = receipt.get("leg_inventory")
        if not isinstance(inventory, list) or any(not isinstance(item, dict) for item in inventory):
            return False
        identities = [tuple(item.get(key) for key in ("id", "provider", "provider_leg_id")) for item in inventory]
        if any(not item[0] or any(not isinstance(value, str) for value in item) for item in identities):
            return False
        current = {tuple(str(leg.get(key) or "") for key in ("id", "provider", "provider_leg_id")) for leg in legs}
        if len({item[0] for item in identities}) != len(identities) or set(identities) != current:
            return False
    current_by_id = {str(leg.get("id")): leg for leg in legs}
    for saved in covered:
        current = current_by_id.get(str(saved.get("id")))
        if not current or any(str(current.get(key) or "") != saved.get(key) for key in ("provider", "provider_leg_id")):
            return False
    covered_ids = {str(item.get("id")) for item in covered}
    for leg in legs:
        provider_id = str(leg.get("provider_leg_id") or "").strip()
        if not provider_id or provider_id.startswith("transfer-") or str(leg.get("id")) in covered_ids:
            continue
        created_at = _receipt_time(leg.get("created_at"))
        if leg.get("status") in _POTENTIALLY_LIVE_LEGS or created_at is None or created_at > proven_at:
            return False
    return True


async def persist_termination_receipt(pool: Any, context: TerminationContext, *, requested: bool) -> TerminationReceiptResult:
    """Save confirmed external effect before finalization/HTTP response can fail.

    Uses the existing append-only call event table, not best-effort audit.
    Storage failure never turns a proven external hangup into a failed effect.
    A changed/unavailable current inventory is different: old proof cannot
    authorize finalization of the current call, so its recovery fence remains.
    """
    proven_at = datetime.now(timezone.utc)
    identity_verified = False
    try:
        if context.leg_inventory is None:
            return TerminationReceiptResult(False, False, "inventory_unavailable")
        async with asyncio.timeout(_RECEIPT_WRITE_TIMEOUT_SECONDS), acquire_with_tenant(
            pool, None, timeout=_RECEIPT_WRITE_TIMEOUT_SECONDS,
        ) as conn:
            call = await conn.fetchrow(
                "SELECT id, tenant_id, provider, provider_call_id, external_call_uuid, status, route_snapshot FROM calls WHERE id=$1::uuid FOR UPDATE",
                context.call_id,
            )
            legs = [dict(row) for row in await conn.fetch(
                "SELECT id, provider, provider_leg_id, status, created_at FROM call_legs WHERE call_id=$1::uuid ORDER BY created_at, id",
                context.call_id,
            )]
            pairs = {(str(leg_id), str(provider)) for leg_id, provider in context.provider_legs}
            covered = [{key: str(leg.get(key) or "") for key in ("id", "provider", "provider_leg_id")}
                       for leg in legs if (str(leg.get("provider_leg_id")), str(leg.get("provider"))) in pairs]
            if {(item["provider_leg_id"], item["provider"]) for item in covered} != pairs:
                return TerminationReceiptResult(False, False, "identity_changed")
            receipt = {
                "version": 3,
                "call_id": context.call_id, "tenant_id": context.tenant_id,
                "control_backend": context.control_backend,
                "provider": context.provider, "provider_call_id": context.provider_call_id,
                "provider_hangup_confirmed": True, "provider_hangup_requested": requested,
                "confirmed_at": proven_at.isoformat(), "legs": covered,
                "scope": "saved_control_backend_and_channel_identities",
            }
            receipt["leg_inventory"] = [
                dict(zip(("id", "provider", "provider_leg_id"), item))
                for item in context.leg_inventory
            ]
            receipt["inventory_coverage"] = "complete_preproof"
            if not call or not receipt_matches(dict(call), legs, receipt):
                return TerminationReceiptResult(False, False, "identity_changed")
            identity_verified = True
            await conn.execute(
                "INSERT INTO call_events (call_id, event_type, source, event_data) VALUES ($1::uuid,$2,$3,$4::jsonb)",
                context.call_id, _TERMINATION_RECEIPT_EVENT, _TERMINATION_RECEIPT_SOURCE, json.dumps(receipt),
            )
        return TerminationReceiptResult(True, True, "saved")
    except Exception:
        logger.exception("confirmed hangup receipt persistence failed call=%s", context.call_id)
        return TerminationReceiptResult(False, identity_verified,
            "storage_unavailable" if identity_verified else "identity_unavailable")


