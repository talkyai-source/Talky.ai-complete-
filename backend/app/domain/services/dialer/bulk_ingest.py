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
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Delimiters between pasted numbers. Deliberately NOT space — a number
# like "+1 415 555 1234" must survive as one token — so we split only on
# line breaks and the common list separators.
_PASTE_SPLIT = re.compile(r"[\n\r;,|\t]+")

# Default insert chunk size (mirrors the CSV path).
DEFAULT_CHUNK_SIZE = 500


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
    custom_fields: dict = field(default_factory=dict)
    source_row: Optional[int] = None


@dataclass
class IngestError:
    row: Optional[int]
    error: str
    phone: Optional[str]


@dataclass
class IngestResult:
    total: int = 0
    imported: int = 0
    revived: int = 0
    duplicates_skipped: int = 0
    invalid: int = 0
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
    return fields


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


def _load_existing(db_client, campaign_id: str) -> tuple[set, dict]:
    """Return (live_phones, deleted_lead_by_phone) for the campaign.

    Always loaded so we never create a second live row for a phone and so
    a phone matching a soft-deleted row is revived in place.
    """
    live_phones: set[str] = set()
    deleted_by_phone: dict[str, str] = {}
    resp = (
        db_client.table("leads")
        .select("id, phone_number, status, is_lead")
        .eq("campaign_id", campaign_id)
        .execute()
    )
    for row in (getattr(resp, "data", None) or []):
        if row.get("status") == "deleted":
            prev = deleted_by_phone.get(row["phone_number"])
            if prev is None or row.get("is_lead"):
                deleted_by_phone[row["phone_number"]] = row["id"]
        else:
            live_phones.add(row["phone_number"])
    return live_phones, deleted_by_phone


def ingest_lead_records(
    db_client,
    *,
    campaign_id: str,
    tenant_id: Optional[str],
    records: list[LeadRecord],
    normalize: Callable[[str], str],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> IngestResult:
    """Normalize, dedup, revive and chunk-insert a batch of contacts.

    ``normalize`` must return an E.164 string or raise ``ValueError`` for
    an unusable number. Mirrors the CSV importer's semantics exactly so
    the two entry points behave identically.
    """
    result = IngestResult(total=len(records))
    live_phones, deleted_by_phone = _load_existing(db_client, campaign_id)

    seen: set[str] = set()
    to_insert: list[dict] = []
    to_revive: list[dict] = []

    for rec in records:
        raw = (rec.phone_raw or "").strip()
        if not raw:
            result.invalid += 1
            result.errors.append(IngestError(rec.source_row, "Missing phone_number", None))
            continue
        try:
            phone = normalize(raw)
        except ValueError as e:
            result.invalid += 1
            result.errors.append(IngestError(rec.source_row, str(e), raw))
            continue

        if phone in seen or phone in live_phones:
            result.duplicates_skipped += 1
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
            })
            live_phones.add(phone)
            continue

        to_insert.append({
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "campaign_id": campaign_id,
            "phone_number": phone,
            "first_name": rec.first_name,
            "last_name": rec.last_name,
            "email": rec.email,
            "custom_fields": _custom_fields_with_company(rec),
            "status": "pending",
            "last_call_result": "pending",
            "call_attempts": 0,
            "created_at": datetime.utcnow().isoformat(),
        })
        live_phones.add(phone)

    # Chunked insert — a single bad chunk is reported but doesn't sink the rest.
    for i in range(0, len(to_insert), chunk_size):
        chunk = to_insert[i:i + chunk_size]
        try:
            db_client.table("leads").insert(chunk).execute()
            result.imported += len(chunk)
        except Exception as e:
            logger.error("bulk_ingest insert chunk %d-%d failed: %s", i, i + len(chunk), e)
            for lead in chunk:
                result.errors.append(
                    IngestError(None, f"Database insert failed: {e}", lead.get("phone_number"))
                )

    # Revive soft-deleted matches in place.
    for rev in to_revive:
        try:
            db_client.table("leads").update({
                "status": "pending",
                "first_name": rev["first_name"],
                "last_name": rev["last_name"],
                "email": rev["email"],
                "custom_fields": rev["custom_fields"],
            }).eq("id", rev["id"]).execute()
            result.revived += 1
            result.imported += 1
        except Exception as e:
            logger.error("bulk_ingest revive %s failed: %s", rev["id"], e)
            result.errors.append(IngestError(None, f"Revive failed: {e}", None))

    logger.info(
        "bulk_ingest campaign=%s total=%d imported=%d revived=%d dup=%d invalid=%d",
        campaign_id, result.total, result.imported, result.revived,
        result.duplicates_skipped, result.invalid,
    )
    return result
