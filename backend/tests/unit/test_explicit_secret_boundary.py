"""Bounded text privacy controls; synthetic SQL/provider ports, never audio proof."""
import asyncio
from copy import deepcopy
import hashlib
import json
import socket
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from app.domain.models.conversation import TranscriptChunk
from app.domain.services.lead_capture_service import project_contact_evidence
from app.domain.services.transcript_service import TranscriptService
from app.domain.services.voice_pipeline.lead_slot_capture import snapshot_slots


MARKER = "[secret removed]"
CONTACT = "My name is Alex Morgan. My email is alex@example.com. My phone is +14155552671."
SECRET = "My card number is 4111 1111 1111 1111."
MIXED = f"{CONTACT} {SECRET} Our budget is 2500 and invoice number is 123456."


@pytest.fixture(autouse=True)
async def local_only(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("No provider or database network permitted")
    with monkeypatch.context() as patch:
        patch.setattr(socket.socket, "connect", denied)
        patch.setattr(socket.socket, "connect_ex", denied)
        patch.setattr(socket, "getaddrinfo", denied)
        TranscriptService.clear_all_buffers()
        yield
        TranscriptService.clear_all_buffers()


def assert_safe(value):
    text = json.dumps(value, default=str)
    assert "4111" not in text
    assert MARKER in text


@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("final", [False, True])
def test_canonical_accumulation_removes_only_explicit_secret_from_all_known_text(role, final):
    svc = TranscriptService()
    metadata = {"provider_item_id": "opaque-item-4111", "caller_turn_order": 1,
        "alternatives": [SECRET, {"transcript": SECRET, "confidence": 0.8}],
        "transcript_alternatives": [{"text": SECRET}]}
    before = deepcopy(metadata)
    turn = svc.accumulate_turn("synthetic", role, MIXED, is_final=final, metadata=metadata)
    assert_safe(turn.content)
    assert CONTACT in turn.content
    assert "budget is 2500 and invoice number is 123456" in turn.content
    assert_safe(turn.metadata["alternatives"])
    assert_safe(turn.metadata["transcript_alternatives"])
    assert turn.metadata["provider_item_id"] == "opaque-item-4111"
    assert metadata == before


def test_revision_retains_safe_original_and_uses_safe_hash_and_character_count():
    svc = TranscriptService()
    svc.accumulate_turn("synthetic", "user", MIXED, turn_index=1, is_final=True,
        metadata={"provider_item_id": "item", "caller_turn_order": 1})
    assert svc.annotate_turn_revision("synthetic", turn_index=1, provider_item_id="item",
        caller_turn_order=1, content=MIXED + " Please help with setup.")
    row = svc.get_transcript_json("synthetic")[0]
    assert_safe(row)
    revision = row["metadata"]["asr_latest_revision"]
    assert row["effective_content_status"] == "revised"
    assert revision["content_sha256"] == hashlib.sha256(revision["content"].encode()).hexdigest()
    assert revision["characters"] == len(revision["content"])
    assert CONTACT in row["content"] and CONTACT in row["original_content"]
    assert svc.annotate_turn_revision("synthetic", turn_index=1, provider_item_id="item",
        caller_turn_order=1, content="")
    retracted = svc.get_transcript_json("synthetic")[0]
    assert retracted["effective_content_status"] == "retracted"
    assert retracted["content"] == "" and MARKER in retracted["original_content"]


async def test_actual_incremental_and_final_sql_payloads_never_retain_explicit_secret():
    from tests.unit.test_transcript_flush_pooled import _FakeConn, _FakePool
    svc = TranscriptService()
    svc.accumulate_turn("synthetic", "user", MIXED, is_final=True)
    conn = _FakeConn()
    pool = _FakePool(conn)
    tenant = "00000000-0000-0000-0000-0000000000b1"
    assert await svc.flush_to_database("synthetic", db_pool=pool, tenant_id=tenant)
    assert await svc.save_transcript("synthetic", db_pool=pool, tenant_id=tenant)
    writes = [(sql, args) for sql, args in conn.executed
              if sql.startswith("UPDATE calls SET transcript=") or sql.startswith("UPDATE transcripts")]
    assert len(writes) == 3
    for _, args in writes:
        assert_safe(args)
        assert CONTACT in str(args)


async def test_traditional_handler_fanout_and_actual_turn_capture_share_safe_source():
    from tests.unit.test_ag03_caller_dispatch_order import pipeline
    service, session = pipeline()
    service.transcript_service = TranscriptService()
    service._stream_llm_and_tts = AsyncMock(return_value=("Thanks for explaining.", 1.0, 1.0))
    service._supports_llm_end_session_action = lambda _session: False
    session._line_phone_checked = True
    websocket = AsyncMock()
    await service.handle_transcript(session, TranscriptChunk(text=MIXED, is_final=True,
        metadata={"alternatives": (SECRET,)}), websocket)
    assert_safe(session.current_user_input)
    assert_safe(session._last_transcript_alternatives)
    assert_safe(websocket.send_json.call_args.args[0])
    await service.handle_transcript(session, TranscriptChunk(text="", is_final=True))
    await asyncio.wait_for(service._pending_llm_tasks[session.call_id], 2)
    rows = snapshot_slots(session.captured_slots)
    assert rows["email"]["value"] == "alex@example.com"
    assert rows["phone"]["value"] == "+14155552671"
    assert_safe([m.content for m in session.conversation_history])
    assert "4111" not in json.dumps(rows, default=str)
    for row in rows.values():
        projected = project_contact_evidence({**row, "source": "caller_stated"},
            service.transcript_service.get_transcript_json(session.call_id))
        assert projected["evidence"]["provenance_status"] == "matched"


@pytest.mark.parametrize("provider", ["openai", "xai"])
@pytest.mark.parametrize("revision", [False, True])
async def test_actual_native_fanout_contact_and_revision_keep_matching_safe_source(provider, revision):
    from tests.unit.test_ag05_native_contact_revision import replay
    r = replay(provider)
    if revision:
        await r.step({"kind": "caller", "item": "source", "text": "My email is old@example.com."})
    await r.step({"kind": "caller", "item": "source", "text": MIXED, "revision": revision})
    rows = snapshot_slots(r.session.captured_slots)
    assert rows["email"]["value"] == "alex@example.com"
    assert rows["phone"]["value"] == "+14155552671"
    assert_safe(r.bridge._latest_caller_text)
    assert_safe([m.content for m in r.bridge._contact_history])
    assert_safe(r.transcripts.get_transcript_json(r.call_id))
    assert "4111" not in json.dumps(rows, default=str)
    for row in rows.values():
        projected = project_contact_evidence({**row, "source": "caller_stated"},
            r.transcripts.get_transcript_json(r.call_id))
        assert projected["evidence"]["provenance_status"] == "matched"


@pytest.mark.parametrize("text", [CONTACT, "My name is PIN Patel.", "I forgot my password.",
    "A password is required to sign in.", "Does the terminal support PIN payments?",
    "My password is not working.", "Our budget is 2500, invoice 123456, due 2026-10-06."])
def test_normal_contacts_names_and_business_or_password_policy_are_unchanged(text):
    svc = TranscriptService()
    assert svc.accumulate_turn("synthetic", "user", text).content == text


@pytest.mark.parametrize("text,secret", [
    (SECRET, "4111 1111 1111 1111"),
    ('My card number is "4111-1111-1111-1111".', "4111-1111-1111-1111"),
    ("CVV: 482.", "482"), ("my PIN is 4826.", "4826"),
    ("One-time passcode: 642873.", "642873"),
    ("My bank account number is 10987654321.", "10987654321"),
    ("My social-security number is 123-45-6789.", "123-45-6789"),
    ('My password is "purple tulip 73!".', "purple tulip 73!"),
    ("My password is 'purple tulip 73!'.", "purple tulip 73!"),
    ("My password is Synthetic73.", "Synthetic73."),
    ("My password is tulip.", "tulip."),
    ("My password is abc.def!", "abc.def!"),
    ("My password is abc?def,ghi;!", "abc?def,ghi;!"),
    ("My card number is 4111    1111    1111    1111.", "4111    1111    1111    1111"),
    ("My bank account number is 123.456.", "123.456"),
])
def test_only_complete_labelled_value_is_removed_and_operation_is_idempotent(text, secret):
    from app.domain.services.explicit_secrets import sanitize_explicit_secrets
    before = CONTACT + " " + text + " Call me Tuesday."
    expected = before.replace(secret, MARKER)
    assert sanitize_explicit_secrets(before) == expected
    assert sanitize_explicit_secrets(expected) == expected


@pytest.mark.parametrize("text", [
    "My card number", "4111 1111 1111 1111", "1234", "My password is not working.",
    "The password is required.", "A password reset is required.", "My password is missing.",
    "My password is a problem.", "The password is at least twelve characters long.",
    "Your password is encrypted in storage.", "The password is case-sensitive.",
    "My customer account number is 123456.", "Our merchant account number is 123456.",
    "passwordistan", "my cardinal number is 123456", "My email is password@example.com.",
    "My card number is " + "1" * 300,
    "My password is " + "x" * 200,
    "My card number is 4111" + " " * 300 + "1111 1111 1111",
    'My password is "abc"suffix',
])
def test_non_offers_and_explicitly_unsupported_shapes_are_not_inferred(text):
    from app.domain.services.explicit_secrets import sanitize_explicit_secrets
    assert sanitize_explicit_secrets(text) == text


def test_imported_revision_sanitization_cannot_turn_invalid_proof_into_valid_evidence():
    svc = TranscriptService()
    meta = {"provider_item_id": "item", "caller_turn_order": 1,
        "asr_latest_revision": {"provider_item_id": "item", "caller_turn_order": 1,
            "revision": 1, "content": SECRET, "content_sha256": "bad-proof",
            "characters": len(SECRET), "truncated": False, "retracted": False}}
    svc.accumulate_turn("synthetic", "user", CONTACT, is_final=True, metadata=meta)
    row = svc.get_transcript_json("synthetic")[0]
    assert row["effective_content_status"] == "unavailable"
    assert "4111" not in json.dumps(row) and "bad-proof" not in json.dumps(row)
    assert CONTACT == row["original_content"]


async def test_summary_receives_safe_transcript_and_name_uncertainty_contract(monkeypatch):
    from app.domain.services.call_summary import summarizer
    svc = TranscriptService()
    svc.accumulate_turn("synthetic", "user", MIXED + " The name may be Alix, I am not sure.")
    create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=
        SimpleNamespace(content=json.dumps(summarizer.EMPTY_SUMMARY)))]))
    monkeypatch.setattr(summarizer, "AsyncGroq", lambda **_: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    await summarizer.summarize_transcript(svc.get_transcript_text("synthetic"))
    messages = create.call_args.kwargs["messages"]
    assert_safe(messages[1]["content"])
    assert CONTACT in messages[1]["content"]
    assert "unclear or inferred name is not a confirmed identity" in messages[0]["content"]
    assert "Never include payment credentials" in messages[0]["content"]


@pytest.mark.parametrize("offered,secret", [
    ("My password is abc.def!", "abc.def!"),
    ("My password is abc?def,ghi;!", "abc?def,ghi;!"),
    ("My card number is 4111    1111    1111    1111.", "4111    1111    1111    1111"),
    ("My bank account number is 123.456.", "123.456"),
])
async def test_complete_secret_with_internal_punctuation_or_wide_gaps_is_absent_from_sql(offered, secret):
    from tests.unit.test_transcript_flush_pooled import _FakeConn, _FakePool
    svc, conn = TranscriptService(), _FakeConn()
    svc.accumulate_turn("synthetic", "user", offered + " " + CONTACT, is_final=True)
    assert await svc.save_transcript("synthetic", db_pool=_FakePool(conn),
        tenant_id="00000000-0000-0000-0000-0000000000b1")
    writes = [args for sql, args in conn.executed
              if sql.startswith("UPDATE calls SET transcript=") or sql.startswith("UPDATE transcripts")]
    assert len(writes) == 2
    for args in writes:
        value = str(args)
        assert secret not in value and MARKER in value and CONTACT in value


async def test_barge_in_transcript_fanout_is_safe_without_changing_input_event():
    from app.domain.models.conversation import BargeInSignal
    from tests.unit.test_ag03_caller_dispatch_order import pipeline
    service, session = pipeline()
    service.handle_barge_in = AsyncMock()
    event = BargeInSignal(text=MIXED)
    await service.handle_transcript(session, event)
    assert_safe(service.handle_barge_in.call_args.kwargs["transcript_text"])
    assert event.text == MIXED


def test_oversized_revision_remains_unavailable_even_if_sanitization_shortens_it():
    svc = TranscriptService()
    svc.accumulate_turn("synthetic", "user", CONTACT, is_final=True, turn_index=1,
        metadata={"provider_item_id": "item", "caller_turn_order": 1})
    content = (SECRET + " ") * 120
    assert len(content) > 4096
    assert svc.annotate_turn_revision("synthetic", turn_index=1, provider_item_id="item",
        caller_turn_order=1, content=content)
    row = svc.get_transcript_json("synthetic")[0]
    assert row["effective_content_status"] == "unavailable"
    assert "4111" not in json.dumps(row)
    assert row["metadata"]["asr_latest_revision"]["truncated"] is True


def test_both_composed_prompt_engines_keep_privacy_scope_and_available_route_contract():
    from app.realtime.prompts import build_realtime_instructions, PROMPT_VERSION
    from app.realtime.personas import RealtimePersona
    from tests.unit.test_prompt_versions import compose
    realtime = build_realtime_instructions(RealtimePersona())
    traditional = compose("lead_gen")
    assert PROMPT_VERSION == "realtime@7"
    for prompt in (realtime, traditional):
        for text in ("passwords", "PINs", "backend", "removed text"):
            assert text in prompt
        assert "legal or financial advice" in prompt
    assert "only when the backend provides one" in realtime
    assert "require explicit backend availability" in traditional
    assert "Decline unrelated regulated advice" in realtime


@pytest.mark.parametrize("text", [
    "My password is correct.", "My password is valid.", "My password is fine.",
    "My password is okay.", "My password is unchanged.", "My password is empty.",
    "My password is blank.", "My password is 12 characters long.",
    "My password is 6 digits long.", "My password is 15 letters long.",
    "My bank account number is 16 digits long.",
])
def test_explicit_password_state_and_numeric_length_descriptions_remain_readable(text):
    svc = TranscriptService()
    assert svc.accumulate_turn("synthetic", "user", text).content == text


@pytest.mark.parametrize("text,secret", [
    ("My password is 1234.", "1234."),
    ("My password is 123456", "123456"),
    ('My password is "correct".', "correct"),
    ("My bank account number is 123456.", "123456"),
])
def test_policy_exclusions_do_not_exempt_actual_values_without_length_description(text, secret):
    svc = TranscriptService()
    assert svc.accumulate_turn("synthetic", "user", text).content == text.replace(secret, MARKER)


@pytest.mark.parametrize("offered,secret", [
    ("My card number is 4111111111111111 digits long.", "4111111111111111"),
    ("My password is 123456 characters long.", "123456"),
    ("CVV is 123 digits long.", "123"),
    ("CVV is 042 digits long.", "042"),
    ("My password is [q7!m9].", "[q7!m9]."),
    ("My password is [q7?m9]", "[q7?m9]"),
])
async def test_scope_exclusions_cannot_retain_large_length_values_or_bracket_passwords(offered, secret):
    from tests.unit.test_transcript_flush_pooled import _FakeConn, _FakePool
    svc, conn = TranscriptService(), _FakeConn()
    turn = svc.accumulate_turn("synthetic", "user", offered + " " + CONTACT, is_final=True)
    assert turn.content == offered.replace(secret, MARKER) + " " + CONTACT
    assert await svc.save_transcript("synthetic", db_pool=_FakePool(conn),
        tenant_id="00000000-0000-0000-0000-0000000000b1")
    writes = [args for sql, args in conn.executed
              if sql.startswith("UPDATE calls SET transcript=") or sql.startswith("UPDATE transcripts")]
    assert len(writes) == 2
    for args in writes:
        assert secret not in str(args) and CONTACT in str(args)


@pytest.mark.parametrize("count", [1, 6, 12, 16, 32, 64])
def test_small_canonical_length_descriptions_and_exact_generated_marker_are_idempotent(count):
    from app.domain.services.explicit_secrets import sanitize_explicit_secrets
    text = f"My password is {count} characters long."
    assert sanitize_explicit_secrets(text) == text
    marked = f"My password is {MARKER}. {CONTACT}"
    assert sanitize_explicit_secrets(marked) == marked
