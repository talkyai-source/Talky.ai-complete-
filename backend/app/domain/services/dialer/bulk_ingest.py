"""Shared bulk lead-ingest core for CSV upload and pasted-text import.

Adding contacts in bulk has the same rules no matter how the numbers
arrive — a CSV file or a blob pasted into a textarea:

  * normalize every number to E.164 (one normalizer, so a CSV import and
    a manual add never disagree about what's dialable),
  * drop duplicates *within the batch* and against the campaign's live
    rows,
  * revive a soft-deleted lead in place (keep its id, call history and
    qualified-lead flag) rather than orphaning it with a fresh row,
  * insert the survivors in chunks so a 100k paste can't blow the
    request size or statement limits.

This module owns that core once. ``contacts.py`` (CSV) and the
paste endpoint both build a list of :class:`LeadRecord` and call
:func:`ingest_lead_records`; the only thing that differs between them is
how the raw rows are parsed, which is exactly the part that *should*
differ. :func:`parse_pasted_numbers` is the paste-side parser and is a
pure function so it's trivially testable.

Pure-ish by design: the only side effects are the lead reads/inserts via
the injected Supabase-style ``db_client`` (sync — matches the existing
import path). Normalization is injected as a callable so the caller
controls the per-tenant default country.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from app.domain.services.campaign_direction_guard import (
    OutboundCampaignDirectionConflict,
    raise_for_outbound_direction_guard,
)

logger = logging.getLogger(__name__)

# Delimiters between pasted numbers. Deliberately NOT space — a number
# like "+1 415 555 1234" must survive as one token — so we split only on
# line breaks and the common list separators.
_PASTE_SPLIT = re.compile(r"[\n\r,|\t]+|;(?!ext=)", re.IGNORECASE)

# Default insert chunk size (mirrors the CSV path).
DEFAULT_CHUNK_SIZE = 500

# The one cap on row errors every import answer returns (it protects the
# response size). The answer also carries the true total and a truncated flag
# so a partial list is never mistaken for the whole list.
IMPORT_ERRORS_CAP = 100
# Cells dropped on rows that still imported; kept at its older, larger cap.
IMPORT_FIELD_ERRORS_CAP = 200
# How many row errors one import writes to the server log.
IMPORT_LOG_SAMPLE = 5

# Customer-facing row errors: one plain sentence each, keyed by a stable code.
ROW_ERROR_TEXT = {
    "phone_missing": "This row has no phone number.",
    "phone_invalid": (
        "This phone number is not valid for the country chosen. "
        "Use the international format, for example +442071234567."
    ),
    "phone_country_required": (
        "This phone number has no country code. Add the country code "
        "(for example +44) or choose a country for the import."
    ),
    "save_failed": "This contact could not be saved. Please try again.",
    "update_failed": "This contact already existed and could not be updated. Please try again.",
    "row_too_many_cells": (
        "This row has more cells than the sheet has headings. "
        "Check for a comma that is not inside quotes."
    ),
}


def mask_phone(phone) -> str:
    """Last four digits only, for logs."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return f"***{digits[-4:]}" if digits else "-"


_DIGIT_RUN = re.compile(r"\+?\d[\d\s().-]{5,}\d")


def redact_digits(text) -> str:
    """Mask every phone-like digit run in free text before it is logged."""
    return _DIGIT_RUN.sub(lambda m: mask_phone(m.group(0)), str(text or ""))


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Postgres puts the offending row's values in its message: "Key (email)=(a@b.c)".
_KEY_VALUES = re.compile(r"\)=\([^)]*\)")


def redact_log_detail(text) -> str:
    """Exception text made safe for a log line: no contact values.

    A database error can quote the row it refused (an email, a name, a number).
    The column names stay; every quoted value, email address and phone-like
    digit run is masked.
    """
    cleaned = _KEY_VALUES.sub(")=(...)", str(text or ""))
    cleaned = _EMAIL.sub("[email]", cleaned)
    return redact_digits(cleaned)


def phone_failure_code(raw: str, country_known: Optional[bool] = None,
                       exc_text: str = "") -> str:
    """Which plain sentence a refused dial number gets.

    Either the country is known (``country_known``) or, inside the shared
    ingest, the normaliser's own message says it was missing.
    """
    if not (raw or "").strip():
        return "phone_missing"
    if country_known is False and not raw.strip().startswith("+"):
        return "phone_country_required"
    if country_known is None and exc_text.startswith("Country or region is required"):
        return "phone_country_required"
    return "phone_invalid"


def cap_errors(items: list, cap: int = IMPORT_ERRORS_CAP) -> tuple[list, int, bool]:
    """(list to return, true total, truncated)."""
    return items[:cap], len(items), len(items) > cap


def log_import_row_errors(source: str, campaign_id, entries: list) -> None:
    """One WARNING per import: a count and the first few, phones masked.

    ``entries`` are (row, code, detail, phone) tuples. Nothing here is per row
    and nothing is INFO.
    """
    if not entries:
        return
    sample = "; ".join(
        f"row={row} code={code} phone={mask_phone(phone)} detail={redact_log_detail(detail)[:200]}"
        for row, code, detail, phone in entries[:IMPORT_LOG_SAMPLE]
    )
    logger.warning(
        "%s import for campaign %s: %d row errors; first %d: %s",
        source, campaign_id, len(entries), min(len(entries), IMPORT_LOG_SAMPLE), sample,
    )


@dataclass
class LeadRecord:
    """One inbound contact before normalization/dedup. ``source_row`` is
    carried only for error reporting back to the user."""
    phone_raw: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    # Optional company/organization for the contact. Stored in the lead's
    # custom_fields JSONB under the canonical "company" key (no schema
    # migration — the leads table already has custom_fields), and later
    # threaded into the outbound agent's "who you're calling" prompt block.
    company: Optional[str] = None
    # Expanded contact fields (goals.md §11). ``phone_raw`` remains the
    # canonical dial number; mobile/business are additional numbers and never
    # silently replace it.
    mobile_number: Optional[str] = None
    business_number: Optional[str] = None
    company_name: Optional[str] = None
    job_title: Optional[str] = None
    best_time_to_call: Optional[str] = None
    timezone: Optional[str] = None
    calling_notes: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    do_not_call: Optional[bool] = None
    custom_fields: dict = field(default_factory=dict)
    source_row: Optional[int] = None


@dataclass
class IngestError:
    row: Optional[int]
    error: str
    phone: Optional[str]
    code: str = "save_failed"
    # Internal cause for the server log only; never sent to the customer.
    detail: str = ""


@dataclass
class IngestResult:
    total: int = 0
    imported: int = 0
    revived: int = 0
    duplicates_skipped: int = 0
    invalid: int = 0
    # Existing live contacts that had no name and were given one by this file.
    names_added: int = 0
    errors: list = field(default_factory=list)


def _custom_fields_with_company(rec: "LeadRecord") -> dict:
    """Return the record's custom_fields with the contact's company folded in
    under the canonical ``company`` key.

    Kept out of the insert/revive dicts inline so CSV import and paste import
    (and any future entry point) store company the exact same way. A blank
    company leaves custom_fields untouched, so a company-less import is
    byte-identical to before this change. An explicit ``company`` already in
    custom_fields (e.g. a stray "Company" header that fell through to the
    catch-all) is overridden by the parsed value so the key stays canonical.
    """
    fields = dict(rec.custom_fields or {})
    company = (rec.company or "").strip()
    if company:
        fields["company"] = company
    mobile = (rec.mobile_number or "").strip()
    if mobile:
        fields["mobile_number"] = mobile
    return fields


def _expanded_lead_columns(rec: "LeadRecord") -> dict:
    """Database-backed expanded fields, omitting absent values.

    Omission matters for backward compatibility: a legacy CSV with four
    columns still produces the same insert payload aside from fields it
    actually supplied, while a richer sample sheet persists every value.
    """
    values = {
        "business_number": rec.business_number,
        "company_name": rec.company_name or rec.company,
        "job_title": rec.job_title,
        "best_time_to_call": rec.best_time_to_call,
        "timezone": rec.timezone,
        "calling_notes": rec.calling_notes,
        "preferred_contact_method": rec.preferred_contact_method,
        "do_not_call": rec.do_not_call,
    }
    return {key: value for key, value in values.items() if value is not None}


def parse_pasted_numbers(text: str) -> list[str]:
    """Extract raw phone tokens from a pasted blob.

    Splits on line breaks, commas, semicolons, pipes and tabs (not
    spaces, so intra-number spacing survives), trims each token, and
    drops empties. Normalization/validation happens later in
    :func:`ingest_lead_records` — this only segments the text.
    """
    if not text:
        return []
    return [tok.strip() for tok in _PASTE_SPLIT.split(text) if tok and tok.strip()]


def _load_existing(
    db_client, campaign_id: str, tenant_id: Optional[str]
) -> tuple[set, dict, dict]:
    """Return (live_phones, deleted_lead_by_phone, nameless_live_by_phone).

    ``nameless_live_by_phone`` maps a live contact's phone to its id when both
    its first and last name are NULL, so a re-import can fill the name in.

    Always loaded so we never create a second live row for a phone and so
    a phone matching a soft-deleted row is revived in place.
    """
    live_phones: set[str] = set()
    deleted_by_phone: dict[str, str] = {}
    nameless_live: dict[str, str] = {}
    resp = (
        db_client.table("leads")
        .select("id, phone_number, status, is_lead, first_name, last_name")
        .eq("campaign_id", campaign_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if getattr(resp, "error", None):
        raise RuntimeError("existing lead lookup failed")
    for row in (getattr(resp, "data", None) or []):
        if row.get("status") == "deleted":
            prev = deleted_by_phone.get(row["phone_number"])
            if prev is None or row.get("is_lead"):
                deleted_by_phone[row["phone_number"]] = row["id"]
        else:
            live_phones.add(row["phone_number"])
            if row.get("first_name") is None and row.get("last_name") is None:
                nameless_live[row["phone_number"]] = row["id"]
    return live_phones, deleted_by_phone, nameless_live


def _fill_missing_name(db_client, *, lead_id: str, campaign_id: str,
                       tenant_id: Optional[str], rec: "LeadRecord") -> bool:
    """Give an existing, nameless live contact the name from a re-imported row.

    ONE statement that touches only first_name and last_name. The tenant,
    campaign and contact id are in its predicates, and both names must still be
    NULL, so a name someone typed in the meantime is never overwritten.
    """
    try:
        resp = (
            db_client.table("leads")
            .update({"first_name": rec.first_name, "last_name": rec.last_name})
            .eq("id", lead_id)
            .eq("campaign_id", campaign_id)
            .eq("tenant_id", tenant_id)
            .is_("first_name", None)
            .is_("last_name", None)
            .execute()
        )
        if getattr(resp, "error", None):
            raise RuntimeError(str(resp.error))
        return bool(getattr(resp, "data", None))
    except Exception as e:  # noqa: BLE001 - a name is a bonus; never fail the import
        logger.error("bulk_ingest name fill %s failed: %s", lead_id, redact_log_detail(e))
        return False


def ingest_lead_records(
    db_client,
    *,
    campaign_id: str,
    tenant_id: Optional[str],
    records: list[LeadRecord],
    normalize: Callable[[str], str],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    list_id: Optional[str] = None,
) -> IngestResult:
    """Normalize, dedup, revive and chunk-insert a batch of contacts.

    ``normalize`` must return an E.164 string or raise ``ValueError`` for
    an unusable number. Mirrors the CSV importer's semantics exactly so
    the two entry points behave identically.

    ``list_id`` (optional) tags every inserted/revived lead with the
    contact-list it belongs to. When None (legacy callers) the lead stays
    Ungrouped (list_id NULL), preserving the pre-list behaviour exactly.
    A revived soft-deleted lead is re-tagged to the new list so a
    re-upload moves it into the fresh list rather than stranding it in an
    old one.
    """
    result = IngestResult(total=len(records))
    live_phones, deleted_by_phone, nameless_live = _load_existing(
        db_client, campaign_id, tenant_id
    )

    seen: set[str] = set()
    to_insert: list[dict] = []
    to_revive: list[dict] = []

    for rec in records:
        raw = (rec.phone_raw or "").strip()
        if not raw:
            result.invalid += 1
            result.errors.append(IngestError(
                rec.source_row, ROW_ERROR_TEXT["phone_missing"], None, "phone_missing",
                "no phone number",
            ))
            continue
        try:
            phone = normalize(raw)
        except ValueError as e:
            result.invalid += 1
            code = phone_failure_code(raw, None, str(e))
            result.errors.append(IngestError(
                rec.source_row, ROW_ERROR_TEXT[code], raw, code, str(e),
            ))
            continue

        if phone in seen or phone in live_phones:
            result.duplicates_skipped += 1
            # A re-import fills a missing name (first row with a name wins).
            lead_id = nameless_live.get(phone)
            if lead_id and (rec.first_name or rec.last_name):
                del nameless_live[phone]
                if _fill_missing_name(db_client, lead_id=lead_id,
                                      campaign_id=campaign_id,
                                      tenant_id=tenant_id, rec=rec):
                    result.names_added += 1
            continue
        seen.add(phone)

        revive_id = deleted_by_phone.pop(phone, None)
        if revive_id is not None:
            to_revive.append({
                "id": revive_id,
                "first_name": rec.first_name,
                "last_name": rec.last_name,
                "email": rec.email,
                "custom_fields": _custom_fields_with_company(rec),
                **_expanded_lead_columns(rec),
            })
            live_phones.add(phone)
            continue

        lead_row = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "campaign_id": campaign_id,
            "phone_number": phone,
            "first_name": rec.first_name,
            "last_name": rec.last_name,
            "email": rec.email,
            "custom_fields": _custom_fields_with_company(rec),
            **_expanded_lead_columns(rec),
            "status": "pending",
            "last_call_result": "pending",
            "call_attempts": 0,
            "created_at": datetime.utcnow().isoformat(),
        }
        # Only stamp list_id when we actually have one, so pre-list callers
        # produce byte-identical insert rows (and never touch the column on a
        # DB where the migration hasn't run yet).
        if list_id is not None:
            lead_row["list_id"] = list_id
        to_insert.append(lead_row)
        live_phones.add(phone)

    # Chunked insert — a single bad chunk is reported but doesn't sink the rest.
    for i in range(0, len(to_insert), chunk_size):
        chunk = to_insert[i:i + chunk_size]
        try:
            write_response = db_client.table("leads").insert(chunk).execute()
            if getattr(write_response, "error", None):
                raise_for_outbound_direction_guard(write_response.error, campaign_id)
                raise RuntimeError(str(write_response.error))
            if not getattr(write_response, "data", None):
                raise RuntimeError("database insert affected no rows")
            result.imported += len(chunk)
        except OutboundCampaignDirectionConflict:
            raise
        except Exception as e:
            logger.error("bulk_ingest insert chunk %d-%d failed: %s", i, i + len(chunk),
                         redact_log_detail(e))
            for lead in chunk:
                result.errors.append(IngestError(
                    None, ROW_ERROR_TEXT["save_failed"], lead.get("phone_number"),
                    "save_failed", str(e),
                ))

    # Revive soft-deleted matches in place.
    for rev in to_revive:
        try:
            revive_payload = {
                "status": "pending",
                "first_name": rev["first_name"],
                "last_name": rev["last_name"],
                "email": rev["email"],
                "custom_fields": rev["custom_fields"],
                **{
                    key: value
                    for key, value in rev.items()
                    if key not in {"id", "first_name", "last_name", "email", "custom_fields"}
                },
            }
            # Move a revived lead into the list it was just re-uploaded under.
            if list_id is not None:
                revive_payload["list_id"] = list_id
            write_response = (
                db_client.table("leads")
                .update(revive_payload)
                .eq("id", rev["id"])
                .eq("campaign_id", campaign_id)
                .eq("tenant_id", tenant_id)
                .execute()
            )
            if getattr(write_response, "error", None):
                raise_for_outbound_direction_guard(write_response.error, campaign_id)
                raise RuntimeError(str(write_response.error))
            if not getattr(write_response, "data", None):
                raise RuntimeError("database revive affected no rows")
            result.revived += 1
            result.imported += 1
        except OutboundCampaignDirectionConflict:
            raise
        except Exception as e:
            logger.error("bulk_ingest revive %s failed: %s", rev["id"], redact_log_detail(e))
            result.errors.append(IngestError(
                None, ROW_ERROR_TEXT["update_failed"], None, "update_failed", str(e),
            ))

    logger.info(
        "bulk_ingest campaign=%s total=%d imported=%d revived=%d dup=%d invalid=%d",
        campaign_id, result.total, result.imported, result.revived,
        result.duplicates_skipped, result.invalid,
    )
    return result
