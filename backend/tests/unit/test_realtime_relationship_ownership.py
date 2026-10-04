"""AG03 native lifetime/order proofs; fake model events, no provider calls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.realtime.bridge import RealtimeBridge
from app.realtime.openai import RealtimeEvent, OpenAIRealtimeSession
from app.realtime.xai import XAIRealtimeSession


def make_bridge():
    provider = SimpleNamespace(update_live_state=AsyncMock(), repair_unspoken_response=AsyncMock())
    session = SimpleNamespace(_voice_action_context_loaded=True, _voice_action_capabilities={})
    bridge = RealtimeBridge(call_id="ag03-synthetic", realtime_session=provider,
        media_gateway=SimpleNamespace(), action_session=session, call_direction="inbound", greet_on_start=False)
    bridge._play_validated_response = AsyncMock()
    bridge._cancel_playback = AsyncMock()
    bridge._observe_contact_turn = AsyncMock()
    return bridge


def start(item, offset):
    return RealtimeEvent(kind="interrupted", raw={"reason": "speech_started", "item_id": item, "audio_start_ms": offset})


def final(item, text):
    return RealtimeEvent(kind="caller_transcript", text=text, is_final=True, raw={"item_id": item})


async def pump(bridge, events):
    async def stream():
        for event in events:
            yield event
    bridge._rt.events = stream
    await bridge._pump_model_events()
    if bridge._playback_task:
        await bridge._playback_task


async def admitted(bridge, text):
    bridge._play_validated_response.reset_mock()
    bridge._repair_attempted = False
    await pump(bridge, [RealtimeEvent(kind="response_candidate", text=text, audio=b"\xff" * 320)])
    return bridge._play_validated_response.await_count > 0


@pytest.mark.asyncio
async def test_explicit_denial_survives_thirty_later_ordinary_caller_turns():
    bridge = make_bridge()
    await pump(bridge, [start("denial", 0), final("denial", "I am not your customer.")])
    for index in range(30):
        await pump(bridge, [start(f"ordinary-{index}", (index + 1) * 1000),
                           final(f"ordinary-{index}", "Tell me about your services.")])
    assert len(bridge._contact_history) <= 12
    assert not await admitted(bridge, "You are our existing customer.")
    assert await admitted(bridge, "I will not assume you are a customer.")


@pytest.mark.asyncio
async def test_delayed_old_final_cannot_overwrite_newer_denial():
    bridge = make_bridge()
    await pump(bridge, [start("old", 0), start("new", 1000),
        final("new", "I am not your customer."), final("old", "I am your customer.")])
    assert not await admitted(bridge, "You are our existing customer.")
    assert bridge._latest_caller_text == "I am not your customer."


@pytest.mark.asyncio
async def test_duplicate_final_does_not_become_another_caller_turn():
    bridge = make_bridge()
    await pump(bridge, [start("one", 0), final("one", "I am not your customer."),
                       final("one", "I am not your customer.")])
    assert bridge._live_user_turn_seq == 1
    assert bridge._observe_contact_turn.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [OpenAIRealtimeSession, XAIRealtimeSession])
async def test_provider_keeps_speech_item_identity_and_commit_order(provider_type):
    provider = provider_type(api_key="synthetic-key", voice="ash" if provider_type is OpenAIRealtimeSession else "eve")
    await provider._handle_server_event({"type": "input_audio_buffer.speech_started", "item_id": "caller-1", "audio_start_ms": 1250})
    interruption = provider._event_queue.get_nowait()
    assert interruption.kind == "interrupted"
    assert interruption.raw["item_id"] == "caller-1"
    assert interruption.raw["audio_start_ms"] == 1250
    await provider._handle_server_event({"type": "input_audio_buffer.committed", "item_id": "caller-1", "previous_item_id": "prior"})
    committed = provider._event_queue.get_nowait()
    assert committed.kind == "caller_turn"
    assert committed.raw["previous_item_id"] == "prior"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [OpenAIRealtimeSession, XAIRealtimeSession])
async def test_normal_continuation_preserves_current_history_and_active_response(provider_type):
    import json

    provider = provider_type(api_key="synthetic-key", voice="ash" if provider_type is OpenAIRealtimeSession else "eve")
    provider._ws = SimpleNamespace(send=AsyncMock())
    await provider.request_response()
    assert [json.loads(call.args[0]) for call in provider._ws.send.await_args_list] == [
        {"type": "response.create"}]
    await provider.request_response()
    assert provider._ws.send.await_count == 1
    assert provider._pending_response_create is True


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_at", ["send", "finish"])
async def test_late_denial_cannot_resume_after_gateway_suppresses_cancellation(blocked_at):
    entered = asyncio.Event()
    never_release = asyncio.Event()
    submission_count = 0
    timeline = []

    async def suppressed_wait():
        entered.set()
        try:
            await never_release.wait()
        except asyncio.CancelledError:
            pass  # Real adapters may complete an accepted write after cancel.

    async def send_audio(call_id, audio):
        nonlocal submission_count
        submission_count += 1
        if blocked_at == "send" and submission_count == 1:
            await suppressed_wait()

    async def finish(call_id, utterance_id):
        if blocked_at == "finish":
            await suppressed_wait()
        return {"utterance_id": utterance_id, "status": "completed", "evidence": "transport_played", "played_ms": 80}

    provider = SimpleNamespace(update_live_state=AsyncMock(side_effect=lambda block: timeline.append("state")),
        request_response=AsyncMock(side_effect=lambda: timeline.append("continue")),
        repair_unspoken_response=AsyncMock(), truncate_response=AsyncMock(), close=AsyncMock())
    gateway = SimpleNamespace(send_audio=send_audio, finish_playback=AsyncMock(side_effect=finish),
        clear_output_buffer=AsyncMock(), playback_receipt=lambda *args: {"played_ms": 17})
    bridge = RealtimeBridge(call_id="ag03-suppressed-cancel", realtime_session=provider,
        media_gateway=gateway, call_direction="inbound", greet_on_start=False)
    bridge._observe_contact_turn = AsyncMock()
    bridge._observe_contact_agent_turn = Mock()
    bridge._record_turn = Mock()

    async def feed(events):
        async def stream():
            for event in events:
                yield event
        provider.events = stream
        await bridge._pump_model_events()

    await feed([RealtimeEvent(kind="caller_turn", raw={"item_id": "current"}),
        RealtimeEvent(kind="response_candidate", text="Our records show you are an existing customer.",
            audio=b"\xff" * 640, raw={"audio_parts": [{"item_id": "answer", "content_index": 0, "audio_bytes": 640}]})])
    await asyncio.wait_for(entered.wait(), timeout=1)
    await feed([final("current", "I am not your customer.")])
    await asyncio.wait_for(asyncio.gather(bridge._playback_task, return_exceptions=True), timeout=1)

    assert submission_count == (1 if blocked_at == "send" else 2)
    assert bridge._utterance["status"] == "interrupted"
    assert bridge._utterance["receipt"] is None
    bridge._observe_contact_agent_turn.assert_not_called()
    assert bridge._action_session._voice_action_delivered_text == ""
    gateway.clear_output_buffer.assert_awaited_once()
    assert provider.truncate_response.await_args.kwargs == {"played_ms": 17}
    provider.request_response.assert_awaited_once()
    provider.repair_unspoken_response.assert_not_awaited()
    assert timeline[:2] == ["state", "continue"]
    recorded = [call for call in bridge._record_turn.call_args_list if call.args[0] == "assistant"]
    assert len(recorded) == 1
    assert recorded[0].kwargs["metadata"]["delivery"]["status"] == "interrupted"
    assert recorded[0].kwargs["metadata"]["delivery"]["evidence"] == "unknown"


@pytest.mark.asyncio
async def test_pre_send_guard_does_not_cancel_itself_and_repairs_only_once():
    from app.domain.services.voice_pipeline.live_structured_state import evidence_from_transcript, reduce_live_state

    provider = SimpleNamespace(update_live_state=AsyncMock(), request_response=AsyncMock(),
        repair_unspoken_response=AsyncMock(), truncate_response=AsyncMock(), close=AsyncMock())
    gateway = SimpleNamespace(send_audio=AsyncMock(), clear_output_buffer=AsyncMock())
    bridge = RealtimeBridge(call_id="ag03-pre-send", realtime_session=provider,
        media_gateway=gateway, call_direction="inbound", greet_on_start=False)
    bridge._live_state = reduce_live_state(bridge._live_state,
        evidence_from_transcript(role="user", text="I am not your customer.", turn_id="fixture"))
    event = RealtimeEvent(kind="response_candidate", text="You are our existing customer.", audio=b"\xff" * 320)
    bridge._playback_task = asyncio.create_task(bridge._play_validated_response(event))
    await asyncio.wait_for(bridge._playback_task, timeout=1)
    assert not bridge._playback_task.cancelled()
    gateway.send_audio.assert_not_awaited()
    provider.request_response.assert_awaited_once()
    provider.repair_unspoken_response.assert_not_awaited()
    bridge._utterance = None
    bridge._playback_task = asyncio.create_task(bridge._play_validated_response(event))
    await asyncio.wait_for(bridge._playback_task, timeout=1)
    provider.request_response.assert_awaited_once()
    provider.close.assert_awaited_once()
    assert bridge._stop.is_set() and bridge._failure_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("older_text,opted_out", [
    ("Please stop calling me.", True),
    ('You said "stop calling me", but I need help.', False),
    ("I did not say stop calling me.", False),
])
async def test_delayed_opt_out_is_retained_without_hanging_up_newer_question(older_text, opted_out):
    bridge = make_bridge()
    await pump(bridge, [start("old", 0), start("new", 1000),
        final("new", "Please tell me what services are available."), final("old", older_text)])
    assert bool(getattr(bridge._action_session, "_caller_opted_out", False)) is opted_out
    assert bridge._latest_caller_text == "Please tell me what services are available."
    assert bridge._termination_task is None
    assert not getattr(bridge._action_session, "_end_call_requested", False)


@pytest.mark.asyncio
async def test_current_replacement_cannot_erase_accepted_opt_out():
    bridge = make_bridge()
    await pump(bridge, [start("one", 0), final("one", "Stop calling me, but please explain your service first."),
        final("one", "Please explain your service first.")])
    assert bridge._action_session._caller_opted_out is True
    assert bridge._termination_task is None


@pytest.mark.asyncio
async def test_late_prior_denial_survives_current_ordinary_transcript_replacement():
    bridge = make_bridge()
    await pump(bridge, [start("old", 0), start("new", 1000),
        final("new", "Tell me about your services."), final("old", "I am not your customer."),
        final("new", "What services are available?")])
    assert not await admitted(bridge, "You are our existing customer.")
    assert bridge._latest_caller_text == "What services are available?"
    assert bridge._observe_contact_turn.await_count == 2
    assert bridge._observe_contact_turn.await_args_list[-1].kwargs == {"revision": True}


@pytest.mark.asyncio
async def test_evicted_speech_anchor_replay_cannot_reverse_newer_correction():
    bridge = make_bridge()
    await pump(bridge, [start("retired", 0), final("retired", "I am your customer."),
        start("denial", 1000), final("denial", "I am not your customer.")])
    for index in range(70):
        await pump(bridge, [start(f"ordinary-{index}", (index + 2) * 1000),
            final(f"ordinary-{index}", "Tell me more.")])
    await pump(bridge, [start("retired", 0),
        RealtimeEvent(kind="caller_turn", raw={"item_id": "retired", "previous_item_id": "old-assistant"}),
        final("retired", "I am your customer.")])
    assert len(bridge._caller_items) <= 64
    assert not await admitted(bridge, "You are our existing customer.")


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-1, True, None, "1000", float("nan"), float("inf")])
async def test_malformed_speech_offsets_do_not_authorize_caller_correction(offset):
    bridge = make_bridge()
    await pump(bridge, [start("denial", 0), final("denial", "I am not your customer."),
        start("invalid", offset), RealtimeEvent(kind="caller_turn", raw={"item_id": "invalid"}),
        final("invalid", "I am your customer.")])
    assert not await admitted(bridge, "You are our existing customer.")


@pytest.mark.asyncio
async def test_unknown_previous_assistant_id_is_not_a_caller_order_error():
    bridge = make_bridge()
    await pump(bridge, [RealtimeEvent(kind="caller_turn", raw={"item_id": "current", "previous_item_id": "assistant-outside-caller-map"}),
        final("current", "I am not your customer.")])
    assert not await admitted(bridge, "You are our existing customer.")


@pytest.mark.asyncio
async def test_evicted_item_late_opt_out_survives_without_current_hangup():
    bridge = make_bridge()
    await pump(bridge, [start("retired", 0)])
    for index in range(70):
        await pump(bridge, [start(f"current-{index}", (index + 1) * 1000),
            final(f"current-{index}", "Please explain your services.")])
    await pump(bridge, [final("retired", "Stop calling me.")])
    assert bridge._action_session._caller_opted_out is True
    assert bridge._latest_caller_text == "Please explain your services."
    assert bridge._termination_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["Actually I am your customer.", ""])
async def test_current_asr_revision_is_evidence_on_original_row_not_a_new_turn(replacement):
    from app.domain.services.transcript_service import TranscriptService
    service = TranscriptService()
    call_id = "ag03-revision-evidence"
    service._buffers.pop(call_id, None)
    service._sealed.pop(call_id, None)
    bridge = make_bridge()
    bridge._call_id = call_id
    bridge._transcript_service = service
    try:
        await pump(bridge, [start("current", 0), final("current", "I am not your customer.")])
        original = service.get_turns(call_id)[0]
        timestamp = original.timestamp
        transcript_index = bridge._turn_index
        await pump(bridge, [final("current", replacement)])
        assert bridge._turn_index == transcript_index
        assert len(service.get_turns(call_id)) == 1
        retained = service.get_transcript_json(call_id)[0]
        assert retained["content"] == replacement
        assert retained["original_content"] == "I am not your customer."
        assert service.get_turns(call_id)[0].content == "I am not your customer."
        assert retained["timestamp"] == timestamp
        assert retained["metadata"]["asr_latest_revision"]["content"] == replacement
        assert retained["metadata"]["asr_latest_revision"]["retracted"] is (not replacement)
        assert retained["metadata"]["provider_item_id"] == "current"
        assert retained["metadata"]["caller_turn_order"] == 1
        assert "I am not your customer." not in service.get_transcript_text(call_id)
        assert service.get_transcript_text(call_id) == (f"User: {replacement}" if replacement else "")
    finally:
        service._buffers.pop(call_id, None)
        service._sealed.pop(call_id, None)


def test_revision_annotation_respects_identity_sealing_and_bounded_metadata():
    import hashlib
    from app.domain.services.transcript_service import TranscriptService
    service = TranscriptService()
    call_id = "ag03-revision-ownership"
    service._buffers.pop(call_id, None)
    service._sealed.pop(call_id, None)
    try:
        service.accumulate_turn(call_id, "user", "Original synthetic ASR.", turn_index=3,
            metadata={"provider_item_id": "one", "caller_turn_order": 4, "other": "retained"})
        assert not service.annotate_turn_revision("missing", turn_index=3, provider_item_id="one", caller_turn_order=4, content="secret")
        assert "missing" not in service._buffers
        for args in [{"turn_index": 4, "provider_item_id": "one", "caller_turn_order": 4},
                     {"turn_index": 3, "provider_item_id": "other", "caller_turn_order": 4},
                     {"turn_index": 3, "provider_item_id": "one", "caller_turn_order": 5}]:
            assert not service.annotate_turn_revision(call_id, **args, content="unrelated")
        content = "a" * 6000
        assert service.annotate_turn_revision(call_id, turn_index=3, provider_item_id="one", caller_turn_order=4, content=content)
        turn = service.get_turns(call_id)[0]
        revision = turn.metadata["asr_latest_revision"]
        assert len(revision["content"]) == 4096 and revision["truncated"]
        assert revision["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
        assert revision["characters"] == 6000 and revision["revision"] == 1
        assert turn.metadata["other"] == "retained"
        assert service.annotate_turn_revision(call_id, turn_index=3, provider_item_id="one", caller_turn_order=4, content="")
        assert turn.metadata["asr_latest_revision"]["revision"] == 2
        assert turn.metadata["asr_latest_revision"]["retracted"] is True
        service._sealed[call_id] = None
        assert not service.annotate_turn_revision(call_id, turn_index=3, provider_item_id="one", caller_turn_order=4, content="late")
        assert turn.metadata["asr_latest_revision"]["content"] == ""
    finally:
        service._buffers.pop(call_id, None)
        service._sealed.pop(call_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_at", ["claim", "context"])
async def test_corrected_final_revokes_old_action_before_external_effect(monkeypatch, pause_at):
    from app.domain.services.voice_pipeline import action_execution
    from app.realtime.openai import RealtimeFunctionCall
    from app.services import email_service
    bridge = make_bridge()
    session = bridge._action_session
    session.tenant_id = "tenant-synthetic"
    session._voice_action_proposals = {"send_email": {
        "id": "synthetic-proposal", "turn": 0,
        "payload": {"recipient": "synthetic@example.invalid", "subject": "Details", "body": "Approved"},
        "summary": "send the email titled Details to synthetic@example.invalid",
    }}
    session._voice_action_delivered_text = session._voice_action_proposals["send_email"]["summary"]
    bridge._rt.send_function_result = AsyncMock()
    await pump(bridge, [start("current", 0), final("current", "yes")])
    paused, release = asyncio.Event(), asyncio.Event()
    context = {"call_id": "call-synthetic", "campaign_id": "campaign-synthetic", "lead_id": None}
    context_calls = 0
    async def read_context(*args):
        nonlocal context_calls
        context_calls += 1
        if pause_at == "context" and context_calls == 1:
            paused.set()
            await release.wait()
        return context
    class Durable:
        def __init__(self, pool):
            pass
        async def execute(self, *, executor, **kwargs):
            if pause_at == "claim":
                paused.set()
                await release.wait()
            return await executor()
    send = AsyncMock(return_value={"success": True, "message_id": "synthetic-provider-receipt"})
    monkeypatch.setattr(action_execution, "_pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(action_execution, "_context", read_context)
    monkeypatch.setattr(action_execution, "_capabilities", lambda context: {"send_email": "approved"})
    monkeypatch.setattr(action_execution, "_parameters", lambda *args: (
        session._voice_action_proposals["send_email"]["payload"],
        session._voice_action_proposals["send_email"]["summary"]))
    monkeypatch.setattr(action_execution, "DurableActionExecutor", Durable)
    monkeypatch.setattr(email_service, "EmailService", lambda pool: SimpleNamespace(send_email=send))
    task = asyncio.create_task(bridge._handle_function_call(RealtimeFunctionCall("fc-send", "send_email", "{}")))
    await asyncio.wait_for(paused.wait(), timeout=1)
    await pump(bridge, [final("current", "No, do not send it.")])
    release.set()
    await task
    send.assert_not_awaited()
    result = bridge._rt.send_function_result.await_args.args[1]
    assert result["status"] == "confirmation_expired"
    assert bridge._observe_contact_turn.await_count == 2
    assert bridge._observe_contact_turn.await_args_list[-1].kwargs == {"revision": True}
