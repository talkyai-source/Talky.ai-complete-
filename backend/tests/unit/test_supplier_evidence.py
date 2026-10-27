"""Supplier-source validation preserves exact reported charges and unknowns."""
import hashlib
import json
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.supplier_evidence import ImportEvidence, parse_evidence, reconciliation_comparison


def source():
    return {"start": "2026-09-01", "end": "2026-09-30", "resolution": {"units": "day", "amount": 1},
            "results": [{"dollars": 0.25, "grouping": {"start": "2026-09-01", "end": "2026-09-01",
                        "deployment": "hosted", "line_item": "streaming::flux-general-en"}}]}


def body(content=None, **kwargs):
    return ImportEvidence(request_id=uuid4(), account_id=uuid4(), reference="Synthetic supplier capture",
                          source_content=content or json.dumps(source()), unfiltered_project_export=True, **kwargs)


def test_retains_exact_original_bytes_without_inventing_credits_or_finance_approval():
    raw = json.dumps(source(), indent=3)
    parsed = parse_evidence(body(raw))
    assert parsed["raw"] == raw.encode()
    assert parsed["source_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert parsed["reported_charges"] == Decimal("0.25")
    normalized = parsed["normalized"]
    assert normalized["query_context"] == "operator_declared_unfiltered_project_export"
    for unknown in ("credits", "tax", "net_spend", "units"):
        assert normalized[unknown] is None
    assert "verified" not in normalized and "paid" not in normalized


def test_decimal_source_is_not_rounded_by_binary_float_or_netting_assumptions():
    raw = json.dumps(source()).replace('"dollars": 0.25', '"dollars": 0.12345678')
    assert parse_evidence(body(raw))["reported_charges"] == Decimal("0.12345678")
    raw = raw.replace("0.12345678", "-0.12345678")
    parsed = parse_evidence(body(raw))
    assert parsed["reported_charges"] == Decimal("-0.12345678")
    assert parsed["normalized"]["credits"] is None


def test_empty_results_remain_unknown_not_zero_spending():
    raw = source(); raw["results"] = []
    parsed = parse_evidence(body(json.dumps(raw)))
    assert parsed["reported_charges"] is None
    assert parsed["normalized"]["coverage"] == "no_charge_rows_returned"


def test_original_utf8_bom_and_postgres_zero_keep_exact_bytes_and_fixed_point():
    raw = "\ufeff" + json.dumps(source()).replace('"dollars": 0.25', '"dollars": 0.00000000')
    parsed = parse_evidence(body(raw))
    assert parsed["raw"].startswith(b"\xef\xbb\xbf")
    assert parsed["raw"] == raw.encode("utf-8")
    assert parsed["normalized"]["reported_charges"] == "0.00000000"
    assert reconciliation_comparison({"reported_charges": Decimal("0E-8"), "currency": "USD"})["unallocated_charges"] == "0.00000000"


@pytest.mark.parametrize("amount", ["true", '"1.25"', "null", "NaN", "Infinity", "1e50", "1e999999999", "0.000000001"])
def test_rejects_ambiguous_or_unbounded_money(amount):
    raw = json.dumps(source()).replace('"dollars": 0.25', '"dollars": ' + amount)
    with pytest.raises(ValueError):
        parse_evidence(body(raw))


@pytest.mark.parametrize("change", [
    lambda doc: doc.update(next_page="hidden-results"),
    lambda doc: doc.update(start="2026-10-01"),
    lambda doc: doc.update(resolution={"units": "day", "amount": True}),
    lambda doc: doc.update(resolution={"units": "month", "amount": 1}),
    lambda doc: doc["results"].append(doc["results"][0]),
    lambda doc: doc["results"][0]["grouping"].update(start="2026-08-31"),
    lambda doc: doc["results"][0]["grouping"].update(secret="not-a-source-field"),
    lambda doc: doc["results"][0]["grouping"].update(tags="not-an-array"),
    lambda doc: doc["results"][0]["grouping"].update(accessor="\ud800"),
    lambda doc: doc["results"][0]["grouping"].update(tags=["\udfff"]),
    lambda doc: doc["results"][0]["grouping"].update(deployment="\x00"),
    lambda doc: doc["results"][0].update(credits=0),
])
def test_rejects_unsupported_coverage_and_group_shapes(change):
    doc = source(); change(doc)
    with pytest.raises(ValueError):
        parse_evidence(body(json.dumps(doc)))


def test_duplicate_json_fields_and_oversize_utf8_are_rejected():
    with pytest.raises(ValueError):
        parse_evidence(body(json.dumps(source()).replace('"start": "2026-09-01"',
            '"start": "2026-09-01", "start": "2026-09-02"', 1)))
    with pytest.raises(ValueError):
        parse_evidence(body("é" * 600000))
    with pytest.raises(ValidationError):
        body("a" * 1048577)


def test_account_reference_and_correction_change_evidence_identity_not_source_hash():
    first = body()
    parsed = parse_evidence(first)
    for changed in (first.model_copy(update={"account_id": uuid4()}),
                    first.model_copy(update={"reference": "Other account export"}),
                    first.model_copy(update={"supersedes_id": uuid4()})):
        other = parse_evidence(changed)
        assert other["source_sha256"] == parsed["source_sha256"]
        assert other["evidence_sha256"] != parsed["evidence_sha256"]
    assert parse_evidence(first.model_copy(update={"request_id": uuid4()}))["evidence_sha256"] == parsed["evidence_sha256"]


def test_unfiltered_scope_must_be_explicit_and_legacy_prices_cannot_be_allocated():
    with pytest.raises(ValueError):
        parse_evidence(body().model_copy(update={"unfiltered_project_export": False}))
    comparison = reconciliation_comparison({"reported_charges": Decimal("12.34"), "currency": "USD",
                                          "legacy_calls_cost": 8, "customer_revenue": 42})
    assert comparison["unallocated_charges"] == "12.34"
    assert comparison["allocated_charges"] is None and comparison["matched"] is False
    assert comparison["net_spend"] is None and comparison["credits"] is None
