"""Actual native parsers/bridge, synthetic receipt only, no provider or DB I/O."""
import json
import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from tests.qualification.ag04_native import NativeReplay
from app.domain.services.voice_pipeline.lead_slot_capture import snapshot_slots


def replay(provider):
    corpus = json.loads((Path(__file__).parents[1] / "fixtures/conversation/ag04_native.json").read_text())
    return NativeReplay({"id": "ag05-contact", "expect": {}, "semantic_ids": []}, provider, corpus)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_source_revision_replaces_only_owned_contact(provider):
    r = replay(provider)
    await r.step({"kind": "caller", "item": "source", "text": "My email is alex at example dot com."})
    r.session.captured_slots = replace(r.session.captured_slots, follow_up="independent request")
    await r.step({"kind": "caller", "item": "source", "revision": True,
                  "text": "My email is blair at example dot com."})
    slots = r.session.captured_slots
    assert slots.email == "blair@example.com"
    assert not slots.email_confirmed
    assert slots.follow_up == "independent request"
    evidence = snapshot_slots(slots)["email"]["evidence"]
    assert evidence["value_source"]["provider_item_id"] == "source"
    assert evidence["confirmation_source"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
@pytest.mark.parametrize("revision", ["No, that is wrong.", ""])
async def test_confirmation_revision_cannot_keep_obsolete_yes(provider, revision):
    r = replay(provider)
    await r.step({"kind": "caller", "item": "source", "text": "My email is alex at example dot com."})
    await r.step({"kind": "response", "text": "Your email is alex@example.com, correct?"})
    await r.step({"kind": "caller", "item": "confirmation", "text": "Yes, that is correct."})
    assert r.session.captured_slots.email_confirmed
    confirmed_evidence = snapshot_slots(r.session.captured_slots)["email"].get("evidence", {})
    assert confirmed_evidence.get("confirmation_source", {}).get("provider_item_id") == "confirmation"
    assert confirmed_evidence["readback"]["evidence"] == "transport_played"
    await r.step({"kind": "caller", "item": "confirmation", "revision": True, "text": revision})
    assert not r.session.captured_slots.email_confirmed
    assert r.bridge._live_state.confirmed_email is None


@pytest.mark.asyncio
async def test_revision_does_not_revoke_independent_capture():
    r = replay("openai")
    await r.step({"kind": "caller", "item": "source", "text": "My email is alex at example dot com."})
    from app.domain.services.voice_pipeline.contact_capture import ContactCaptureState, CaptureStatus
    independent = ContactCaptureState(kind="email", status=CaptureStatus.CONFIRMED,
        normalized_value="manual@example.com", raw_value="manual@example.com", validation_status="confirmed")
    r.session.captured_slots = replace(r.session.captured_slots, email="manual@example.com",
        email_confirmed=True, email_capture=independent)
    await r.step({"kind": "caller", "item": "source", "revision": True, "text": ""})
    assert r.session.captured_slots.email_capture is independent
    await r.step({"kind": "caller", "item": "source", "revision": True,
                  "text": "My email is another at example dot com."})
    assert r.session.captured_slots.email_capture is independent


@pytest.mark.asyncio
async def test_duplicate_final_keeps_identical_confirmation_and_no_new_capture():
    r = replay("openai")
    await r.step({"kind": "caller", "item": "source", "text": "My email is alex at example dot com."})
    before = r.session.captured_slots
    await r.step({"kind": "caller", "item": "source", "revision": True,
                  "text": "My email is alex at example dot com."})
    assert r.session.captured_slots is before


@pytest.mark.asyncio
async def test_revision_preserves_independent_legacy_scalar_and_readback_group():
    r = replay("openai")
    await r.step({"kind": "caller", "item": "source", "text": "My email is alex at example dot com."})
    # A legacy producer may have scalar evidence without a structured capture;
    # emulate that boundary without auto-lifting through CallState.__post_init__.
    object.__setattr__(r.session.captured_slots, "phone", "+14155552671")
    object.__setattr__(r.session.captured_slots, "phone_confirmed", True)
    object.__setattr__(r.session.captured_slots, "phone_readback_attempts", 2)
    assert r.session.captured_slots.phone_capture is None
    await r.step({"kind": "caller", "item": "source", "revision": True,
                  "text": "My email is blair at example dot com."})
    assert r.session.captured_slots.phone == "+14155552671"
    assert r.session.captured_slots.phone_confirmed
    assert r.session.captured_slots.phone_readback_attempts == 2
    assert r.session.captured_slots.phone_capture is None


@pytest.mark.asyncio
async def test_confirmation_revision_uses_original_readback_not_later_assistant():
    r = replay("openai")
    await r.step({"kind": "caller", "item": "source", "text": "My email is alex at example dot com."})
    await r.step({"kind": "response", "text": "Your email is alex@example.com, correct?"})
    await r.step({"kind": "caller", "item": "confirmation", "text": "No, that is wrong."})
    await r.step({"kind": "response", "text": "What information would you like?"})
    await r.step({"kind": "caller", "item": "confirmation", "revision": True, "text": "Yes, that is correct."})
    assert r.session.captured_slots.email_confirmed
    assert snapshot_slots(r.session.captured_slots)["email"]["evidence"]["readback"]["utterance_id"] == "rt-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["My email is maybe unclear", "My phone number is +123"])
async def test_caller_unusable_contact_keeps_null_status_with_owned_source(text):
    r = replay("openai")
    await r.step({"kind": "caller", "item": "unclear", "text": text})
    rows = snapshot_slots(r.session.captured_slots)
    assert len(rows) == 1
    row = next(iter(rows.values()))
    assert row["value"] is None and not row["confirmed"]
    assert row["validation_status"] in {"needs_clarification", "invalid"}
    assert row["evidence"]["value_source"]["provider_item_id"] == "unclear"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Tell me about your service.", "My colleague's email is other@example.com.",
    'He said "My email is other@example.com".', 'She said her phone is +14155552671.',
    "Please don't note my email other@example.com.", "My email is not other@example.com.",
    "Please email my colleague at other@example.com."])
async def test_unrelated_or_third_party_turn_creates_no_caller_contact_row(text):
    r = replay("openai")
    await r.step({"kind": "caller", "text": text})
    assert snapshot_slots(r.session.captured_slots) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["My email is 'alex@example.com'.", 'Our business email is alex@example.com.',
    'alex@example.com', "'alex@example.com'"])
async def test_direct_self_business_and_bare_contact_answers_remain_supported(text):
    r = replay("openai")
    await r.step({"kind": "caller", "text": text})
    row = snapshot_slots(r.session.captured_slots)["email"]
    assert row["value"] == "alex@example.com" and not row["confirmed"]


@pytest.mark.asyncio
async def test_native_flush_resnapshots_revision_arriving_during_paused_write_and_drains():
    r = replay("openai")
    entered, release = asyncio.Event(), asyncio.Event()
    writes = []
    async def flush(call_id, **kwargs):
        writes.append((r.transcripts.get_transcript_text(call_id), kwargs))
        if len(writes) == 1:
            entered.set()
            await release.wait()
        return True
    r.transcripts.flush_to_database = flush
    r.bridge._knowledge_pool = object()
    r.bridge._schedule_contact_persist = lambda **_: None
    target, tenant = str(uuid4()), str(uuid4())
    r.session._dialer_call_id, r.session._dialer_tenant_id = target, tenant
    await r.step({"kind": "caller", "item": "source", "text": "My email is alex at example dot com."})
    await entered.wait()
    task = r.bridge._transcript_flush_task
    await r.step({"kind": "caller", "item": "source", "revision": True,
                  "text": "My email is blair at example dot com."})
    assert r.bridge._transcript_flush_task is task
    release.set()
    await r.bridge.stop()
    assert len(writes) == 2
    assert "blair" in writes[-1][0] and "alex" not in writes[-1][0]
    assert writes[-1][1]["target_call_id"] == target
    assert writes[-1][1]["tenant_id"] == tenant


@pytest.mark.asyncio
async def test_empty_owned_final_then_revision_has_real_source_row():
    r = replay("openai")
    await r.step({"kind": "caller", "item": "source", "text": ""})
    assert r.bridge._current_caller_transcript_index == 0
    original = r.transcripts.get_turns(r.call_id)[0]
    assert original.content == ""
    await r.step({"kind": "caller", "item": "source", "revision": True,
                  "text": "My email is alex at example dot com."})
    rows = r.transcripts.get_transcript_json(r.call_id)
    assert len(rows) == 1 and "alex" in rows[0]["content"]
    assert r.transcripts.get_turns(r.call_id)[0].content == ""
    assert snapshot_slots(r.session.captured_slots)["email"]["evidence"]["value_source"]["provider_item_id"] == "source"


@pytest.mark.asyncio
@pytest.mark.parametrize("key,kind", [("email", "text"), ("phone_2", "email")])
async def test_known_contact_key_cannot_bypass_contact_validation(key, kind):
    from app.domain.services.lead_capture_service import LeadCaptureService, InvalidCaptureError
    with pytest.raises(InvalidCaptureError, match="matching"):
        await LeadCaptureService(None).capture(tenant_id=str(uuid4()), call_id=str(uuid4()),
            field_key=key, field_type=kind, value="invalid", source="manual_edit", confirmed=True)
