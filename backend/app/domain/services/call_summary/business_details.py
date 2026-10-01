"""Save supported caller facts from the existing summary, with transcript proof."""
from __future__ import annotations

import hashlib
import json
import re

BUSINESS_FIELDS = frozenset({
    "identified_need", "current_provider", "preferred_channel", "callback_request",
    "next_owner", "next_action", "referral",
})

_CORRECTION = re.compile(
    r"\b(?:actually|correction|i meant|instead|cancel|no longer|never mind|withdraw|"
    r"that(?:'s| is) (?:wrong|incorrect)|do not|don't)\b", re.I,
)


def _json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def summary_snapshot(row) -> list:
    return [row.get("transcript") or "", _json_value(row.get("transcript_json")),
            _json_value(row.get("action_results"))]


def transcript_revision(row) -> str:
    """Include structured turns and action receipts, not just rendered text."""
    return hashlib.sha256(json.dumps(summary_snapshot(row), sort_keys=True, default=str).encode("utf-8")).hexdigest()


def verified_details(details, transcript_json) -> list[dict]:
    """Return reviewable notes, never certify a model's interpretation as fact.

    Matching a substring cannot prove polarity or who a statement refers to.
    Preserve the complete caller turn and flag the model's label for review.
    Explicit later corrections invalidate earlier candidates even if the model
    omitted the replacement; omitting a detail is safer than reviving a denial.
    """
    if isinstance(transcript_json, str):
        try:
            transcript_json = json.loads(transcript_json)
        except ValueError:
            return []
    if isinstance(transcript_json, dict):
        transcript_json = transcript_json.get("turns", [])
    if not isinstance(transcript_json, list) or not isinstance(details, list):
        return []
    callers = [(i, str(t.get("content") or t.get("text") or ""))
               for i, t in enumerate(transcript_json)
               if isinstance(t, dict) and t.get("role") == "user"
               and t.get("is_final") is not False
               and t.get("include_in_plaintext", True)]
    found = {}
    for item in details:
        if not isinstance(item, dict) or item.get("field_key") not in BUSINESS_FIELDS:
            continue
        quote = str(item.get("source_quote") or "").strip()
        value = str(item.get("value") or "").strip()
        if len(quote) < 3 or len(quote) > 4000 or not value:
            continue
        matches = [(i, text) for i, text in callers if quote.casefold() in text.casefold()]
        if not matches:
            continue
        index, context = matches[-1]
        key = item["field_key"]
        if any(i > index and _CORRECTION.search(text) for i, text in callers):
            continue
        # These are notes, not verified scalar attributes: never strip "not",
        # the other person's name, or surrounding context from the user's words.
        value = context.strip()
        status = "needs_review"
        evidence = {"source_quote": context.strip(), "turn_index": index,
                    "status": status, "subject": "referral" if key == "referral" else "unverified",
                    "validation": "caller_quote_only",
                    "extraction": "post_call", "time_resolution": "needs_review" if key == "callback_request" else None}
        if key not in found or found[key]["evidence"]["turn_index"] <= index:
            found[key] = {"field_key": key, "value": value, "evidence": evidence}
    return list(found.values())


async def persist_business_details(pool, tenant_id, call_id, summary, row) -> int:
    from app.domain.services.lead_capture_service import LeadCaptureService
    transcript = row.get("transcript") or ""
    revision = transcript_revision(row)
    service = LeadCaptureService(pool)
    count = 0
    details = verified_details(summary.get("business_details"), row.get("transcript_json"))
    # A corrected/retracted transcript must not leave the previous AI note
    # behind. Keep operator edits and live capture rows untouched.
    from app.core.db_utils import acquire_with_tenant
    async with acquire_with_tenant(pool, tenant_id) as conn:
        await conn.execute(
            """DELETE FROM call_lead_details d USING calls c
                 WHERE d.call_id=$1::uuid AND d.tenant_id=$2::uuid
                   AND c.id=d.call_id AND c.tenant_id=d.tenant_id
                   AND c.summary_transcript_hash=$3
                   AND jsonb_build_array(COALESCE(c.transcript,''), c.transcript_json, c.action_results)=$5::jsonb
                   AND d.source <> 'manual_edit' AND d.evidence->>'extraction'='post_call'
                   AND (d.evidence->>'transcript_revision' IS DISTINCT FROM $3
                        OR NOT (d.field_key = ANY($4::text[])))""",
            call_id, tenant_id, revision, [d["field_key"] for d in details],
            json.dumps(summary_snapshot(row), default=str),
        )
    for detail in details:
        detail["evidence"]["transcript_revision"] = revision
        count += bool(await service.capture(
            tenant_id=tenant_id, call_id=call_id,
            campaign_id=str(row["campaign_id"]) if row.get("campaign_id") else None,
            lead_id=str(row["lead_id"]) if row.get("lead_id") else None,
            source="caller_stated", confirmed=False, field_type="notes",
            expected_transcript=transcript, expected_summary_hash=revision,
            expected_summary_snapshot=summary_snapshot(row), **detail,
        ))
    return count


async def save_summary_details(pool, tenant_id, call_id, summary, row) -> None:
    """A failed write remains visible and retryable even if summary text exists."""
    from app.core.db_utils import acquire_with_tenant
    import logging
    if "business_details" not in summary:
        return  # A historical summary has not extracted these fields yet.
    try:
        await persist_business_details(pool, tenant_id, call_id, summary, row)
        status = "complete"
    except Exception:
        logging.getLogger(__name__).exception("lead_detail_extraction_failed call=%s", call_id)
        status = "failed"
    async with acquire_with_tenant(pool, tenant_id) as conn:
        await conn.execute(
            """UPDATE calls SET lead_details_status=$3
                 WHERE id=$1::uuid AND tenant_id=$2::uuid AND summary_transcript_hash=$4
                   AND jsonb_build_array(COALESCE(transcript,''), transcript_json, action_results)=$5::jsonb""",
            call_id, tenant_id, status, transcript_revision(row),
            json.dumps(summary_snapshot(row), default=str),
        )
