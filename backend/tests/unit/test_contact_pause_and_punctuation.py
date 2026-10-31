"""Pending contacts remain until a model-recorded correction."""
import pytest



@pytest.mark.asyncio
async def test_native_pending_contact_survives_conversation_until_model_records_correction():
    from tests.unit.test_ag05_native_contact_revision import replay, record
    r = replay("openai")
    await r.step({"kind": "caller", "text": "My email is anna@example.com"})
    await record(r, "anna@example.com")
    pending = r.session.captured_slots.email_capture
    await r.step({"kind": "caller", "text": "Leave it unconfirmed."})
    await r.step({"kind": "response", "text": "Okay. What would you like to discuss?"})
    assert r.session.captured_slots.email_capture is pending
    assert not r.session.captured_slots.email_confirmed
    await r.step({"kind": "caller", "text": "Actually my email is anna.service@example.com"})
    assert r.session.captured_slots.email == "anna@example.com"
    await record(r, "anna.service@example.com")
    assert r.session.captured_slots.email == "anna.service@example.com"
    assert not r.session.captured_slots.email_confirmed
    assert not any("BACKEND CONTACT MODE" in str(event) for event in r.socket.sent)
