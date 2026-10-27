"""Strict retained Deepgram billing export parsing; never a net-spend model.

Source contract: https://developers.deepgram.com/reference/manage/billing/breakdown/get
Uploaded bytes and project/query context are claims until an operator reviews
the original account source. No network, price lookup, currency conversion or
invoice/credit assumptions occur here.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SOURCE_BYTES = 1048576
SOURCE_KIND = "deepgram_billing_breakdown_v1"
SOURCE_DOC = "https://developers.deepgram.com/reference/manage/billing/breakdown/get"


def money_text(value):
    return None if value is None else format(Decimal(str(value)), "f")


def _storage_text(value):
    # JSON permits escaped lone surrogates/NUL; PostgreSQL UTF-8/jsonb does not.
    if "\x00" in value:
        raise ValueError("Source text cannot contain NUL")
    value.encode("utf-8", errors="strict")
    return value


class ImportEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    account_id: UUID
    reference: str = Field(min_length=1, max_length=120)
    source_content: str = Field(min_length=1, max_length=MAX_SOURCE_BYTES)
    # This first parser supports ONLY the unfiltered project export. It does
    # not infer query context from a response lacking those request fields.
    unfiltered_project_export: bool
    supersedes_id: UUID | None = None

    @field_validator("reference")
    @classmethod
    def reference_text(cls, value):
        if value != value.strip() or re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("Use a nonempty reference without control characters")
        return _storage_text(value)


class ReviewEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stage: str = Field(pattern=r"^(reviewed|reconciled)$")
    review_reference: str = Field(min_length=1, max_length=120)
    notes: str = Field(min_length=10, max_length=2000)
    source_account_and_period_checked: bool
    accept_unallocated_difference: bool = False

    @field_validator("review_reference", "notes")
    @classmethod
    def review_text(cls, value):
        if value != value.strip() or re.search(r"[\x00-\x08\x0b-\x1f\x7f]", value):
            raise ValueError("Use a nonempty review without control characters")
        return _storage_text(value)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate source keys are not accepted")
        result[key] = value
    return result


def _bad_number(_value):
    raise ValueError("Nonfinite source numbers are not accepted")


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Source dates must use YYYY-MM-DD")
    return date.fromisoformat(value)


def parse_evidence(body: ImportEvidence) -> dict:
    raw = body.source_content.encode("utf-8")
    if len(raw) > MAX_SOURCE_BYTES or not body.unfiltered_project_export:
        raise ValueError("A bounded unfiltered project export is required")
    try:
        source = json.loads(raw, parse_float=Decimal, parse_int=Decimal,
                            parse_constant=_bad_number, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid supplier JSON export") from exc
    if not isinstance(source, dict) or set(source) != {"start", "end", "resolution", "results"}:
        raise ValueError("Unsupported supplier export fields")
    start, end = _day(source["start"]), _day(source["end"])
    if end < start or (end-start).days > 366:
        raise ValueError("Use one supplier period of at most 367 days")
    if (source["resolution"] != {"units": "day", "amount": Decimal(1)}
            or not isinstance(source["resolution"].get("amount"), Decimal)):
        raise ValueError("Only the documented daily export is supported")
    rows = source["results"]
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("Supplier export exceeds the supported row count")
    seen, total, entries = set(), Decimal(0), []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"dollars", "grouping"}:
            raise ValueError("Unsupported supplier charge row")
        amount, group = row["dollars"], row["grouping"]
        if (not isinstance(amount, Decimal) or not amount.is_finite()
                or amount.copy_abs() > Decimal("1000000000000") or amount.as_tuple().exponent < -8):
            raise ValueError("Supplier charge requires a bounded exact decimal")
        if not isinstance(group, dict) or not {"start", "end"} <= set(group) or set(group)-{
                "start", "end", "accessor", "deployment", "line_item", "tags"}:
            raise ValueError("Unsupported supplier grouping")
        left, right = _day(group["start"]), _day(group["end"])
        if not start <= left <= right <= end:
            raise ValueError("Supplier charge lies outside the source period")
        for key in ("accessor", "deployment", "line_item"):
            if key in group and (not isinstance(group[key], str) or len(group[key]) > 200):
                raise ValueError("Unsupported supplier grouping value")
            if key in group:
                _storage_text(group[key])
        if "tags" in group and (not isinstance(group["tags"], list) or len(group["tags"]) > 50
                or any(not isinstance(tag, str) or len(tag) > 200 for tag in group["tags"])):
            raise ValueError("Unsupported supplier tags")
        for tag in group.get("tags", []):
            _storage_text(tag)
        key = json.dumps(group, sort_keys=True, separators=(",", ":"))
        if key in seen:
            raise ValueError("Duplicate supplier group would double-count charges")
        seen.add(key)
        total += amount
        entries.append({"reported_charges": money_text(amount), "grouping": group})
    normalized = {
        "source_kind": SOURCE_KIND, "source_documentation": SOURCE_DOC,
        "supplier": "deepgram", "account_id": str(body.account_id),
        "period_start": str(start), "period_end": str(end), "currency": "USD",
        "reported_charges": money_text(total) if rows else None,
        "credits": None, "tax": None, "net_spend": None, "units": None,
        "query_context": "operator_declared_unfiltered_project_export",
        "coverage": "reported_rows_only" if rows else "no_charge_rows_returned",
        "entries": entries,
    }
    source_sha = hashlib.sha256(raw).hexdigest()
    identity = {"source_sha256": source_sha, "account_id": str(body.account_id),
                "reference": body.reference, "supersedes_id": str(body.supersedes_id) if body.supersedes_id else None,
                "source_kind": SOURCE_KIND, "unfiltered_project_export": True}
    return {"raw": raw, "normalized": normalized, "source_sha256": source_sha,
            "evidence_sha256": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
            "period_start": start, "period_end": end, "reported_charges": total if rows else None}


def reconciliation_comparison(statement: dict) -> dict:
    # Calls have no authoritative supplier/account cost attribution. Do not
    # subtract their legacy USD estimates or customer charges from this source.
    amount = statement.get("reported_charges")
    return {"basis": "no_retained_supplier_account_allocation",
            "allocated_charges": None, "unallocated_charges": money_text(amount),
            "currency": statement["currency"], "credits": None, "net_spend": None,
            "matched": False, "other_supplier_coverage": "missing"}
