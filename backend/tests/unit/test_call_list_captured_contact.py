"""The calls list must carry the contact the caller GAVE during the call.

2026-09-25: call_lead_details held b97ce4c5's phone (+923085397539) and
4291700f's email, but GET /calls never returned them — the list showed only
the line the caller rang from ("ext:940007" / "Private caller"), and a caller
who left their number never showed as a hot lead.
"""
from __future__ import annotations

import pytest

from app.api.v1.endpoints.calls import CallListItem, _captured_contact_rows_sql, _display_captured_contacts


def test_the_candidate_query_is_tenant_scoped_and_bounded():
    sql = " ".join(_captured_contact_rows_sql().split())
    assert "d.call_id = c.id AND d.tenant_id = c.tenant_id" in sql
    assert "LIMIT 64" in sql
    assert "d.evidence" in sql
    assert "NOT IN ('invalid','cancelled','needs_clarification')" in sql


def test_the_list_item_carries_the_captured_contact():
    item = CallListItem(
        id="b97ce4c5", timestamp="2026-09-23T12:41:32Z", to_number="ext:940003",
        status="ended", captured_phone="+923085397539", captured_email=None,
    )
    assert item.model_dump()["captured_phone"] == "+923085397539"


def test_the_list_item_says_when_a_contact_is_unconfirmed():
    item = CallListItem(
        id="4291700f", timestamp="2026-09-24T11:10:00Z", to_number="+923001234567",
        status="ended", captured_phone="+923085397539", captured_phone_confirmed=False,
    )
    dumped = item.model_dump()
    assert dumped["captured_phone_confirmed"] is False
    assert dumped["captured_email_confirmed"] is None


@pytest.mark.parametrize("revised", ["confirmation", "value", None])
def test_list_contact_uses_current_transcript_before_confirmed_selection(revised):
    import hashlib
    from app.domain.services.transcript_service import TranscriptService

    service = TranscriptService()
    service.clear_buffer("list-proof")
    texts = ["My email is alex@example.com.", "Yes, that's correct."]
    owners = []
    try:
        for index, text in enumerate(texts, 1):
            service.accumulate_turn("list-proof", "user", text, is_final=True, turn_index=index,
                metadata={"provider_item_id": f"item-{index}", "caller_turn_order": index})
            owners.append({"provider_item_id": f"item-{index}", "caller_turn_order": index,
                           "revision_sha256": hashlib.sha256(text.encode()).hexdigest()})
        candidate = {"field_key": "email", "field_type": "email", "source": "caller_stated",
                     "value": "alex@example.com", "normalized_value": "alex@example.com",
                     "confirmed": True, "validation_status": "confirmed", "confirmed_at": "synthetic-time",
                     "evidence": {"value_source": owners[0], "confirmation_source": owners[1]}}
        if revised:
            index = 2 if revised == "confirmation" else 1
            service.annotate_turn_revision("list-proof", turn_index=index,
                provider_item_id=f"item-{index}", caller_turn_order=index, content="No, that was wrong.")
        result = _display_captured_contacts({"captured_contact_rows": [candidate],
                                            "transcript_json": service.get_transcript_json("list-proof")})
        assert result["captured_email"] == (None if revised == "value" else "alex@example.com")
        assert result["captured_email_confirmed"] is (None if revised == "value" else revised is None)
        assert set(result) == {"captured_phone", "captured_email", "captured_phone_confirmed", "captured_email_confirmed"}
    finally:
        service.clear_buffer("list-proof")


def test_manual_contacts_and_existing_preference_are_preserved():
    rows = [
        {"field_key": "secondary", "field_type": "email", "value": "secondary@example.com",
         "source": "manual_edit", "confirmed": True, "updated_at": "2026-10-06"},
        {"field_key": "email", "field_type": "email", "value": "primary@example.com",
         "source": "manual_edit", "confirmed": True, "updated_at": "2026-10-05"},
        {"field_key": "email", "field_type": "email", "value": "unconfirmed@example.com",
         "source": "caller_stated", "confirmed": False, "updated_at": "2026-10-07"},
    ]
    result = _display_captured_contacts({"captured_contact_rows": rows, "transcript_json": []})
    assert result["captured_email"] == "primary@example.com"
    assert result["captured_email_confirmed"] is True
