"""Structured lead capture — what the agent learned, and how much to trust it.

goals.md §7. Reads the campaign's field DEFINITIONS, writes the per-call captured
VALUES, and keeps provenance attached to every one of them.

THE PROVENANCE IS NOT DECORATION
---------------------------------
§7 is explicit: "Record the source of each value" and "Do not treat inferred
values as confirmed facts." A budget the model INFERRED from "we're only a small
outfit" is a different kind of thing from a budget the caller SAID, and a CRM
that cannot tell them apart will eventually act on the wrong one — which is
worse than not capturing it, because nobody knows to check.

So every value carries:

    source     where it came from, one of four
    confirmed  the capture machine accepted caller confirmation; receipt
               provenance is separate and is never proof of human hearing

`TRUST_ORDER` encodes the one rule that matters when the same field is captured
twice on one call: a manual edit beats what the caller said, which beats what
the model guessed. Without it, a late inference could silently overwrite a
confirmed fact — the agent hearing "…so maybe next quarter" after the caller
already gave a firm date.

ABSENT IS NOT NULL
------------------
§7 asks the agent to use "unknown" when information was not provided. That is
represented by writing NO ROW, not the string "unknown":

    no row          never established
    row, NULL value no usable value; validation/evidence state explains why

Those want different follow-up, and collapsing them loses the distinction
permanently.
"""
from __future__ import annotations

import json
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

# Increasing order of trust. Index = authority; a write only wins if its source
# ranks at or above the value already stored.
TRUST_ORDER = ("agent_inferred", "imported", "caller_stated", "manual_edit")

VALID_SOURCES = frozenset(TRUST_ORDER)
VALID_TYPES = frozenset({
    "text", "number", "email", "phone", "datetime",
    "single_select", "multi_select", "notes",
})

MAX_VALUE_CHARS = 4000
CONTACT_VALIDATION_STATUSES = frozenset({
    "needs_clarification",
    "invalid",
    "awaiting_confirmation",
    "confirmed",
    "cancelled",
})


def project_contact_evidence(row: dict, transcript_json) -> dict:
    """Do not present superseded caller evidence as an active saved contact.

    The persisted row remains audit evidence; projection compares its bounded
    source identities to the currently durable caller transcript. Manual edits
    are independent. Legacy rows retain their historical claim, explicitly
    unversioned rather than being promoted to newly verified evidence.
    """
    result = dict(row)
    evidence = result.get("evidence") or {}
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except ValueError:
            evidence = {}
    evidence = dict(evidence) if isinstance(evidence, dict) else {}
    result["evidence"] = evidence
    if result.get("field_type") not in {"email", "phone"} or result.get("source") != "caller_stated":
        return result
    owners = {name: evidence.get(name) for name in ("value_source", "confirmation_source", "status_source")}
    if not any(owner is not None for owner in owners.values()):
        evidence["provenance_status"] = "unversioned"
        return result
    if isinstance(transcript_json, str):
        try:
            transcript_json = json.loads(transcript_json)
        except ValueError:
            transcript_json = []
    if isinstance(transcript_json, dict):
        transcript_json = transcript_json.get("turns")
    from app.domain.services.transcript_service import effective_turn
    from app.domain.services.voice_pipeline.contact_capture import ContactSource
    turns = [effective_turn(item) for item in (transcript_json or []) if isinstance(item, dict)] if isinstance(transcript_json, (list, tuple)) else []
    checks = {}
    if result.get("value") is not None and owners["value_source"] is None:
        checks["value_source"] = "unavailable"
    if result.get("confirmed") and owners["confirmation_source"] is None:
        checks["confirmation_source"] = "unavailable"
    for name, owner in owners.items():
        if owner is None:
            continue
        try:
            source = ContactSource(**owner)
        except (ValueError, TypeError):
            checks[name] = "unavailable"
            continue
        matches = [item for item in turns if item.get("role") == "user" and item.get("is_final") is not False
                   and isinstance(item.get("metadata"), dict)
                   and item["metadata"].get("provider_item_id") == source.provider_item_id
                   and item["metadata"].get("caller_turn_order") == source.caller_turn_order]
        if len(matches) != 1:
            checks[name] = "unavailable"
        elif matches[0].get("effective_content_status") == "unavailable":
            checks[name] = "unavailable"
        else:
            content = str(matches[0].get("content") or "").strip()
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            checks[name] = "matched" if digest == source.revision_sha256 else "changed"
    evidence["source_checks"] = checks
    if all(status == "matched" for status in checks.values()):
        evidence["provenance_status"] = "matched"
        return result
    evidence["provenance_status"] = "needs_review"
    evidence["status"] = "needs_review"
    if result.get("confirmed_at") is not None:
        evidence["stored_confirmation_at"] = str(result["confirmed_at"])
    result["confirmed"] = False
    result["confirmed_at"] = None
    if checks.get("value_source") != "matched":
        result["value"] = None
        result["normalized_value"] = None
        result["validation_status"] = "needs_clarification"
    elif result.get("value") is not None:
        result["validation_status"] = "awaiting_confirmation"
    return result


class InvalidCaptureError(ValueError):
    """Rejected before it reaches the database."""


def _rank(source: str) -> int:
    try:
        return TRUST_ORDER.index(source)
    except ValueError:
        return -1


def normalise_value(value: Any, field_type: str) -> Optional[str]:
    """One text column, so multi-select becomes a JSON array and everything
    else becomes a trimmed string. Returns None for a genuinely empty value —
    which the caller should store as "asked and declined", not skip."""
    if value is None:
        return None
    if field_type == "multi_select":
        items = value if isinstance(value, (list, tuple)) else [value]
        cleaned = [str(v).strip() for v in items if str(v).strip()]
        return json.dumps(cleaned) if cleaned else None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > MAX_VALUE_CHARS:
        # Truncating a captured value silently would put a half-sentence in a
        # CRM. Refuse instead; the caller decides what to do.
        raise InvalidCaptureError(
            f"value for this field is {len(text)} chars, over the "
            f"{MAX_VALUE_CHARS} limit"
        )
    return text


class LeadCaptureService:
    """All queries go through ``acquire_with_tenant`` so RLS sees a tenant."""

    def __init__(self, pool) -> None:
        self._pool = pool

    # ── definitions ─────────────────────────────────────────────────────────

    async def fields_for_campaign(
        self, tenant_id: str, campaign_id: str, *, agent_only: bool = False
    ) -> list[dict]:
        """The field definitions for a campaign.

        ``agent_only`` filters to what the model is actually told to chase.
        A field can be captured for reporting without the agent ever being
        asked for it, which is why agent_visible and user_visible are separate
        columns rather than one 'visible' flag.
        """
        from app.core.db_utils import acquire_with_tenant

        clause = " AND agent_visible" if agent_only else ""
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            rows = await conn.fetch(
                f"""
                SELECT field_key, label, field_type, is_required,
                       agent_visible, user_visible, options, sort_order
                  FROM campaign_lead_fields
                 WHERE campaign_id = $1::uuid{clause}
                 ORDER BY sort_order, label
                """,
                str(campaign_id),
            )
        return [dict(r) for r in rows]

    # ── capture ─────────────────────────────────────────────────────────────

    async def capture(
        self,
        *,
        tenant_id: str,
        call_id: str,
        field_key: str,
        value: Any,
        source: str,
        field_type: str = "text",
        confirmed: bool = False,
        campaign_id: Optional[str] = None,
        lead_id: Optional[str] = None,
        is_required: bool = False,
        raw_value: Any = None,
        normalized_value: Any = None,
        validation_status: Optional[str] = None,
        confirmed_at: Optional[datetime] = None,
        evidence: Optional[dict] = None,
        expected_transcript: Optional[str] = None,
        expected_summary_hash: Optional[str] = None,
        expected_summary_snapshot: Optional[list] = None,
        expected_contact: Optional[dict] = None,
    ) -> bool:
        """Store one captured field. Returns True if it was written.

        A LOWER-TRUST SOURCE DOES NOT OVERWRITE A HIGHER ONE. The agent
        inferring something late in a call must not replace what the caller
        explicitly said earlier — and it must certainly not replace a human's
        manual correction. That rule lives in the ON CONFLICT clause so it holds
        even when two writers race.
        """
        if source not in VALID_SOURCES:
            raise InvalidCaptureError(
                f"unknown source {source!r}; expected one of {sorted(VALID_SOURCES)}"
            )
        if field_type not in VALID_TYPES:
            raise InvalidCaptureError(f"unknown field type {field_type!r}")
        key = (field_key or "").strip()
        if not key:
            raise InvalidCaptureError("field_key is required")
        contact_key = re.fullmatch(r"(email|phone)(?:_([2-9]|[1-9][0-9]+))?", key)
        if contact_key and field_type != contact_key.group(1):
            raise InvalidCaptureError("contact field_key requires its matching email/phone type")

        stored = normalise_value(value, field_type)
        is_contact = field_type in {"email", "phone"}
        if expected_contact is not None and (not is_contact or source != "caller_stated"):
            raise InvalidCaptureError("contact compare-and-set requires a caller-stated contact")
        manual_withdrawal = (is_contact and source == "manual_edit" and value is None
                             and validation_status == "cancelled" and not confirmed)
        caller_null = (is_contact and source == "caller_stated" and value is None and not confirmed
                       and validation_status in {"needs_clarification", "invalid", "cancelled"})
        if caller_null:
            from app.domain.services.voice_pipeline.contact_capture import ContactSource
            try:
                ContactSource(**((evidence or {}).get("status_source") or (evidence or {}).get("value_source") or {}))
            except (ValueError, TypeError):
                raise InvalidCaptureError("null caller contact requires explicit source evidence") from None
            if normalized_value is not None or confirmed_at is not None:
                raise InvalidCaptureError("unusable caller contact cannot contain a normalized value or confirmation")
        audit_status = validation_status
        audit_raw = None
        audit_normalized = None
        audit_confirmed_at = confirmed_at
        if manual_withdrawal:
            if raw_value is not None or normalized_value is not None or confirmed_at is not None:
                raise InvalidCaptureError("withdrawn manual contact cannot contain a value or confirmation")
            stored = None
            audit_status = "cancelled"
            evidence = {**(evidence or {}), "status": "manually_withdrawn"}
        elif caller_null:
            stored = None
            audit_raw = normalise_value(raw_value, field_type)
        elif is_contact:
            audit_status = audit_status or (
                "confirmed" if confirmed else "awaiting_confirmation"
            )
            if audit_status not in CONTACT_VALIDATION_STATUSES:
                raise InvalidCaptureError(
                    "unknown contact validation_status; expected one of "
                    f"{sorted(CONTACT_VALIDATION_STATUSES)}"
                )
            # 2026-09-28 (owner): a contact the caller SAID is kept even if the
            # read-back never completed (4291700f gave a number that was never
            # shown), stored unconfirmed. Only the caller's own parsed words
            # may do that — never a model guess, an import or a manual edit.
            caller_pending = (
                source == "caller_stated"
                and not confirmed
                and audit_status == "awaiting_confirmation"
            )
            if not caller_pending and (not confirmed or audit_status != "confirmed"):
                raise InvalidCaptureError(
                    "email/phone must be confirmed before persistence unless "
                    "the caller stated it and it is awaiting confirmation"
                )
            if confirmed != (audit_status == "confirmed"):
                raise InvalidCaptureError(
                    "confirmed flag and contact validation_status disagree"
                )
            audit_raw = normalise_value(
                raw_value if raw_value is not None else value,
                field_type,
            )
            audit_normalized = normalise_value(
                normalized_value if normalized_value is not None else value,
                field_type,
            )
            if audit_normalized is None:
                raise InvalidCaptureError("confirmed contact value is empty")
            if field_type == "phone":
                from app.domain.services.phone_number_normalizer import (
                    normalize_phone_for_capture,
                )

                try:
                    audit_normalized = normalize_phone_for_capture(
                        audit_normalized,
                        region=None,
                    )
                except ValueError as exc:
                    raise InvalidCaptureError(
                        "confirmed phone must be a valid +E.164 number"
                    ) from exc
            else:
                from email_validator import EmailNotValidError, validate_email

                try:
                    audit_normalized = validate_email(
                        audit_normalized,
                        check_deliverability=False,
                    ).normalized.lower()
                except EmailNotValidError as exc:
                    raise InvalidCaptureError(
                        "confirmed email must be valid"
                    ) from exc
            if confirmed and audit_confirmed_at is None:
                audit_confirmed_at = datetime.now(timezone.utc)
            if not confirmed:
                audit_confirmed_at = None
            # The primary value presented to CRM/API consumers is canonical.
            stored = audit_normalized
        elif audit_status is not None or confirmed_at is not None:
            raise InvalidCaptureError(
                "contact validation audit fields are only valid for email/phone"
            )

        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO call_lead_details
                    (tenant_id, call_id, campaign_id, lead_id, field_key,
                     field_type, value, source, confirmed, is_required,
                     raw_value, normalized_value, validation_status, confirmed_at, evidence)
                SELECT $1::uuid, $2::uuid, c.campaign_id, c.lead_id, $5::text, $6, $7::text, $8, $9::boolean, $10,
                        $11, $12, $13::text, $14, $16::jsonb
                  FROM calls c WHERE c.id = $2::uuid AND c.tenant_id = $1::uuid
                               AND ($3::uuid IS NULL OR c.campaign_id = $3::uuid)
                               AND ($4::uuid IS NULL OR c.lead_id = $4::uuid)
                               AND ($17::text IS NULL OR transcript = $17)
                               AND ($18::text IS NULL OR summary_transcript_hash = $18)
                               AND ($19::jsonb IS NULL OR jsonb_build_array(COALESCE(transcript,''), transcript_json, action_results) = $19::jsonb)
                               AND ($20::jsonb IS NULL OR $20->>'absent' = 'true'
                                    OR EXISTS (SELECT 1 FROM call_lead_details d
                                        WHERE d.call_id=$2::uuid AND d.tenant_id=$1::uuid AND d.field_key=$5::text
                                          AND (jsonb_build_object('value',d.value,'evidence',d.evidence)=$20::jsonb
                                               OR (d.value IS NOT DISTINCT FROM $7::text AND d.evidence=$16::jsonb
                                                   AND d.confirmed=$9::boolean AND d.validation_status IS NOT DISTINCT FROM $13::text))))
                ON CONFLICT (call_id, field_key) DO UPDATE
                   SET value      = EXCLUDED.value,
                       source     = EXCLUDED.source,
                       -- confirmed is sticky: once the caller has agreed a
                       -- value, a later unconfirmed write of the SAME value
                       -- must not quietly downgrade it to unconfirmed.
                       confirmed  = CASE WHEN EXCLUDED.source='manual_edit' AND EXCLUDED.validation_status='cancelled'
                                    THEN FALSE ELSE call_lead_details.confirmed OR EXCLUDED.confirmed END,
                       field_type = EXCLUDED.field_type,
                       raw_value = EXCLUDED.raw_value,
                       normalized_value = EXCLUDED.normalized_value,
                       validation_status = EXCLUDED.validation_status,
                       confirmed_at = CASE
                           WHEN EXCLUDED.source='manual_edit' AND EXCLUDED.validation_status='cancelled' THEN NULL
                           WHEN EXCLUDED.confirmed
                           THEN COALESCE(
                               EXCLUDED.confirmed_at,
                               call_lead_details.confirmed_at
                           )
                           ELSE call_lead_details.confirmed_at
                       END,
                       evidence = CASE WHEN EXCLUDED.source = 'manual_edit' AND EXCLUDED.validation_status='cancelled'
                                  THEN EXCLUDED.evidence || '{"status":"manually_withdrawn"}'::jsonb
                                  WHEN EXCLUDED.source = 'manual_edit'
                                  THEN EXCLUDED.evidence || '{"status":"manually_verified"}'::jsonb
                                  ELSE EXCLUDED.evidence END,
                       updated_at = NOW()
                 -- BOTH ranks are computed HERE, against the row actually
                 -- present at write time. Comparing against a rank read a
                 -- moment earlier in Python would lose the race this clause
                 -- exists to win.
                 WHERE array_position($15::text[], EXCLUDED.source)
                    >= array_position($15::text[], call_lead_details.source)
                   -- A confirmed value is never replaced by an unconfirmed one,
                   -- whatever the source rank. Without this a same-source retry
                   -- passed the rank test, overwrote value, and the sticky OR
                   -- above kept confirmed=TRUE on a value nobody agreed.
                   AND (NOT (call_lead_details.confirmed AND NOT EXCLUDED.confirmed)
                        OR (EXCLUDED.source='manual_edit' AND EXCLUDED.validation_status='cancelled'))
                   -- RLS is context, not a substitute for ownership in the
                   -- mutation itself. Keep the explicit tenant predicate so
                   -- this upsert cannot cross tenants under any privileged
                   -- maintenance context.
                   AND call_lead_details.tenant_id = $1::uuid
                   AND ($20::jsonb IS NULL
                        OR jsonb_build_object('value',call_lead_details.value,'evidence',call_lead_details.evidence)=$20::jsonb
                        OR (call_lead_details.value IS NOT DISTINCT FROM EXCLUDED.value
                            AND call_lead_details.evidence=EXCLUDED.evidence
                            AND call_lead_details.confirmed=EXCLUDED.confirmed))
                   AND (EXCLUDED.source = 'manual_edit'
                        OR NOT (call_lead_details.evidence ? 'turn_index')
                        OR NOT (EXCLUDED.evidence ? 'turn_index')
                        OR (EXCLUDED.evidence->>'turn_index')::int >= (call_lead_details.evidence->>'turn_index')::int)
                RETURNING id
                """,
                str(tenant_id), str(call_id),
                str(campaign_id) if campaign_id else None,
                str(lead_id) if lead_id else None,
                key, field_type, stored, source, bool(confirmed), bool(is_required),
                audit_raw, audit_normalized, audit_status, audit_confirmed_at,
                list(TRUST_ORDER), json.dumps(evidence or {}), expected_transcript,
                expected_summary_hash,
                json.dumps(expected_summary_snapshot, default=str) if expected_summary_snapshot is not None else None,
                json.dumps(expected_contact) if expected_contact is not None else None,
            )

        if row is None:
            # The WHERE on the DO UPDATE refused: something more trusted is
            # already there. Worth a log line — silently dropping a capture is
            # how you end up unable to explain a missing field.
            logger.info(
                "lead_capture_skipped call=%s field=%s source=%s — a "
                "higher-trust value is already stored",
                str(call_id)[:8], key, source,
            )
            return False
        return True

    async def revoke_caller_contact(
        self,
        *,
        tenant_id: str,
        call_id: str,
        field_key: str,
        validation_status: str,
        expected_contact: Optional[dict] = None,
        revocation_source: Optional[dict] = None,
    ) -> bool:
        """Tombstone a caller-stated contact that is no longer confirmed.

        Corrections and explicit withdrawals must stop exposing the old value
        immediately. The predicate preserves manual/imported higher-trust rows;
        this method never inserts an unconfirmed contact or stores its pending
        replacement.
        """
        key = str(field_key or "").strip()
        match = re.fullmatch(r"(email|phone)(?:_([2-9]|[1-9][0-9]+))?", key)
        if not match:
            raise InvalidCaptureError("only email/phone can be revoked")
        if validation_status not in CONTACT_VALIDATION_STATUSES - {"confirmed"}:
            raise InvalidCaptureError("contact revocation requires a pending status")
        if revocation_source is not None:
            from app.domain.services.voice_pipeline.contact_capture import ContactSource
            try:
                ContactSource(**revocation_source)
            except (ValueError, TypeError):
                raise InvalidCaptureError("invalid revocation source evidence") from None
        marker = {"status": "revoked"}
        if revocation_source is not None:
            marker["status_source"] = revocation_source

        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            row = await conn.fetchrow(
                """
                UPDATE call_lead_details
                   SET value = NULL,
                       confirmed = FALSE,
                       raw_value = NULL,
                       normalized_value = NULL,
                       validation_status = $4,
                       confirmed_at = NULL,
                       evidence = evidence || $7::jsonb,
                       updated_at = NOW()
                 WHERE tenant_id = $1::uuid
                   AND call_id = $2::uuid
                   AND field_key = $3
                   AND field_type = $5
                   AND source = 'caller_stated'
                   AND ($6::jsonb IS NULL OR jsonb_build_object('value',value,'evidence',evidence)=$6::jsonb
                        OR (value IS NULL AND NOT confirmed AND validation_status=$4
                            AND evidence=(($6::jsonb->'evidence') || $7::jsonb)))
                RETURNING id
                """,
                str(tenant_id),
                str(call_id),
                key,
                validation_status,
                match.group(1),
                json.dumps(expected_contact) if expected_contact is not None else None,
                json.dumps(marker),
            )
        return row is not None

    async def capture_many(
        self, *, tenant_id: str, call_id: str, items: Sequence[dict], **common
    ) -> int:
        """Best-effort bulk capture. One bad field must not lose the rest —
        a call that produced five good values and one overlong note should
        keep the five."""
        written = 0
        for item in items:
            try:
                if await self.capture(
                    tenant_id=tenant_id, call_id=call_id,
                    field_key=item.get("field_key", ""),
                    value=item.get("value"),
                    source=item.get("source", "agent_inferred"),
                    field_type=item.get("field_type", "text"),
                    confirmed=bool(item.get("confirmed", False)),
                    raw_value=item.get("raw_value"),
                    normalized_value=item.get("normalized_value"),
                    validation_status=item.get("validation_status"),
                    confirmed_at=item.get("confirmed_at"),
                    evidence=item.get("evidence"),
                    **common,
                ):
                    written += 1
            except InvalidCaptureError as exc:
                logger.warning(
                    "lead_capture_rejected call=%s field=%s — %s",
                    str(call_id)[:8], item.get("field_key"), exc,
                )
        return written

    # ── read ────────────────────────────────────────────────────────────────

    async def details_for_call(self, tenant_id: str, call_id: str) -> list[dict]:
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            rows = await conn.fetch(
                """
                SELECT d.field_key, d.field_type, d.value, d.source, d.confirmed,
                       d.is_required, d.raw_value, d.normalized_value,
                       d.validation_status, d.confirmed_at, d.evidence, d.updated_at,
                       c.transcript_json AS _source_transcript
                  FROM call_lead_details d JOIN calls c ON c.id=d.call_id AND c.tenant_id=d.tenant_id
                 WHERE d.call_id = $1::uuid
                 ORDER BY is_required DESC, field_key
                """,
                str(call_id),
            )
        return [self._detail_row(r) for r in rows]

    @staticmethod
    def _detail_row(row) -> dict:
        result = dict(row)
        if isinstance(result.get("evidence"), str):
            try:
                result["evidence"] = json.loads(result["evidence"])
            except ValueError:
                result["evidence"] = {}
        transcript = result.pop("_source_transcript", None)
        return project_contact_evidence(result, transcript)

    async def details_for_lead(self, tenant_id: str, lead_id: str) -> list[dict]:
        from app.core.db_utils import acquire_with_tenant
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            rows = await conn.fetch(
                """SELECT DISTINCT ON (d.field_key) d.field_key, d.field_type,
                          d.value, d.source, d.confirmed, d.is_required, d.updated_at,
                          d.validation_status, d.evidence, d.call_id,
                          d.raw_value, d.normalized_value, d.confirmed_at,
                          c.transcript_json AS _source_transcript
                     FROM call_lead_details d
                     JOIN calls c ON c.id=d.call_id AND c.tenant_id=d.tenant_id
                    WHERE d.tenant_id=$1::uuid AND c.lead_id=$2::uuid
                    ORDER BY d.field_key, (d.source='manual_edit') DESC,
                             CASE WHEN d.source='manual_edit' THEN d.updated_at END DESC,
                             c.created_at DESC, d.updated_at DESC""",
                str(tenant_id), str(lead_id),
            )
        return [self._detail_row(r) for r in rows]

    async def crm_deliveries(self, tenant_id: str, *, call_id=None, lead_id=None) -> list[dict]:
        """Current receipts for this call, or the contact's latest call."""
        from app.core.db_utils import acquire_with_tenant
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            rows = await conn.fetch(
                """SELECT provider, status, attempts, updated_at
                     FROM crm_deliveries WHERE tenant_id=$1::uuid
                     AND call_id=(SELECT id FROM calls WHERE tenant_id=$1::uuid
                       AND (($2::uuid IS NOT NULL AND id=$2::uuid)
                            OR ($2::uuid IS NULL AND lead_id=$3::uuid))
                       ORDER BY created_at DESC LIMIT 1)
                     ORDER BY provider""",
                str(tenant_id), call_id, lead_id,
            )
        return [dict(row) for row in rows]

    async def processing_status(self, tenant_id: str, *, call_id=None, lead_id=None) -> str:
        from app.core.db_utils import acquire_with_tenant
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            result = await conn.fetchval(
                """SELECT lead_details_status FROM calls WHERE tenant_id=$1::uuid
                     AND (($2::uuid IS NOT NULL AND id=$2::uuid)
                          OR ($2::uuid IS NULL AND lead_id=$3::uuid))
                     ORDER BY created_at DESC LIMIT 1""",
                str(tenant_id), call_id, lead_id,
            )
        return result or "no_calls"

    async def transcript_save_state(self, tenant_id: str, *, call_id=None, lead_id=None) -> str:
        """Transcript durability is independent of analysis/Lead processing."""
        from app.core.db_utils import acquire_with_tenant
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            result = await conn.fetchval(
                """SELECT transcript_save_state FROM calls WHERE tenant_id=$1::uuid
                     AND (($2::uuid IS NOT NULL AND id=$2::uuid)
                          OR ($2::uuid IS NULL AND lead_id=$3::uuid))
                     ORDER BY created_at DESC LIMIT 1""",
                str(tenant_id), call_id, lead_id,
            )
        return result or "no_calls"

    async def missing_required(
        self, tenant_id: str, call_id: str, campaign_id: Optional[str] = None
    ) -> list[str]:
        """Required fields with no value — what the lead panel highlights.

        Campaign authority comes from the bound call. The optional legacy
        argument is accepted for compatibility, never for authorization.
        Required contact values must be confirmed; generic facts retain their
        existing nonempty-value rule. A null alone does not prove refusal.
        """
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            rows = await conn.fetch(
                """
                SELECT f.field_key, f.field_type
                  FROM calls c JOIN campaign_lead_fields f ON f.campaign_id=c.campaign_id
                 WHERE c.id = $2::uuid AND c.tenant_id=$1::uuid
                   AND f.is_required
                 ORDER BY f.sort_order, f.field_key
                """,
                str(tenant_id), str(call_id),
            )
        if not rows:
            return []
        details = {row["field_key"]: row for row in await self.details_for_call(tenant_id, call_id)}
        return [row["field_key"] for row in rows
                if (saved := details.get(row["field_key"])) is None or saved.get("value") is None
                or (row["field_type"] in {"email", "phone"}
                    and (not saved.get("confirmed") or saved.get("validation_status") != "confirmed"))]
