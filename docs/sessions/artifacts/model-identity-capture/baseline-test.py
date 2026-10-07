"""Live model-owned identity capture; actual services with synthetic SQL ports."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import lead_details as api
from app.domain.services.lead_capture_service import project_contact_evidence
from app.domain.services.voice_pipeline.contact_recording import record_contact
from app.domain.services.voice_pipeline.lead_slot_capture import snapshot_slots
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
