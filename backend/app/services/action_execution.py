"""Durable, fail-closed action receipts shared by dashboard and live voice.

Authorization and confirmed parameters are the caller's responsibility. A
committed running claim is never automatically reclaimed: after an ambiguous
remote write, reconciliation is required before another send can be safe.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from uuid import UUID

from app.core.db_utils import acquire_with_tenant


def _json(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _uuid(value):
    return str(UUID(str(value))) if value else None


def unknown_result(action, action_id=None, *, status="unknown"):
    return {
        "version": 1,
        "action": action,
        "action_id": action_id,
        "success": False,
        "status": status,
        "confirmation_allowed": False,
        "message": "The action outcome is not confirmed. Do not repeat it; review the saved receipt.",
    }


_RECEIPT_FIELDS = (
    "provider",
    "connector_id",
    "external_account_id",
    "message_id",
    "external_event_id",
    "meeting_id",
    "job_id",
    "reminder_id",
    "plan_id",
    "child_action_id",
)


def public_action_receipt(row):
    """Project recorded evidence, never raw parameters or arbitrary provider payloads."""
    try:
        output = _json(row.get("output_data"))
    except (TypeError, ValueError):
        output = {}
    receipt = {}
    # An unconfirmed outer save can retain a provider reference for inspection.
    # It never turns that reference into a completed outcome.
    provider_result = output.get("provider_result")
    for source in (provider_result if isinstance(provider_result, dict) else {}, output):
        for key in _RECEIPT_FIELDS:
            value = source.get(key)
            if isinstance(value, (str, UUID)) and str(value).strip() and len(str(value)) <= 512:
                receipt[key] = str(value)
    status = str(row.get("status") or "unknown")
    success = status in {"completed", "scheduled"} and output.get("success") is True
    confirmed = success and output.get("confirmation_allowed") is True
    if output.get("status") in {
        "accepted",
        "provider_accepted",
        "created",
        "updated",
        "cancelled",
        "scheduled",
        "queued",
        "completed",
    }:
        receipt["provider_status"] = output["status"]
    return {
        **{
            key: row.get(key)
            for key in (
                "type",
                "triggered_by",
                "lead_id",
                "created_at",
                "started_at",
                "completed_at",
            )
        },
        "id": str(row["id"]),
        "action_id": str(row["id"]),
        "status": status,
        "success": success,
        "confirmation_allowed": confirmed,
        "receipt": receipt,
        "error": (
            None
            if confirmed
            else (
                "Completion was recorded, but its outcome is unverified. Review the receipt before repeating the action."
                if status in {"completed", "scheduled"}
                else (
                    "The action outcome is not confirmed. Review this receipt; do not resend automatically."
                    if status in {"running", "unknown", "pending"}
                    else "The action did not complete."
                )
            )
        ),
    }


async def find_owned_action_receipt(
    db_client, *, tenant_id, user_id, proposal_id=None, action_id=None
):
    """Read only through the tenant pool: recovery never executes an effect."""
    if not tenant_id or not user_id:
        return None
    if action_id:
        predicate, reference = "id=$3::uuid", str(UUID(str(action_id)))
    elif isinstance(proposal_id, str) and 0 < len(proposal_id) <= 180:
        predicate, reference = "idempotency_key=$3", f"assistant:{user_id}:{proposal_id}"
    else:
        return None
    async with acquire_with_tenant(db_client.pool, str(tenant_id)) as conn:
        row = await conn.fetchrow(
            "SELECT id,type,status,triggered_by,lead_id,output_data,created_at,started_at,completed_at "
            "FROM assistant_actions WHERE tenant_id=$1::uuid AND user_id=$2::uuid AND " + predicate,
            str(tenant_id),
            str(user_id),
            reference,
        )
    return public_action_receipt(dict(row)) if row else None


class DurableActionExecutor:
    def __init__(self, pool):
        self.pool = pool

    async def execute(
        self,
        *,
        tenant_id,
        idempotency_key,
        action,
        payload,
        executor,
        call_id=None,
        lead_id=None,
        campaign_id=None,
        user_id=None,
        triggered_by="voice",
        conversation_id=None,
    ):
        if not tenant_id or not idempotency_key or len(idempotency_key) > 255:
            raise ValueError("A tenant and bounded idempotency key are required")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        recorded_input = json.dumps({"request_hash": digest, "parameters": payload}, default=str)
        async with acquire_with_tenant(self.pool, tenant_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO assistant_actions
                    (tenant_id, type, status, idempotency_key, input_data,
                     call_id, lead_id, campaign_id, user_id, triggered_by, conversation_id)
                VALUES ($1::uuid,$2,'pending',$3,$4::jsonb,$5::uuid,$6::uuid,$7::uuid,$8::uuid,$9,$10::uuid)
                ON CONFLICT (tenant_id,idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                RETURNING *
            """,
                str(tenant_id),
                action,
                idempotency_key,
                recorded_input,
                _uuid(call_id),
                _uuid(lead_id),
                _uuid(campaign_id),
                _uuid(user_id),
                triggered_by,
                _uuid(conversation_id),
            )
            if row is None:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM assistant_actions WHERE tenant_id=$1::uuid AND idempotency_key=$2
                """,
                    str(tenant_id),
                    idempotency_key,
                )
            if row is None:
                raise RuntimeError("Action receipt was not persisted")
            row = dict(row)
            action_id = str(row["id"])
            if row["type"] != action or _json(row["input_data"]).get("request_hash") != digest:
                return {
                    **unknown_result(action, action_id, status="request_conflict"),
                    "message": "This request ID already belongs to different confirmed parameters.",
                }
            if row["status"] != "pending":
                saved = _json(row.get("output_data"))
                return (
                    {**saved, "action_id": action_id, "replayed": True}
                    if saved
                    else unknown_result(action, action_id)
                )
            claimed = await conn.fetchval(
                """
                UPDATE assistant_actions SET status='running', started_at=NOW()
                WHERE tenant_id=$1::uuid AND id=$2::uuid AND status='pending' RETURNING id
            """,
                str(tenant_id),
                action_id,
            )
            if not claimed:
                return unknown_result(action, action_id)

        # The claim is committed BEFORE entering any external executor.
        result = None
        try:
            result = dict(await executor())
            if result.get("action_id") and result["action_id"] != action_id:
                result["child_action_id"] = str(result["action_id"])
            result.update(action=action, action_id=action_id)
            result.setdefault("confirmation_allowed", False)
            if result.get("success") is True:
                status = "scheduled" if result.get("status") == "scheduled" else "completed"
            else:
                status = (
                    "unknown" if result.get("status") in {"unknown", "in_progress"} else "failed"
                )
            await self._save(tenant_id, action_id, status, result)
            return result
        except asyncio.CancelledError:
            # A timeout may cancel us after the provider accepted the request.
            # Preserve a receipt when possible, never release the claim to retry.
            try:
                await asyncio.shield(
                    self._save(tenant_id, action_id, "unknown", unknown_result(action, action_id))
                )
            except Exception:
                pass  # The committed running claim is itself a no-resend fence.
            raise
        except Exception:
            unconfirmed = unknown_result(action, action_id)
            if result:
                # A remote receipt remains useful for reconciliation even when
                # the first local persistence attempt failed. Do not turn it
                # into caller-visible completion until durable saving succeeds.
                unconfirmed["provider_result"] = result
            try:
                await self._save(tenant_id, action_id, "unknown", unconfirmed)
            except Exception:
                pass
            return unconfirmed

    async def _save(self, tenant_id, action_id, status, result):
        async with acquire_with_tenant(self.pool, tenant_id) as conn:
            updated = await conn.execute(
                """
                UPDATE assistant_actions SET status=$3, output_data=$4::jsonb, completed_at=NOW(),
                    scheduled_at=COALESCE($5::timestamptz,scheduled_at)
                WHERE tenant_id=$1::uuid AND id=$2::uuid AND status='running'
            """,
                str(tenant_id),
                action_id,
                status,
                json.dumps(result, default=str),
                (
                    datetime.fromisoformat(result["scheduled_at"])
                    if status == "scheduled" and result.get("scheduled_at")
                    else None
                ),
            )
            if updated != "UPDATE 1":
                raise RuntimeError("Action receipt update was not confirmed")
