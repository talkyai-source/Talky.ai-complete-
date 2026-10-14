"""Save supported caller facts from the existing summary, with transcript proof.

What is saved (2026-10-14, owner: "make sure agent is able to save all the
information"):

* ``value`` is a short, clean rewording of what the caller said, so a lead
  record reads "WhatsApp", not the whole sentence it came from. The caller's
  complete turn stays in ``evidence.source_quote``.
* Every note is ``needs_review``: a model's interpretation is never certified
  as fact. If the rewording could invert the caller's meaning (the turn has a
  negation the rewording dropped) or adds figures the caller never said, the
  caller's own turn is kept as the value instead.
* The campaign's own NON-contact fields (job title, best time to call, notes,
  selects, ...) are saved the same way. Contact details and identity are never
  written here: they need the caller's confirmation through the live
  ``record_contact`` tool.
* A row this path does not own (manual edit, confirmed, live capture) is never
  replaced; this path's own rows are replaced per transcript revision.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Mapping, Optional

from app.domain.services.lead_capture_service import MAX_VALUE_CHARS

logger = logging.getLogger(__name__)

BUSINESS_FIELDS = frozenset({
    "identified_need", "current_provider", "preferred_channel", "callback_request",
    "next_owner", "next_action", "referral", "timeline",
})

_CORRECTION = re.compile(
    r"\b(?:actually|correction|i meant|instead|cancel|no longer|never mind|withdraw|"
    r"that(?:'s| is) (?:wrong|incorrect)|do not|don't)\b", re.I,
)

# A "concise" value is a short rewording. A longer one is rejected, never cut:
# a truncated sentence can lose its "not".
MAX_CONCISE_CHARS = 200
MAX_CAMPAIGN_FIELDS = 30
MAX_FIELD_OPTIONS = 30
MAX_LABEL_CHARS = 80
MAX_OPTION_CHARS = 80

_EXTRACTABLE_TYPES = frozenset({"text", "notes", "number", "datetime", "single_select", "multi_select"})
_SELECT_TYPES = frozenset({"single_select", "multi_select"})
# Written only by the live record_contact tool (caller confirmation), or system
# notes: the post-call model may not write them whatever type the campaign gave.
_LIVE_ONLY_KEYS = frozenset({
    "first_name", "last_name", "full_name", "name", "company_name",
    "email", "phone", "phone_number", "mobile_number", "business_number",
    "contact_followup",
})
_CONTACT_KEY = re.compile(r"(?:email|phone)(?:_\d+)?")
_FIELD_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_TIME_FIELDS = frozenset({"callback_request", "timeline"})

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Whole words (so "know", "number", "notes" are not negations) and n't contractions.
_NEGATION = re.compile(
    r"\b(?:not|no|never|none|nothing|nobody|neither|nor|cannot|nahi|nahin|nicht|nein)\b"
    r"|\b[a-z]*n['’]t\b", re.I,
)
_FIGURE = re.compile(r"\d+(?:[.,:/-]\d+)*")


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
    return hashlib.sha256(json.dumps({"canonical_version": 2, "snapshot": summary_snapshot(row)}, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# ── campaign fields ─────────────────────────────────────────────────────────

def _clean(value) -> str:
    """Single-spaced text with no control characters."""
    return " ".join(_CONTROL.sub(" ", "" if value is None else str(value)).split())


def _options(value) -> list[str]:
    """The listed options of a select field (JSONB may arrive as text)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, (list, tuple)):
        return []
    options: list[str] = []
    for item in value:
        text = _clean(item) if isinstance(item, str) else ""
        if text and len(text) <= MAX_OPTION_CHARS and text.casefold() not in {o.casefold() for o in options}:
            options.append(text)
        if len(options) >= MAX_FIELD_OPTIONS:
            break
    return options


def extractable_campaign_fields(fields) -> list[dict]:
    """The campaign's own fields post-call extraction may fill, normalised.

    Input is ``LeadCaptureService.fields_for_campaign`` rows. Dropped: email and
    phone types, identity and contact keys (first_name, last_name, full_name,
    company_name, email, phone, *_number of the live contact family, ...), the
    reserved ``contact_followup`` note, and select fields without usable
    options. Idempotent: its own output can be passed back in.
    """
    usable: list[dict] = []
    seen: set[str] = set()
    for raw in fields or []:
        if not isinstance(raw, Mapping):
            continue
        key = str(raw.get("field_key") or "").strip()
        field_type = str(raw.get("field_type") or "text").strip().lower()
        folded = key.casefold()
        if (not _FIELD_KEY.fullmatch(key) or folded in seen
                or field_type not in _EXTRACTABLE_TYPES
                or folded in _LIVE_ONLY_KEYS or _CONTACT_KEY.fullmatch(folded)):
            continue
        options = _options(raw.get("options")) if field_type in _SELECT_TYPES else []
        if field_type in _SELECT_TYPES and not options:
            continue
        seen.add(folded)
        spec = {"field_key": key, "label": _clean(raw.get("label"))[:MAX_LABEL_CHARS] or key,
                "field_type": field_type}
        if options:
            spec["options"] = options
        usable.append(spec)
        if len(usable) >= MAX_CAMPAIGN_FIELDS:
            break
    return usable


async def load_campaign_extract_fields(pool, tenant_id, campaign_id) -> list[dict]:
    """Extractable fields of this call's campaign; ``[]`` when it has none.

    A database error propagates on purpose: the caller must retry later rather
    than silently skip fields it was supposed to capture.
    """
    if not campaign_id:
        return []
    from app.domain.services.lead_capture_service import LeadCaptureService
    return extractable_campaign_fields(
        await LeadCaptureService(pool).fields_for_campaign(str(tenant_id), str(campaign_id)))


# ── values ──────────────────────────────────────────────────────────────────

def _figures(text: str) -> set[str]:
    return {re.sub(r"\D", "", match) for match in _FIGURE.findall(text)}


def _polarity_ok(turn: str, value: str) -> bool:
    """False when the caller's turn has a negation that the value dropped."""
    return not (_NEGATION.search(turn) and not _NEGATION.search(value))


def _bounded(text: str) -> str:
    return text if len(text) <= MAX_VALUE_CHARS else text[: MAX_VALUE_CHARS - 1].rstrip() + "…"


def _free_text(model_value, turn: str) -> tuple[str, str]:
    """(value, basis): the model's short value when it cannot mislead, else the caller's turn."""
    said = _clean(turn)
    short = _clean(model_value).strip('"“” ')
    if (short and len(short) <= MAX_CONCISE_CHARS and _polarity_ok(said, short)
            and _figures(short) <= _figures(said)):
        return short, "model_summary"
    return _bounded(said), "caller_turn"


def _fold(text) -> str:
    return _clean(text).strip(" .\"'“”").casefold()


def _listed(model_value, options, *, multi: bool):
    """The listed option(s) the model named, canonically spelled; None if none is listed."""
    canonical = {_fold(option): option for option in options}
    text = _clean(model_value)
    whole = canonical.get(_fold(text))
    if whole:
        return [whole] if multi else whole
    if not multi:
        return None
    picked: list[str] = []
    for part in re.split(r"[,;|]", text):
        option = canonical.get(_fold(part))
        if option and option not in picked:
            picked.append(option)
    return picked or None


def _number(model_value) -> Optional[str]:
    text = re.sub(r"[£$€,\s]", "", _clean(model_value))
    return text if re.fullmatch(r"-?\d+(?:\.\d+)?", text) else None


def _is_iso_datetime(text: str) -> bool:
    try:
        datetime.fromisoformat(text.strip())
    except ValueError:
        return False
    return True


def _field_value(field_type: str, options, model_value, turn: str):
    """(value, stored field_type, basis) for one detail; None = do not save.

    Free text falls back to the caller's own turn when the model's value could
    mislead. A select or number has no such fallback (the turn is not an
    option or a number), so what cannot be checked is dropped.
    """
    said = _clean(turn)
    if field_type in _SELECT_TYPES:
        picked = _listed(model_value, options, multi=field_type == "multi_select")
        shown = ", ".join(picked) if isinstance(picked, list) else picked
        if not picked or not _polarity_ok(said, shown):
            return None
        return picked, field_type, "listed_option"
    if field_type == "number":
        number = _number(model_value)
        if number is None or not _figures(number) <= _figures(said) or not _polarity_ok(said, number):
            return None
        return number, "number", "model_summary"
    value, basis = _free_text(model_value, turn)
    # A caller's "next Thursday" is not a datetime; never present it as one.
    stored = "notes" if field_type == "datetime" and not _is_iso_datetime(value) else field_type
    return value, stored, basis


def verified_details(details, transcript_json, *, campaign_fields=None) -> list[dict]:
    """Return reviewable notes, never certify a model's interpretation as fact.

    Matching a substring cannot prove polarity or who a statement refers to.
    The caller's complete turn is kept as evidence and the model's label stays
    flagged for review. The stored ``value`` is the model's short rewording
    only when it provably cannot mislead (see ``_field_value``); otherwise it is
    the caller's own turn. Explicit later corrections invalidate earlier
    candidates even if the model omitted the replacement; omitting a detail is
    safer than reviving a denial.

    ``campaign_fields`` are the campaign's own field definitions; only their
    extractable (non-contact) members are accepted next to ``BUSINESS_FIELDS``.
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
    from app.domain.services.transcript_service import conversation_turns
    transcript_json = conversation_turns(transcript_json)
    callers = [(i, str(t.get("content") or ""))
               for i, t in enumerate(transcript_json)
               if isinstance(t, dict) and t.get("role") == "user"
               and t.get("is_final") is not False
               and t.get("include_in_plaintext", True)]
    specs = {spec["field_key"]: spec for spec in extractable_campaign_fields(campaign_fields)}
    found = {}
    for item in details:
        key = item.get("field_key") if isinstance(item, dict) else None
        if not isinstance(key, str) or (key not in BUSINESS_FIELDS and key not in specs):
            continue
        quote = str(item.get("source_quote") or "").strip()
        value = str(item.get("value") or "").strip()
        if len(quote) < 3 or len(quote) > 4000 or not value:
            continue
        matches = [(i, text) for i, text in callers if quote.casefold() in text.casefold()]
        if not matches:
            continue
        index, context = matches[-1]
        if any(i > index and _CORRECTION.search(text) for i, text in callers):
            continue
        spec = specs.get(key)
        resolved = _field_value(spec["field_type"] if spec else "notes",
                                spec.get("options", []) if spec else [], value, context)
        if resolved is None:
            continue
        value, stored_type, basis = resolved
        # These are notes, not verified scalar attributes: the caller's whole
        # turn stays as evidence, and a rewording that could drop "not", the
        # other person's name or any context was replaced by the turn itself.
        status = "needs_review"
        evidence = {"source_quote": context.strip(), "turn_index": index,
                    "status": status, "subject": "referral" if key == "referral" else "unverified",
                    "validation": "caller_quote_only", "value_basis": basis,
                    "extraction": "post_call",
                    "time_resolution": "needs_review" if key in _TIME_FIELDS or (spec and spec["field_type"] == "datetime") else None}
        if key not in found or found[key]["evidence"]["turn_index"] <= index:
            found[key] = {"field_key": key, "value": value, "field_type": stored_type, "evidence": evidence}
    return list(found.values())


async def persist_business_details(pool, tenant_id, call_id, summary, row) -> int:
    from app.domain.services.lead_capture_service import InvalidCaptureError, LeadCaptureService
    transcript = row.get("transcript") or ""
    revision = transcript_revision(row)
    service = LeadCaptureService(pool)
    count = 0
    campaign_fields = await load_campaign_extract_fields(pool, tenant_id, row.get("campaign_id"))
    details = verified_details(summary.get("business_details"), row.get("transcript_json"),
                               campaign_fields=campaign_fields)
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
        # Only this path's own unconfirmed post-call rows are ours to replace.
        # A manual edit, a confirmed value or a live-captured row for the same
        # field stays as it is. (capture() also refuses to replace a manual or
        # confirmed row atomically; this read makes the rule explicit for every
        # other row and keeps the skip visible in the log.)
        protected = set()
        if details:
            protected = {r["field_key"] for r in await conn.fetch(
                """SELECT field_key FROM call_lead_details
                    WHERE call_id=$1::uuid AND tenant_id=$2::uuid AND field_key = ANY($3::text[])
                      AND (source = 'manual_edit' OR COALESCE(confirmed, FALSE)
                           OR evidence->>'extraction' IS DISTINCT FROM 'post_call')""",
                call_id, tenant_id, [d["field_key"] for d in details])}
    for detail in details:
        if detail["field_key"] in protected:
            logger.info("lead_detail_kept call=%s field=%s - a manual, confirmed or live value is already saved",
                        str(call_id)[:8], detail["field_key"])
            continue
        detail["evidence"]["transcript_revision"] = revision
        try:
            count += bool(await service.capture(
                tenant_id=tenant_id, call_id=call_id,
                campaign_id=str(row["campaign_id"]) if row.get("campaign_id") else None,
                lead_id=str(row["lead_id"]) if row.get("lead_id") else None,
                source="caller_stated", confirmed=False,
                expected_transcript=transcript, expected_summary_hash=revision,
                expected_summary_snapshot=summary_snapshot(row), **detail,
            ))
        except InvalidCaptureError as exc:
            # One refused value must not lose the others (capture_many's rule).
            logger.warning("lead_detail_rejected call=%s field=%s - %s",
                           str(call_id)[:8], detail["field_key"], exc)
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
