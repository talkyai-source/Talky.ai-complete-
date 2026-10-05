"""
CRM Sync Service — log finished calls into every connected CRM.

Rewritten 2026-09-07 for the Salesforce connector. The previous version was
dead code: nothing called it, it read a ``connectors.encrypted_tokens``
column that does not exist, and it called ``find_contact_by_email`` which no
CRM provider implements. This version:

* resolves connectors through ``connector_resolver`` — the ONE place that
  decrypts, refreshes and writes back OAuth tokens — one connector per
  active CRM provider (a tenant may hold HubSpot AND Salesforce);
* speaks only the ``CRMProvider`` contract (``search_contact`` /
  ``create_contact`` / ``log_call`` / ``update_call_log``);
* is driven by two hooks and is idempotent across them:
    1. ``CallService.handle_call_status`` — the terminal settlement of every
       outbound call (answered or not).  Logs the call immediately with its
       outcome, duration and a transcript excerpt.
    2. ``call_transcript_persister`` — after the AI summary is stored
       (answered calls, inbound included).  Creates the log if hook 1 did
       not run for this call (inbound), otherwise amends the existing log
       with the summary headline / next step.
* keeps CRM record ids per provider in ``leads.custom_fields.crm_ids``
  (``leads.crm_contact_id`` stays the newest CRM's id for legacy readers)
  and records ``calls.crm_call_id`` / ``calls.crm_synced_at``;
* never raises into a call teardown; every failure is a WARNING with the
  call id and provider.

Tenant scoping: every SQL statement runs through ``acquire_with_tenant`` AND
carries an explicit ``tenant_id`` predicate (the app role once had
BYPASSRLS; never rely on RLS alone).
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.services.crm_delivery_store import CRMDeliveryStore, MAX_ATTEMPTS
from app.domain.services.call_summary.business_details import transcript_revision
from app.domain.services.lead_capture_service import LeadCaptureService
from app.core.db_utils import acquire_with_tenant
from app.domain.services.call_status import CallOutcome
from app.infrastructure.connectors.base import ConnectorProviderError
from app.services.connector_resolver import (
    ConnectorLookupError,
    ConnectorNotConnectedError,
    list_active_connector_providers,
    resolve_active_connector,
    connector_identity,
    verify_reviewed_connector,
    ReviewedConnectorChanged,
)

logger = logging.getLogger(__name__)

_TRANSCRIPT_EXCERPT_CHARS = 1500
# Hook 1 fires before the AI summary exists; hook 2 amends. Keep the process
# from running two syncs for one call at the same instant.
_call_locks: Dict[str, asyncio.Lock] = {}
_inflight_tasks: set = set()


class CRMSyncResult(BaseModel):
    """Outcome of one call's sync across all connected CRM providers."""
    success: bool
    call_id: str
    providers: List[str] = []
    crm_contact_id: Optional[str] = None
    crm_call_id: Optional[str] = None
    skipped: bool = False
    skipped_reason: Optional[str] = None
    error_message: Optional[str] = None
    warning_message: Optional[str] = None
    updated_existing: bool = False


class CRMNotConnectedWarning:
    """Operator-facing copy for missing/expired CRM connections."""

    MISSING_CRM = (
        "CRM not connected. Call data was saved in Talky.ai but not synced to a CRM. "
        "Connect HubSpot or Salesforce on the Connectors page to enable automatic "
        "lead creation and call logging."
    )
    TOKEN_EXPIRED = (
        "CRM connection expired. Reconnect it on the Connectors page to resume "
        "automatic call logging."
    )


class CRMDestinationMismatch(RuntimeError):
    """A saved remote ID cannot safely be used with the current connection."""


# ---------------------------------------------------------------------------
# Outcome / body shaping
# ---------------------------------------------------------------------------

# Keyed by the CallOutcome enum (plus the legacy calls.status spellings the
# dialer still writes) so the outcome vocabulary stays defined in exactly one
# place — call_status.py — as the billing/gate guard test requires.
_OUTCOME_LABELS = {
    CallOutcome.ANSWERED.value: "Answered",
    "completed": "Completed",
    CallOutcome.CUSTOMER_HUNG_UP.value: "Completed",
    CallOutcome.AGENT_HUNG_UP.value: "Completed",
    "goal_achieved": "Goal achieved",
    CallOutcome.NO_ANSWER.value: "No answer",
    CallOutcome.BUSY.value: "Busy",
    CallOutcome.VOICEMAIL.value: "Voicemail",
    CallOutcome.REJECTED.value: "Rejected",
    CallOutcome.UNREACHABLE.value: "Unreachable",
    CallOutcome.NETWORK_FAILURE.value: "Network failure",
    CallOutcome.CANCELLED.value: "Cancelled",
    "canceled": "Cancelled",
    CallOutcome.FAILED.value: "Failed",
}

_HUBSPOT_STATUS = {
    CallOutcome.ANSWERED.value: "COMPLETED",
    "completed": "COMPLETED",
    CallOutcome.CUSTOMER_HUNG_UP.value: "COMPLETED",
    CallOutcome.AGENT_HUNG_UP.value: "COMPLETED",
    "goal_achieved": "COMPLETED",
    CallOutcome.NO_ANSWER.value: "NO_ANSWER",
    CallOutcome.BUSY.value: "BUSY",
    CallOutcome.VOICEMAIL.value: "COMPLETED",
    CallOutcome.REJECTED.value: "FAILED",
    CallOutcome.UNREACHABLE.value: "FAILED",
    CallOutcome.NETWORK_FAILURE.value: "FAILED",
    CallOutcome.CANCELLED.value: "CANCELED",
    "canceled": "CANCELED",
    CallOutcome.FAILED.value: "FAILED",
}


def outcome_label(outcome: Optional[str], summary: Optional[dict] = None) -> str:
    """Human disposition: the AI summary's label when it exists (e.g.
    ``qualified``), else the telephony outcome."""
    if isinstance(summary, dict):
        ai = str(summary.get("outcome") or "").strip()
        if ai:
            head = ai.split("|")[0].split(" - ")[0].split(":")[0].strip()
            return (head[:1].upper() + head[1:])[:60] if head else ai[:60]
    key = str(outcome or "").strip().lower()
    return _OUTCOME_LABELS.get(key, key.replace("_", " ").title() if key else "Completed")


def provider_outcome(provider: str, outcome: Optional[str], summary: Optional[dict] = None) -> str:
    """HubSpot needs its enumerated hs_call_status; Salesforce takes free text."""
    key = str(outcome or "").strip().lower()
    if provider == "hubspot":
        return _HUBSPOT_STATUS.get(key, "COMPLETED")
    return outcome_label(outcome, summary)


def build_call_body(call: Dict[str, Any], summary: Optional[dict], *, campaign_name: Optional[str]) -> str:
    """The text that lands in the CRM activity."""
    direction = str(call.get("direction") or "outbound").lower()
    duration = int(call.get("duration_seconds") or 0)
    lines: List[str] = [
        f"Talky.ai {direction} call - {outcome_label(call.get('outcome'), summary)}",
    ]
    if campaign_name:
        lines.append(f"Campaign: {campaign_name}")
    lines.append(f"Duration: {duration // 60}m {duration % 60}s")
    started = call.get("started_at") or call.get("created_at")
    if started:
        lines.append(f"When: {started if isinstance(started, str) else started.isoformat()}")
    if call.get("phone_number"):
        lines.append(f"Number: {call['phone_number']}")

    if isinstance(summary, dict) and summary.get("headline"):
        lines.append("")
        lines.append(f"Summary: {summary.get('headline')}")
        if summary.get("next_step"):
            lines.append(f"Next step: {summary.get('next_step')}")
        tips = summary.get("follow_up_tips")
        if isinstance(tips, list) and tips:
            lines.append("Follow-up tips:")
            lines.extend(f"- {tip}" for tip in tips[:4] if tip)

    if call.get("recording_url"):
        lines.append("")
        lines.append(f"Recording: {call['recording_url']}")

    transcript = str(call.get("transcript") or "").strip()
    if transcript:
        excerpt = transcript[:_TRANSCRIPT_EXCERPT_CHARS]
        if len(transcript) > _TRANSCRIPT_EXCERPT_CHARS:
            excerpt += " ..."
        lines.append("")
        lines.append("Transcript excerpt:")
        lines.append(excerpt)

    lines.append("")
    lines.append(f"Talky.ai call id: {call.get('id')}")
    return "\n".join(lines)


def _coerce_json(value: Any) -> Optional[dict]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except ValueError:
            return None
    return None


def _crm_ids(lead: Optional[Dict[str, Any]]) -> Dict[str, str]:
    custom = _coerce_json((lead or {}).get("custom_fields")) or {}
    ids = custom.get("crm_ids")
    return {str(k): str(v) for k, v in ids.items() if v} if isinstance(ids, dict) else {}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class CRMSyncService:
    """Log one finished call into every active CRM connector of its tenant."""

    def __init__(self, db_client, db_pool=None):
        self.db_client = db_client
        self.db_pool = db_pool or getattr(db_client, "pool", None)
        self.deliveries = CRMDeliveryStore(self.db_pool)

    # ----- public -----------------------------------------------------

    async def sync_call(self, tenant_id: str, call_id: str, *, reason: str = "settlement") -> CRMSyncResult:
        """Idempotent: safe to call from both hooks and on retry."""
        lock = _call_locks.setdefault(call_id, asyncio.Lock())
        try:
            async with lock:
                return await self._sync_call_locked(str(tenant_id), str(call_id), reason=reason)
        finally:
            if not lock.locked():
                _call_locks.pop(call_id, None)

    async def _sync_call_locked(self, tenant_id: str, call_id: str, *, reason: str) -> CRMSyncResult:
        try:
            providers = list_active_connector_providers(self.db_client, tenant_id, "crm")
        except ConnectorLookupError as exc:
            return CRMSyncResult(success=False, call_id=call_id, error_message=exc.message)
        if not providers:
            return CRMSyncResult(success=False, call_id=call_id, skipped=True,
                skipped_reason="no_crm_connected", warning_message=CRMNotConnectedWarning.MISSING_CRM)
        call = await self._load_call(tenant_id, call_id)
        if call is None:
            return CRMSyncResult(success=False, call_id=call_id, error_message="call not found")
        # Transcript revisions can commit before their replacement summary.
        # A delivery must not re-export stale AI conclusions beside the caller's
        # corrected words. Historical summaries without source proof are also
        # omitted; the current transcript/outcome remain available.
        summary = _coerce_json(call.get("summary_json"))
        if call.get("summary_transcript_hash") != transcript_revision(call):
            summary = None
        lead = await self._recipient_lead(tenant_id, call, await self._load_lead(tenant_id, call.get("lead_id")))
        body = build_call_body(call, summary, campaign_name=await self._campaign_name(tenant_id, call.get("campaign_id")))
        successful, errors = [], []
        first_contact, first_log, updated = None, None, False
        for provider in providers:
            # Persist intent before any external request. A trigger also queues
            # this in the call's transaction if the teardown hook never runs.
            desired = hashlib.sha256(json.dumps({
                "body": body, "outcome": provider_outcome(provider, call.get("outcome"), summary),
                "recipient": self._recipient_identity(call, lead),
                "contact_evidence": lead.get("_crm_contact_evidence_sha256"),
            }, sort_keys=True).encode()).hexdigest()
            receipt = await self.deliveries.enqueue(tenant_id, call_id, provider, desired,
                legacy_id=call.get("crm_call_id"), source_revision=call["source_revision"])
            if not receipt.get("source_current", True):
                errors.append(f"{provider}: newer call details queued for delivery")
                continue
            if receipt["status"] == "succeeded":
                successful.append(provider)
                first_log = first_log or receipt.get("remote_call_id")
                continue
            receipt = await self.deliveries.claim(tenant_id, call_id, provider,
                source_revision=call["source_revision"], expected_key=desired)
            if receipt is None:
                errors.append(f"{provider}: delivery pending, held for review, or already processing")
                continue
            try:
                was_update = bool(receipt.get("remote_call_id"))
                delivered = await asyncio.wait_for(self._deliver(receipt, call, lead, body, summary), 60)
                if delivered:
                    successful.append(provider)
                    first_contact = first_contact or receipt.get("remote_contact_id")
                    first_log = first_log or receipt.get("remote_call_id")
                    updated = updated or was_update
            except Exception as exc:
                phase = receipt.get("phase")
                # A rejected request is safe to retry. A lost POST response is
                # not: even an HTTP 5xx can follow a committed provider write.
                code = getattr(exc, "status_code", None)
                rejected = code is not None and 400 <= code < 500 and code != 408
                unknown = isinstance(exc, CRMDestinationMismatch) or (
                    phase in ("creating_contact", "creating_call", "legacy_unverified") and not rejected)
                permanent = rejected and getattr(exc, "category", None) not in ("authentication", "rate_limit")
                state = "unknown" if unknown else "failed" if permanent or receipt["attempts"] >= MAX_ATTEMPTS else "pending"
                category = getattr(exc, "category", type(exc).__name__)
                detail = f"{category}: delivery {state}"
                await self.deliveries.save(receipt, status=state, error=detail)
                errors.append(f"{provider}: {detail}")
                logger.warning("crm_sync provider=%s call=%s phase=%s status=%s", provider, call_id, phase, state)
        if successful and first_log:
            await self._mark_call_synced(tenant_id, call_id, first_log)
        return CRMSyncResult(success=bool(successful) and not errors, call_id=call_id,
            providers=successful, crm_contact_id=first_contact, crm_call_id=first_log,
            updated_existing=updated, error_message="; ".join(errors) or None)

    async def _deliver(self, receipt, call, lead, body, summary):
        tenant_id, provider = str(receipt["tenant_id"]), receipt["provider"]
        async def validate_source(fresh):
            await self._validate_write_source(tenant_id, call, lead,
                connector=fresh, reviewed_settings=settings, provider=provider)
        if receipt["phase"] == "legacy_unverified":
            raise RuntimeError("Legacy shared CRM ID requires provider ownership review")
        if receipt.get("reconcile") and receipt["phase"] == "creating_call":
            # A matching title/subject proves neither this uncertain write nor
            # its original contact association. Keep the saved evidence for
            # inspection; never adopt or patch an activity based on that match.
            raise CRMDestinationMismatch("Call creation outcome requires original-account review")
        connector = await self._connector(tenant_id, provider,
            connector_id=str(receipt['destination_connector_id']) if receipt.get('destination_connector_id') else None)
        if not await self.deliveries.bind_destination(receipt, str(connector.connector_id), str(connector.external_account_id)):
            raise CRMDestinationMismatch("CRM account changed or remote ID ownership is unverified; review required")
        settings = json.loads(json.dumps(getattr(connector, "config", None) or {}))
        if not self._provider_wants_this_call(provider, settings, call):
            await self.deliveries.save(receipt, status="skipped", error="Disabled by connector settings")
            return False
        if receipt.get("reconcile") and receipt["phase"] == "creating_contact":
            # A current broad email/phone search cannot prove which record an
            # earlier uncertain create produced. Keep its original arguments
            # and account for review; never substitute today's lead details.
            raise CRMDestinationMismatch("Contact creation outcome requires original-account review")
        effect = _coerce_json(receipt.get("contact_effect"))
        recipient = self._recipient_identity(call, lead)
        if effect is not None:
            if (effect.get("recipient") != recipient
                    or effect.get("account_id") != str(connector.external_account_id)
                    or effect.get("connector_id") != str(connector.connector_id)):
                raise CRMDestinationMismatch("The original CRM recipient has changed; review required")
            if not receipt.get("remote_contact_id") and not receipt.get("remote_call_id"):
                if (effect.get("arguments") != self._contact_arguments(provider, call, lead, body)
                        or effect.get("source_contact_evidence_sha256") != lead.get("_crm_contact_evidence_sha256")):
                    raise CRMDestinationMismatch("The original CRM contact intent changed before creation; review required")
        elif (receipt.get("remote_contact_id") or receipt.get("remote_call_id")) and lead.get("_crm_explicit_contacts"):
            raise CRMDestinationMismatch("Historical CRM recipient ownership is unverified; review required")
        remote_call = receipt.get("remote_call_id")
        if remote_call:
            await self.deliveries.save(receipt, phase="updating_call")
            updated = await self._with_auth_retry(tenant_id, provider, connector,
                lambda c: c.update_call_log(str(remote_call), call_body=body,
                    outcome=provider_outcome(provider, call.get("outcome"), summary)),
                before_write=validate_source)
            if not updated:
                raise RuntimeError("Provider did not confirm the call update")
        else:
            contact = receipt.get("remote_contact_id")
            if not contact:
                await self.deliveries.save(receipt, phase="resolving_contact")
                if not recipient["email"] and not recipient["phone"]:
                    raise CRMDestinationMismatch("No usable CRM contact channel is available")
                if effect is None:
                    arguments = self._contact_arguments(provider, call, lead, body)
                    encoded = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
                    effect = {
                        "version": 1, "provider": provider,
                        "connector_id": str(connector.connector_id),
                        "account_id": str(connector.external_account_id),
                        "call_id": str(call["id"]),
                        "source_call_revision": call.get("source_revision"),
                        "source_lead_revision": (lead or {}).get("source_revision"),
                        "source_contact_evidence_sha256": lead.get("_crm_contact_evidence_sha256"),
                        "recipient": recipient,
                        "arguments_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                        "arguments": arguments,
                    }
                    try:
                        await self.deliveries.bind_contact_effect(receipt, effect)
                    except RuntimeError as exc:
                        raise CRMDestinationMismatch("CRM contact source or ownership changed before admission") from exc
                async def before_create(arguments):
                    try:
                        await self.deliveries.begin_contact_create(receipt, effect,
                            source_revision=call.get("source_revision"))
                    except RuntimeError as exc:
                        raise CRMDestinationMismatch("CRM original contact source is no longer current") from exc
                async def validate_create(fresh):
                    await self._validate_write_source(tenant_id, call, lead,
                        connector=fresh, reviewed_settings=settings,
                        expected_create=effect["arguments"], provider=provider, body=body)
                contact = await self._resolve_contact(tenant_id, provider, connector, call, lead, settings,
                    body_for_new=body, before_create=before_create, create_arguments=effect["arguments"],
                    before_write=validate_create)
                if not contact:
                    raise ConnectorProviderError(provider=provider, operation="resolve_contact",
                        category="invalid_request", status_code=400,
                        message="No contact found and contact creation disabled or impossible")
                await self.deliveries.save(receipt, contact_id=str(contact))
            await self.deliveries.save(receipt, phase="creating_call")
            remote_call = await self._with_auth_retry(tenant_id, provider, connector,
                lambda c: c.log_call(contact_id=contact, call_body=body,
                    duration_seconds=int(call.get("duration_seconds") or 0),
                    outcome=provider_outcome(provider, call.get("outcome"), summary),
                    call_direction=str(call.get("direction") or "outbound").upper(), timestamp=self._call_timestamp(call)),
                before_write=validate_source)
            if not remote_call:
                raise RuntimeError("Provider returned no call ID")
            # Persist the receipt before treating the operation as successful.
            await self.deliveries.save(receipt, call_id=str(remote_call))
        await self.deliveries.save(receipt, status="succeeded", phase="complete")
        return True

    # ----- provider helpers -------------------------------------------

    async def _write_admission_stamp(self, tenant_id, call_id, connector_id, provider):
        """One DB snapshot of the authority used by this prepared write.

        At most two current primary rows and two winning manual rows are
        retained. Empty sets are significant: an inserted/deleted correction
        must invalidate a read too. Account selection matches the resolver's
        newest active account, rather than accepting any historical match.
        Token values/rotation timestamps are deliberately not returned.
        """
        async with acquire_with_tenant(self.db_pool, tenant_id) as conn:
            row = await conn.fetchrow(
                """SELECT c.xmin::text AS call_revision, c.lead_id::text AS lead_id,
                          l.xmin::text AS lead_revision,
                          x.id::text AS connector_id, x.provider, x.status AS connector_status,
                          x.config AS connector_config,
                          a.id::text AS account_row_id, a.external_account_id,
                          a.status AS account_status, a.scopes AS account_scopes,
                          COALESCE((SELECT jsonb_agg(p.stamp ORDER BY p.kind,p.field_key)
                            FROM (
                              SELECT 'current' AS kind,d.field_key,
                                     jsonb_build_array('current',d.field_key,d.id::text,d.xmin::text) AS stamp
                                FROM call_lead_details d
                               WHERE d.tenant_id=c.tenant_id AND d.call_id=c.id
                                 AND d.field_key IN ('email','phone')
                              UNION ALL
                              SELECT 'manual',m.field_key,m.stamp FROM (
                                SELECT DISTINCT ON (d.field_key) d.field_key,
                                       jsonb_build_array('manual',d.field_key,d.id::text,d.xmin::text) AS stamp
                                  FROM call_lead_details d JOIN calls source
                                    ON source.id=d.call_id AND source.tenant_id=d.tenant_id
                                 WHERE d.tenant_id=c.tenant_id AND source.lead_id=c.lead_id
                                   AND d.field_key IN ('email','phone') AND d.source='manual_edit'
                                 ORDER BY d.field_key,d.updated_at DESC,source.created_at DESC
                              ) m
                            ) p),'[]'::jsonb) AS primary_rows
                     FROM calls c
                     LEFT JOIN leads l ON l.id=c.lead_id AND l.tenant_id=c.tenant_id
                     JOIN connectors x ON x.id=$3::uuid AND x.tenant_id=c.tenant_id
                       AND x.type='crm' AND x.provider=$4 AND x.status='active'
                     JOIN LATERAL (
                       SELECT id,external_account_id,status,scopes FROM connector_accounts
                        WHERE connector_id=x.id AND tenant_id=c.tenant_id AND status='active'
                        ORDER BY last_refreshed_at DESC LIMIT 1
                     ) a ON TRUE
                    WHERE c.tenant_id=$1::uuid AND c.id=$2::uuid""",
                str(tenant_id), str(call_id), str(connector_id), provider,
            )
        if row is None:
            raise CRMDestinationMismatch("CRM source or original account is no longer active")
        result = dict(row)
        for key in ("connector_config", "primary_rows"):
            if isinstance(result[key], str):
                result[key] = json.loads(result[key])
        result["connector_config"] = result["connector_config"] or {}
        return result

    async def _validate_write_source(self, tenant_id, call, lead, *, connector,
            reviewed_settings, expected_create=None, provider=None, body=None):
        identity = connector_identity(connector, str(connector.connector_id), provider)
        before = await self._write_admission_stamp(tenant_id, str(call["id"]),
            identity["connector_id"], identity["provider"])
        if (before.get("external_account_id") != identity["external_account_id"]
                or (provider == "salesforce" and before.get("connector_config") != reviewed_settings)
                or (getattr(connector, "config", None) or {}) != reviewed_settings):
            raise CRMDestinationMismatch("CRM original account or write settings changed before admission")
        current_call = await self._load_call(tenant_id, str(call["id"]))
        if current_call is None or current_call.get("source_revision") != call.get("source_revision"):
            raise CRMDestinationMismatch("CRM call source changed before the write; review required")
        current = await self._recipient_lead(tenant_id, current_call,
            await self._load_lead(tenant_id, current_call.get("lead_id")))
        if (self._recipient_identity(current_call, current) != self._recipient_identity(call, lead)
                or current.get("_crm_contact_evidence_sha256") != lead.get("_crm_contact_evidence_sha256")):
            raise CRMDestinationMismatch("CRM contact authority changed before the write; review required")
        if expected_create is not None and self._contact_arguments(provider, current_call, current, body) != expected_create:
            raise CRMDestinationMismatch("CRM contact creation arguments changed before the write; review required")
        after = await self._write_admission_stamp(tenant_id, str(call["id"]),
            identity["connector_id"], identity["provider"])
        if after != before:
            raise CRMDestinationMismatch("CRM source or original account changed during admission")
        # This final DB snapshot is the write admission point. The caller now
        # invokes the provider directly; commits after admission cannot
        # atomically cancel a request already sent to another system.

    @staticmethod
    def _recipient_identity(call, lead):
        phone = lead.get("phone_number")
        if "phone" not in lead.get("_crm_explicit_contacts", ()):
            phone = phone or call.get("phone_number")
        return {"email": lead.get("email") or None, "phone": phone or None}

    async def _recipient_lead(self, tenant_id, call, lead):
        """Use authoritative primary evidence; never promote a second contact.

        Imported lead channels remain the fallback only when there is no
        explicit primary evidence. An unconfirmed/withdrawn/revised primary
        suppresses its channel, so it cannot resurrect an older import.
        """
        result = dict(lead or {})
        service = LeadCaptureService(self.db_pool)
        current = await service.details_for_call(tenant_id, str(call["id"]))
        rows = {r["field_key"]: r for r in current
                if r.get("field_key") in ("email", "phone")
                and r.get("field_type") == r.get("field_key")}
        if call.get("lead_id"):
            for row in await service.details_for_lead(tenant_id, str(call["lead_id"])):
                if (row.get("source") == "manual_edit" and row.get("field_key") in ("email", "phone")
                        and row.get("field_type") == row.get("field_key")):
                    rows[row["field_key"]] = row
        for kind, row in rows.items():
            evidence = row.get("evidence") or {}
            supported = row.get("source") == "manual_edit" or (
                row.get("source") == "caller_stated" and evidence.get("provenance_status") == "matched")
            usable = supported and row.get("confirmed") is True and row.get("validation_status") == "confirmed"
            result["email" if kind == "email" else "phone_number"] = (
                (row.get("normalized_value") or row.get("value")) if usable else None)
        result["_crm_explicit_contacts"] = sorted(rows)
        result["_crm_contact_evidence_sha256"] = hashlib.sha256(json.dumps(
            rows, sort_keys=True, default=str).encode()).hexdigest() if rows else None
        return result

    def _contact_arguments(self, provider, call, lead, body):
        recipient = self._recipient_identity(call, lead)
        return {"email": recipient["email"] or "", "first_name": lead.get("first_name"),
                "last_name": lead.get("last_name"), "phone": recipient["phone"],
                "properties": self._new_contact_properties(provider, lead, body)}

    @staticmethod
    def _provider_wants_this_call(provider: str, settings: Dict[str, Any], call: Dict[str, Any]) -> bool:
        if provider != "salesforce":
            return True
        if settings.get("log_calls") is False:
            return False
        if str(call.get("direction") or "outbound").lower() == "inbound" and settings.get("sync_inbound") is False:
            return False
        return True

    async def _connector(self, tenant_id: str, provider: str, *, force_refresh: bool = False, connector_id=None):
        try:
            connector, _connector_id, _provider = await resolve_active_connector(
                self.db_client, tenant_id, "crm", provider=provider, force_refresh=force_refresh, connector_id=connector_id,
            )
        except ConnectorNotConnectedError as exc:
            if connector_id:
                raise CRMDestinationMismatch("The saved CRM connection is no longer usable; review required") from exc
            raise
        if not connector.external_account_id:
            identity = await connector.fetch_account_identity()
            connector.external_account_id = str((identity or {}).get('external_account_id') or '') or None
        if not connector.external_account_id:
            raise CRMDestinationMismatch("CRM account identity could not be verified; review required")
        return connector

    async def _with_auth_retry(self, tenant_id: str, provider: str, connector, op, *, before_write=None):
        """Run ``op(connector)``; on an authentication failure refresh once
        (Salesforce sessions expire without warning) and retry."""
        reviewed = connector_identity(connector, str(connector.connector_id), provider)
        if before_write is not None:
            fresh = await self._connector(tenant_id, provider, connector_id=reviewed["connector_id"])
            try:
                verify_reviewed_connector(fresh, str(fresh.connector_id), provider, reviewed)
            except ReviewedConnectorChanged as exc:
                raise CRMDestinationMismatch("CRM account changed before the write; no effect sent") from exc
            await before_write(fresh)
            connector = fresh
        try:
            return await op(connector)
        except ConnectorProviderError as exc:
            if exc.category != "authentication":
                raise
            logger.info("crm_sync provider=%s auth failure — forcing token refresh once", provider)
            fresh = await self._connector(tenant_id, provider, force_refresh=True, connector_id=str(connector.connector_id))
            if (str(fresh.connector_id), fresh.external_account_id) != (str(connector.connector_id), connector.external_account_id):
                raise CRMDestinationMismatch("CRM account changed during token refresh; no write retried")
            if before_write is not None:
                await before_write(fresh)
            return await op(fresh)

    async def _resolve_contact(
        self,
        tenant_id: str,
        provider: str,
        connector,
        call: Dict[str, Any],
        lead: Optional[Dict[str, Any]],
        settings: Dict[str, Any],
        *,
        body_for_new: str,
        before_create=None,
        allow_create=True,
        create_arguments=None,
        before_write=None,
    ) -> Optional[str]:
        ids = _crm_ids(lead)
        custom = _coerce_json((lead or {}).get('custom_fields')) or {}
        destinations = custom.get('crm_destinations') or {}
        saved = destinations.get(provider) if isinstance(destinations, dict) else None
        if (not (lead or {}).get("_crm_explicit_contacts")
                and isinstance(saved, dict) and saved.get('account_id') == connector.external_account_id
                and saved.get('contact_id')):
            return str(saved['contact_id'])
        # Provider-only/legacy IDs carry no account proof. Resolve the contact
        # in the verified current account instead of attaching an old ID.

        recipient = self._recipient_identity(call, lead or {})
        email, phone = recipient["email"], recipient["phone"]
        found = await self._with_auth_retry(
            tenant_id, provider, connector, lambda c: c.search_contact(email=email, phone=phone),
        )
        contact_id: Optional[str] = None
        if found and found.get("id"):
            contact_id = str(found["id"])
        else:
            if not allow_create:
                return None
            if provider == "salesforce" and settings.get("create_leads") is False:
                return None
            if provider == "hubspot" and not email:
                # HubSpot contacts are keyed by e-mail; without one there is
                # no contact to log against.
                return None
            arguments = create_arguments or self._contact_arguments(provider, call, lead or {}, body_for_new)
            if before_create is not None:
                await before_create(arguments)
            created = await self._with_auth_retry(
                tenant_id, provider, connector,
                lambda c: c.create_contact(**arguments),
                before_write=before_write,
            )
            contact_id = str(created.get("id")) if created and created.get("id") else None
            if contact_id:
                logger.info("crm_sync provider=%s created record %s for lead %s", provider, contact_id, (lead or {}).get("id"))

        if contact_id and lead and lead.get("id"):
            await self._remember_contact_id(tenant_id, str(lead["id"]), provider, contact_id, ids,
                account_id=connector.external_account_id, connector_id=str(connector.connector_id))
        return contact_id

    @staticmethod
    def _new_contact_properties(provider: str, lead: Optional[Dict[str, Any]], body: str) -> Dict[str, Any]:
        if provider != "salesforce":
            return {}
        custom = _coerce_json((lead or {}).get("custom_fields")) or {}
        company = (lead or {}).get("company_name") or custom.get("company")
        props: Dict[str, Any] = {"description": f"Created by Talky.ai after a call.\n\n{body}"}
        if company:
            props["company"] = str(company)
        title = (lead or {}).get("job_title")
        if title:
            props["Title"] = str(title)[:128]
        return props

    @staticmethod
    def _call_timestamp(call: Dict[str, Any]) -> datetime:
        for key in ("answered_at", "started_at", "ended_at", "created_at"):
            value = call.get(key)
            if isinstance(value, datetime):
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            if isinstance(value, str) and value:
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        return datetime.now(timezone.utc)

    # ----- SQL ----------------------------------------------------------

    async def _load_call(self, tenant_id: str, call_id: str) -> Optional[Dict[str, Any]]:
        if self.db_pool is None:
            return None
        async with acquire_with_tenant(self.db_pool, tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT xmin::text AS source_revision, id, tenant_id, campaign_id, lead_id, phone_number, direction,
                       status, outcome, duration_seconds, transcript, summary,
                       summary_json, transcript_json, action_results, summary_transcript_hash,
                       recording_url, crm_call_id, crm_synced_at,
                       started_at, answered_at, ended_at, created_at
                  FROM calls
                 WHERE id = $1::uuid AND tenant_id = $2::uuid
                """,
                call_id, tenant_id,
            )
        return dict(row) if row else None

    async def _load_lead(self, tenant_id: str, lead_id: Any) -> Optional[Dict[str, Any]]:
        if not lead_id or self.db_pool is None:
            return None
        async with acquire_with_tenant(self.db_pool, tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT xmin::text AS source_revision, id, first_name, last_name, email, phone_number, crm_contact_id,
                       custom_fields, company_name, job_title
                  FROM leads
                 WHERE id = $1::uuid AND tenant_id = $2::uuid
                """,
                str(lead_id), tenant_id,
            )
        return dict(row) if row else None

    async def _campaign_name(self, tenant_id: str, campaign_id: Any) -> Optional[str]:
        if not campaign_id or self.db_pool is None:
            return None
        try:
            async with acquire_with_tenant(self.db_pool, tenant_id) as conn:
                return await conn.fetchval(
                    "SELECT name FROM campaigns WHERE id = $1::uuid AND tenant_id = $2::uuid",
                    str(campaign_id), tenant_id,
                )
        except Exception:  # noqa: BLE001 — cosmetic
            return None

    async def _remember_contact_id(
        self, tenant_id: str, lead_id: str, provider: str, contact_id: str, existing_ids: Dict[str, str],
        *, account_id: str, connector_id: str,
    ) -> None:
        if self.db_pool is None:
            return
        ids = dict(existing_ids)
        ids[provider] = contact_id
        try:
            async with acquire_with_tenant(self.db_pool, tenant_id) as conn:
                await conn.execute(
                    """
                    UPDATE leads
                       SET crm_contact_id = $3,
                           custom_fields = COALESCE(custom_fields, '{}'::jsonb)
                                           || jsonb_build_object('crm_ids', COALESCE(custom_fields->'crm_ids','{}'::jsonb) || $4::jsonb)
                                           || jsonb_build_object('crm_destinations', COALESCE(custom_fields->'crm_destinations','{}'::jsonb) || $5::jsonb),
                           updated_at = NOW()
                     WHERE id = $1::uuid AND tenant_id = $2::uuid
                    """,
                    lead_id, tenant_id, contact_id, json.dumps({provider: contact_id}),
                    json.dumps({provider: {'account_id': account_id, 'connector_id': connector_id, 'contact_id': contact_id}}),
                )
        except Exception as exc:  # noqa: BLE001 — the CRM write already succeeded
            logger.warning("crm_sync: could not remember %s id for lead %s: %s", provider, lead_id, exc)

    async def _mark_call_synced(self, tenant_id: str, call_id: str, crm_call_id: Optional[str]) -> None:
        if self.db_pool is None:
            return
        try:
            async with acquire_with_tenant(self.db_pool, tenant_id) as conn:
                await conn.execute(
                    """
                    UPDATE calls
                       SET crm_call_id = COALESCE(crm_call_id, $3),
                           crm_synced_at = NOW(),
                           updated_at = NOW()
                     WHERE id = $1::uuid AND tenant_id = $2::uuid
                    """,
                    call_id, tenant_id, crm_call_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("crm_sync: could not mark call %s synced: %s", call_id, exc)


# ---------------------------------------------------------------------------
# Scheduling (fire-and-forget from teardown paths)
# ---------------------------------------------------------------------------

async def drain_crm_deliveries(db_client, pool, *, batch_size=10):
    """Recover queued/expired work after restart on the existing worker."""
    from app.core.security.tenant_isolation import set_current_tenant_id, get_current_tenant_id
    service = CRMSyncService(db_client, db_pool=pool)
    rows = await service.deliveries.due(limit=batch_size)
    previous = get_current_tenant_id()
    try:
        for row in rows:
            tenant_id = str(row["tenant_id"])
            set_current_tenant_id(tenant_id)
            try:
                await service.sync_call(tenant_id, str(row["call_id"]), reason="retry")
            except Exception as exc:
                logger.warning("crm_delivery_worker call=%s failed=%s", row["call_id"], type(exc).__name__)
    finally:
        set_current_tenant_id(previous)
    return len(rows)

async def _lookup_tenant_for_call(pool, call_id: str) -> Optional[str]:
    """Teardown hooks sometimes only know the call id. The row lookup needs
    the RLS bypass exactly like ``bind_telephony_call`` does."""
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                value = await conn.fetchval(
                    "SELECT tenant_id FROM calls WHERE id = $1::uuid", str(call_id)
                )
        return str(value) if value else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("crm_sync: tenant lookup failed for call %s: %s", call_id, exc)
        return None


async def run_crm_sync(call_id: str, *, tenant_id: Optional[str] = None, reason: str = "settlement") -> Optional[CRMSyncResult]:
    """Resolve the container, set the tenant context and sync one call.
    Never raises."""
    try:
        from app.core.container import get_container
        from app.core.security.tenant_isolation import set_current_tenant_id

        container = get_container()
        if not container.is_initialized:
            return None
        pool = container.db_pool
        tenant = tenant_id or await _lookup_tenant_for_call(pool, call_id)
        if not tenant:
            return None
        # The task inherits a copy of the caller's context; scope every
        # adapter query in this task to the call's tenant.
        set_current_tenant_id(str(tenant))
        service = CRMSyncService(container.db_client, db_pool=pool)
        result = await service.sync_call(str(tenant), str(call_id), reason=reason)
        if result.success:
            logger.info(
                "crm_sync call=%s reason=%s providers=%s crm_call_id=%s",
                call_id, reason, ",".join(result.providers), result.crm_call_id,
            )
        return result
    except Exception as exc:  # noqa: BLE001 — teardown side effect
        logger.warning("crm_sync call=%s reason=%s failed: %r", call_id, reason, exc)
        return None


def schedule_crm_sync(call_id: Optional[str], *, tenant_id: Optional[str] = None, reason: str = "settlement") -> None:
    """Fire-and-forget entry point for teardown hooks. Never raises, never blocks."""
    if not call_id:
        return
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(run_crm_sync(str(call_id), tenant_id=tenant_id, reason=reason))
        _inflight_tasks.add(task)
        task.add_done_callback(_inflight_tasks.discard)
    except RuntimeError:
        # No running loop — only in synchronous tests / scripts.
        pass


# Backwards-compatible singleton getter (older callers).
_crm_sync_service: Optional[CRMSyncService] = None


def get_crm_sync_service(db_client) -> CRMSyncService:
    global _crm_sync_service
    if _crm_sync_service is None or _crm_sync_service.db_client is not db_client:
        _crm_sync_service = CRMSyncService(db_client)
    return _crm_sync_service
