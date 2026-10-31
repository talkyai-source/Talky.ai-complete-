"""Live model-owned identity capture; actual services with synthetic SQL ports."""
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.endpoints import lead_details as api
from app.domain.services.lead_capture_service import LeadCaptureService, project_contact_evidence
from app.domain.services.voice_pipeline.contact_recording import record_contact
from app.domain.services.voice_pipeline.lead_slot_capture import snapshot_slots
from app.services.scripts.prompt_builder import compose_system_prompt
from tests.unit.test_model_contact_recording import SQLPort, args, caller, native, session


IDENTITIES = [("full_name", "Siân O’Neill"), ("company_name", "Élan & Sons Ltd")]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,value", IDENTITIES)
async def test_model_identity_is_saved_pending_then_confirmed_corrected_and_withdrawn(kind, value):
    pool, s = SQLPort(), session()
    text = f"My {kind} is {value}."
    caller(s, text, 1)
    pending = await record_contact(s, args(text, kind=kind, value=value), pool=pool)
    assert pending["saved"] and pending["validation_status"] == "awaiting_confirmation"
    snapshot = snapshot_slots(s.captured_slots)[kind]
    assert snapshot["field_type"] == "text" and snapshot["value"] == value
    assert snapshot["raw_value"] == text and not snapshot["confirmed"]
    caller(s, "Yes, you've got that right.", 2)
    confirmed = await record_contact(s, args("Yes, you've got that right.", kind=kind,
        operation="confirm", value=value, expected=value), pool=pool)
    assert confirmed["saved"] and confirmed["validation_status"] == "confirmed"
    caller(s, "Actually use the updated spelling.", 3)
    corrected = await record_contact(s, args("Actually use the updated spelling.", kind=kind,
        value=value + " II", expected=value), pool=pool)
    assert corrected["saved"] and corrected["validation_status"] == "awaiting_confirmation"
    caller(s, "Please remove that detail.", 4)
    withdrawn = await record_contact(s, args("Please remove that detail.", kind=kind,
        operation="withdraw", value=None, expected=value + " II"), pool=pool)
    assert withdrawn["saved"] and withdrawn["validation_status"] == "cancelled"
    assert snapshot_slots(s.captured_slots)[kind]["value"] is None


@pytest.mark.parametrize("kind,value", IDENTITIES)
def test_identity_projection_withholds_superseded_caller_name_or_company(kind, value):
    text = f"My {kind} is {value}."
    source = {"provider_item_id": "caller-1", "caller_turn_order": 1,
              "revision_sha256": hashlib.sha256(text.encode()).hexdigest()}
    row = {"field_key": kind, "field_type": "text", "source": "caller_stated", "value": value,
           "confirmed": False, "validation_status": "awaiting_confirmation",
           "evidence": {"value_source": source, "status_source": source}}
    turns = [{"role": "user", "content": "That is somebody else's detail.", "is_final": True,
              "metadata": {"provider_item_id": "caller-1", "caller_turn_order": 1}}]
    result = project_contact_evidence(row, turns)
    assert result["value"] is None and result["validation_status"] == "needs_clarification"
    assert result["evidence"]["provenance_status"] == "needs_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,_value", IDENTITIES)
async def test_manual_identity_removal_is_cancelled_not_confirmed(monkeypatch, kind, _value):
    captured = []
    class Service:
        async def capture(self, **kwargs):
            captured.append(kwargs)
            return True
    monkeypatch.setattr(api, "_service", Service)
    result = await api.correct_lead_detail("call", kind, api.ManualEdit(value=None, field_type="text"),
        SimpleNamespace(tenant_id="tenant"))
    assert result["ok"]
    assert captured[0]["source"] == "manual_edit"
    assert captured[0]["confirmed"] is False and captured[0]["validation_status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_native_shared_tool_exposes_and_records_identity_without_parsing(provider):
    replay, pool, bound = native(provider), SQLPort(), session()
    s = replay.session
    s._lead_capture_binding, s._voice_action_pool = None, pool
    for key in ("_dialer_call_id", "_dialer_tenant_id", "_dialer_campaign_id", "_dialer_lead_id"):
        setattr(s, key, getattr(bound, key))
    for kind, value in IDENTITIES:
        text = f"My {kind} is {value}."
        await replay.step({"kind": "caller", "text": text})
        assert kind not in snapshot_slots(s.captured_slots)
        await replay.step({"kind": "tool", "name": "record_contact", "arguments": args(text, kind=kind, value=value)})
        assert snapshot_slots(s.captured_slots)[kind]["value"] == value
    results = [json.loads(row["item"]["output"]) for row in replay.socket.sent
               if row.get("item", {}).get("type") == "function_call_output"]
    assert all(row["saved"] for row in results)
    assert results[-1]["contacts"]["full_name"]["validation_status"] == "awaiting_confirmation"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,value", IDENTITIES)
async def test_identity_pending_context_cas_add_and_confirmation_are_honest(kind, value):
    pool, s = SQLPort(), session()
    caller(s, f"I use {value}.", 1)
    assert (await record_contact(s, args(f"I use {value}.", kind=kind, value=value), pool=pool))["saved"]
    facts = json.loads(compose_system_prompt("GUIDE", s.captured_slots).split("\n")[1])
    assert facts[kind] == {"field_key": kind, "value": value, "status": "awaiting_confirmation"}
    same_turn = args(f"I use {value}.", kind=kind, value=value, expected=value, operation="confirm")
    assert (await record_contact(s, same_turn, pool=pool))["status"] == "confirmation_not_current"
    caller(s, "Yes, keep it.", 2)
    assert (await record_contact(s, args("Yes, keep it.", kind=kind, value=value, expected=value,
        operation="confirm"), pool=pool))["saved"]
    before = s.captured_slots
    caller(s, "And another one.", 3)
    assert (await record_contact(s, args("And another one.", kind=kind, value="Another", expected=value,
        operation="add"), pool=pool))["status"] == "additional_contact_not_ready"
    assert (await record_contact(s, args("And another one.", kind=kind, value="Another", expected="Outdated"),
        pool=pool))["status"] == "contact_changed"
    assert s.captured_slots is before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,value", IDENTITIES)
@pytest.mark.parametrize("revision", ["value", "confirmation"])
async def test_identity_live_source_revision_demotes_only_its_owned_capture(kind, value, revision):
    pool, s = SQLPort(), session()
    caller(s, value, 1, "identity")
    assert (await record_contact(s, args(value, kind=kind, value=value), pool=pool))["saved"]
    caller(s, "Yes.", 2, "confirmation")
    assert (await record_contact(s, args("Yes.", kind=kind, value=value, expected=value,
        operation="confirm"), pool=pool))["saved"]
    caller(s, "alex@example.com", 3)
    assert (await record_contact(s, args("alex@example.com"), pool=pool))["saved"]
    email = s.captured_slots.email_capture
    caller(s, "That is not right.", 1 if revision == "value" else 2,
        "identity" if revision == "value" else "confirmation")
    row = snapshot_slots(s.captured_slots)[kind]
    assert not row["confirmed"] and row["confirmed_at"] is None
    assert row["value"] == (None if revision == "value" else value)
    assert s.captured_slots.email_capture is email


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fail", "conflict"])
async def test_identity_save_failure_cannot_borrow_email_acknowledgement(mode):
    pool, s = SQLPort(), session()
    caller(s, "alex@example.com", 1)
    assert (await record_contact(s, args("alex@example.com"), pool=pool))["saved"]
    setattr(pool, mode, True)
    caller(s, "My name is Siân O’Neill.", 2)
    result = await record_contact(s, args("My name is Siân O’Neill.", kind="full_name", value="Siân O’Neill"), pool=pool)
    assert not result["saved"] and not result["confirmation_allowed"]
    assert result["field_key"] == "full_name" and result["validation_status"] == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_identity_quote_and_source_cannot_be_invented():
    pool, s = SQLPort(), session()
    caller(s, "Tell me about your company.", 1)
    result = await record_contact(s, args("My company is Northwind.", kind="company_name", value="Northwind"), pool=pool)
    assert result["status"] == "quote_not_found" and not pool.writes  # never said in this call
    old = s._contact_turn
    caller(s, "My name is Alex.", 2)
    result = await record_contact(s, args("Tell me about your company.", kind="company_name", value="Northwind"),
        turn=old, pool=pool)
    assert result["status"] == "stale_caller_turn" and not pool.writes


@pytest.mark.asyncio
async def test_unusable_identity_stays_explicitly_unconfirmed_and_scalar_cannot_invent_source():
    from app.services.scripts.call_state_tracker import CallState
    pool, s = SQLPort(), session()
    text = "The text includes a newline."
    caller(s, text, 1)
    result = await record_contact(s, args(text, kind="full_name", value="Alex\nSmith"), pool=pool)
    assert result["saved"] and result["value"] is None and result["validation_status"] == "needs_clarification"
    assert snapshot_slots(CallState(full_name="Unowned name", full_name_confirmed=True)) == {}


def test_both_actual_prompt_engines_describe_the_same_extended_tool_without_scripting_names():
    from app.realtime.personas import RealtimePersona
    from app.realtime.prompts import build_realtime_instructions, PROMPT_VERSION
    from app.realtime.tools import realtime_voice_action_tools
    from app.domain.services.voice_pipeline.contact_recording import CONTACT_TOOL_SPEC
    from tests.unit.test_prompt_versions import compose
    for text in (build_realtime_instructions(RealtimePersona()), compose("lead_gen")):
        assert "name, company, email or phone" in text and "selected candidate as expected_value" in text
        assert "persistence" in text and "pending" in text
    assert PROMPT_VERSION == "realtime@10"
    wire = next(spec for spec in realtime_voice_action_tools() if spec["name"] == "record_contact")
    assert wire["parameters"] == CONTACT_TOOL_SPEC["function"]["parameters"]
    assert wire["parameters"]["properties"]["kind"]["enum"] == ["email", "phone", "full_name", "company_name"]


@pytest.mark.asyncio
async def test_required_live_identity_is_pending_but_legacy_imported_name_keeps_existing_policy():
    class Pool(SQLPort):
        async def fetch(self, sql, *parameters):
            assert "campaign_lead_fields" in sql
            return [{"field_key": "full_name", "field_type": "text"}]
    service = LeadCaptureService(Pool())
    saved = {"field_key": "full_name", "value": "Alex", "confirmed": False,
             "validation_status": "awaiting_confirmation"}
    async def rows(*_):
        return [saved]
    service.details_for_call = rows
    tenant_id, call_id = str(uuid4()), str(uuid4())
    assert await service.missing_required(tenant_id, call_id) == ["full_name"]
    saved["validation_status"] = None
    assert await service.missing_required(tenant_id, call_id) == []
