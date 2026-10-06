"""Model contact recording, source ownership and persistence boundaries."""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline.contact_capture import CaptureStatus
from app.services.scripts.call_state_tracker import CallState
from app.domain.services.voice_pipeline.lead_slot_capture import snapshot_slots
from tests.unit.test_ag05_native_contact_revision import replay, record


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["transport_played", "transmitted"])
async def test_native_played_contact_question_does_not_start_regex_capture_mode(evidence):
    r = replay("openai")
    await r.step({"kind": "response", "text": "What is the best phone number to call you back on?",
        "receipt": "completed" if evidence == "transport_played" else "transmitted"})
    assert getattr(r.session.captured_slots, "agent_asked_kind", None) is None
    assert getattr(r.session.captured_slots, "active_contact_kind", None) is None
    assert getattr(r.session.captured_slots, "phone_capture", None) is None
    assert r.gateway.submissions


@pytest.mark.asyncio
async def test_native_tool_sets_pending_and_confirms_from_later_caller_evidence():
    r = replay("openai")
    await r.step({"kind": "caller", "text": "bob at acme dot com"})
    assert snapshot_slots(r.session.captured_slots) == {}
    await record(r, "bob@acme.com")
    assert r.session.captured_slots.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION
    await r.step({"kind": "caller", "text": "yes, that's right"})
    assert not r.session.captured_slots.email_confirmed
    await record(r, "bob@acme.com", operation="confirm")
    assert r.session.captured_slots.email_capture.status is CaptureStatus.CONFIRMED
    assert r.session.captured_slots.email_capture.readback is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "expected_status"),
    (
        ("yes, that's right", "confirmed"),
        ("never mind, don't save that", "cancelled"),
    ),
)
async def test_native_model_resolution_updates_contact_without_directive_interrupt(reply, expected_status):
    r = replay("openai")
    await r.step({"kind": "caller", "text": "bob at acme dot com"})
    await record(r, "bob@acme.com")
    await r.step({"kind": "caller", "text": reply})
    await record(r, "bob@acme.com" if expected_status == "confirmed" else None,
        operation="confirm" if expected_status == "confirmed" else "withdraw")
    assert r.session.captured_slots.email_capture.validation_status == expected_status
    assert not any("BACKEND CONTACT MODE" in str(event) for event in r.socket.sent)


@pytest.mark.asyncio
async def test_native_model_can_record_both_fields_without_forced_capture_order():
    r = replay("openai")
    await r.step({"kind": "caller", "text": "My email is bob@acme.com and phone is +14155552671"})
    await record(r, "+14155552671", kind="phone")
    await record(r, "bob@acme.com")
    assert r.session.captured_slots.active_contact_kind is None
    assert not r.session.captured_slots.email_confirmed and not r.session.captured_slots.phone_confirmed
    await r.step({"kind": "caller", "text": "Yes, both details are correct"})
    await record(r, "bob@acme.com", operation="confirm")
    await record(r, "+14155552671", kind="phone", operation="confirm")
    assert r.session.captured_slots.email_confirmed and r.session.captured_slots.phone_confirmed
    assert not any("BACKEND CONTACT MODE" in str(event) for event in r.socket.sent)


@pytest.mark.asyncio
async def test_native_none_confidence_does_not_create_contact_without_model_tool():
    r = replay("openai")
    await r.step({"kind": "caller", "text": "bob at acme dot com", "confidence": None})
    assert snapshot_slots(r.session.captured_slots) == {}
    await record(r, "bob@acme.com")
    assert r.session.captured_slots.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION


@pytest.mark.asyncio
async def test_native_partial_tool_candidate_does_not_force_an_interruption_or_wording():
    r = replay("openai")
    await r.step({"kind": "caller", "text": "My email starts bob at"})
    await record(r, "bob at")
    assert r.session.captured_slots.email is None
    assert r.session.captured_slots.email_capture.status is CaptureStatus.NEEDS_CLARIFICATION
    assert not any("BACKEND CONTACT MODE" in str(event) for event in r.socket.sent)
    await r.step({"kind": "response", "text": "What comes after that?"})
    assert any(event.get("text") == "What comes after that?" for event in r.gateway.controls)




def test_only_confirmed_contact_exposes_full_audit_payload_for_persistence():
    from dataclasses import replace
    from app.domain.services.voice_pipeline.contact_capture import ContactCaptureState
    pending = CallState(email="bob@acme.com", email_capture=ContactCaptureState(
        kind="email", status=CaptureStatus.AWAITING_CONFIRMATION,
        raw_value="bob at acme dot com", normalized_value="bob@acme.com"))
    pending_item = snapshot_slots(pending)["email"]
    assert pending_item["confirmed"] is False
    assert pending_item["validation_status"] == "awaiting_confirmation"
    assert pending_item["confirmed_at"] is None
    confirmed = replace(pending, email_confirmed=True, email_capture=replace(
        pending.email_capture, status=CaptureStatus.CONFIRMED, confirmed_at=datetime.now(timezone.utc)))
    item = snapshot_slots(confirmed)["email"]
    assert item["value"] == "bob@acme.com"
    assert item["raw_value"] == "bob at acme dot com"
    assert item["normalized_value"] == "bob@acme.com"
    assert item["validation_status"] == "confirmed"
    assert item["confirmed_at"] is not None


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


class _AuditConn:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def transaction(self):
        return _AsyncContext(None)

    async def execute(self, *_args):
        return None

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args))
        if "FROM calls" in sql:
            return {"is_test": False}
        return {"id": "written"}


class _AuditPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self, **_kwargs):
        return _AsyncContext(self.conn)


def _audit_native(conn):
    r = replay("openai")
    r.session._lead_capture_binding = {
        "call_id": "22222222-2222-2222-2222-222222222222",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "campaign_id": "33333333-3333-3333-3333-333333333333",
        "lead_id": "44444444-4444-4444-4444-444444444444",
    }
    r.bridge._knowledge_pool = _AuditPool(conn)
    r.bridge._schedule_transcript_flush = lambda: None  # separate transcript persistence contract
    return r


@pytest.mark.asyncio
async def test_realtime_confirmation_persists_canonical_value_and_audit_once():
    conn = _AuditConn()
    r = _audit_native(conn)
    await r.step({"kind": "caller", "text": "bob at acme dot com"})
    await record(r, "bob@acme.com")
    pending = [args for sql, args in conn.statements if "INSERT INTO call_lead_details" in sql]
    assert len(pending) == 1 and pending[0][8] is False
    conn.statements.clear()
    await r.step({"kind": "caller", "text": "yes, that's right"})
    await record(r, "bob@acme.com", operation="confirm")
    await record(r, "bob@acme.com", operation="confirm")
    inserts = [(sql, args) for sql, args in conn.statements if "INSERT INTO call_lead_details" in sql]
    assert len(inserts) == 1
    _, args = inserts[0]
    assert args[6] == args[11] == "bob@acme.com" and args[8] is True
    assert args[10] == "bob at acme dot com" and args[12] == "confirmed" and args[13] is not None


@pytest.mark.asyncio
async def test_realtime_pending_correction_revokes_prior_caller_contact_row():
    conn = _AuditConn()
    r = _audit_native(conn)
    await r.step({"kind": "caller", "text": "bob at acme dot com"})
    await record(r, "bob@acme.com")
    await r.step({"kind": "caller", "text": "yes"})
    await record(r, "bob@acme.com", operation="confirm")
    await r.step({"kind": "caller", "text": "Actually, change my email to alice@example.com"})
    await record(r, "alice@example.com")
    revocations = [(sql, args) for sql, args in conn.statements
        if "UPDATE call_lead_details" in sql and "source = 'caller_stated'" in sql]
    assert len(revocations) == 1
    sql, args = revocations[0]
    assert "value = NULL" in sql and args[2] == "email" and args[3] == "awaiting_confirmation"
    assert r.session.captured_slots.email == "alice@example.com" and not r.session.captured_slots.email_confirmed


@pytest.mark.asyncio
async def test_realtime_contact_persistence_is_serialized_across_corrections(
    monkeypatch,
):
    from app.domain.services.voice_pipeline import lead_slot_capture
    from app.realtime.bridge import RealtimeBridge

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    completed = []

    async def _capture(session, **_kwargs):
        value = session.captured_slots.email
        if value == "old@example.com":
            first_started.set()
            await release_first.wait()
        completed.append(value)
        return 1

    monkeypatch.setattr(lead_slot_capture, "capture_turn_slots", _capture)
    session = SimpleNamespace(
        captured_slots=CallState(
            email="old@example.com",
            email_confirmed=True,
        )
    )
    bridge = RealtimeBridge(
        call_id="voice-call",
        realtime_session=object(),
        media_gateway=object(),
        contact_session=session,
        knowledge_pool=object(),
    )

    bridge._schedule_contact_persist()
    await first_started.wait()
    session.captured_slots = CallState(
        email="new@example.com",
        email_confirmed=True,
    )
    bridge._schedule_contact_persist()
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(*tuple(bridge._contact_tasks))

    assert completed == ["old@example.com", "new@example.com"]


def test_campaign_phone_region_is_threaded_only_when_explicitly_configured():
    from app.domain.services.telephony_session_config import (
        build_telephony_session_config,
    )

    common = {
        "knowledge_driven": True,
        "company_name": "Acme",
        "agent_names": ["Alex"],
    }
    configured = build_telephony_session_config(
        campaign={
            "id": "configured-region",
            "script_config": {**common, "default_country_code": "gb"},
        }
    )
    absent = build_telephony_session_config(
        campaign={"id": "no-region", "script_config": common}
    )

    assert configured.contact_phone_region == "GB"
    assert absent.contact_phone_region is None


def test_realtime_guide_distinguishes_pending_confirmation_and_persistence():
    from app.realtime.prompts import RealtimePersona, build_realtime_instructions
    text = build_realtime_instructions(RealtimePersona()).lower()
    assert "record_contact" in text and "expected_value" in text
    assert "new value is pending" in text and "result says saved" in text
    assert "confirmation does not mean a message was sent" in text
    assert "say exactly" not in text


def test_turn_telemetry_never_logs_raw_contact_bearing_transcripts():
    from app.domain.services.voice_pipeline.turn_ender import TurnEnder

    source = inspect.getsource(TurnEnder.handle)
    assert "transcript=%r" not in source
    assert '"transcript": full_transcript' not in source
    assert '"response": response_text' not in source
    assert '"voice.turn.transcript"' not in source
