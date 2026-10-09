"""Real contact tool/state/persistence boundary; synthetic SQL and model ports only."""
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.services.voice_pipeline.contact_capture import ContactSource
from app.domain.services.voice_pipeline.contact_recording import bind_contact_turn, record_contact
from app.domain.services.voice_pipeline.lead_slot_capture import snapshot_slots
from app.services.scripts.call_state_tracker import CallState


class SQLPort:
    """Actual service validation/SQL construction, no SQL engine or RLS claim."""
    def __init__(self, *, fail=False, conflict=False, is_test=False):
        self.fail, self.conflict, self.is_test = fail, conflict, is_test
        self.writes = []
        self.entered = asyncio.Event()
        self.release = None

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, sql, *args):
        assert sql.startswith("SET LOCAL ")

    async def fetchrow(self, sql, *args):
        if "SELECT is_test" in sql:
            return {"is_test": self.is_test}
        assert "call_lead_details" in sql
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("synthetic write failure")
        if self.conflict:
            return None
        self.writes.append((sql, args))
        return {"id": str(uuid4())}


def session(pool=None):
    return SimpleNamespace(captured_slots=CallState(), contact_phone_region=None,
        _voice_action_pool=pool, _dialer_call_id=str(uuid4()), _dialer_tenant_id=str(uuid4()),
        _dialer_campaign_id=str(uuid4()), _dialer_lead_id=str(uuid4()))


def caller(s, text, order, item=None):
    return bind_contact_turn(s, text, ContactSource(item or f"caller-{order}", order,
        hashlib.sha256(text.strip().encode()).hexdigest()))


def args(text, *, kind="email", operation="set", value="alex@example.com", expected=None):
    return {"kind": kind, "operation": operation, "value": value,
            "expected_value": expected, "source_quote": text}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,value,text", [
    ("email", "alex@example.com", "Use alex at example dot com."),
    ("phone", "+14155552671", "Reach me on four one five, five five five, two six seven one, in the US."),
])
async def test_model_candidate_and_natural_confirmation_use_actual_save_boundary(kind, value, text):
    pool, s = SQLPort(), session()
    caller(s, text, 1)
    result = await record_contact(s, args(text, kind=kind, value=value), pool=pool)
    assert result["saved"] and result["validation_status"] == "awaiting_confirmation"
    capture = getattr(s.captured_slots, f"{kind}_capture")
    assert capture.normalized_value == value and capture.value_source.caller_turn_order == 1
    reply = "That's precisely the one I want you to use."
    caller(s, reply, 2)
    result = await record_contact(s, args(reply, kind=kind, operation="confirm", value=value, expected=value), pool=pool)
    assert result["saved"] and result["validation_status"] == "confirmed"
    capture = getattr(s.captured_slots, f"{kind}_capture")
    assert capture.confirmation_source.caller_turn_order == 2
    assert capture.confirmation_evidence == "model_interpreted_caller_confirmation"
    assert capture.readback is None  # no invented transport/human hearing proof
    assert len(pool.writes) == 2
    assert all(parameters[0] == s._dialer_tenant_id and parameters[1] == s._dialer_call_id
               for _, parameters in pool.writes)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,value", [("email", "alex at"), ("phone", "+123"), ("phone", "4155552671")])
async def test_partial_contact_retains_visible_quote_with_no_usable_value(kind, value):
    pool, s = SQLPort(), session()
    text = "My contact is " + value
    caller(s, text, 1)
    result = await record_contact(s, args(text, kind=kind, value=value), pool=pool)
    row = snapshot_slots(s.captured_slots)[kind]
    assert result["saved"] and result["validation_status"] == "needs_clarification"
    assert row["value"] is None and not row["confirmed"] and row["raw_value"] == text
    assert row["evidence"]["value_source"]["provider_item_id"] == "caller-1"


@pytest.mark.asyncio
async def test_correction_reopens_confirmation_and_withdrawal_uses_current_value():
    pool, s = SQLPort(), session()
    text = "Use alex@example.com"
    caller(s, text, 1)
    await record_contact(s, args(text), pool=pool)
    caller(s, "Fine by me", 2)
    await record_contact(s, args("Fine by me", operation="confirm", expected="alex@example.com"), pool=pool)
    text = "Remove alex and use blair at example dot com instead"
    caller(s, text, 3)
    result = await record_contact(s, args(text, value="blair@example.com", expected="alex@example.com"), pool=pool)
    assert result["saved"] and result["validation_status"] == "awaiting_confirmation"
    assert s.captured_slots.email == "blair@example.com" and not s.captured_slots.email_confirmed
    assert any("UPDATE call_lead_details" in sql for sql, _ in pool.writes)
    caller(s, "Actually, don't keep that address", 4)
    result = await record_contact(s, args("Actually, don't keep that address", operation="withdraw",
        value=None, expected="blair@example.com"), pool=pool)
    assert result["saved"] and result["validation_status"] == "cancelled"
    assert snapshot_slots(s.captured_slots)["email"]["value"] is None


@pytest.mark.asyncio
async def test_additional_contact_keeps_confirmed_first_contact():
    pool, s = SQLPort(), session()
    caller(s, "alex@example.com", 1)
    await record_contact(s, args("alex@example.com"), pool=pool)
    caller(s, "That is the one", 2)
    await record_contact(s, args("That is the one", operation="confirm", expected="alex@example.com"), pool=pool)
    caller(s, "Keep a second address, blair@example.com", 3)
    result = await record_contact(s, args("Keep a second address, blair@example.com", operation="add",
        value="blair@example.com", expected="alex@example.com"), pool=pool)
    rows = snapshot_slots(s.captured_slots)
    assert result["saved"] and rows["email"]["confirmed"]
    assert rows["email"]["value"] == "alex@example.com"
    assert rows["email_2"]["value"] == "blair@example.com" and not rows["email_2"]["confirmed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"source_quote": "words the caller never said"}, {"kind": "name"},
    {"expected_value": "old@example.com"}, {"tenant_id": "invented"},
    {"value": 123}, {"source_quote": []},
])
async def test_bad_quote_kind_shape_or_stale_candidate_cannot_write(change):
    pool, s = SQLPort(), session()
    caller(s, "alex@example.com", 1)
    result = await record_contact(s, {**args("alex@example.com"), **change}, pool=pool)
    assert not result["saved"] and pool.writes == [] and snapshot_slots(s.captured_slots) == {}


@pytest.mark.asyncio
async def test_same_turn_cannot_promote_new_value_to_confirmed():
    pool, s = SQLPort(), session()
    caller(s, "alex@example.com", 1)
    await record_contact(s, args("alex@example.com"), pool=pool)
    result = await record_contact(s, args("alex@example.com", operation="confirm", expected="alex@example.com"), pool=pool)
    assert result["status"] == "confirmation_not_current" and not s.captured_slots.email_confirmed


@pytest.mark.asyncio
async def test_missing_and_explicitly_missing_or_stale_turn_cannot_adopt_new_context():
    pool, s = SQLPort(), session()
    result = await record_contact(s, args("alex@example.com"), pool=pool)
    assert result["status"] == "caller_evidence_unavailable"
    old = caller(s, "alex@example.com", 1)
    caller(s, "alex@example.com", 2)
    for turn, status in [(None, "caller_evidence_unavailable"), (old, "stale_caller_turn")]:
        result = await record_contact(s, args("alex@example.com"), turn=turn, pool=pool)
        assert result["status"] == status
    assert not pool.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fail", "conflict", "test"])
async def test_unacknowledged_writes_never_claim_saved(mode):
    pool, s = SQLPort(fail=mode == "fail", conflict=mode == "conflict", is_test=mode == "test"), session()
    caller(s, "alex@example.com", 1)
    result = await record_contact(s, args("alex@example.com"), pool=pool)
    assert s.captured_slots.email == "alex@example.com"
    if mode == "test":
        # A browser test call never writes lead data by policy, which is not a
        # failure: the model is told it is recorded, as on a real call, so the
        # test shows real behaviour (2026-10-08: "not_saved" made the agent
        # retry and apologise in every test). Still nothing is written.
        assert result["saved"] and result["status"] == "saved_test_call" and pool.writes == []
        return
    assert not result["saved"] and not result["success"] and not result["confirmation_allowed"]
    assert result["status"] == ("save_failed" if mode == "fail" else "not_saved")


@pytest.mark.asyncio
async def test_retry_exact_candidate_is_acknowledged_without_duplicate_write():
    pool, s = SQLPort(), session()
    caller(s, "alex@example.com", 1)
    await record_contact(s, args("alex@example.com"), pool=pool)
    result = await record_contact(s, args("alex@example.com", expected="alex@example.com"), pool=pool)
    assert result["saved"] and len(pool.writes) == 1


@pytest.mark.asyncio
async def test_selected_field_ack_does_not_borrow_another_contact_write():
    pool, s = SQLPort(), session()
    caller(s, "My phone is +14155552671", 1)
    await record_contact(s, args("My phone is +14155552671", kind="phone", value="+14155552671"), pool=pool)
    pool.conflict = True
    caller(s, "alex@example.com", 2)
    result = await record_contact(s, args("alex@example.com"), pool=pool)
    assert not result["saved"]


@pytest.mark.asyncio
@pytest.mark.parametrize("revised", ["Use a different address", ""])
async def test_revision_demotes_its_own_confirmation_but_keeps_value_and_other_fields(revised):
    pool, s = SQLPort(), session()
    caller(s, "alex@example.com", 1)
    await record_contact(s, args("alex@example.com"), pool=pool)
    caller(s, "That is right", 2, "affirm")
    await record_contact(s, args("That is right", operation="confirm", expected="alex@example.com"), pool=pool)
    s.captured_slots = replace(s.captured_slots, follow_up="independent request")
    caller(s, revised, 2, "affirm")
    assert not s.captured_slots.email_confirmed and s.captured_slots.email == "alex@example.com"
    assert s.captured_slots.follow_up == "independent request"


@pytest.mark.asyncio
async def test_revision_during_write_never_returns_current_save_claim():
    pool, s = SQLPort(), session()
    pool.release = asyncio.Event()
    old = caller(s, "alex@example.com", 1, "source")
    task = asyncio.create_task(record_contact(s, args("alex@example.com"), turn=old, pool=pool))
    await pool.entered.wait()
    caller(s, "Not my address", 1, "source")
    pool.release.set()
    result = await task
    assert result["status"] == "stale_caller_turn" and not result["saved"]
    assert result["value"] is None and result["validation_status"] == "needs_clarification"
    assert s.captured_slots.email is None


@pytest.mark.asyncio
async def test_concurrent_old_expected_value_cannot_overwrite_first_update():
    pool, s = SQLPort(), session()
    text = "My address is alex@example.com or blair@example.com"
    caller(s, text, 1)
    first, second = await asyncio.gather(
        record_contact(s, args(text), pool=pool),
        record_contact(s, args(text, value="blair@example.com"), pool=pool))
    assert first["saved"] and second["status"] == "contact_changed"
    assert s.captured_slots.email == "alex@example.com" and len(pool.writes) == 1


def native(provider):
    from tests.qualification.ag04_native import NativeReplay
    corpus = json.loads((Path(__file__).parents[1] / "fixtures/conversation/ag04_native.json").read_text(encoding="utf-8"))
    replay = NativeReplay({"id": "model-contact", "expect": {}, "semantic_ids": []}, provider, corpus)
    replay.session._lead_capture_binding = {"call_id": None, "tenant_id": None, "campaign_id": None, "lead_id": None}
    return replay


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_native_actual_wire_waits_for_tool_not_regex_and_returns_pending(provider):
    replay = native(provider)
    await replay.step({"kind": "caller", "item": "source", "text": "My email is alex@example.com"})
    assert snapshot_slots(replay.session.captured_slots) == {}
    await replay.step({"kind": "tool", "name": "record_contact", "arguments": args("My email is alex@example.com")})
    assert replay.session.captured_slots.email == "alex@example.com"
    assert not replay.session.captured_slots.email_confirmed
    assert any(t["name"] == "record_contact" for t in replay.tools)
    results = [json.loads(row["item"]["output"]) for row in replay.socket.sent
        if row.get("item", {}).get("type") == "function_call_output"]
    assert results[-1]["validation_status"] == "awaiting_confirmation" and not results[-1]["saved"]
    assert not any("BACKEND CONTACT MODE" in str(row) for row in replay.socket.sent)


@pytest.mark.asyncio
async def test_native_tool_before_final_transcript_cannot_use_prior_turn():
    replay = native("openai")
    await replay.step({"kind": "caller", "text": "alex@example.com"})
    await replay.step({"kind": "start", "item": "new"})
    await replay.step({"kind": "tool", "name": "record_contact", "arguments": args("alex@example.com")})
    assert snapshot_slots(replay.session.captured_slots) == {}


@pytest.mark.asyncio
async def test_native_revision_withdraws_model_record_without_scripted_replacement():
    replay = native("openai")
    await replay.step({"kind": "caller", "item": "source", "text": "alex@example.com"})
    await replay.step({"kind": "tool", "name": "record_contact", "arguments": args("alex@example.com")})
    await replay.step({"kind": "caller", "item": "source", "revision": True, "text": "blair@example.com"})
    assert replay.session.captured_slots.email is None
    assert replay.session.captured_slots.email_capture.validation_status == "needs_clarification"


@pytest.mark.asyncio
async def test_native_confirmation_revision_removes_live_confirmed_fact_and_returns_current_pending():
    replay = native("openai")
    await replay.step({"kind": "caller", "item": "value", "text": "alex@example.com"})
    await replay.step({"kind": "tool", "name": "record_contact", "arguments": args("alex@example.com")})
    await replay.step({"kind": "caller", "item": "confirm", "text": "That works for me"})
    await replay.step({"kind": "tool", "name": "record_contact", "arguments":
        args("That works for me", operation="confirm", expected="alex@example.com")})
    assert replay.bridge._live_state.confirmed_email == "alex@example.com"
    await replay.step({"kind": "caller", "item": "confirm", "revision": True, "text": ""})
    assert replay.bridge._live_state.confirmed_email is None
    await replay.step({"kind": "tool", "name": "record_contact", "arguments":
        args("That works for me", operation="confirm", expected="alex@example.com")})
    results = [json.loads(row["item"]["output"]) for row in replay.socket.sent
        if row.get("item", {}).get("type") == "function_call_output"]
    assert results[-1]["status"] == "caller_evidence_unavailable"
    assert results[-1]["value"] == "alex@example.com"
    assert results[-1]["validation_status"] == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_native_prose_is_not_rejected_by_old_price_relationship_or_action_regex():
    replay = native("openai")
    await replay.step({"kind": "caller", "text": "Tell me about your service"})
    text = "You said the old supplier emailed a $999 quote to your colleague."
    await replay.step({"kind": "response", "text": text})
    assert any(c.get("text") == text for c in replay.gateway.controls)
    assert not replay.bridge._repair_attempted


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_native_current_historical_and_revised_words_do_not_become_inferred_state(provider):
    replay = native(provider)
    actual = replace(replay.bridge._live_state, identity_introduced=True,
        last_tool_name="send_email", last_tool_success=False, last_tool_code="failed")
    replay.bridge._live_state = actual
    await replay.step({"kind": "start", "item": "older"})
    await replay.step({"kind": "start", "item": "current"})
    original = "I am your customer. Our current provider is Acme. I am very interested."
    await replay.step({"kind": "caller", "item": "current", "revision": True, "text": original})
    assert replay.bridge._live_state == actual
    await replay.step({"kind": "caller", "item": "older", "revision": True,
        "text": "I am not your customer. Our current provider is Before."})
    assert replay.bridge._live_state == actual
    assert replay.bridge._latest_caller_text == original
    corrected = "Actually, I am not your customer. I only want information."
    await replay.step({"kind": "caller", "item": "current", "revision": True, "text": corrected})
    assert replay.bridge._live_state == actual
    assert replay.bridge._latest_caller_text == corrected
    assert replay.session._contact_turn.text == corrected
    assert replay.session._voice_action_user_turn == 2
    rows = replay.transcripts.get_transcript_json(replay.call_id)
    current = next(row for row in rows if row["metadata"].get("provider_item_id") == "current")
    assert current["original_content"] == original and current["content"] == corrected
    assert current["metadata"]["caller_turn_order"] == 2


@pytest.mark.asyncio
async def test_actual_turn_runner_binds_evidence_but_does_not_parse_or_classify_contact():
    from app.domain.services.transcript_service import TranscriptService
    from app.domain.services.voice_pipeline.turn_runner import TurnRunner
    text = "My email is alex@example.com"
    service, s = TranscriptService(), session()
    s.call_id, s.turn_id, s.talklee_call_id = str(uuid4()), 1, None
    s.conversation_history, s._line_phone_checked = [], True
    actual_turn = service.accumulate_turn(s.call_id, "user", text, is_final=True, turn_index=0)
    assert service.bind_caller_turn(s.call_id, actual_turn, caller_turn_order=1)
    async def stream(current, websocket):
        assert current._contact_turn.source.provider_item_id == "traditional:1"
        assert snapshot_slots(current.captured_slots) == {}
        result = await record_contact(current, args(text), pool=SQLPort())
        assert result["saved"]
        return "Is that address right?", 1, 1
    pipeline = SimpleNamespace(transcript_service=service, _stream_llm_and_tts=stream,
        _supports_llm_end_session_action=lambda _: False)
    task = asyncio.create_task(TurnRunner(pipeline).run(s, text))
    task._caller_turn_order = 1
    await task
    assert s.captured_slots.email == "alex@example.com" and not s.captured_slots.email_confirmed
    assert s.captured_slots.agent_asked_kind is None


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", " My password is Qwerty!."])
async def test_actual_transcript_dispatch_echo_cleanup_keeps_canonical_contact_evidence(suffix):
    from unittest.mock import AsyncMock
    from app.domain.models.conversation import Message, MessageRole, TranscriptChunk
    from app.domain.services.explicit_secrets import sanitize_explicit_secrets
    from app.domain.services.transcript_service import TranscriptService
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler
    from app.domain.services.voice_pipeline.turn_ender import TurnEnder
    from tests.unit.test_instant_opener_continues_to_llm import _session, _pipeline_stub
    s, pipeline, pool = _session(), _pipeline_stub(), SQLPort()
    s.call_id = str(uuid4())
    s.turn_id, s._line_phone_checked = 2, True
    s._dialer_call_id, s._dialer_tenant_id = str(uuid4()), str(uuid4())
    s._dialer_campaign_id, s._dialer_lead_id = str(uuid4()), str(uuid4())
    disclosure = "This call may be recorded for quality and training purposes."
    quote = "My email is alex@example.com"
    raw = disclosure + " " + quote + suffix
    canonical = sanitize_explicit_secrets(raw)
    s.conversation_history = [Message(role=MessageRole.ASSISTANT, content=disclosure)]
    service = pipeline.transcript_service = TranscriptService()
    pipeline.stt_provider.detect_turn_end = lambda chunk: chunk.is_final and not chunk.text
    pipeline.handle_turn_end = TurnEnder(pipeline).handle
    observations = []
    async def stream(current, websocket):
        result = await record_contact(current, args(quote), pool=pool)
        observations.append((current.conversation_history[-1].content, current._contact_turn, result))
        return "Is that the address you want to use?", 1, 1
    pipeline._stream_llm_and_tts = AsyncMock(side_effect=stream)
    handler = TranscriptHandler(pipeline)
    try:
        await handler.handle(s, TranscriptChunk(text=raw, is_final=True, confidence=1.0))
        original = service.get_turns(s.call_id)[0]
        await handler.handle(s, TranscriptChunk(text="", is_final=True))
        task = pipeline._pending_llm_tasks[s.call_id]
        await asyncio.wait_for(task, 2)
        assert len(observations) == 1
        shown, bound, result = observations[0]
        assert shown == sanitize_explicit_secrets(quote + suffix)
        assert result["saved"] and result["validation_status"] == "awaiting_confirmation"
        assert bound.text == canonical and bound.source.provider_item_id == "traditional:1"
        assert bound.source.revision_sha256 == hashlib.sha256(canonical.encode()).hexdigest()
        assert len(pool.writes) == 1
        row = next(row for row in service.get_transcript_json(s.call_id) if row["role"] == "user")
        assert row["content"] == original.content == canonical
        assert "original_content" not in row and "asr_latest_revision" not in row["metadata"]
        assert row["metadata"]["provider_item_id"] == "traditional:1"
        evidence = snapshot_slots(s.captured_slots)["email"]["evidence"]
        assert evidence["value_source"]["revision_sha256"] == bound.source.revision_sha256
        assert snapshot_slots(s.captured_slots)["email"]["raw_value"] == quote
    finally:
        service.clear_buffer(s.call_id)


@pytest.mark.parametrize("state", ["revised", "retracted", "malformed_revision", "duplicate_owner"])
def test_canonical_contact_bundle_resolves_only_one_current_owned_row(state):
    from app.domain.services.transcript_service import TranscriptService
    service, call_id = TranscriptService(), str(uuid4())
    original = "My email is old@example.com"
    revised = "My email is alex@example.com"
    row = service.accumulate_turn(call_id, "user", original, is_final=True, turn_index=0)
    assert service.bind_caller_turn(call_id, row, caller_turn_order=1)
    try:
        if state == "duplicate_owner":
            other = service.accumulate_turn(call_id, "user", revised, is_final=True, turn_index=1)
            assert service.bind_caller_turn(call_id, other, caller_turn_order=1)
        else:
            assert service.annotate_turn_revision(call_id, turn_index=0,
                provider_item_id="traditional:1", caller_turn_order=1,
                content="" if state == "retracted" else revised)
            if state == "malformed_revision":
                row.metadata["asr_latest_revision"]["content_sha256"] = "invalid"
        bundle = service.caller_evidence(call_id, 1)
        if state == "revised":
            assert bundle["text"] == revised
            assert bundle["source"] == service.caller_source(call_id, 1)
            assert bundle["source"]["revision_sha256"] == hashlib.sha256(revised.encode()).hexdigest()
            assert row.content == original
        else:
            assert bundle is None and service.caller_source(call_id, 1) is None
    finally:
        service.clear_buffer(call_id)


@pytest.mark.asyncio
async def test_caller_first_hello_reaches_model_once_without_presynth_script(monkeypatch):
    from unittest.mock import AsyncMock
    from app.domain.models.conversation import MessageRole
    from app.domain.services.voice_pipeline.turn_ender import TurnEnder
    from tests.unit.test_instant_opener_continues_to_llm import _session, _pipeline_stub, _FakeVoiceSession
    s, pipeline = _session(), _pipeline_stub("Hello, what would you like to discuss?")
    s._voice_session_ref = _FakeVoiceSession(s)
    player = AsyncMock()
    monkeypatch.setattr("app.domain.services.telephony.modes.agent_first._send_outbound_greeting", player)
    await TurnEnder(pipeline).handle(s, None, user_text="Hello?")
    player.assert_not_awaited()
    assert pipeline._run_turn.await_count == 1
    assert [m.role for m in s.conversation_history] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert s.conversation_history[0].content == "Hello?"
