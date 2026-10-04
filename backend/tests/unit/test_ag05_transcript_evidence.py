"""Revised caller evidence is canonical without erasing the original capture."""
from copy import deepcopy
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.domain.services.call_summary.business_details import verified_details
from app.domain.services.transcript_service import TranscriptService, conversation_turns
from app.domain.services.transcript_service import transcript_text_from_turns


@pytest.fixture
def transcript():
    TranscriptService.clear_all_buffers()
    service = TranscriptService()
    service.accumulate_turn(
        "ag05-synthetic", "user", "My provider is FormerCo.", turn_index=2,
        metadata={"provider_item_id": "item-2", "caller_turn_order": 2},
    )
    yield service
    TranscriptService.clear_all_buffers()


def revise(service, text):
    assert service.annotate_turn_revision(
        "ag05-synthetic", turn_index=2, provider_item_id="item-2",
        caller_turn_order=2, content=text,
    )


def test_latest_revision_is_readable_and_original_is_preserved(transcript):
    revise(transcript, "My provider is CurrentCo.")
    row = transcript.get_transcript_json("ag05-synthetic")[0]
    assert row["content"] == "My provider is CurrentCo."
    assert row["original_content"] == "My provider is FormerCo."
    assert row["effective_content_status"] == "revised"
    assert transcript.get_transcript_text("ag05-synthetic") == "User: My provider is CurrentCo."
    assert transcript.get_turns("ag05-synthetic")[0].content == "My provider is FormerCo."
    assert conversation_turns([row]) == [row]
    assert conversation_turns([row, deepcopy(row)]) == [row]


def test_retraction_retains_audit_evidence_but_not_an_utterance(transcript):
    revise(transcript, "")
    row = transcript.get_transcript_json("ag05-synthetic")[0]
    assert row["content"] == ""
    assert row["original_content"] == "My provider is FormerCo."
    assert row["effective_content_status"] == "retracted"
    assert transcript.get_transcript_text("ag05-synthetic") == ""
    assert transcript.get_metrics("ag05-synthetic")["user_word_count"] == 0


@pytest.mark.parametrize("field,value", [
    ("provider_item_id", "other-item"), ("caller_turn_order", 3),
    ("revision", True), ("content_sha256", "bad"), ("truncated", True),
    ("characters", 999),
])
def test_invalid_revision_cannot_become_current_financial_or_caller_evidence(transcript, field, value):
    revise(transcript, "My provider is CurrentCo.")
    raw = deepcopy(transcript.get_turns("ag05-synthetic")[0].to_dict())
    raw["metadata"]["asr_latest_revision"][field] = value
    row = conversation_turns([raw])[0]
    assert row["content"] == ""
    assert row["original_content"] == "My provider is FormerCo."
    assert row["effective_content_status"] == "unavailable"


def test_business_quotes_use_effective_current_revision(transcript):
    revise(transcript, "My provider is CurrentCo.")
    raw = [turn.to_dict() for turn in transcript.get_turns("ag05-synthetic")]
    assert not verified_details([{"field_key": "current_provider", "value": "FormerCo",
                                  "source_quote": "My provider is FormerCo."}], raw)
    found = verified_details([{"field_key": "current_provider", "value": "CurrentCo",
                               "source_quote": "My provider is CurrentCo."}], raw)
    assert found[0]["evidence"]["status"] == "needs_review"


def test_mismatched_revision_request_does_not_rewrite_original(transcript):
    assert not transcript.annotate_turn_revision(
        "ag05-synthetic", turn_index=2, provider_item_id="other-item",
        caller_turn_order=2, content="Wrong caller",
    )
    assert transcript.get_transcript_text("ag05-synthetic") == "User: My provider is FormerCo."


def test_distinct_owned_identical_words_keep_resolvable_caller_identity(transcript):
    transcript.clear_buffer("ag05-synthetic")
    first = transcript.accumulate_turn("ag05-synthetic", "user", "Yes please", is_final=True)
    second = transcript.accumulate_turn("ag05-synthetic", "user", "Yes please", is_final=True)
    assert transcript.bind_caller_turn("ag05-synthetic", first, caller_turn_order=1)
    assert transcript.bind_caller_turn("ag05-synthetic", second, caller_turn_order=2)
    rows = transcript.get_transcript_json("ag05-synthetic")
    assert [r["metadata"]["caller_turn_order"] for r in rows] == [1, 2]
    assert transcript.caller_source("ag05-synthetic", 2)["provider_item_id"] == "traditional:2"
    assert len(conversation_turns([rows[0], deepcopy(rows[0])])) == 1
    assert not transcript.bind_caller_turn("ag05-synthetic", first, caller_turn_order=3)


async def test_actual_queued_coalescing_keeps_raw_source_identity_even_when_not_answered(transcript):
    from tests.unit.test_ag03_caller_dispatch_order import pipeline, final

    service, session = pipeline()
    service.transcript_service = transcript
    entered, release = asyncio.Event(), asyncio.Event()

    async def run(*args, **kwargs):
        entered.set()
        await release.wait()
        return "Understood.", 1.0, 1.0

    service._run_turn = run
    try:
        await final(service, session, "Please help with this.")
        first = service._pending_llm_tasks[session.call_id]
        await asyncio.wait_for(entered.wait(), 2)
        service._utterance_seq[session.call_id] = 1
        await final(service, session, "Please help with this.")
        await final(service, session, "Please help with this.")  # duplicate queue receipt
        await final(service, session, "What are your hours?")  # coalesces queued turn
        sources = [transcript.caller_source(session.call_id, order) for order in (1, 2, 3)]
        assert all(sources)
        assert [s["provider_item_id"] for s in sources] == ["traditional:1", "traditional:2", "traditional:3"]
        caller_rows = [r for r in transcript.get_transcript_json(session.call_id) if r["role"] == "user"]
        assert [r["metadata"]["caller_turn_order"] for r in caller_rows] == [1, 2, 3]
        release.set()
        await asyncio.wait_for(first, 2)
        tail = service._pending_llm_tasks.get(session.call_id)
        if tail:
            await asyncio.wait_for(tail, 2)
        assert session._accepted_caller_turn_order == 3
    finally:
        release.set()
        task = service._pending_llm_tasks.get(session.call_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_final_save_retries_preserves_failure_and_only_clears_after_ack(transcript, monkeypatch):
    writer = AsyncMock(side_effect=RuntimeError("synthetic DB unavailable"))
    monkeypatch.setattr(transcript, "_write_calls_transcript", writer)
    assert await transcript.save_transcript("ag05-synthetic", db_pool=object(), tenant_id="tenant") is None
    assert writer.await_count == 3
    assert transcript.get_turns("ag05-synthetic")
    transcript.accumulate_turn("ag05-synthetic", "user", "late unrelated final")
    assert len(transcript.get_turns("ag05-synthetic")) == 1
    writer.side_effect = None
    writer.return_value = "committed-row"
    assert await transcript.save_transcript("ag05-synthetic", db_pool=object(), tenant_id="tenant") == "committed-row"
    assert transcript.get_turns("ag05-synthetic") == []
    assert not await transcript.flush_to_database("ag05-synthetic", db_pool=object(), tenant_id="tenant")


def test_unsaved_memory_retention_is_bounded_without_unsealing_evicted_call(transcript, monkeypatch):
    monkeypatch.setattr(transcript, "_FAILED_RETAIN_MAX", 1)
    transcript.retain_failed_finalization("ag05-synthetic")
    transcript.accumulate_turn("other", "user", "other caller")
    transcript.retain_failed_finalization("other")
    assert transcript.get_turns("ag05-synthetic") == []
    transcript.accumulate_turn("ag05-synthetic", "user", "must not replace lost evidence")
    assert transcript.get_turns("ag05-synthetic") == []
    assert transcript.get_turns("other")


async def test_queued_flush_snapshots_current_revision_only_after_acquiring_lock(transcript, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    written = []

    async def writer(_pool, _id, text, *_args, **_kwargs):
        written.append(text)
        if len(written) == 1:
            entered.set()
            await release.wait()
        return "saved"

    monkeypatch.setattr(transcript, "_write_calls_transcript", writer)
    first = asyncio.create_task(transcript.flush_to_database("ag05-synthetic", db_pool=object(), tenant_id="tenant"))
    await entered.wait()
    second = asyncio.create_task(transcript.flush_to_database("ag05-synthetic", db_pool=object(), tenant_id="tenant"))
    revise(transcript, "My provider is CurrentCo.")
    release.set()
    assert await asyncio.gather(first, second) == [True, True]
    assert written == ["User: My provider is FormerCo.", "User: My provider is CurrentCo."]


@pytest.mark.parametrize("wrapped", [False, True])
def test_supported_legacy_turn_aliases_share_canonical_rendering(wrapped):
    turns = [{"speaker": "caller", "text": "My provider is CurrentCo."},
             {"speaker": "agent", "text": "Thank you."}]
    data = {"turns": turns} if wrapped else turns
    assert transcript_text_from_turns(data) == "User: My provider is CurrentCo.\nAssistant: Thank you."
    assert verified_details([{"field_key": "current_provider", "value": "CurrentCo",
                              "source_quote": "My provider is CurrentCo."}], data)


def test_legacy_alias_does_not_resurrect_retracted_owned_revision(transcript):
    revise(transcript, "")
    raw = transcript.get_turns("ag05-synthetic")[0].to_dict()
    raw["speaker"] = raw.pop("role")
    raw["text"] = raw.pop("content")
    assert transcript_text_from_turns({"turns": [raw]}) == ""
    assert conversation_turns({"turns": [raw]})[0]["effective_content_status"] == "retracted"


def test_empty_owned_final_retains_identity_for_a_later_revision(transcript):
    transcript.clear_buffer("ag05-synthetic")
    row = transcript.accumulate_turn("ag05-synthetic", "user", "", is_final=True,
                                    turn_index=4, metadata={"provider_item_id": "native-4", "caller_turn_order": 4})
    assert row is not None
    assert transcript.get_transcript_text("ag05-synthetic") == ""
    assert transcript.annotate_turn_revision("ag05-synthetic", turn_index=4,
        provider_item_id="native-4", caller_turn_order=4, content="My email is alex@example.com.")
    current = transcript.get_transcript_json("ag05-synthetic")[0]
    assert current["original_content"] == ""
    assert current["content"] == "My email is alex@example.com."
    assert transcript.accumulate_turn("ag05-synthetic", "user", "", is_final=True) is None
    assert transcript.accumulate_turn("ag05-synthetic", "assistant", "", is_final=True,
        metadata={"provider_item_id": "native-5", "caller_turn_order": 5}) is None


async def test_cancelled_final_save_retains_bounded_frozen_evidence(transcript, monkeypatch):
    entered = asyncio.Event()

    async def writer(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(transcript, "_write_calls_transcript", writer)
    task = asyncio.create_task(transcript.save_transcript("ag05-synthetic", db_pool=object(), tenant_id="tenant"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "ag05-synthetic" in transcript._failed_finalizations
    assert transcript.get_turns("ag05-synthetic")
    assert transcript.accumulate_turn("ag05-synthetic", "user", "late") is None


async def test_actual_traditional_runner_contact_sources_resolve_to_saved_caller_rows(transcript):
    import hashlib
    from tests.unit.test_ag03_caller_dispatch_order import pipeline, final

    service, session = pipeline()
    service.transcript_service = transcript
    session._line_phone_checked = True
    session._has_introduced = True
    service._stream_llm_and_tts = AsyncMock(side_effect=[
        ("Your email is alex@example.com, is that correct?", 1.0, 1.0),
        ("Thank you for confirming.", 1.0, 1.0),
    ])
    for utterance in ("My email is alex at example dot com.", "Yes, that's correct."):
        await final(service, session, utterance)
        task = service._pending_llm_tasks.get(session.call_id)
        assert task is not None
        await asyncio.wait_for(task, 2)
    capture = session.captured_slots.email_capture
    assert capture.normalized_value == "alex@example.com"
    assert capture.status.value == "confirmed"
    assert capture.value_source.provider_item_id == "traditional:1"
    assert capture.confirmation_source.provider_item_id == "traditional:2"
    rows = {r["metadata"].get("provider_item_id"): r for r in transcript.get_transcript_json(session.call_id)}
    for source in (capture.value_source, capture.confirmation_source):
        row = rows[source.provider_item_id]
        assert source.caller_turn_order == row["metadata"]["caller_turn_order"]
        assert source.revision_sha256 == hashlib.sha256(row["content"].encode()).hexdigest()
    # This path has no retained correlated playback identity; do not invent it.
    assert capture.readback is None


@pytest.mark.parametrize("media", ["submitted", "interrupted", "failed", "exception"])
async def test_clarification_durable_snapshot_only_contains_successful_submission(transcript, monkeypatch, media):
    from types import SimpleNamespace
    from tests.unit.test_ag03_caller_dispatch_order import pipeline

    service, session = pipeline()
    service.transcript_service = transcript
    session.conversation_history = []
    session.turn_id = 0
    session._dialer_call_id = "owned-call"
    session._dialer_tenant_id = "owned-tenant"
    session.tenant_id = None  # no KB; authoritative telephony binding still works
    submitted = "Sorry, I didn't catch that — could you say that again?"
    service.synthesize_and_send_audio = AsyncMock(return_value=media == "interrupted")
    if media == "exception":
        service.synthesize_and_send_audio.side_effect = RuntimeError("synthetic media error")
    session._tts_delivery_failed = media == "failed"
    flush = AsyncMock(return_value=True)
    monkeypatch.setattr(transcript, "flush_to_database", flush)
    monkeypatch.setattr("app.domain.services.voice_pipeline.turn_ender.get_container",
                        lambda: SimpleNamespace(is_initialized=True, db_pool="synthetic-pool"))
    await service.handle_turn_end(session, user_text="Uncertain contact", confidence=0.1)
    if media == "submitted":
        assert transcript.get_transcript_text(session.call_id) == "Assistant: " + submitted
        row = transcript.get_transcript_json(session.call_id)[0]
        assert row["metadata"]["delivery_evidence"] == "submitted"
        flush.assert_awaited_once_with(call_id=session.call_id, db_pool="synthetic-pool",
            tenant_id="owned-tenant", talklee_call_id=session.talklee_call_id, target_call_id="owned-call")
    else:
        assert transcript.get_transcript_json(session.call_id) == []
        flush.assert_not_awaited()
    assert not any(message.role.value == "user" for message in session.conversation_history)


@pytest.mark.parametrize("wrapped", [False, True])
async def test_summary_uses_legacy_structured_current_text_not_stale_plaintext(wrapped):
    from unittest.mock import patch
    from tests.unit.call_summary.test_store import _make_conn, _patch_acquire, _FAKE_SUMMARY, _TENANT_ID, _CALL_ID
    from app.domain.services.call_summary.store import generate_and_store

    turns = [{"speaker": "caller", "text": "My provider is CurrentCo."}]
    row = {"transcript": "User: Obsolete supplier", "transcript_json": {"turns": turns} if wrapped else turns,
           "summary_json": None, "action_results": None}
    conn = _make_conn(row)
    summarizer = AsyncMock(return_value=_FAKE_SUMMARY)
    with _patch_acquire(conn), patch("app.domain.services.call_summary.store.summarize_transcript", summarizer):
        await generate_and_store(None, _TENANT_ID, _CALL_ID)
    assert summarizer.await_args.args[0] == "User: My provider is CurrentCo."


async def test_summary_does_not_resurrect_plaintext_after_wrapped_retraction(transcript):
    from unittest.mock import patch
    from tests.unit.call_summary.test_store import _make_conn, _patch_acquire, _TENANT_ID, _CALL_ID
    from app.domain.services.call_summary.store import generate_and_store

    revise(transcript, "")
    row = {"transcript": "User: My provider is FormerCo.",
           "transcript_json": {"turns": [t.to_dict() for t in transcript.get_turns("ag05-synthetic")]},
           "summary_json": None, "action_results": None}
    conn = _make_conn(row)
    summarizer = AsyncMock()
    with _patch_acquire(conn), patch("app.domain.services.call_summary.store.summarize_transcript", summarizer):
        assert await generate_and_store(None, _TENANT_ID, _CALL_ID) is None
    summarizer.assert_not_awaited()
