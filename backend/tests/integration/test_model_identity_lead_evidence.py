"""Shared model tool → actual restricted PostgreSQL → existing Leads API.

Reuses the approved disposable UUID fixture; no provider/model or browser.
"""
import json
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import lead_details as api
from app.domain.services.transcript_service import TranscriptService
from app.domain.services.voice_pipeline.contact_recording import bind_contact_turn, record_contact
from app.domain.services.voice_pipeline.contact_capture import ContactSource
from app.domain.services.voice_pipeline.lead_slot_capture import capture_turn_slots
from app.services.scripts.call_state_tracker import CallState
from tests.integration import test_ag05_lead_evidence as lead_fixtures

lead_db = lead_fixtures.lead_db

pytestmark = pytest.mark.integration
IDENTITIES = [("full_name", "Siân O’Neill"), ("company_name", "Élan & Sons Ltd")]


def live_session(db):
    return SimpleNamespace(captured_slots=CallState(), contact_phone_region=None,
        _voice_action_pool=db.pool, _dialer_call_id=str(db.calls[0]),
        _dialer_tenant_id=str(db.tenants[0]), _dialer_campaign_id=str(db.campaigns[0]),
        _dialer_lead_id=str(db.leads[0]))


async def turn(db, s, kind, value, operation="set", expected=None, *, text=None):
    text = text or f"Please {operation} my {kind}: {value}."
    order = len(getattr(s, "_identity_test_turns", [])) + 1
    transcripts = TranscriptService()
    key = str(db.calls[0])
    # The real canonical source constructor is shared with traditional turns.
    current = transcripts.accumulate_turn(key, "user", text, is_final=True, turn_index=order - 1)
    assert transcripts.bind_caller_turn(key, current, caller_turn_order=order)
    bundle = transcripts.caller_evidence(key, order)
    bind_contact_turn(s, bundle["text"], ContactSource(**bundle["source"]))
    s._identity_test_turns = transcripts.get_transcript_json(key)
    await db.admin.execute("UPDATE calls SET transcript_json=$2::jsonb WHERE id=$1", db.calls[0],
                           json.dumps(s._identity_test_turns, default=str))
    return await record_contact(s, {"kind": kind, "operation": operation, "value": value,
        "expected_value": expected, "source_quote": text}, pool=db.pool)


async def projected(db):
    actor = SimpleNamespace(tenant_id=str(db.tenants[0]))
    calls = await api.get_lead_details(str(db.calls[0]), current_user=actor)
    leads = await api.get_contact_lead_details(str(db.leads[0]), current_user=actor)
    return ({row["field_key"]: row for row in calls["details"]},
            {row["field_key"]: row for row in leads["details"]})


@pytest.mark.parametrize("kind,value", IDENTITIES)
async def test_model_identity_roundtrip_correction_withdrawal_and_tenant_isolation(lead_db, kind, value):
    db, s = lead_db, live_session(lead_db)
    try:
        pending = await turn(db, s, kind, value)
        assert pending["saved"] and not getattr(s.captured_slots, f"{kind}_confirmed")
        for rows in await projected(db):
            row = rows[kind]
            assert row["field_type"] == "text" and row["value"] == value
            assert not row["confirmed"] and row["validation_status"] == "awaiting_confirmation"
            assert row["evidence"]["provenance_status"] == "matched"
        assert (await turn(db, s, kind, value, "confirm", value))["saved"]
        for rows in await projected(db):
            assert rows[kind]["confirmed"] and rows[kind]["confirmed_at"] is not None
        assert (await turn(db, s, kind, value + " II", expected=value))["saved"]
        for rows in await projected(db):
            assert rows[kind]["value"] == value + " II" and not rows[kind]["confirmed"]
            assert rows[kind]["confirmed_at"] is None
        assert (await turn(db, s, kind, None, "withdraw", value + " II", text="Remove that detail."))["saved"]
        for rows in await projected(db):
            assert rows[kind]["value"] is None and rows[kind]["validation_status"] == "cancelled"
        foreign = await api.get_contact_lead_details(str(db.leads[0]),
            current_user=SimpleNamespace(tenant_id=str(db.tenants[1])))
        assert foreign["details"] == []
        # Captured full name is not split into the imported/generated display name.
        original = await db.admin.fetchrow("SELECT first_name,last_name,company_name FROM leads WHERE id=$1", db.leads[0])
        assert all(value is None for value in original.values())
    finally:
        TranscriptService().clear_buffer(str(db.calls[0]))


@pytest.mark.parametrize("kind,value", IDENTITIES)
async def test_manual_identity_change_and_removal_outrank_later_model_write(lead_db, kind, value):
    db, s = lead_db, live_session(lead_db)
    actor = SimpleNamespace(tenant_id=str(db.tenants[0]))
    try:
        assert (await turn(db, s, kind, value))["saved"]
        assert (await turn(db, s, kind, value, "confirm", value))["saved"]
        await api.correct_lead_detail(str(db.calls[0]), kind,
            api.ManualEdit(value="Operator correction", field_type="text"), actor)
        denied = await turn(db, s, kind, "Later model value", expected=value)
        assert not denied["saved"]
        for rows in await projected(db):
            assert rows[kind]["value"] == "Operator correction" and rows[kind]["source"] == "manual_edit"
        await api.correct_lead_detail(str(db.calls[0]), kind, api.ManualEdit(value=None, field_type="text"), actor)
        assert not (await turn(db, s, kind, "Retry model value", expected="Later model value"))["saved"]
        for rows in await projected(db):
            assert rows[kind]["value"] is None and not rows[kind]["confirmed"]
            assert rows[kind]["validation_status"] == "cancelled" and rows[kind]["source"] == "manual_edit"
    finally:
        TranscriptService().clear_buffer(str(db.calls[0]))


@pytest.mark.parametrize("kind,value", IDENTITIES)
async def test_durable_identity_revision_hides_stale_value_when_sql_revoke_is_denied(lead_db, kind, value):
    db, s = lead_db, live_session(lead_db)
    try:
        assert (await turn(db, s, kind, value))["saved"]
        transcripts = TranscriptService()
        source = s._contact_turn.source
        replacement = "I was talking about someone else's details."
        assert transcripts.annotate_turn_revision(str(db.calls[0]), turn_index=0,
            provider_item_id=source.provider_item_id, caller_turn_order=source.caller_turn_order,
            content=replacement)
        bundle = transcripts.caller_evidence(str(db.calls[0]), 1)
        bind_contact_turn(s, bundle["text"], ContactSource(**bundle["source"]))
        await db.admin.execute("UPDATE calls SET transcript_json=$2::jsonb WHERE id=$1", db.calls[0],
                               json.dumps(transcripts.get_transcript_json(str(db.calls[0])), default=str))
        await db.admin.execute(f'REVOKE UPDATE ON call_lead_details FROM "{db.role}"')
        await capture_turn_slots(s, pool=db.pool)
        for rows in await projected(db):
            assert rows[kind]["value"] is None and not rows[kind]["confirmed"]
            assert rows[kind]["evidence"]["provenance_status"] == "needs_review"
    finally:
        await db.admin.execute(f'GRANT UPDATE ON call_lead_details TO "{db.role}"')
        TranscriptService().clear_buffer(str(db.calls[0]))


@pytest.mark.parametrize("kind,value", IDENTITIES)
async def test_existing_imported_identity_retains_generic_unconfirmed_behavior(lead_db, kind, value):
    db = lead_db
    assert await db.service.capture(tenant_id=str(db.tenants[0]), call_id=str(db.calls[0]),
        field_key=kind, field_type="text", value=value, source="imported", confirmed=False)
    for rows in await projected(db):
        row = rows[kind]
        assert row["source"] == "imported" and row["value"] == value and not row["confirmed"]
        assert row["validation_status"] is None and row["evidence"] == {}
