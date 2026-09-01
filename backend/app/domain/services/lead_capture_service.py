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
    confirmed  whether the caller heard it read back and agreed

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
    row, NULL value asked, and the caller declined

Those want different follow-up, and collapsing them loses the distinction
permanently.
"""
from __future__ import annotations

import json
import logging
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

        stored = normalise_value(value, field_type)

        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO call_lead_details
                    (tenant_id, call_id, campaign_id, lead_id, field_key,
                     field_type, value, source, confirmed, is_required)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (call_id, field_key) DO UPDATE
                   SET value      = EXCLUDED.value,
                       source     = EXCLUDED.source,
                       -- confirmed is sticky: once the caller has agreed a
                       -- value, a later unconfirmed write of the SAME value
                       -- must not quietly downgrade it to unconfirmed.
                       confirmed  = call_lead_details.confirmed OR EXCLUDED.confirmed,
                       field_type = EXCLUDED.field_type,
                       updated_at = NOW()
                 -- BOTH ranks are computed HERE, against the row actually
                 -- present at write time. Comparing against a rank read a
                 -- moment earlier in Python would lose the race this clause
                 -- exists to win.
                 WHERE array_position($11::text[], EXCLUDED.source)
                    >= array_position($11::text[], call_lead_details.source)
                   -- A confirmed value is never replaced by an unconfirmed one,
                   -- whatever the source rank. Without this a same-source retry
                   -- passed the rank test, overwrote value, and the sticky OR
                   -- above kept confirmed=TRUE on a value nobody agreed.
                   AND NOT (call_lead_details.confirmed AND NOT EXCLUDED.confirmed)
                   -- Prod's app role is superuser + BYPASSRLS, so the table's
                   -- policy is inert and an ON CONFLICT that matched a row
                   -- would happily update it across tenants. Name the tenant.
                   AND call_lead_details.tenant_id = $1::uuid
                RETURNING id
                """,
                str(tenant_id), str(call_id),
                str(campaign_id) if campaign_id else None,
                str(lead_id) if lead_id else None,
                key, field_type, stored, source, bool(confirmed), bool(is_required),
                list(TRUST_ORDER),
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
                SELECT field_key, field_type, value, source, confirmed,
                       is_required, updated_at
                  FROM call_lead_details
                 WHERE call_id = $1::uuid
                 ORDER BY is_required DESC, field_key
                """,
                str(call_id),
            )
        return [dict(r) for r in rows]

    async def missing_required(
        self, tenant_id: str, call_id: str, campaign_id: str
    ) -> list[str]:
        """Required fields with no value — what the lead panel highlights.

        A row whose value is NULL counts as missing for this purpose: the
        caller was asked and declined, so the information is still absent even
        though the question was put.
        """
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            rows = await conn.fetch(
                """
                SELECT f.field_key
                  FROM campaign_lead_fields f
             LEFT JOIN call_lead_details d
                    ON d.call_id = $2::uuid AND d.field_key = f.field_key
                 WHERE f.campaign_id = $1::uuid
                   AND f.is_required
                   AND (d.id IS NULL OR d.value IS NULL)
                 ORDER BY f.sort_order, f.field_key
                """,
                str(campaign_id), str(call_id),
            )
        return [r["field_key"] for r in rows]
