"""Durable caller-source mismatch cannot be presented as usable contact."""
import hashlib
from copy import deepcopy

import pytest

from app.domain.services.lead_capture_service import project_contact_evidence
from app.domain.services.transcript_service import TranscriptService


def fixture():
    texts = [("value", 1, "My email is alex@example.com."), ("yes", 2, "Yes, correct.")]
    turns = [{"role": "user", "content": text, "is_final": True,
              "metadata": {"provider_item_id": item, "caller_turn_order": order}}
             for item, order, text in texts]
    sources = [{"provider_item_id": item, "caller_turn_order": order,
                "revision_sha256": hashlib.sha256(text.encode()).hexdigest()} for item, order, text in texts]
    row = {"field_key": "email", "field_type": "email", "source": "caller_stated",
           "value": "alex@example.com", "normalized_value": "alex@example.com", "raw_value": "alex@example.com",
           "confirmed": True, "confirmed_at": "2026-10-05T00:00:00Z", "validation_status": "confirmed",
           "evidence": {"value_source": sources[0], "confirmation_source": sources[1],
                        "status_source": sources[1], "confirmation_evidence": "readback_and_caller_affirmation"}}
    return row, turns


def revised(turns, index, content):
    service = TranscriptService()
    call = "ag05-projection-" + str(index)
    service.clear_buffer(call)
    service._sealed.pop(call, None)
    for position, turn in enumerate(turns):
        service.accumulate_turn(call, "user", turn["content"], is_final=True, turn_index=position, metadata=turn["metadata"])
    target = turns[index]["metadata"]
    assert service.annotate_turn_revision(call, turn_index=index, content=content,
        provider_item_id=target["provider_item_id"], caller_turn_order=target["caller_turn_order"])
    result = service.get_transcript_json(call)
    service.clear_buffer(call)
    return result


def test_current_matching_contact_remains_confirmed_without_mutating_input():
    row, turns = fixture()
    before = deepcopy(row)
    result = project_contact_evidence(row, turns)
    assert result["confirmed"] and result["value"] == "alex@example.com"
    assert result["evidence"]["provenance_status"] == "matched"
    assert row == before


@pytest.mark.parametrize("index,content,expected_value", [
    (0, "My email is blair@example.com.", None),
    (0, "", None), (1, "No, wrong.", "alex@example.com"), (1, "", "alex@example.com"),
])
def test_durable_revision_demotes_failed_contact_write_without_guessing_replacement(index, content, expected_value):
    row, turns = fixture()
    result = project_contact_evidence(row, revised(turns, index, content))
    assert not result["confirmed"] and result["confirmed_at"] is None
    assert result["value"] == expected_value
    assert result["evidence"]["status"] == "needs_review"
    assert result["raw_value"] == "alex@example.com"  # Historical audit remains.


@pytest.mark.parametrize("turns", [None, [], "not json", {"turns": []}])
def test_typed_proof_without_durable_source_is_unavailable_not_confirmed(turns):
    row, _ = fixture()
    result = project_contact_evidence(row, turns)
    assert result["value"] is None and not result["confirmed"]


def test_manual_and_unversioned_historical_rows_are_not_relabelled_as_verified():
    row, _ = fixture()
    row["source"] = "manual_edit"
    assert project_contact_evidence(row, []) == row
    row["source"], row["evidence"] = "caller_stated", {}
    result = project_contact_evidence(row, [])
    assert result["confirmed"]  # Existing claim, not a newly established proof.
    assert result["evidence"]["provenance_status"] == "unversioned"


def test_canonical_whitespace_does_not_falsely_revoke_matching_source():
    row, turns = fixture()
    turns[0]["content"] = "  " + turns[0]["content"] + "  "
    result = project_contact_evidence(row, turns)
    assert result["confirmed"]


@pytest.mark.parametrize("owner", [{}, False, "", []])
def test_present_malformed_owner_is_not_legacy_confirmed(owner):
    row, turns = fixture()
    row["evidence"] = {"value_source": owner}
    result = project_contact_evidence(row, turns)
    assert not result["confirmed"] and result["value"] is None
    assert result["evidence"]["provenance_status"] == "needs_review"


def test_versioned_confirmed_value_requires_its_confirmation_owner():
    row, turns = fixture()
    row["evidence"].pop("confirmation_source")
    result = project_contact_evidence(row, turns)
    assert not result["confirmed"] and result["value"] == "alex@example.com"
    assert result["evidence"]["source_checks"]["confirmation_source"] == "unavailable"


def test_caller_null_status_needs_no_invented_value_or_confirmation_source():
    row, turns = fixture()
    row.update(value=None, normalized_value=None, confirmed=False, confirmed_at=None, validation_status="invalid")
    row["evidence"] = {"status_source": row["evidence"]["status_source"]}
    result = project_contact_evidence(row, turns)
    assert result["value"] is None and not result["confirmed"]
    assert result["validation_status"] == "invalid"
    assert result["evidence"]["provenance_status"] == "matched"
